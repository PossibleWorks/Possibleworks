# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Sandwich Leave Policy -- mechanical helpers only.

Detection, orchestration, idempotency and notification decisions are NOT
here -- they live entirely in the Server Script (see
server_script_reference.txt) by design, so HR can inspect and edit the actual
policy logic from Desk without a code deployment.

This module exists only because Frappe's Server Script sandbox (safe_exec)
cannot `import` another app's module, and can only reach app code through a
whitelisted function via frappe.call(). Every function here is a thin,
single-purpose, side-effecting primitive with no policy decisions of its own
-- the kind of thing that would be identical no matter what triggered it.
"""

import frappe
from frappe.utils import cstr, getdate

from possibleworks.utils.holiday_utils import get_holidays_for_employee


@frappe.whitelist()
def get_holiday_dates(employee, from_date, to_date):
	"""Sorted list of holiday date strings for `employee` within the range
	(inclusive). Thin adapter only: holiday_utils.get_holidays_for_employee
	isn't itself whitelisted, and returns a set of date objects, which isn't
	JSON-safe for frappe.call to hand back to the Server Script."""
	holidays = get_holidays_for_employee(employee, from_date, to_date)
	return sorted(cstr(d) for d in holidays)


@frappe.whitelist()
def cancel_leave_application(name):
	"""Cancel a submitted Leave Application by name -- refunds the leave
	balance the same as any ordinary cancel. No-op if already cancelled."""
	leave_application = frappe.get_doc("Leave Application", name)
	if leave_application.docstatus == 1:
		leave_application.cancel()


@frappe.whitelist()
def force_mark_absent(employee, date, company):
	"""
	Set Attendance = Absent for `employee` on `date`.

	Cancels and deletes whatever Attendance record already exists for that
	date first -- e.g. the "On Leave" row Leave Application submission
	creates, or whatever an unrelated hook produces on cancel -- then creates
	a fresh one, so it's never left layered on top of a stale row.

	NOT unconditional, despite the name: hrms's own Attendance.validate()
	(hrms/hr/doctype/attendance/attendance.py) independently looks up any
	Approved + submitted Leave Application covering this employee/date and
	force-overwrites status to "On Leave"/"Half Day" if one exists --
	regardless of what's set here. The caller MUST cancel the covering Leave
	Application first (see cancel_leave_application) for this to actually end
	up Absent. Calling this before that cancellation silently produces
	"On Leave" instead, with no error -- verified in
	test_helper_force_mark_absent_is_overridden_by_an_active_leave_application.

	Tags the record with custom_sandwich_policy_applied so the calendar/report
	can show it distinctly from an ordinary unauthorized absence.
	"""
	date = getdate(date)

	existing_names = frappe.get_all(
		"Attendance",
		filters={"employee": employee, "attendance_date": date, "docstatus": ["<", 2]},
		pluck="name",
	)
	for existing_name in existing_names:
		attendance = frappe.get_doc("Attendance", existing_name)
		if attendance.docstatus == 1:
			attendance.cancel()
		frappe.delete_doc("Attendance", attendance.name, ignore_permissions=True, force=True)

	attendance = frappe.new_doc("Attendance")
	attendance.employee = employee
	attendance.attendance_date = date
	attendance.status = "Absent"
	attendance.company = company
	attendance.custom_sandwich_policy_applied = 1
	attendance.insert(ignore_permissions=True)
	attendance.submit()
	return attendance.name


