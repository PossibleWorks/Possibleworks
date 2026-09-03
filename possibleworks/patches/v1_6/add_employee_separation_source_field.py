# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Two fields `Employee Separation` does not ship with.

`created_from_possibleworks` -- the scheduler in pw-cron-jobs-v2 submits exit
checklists once `boarding_begins_on` arrives. Without this flag its only available
filter is `docstatus = 0`, which also matches a draft HR built by hand and is still
editing -- and submitting one is effectively irreversible: it mints a Project and a
Task per row, and cancelling deletes both.

`last_working_day` -- the end date the approving manager set. `boarding_begins_on` is
clamped to today for a short notice period, so it cannot be used to reconstruct this,
and the exit tile has to be able to tell an assignee when the person actually leaves.
Kept here rather than on `Employee.relieving_date`, which stays untouched: setting
that requires `Employee.status = "Left"`, which Frappe refuses while anybody still
reports to the leaver.

Both read-only: they are statements of record, not switches. In particular nobody
should be able to tick the flag to opt a hand-made draft into automatic submission.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from possibleworks.offboarding.constants import (
	LAST_WORKING_DAY_FIELD,
	SEPARATION_DOCTYPE,
	SOURCE_FIELD,
)


def execute():
	if not frappe.db.exists("DocType", SEPARATION_DOCTYPE):
		# hrms not installed on this site.
		return

	meta = frappe.get_meta(SEPARATION_DOCTYPE)
	anchor = (
		"employee_separation_template"
		if meta.has_field("employee_separation_template")
		else "company"
	)

	create_custom_fields(
		{
			SEPARATION_DOCTYPE: [
				{
					"fieldname": LAST_WORKING_DAY_FIELD,
					"label": "Last Working Day",
					"fieldtype": "Date",
					"insert_after": anchor,
					"read_only": 1,
					"description": (
						"The end of the notice period, as approved in PossibleWorks. "
						"The checklist is scheduled backwards from this date."
					),
				},
				{
					"fieldname": SOURCE_FIELD,
					"label": "Created from PossibleWorks",
					"fieldtype": "Check",
					"insert_after": LAST_WORKING_DAY_FIELD,
					"read_only": 1,
					"print_hide": 1,
					"search_index": 1,
					"description": (
						"Set automatically when a manager approves a resignation in PossibleWorks. "
						"The scheduler only submits checklists carrying this flag."
					),
				},
			]
		},
		update=True,
	)
