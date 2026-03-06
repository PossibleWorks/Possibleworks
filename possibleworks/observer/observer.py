"""
Observer
Main event listener for Frappe document changes.
"""

import frappe
from typing import Any
from .constants import IGNORED_DOCTYPES
from .settings_helper import SettingsHelper
from .payload_builder import PayloadBuilder
from .redis_buffer_service import RedisBufferService
from .workflow_service import WorkflowService


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
            "after_insert": "possibleworks.observer.observer.handle_after_insert",
            "on_update": "possibleworks.observer.observer.handle_on_update",
            "on_submit": "possibleworks.observer.observer.handle_on_submit",
            "on_cancel": "possibleworks.observer.observer.handle_on_cancel",
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

        # Ignore internal system doctypes
        if doctype in IGNORED_DOCTYPES:
            frappe.logger().debug(f"Observer: Ignoring system doctype: {doctype}")
            return False

        # Ignore custom doctypes that start with underscore (test/temp docs)
        if doctype.startswith("_"):
            return False
        
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

            frappe.logger().debug(
                f"Observer: Processing {doc.doctype}/{doc.name} - {event_type}"
            )

            # Build the payload
            payload = PayloadBuilder.build_payload(doc, event_type)
            
            if not payload:
                frappe.logger().warning(
                    f"Observer: Failed to build payload for {doc.doctype}/{doc.name}"
                )
                return False

            # Push to Redis
            success = RedisBufferService.push_event(payload)
            
            if success:
                frappe.logger().debug(
                    f"Observer: Event queued for {doc.doctype}/{doc.name}"
                )
            else:
                frappe.logger().warning(
                    f"Observer: Failed to queue event for {doc.doctype}/{doc.name}"
                )

            return success

        except Exception as e:
            frappe.logger().error(
                f"Observer: Error processing event for {doc.doctype}/{doc.name}: {str(e)}"
            )
            return False


# ============================================================================
# Hook functions - these are called by Frappe
# ============================================================================

def handle_after_insert(doc: Any, method: str = None):
    """Handle after_insert event"""
    WorkflowEventObserver.process_event(doc, "after_insert")


def handle_on_update(doc: Any, method: str = None):
    """Handle on_update event"""
    WorkflowEventObserver.process_event(doc, "on_update")


def handle_on_submit(doc: Any, method: str = None):
    """Handle on_submit event"""
    WorkflowEventObserver.process_event(doc, "on_submit")


def handle_on_cancel(doc: Any, method: str = None):
    """Handle on_cancel event"""
    WorkflowEventObserver.process_event(doc, "on_cancel")


def handle_before_insert(doc: Any, method: str = None):
    """Handle before_insert event"""
    WorkflowEventObserver.process_event(doc, "before_insert")


def handle_before_update(doc: Any, method: str = None):
    """Handle before_update event"""
    WorkflowEventObserver.process_event(doc, "before_update")