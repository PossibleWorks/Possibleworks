# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Adds a free-text "Make/Model" field to Supplier Quotation Item, next to
Description -- lets a supplier record the brand/model they're quoting
against, in both the internal PW app's own Supplier Quotation form and the
guest supplier portal (rfq_portal.py).
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Supplier Quotation Item": [
				{
					"fieldname": "make_model",
					"label": "Make/Model",
					"fieldtype": "Data",
					"insert_after": "description",
				},
			],
		},
		update=True,
	)
