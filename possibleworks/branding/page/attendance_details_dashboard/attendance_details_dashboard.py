# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
import re

def _strip_html(text):
	return re.sub(r"<[^>]+>", "", text or "").strip() or "Holiday"


@frappe.whitelist()
def get_attendance_data(from_date, to_date):
	employees = frappe.get_list(
		"Employee",
		fields=["name", "employee_name", "department", "designation", "branch"],
		filters={"status": "Active"},
		limit_page_length=0,
	)

	employee_ids = [e.name for e in employees]

	attendance = frappe.get_list(
		"Attendance",
		fields=[
			"employee", "employee_name", "department", "attendance_date",
			"status", "shift", "in_time", "out_time", "late_entry", "early_exit",
		],
		filters=[
			["attendance_date", "between", [from_date, to_date]],
			["employee", "in", employee_ids] if employee_ids else ["employee", "=", ""],
		],
		limit_page_length=0,
	)

	# Fetch holiday assignments using raw SQL for reliability
	# Get most recent assignment per employee (Employee-level)
	emp_holiday_list_map = {}
	if employee_ids:
		placeholders = ", ".join(["%s"] * len(employee_ids))
		rows = frappe.db.sql(
			f"""
			SELECT assigned_to, holiday_list, from_date
			FROM `tabHoliday List Assignment`
			WHERE applicable_for = 'Employee'
			  AND docstatus != 2
			  AND assigned_to IN ({placeholders})
			ORDER BY from_date DESC
			""",
			tuple(employee_ids),
			as_dict=True,
		)
		for row in rows:
			emp_id = row.assigned_to
			# Skip future assignments (from_date after our period)
			if row.from_date and str(row.from_date) > str(to_date):
				continue
			# First match wins (most recent assignment due to ORDER BY from_date DESC)
			if emp_id not in emp_holiday_list_map:
				emp_holiday_list_map[emp_id] = row.holiday_list

	# Fallback: Company-level assignment for employees without individual assignment
	missing_ids = [e.name for e in employees if e.name not in emp_holiday_list_map]
	if missing_ids:
		co_rows = frappe.db.sql(
			"""
			SELECT e.name as emp_id, h.holiday_list
			FROM `tabEmployee` e
			JOIN `tabHoliday List Assignment` h
			  ON h.applicable_for = 'Company'
			  AND h.assigned_to = e.company
			  AND h.docstatus != 2
			WHERE e.name IN ({})
			ORDER BY h.from_date DESC
			""".format(", ".join(["%s"] * len(missing_ids))),
			tuple(missing_ids),
			as_dict=True,
		)
		for row in co_rows:
			if row.emp_id not in emp_holiday_list_map:
				emp_holiday_list_map[row.emp_id] = row.holiday_list

	# Collect unique holiday lists needed
	unique_holiday_lists = list(set(emp_holiday_list_map.values()))

	holiday_map = {}  # holiday_list_name -> [{"date": ..., "description": ..., "weekly_off": ...}]
	if unique_holiday_lists:
		placeholders = ", ".join(["%s"] * len(unique_holiday_lists))
		hol_rows = frappe.db.sql(
			f"""
			SELECT parent, DATE_FORMAT(holiday_date, '%%Y-%%m-%%d') as holiday_date,
			       description, weekly_off
			FROM `tabHoliday`
			WHERE parent IN ({placeholders})
			  AND holiday_date BETWEEN %s AND %s
			""",
			tuple(unique_holiday_lists) + (from_date, to_date),
			as_dict=True,
		)
		for h in hol_rows:
			holiday_map.setdefault(h.parent, []).append({
				"date": h.holiday_date,
				"description": _strip_html(h.description),
				"weekly_off": bool(h.weekly_off),
			})

	# Build per-employee holiday list: employee_id -> [holiday entries]
	employee_holidays = {}
	for emp in employees:
		hl = emp_holiday_list_map.get(emp.name)
		employee_holidays[emp.name] = holiday_map.get(hl, []) if hl else []

	# Fetch all active Leave Applications with their status
	leave_requests = frappe.db.sql(
		"""
		SELECT employee,
		       DATE_FORMAT(from_date, '%%Y-%%m-%%d') as from_date,
		       DATE_FORMAT(to_date,   '%%Y-%%m-%%d') as to_date,
		       status,
		       leave_type,
		       docstatus
		FROM `tabLeave Application`
		WHERE docstatus != 2
		  AND status != 'Rejected'
		  AND from_date <= %s AND to_date >= %s
		""",
		(to_date, from_date),
		as_dict=True,
	)

	# Fetch Attendance Requests with their docstatus
	att_requests = frappe.db.sql(
		"""
		SELECT employee,
		       DATE_FORMAT(from_date, '%%Y-%%m-%%d') as from_date,
		       DATE_FORMAT(to_date,   '%%Y-%%m-%%d') as to_date,
		       docstatus,
		       reason
		FROM `tabAttendance Request`
		WHERE docstatus != 2
		  AND from_date <= %s AND to_date >= %s
		""",
		(to_date, from_date),
		as_dict=True,
	)

	return {
		"employees": employees,
		"attendance": attendance,
		"employee_holidays": employee_holidays,
		"leave_requests": leave_requests,
		"att_requests": att_requests,
	}


