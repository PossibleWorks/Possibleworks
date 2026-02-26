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
				"Leave Type {0} requires attachments. Please add at least one attachment in the Attachments table before saving."
			).format(frappe.bold(doc.leave_type)),
			title=_("Attachments Required"),
		)
