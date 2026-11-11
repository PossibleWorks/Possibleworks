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
