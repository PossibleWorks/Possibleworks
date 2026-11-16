# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import cint


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def users_with_role(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""Link-query for User, scoped to users holding a given Role.

	Wired up as a Link field's `linkSearch.query` with filters={"role": "<Role Name>"}
	(e.g. Stock Entry's custom_assigned_to -> role "Material Request").
	"""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	role = (filters or {}).get("role")
	if not role:
		return []

	user = frappe.qb.DocType("User")
	has_role = frappe.qb.DocType("Has Role")

	query = (
		frappe.qb.from_(user)
		.inner_join(has_role)
		.on(has_role.parent == user.name)
		.select(user.name, user.full_name)
		.where(has_role.role == role)
		.where(user.enabled == 1)
		.where(user.name.notin(["Administrator", "Guest"]))
		.orderby(user.full_name)
		.limit(cint(page_len))
		.offset(cint(start))
	)

	if txt:
		txt = f"%{txt}%"
		query = query.where((user.name.like(txt)) | (user.full_name.like(txt)))

	return query.run(as_dict=as_dict)


def _department_user_ids(department):
	"""User ids of active Employees in `department` or any of its sub-departments."""
	bounds = frappe.db.get_value("Department", department, ["lft", "rgt"], as_dict=True)
	if not bounds:
		return []

	employee = frappe.qb.DocType("Employee")
	dept = frappe.qb.DocType("Department")
	rows = (
		frappe.qb.from_(employee)
		.inner_join(dept)
		.on(dept.name == employee.department)
		.select(employee.user_id)
		.distinct()
		.where(dept.lft >= bounds.lft)
		.where(dept.rgt <= bounds.rgt)
		.where(employee.status == "Active")
		.where(employee.user_id.isnotnull())
		.where(employee.user_id != "")
		.run(pluck=True)
	)
	return rows


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def users_in_department(doctype, txt, searchfield, start, page_len, filters, as_dict=False):
	"""Link-query for User behind Material Request's "Requested By".

	With filters={"department": "<Department>"} it returns only users linked to an active
	Employee in that department (sub-departments included); with no department it returns
	every enabled system user.
	"""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)

	user = frappe.qb.DocType("User")
	query = (
		frappe.qb.from_(user)
		.select(user.name, user.full_name)
		.where(user.enabled == 1)
		.where(user.user_type == "System User")
		.where(user.name.notin(["Administrator", "Guest"]))
		.orderby(user.full_name)
		.limit(cint(page_len))
		.offset(cint(start))
	)

	department = (filters or {}).get("department")
	if department:
		user_ids = _department_user_ids(department)
		if not user_ids:
			return []
		query = query.where(user.name.isin(user_ids))

	if txt:
		txt = f"%{txt}%"
		query = query.where((user.name.like(txt)) | (user.full_name.like(txt)))

	return query.run(as_dict=as_dict)


@frappe.whitelist()
def requested_by_in_department(department=None, user=None):
	"""Keeps "Requested By" when its user belongs to `department`, else clears it.

	Called when the department changes so a previously tagged user from another
	department is not silently carried over.
	"""
	if not user or not department:
		return {"user": user or ""}
	return {"user": user if user in _department_user_ids(department) else ""}
