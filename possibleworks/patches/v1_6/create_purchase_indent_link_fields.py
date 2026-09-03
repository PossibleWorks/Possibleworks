# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add the reverse links from the buying documents back to a Purchase Indent.

The indent sits between a Material Request and the buying documents raised from it
(MR -> PI -> PO/RFQ/SQ). Its rows already carry `material_request` and
`material_request_item`, and the mappers pass those straight through, so the original
request still closes out through Purchase Order's own `status_updater`. These fields
are the other half: without them nothing records which indent produced a given
Purchase Order, and `purchase_indent_status` would have no column to aggregate on.

Deliberately NOT `no_copy`, unlike erpnext's own `material_request` pair on Purchase
Order Item: amending a Purchase Order has to keep pointing at its indent, otherwise
the indent's `ordered_qty` silently drops to zero the moment anyone amends.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

TARGET_DOCTYPES = ("Purchase Order Item", "Request for Quotation Item", "Supplier Quotation Item")


def execute():
	custom_fields = {}

	for doctype in TARGET_DOCTYPES:
		meta = frappe.get_meta(doctype)

		# Sit next to erpnext's own source-document links. Anchoring to a field that
		# does not exist makes validate_insert_after throw, so fall back to item_code,
		# which every one of these child tables has.
		anchor = "material_request_item" if meta.has_field("material_request_item") else "item_code"

		custom_fields[doctype] = [
			{
				"fieldname": "purchase_indent",
				"label": "Purchase Indent",
				"fieldtype": "Link",
				"options": "Purchase Indent",
				"insert_after": anchor,
				"read_only": 1,
				"print_hide": 1,
				"search_index": 1,
			},
			{
				"fieldname": "purchase_indent_item",
				"label": "Purchase Indent Item",
				"fieldtype": "Data",
				"insert_after": "purchase_indent",
				"read_only": 1,
				"hidden": 1,
				"print_hide": 1,
				"search_index": 1,
			},
		]

	# `create_custom_fields` only prefixes `custom_` when `fieldname` is omitted, so
	# these keep their bare names and are addressable as plain columns in the rollup.
	create_custom_fields(custom_fields, update=True)
