# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Adds a per-line "Warranty" field to the buying-cycle item child tables,
next to Description -- lets a supplier record the warranty they're offering
on a quoted item, in both the internal PW app's own forms and the guest
supplier portal (rfq_portal.py).

Deliberately NOT `no_copy`: `get_mapped_doc`'s default child-row copy carries
over any matching, non-`no_copy` fieldname, so this value flows Supplier
Quotation Item -> Purchase Order Item -> Purchase Receipt Item / Purchase
Invoice Item automatically via the existing mappers, with no mapper changes
needed.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

TARGET_DOCTYPES = (
	"Supplier Quotation Item",
	"Purchase Order Item",
	"Purchase Receipt Item",
	"Purchase Invoice Item",
)


def execute():
	custom_fields = {
		doctype: [
			{
				"fieldname": "warranty",
				"label": "Warranty",
				"fieldtype": "Data",
				"insert_after": "description",
			},
		]
		for doctype in TARGET_DOCTYPES
	}

	create_custom_fields(custom_fields, update=True)
