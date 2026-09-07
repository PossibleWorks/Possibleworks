"""
Workflow Service
Handles extraction of workflow information from Frappe documents.
"""

import frappe
import json
from typing import Dict, Optional, List


class WorkflowService:
    """
    Service to extract workflow metadata from documents.
    
    Answers questions like:
    - Does this doctype have a workflow?
    - What is the current workflow state?
    - What transitions are possible?
    - Which roles can perform actions?
    """

    @staticmethod
    def get_workflow_info(doctype: str, doc_name: str) -> Optional[Dict]:
        """
        Extract complete workflow information for a document.
        
        Args:
            doctype: Document type (e.g., "Leave Application")
            doc_name: Document name/ID
            
        Returns:
            Dict with workflow metadata or None if no workflow exists
            
        Structure:
        {
            "workflow_name": "Leave Approval",
            "state_field": "workflow_state",
            "current_state": "Draft",
            "next_states": [...],
            "next_roles": [...]
        }
        """
        try:
            # Check if doctype has a workflow
            workflow_name = WorkflowService.get_workflow_name(doctype)
            
            if not workflow_name:
                return None

            # Get the current state
            doc = frappe.get_doc(doctype, doc_name)
            state_field = WorkflowService.get_state_field(workflow_name)
            current_state = getattr(doc, state_field, None) if state_field else None

            if not current_state:
                return None

            # Get possible transitions
            transitions = WorkflowService.get_transitions(
                workflow_name=workflow_name,
                current_state=current_state,
                doctype=doctype,
                doc_name=doc_name
            )

            # Get allowed roles
            allowed_roles = WorkflowService.get_allowed_roles(
                workflow_name=workflow_name,
                current_state=current_state
            )

            return {
                "workflow_name": workflow_name,
                "state_field": state_field,
                "current_state": current_state,
                "next_states": transitions,
                "next_roles": allowed_roles
            }

        except Exception as e:
            frappe.logger().warning(
                f"WorkflowService: Error extracting workflow for {doctype}/{doc_name}: {str(e)}"
            )
            return None

    @staticmethod
    def get_workflow_name(doctype: str) -> Optional[str]:
        """
        Get the workflow name for a doctype.
        
        Returns:
            Workflow name or None
        """
        try:
            workflow = frappe.db.get_value(
                "Workflow",
                {"document_type": doctype, "is_active": 1},
                "name"
            )
            return workflow
        except Exception:
            return None

    @staticmethod
    def get_state_field(workflow_name: str) -> Optional[str]:
        """
        Get the field name that stores the workflow state.
        
        Usually "workflow_state" but can be custom.
        """
        try:
            state_field = frappe.db.get_value(
                "Workflow",
                workflow_name,
                "workflow_state_field"
            )
            return state_field or "workflow_state"
        except Exception:
            return "workflow_state"

    @staticmethod
    def get_transitions(
        workflow_name: str,
        current_state: str,
        doctype: str,
        doc_name: str
    ) -> List[Dict]:
        """
        Get possible transitions from current state.
        
        Returns:
            List of transitions
            [
                {
                    "from_state": "Draft",
                    "to_state": "Approved",
                    "action": "Approve",
                    "allowed_roles": ["Manager"]
                }
            ]
        """
        try:
            transitions = frappe.get_all(
                "Workflow Transition",
                filters={
                    "parent": workflow_name,
                    "state": current_state
                },
                fields=["name", "state", "next_state", "action", "allowed"]
            )

            result = []
            for transition in transitions:
                result.append({
                    "from_state": transition.get("state"),
                    "to_state": transition.get("next_state"),
                    "action": transition.get("action"),
                    "allowed_roles": WorkflowService._parse_allowed_roles(
                        transition.get("allowed")
                    )
                })

            return result

        except Exception as e:
            frappe.logger().warning(
                f"WorkflowService: Error getting transitions for {workflow_name}/{current_state}: {str(e)}"
            )
            return []

    @staticmethod
    def get_allowed_roles(workflow_name: str, current_state: str) -> List[str]:
        """
        Get roles that can perform any action from current state.
        
        Returns:
            List of role names
        """
        try:
            transitions = frappe.get_all(
                "Workflow Transition",
                filters={
                    "parent": workflow_name,
                    "state": current_state
                },
                fields=["allowed"]
            )

            roles = set()
            for transition in transitions:
                allowed = WorkflowService._parse_allowed_roles(
                    transition.get("allowed")
                )
                roles.update(allowed)

            return list(roles)

        except Exception:
            return []

    @staticmethod
    def _parse_allowed_roles(allowed_field: str) -> List[str]:
        """
        Parse the allowed field which can be:
        - Comma separated: "Role1,Role2"
        - Empty: ""
        
        Returns:
            List of role names
        """
        if not allowed_field:
            return []

        try:
            # Try parsing as JSON first (in case it's stored as JSON)
            return json.loads(allowed_field)
        except (json.JSONDecodeError, TypeError):
            # Fall back to comma-separated
            return [role.strip() for role in allowed_field.split(",") if role.strip()]

    @staticmethod
    def has_workflow(doctype: str) -> bool:
        """
        Quick check if a doctype has an active workflow.
        
        Returns:
            True if workflow exists
        """
        workflow = WorkflowService.get_workflow_name(doctype)
        return workflow is not None

