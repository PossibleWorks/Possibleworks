# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Keeps a Purchase Indent's `ordered_qty`, `% Ordered` and status in step with the
Purchase Orders raised from it.

Purchase Order drives Material Request's `per_ordered` through its own `status_updater`
(see `purchase_order.py`), but that list is defined on erpnext's controller and cannot
be extended from here without overriding the class. Recomputing from a `doc_events`
hook is the cheaper equivalent: it is idempotent, derives everything from submitted
rows rather than incrementing a counter, and so cannot drift if an event is ever missed.
"""

import frappe
from frappe.query_builder.functions import Sum
from frappe.utils import flt

# `per_ordered` is a rounded percentage, so an indent whose lines are all covered can
# land a hair under 100 through float division. Treat that as fully ordered.
FULLY_ORDERED_THRESHOLD = 99.99

# Statuses that describe a human decision rather than order progress. Never overwritten.
TERMINAL_STATUSES = ("Stopped", "Cancelled")


def update_from_purchase_order(doc, method=None):
	"""`doc_events` entry point for Purchase Order submit / cancel / update-after-submit."""
	for indent in indents_referenced_by(doc):
		update_ordered_qty(indent)


def indents_referenced_by(doc):
	"""The distinct indents a buying document draws from.

	`row.get` rather than `row.purchase_indent` on purpose: the field arrives with
	`patches/v1_6/create_purchase_indent_link_fields`, and a Purchase Order saved on a
	site that has not migrated yet must not raise on the way through this hook.
	"""
	return {
		row.get("purchase_indent") for row in doc.get("items") or [] if row.get("purchase_indent")
	} - {None, ""}


def update_ordered_qty(purchase_indent):
	"""Recompute one indent from every submitted Purchase Order row pointing at it."""
	indent = frappe.get_doc("Purchase Indent", purchase_indent)

	# Query builder rather than `frappe.get_all`: Frappe 16 rejects an aggregate written
	# as a string in `fields` ("SQL functions are not allowed as strings in SELECT").
	po_item = frappe.qb.DocType("Purchase Order Item")
	ordered_by_row = dict(
		frappe.qb.from_(po_item)
		.select(po_item.purchase_indent_item, Sum(po_item.stock_qty))
		.where((po_item.purchase_indent == purchase_indent) & (po_item.docstatus == 1))
		.groupby(po_item.purchase_indent_item)
		.run()
	)

	requested_total = ordered_total = 0.0

	for row in indent.items:
		row_ordered = flt(ordered_by_row.get(row.name))

		if flt(row.ordered_qty) != row_ordered:
			# update_modified=False: this fires from another document's submit, and
			# bumping `modified` here would hand a "Document has been modified" error
			# to anyone who happens to have the indent open.
			frappe.db.set_value(
				"Purchase Indent Item", row.name, "ordered_qty", row_ordered, update_modified=False
			)

		requested_total += flt(row.stock_qty)
		# Capped per row so one over-ordered line cannot mask another that is short.
		ordered_total += min(row_ordered, flt(row.stock_qty))

	per_ordered = flt(ordered_total / requested_total * 100, 2) if requested_total else 0.0

	frappe.db.set_value(
		"Purchase Indent",
		purchase_indent,
		{"per_ordered": per_ordered, "status": resolve_status(indent, per_ordered)},
		update_modified=False,
	)


def resolve_status(indent, per_ordered):
	"""Order progress for a submitted indent, leaving human decisions alone."""
	if indent.docstatus != 1 or indent.status in TERMINAL_STATUSES:
		return indent.status

	if per_ordered >= FULLY_ORDERED_THRESHOLD:
		return "Ordered"
	if per_ordered > 0:
		return "Partially Ordered"
	return "Pending"
