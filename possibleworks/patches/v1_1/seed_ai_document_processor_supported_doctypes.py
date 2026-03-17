# Copyright (c) 2026, Possibleworks and contributors

import frappe

from possibleworks.ap_invoice_processing.constants import (
	ROLLOUT_DOCTYPES,
	get_settings_doctype,
)


def execute():
	settings_doctype = get_settings_doctype()
	if not frappe.db.exists("DocType", settings_doctype):
		return

	settings = frappe.get_single(settings_doctype)
	current_rows = list(settings.get("supported_doctypes") or [])
	allowed_set = set(ROLLOUT_DOCTYPES)

	# Keep only allowed rows.
	filtered = [row for row in current_rows if row.document_type in allowed_set]
	settings.set("supported_doctypes", filtered)

	existing = {row.document_type for row in settings.get("supported_doctypes") if row.document_type}
	for dt in ROLLOUT_DOCTYPES:
		if dt not in existing:
			settings.append("supported_doctypes", {"document_type": dt, "enabled": 1})

	# Ensure all rollout rows are enabled by default.
	for row in settings.get("supported_doctypes"):
		if row.document_type in allowed_set:
			row.enabled = 1

	settings.save(ignore_permissions=True)
