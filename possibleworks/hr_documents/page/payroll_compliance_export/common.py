# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Shared query/period logic used by both the PF and ESCI exports."""

import calendar

import frappe
from frappe.utils import add_days, formatdate, getdate

from possibleworks.hr_documents.page.payroll_compliance_export.constants import MAX_PERIOD_MONTHS
from possibleworks.utils.payroll_period import _get_period_boundaries


def get_period_months(payroll_period):
	"""Break a Payroll Period's [start_date, end_date] span into one entry per
	payroll cycle. The actual cycle boundaries (Monthly vs Custom, same-month
	vs day-wrap) are resolved by possibleworks.utils.payroll_period's own
	_get_period_boundaries() — the canonical implementation of that logic
	already used elsewhere in this app — not recomputed here.

	Each cycle is clipped to the Payroll Period's own start_date/end_date, so
	a partial first/last cycle shows only the days actually within the period.
	Returns a list of dicts (earliest first): {label, start_date, end_date}
	(dates as ISO strings, ready to hand back to the server unchanged).
	"""
	doc = frappe.get_doc("Payroll Period", payroll_period)
	period_start, period_end = getdate(doc.start_date), getdate(doc.end_date)

	months = []
	seen = set()
	anchor = period_start
	iterations = 0
	# Normal progress jumps straight from one cycle to the next, but a date
	# that falls before this month's own window can resolve to the *previous*
	# month's cycle (see _get_period_boundaries) — in that case we step
	# forward a day at a time until we cross into real territory. Bound total
	# iterations generously (not just result count) so that fallback can never
	# spin forever even on a misconfigured Payroll Period.
	max_iterations = MAX_PERIOD_MONTHS * 40

	while anchor <= period_end and len(months) < MAX_PERIOD_MONTHS and iterations < max_iterations:
		iterations += 1
		cycle_start, cycle_end = _get_period_boundaries(anchor, company=doc.company)

		if (cycle_start, cycle_end) not in seen:
			seen.add((cycle_start, cycle_end))
			visible_start = max(cycle_start, period_start)
			visible_end = min(cycle_end, period_end)
			if visible_start <= visible_end:
				months.append({
					"label": (
						f"{calendar.month_name[cycle_end.month]} {cycle_end.year} "
						f"({formatdate(cycle_start)} - {formatdate(cycle_end)})"
					),
					"start_date": str(visible_start),
					"end_date": str(visible_end),
				})

		anchor = add_days(cycle_end, 1) if cycle_end >= anchor else add_days(anchor, 1)

	return months


def get_salary_slips_with_components(company, start_date, end_date):
	"""Fetch submitted Salary Slips for `company` within [start_date, end_date],
	plus their earnings/deductions and each employee's UAN/ESIC numbers —
	exactly three bulk queries total, regardless of employee count (no N+1).
	Shared by both the PF and ESCI exports, since both need the same
	per-employee payslip data for the same period."""
	slips = frappe.get_all(
		"Salary Slip",
		filters={
			"docstatus": 1,
			"company": company,
			"start_date": [">=", start_date],
			"end_date": ["<=", end_date],
		},
		fields=["name", "employee", "employee_name", "gross_pay", "total_working_days", "payment_days"],
	)
	if not slips:
		return []

	slip_names = [s.name for s in slips]
	detail_rows = frappe.get_all(
		"Salary Detail",
		filters={"parent": ["in", slip_names], "parenttype": "Salary Slip"},
		fields=["parent", "salary_component", "amount"],
	)
	components_by_slip = {}
	for d in detail_rows:
		components_by_slip.setdefault(d.parent, {})[d.salary_component] = d.amount

	employee_ids = list({s.employee for s in slips})
	employee_by_id = {
		e.name: e
		for e in frappe.get_all(
			"Employee",
			filters={"name": ["in", employee_ids]},
			fields=["name", "custom_uan_number", "custom_esic_number"],
		)
	}

	for s in slips:
		s["components"] = components_by_slip.get(s.name, {})
		employee = employee_by_id.get(s.employee)
		s["uan_number"] = employee.custom_uan_number if employee else None
		s["esic_number"] = employee.custom_esic_number if employee else None

	return slips
