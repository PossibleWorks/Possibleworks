"""
Observer
Main event listener for Frappe document changes.
"""

import frappe
from typing import Any
from .settings_helper import SettingsHelper
from .payload_builder import PayloadBuilder
from .redis_buffer_service import RedisBufferService
from .workflow_service import WorkflowService
from .constants import (
    ALWAYS_OBSERVED_DOCTYPES,
    IMMEDIATE_SEND_DOCTYPES,
    SKIP_STATE_CHANGED_CHECK_DOCTYPES,
)


class WorkflowEventObserver:
    """
    Main observer class that listens to Frappe document events.
    
    This observer:
    1. Listens to all document events
    2. Filters out internal system doctypes
    3. Builds event payloads
    4. Pushes to Redis for processing
    
    Registered in hooks.py like:
    doc_events = {
        "*": {
            "after_insert": "possibleworks.observer.observer.handle_workflow_event",
            "on_update": "possibleworks.observer.observer.handle_workflow_event",
            ...
        }
    }
    """

    @staticmethod
    def should_process(doctype: str) -> bool:
        """
        Check if this doctype should be observed.
        
        Filters out internal system doctypes to prevent noise.
        
        Args:
            doctype: Document type to check
            
        Returns:
            True if should process, False otherwise
        """
        # Check if observer is enabled
        if not SettingsHelper.is_observer_enabled():
            return False

        # Always observe explicitly whitelisted doctypes
        if doctype in ALWAYS_OBSERVED_DOCTYPES:
            return True

        # Always observe procurement doctypes (immediate delivery)
        if doctype in IMMEDIATE_SEND_DOCTYPES:
            return True

        # Only process doctypes that have an active workflow
        if not WorkflowService.has_workflow(doctype):
            frappe.logger().debug(
                f"Observer: Skipping doctype without active workflow: {doctype}"
            )
            return False

        return True

    @staticmethod
    def process_event(doc: Any, event_type: str) -> bool:
        """
        Main event processing logic.
        
        Args:
            doc: Frappe document
            event_type: Event type (after_insert, on_update, etc.)
            
        Returns:
            True if event was processed successfully
        """
        try:
            # Quick check: should we process this?
            if not WorkflowEventObserver.should_process(doc.doctype):
                return False

            if event_type == "after_insert":
                # Always fire — doc is new, workflow just initialized
                pass

            elif event_type == "on_update":
                doc_before = doc.get_doc_before_save()
                if not doc_before:
                    frappe.logger().debug(
                        f"Observer: Skipping {doc.doctype}/{doc.name} on_update — "
                        f"no prior version (fired during insert transaction)"
                    )
                    return False

                # For always-observed doctypes (e.g. Employee), fire on any field change —
                # not just workflow/status transitions, since they are master records.
                if doc.doctype not in SKIP_STATE_CHANGED_CHECK_DOCTYPES:
                    if not WorkflowEventObserver._state_changed(doc, doc_before):
                        frappe.logger().debug(
                            f"Observer: Skipping {doc.doctype}/{doc.name} — "
                            f"state unchanged"
                        )
                        return False

            elif event_type in ("on_submit", "on_cancel", "on_discard"):
                pass  # Explicit lifecycle — always fire

            elif event_type in ("on_trash", "after_delete"):
                # Doc is being deleted — always fire regardless of workflow state
                # Use after_delete as fallback since on_trash can sometimes not fire
                # for draft docs in certain Frappe versions
                pass

            else:
                # before_insert or anything else — skip
                return False

            frappe.logger().debug(
                f"Observer: Processing {doc.doctype}/{doc.name} - {event_type}"
            )

            if doc.doctype in IMMEDIATE_SEND_DOCTYPES:
                doc_before = doc.get_doc_before_save()
                payload = PayloadBuilder.build_simple_payload(doc_before,doc,event_type)
                if not payload:
                    frappe.logger().warning(
                        f"Observer: Failed to build simple payload for {doc.doctype}/{doc.name}"
                    )
                    _create_dropped_log(doc, event_type, "Immediate", "Could not build payload — check company / tenant_id configuration")
                    return False
                # Attach log metadata so the background job creates the OEL row
                # inside its own transaction. Creating the row here (in the main
                # request transaction) and then updating it in the worker races
                # against the uncommitted INSERT under snapshot isolation →
                # MariaDB error 1020. Worker-side creation eliminates the race.
                payload["_log_meta"] = {
                    "reference_doctype": doc.doctype,
                    "reference_name": doc.name,
                    "event_type": event_type,
                    "delivery_mode": "Immediate",
                    "tenant_id": payload.get("tenant_id") or "",
                    "company": payload.get("company") or "",
                    "triggered_by": frappe.session.user,
                }
                frappe.enqueue(
                    "possibleworks.observer.batch_processor.send_single_event",
                    payload=payload,
                    queue="short",
                )
                # from possibleworks.observer.batch_processor import send_single_event
                # send_single_event(payload=payload)
                frappe.logger().debug(
                    f"Observer: Event enqueued for {doc.doctype}/{doc.name} - {event_type}"
                )
            else:
                extra_fields = {}
                if doc.doctype == "Employee" and event_type == "on_update":
                    doc_before = doc.get_doc_before_save()
                    if doc_before:
                        extra_fields = {"before_save_company_email": doc_before.company_email}
                payload = PayloadBuilder.build_payload(doc, event_type, extra_fields=extra_fields)
                if not payload:
                    frappe.logger().warning(
                        f"Observer: Failed to build payload for {doc.doctype}/{doc.name}"
                    )
                    _create_dropped_log(doc, event_type, "Batch", "Could not build payload — check company / tenant_id configuration")
                    return False
                log_name = _create_event_log(doc, event_type, "Batch", "Queued", payload)
                if log_name:
                    payload["_log_id"] = log_name
                success = RedisBufferService.push_event(payload)
                if success:
                    frappe.logger().debug(
                        f"Observer: Event queued for {doc.doctype}/{doc.name} - {event_type}"
                    )
                else:
                    frappe.logger().warning(
                        f"Observer: Failed to queue event for {doc.doctype}/{doc.name}"
                    )
                    if log_name:
                        frappe.db.set_value("Observer Event Log", log_name, {
                            "status": "Dropped",
                            "error_message": "Redis push failed — event could not be queued",
                        }, update_modified=False)
                        frappe.db.commit()
            return True

        except Exception as e:
            frappe.logger().error(
                f"Observer: Error processing event for {doc.doctype}/{doc.name}: {str(e)}"
            )
            return False

    @staticmethod
    def _state_changed(doc: Any, doc_before: Any) -> bool:
        """
        Checks if a meaningful state transition occurred.

        - If doctype has an active workflow: compares workflow_state field.
        - Otherwise: falls back to comparing status + docstatus.
        """
        try:
            workflow_name = WorkflowService.get_workflow_name(doc.doctype)

            if workflow_name:
                state_field = WorkflowService.get_state_field(workflow_name)
                old_state = getattr(doc_before, state_field, None)
                new_state = getattr(doc, state_field, None)
                changed = old_state != new_state
                frappe.logger().info(
                    f"_state_changed (workflow): {doc.doctype}/{doc.name} "
                    f"old='{old_state}' new='{new_state}' changed={changed}"
                )
                return changed

            # No workflow — fall back to status / docstatus
            old_status = getattr(doc_before, "status", None)
            new_status = getattr(doc, "status", None)
            old_docstatus = getattr(doc_before, "docstatus", None)
            new_docstatus = getattr(doc, "docstatus", None)
            changed = (old_status != new_status) or (old_docstatus != new_docstatus)
            frappe.logger().info(
                f"_state_changed (fallback): {doc.doctype}/{doc.name} "
                f"status: '{old_status}'->'{new_status}' "
                f"docstatus: '{old_docstatus}'->'{new_docstatus}' changed={changed}"
            )
            return changed

        except Exception as e:
            frappe.logger().warning(
                f"_state_changed error for {doc.doctype}/{doc.name}: {str(e)}"
            )
            return False

