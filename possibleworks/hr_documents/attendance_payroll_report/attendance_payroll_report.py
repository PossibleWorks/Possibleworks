# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Emails payroll a per-cycle attendance exception report: every employee marked
Absent or Half Day in a payroll period, enriched with any still-pending
regularization (Attendance Request / Leave Application), actual check-in/out
punches -- including off-shift ones, which never attach to an Attendance
record -- and whether each date was a holiday on that specific employee's
calendar, independent of what Attendance.status says.

Two callers share `send_attendance_exception_report`:
  - `daily_attendance_report_dispatch` (hooks.py scheduler_events["daily"]),
    which fires the day after a payroll cycle ends, using
    `possibleworks.utils.payroll_period._get_period_boundaries` (the same
    Monthly/Custom cycle logic already used by the payroll compliance export)
    to detect that boundary.
  - The "Send Attendance Report" button on the Payroll Period form
    (public/js/payroll_period_attendance_report.js), which can target any
    from_date/to_date, not just the last completed cycle -- e.g. to resend
    after attendance corrections.

`custom_last_auto_report_sent_till` on Payroll Period is a dedup guard read
ONLY by the scheduler dispatcher, so it never runs the same cycle twice in one
day; the manual button always sends regardless of that field.
"""

import re

import frappe
from frappe import _
from frappe.utils import add_days, cstr, flt, get_datetime, getdate, nowdate

from possibleworks.hr_documents.attendance_payroll_report.excel_builder import (
	build_attendance_exception_workbook,
)
from possibleworks.utils.branded_email import render_branded_email
from possibleworks.utils.holiday_utils import build_holiday_cache, get_holidays_for_employee
from possibleworks.utils.payroll_period import _get_period_boundaries

ALLOWED_ROLES = ("System Manager", "HR Manager", "HR User")
EXCEPTION_STATUSES = ("Absent", "Half Day")


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================


@frappe.whitelist()
def send_attendance_exception_report(payroll_period, from_date=None, to_date=None):
	"""Build and email the Absent/Half-Day exception report for one cycle.

	`from_date`/`to_date` default to the most recently completed cycle (as of
	today) for the Payroll Period's company; pass them explicitly to resend
	for any other range.
	"""
	_check_permission()

	period = frappe.get_doc("Payroll Period", payroll_period)

	if from_date and to_date:
		from_date, to_date = getdate(from_date), getdate(to_date)
	else:
		from_date, to_date = _get_period_boundaries(add_days(getdate(nowdate()), -1), period.company)

	recipients = _parse_emails(period.get("custom_to_emails"))
	cc = _parse_emails(period.get("custom_cc_emails"))

	if not recipients:
		frappe.throw(
			_(
				"Add at least one recipient in 'Send Report To' on this Payroll "
				"Period before sending the attendance report."
			)
		)

	try:
		rows = _build_report_rows(period.company, from_date, to_date)
		xlsx_bytes = build_attendance_exception_workbook(rows)

		frappe.sendmail(
			recipients=recipients,
			cc=cc or None,
			subject=_("Attendance Exception Report - {0} - {1} to {2}").format(
				period.company, frappe.utils.formatdate(from_date), frappe.utils.formatdate(to_date)
			),
			message=_build_email_body(period.company, from_date, to_date, rows),
			attachments=[
				{
					"fname": f"Attendance-Exceptions-{from_date}-to-{to_date}.xlsx",
					"fcontent": xlsx_bytes,
				}
			],
		)

		frappe.db.set_value("Payroll Period", period.name, "custom_last_auto_report_sent_till", to_date)

		return {"sent": True, "from_date": str(from_date), "to_date": str(to_date), "rows": len(rows)}

	except Exception:
		frappe.log_error(
			title=f"Attendance exception report failed for {payroll_period}",
			message=frappe.get_traceback(with_context=True),
		)
		frappe.throw(_("Could not build/send the attendance report. Check Error Log for details."))


def daily_attendance_report_dispatch():
	"""Scheduler entry point: fires the report for any Payroll Period whose
	cycle (Monthly or Custom) ended yesterday and hasn't been auto-sent yet."""
	today = getdate(nowdate())
	yesterday = add_days(today, -1)

	periods = frappe.get_all(
		"Payroll Period",
		filters={"start_date": ["<=", today], "end_date": [">=", today], "docstatus": ["!=", 2]},
		fields=["name", "company", "custom_last_auto_report_sent_till"],
		order_by="company",
	)

	for period in periods:
		try:
			start, end = _get_period_boundaries(yesterday, period.company)
		except Exception:
			frappe.log_error(
				title=f"Attendance report dispatch: could not resolve cycle for {period.name}",
				message=frappe.get_traceback(with_context=True),
			)
			continue

		if end != yesterday:
			continue

		last_sent = (
			getdate(period.custom_last_auto_report_sent_till)
			if period.custom_last_auto_report_sent_till
			else None
		)
		if last_sent and last_sent >= end:
			continue

		try:
			send_attendance_exception_report(period.name, start, end)
		except Exception:
			# One company's failure (e.g. no recipients configured yet, or a
			# build/send error) must not stop the rest of today's dispatch --
			# every other due Payroll Period still needs its own attempt.
			frappe.log_error(
				title=f"Attendance report dispatch: send failed for {period.name} ({period.company})",
				message=frappe.get_traceback(with_context=True),
			)


