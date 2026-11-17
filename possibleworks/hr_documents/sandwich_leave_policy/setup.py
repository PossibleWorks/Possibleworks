# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Sandwich Leave Policy -- one-time, site-scoped Custom Field setup.

WHY THIS ISN'T IN hooks.py `fixtures`
--------------------------------------
`bench migrate` syncs every installed app's fixtures into whichever site is
being migrated (see possibleworks/utils/custom_field_sync.py for the fuller
explanation). Salary Slip and Attendance are ordinary transactional
doctypes used by every tenant on this bench, not a settings hub like Policy
Configuration -- so a "Sandwich Leave Days" field has no business showing up
on Nyas, GRIET, or any other site that never uses this feature. This module
is plain code with no hooks and no scheduler entry: shipping it changes
nothing anywhere until a site explicitly runs it.

USAGE -- run once per site that actually enables the Sandwich Leave Policy:

    bench --site gvs-hris-prod execute \
        possibleworks.hr_documents.sandwich_leave_policy.setup.create_custom_fields
"""

import frappe


def create_custom_fields():
	_create_if_missing(
		dt="Salary Slip",
		fieldname="custom_sandwich_leave_days",
		fieldtype="Int",
		label="Sandwich Leave Days",
		insert_after="payment_days",
		read_only=1,
		description=(
			"Number of extra days deducted from Payment Days under the Sandwich Leave Policy."
		),
	)
	_create_if_missing(
		dt="Attendance",
		fieldname="custom_sandwich_policy_applied",
		fieldtype="Check",
		label="Sandwich Policy Applied",
		insert_after="status",
		read_only=1,
		description=(
			"Set when this Absent record was created by the Sandwich Leave Policy script, "
			"so the calendar/report can distinguish it from an ordinary unauthorized absence."
		),
	)
	frappe.db.commit()


def _create_if_missing(dt, fieldname, **field_kwargs):
	name = f"{dt}-{fieldname}"
	if frappe.db.exists("Custom Field", name):
		print(f"{name} already exists, skipping.")
		return

	frappe.get_doc(
		{
			"doctype": "Custom Field",
			"dt": dt,
			"fieldname": fieldname,
			**field_kwargs,
		}
	).insert(ignore_permissions=True)
	print(f"Created {name}.")
