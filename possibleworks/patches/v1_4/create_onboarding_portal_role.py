# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Create the applicant portal role with Desk access switched off.

`desk_access` defaults to 1 on Role, so this cannot be left to auto-creation: an
applicant would otherwise be able to reach /app. The role intentionally carries no
DocPerm anywhere -- it exists to deny Desk and to mark these users.
"""

import frappe

from possibleworks.onboarding.constants import PORTAL_ROLE


def execute():
	if frappe.db.exists("Role", PORTAL_ROLE):
		# Re-assert the flag: a role edited by hand must not silently regain Desk.
		frappe.db.set_value("Role", PORTAL_ROLE, "desk_access", 0)
		return

	role = frappe.new_doc("Role")
	role.role_name = PORTAL_ROLE
	role.desk_access = 0
	role.is_custom = 0
	role.insert(ignore_permissions=True)