# =============================================================================
# PERMISSION / RECIPIENTS
# =============================================================================


def _check_permission():
	if frappe.session.user == "Administrator":
		return
	if not set(ALLOWED_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("You are not permitted to send the attendance report."), frappe.PermissionError)


def _parse_emails(value):
	if not value:
		return []
	seen = []
	for token in re.split(r"[,\n;]+", str(value)):
		address = token.strip()
		if address and "@" in address and address not in seen:
			seen.append(address)
	return seen


# =============================================================================
# DATA FETCH
# =============================================================================


def _build_report_rows(company, from_date, to_date):
	attendance_rows = frappe.get_all(
		"Attendance",
		filters={
			"company": company,
			"attendance_date": ["between", [from_date, to_date]],
			"status": ["in", EXCEPTION_STATUSES],
			"docstatus": 1,
		},
		fields=[
			"employee",
			"employee_name",
			"department",
			"attendance_date",
			"status",
			"shift",
			"working_hours",
		],
		order_by="employee, attendance_date",
	)

	if not attendance_rows:
		return []

	employee_ids = sorted({a.employee for a in attendance_rows})

	pending_by_employee = _fetch_pending_requests(employee_ids, from_date, to_date)
	holidays_by_employee = _fetch_holidays(employee_ids, from_date, to_date)
	checkins_by_key = _fetch_checkins(employee_ids, from_date, to_date)

	rows = []
	for a in attendance_rows:
		date = getdate(a.attendance_date)
		date_str = cstr(date)

		is_holiday = date in holidays_by_employee.get(a.employee, set())
		checkins = checkins_by_key.get((a.employee, date_str), {"normal": [], "offshift": []})

		pending_label = ""
		for start, end, label in pending_by_employee.get(a.employee, []):
			if start <= date <= end:
				pending_label = label
				break

		rows.append(
			{
				"employee": a.employee,
				"employee_name": a.employee_name,
				"department": a.department or "",
				"date": date_str,
				"day_name": date.strftime("%a"),
				"status": a.status,
				"working_hours": flt(a.working_hours, 2) if a.working_hours else "",
				"holiday": _("Holiday") if is_holiday else "",
				"is_holiday": is_holiday,
				"pending_request": pending_label,
				"shift": a.shift or "",
				"checkins": ", ".join(checkins["normal"]),
				"offshift_checkins": ", ".join(checkins["offshift"]),
			}
		)

	return rows


