# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Adds the Contact Number field to RFQ supplier rows and fills it on existing rows.

New and edited RFQs fill it on save (CustomRequestForQuotation.set_contact_numbers);
this covers rows saved before the field existed.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from possibleworks.finance.queries import supplier_contact_number

FIELDNAME = "pw_contact_number"


def execute():
	# Fixtures sync after patches, so the field may not exist on this site yet.
	create_custom_fields(
		{
			"Request for Quotation Supplier": [
				{
					"fieldname": FIELDNAME,
					"label": "Contact Number",
					"fieldtype": "Data",
					"insert_after": "contact",
					"read_only": 1,
					"description": "Number of the row's contact, filled in from the Contact on save.",
				}
			]
		},
		update=True,
	)

	rows = frappe.get_all(
		"Request for Quotation Supplier",
		filters={"parenttype": "Request for Quotation", "supplier": ("is", "set")},
		fields=["name", "supplier", "contact"],
	)
	numbers = {}
	for row in rows:
		key = (row.supplier, row.contact)
		if key not in numbers:
			numbers[key] = supplier_contact_number(row.supplier, row.contact)
		if numbers[key]:
			frappe.db.set_value(
				"Request for Quotation Supplier", row.name, FIELDNAME, numbers[key], update_modified=False
			)