@frappe.whitelist()
def get_workflow_position(doctype: str, docname: str) -> Dict:
    """Where a document sits in its workflow, and what may legitimately be done next.

    Everything pw-server needs to route a card, in one call:
      current_state     -- the workflow state field's value
      is_initial_state  -- nothing transitions INTO this state, so a save here is not
                           an event worth notifying anyone about
      transitions       -- available from this state, WITH `condition` evaluated

    Why not `frappe.model.workflow.get_transitions`: it also filters by the CALLING
    user's roles. pw-server calls with an admin key on behalf of each recipient, so a
    role filter here would answer for the admin, not the recipient. The role check
    therefore stays with the caller, which already routes by each transition's
    `allowed` role. The condition check is what could not be done caller-side -- it
    needs the document and Frappe's own evaluator, and without it a branching state
    (approve under a threshold, escalate over it) offers every branch at once.
    """
    from frappe.model.workflow import get_workflow_name, is_transition_condition_satisfied

    workflow_name = get_workflow_name(doctype)
    if not workflow_name:
        return {"current_state": None, "is_initial_state": False, "transitions": []}

    doc = frappe.get_doc(doctype, docname)
    doc.check_permission("read")

    workflow = frappe.get_cached_doc("Workflow", workflow_name)
    current_state = doc.get(workflow.workflow_state_field)

    transitions = []
    for t in workflow.transitions:
        if t.state != current_state:
            continue
        if not is_transition_condition_satisfied(t, doc):
            continue
        transitions.append({
            "action": t.action,
            "from_state": t.state,
            "to_state": t.next_state,
            "allowed_roles": [t.allowed] if t.allowed else [],
            "allow_self_approval": t.allow_self_approval,
        })

    return {
        "current_state": current_state,
        # Computed over ALL transitions, not the filtered set: a state is initial only
        # if nothing anywhere leads into it.
        "is_initial_state": not any(t.next_state == current_state for t in workflow.transitions),
        "transitions": transitions,
        # How the document could have ARRIVED here. Frappe records only the resulting
        # state -- `apply_workflow` ends with add_comment("Workflow", next_state.state)
        # -- so the name of the action taken exists nowhere in the document's history and
        # can only be recovered by matching the state it came from against the workflow.
        # The caller knows that previous state (the tile it is retiring was raised for),
        # so this lets it name the action exactly instead of guessing from the state name,
        # which cannot tell "Escalate" from "Approve" when both land in an "Approv..."
        # state. No condition is evaluated: a condition gates whether a transition MAY be
        # taken, and by now it already has been.
        "incoming_transitions": [
            {"action": t.action, "from_state": t.state}
            for t in workflow.transitions
            if t.next_state == current_state
        ],
    }