def _fetch_pending_requests(employee_ids, from_date, to_date):
	"""{employee: [(from_date, to_date, label), ...]} for still-open
	regularizations overlapping the range -- a submitted/approved one has
	already updated Attendance.status directly and is not "pending" anymore."""
	requests_by_employee = {}

	attendance_requests = frappe.get_all(
		"Attendance Request",
		filters={
			"employee": ["in", employee_ids],
			"docstatus": 0,
			"from_date": ["<=", to_date],
			"to_date": [">=", from_date],
		},
		fields=["employee", "from_date", "to_date", "reason"],
	)
	for r in attendance_requests:
		requests_by_employee.setdefault(r.employee, []).append(
			(getdate(r.from_date), getdate(r.to_date), _("Attendance Request (Draft) - {0}").format(r.reason))
		)

	leave_applications = frappe.get_all(
		"Leave Application",
		filters={
			"employee": ["in", employee_ids],
			"status": "Open",
			"docstatus": 0,
			"from_date": ["<=", to_date],
			"to_date": [">=", from_date],
		},
		fields=["employee", "from_date", "to_date", "leave_type"],
	)
	for r in leave_applications:
		requests_by_employee.setdefault(r.employee, []).append(
			(getdate(r.from_date), getdate(r.to_date), _("Leave Application (Open) - {0}").format(r.leave_type))
		)

	return requests_by_employee


def _fetch_holidays(employee_ids, from_date, to_date):
	"""{employee: {holiday dates}}, via possibleworks.utils.holiday_utils --
	resolves each employee's Holiday List Assignment (falling back to
	Employee.holiday_list) independently of Attendance.status, so a holiday
	shows up even on a day marked Absent/Half Day. `build_holiday_cache` is
	built once and shared across employees, per that module's own contract.

	get_holidays_for_employee throws when an employee has no Holiday List
	coverage for the range at all -- a real HR-setup gap, but one employee's
	missing setup should not block the whole payroll report, so it is caught
	and logged per employee rather than propagated."""
	holiday_cache = build_holiday_cache()
	holidays_by_employee = {}
	for employee in employee_ids:
		try:
			holidays_by_employee[employee] = get_holidays_for_employee(
				employee, from_date, to_date, holiday_cache=holiday_cache
			)
		except frappe.ValidationError:
			frappe.log_error(
				title=f"Attendance report: no Holiday List coverage for {employee}",
				message=frappe.get_traceback(with_context=True),
			)
			holidays_by_employee[employee] = set()
	return holidays_by_employee


def _fetch_checkins(employee_ids, from_date, to_date):
	"""{(employee, date_str): {"normal": [...], "offshift": [...]}} of
	"IN 09:12"-style labels. Off-shift checkins never link to an Attendance
	record, so they must be queried from Employee Checkin directly rather than
	read off Attendance's own in_time/out_time."""
	from_datetime = get_datetime(f"{from_date} 00:00:00")
	to_datetime = get_datetime(f"{to_date} 23:59:59")

	checkins = frappe.get_all(
		"Employee Checkin",
		filters={"employee": ["in", employee_ids], "time": ["between", [from_datetime, to_datetime]]},
		fields=["employee", "log_type", "time", "offshift"],
		order_by="employee, time",
	)

	grouped = {}
	for c in checkins:
		checkin_time = get_datetime(c.time)
		key = (c.employee, cstr(getdate(checkin_time)))
		bucket = grouped.setdefault(key, {"normal": [], "offshift": []})
		label = f"{c.log_type or '?'} {checkin_time.strftime('%H:%M')}"
		bucket["offshift" if c.offshift else "normal"].append(label)

	return grouped


# =============================================================================
# EMAIL BODY
# =============================================================================


def _build_email_body(company, from_date, to_date, rows):
	return render_branded_email(
		heading=_("Attendance Exception Report"),
		paragraphs=[
			_("Attached is the attendance exception report for {0}, {1} to {2}.").format(
				company, frappe.utils.formatdate(from_date), frappe.utils.formatdate(to_date)
			),
			_("{0} Absent/Half-Day record(s) in this cycle.").format(len(rows)),
		],
		notes=[
			_(
				"Dates highlighted in the attached sheet are holidays on that employee's "
				"own calendar -- please do not treat those as unapproved absences."
			),
		],
		signoff=[_("Regards,"), _("HR Team")],
		footer_note=_("This is an automated attendance report."),
	)
