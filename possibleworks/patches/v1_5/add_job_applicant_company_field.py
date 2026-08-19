# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Give `Job Applicant` a company field.

Job Applicant is the one doctype in the hiring chain with nothing the Observer can
resolve a company from -- no `company`, no `department`, no `employee`. Without one,
`PayloadBuilder._resolve_company` returns None, the payload is never built, and every
event for the doctype is written off as Dropped. Since Job Applicant was added to
IMMEDIATE_SEND_DOCTYPES, that would be silent and total.

`job_title` (a Job Opening) would answer the question for records created by the
recruitment desk, but the onboarding flow does not create a Job Opening -- the applicant
it mints exists only to satisfy `Employee Onboarding`'s reqd links.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	if not frappe.db.exists("DocType", "Job Applicant"):
		# hrms not installed on this site.
		return

	create_custom_fields(
		{
			"Job Applicant": [
				{
					"fieldname": "company",
					"label": "Company",
					"fieldtype": "Link",
					"options": "Company",
					"insert_after": "designation",
					"print_hide": 1,
					"search_index": 1,
				}
			]
		},
		update=True,
	)
