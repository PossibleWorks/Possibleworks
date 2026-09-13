# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add the 'Employee Letters' section (Section Break + HTML field) to the
Employee doctype. The HTML field is the mount point rendered by
public/js/employee/employee_letters.js."""

from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Employee": [
				{
					"fieldname": "custom_employee_letters_sb",
					"label": "Employee Letters",
					"fieldtype": "Section Break",
					"insert_after": "relieving_date",
					"collapsible": 1,
				},
				{
					"fieldname": "custom_employee_letters_html",
					"label": "Employee Letters",
					"fieldtype": "HTML",
					"insert_after": "custom_employee_letters_sb",
				},
				# Close the letters section so it spans full width and the
				# remaining exit fields (Leave Encashed, etc.) flow below it.
				{
					"fieldname": "custom_employee_letters_end_sb",
					"label": "",
					"fieldtype": "Section Break",
					"insert_after": "custom_employee_letters_html",
				},
			]
		},
		ignore_validate=True,
		update=True,
	)
