# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Create the Role Profile every onboarded employee is given.

`Employee` has to be one of its roles. `User.populate_role_profile_roles`
(frappe/core/doctype/user/user.py:259) prunes any role a user holds that no assigned
profile grants:

    self.roles = [r for r in self.roles if r.role in new_roles]

`Employee.update_user()` appends the Employee role and saves the User, so without it
listed here that role would be added and then stripped again on the very next User
save -- silently, and repeatedly.

Idempotent, and additive only: a site that adds a role to this profile keeps it.
"""

import frappe

from possibleworks.onboarding.provisioning import ensure_standard_role_profile


def execute():
	name = ensure_standard_role_profile()
	frappe.logger().info(f"possibleworks: Role Profile {name} is present")
