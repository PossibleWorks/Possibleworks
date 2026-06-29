# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and Contributors
# License: MIT

from datetime import datetime, date as date_type, time as dt_time

import frappe
from frappe import _
from frappe.utils import getdate, add_days, flt


# def share_with_hierarchy_approvers(doc, method=None):
# 	"""Share leave application with all managers up the reports_to chain."""
# 	if not doc.employee:
# 		return

# 	current_employee = doc.employee
# 	visited = set()

# 	while current_employee and current_employee not in visited:
# 		visited.add(current_employee)

# 		reports_to = frappe.db.get_value("Employee", current_employee, "reports_to")
# 		if not reports_to:
# 			break

# 		manager_user = frappe.db.get_value("Employee", reports_to, "user_id")
# 		if manager_user and not frappe.has_permission(doc=doc, ptype="read", user=manager_user):
# 			frappe.share.add_docshare(
# 				doc.doctype,
# 				doc.name,
# 				manager_user,
# 				read=1,
# 				write=1,
# 				submit=1,
# 				flags={"ignore_share_permission": True},
# 			)

# 		current_employee = reports_to


def reconstruct_attendance_on_leave_cancel(doc, method=None):
	"""
	When a leave application is cancelled (via workflow or direct cancel),
	cancel the On Leave / Half Day attendance records and reconstruct from
	Employee Checkins for each date in the leave range.

	- Past dates with checkins  → mark Present / Half Day / Absent via shift thresholds
	- Past dates without checkins → mark Absent
	- Future dates (>= today)   → skip
	- Holidays                  → skip
	"""
	from hrms.hr.doctype.attendance.attendance import mark_attendance
	from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift
	from possibleworks.utils.holiday_utils import get_holidays_for_employee

	employee = doc.employee
	from_date = getdate(doc.from_date)
	to_date = getdate(doc.to_date)
	today = date_type.today()

	try:
		holiday_dates = get_holidays_for_employee(employee, from_date, to_date)
	except Exception:
		holiday_dates = set()

	_delete_cancelled_attendance(employee, from_date, to_date)

	current = from_date
	while current <= to_date:
		if current >= today:
			current = getdate(add_days(current, 1))
			continue

		is_holiday = current in holiday_dates

		# noon timestamp — safely inside any normal shift window for resolution
		timestamp = datetime.combine(current, dt_time(12, 0))
		shift_details = get_employee_shift(employee, timestamp, consider_default_shift=True)

		if shift_details:
			checkins = _get_checkins_for_shift(employee, shift_details)
			if checkins:
				mark_on_holidays = frappe.db.get_value(
					"Shift Type", shift_details.shift_type.name, "mark_auto_attendance_on_holidays"
				)
				if is_holiday and not mark_on_holidays:
					current = getdate(add_days(current, 1))
					continue
				_process_and_mark(checkins, current, shift_details.shift_type)
			else:
				if is_holiday:
					current = getdate(add_days(current, 1))
					continue
				mark_attendance(employee, current, "Absent", shift=shift_details.shift_type.name)
		# no shift assigned — skip

		current = getdate(add_days(current, 1))


def _delete_cancelled_attendance(employee, from_date, to_date):
	cancelled = frappe.get_all(
		"Attendance",
		filters={
			"employee": employee,
			"attendance_date": ["between", [from_date, to_date]],
			"docstatus": 2,
		},
		pluck="name",
	)
	for name in cancelled:
		frappe.delete_doc("Attendance", name, ignore_permissions=True, force=True)


def _get_checkins_for_shift(employee, shift_details):
	return frappe.get_all(
		"Employee Checkin",
		filters={
			"employee": employee,
			"time": ["between", [shift_details.actual_start, shift_details.actual_end]],
			"skip_auto_attendance": 0,
		},
		fields=[
			"name", "employee", "log_type", "time", "shift",
			"shift_start", "shift_end", "shift_actual_start", "shift_actual_end",
			"device_id", "overtime_type",
		],
		order_by="time asc",
	)



def _process_and_mark(checkins, attendance_date, shift_type):
	"""Determine attendance status from checkins and create the attendance record."""
	from hrms.hr.doctype.employee_checkin.employee_checkin import mark_attendance_and_link_log

	shift_type_doc = frappe.get_doc("Shift Type", shift_type.name)
	threshold_absent = flt(shift_type_doc.working_hours_threshold_for_absent)
	threshold_half_day = flt(shift_type_doc.working_hours_threshold_for_half_day)

	(status, working_hours, late_entry, early_exit, in_time, out_time) = shift_type_doc.get_attendance(
		checkins, threshold_absent, threshold_half_day
	)

	mark_attendance_and_link_log(
		checkins,
		status,
		attendance_date,
		working_hours,
		late_entry,
		early_exit,
		in_time,
		out_time,
		shift_type_doc.name,
	)


def validate_custom_attachments_required(doc, method=None):
	"""
	On save: if the selected Leave Type has "Attachments Required"
	(custom_attachments_required) checked, ensure at least one attachment
	is added in custom_attachments.
	"""
	if not doc.leave_type:
		return

	attachments_required = frappe.db.get_value(
		"Leave Type",
		doc.leave_type,
		"custom_attachments_required",
	)

	if not attachments_required:
		return

	# custom_attachments is a Table (Leave Supporting Documents)
	attachments = doc.get("custom_attachments") or []
	if not attachments or len(attachments) == 0:
		frappe.throw(
			_(
				"Attachment is mandatory for {0}. Please upload the required document to proceed."
			).format(frappe.bold(doc.leave_type)),
			title=_("Attachment Required"),
		)
