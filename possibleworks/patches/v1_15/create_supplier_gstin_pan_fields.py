# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Adds GSTIN and PAN fields to Supplier. Stock ERPNext has neither (India
Compliance would add them, but it isn't installed), so the PAN the PW app's
New Supplier form already sends was silently dropped on insert.

Fieldnames match India Compliance's (`gstin`, `pan`) so that app can adopt
these fields if it's ever installed. `tax_id` is left as-is: AP invoice
matching (ap_invoice_processing/smart_match.py) still looks suppliers up by it,
and the PW app keeps writing the GSTIN there too.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Supplier": [
				{
					"fieldname": "gstin",
					"label": "GSTIN",
					"fieldtype": "Data",
					"insert_after": "tax_id",
				},
				{
					"fieldname": "pan",
					"label": "PAN",
					"fieldtype": "Data",
					"insert_after": "gstin",
				},
			]
		},
		update=True,
	)
