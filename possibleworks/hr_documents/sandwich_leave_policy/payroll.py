# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Sandwich Leave Policy -- Salary Slip payment_days adjustment.

Wired as a `doc_events["Salary Slip"]["validate"]` hook, which Frappe always
runs *after* SalarySlip.validate() has already computed payment_days
(frappe/model/document.py: the class's own validate() runs before any
doc_events hook, never interleaved) -- so doc.payment_days here already
reflects native attendance/leave for the period, and our adjustment is the
final word, surviving every save/submit/Payroll Entry regeneration.

Only the *holiday* Sandwich Leave Log rows are summed here. The bridging
leave dates (e.g. Fri/Mon) are not: once the sandwich script marks them
Attendance = Absent, native payroll already reduces payment_days for those on
its own, because they're real working days the day-scan always looks at. The
holiday dates (Sat/Sun) are the only ones native payroll can never see, since
Payroll Settings.include_holidays_in_total_working_days stays off -- that's
the whole reason this hook exists.
"""

import frappe


def apply_sandwich_payment_days_adjustment(doc, method=None):
	if not frappe.db.get_single_value("Policy Configuration", "enable_sandwich_leave_policy"):
		return

	if not doc.employee or not doc.start_date or not doc.end_date:
		return

	holiday_days = frappe.db.count(
		"Sandwich Leave Log",
		filters={
			"employee": doc.employee,
			"is_holiday_date": 1,
			"date": ["between", [doc.start_date, doc.end_date]],
		},
	)
	if not holiday_days:
		return

	doc.payment_days = max(0, (doc.payment_days or 0) - holiday_days)
	# No-op if this site hasn't run setup.create_custom_fields() yet -- the
	# attribute is simply not persisted until the Custom Field exists.
	doc.custom_sandwich_leave_days = holiday_days
	doc.calculate_net_pay()