@frappe.whitelist()
def get_leave_balances(employee, date=None):
	"""Return leave balances for an employee for the allocation period covering `date`.

	Uses the HRMS get_leave_details API which correctly handles carry-forward
	leaves, allocation periods, and leave year boundaries (e.g. April new year).
	Pass date as the first day of the selected month so April selections pick up
	the new leave year allocation instead of the previous year's.
	"""
	from frappe.utils import getdate, nowdate
	from hrms.hr.doctype.leave_application.leave_application import get_leave_details

	date = getdate(date) if date else getdate(nowdate())
	leave_details = get_leave_details(employee, date)
	allocation = leave_details.get("leave_allocation", {})

	result = []
	for leave_type, details in allocation.items():
		result.append({
			"leave_type": leave_type,
			"allocated": details.get("total_leaves", 0),
			"taken": details.get("leaves_taken", 0),
			"balance": details.get("remaining_leaves", 0),
		})
	result.sort(key=lambda x: x["leave_type"])
	return result


@frappe.whitelist()
def get_leave_periods_for_dates(employee, dates):
	"""Group a list of dates by their leave allocation period and return per-period balances.

	Returns a list of period objects:
	  { from_date, to_date, has_allocation, dates: [...], balances: {leave_type: remaining} }
	"""
	import json
	from frappe.utils import getdate
	from hrms.hr.doctype.leave_application.leave_application import (
		get_leave_allocation_records,
		get_leave_details,
	)

	if isinstance(dates, str):
		dates = json.loads(dates)

	periods = {}  # key -> period dict

	for d in sorted(set(dates)):
		alloc = get_leave_allocation_records(employee, getdate(d))
		if not alloc:
			key = "__no_alloc__"
			if key not in periods:
				periods[key] = {
					"from_date": None,
					"to_date": None,
					"has_allocation": False,
					"dates": [],
					"balances": {},
				}
			periods[key]["dates"].append(d)
			continue

		first = next(iter(alloc.values()))
		key = f"{first.from_date}|{first.to_date}"
		if key not in periods:
			leave_details = get_leave_details(employee, getdate(d))
			periods[key] = {
				"from_date": str(first.from_date),
				"to_date": str(first.to_date),
				"has_allocation": True,
				"dates": [],
				"balances": {
					lt: v.get("remaining_leaves", 0)
					for lt, v in leave_details.get("leave_allocation", {}).items()
				},
			}
		periods[key]["dates"].append(d)

	return list(periods.values())


