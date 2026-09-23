# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add a per-line "Payment Terms" field to the buying-cycle item child tables.

Supplier Quotation has no payment-terms concept at all in core ERPNext (unlike
Purchase Order/Invoice, which get a header `payment_terms_template` + generated
schedule) -- so a per-item Payment Term is new, not a duplicate of something that
already exists higher up the document.

Deliberately NOT `no_copy`: `get_mapped_doc`'s default child-row copy carries over
any matching, non-`no_copy` fieldname, so this value flows Supplier Quotation Item
-> Purchase Order Item -> Purchase Invoice Item automatically via the existing
`make_purchase_order`/`make_purchase_invoice` mappers, with no mapper changes needed.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

TARGET_DOCTYPES = ("Supplier Quotation Item", "Purchase Order Item", "Purchase Invoice Item")


def execute():
	custom_fields = {}

	for doctype in TARGET_DOCTYPES:
		meta = frappe.get_meta(doctype)

		# Sit next to the delivery-date field where it exists; every one of these
		# child tables has item_code as a safe universal fallback anchor.
		anchor = "expected_delivery_date" if meta.has_field("expected_delivery_date") else "item_code"

		custom_fields[doctype] = [
			{
				"fieldname": "payment_terms",
				"label": "Payment Terms",
				"fieldtype": "Link",
				"options": "Payment Term",
				"insert_after": anchor,
			},
		]

	create_custom_fields(custom_fields, update=True)
