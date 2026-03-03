# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT

import frappe
from frappe import _


def validate_custom_attachments_required(doc, method=None):
	"""
	On save: if the selected Leave Type has "Attachments Required"
	(custom_attachments_required) checked, ensure at least one attachment
	is added in custom_attachments.
	"""
	if not doc.leave_type:
		return

	attachments_required = frappe.db.get_value(
		"Leave Type",
		doc.leave_type,
		"custom_attachments_required",
	)

	if not attachments_required:
		return

	# custom_attachments is a Table (Leave Supporting Documents)
	attachments = doc.get("custom_attachments") or []
	if not attachments or len(attachments) == 0:
		frappe.throw(
			_(
				"Attachment is mandatory for {0}. Please upload the required document to proceed."
			).format(frappe.bold(doc.leave_type)),
			title=_("Attachment Required"),
		)