@frappe.whitelist()
def submit_leave_application(employee, employee_name, from_date, to_date, leave_type, description="", half_day=0, half_day_date=None):
	"""Create and submit a Leave Application, respecting workflow if enabled."""
	from frappe.model.workflow import apply_workflow
	from frappe.utils import cint
	from possibleworks.observer.workflow_service import WorkflowService

	# Resolve a valid leave approver for this employee
	leave_approver = frappe.db.get_value("Employee", employee, "leave_approver")
	if not leave_approver:
		department = frappe.db.get_value("Employee", employee, "department")
		if department:
			leave_approver = frappe.db.get_value(
				"Department Approver",
				{"parent": department, "parentfield": "leave_approvers"},
				"approver",
			)
	leave_approver = leave_approver or frappe.session.user

	workflow_name = WorkflowService.get_workflow_name("Leave Application")

	if workflow_name:
		# Workflow is enabled: insert without forcing status, then apply first transition
		doc = frappe.get_doc({
			"doctype": "Leave Application",
			"employee": employee,
			"employee_name": employee_name,
			"from_date": from_date,
			"to_date": to_date,
			"leave_type": leave_type,
			"description": description,
			"leave_approver": leave_approver,
			"half_day": cint(half_day),
			"half_day_date": half_day_date or (from_date if cint(half_day) else None),
		})
		doc.insert(ignore_permissions=True)

		state_field = WorkflowService.get_state_field(workflow_name)
		current_state = getattr(doc, state_field, None)
		transitions = WorkflowService.get_transitions(
			workflow_name=workflow_name,
			current_state=current_state,
			doctype="Leave Application",
			doc_name=doc.name,
		)
		if transitions:
			apply_workflow(doc, transitions[0]["action"])
	else:
		# No workflow: default behaviour
		doc = frappe.get_doc({
			"doctype": "Leave Application",
			"employee": employee,
			"employee_name": employee_name,
			"from_date": from_date,
			"to_date": to_date,
			"leave_type": leave_type,
			"description": description,
			"status": "Approved",
			"leave_approver": leave_approver,
			"half_day": cint(half_day),
			"half_day_date": half_day_date or (from_date if cint(half_day) else None),
		})
		doc.insert(ignore_permissions=True)
		doc.submit()

	return doc.name


@frappe.whitelist()
def approve_pending_leaves(employee, dates):
	"""Approve Open Leave Applications whose date range covers the given dates."""
	import json
	from frappe.model.workflow import apply_workflow
	from possibleworks.observer.workflow_service import WorkflowService

	if isinstance(dates, str):
		dates = json.loads(dates)

	workflow_name = WorkflowService.get_workflow_name("Leave Application")
	approved = 0
	for dt in dates:
		apps = frappe.get_list(
			"Leave Application",
			filters={
				"employee": employee,
				"status": "Open",
				"docstatus": 0,
				"from_date": ["<=", dt],
				"to_date": [">=", dt],
			},
			fields=["name"],
		)
		for app in apps:
			doc = frappe.get_doc("Leave Application", app.name)
			if workflow_name:
				state_field = WorkflowService.get_state_field(workflow_name)
				current_state = getattr(doc, state_field, None)
				transitions = WorkflowService.get_transitions(
					workflow_name=workflow_name,
					current_state=current_state,
					doctype="Leave Application",
					doc_name=doc.name,
				)
				if transitions:
					apply_workflow(doc, transitions[0]["action"])
			else:
				doc.status = "Approved"
				doc.leave_approver = frappe.session.user
				doc.save(ignore_permissions=True)
				doc.submit()
			approved += 1
	return {"approved": approved}


@frappe.whitelist()
def approve_pending_att_requests(employee, dates):
	"""Submit (approve) pending Attendance Requests covering the given dates."""
	import json
	if isinstance(dates, str):
		dates = json.loads(dates)

	approved = 0
	for dt in dates:
		reqs = frappe.get_list(
			"Attendance Request",
			filters={
				"employee": employee,
				"docstatus": 0,
				"from_date": ["<=", dt],
				"to_date": [">=", dt],
			},
			fields=["name"],
		)
		for req in reqs:
			doc = frappe.get_doc("Attendance Request", req.name)
			doc.submit()
			approved += 1
	return {"approved": approved}
