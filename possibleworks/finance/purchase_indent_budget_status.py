# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Advances a Purchase Indent's `budget_status` to "Completed" once a
Purchase Invoice against it is submitted -- the last step of

    Exceeds Budget -> Committed -> Approved -> Completed

("Approved" is set by purchase_indent_status.py on Purchase Order submit;
this module only owns the final step.) Traces back through Purchase Invoice
Item's `purchase_indent` field (patches/v1_9), the reverse-link equivalent of
the one Purchase Order Item already had.
"""

import frappe

BUDGET_STATUS_COMPLETED = "Completed"


def update_from_purchase_invoice(doc, method=None):
	"""`doc_events` entry point for Purchase Invoice on_submit."""
	for indent in indents_referenced_by(doc):
		frappe.db.set_value(
			"Purchase Indent", indent, "budget_status", BUDGET_STATUS_COMPLETED, update_modified=False
		)


def indents_referenced_by(doc):
	"""The distinct indents a Purchase Invoice's rows trace back to.

	`row.get` rather than `row.purchase_indent`: the field arrives with
	patches/v1_9, and a Purchase Invoice saved before a site has migrated
	must not raise on the way through this hook.
	"""
	return {
		row.get("purchase_indent") for row in doc.get("items") or [] if row.get("purchase_indent")
	} - {None, ""}
