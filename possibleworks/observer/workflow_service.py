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