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

	attendance = frappe.get_list(
		"Attendance",
		fields=[
			"employee", "employee_name", "department", "attendance_date",
			"status", "shift", "in_time", "out_time", "late_entry", "early_exit",
		],
		filters=[["attendance_date", "between", [from_date, to_date]]],
		limit_page_length=0,
	)

	employee_ids = [e.name for e in employees]

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
def get_leave_balances(employee):
	"""Return leave balances for an employee.

	Uses Leave Allocation for allocated/carry-forward counts and
	Leave Application (submitted) for taken days.
	"""
	alloc_rows = frappe.db.sql(
		"""
		SELECT leave_type,
		       SUM(total_leaves_allocated) as allocated
		FROM `tabLeave Allocation`
		WHERE employee = %s
		  AND docstatus = 1
		  AND from_date <= CURDATE()
		  AND to_date >= CURDATE()
		GROUP BY leave_type
		""",
		(employee,),
		as_dict=True,
	)

	if not alloc_rows:
		return []

	leave_types = [r.leave_type for r in alloc_rows]
	placeholders = ", ".join(["%s"] * len(leave_types))

	taken_rows = frappe.db.sql(
		f"""
		SELECT leave_type,
		       SUM(total_leave_days) as taken
		FROM `tabLeave Application`
		WHERE employee = %s
		  AND leave_type IN ({placeholders})
		  AND docstatus = 1
		GROUP BY leave_type
		""",
		tuple([employee] + leave_types),
		as_dict=True,
	)
	taken_map = {r.leave_type: float(r.taken or 0) for r in taken_rows}

	result = []
	for r in alloc_rows:
		allocated = float(r.allocated or 0)
		taken = taken_map.get(r.leave_type, 0)
		result.append({
			"leave_type": r.leave_type,
			"allocated": allocated,
			"taken": taken,
			"balance": allocated - taken,
		})
	result.sort(key=lambda x: x["leave_type"])
	return result


@frappe.whitelist()
def submit_leave_application(employee, employee_name, from_date, to_date, leave_type, description=""):
	"""Create and auto-approve a Leave Application."""
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
	})
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name


@frappe.whitelist()
def approve_pending_leaves(employee, dates):
	"""Approve Open Leave Applications whose date range covers the given dates."""
	import json
	if isinstance(dates, str):
		dates = json.loads(dates)

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
