# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Email Template has no built-in way to classify what a template is for -- HR,
Finance, or anything else -- so every template (interview reminders, leave
notices, dispatch emails, ...) shows up in every Link field that points at it,
including the Finance module's own (e.g. Request for Quotation's Email
Template field). This tags a template as Finance-relevant so those fields can
filter down to just the ones that make sense there.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Email Template": [
				{
					"fieldname": "is_finance_template",
					"label": "Is Finance Template",
					"fieldtype": "Check",
					"insert_after": "subject",
					"default": "0",
					"in_standard_filter": 1,
				},
			],
		},
		update=True,
	)
