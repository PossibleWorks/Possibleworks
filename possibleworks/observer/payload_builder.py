"""
Payload Builder
Constructs standardized JSON payloads for workflow events.
"""

import frappe
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from .workflow_service import WorkflowService
from .settings_helper import SettingsHelper


class PayloadBuilder:
    """
    Builds standardized JSON payloads for events.
    
    Example output:
    {
        "timestamp": "2024-01-15T10:30:45.123Z",
        "event_type": "on_update",
        "user": "Administrator",
        "client": {
            "name": "GRIET",
            "environment": "UAT"
        },
        "document": {
            "doctype": "Leave Application",
            "name": "LA-2024-001",
            "status": "Approved"
        },
        "workflow": {
            "workflow_name": "Leave Approval",
            "current_state": "Approved",
            "transitions": [...]
        }
    }
    """

    @staticmethod
    def build_payload(doc: Any, event_type: str) -> Optional[Dict]:
        """
        Build a complete event payload.
        
        Args:
            doc: Frappe document object
            event_type: Event type (after_insert, on_update, etc.)
            
        Returns:
            Dictionary payload or None if payload building failed
        """
        try:
            payload = {
                "timestamp": PayloadBuilder._get_timestamp(),
                "event_type": event_type,
                "user": frappe.session.user,
                "client": PayloadBuilder._build_client_info(),
                "document": doc.as_dict(),
                "workflow": PayloadBuilder._build_workflow_info(doc),
            }

            return payload

        except Exception as e:
            frappe.logger().error(
                f"PayloadBuilder: Error building payload for {doc.doctype}/{doc.name}: {str(e)}"
            )
            return None

    @staticmethod
    def _get_timestamp() -> str:
        """
        Get ISO 8601 formatted timestamp.
        
        Returns:
            "2024-01-15T10:30:45.123Z"
        """
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _build_client_info() -> Dict[str, str]:
        """
        Build client information from Possibleworks Settings.
        
        Returns:
            {
                "name": "GRIET",
                "environment": "UAT"
            }
        """
        return {
            "name": SettingsHelper.get_client_name(),
            "environment": SettingsHelper.get_client_environment(),
        }

    @staticmethod
    def _build_document_info(doc: Any) -> Dict[str, Any]:
        """
        Build document metadata.
        
        Returns:
            {
                "doctype": "Leave Application",
                "name": "LA-2024-001",
                "status": "Approved",
                ...
            }
        """
        try:
            doc_dict = doc.as_dict()
            
            # Include key fields
            document_info = {
                "doctype": doc.doctype,
                "name": doc.name,
            }

            # Add status/state if exists
            if hasattr(doc, "status"):
                document_info["status"] = doc.status
            
            if hasattr(doc, "workflow_state"):
                document_info["workflow_state"] = doc.workflow_state

            # Add custom fields if they exist
            # This allows flexibility for different doctypes
            important_fields = [
                "status", "state", "approval_status", 
                "employee", "from_date", "to_date",
                "subject", "description"
            ]
            
            for field in important_fields:
                if field in doc_dict:
                    document_info[field] = doc_dict[field]

            return document_info

        except Exception as e:
            frappe.logger().warning(
                f"PayloadBuilder: Error building document info: {str(e)}"
            )
            return {
                "doctype": doc.doctype,
                "name": doc.name,
            }

    @staticmethod
    def _build_workflow_info(doc: Any) -> Optional[Dict]:
        """
        Build workflow information if the document has a workflow.
        
        Returns:
            Workflow dict or None
        """
        try:
            workflow_info = WorkflowService.get_workflow_info(doc.doctype, doc.name)
            return workflow_info

        except Exception as e:
            frappe.logger().warning(
                f"PayloadBuilder: Error building workflow info: {str(e)}"
            )
            return None

    @staticmethod
    def payload_to_json(payload: Dict) -> str:
        """
        Convert payload to JSON string.
        
        Args:
            payload: Payload dictionary
            
        Returns:
            JSON string
        """
        try:
            return json.dumps(payload, default=str, indent=None)
        except Exception as e:
            frappe.logger().error(f"PayloadBuilder: Error converting payload to JSON: {str(e)}")
            return "{}"