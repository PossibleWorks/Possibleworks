# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Index `Employee Onboarding.employee`.

We look the checklist up by Employee -- in `boarding.ensure_employee_onboarding`, and
by hand whenever somebody asks "which onboarding belongs to this employee". Upstream
ships that field without `search_index`, because in stock flow it is a mostly-empty
field written after the fact rather than a key. In our flow it is set explicitly at
creation, so it is worth indexing.

A Property Setter rather than a Custom Field: the column already exists and already
holds exactly the value we need, so a second Link field would only be a copy that
could drift out of step with it.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Employee Onboarding"):
		# hrms not installed on this site.
		return

	frappe.make_property_setter(
		{
			"doctype": "Employee Onboarding",
			"fieldname": "employee",
			"property": "search_index",
			"value": 1,
			"property_type": "Check",
		},
		is_system_generated=False,
	)
