# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""The new employee's login account.

Two deliberate orderings, both about what a partial failure leaves behind.

The **User is created before the Employee**, because `Employee.user_id` has to point
at a real account at insert time and `Employee.on_update` is what appends the Employee
role and creates the User Permissions. Everything up to `Employee.insert()` is in one
transaction, so a throw anywhere in that window rolls the account back with it.

The **role profile is assigned after the Employee exists**. If it were applied at
creation time, a failure in between would leave a System Manager account with no
Employee attached to it. Created bare, the worst case is a powerless account -- and
because that happens pre-commit, even that is rolled back.
"""

import frappe
from frappe import _
from frappe.utils import validate_email_address

from possibleworks.onboarding.constants import (
	STANDARD_ROLE_PROFILE,
	STANDARD_ROLE_PROFILE_ROLES,
)

USER_DOCTYPE = "User"
ROLE_PROFILE_DOCTYPE = "Role Profile"


def validate_work_email_available(applicant) -> None:
	"""`before_submit` gate: the work email must be free to become a login.

	Refuses on ANY existing User, which is the safe reading of an ambiguous situation.
	A pre-existing account at this address is either a leaver (whose password they may
	still know), a recycled address now pointing at a different person, or an account
	already tied to another Employee -- and we cannot tell which apart reliably. The
	cost of refusing is that HR supplies a different address or an administrator clears
	the old account; the cost of guessing wrong is handing someone else's credentials a
	live System Manager login.

	Consequence worth knowing: if IT pre-creates the Frappe account before day one,
	this refuses. That is the trade accepted when the safe branch was chosen.
	"""
	email = (applicant.company_email or "").strip()

	if not email:
		frappe.throw(
			_(
				"Work Email is required before submitting -- it becomes this employee's login. Set it, then submit."
			),
			title=_("Work Email Required"),
		)

	validate_email_address(email, throw=True)

	# The applicant's portal login is a Website User keyed on `personal_email`. Reusing
	# it would silently promote an outsider's applicant account into a System Manager.
	if applicant.personal_email and email.lower() == applicant.personal_email.lower():
		frappe.throw(
			_(
				"Work Email cannot be the same as Personal Email. {0} is already the applicant's portal login, and reusing it would turn that account into a staff login."
			).format(frappe.bold(email)),
			title=_("Work Email Must Differ"),
		)

	existing = frappe.db.exists(USER_DOCTYPE, email)
	if not existing:
		return

	linked = frappe.db.get_value("Employee", {"user_id": existing}, ["name", "employee_name"], as_dict=True)
	if linked:
		frappe.throw(
			_("{0} is already the login for Employee {1} ({2}). Use a different Work Email.").format(
				frappe.bold(email), frappe.bold(linked.name), linked.employee_name
			),
			title=_("Work Email Already In Use"),
		)

	frappe.throw(
		_(
			"A user account already exists for {0}. Use a different Work Email, or have an administrator remove that account first -- it is not safe to attach a new employee to an existing login."
		).format(frappe.bold(email)),
		title=_("Work Email Already In Use"),
	)


def create_employee_user(applicant) -> str:
	"""Create the login. No roles yet -- see the module docstring.

	`user_type` is left alone on purpose: `User.set_system_user()` derives it from
	whether any role carries desk access, so this account starts as a Website User and
	is promoted the moment `Employee.update_user()` appends the Employee role.
	"""
	user = frappe.new_doc(USER_DOCTYPE)
	user.email = applicant.company_email
	user.first_name = applicant.first_name or applicant.applicant_name or applicant.company_email
	user.last_name = applicant.last_name
	user.enabled = 1
	# No welcome mail, ever. Nothing here has told the employee their password.
	user.send_welcome_email = 0
	user.flags.ignore_permissions = True
	user.insert(ignore_permissions=True)

	return user.name


def assign_standard_role_profile(applicant, employee: str) -> None:
	"""Attach the standard profile to the Employee's login. Idempotent.

	Runs after `Employee.insert()`, so `update_user()` has already appended the
	Employee role; saving here re-syncs roles from the profile, which is why Employee
	has to be one of the profile's roles.
	"""
	user_name = frappe.db.get_value("Employee", employee, "user_id")
	if not user_name:
		return

	if not frappe.db.exists(ROLE_PROFILE_DOCTYPE, STANDARD_ROLE_PROFILE):
		# A site that has not run the patch yet. Say so rather than failing silently:
		# the employee would otherwise end up with only the auto-added Employee role.
		frappe.throw(
			_("Role Profile {0} does not exist. Run `bench migrate` to create it.").format(
				frappe.bold(STANDARD_ROLE_PROFILE)
			)
		)

	user = frappe.get_doc(USER_DOCTYPE, user_name)
	if any(row.role_profile == STANDARD_ROLE_PROFILE for row in user.get("role_profiles") or []):
		return

	user.append("role_profiles", {"role_profile": STANDARD_ROLE_PROFILE})
	user.flags.ignore_permissions = True
	user.save(ignore_permissions=True)


def ensure_standard_role_profile() -> str:
	"""Create the profile if absent, and top up any role it is missing.

	Never removes a role a site has added of its own accord -- editing a Role Profile
	re-saves every user holding it (`RoleProfile.update_all_users`), so silently
	dropping one would strip it across the company.
	"""
	if frappe.db.exists(ROLE_PROFILE_DOCTYPE, STANDARD_ROLE_PROFILE):
		profile = frappe.get_doc(ROLE_PROFILE_DOCTYPE, STANDARD_ROLE_PROFILE)
	else:
		profile = frappe.new_doc(ROLE_PROFILE_DOCTYPE)
		profile.role_profile = STANDARD_ROLE_PROFILE

	present = {row.role for row in profile.get("roles") or []}
	missing = [role for role in STANDARD_ROLE_PROFILE_ROLES if role not in present]

	for role in missing:
		if frappe.db.exists("Role", role):
			profile.append("roles", {"role": role})

	if profile.is_new():
		profile.insert(ignore_permissions=True)
	elif missing:
		profile.save(ignore_permissions=True)

	return profile.name
