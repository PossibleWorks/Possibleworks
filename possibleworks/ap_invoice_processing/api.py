# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json
import frappe
from frappe import _

from possibleworks.ap_invoice_processing.openai_service import extract_data_from_file
from possibleworks.ap_invoice_processing.smart_match import perform_smart_match
from possibleworks.ap_invoice_processing.constants import get_extraction_log_doctype
from possibleworks.ap_invoice_processing.doctype.ai_document_processor_settings.ai_document_processor_settings import (
	APProcessorSettings,
)

# Categories of internal messages that should NOT be shown verbatim to the browser.
_INTERNAL_PREFIXES = ("Traceback", "File ", "frappe.", "erpnext.")


def _safe_error_message(exc):
	"""Return a user-facing error string that does not leak internal details."""
	msg = str(exc).strip()
	# If the message looks like an internal traceback or module path, replace it.
	if any(msg.startswith(p) for p in _INTERNAL_PREFIXES) or "\n" in msg:
		return "An internal error occurred during extraction. Please check the Error Log for details."
	return msg


@frappe.whitelist()
def get_ai_access(target_doctype: str | None = None):
	"""Lightweight access check for client-side UI gating (buttons)."""
	target_doctype = (target_doctype or "").strip()
	enabled = APProcessorSettings.is_enabled()
	doctype_enabled = bool(target_doctype) and APProcessorSettings.is_doctype_supported(target_doctype)
	return {
		"allowed": bool(enabled and doctype_enabled),
		"enabled": bool(enabled),
		"doctype_enabled": bool(doctype_enabled),
	}


@frappe.whitelist()
def process_single_invoice(file_url, target_doctype="Purchase Invoice"):
	"""
	API endpoint called from the "Upload & Extract" button.
	1. Gets the File doc by URL
	2. Extracts data using OpenAI
	3. Performs smart matching
	4. Logs the extraction for audit
	5. Returns combined result to frontend
	"""
	try:
		# Find the file doc attached
		file_name = file_url.split("/")[-1]
		file_doc = frappe.get_all("File", filters={"file_url": file_url}, limit=1)
		
		if not file_doc:
			file_doc = frappe.get_all("File", filters={"file_name": file_name}, limit=1)
			
		if not file_doc:
			frappe.throw(_("Could not find the uploaded file document in ERPNext."))
			
		file_name = file_doc[0].name

		# 1. Extract Data
		extraction_result = extract_data_from_file(file_name, target_doctype=target_doctype)
		
		# 2. Smart Match
		parsed_data = extraction_result["parsed"]
		match_result = perform_smart_match(parsed_data, target_doctype=target_doctype)

		# 3. Audit Log
		log_doctype = get_extraction_log_doctype()
		tool_calls_log = extraction_result.get("tool_calls_log") or []
		log_doc = frappe.get_doc({
			"doctype": log_doctype,
			"file": file_name,
			"file_url": file_url,
			"target_doctype": target_doctype,
			"triggered_by": frappe.session.user,
			"status": "Success",
			"raw_openai_response": extraction_result["raw_response"],
			"page_count": extraction_result["page_count"],
			# AI usage & cost
			"model_used": extraction_result.get("model_used"),
			"agent_steps": extraction_result.get("agent_steps"),
			"input_tokens": extraction_result.get("input_tokens"),
			"output_tokens": extraction_result.get("output_tokens"),
			"cached_tokens": extraction_result.get("cached_tokens"),
			"total_tokens": extraction_result.get("total_tokens"),
			"estimated_cost_usd": extraction_result.get("estimated_cost_usd"),
			"tool_calls_log": json.dumps(tool_calls_log, indent=2, default=str),
		})
		log_doc.insert(ignore_permissions=True)

		# 4. Return to frontend
		return {
			"status": "success",
			"parsed_data": parsed_data,
			"match_result": match_result,
			"log_id": log_doc.name
		}

	except Exception as e:
		frappe.log_error("AI Document Single Extraction Failed", frappe.get_traceback())
		# Write a failure audit record so debugging has something to look at
		# even when extraction never produced a result.
		try:
			log_doctype = get_extraction_log_doctype()
			frappe.get_doc({
				"doctype": log_doctype,
				"file_url": file_url,
				"target_doctype": target_doctype,
				"triggered_by": frappe.session.user,
				"status": "Failed",
				"raw_openai_response": frappe.get_traceback(),
			}).insert(ignore_permissions=True)
		except Exception:
			pass  # never let audit-log failure mask the original error
		return {
			"status": "error",
			"message": _safe_error_message(e),
		}


@frappe.whitelist()
def log_user_submission(log_id, final_submitted_values):
	"""
	API to update the audit log with what the user actually submitted,
	comparing it to the original AI extraction.
	"""
	if not log_id:
		return

	log_doctype = get_extraction_log_doctype()

	# Ownership check: only the user who triggered the extraction may update it.
	owner = frappe.db.get_value(log_doctype, log_id, "triggered_by")
	if not owner or owner != frappe.session.user:
		frappe.throw(_("Not permitted to update this extraction log."), frappe.PermissionError)

	if isinstance(final_submitted_values, str):
		final_submitted_values = json.loads(final_submitted_values)

	frappe.db.set_value(
		log_doctype,
		log_id,
		"final_submitted_values",
		json.dumps(final_submitted_values, indent=2)
	)
	frappe.db.commit()
