# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add the Employee fields the Onboarding module writes into.

`onboarding_applicant` is the reverse link. It matters more than it looks: it is set
BEFORE `Employee.insert()`, so it goes out in the same INSERT statement. The Observer
calls `frappe.db.commit()` inside `Employee.after_insert` (Employee is in
IMMEDIATE_SEND_DOCTYPES), which means a failure after that point cannot be rolled
back -- this column is what makes an orphaned Employee discoverable, and it is what
the duplicate guard walks the amendment chain against.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	meta = frappe.get_meta("Employee")

	# `pan_number` only exists where hrms.regional.india.setup ran; anchoring to a
	# missing field makes validate_insert_after throw DoesNotExistError.
	if meta.has_field("pan_number"):
		aadhaar_anchor = "pan_number"
	elif meta.has_field("passport_number"):
		aadhaar_anchor = "passport_number"
	else:
		aadhaar_anchor = "date_of_birth"

	link_anchor = "job_applicant" if meta.has_field("job_applicant") else "company"

	custom_fields = {
		"Employee": [
			{
				"fieldname": "onboarding_applicant",
				"label": "Onboarding Applicant",
				"fieldtype": "Link",
				"options": "Onboarding Applicant",
				"insert_after": link_anchor,
				"read_only": 1,
				"no_copy": 1,
				"print_hide": 1,
				"search_index": 1,
			},
			{
				"fieldname": "aadhar_number",
				"label": "Aadhaar Number",
				"fieldtype": "Data",
				"length": 12,
				"insert_after": aadhaar_anchor,
				"print_hide": 1,
				"translatable": 0,
			},
		]
	}

	# `create_custom_fields` only applies the `custom_` prefix when `fieldname` is
	# omitted, so these keep their bare names and map cleanly from the applicant.
	create_custom_fields(custom_fields, update=True)
