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

        Returns None (and logs a warning) if company or tenant_id cannot be resolved,
        which causes the observer to skip queuing the event entirely.
        """
        try:
            company = PayloadBuilder._resolve_company(doc)
            if not company:
                frappe.logger().warning(
                    f"PayloadBuilder: Skipping — could not resolve company. "
                    f"doctype={doc.doctype} name={doc.name} event_type={event_type}"
                )
                return None

            tenant_id = frappe.db.get_value("Company", company, "custom_tenant_id") or ""
            if not tenant_id:
                frappe.logger().warning(
                    f"PayloadBuilder: Skipping — company '{company}' has no custom_tenant_id. "
                    f"doctype={doc.doctype} name={doc.name}"
                )
                return None

            payload = {
                "timestamp": PayloadBuilder._get_timestamp(),
                "event_type": event_type,
                "tenant_id": tenant_id,
                "company": company,
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
    def _resolve_company(doc: Any) -> Optional[str]:
        """
        Resolve company from a live Frappe document using a fallback chain.

          1. doc.company          — direct field (most HR doctypes)
          2. doc.employee         — Employee Checkin and similar
          3. User doctype         — find linked Employee by user_id
          4. doc.department       — last resort

        Returns company name, or None if unresolvable.
        """
        # 1. Direct field
        if getattr(doc, "company", None):
            return doc.company

        # 2. Via employee link (e.g. Employee Checkin)
        if getattr(doc, "employee", None):
            company = frappe.db.get_value("Employee", doc.employee, "company")
            if company:
                return company

        # 3. User doctype — find company via linked Employee record
        if doc.doctype == "User":
            company = frappe.db.get_value("Employee", {"user_id": doc.name}, "company")
            if company:
                return company

        # 4. Via department link
        if getattr(doc, "department", None):
            company = frappe.db.get_value("Department", doc.department, "company")
            if company:
                return company

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