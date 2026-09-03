# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""The exit checklist, built when a manager approves a resignation in PossibleWorks.

The mirror of `onboarding/boarding.py`, and deliberately shaped the same way: a
site-owned default template matched by title, rows copied in Python, and the checklist
left as a DRAFT for the Project and Tasks to be created later at submit.

Two things differ from onboarding, and both come from the same fact -- **nobody
presses Submit here**. The onboarding checklist waits for HR to open it, assign owners
and submit; a separation is submitted unattended by the pw-cron-jobs-v2 scheduler once
`boarding_begins_on` arrives. So:

  * Every activity must already have an owner when the record is created, or the
    Tasks are assigned to nobody and not one tile is sent. See EXIT_COORDINATOR_ROLE.
  * `boarding_begins_on` is computed rather than copied from a date on the source
    record, because it is both the task anchor AND the scheduler's due signal.

Nothing here touches `Employee.status` or `relieving_date`. Completing the checklist
moves `Employee Separation.boarding_status` to Completed and stops there -- which is
stock HRMS behaviour, and is the agreed scope.
"""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from possibleworks.boarding_activities import copy_template_activities
from possibleworks.offboarding.constants import (
	DEFAULT_SEPARATION_ACTIVITIES,
	DEFAULT_SEPARATION_TEMPLATE_TITLE,
	EXIT_COORDINATOR_ROLE,
	MANAGER_OWNED_ACTIVITIES,
	NOTICE_WINDOW_DAYS,
	LAST_WORKING_DAY_FIELD,
	SEPARATION_DOCTYPE,
	SEPARATION_TEMPLATE_DOCTYPE,
	SOURCE_FIELD,
)

EMPLOYEE_DOCTYPE = "Employee"

# Employee fields carried onto the separation record. `employee_name` is what the
# PossibleWorks tile shows, so it is not merely cosmetic here.
EMPLOYEE_SNAPSHOT_FIELDS = {
	"employee_name": "employee_name",
	"company": "company",
	"department": "department",
	"designation": "designation",
	"grade": "employee_grade",
}


def ensure_exit_coordinator_role() -> str:
	"""Get-or-create the fallback owner role. Idempotent.

	Created in code so no site has to be configured by hand before the first exit --
	the cost of it being absent is a checklist that notifies nobody, and that failure
	is silent.
	"""
	if frappe.db.exists("Role", EXIT_COORDINATOR_ROLE):
		return EXIT_COORDINATOR_ROLE

	role = frappe.new_doc("Role")
	role.role_name = EXIT_COORDINATOR_ROLE
	# Desk access on purpose: the holders of this role work the exit checklist, and
	# the Tasks it creates live in the Desk.
	role.desk_access = 1
	role.insert(ignore_permissions=True)

	return role.name


def ensure_default_separation_template() -> str:
	"""Get-or-create the shared default template, matched by `title`.

	Looked up by title because `autoname` is a series, so the title is neither the
	record name nor unique -- `frappe.db.exists` on a name would never match.

	Only ever created, never updated. Once a site has this template it owns it: an
	admin who rewrites an activity, changes an offset or assigns a specific owner
	should not have that undone the next time somebody resigns.
	"""
	existing = frappe.db.get_value(
		SEPARATION_TEMPLATE_DOCTYPE, {"title": DEFAULT_SEPARATION_TEMPLATE_TITLE}, "name"
	)
	if existing:
		return existing

	role = ensure_exit_coordinator_role()

	template = frappe.new_doc(SEPARATION_TEMPLATE_DOCTYPE)
	template.title = DEFAULT_SEPARATION_TEMPLATE_TITLE

	for activity in DEFAULT_SEPARATION_ACTIVITIES:
		# The role is the floor, applied to every row. `ensure_employee_separation`
		# narrows individual rows to a named user afterwards; anything it cannot
		# resolve keeps this and still reaches somebody.
		template.append("activities", dict(activity, role=role))

	template.flags.ignore_permissions = True
	template.insert(ignore_permissions=True)

	return template.name


def resolve_boarding_begins_on(notice_end_date):
	"""Day zero for the checklist: NOTICE_WINDOW_DAYS before the last working day.

	Clamped to today, because a short or already-elapsed notice period would otherwise
	put day zero in the past and every task would be created overdue.

	Note for the scheduler: because of that clamp, day zero is not always exactly
	`last working day - 7`. A job that matches `boarding_begins_on == today` will miss
	any record clamped to a day the job had already run for. Match `<= today` instead.
	"""
	anchor = add_days(getdate(notice_end_date), -NOTICE_WINDOW_DAYS)
	return max(anchor, getdate(today()))


def validate_holiday_list_available(employee: str) -> None:
	"""Gate: a Holiday List must resolve, or the checklist cannot schedule a task.

	Checked here, at creation, rather than left to surface at submit. Submit is
	unattended -- a throw there lands in a scheduler log where nobody is looking, and
	the exit silently never starts. Raised now, it reaches the manager who is
	approving.

	`EmployeeBoardingController.get_holiday_list()` takes its `if self.employee:`
	branch for a separation and calls `get_holiday_list_for_employee()`, which HRMS
	replaces (`employee_holiday_list` hook) with a resolver that reads ONLY submitted
	`Holiday List Assignment` records -- the employee's own, then their company's.
	"""
	from hrms.utils.holiday_list import get_holiday_list_for_employee

	# raise_exception=False so we can throw our own message, naming the approval that
	# triggered this rather than a bare Employee id.
	if get_holiday_list_for_employee(employee, raise_exception=False):
		return

	company = frappe.db.get_value(EMPLOYEE_DOCTYPE, employee, "company")
	frappe.throw(
		_(
			"No Holiday List is assigned to {0} or to {1}, so the exit checklist would not be able to schedule any task. Create a Holiday List Assignment first."
		).format(frappe.bold(employee), frappe.bold(company or _("their company"))),
		title=_("Holiday List Missing"),
	)


def get_reporting_manager_user(employee: str) -> str | None:
	"""The login of the leaver's reporting manager, if there is one with an account."""
	reports_to = frappe.db.get_value(EMPLOYEE_DOCTYPE, employee, "reports_to")
	if not reports_to:
		return None

	return frappe.db.get_value(EMPLOYEE_DOCTYPE, reports_to, "user_id") or None


