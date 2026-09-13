# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add the 'Letters' tab to the Employee doctype.

The general-purpose letters (Service Certificate, Visa Letter, and anything HR
creates) live here; the exit letters stay in the collapsible section next to the
relieving fields, added by v1_1.create_employee_letter_section.

Both are mount points for public/js/employee/employee_letters.js, which renders
one group of cards per `Employee Letter Template.placement`.
"""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

# `old_parent` is the last field of the Employee Exit tab (a hidden tree field),
# so anchoring here drops the new tab between Employee Exit and Connections
# without pulling any standard field into it.
INSERT_AFTER = "old_parent"


def execute():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "custom_letters_tab",
					"label": "Letters",
					"fieldtype": "Tab Break",
					"insert_after": INSERT_AFTER,
				},
				{
					"fieldname": "custom_letters_html",
					"label": "Letters",
					"fieldtype": "HTML",
					"insert_after": "custom_letters_tab",
				},
			]
		},
		ignore_validate=True,
		update=True,
	)
