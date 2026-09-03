# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Copying template rows into a boarding checklist.

Shared because `Employee Onboarding Template` and `Employee Separation Template` both
hold their rows in the SAME child doctype, `Employee Boarding Activity`, and both are
consumed by the same `EmployeeBoardingController`. One copier serves both.

Why a copier is needed at all: selecting a template only fills `activities`
CLIENT-side -- `employee_onboarding.js` and `employee_separation.js` each call
`get_onboarding_details` from a field handler. Setting the template link from Python
leaves `activities` empty, and an empty checklist submits to a Project with no tasks
at all. Nothing raises; the record just does nothing.
"""

import frappe

ACTIVITY_DOCTYPE = "Employee Boarding Activity"

# Copied verbatim from the template's rows into the checklist's rows. `task` is
# excluded -- it is written back by the controller when the Task is created.
ACTIVITY_FIELDS = (
	"activity_name",
	"description",
	"user",
	"role",
	"begin_on",
	"duration",
	"task_weight",
	"required_for_employee_creation",
)


def copy_template_activities(template: str, parenttype: str, doc) -> list:
	"""Append `template`'s rows to `doc.activities`, in template order.

	Returns the appended rows so a caller can refine them -- `Employee Separation`
	re-points some of them at a specific user once it knows whose exit this is.
	"""
	rows = frappe.get_all(
		ACTIVITY_DOCTYPE,
		filters={"parent": template, "parenttype": parenttype},
		fields=list(ACTIVITY_FIELDS),
		order_by="idx",
	)

	return [doc.append("activities", row) for row in rows]