def _assign_manager_owned_rows(rows, manager_user: str | None) -> None:
	"""Re-point the manager's activities at the manager specifically.

	`role` is CLEARED on those rows, not left alongside `user`. Frappe unions the two
	(employee_boarding_controller.py:76-93, `users = unique(users + user_list)`), so
	leaving both set would fan a handover task out to every exit coordinator as well
	as the manager, and each of them would get a tile for work that is not theirs.

	With no manager, or a manager with no login, the row keeps the role -- the task
	still reaches someone.
	"""
	if not manager_user:
		return

	for row in rows:
		if row.activity_name in MANAGER_OWNED_ACTIVITIES:
			row.user = manager_user
			row.role = None


def employee_separation_missing(employee: str) -> bool:
	"""True when this Employee has no live separation checklist."""
	return not frappe.db.exists(
		SEPARATION_DOCTYPE, {"employee": employee, "docstatus": ("!=", 2)}
	)


def ensure_employee_separation(employee: str, notice_end_date, resignation_letter_date=None) -> dict:
	"""Create the exit checklist as a DRAFT, or return the one already there.

	Idempotent by `employee`, and it has to be: `Employee Separation` ships with NO
	duplicate guard of its own (unlike `Employee Onboarding`, which has
	`validate_duplicate_employee_onboarding`), so Frappe would happily accept a second
	one, mint a second Project and a second set of Tasks, and send every assignee a
	duplicate tile.

	Draft on purpose. Submitting is what creates the Project and the Tasks, and that
	is done later by the pw-cron-jobs-v2 scheduler once `boarding_begins_on` arrives,
	which leaves HR the window in between to adjust owners and dates.
	"""
	if not frappe.db.exists(EMPLOYEE_DOCTYPE, employee):
		frappe.throw(
			_("Employee {0} does not exist.").format(frappe.bold(employee)),
			frappe.DoesNotExistError,
		)

	existing = frappe.db.get_value(
		SEPARATION_DOCTYPE,
		{"employee": employee, "docstatus": ("!=", 2)},
		["name", "boarding_begins_on", "docstatus"],
		as_dict=True,
	)
	if existing:
		return {
			"name": existing.name,
			"created": False,
			"docstatus": existing.docstatus,
			"boarding_begins_on": str(existing.boarding_begins_on or ""),
		}

	validate_holiday_list_available(employee)

	snapshot = frappe.db.get_value(
		EMPLOYEE_DOCTYPE, employee, list(EMPLOYEE_SNAPSHOT_FIELDS.keys()), as_dict=True
	)

	doc = frappe.new_doc(SEPARATION_DOCTYPE)
	doc.employee = employee
	for source_field, target_field in EMPLOYEE_SNAPSHOT_FIELDS.items():
		value = snapshot.get(source_field)
		if value:
			doc.set(target_field, value)

	doc.boarding_begins_on = resolve_boarding_begins_on(notice_end_date)
	# Not the same thing as day zero: the controller writes this to the Project's
	# `expected_start_date` for a separation (employee_boarding_controller.py:36-40),
	# so it should say when the person actually resigned.
	doc.resignation_letter_date = getdate(resignation_letter_date or today())

	template = ensure_default_separation_template()
	doc.employee_separation_template = template
	rows = copy_template_activities(template, SEPARATION_TEMPLATE_DOCTYPE, doc)
	_assign_manager_owned_rows(rows, get_reporting_manager_user(employee))

	# Both fields arrive via a patch, so both are guarded -- a site mid-migrate should
	# get a usable checklist rather than an exception. LAST_WORKING_DAY_FIELD is the
	# manager's exact end date; `boarding_begins_on` above may have been clamped and so
	# cannot be relied on to reconstruct it. SOURCE_FIELD is what the scheduler filters
	# on so it never submits a draft HR is still editing.
	meta = frappe.get_meta(SEPARATION_DOCTYPE)
	if meta.has_field(LAST_WORKING_DAY_FIELD):
		doc.set(LAST_WORKING_DAY_FIELD, getdate(notice_end_date))
	if meta.has_field(SOURCE_FIELD):
		doc.set(SOURCE_FIELD, 1)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	return {
		"name": doc.name,
		"created": True,
		"docstatus": doc.docstatus,
		"boarding_begins_on": str(doc.boarding_begins_on),
	}