def _create_event_log(doc: Any, event_type: str, delivery_mode: str, status: str, payload: dict) -> "str | None":
	"""
	Insert an Observer Event Log row and return its name.

	Returns None if the insert fails — observer must never break doc saves.
	Call this BEFORE pushing to Redis so the returned name can be embedded
	in the payload as '_log_id' for the batch processor to look up later.
	"""
	try:
		import json
		from frappe.utils import now_datetime
		log = frappe.get_doc({
			"doctype": "Observer Event Log",
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"event_type": event_type,
			"delivery_mode": delivery_mode,
			"status": status,
			"tenant_id": payload.get("tenant_id") or "",
			"company": payload.get("company") or "",
			"triggered_by": frappe.session.user,
			"payload": json.dumps(payload, default=str),
			"queued_at": now_datetime(),
		})
		log.insert(ignore_permissions=True)
		return log.name
	except Exception as exc:
		frappe.logger().error(f"Observer: Failed to create event log: {exc}")
		return None


def _create_dropped_log(doc: Any, event_type: str, delivery_mode: str, error_message: str) -> None:
	"""
	Insert a Dropped Observer Event Log row (no payload — event was blocked before building).
	Failures are swallowed — log creation must never break doc saves.
	"""
	try:
		from frappe.utils import now_datetime
		log = frappe.get_doc({
			"doctype": "Observer Event Log",
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"event_type": event_type,
			"delivery_mode": delivery_mode,
			"status": "Dropped",
			"triggered_by": frappe.session.user,
			"error_message": error_message,
			"queued_at": now_datetime(),
		})
		log.insert(ignore_permissions=True)
	except Exception as exc:
		frappe.logger().error(f"Observer: Failed to create dropped log: {exc}")


# ============================================================================
# Hook functions - these are called by Frappe
# ============================================================================

def handle_workflow_event(doc: Any, method: str = None):
    """Generic handler for all workflow-related doc events."""
    # `method` is the event name: "after_insert", "on_update", etc.
    if not method:
        return
    WorkflowEventObserver.process_event(doc, method)