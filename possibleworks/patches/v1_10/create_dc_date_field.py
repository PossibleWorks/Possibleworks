# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Adds a DC Date to Purchase Receipt, pairing it with the existing native
`supplier_delivery_note` field -- which the app now labels "DC Number" (see
PurchaseReceipt.ts) rather than its native "Supplier Delivery Note" label, to
read as a matched pair with "Supplier Invoice No" / "Supplier Invoice Date"
(patches/v1_8). No new field needed for the number itself, only the date.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Purchase Receipt": [
				{
					"fieldname": "dc_date",
					"label": "DC Date",
					"fieldtype": "Date",
					"insert_after": "supplier_delivery_note",
					"depends_on": "eval:in_list(['DC', 'Both'], doc.document_received)",
					"allow_on_submit": 1,
				},
			],
		},
		update=True,
	)
