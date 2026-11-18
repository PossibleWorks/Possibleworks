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


@frappe.whitelist()
def supplier_contact_details(supplier=None, contact=None):
	"""Contact, email and number to pre-fill on a supplier row (RFQ Suppliers table).

	erpnext's get_party_details only looks at the contact ticked "Is Primary Contact"
	(or "Is Billing Contact") and only at the number ticked "Is Primary Mobile", so a
	number added on any other contact, or left unticked, never filled in.

	Here the supplier's contacts are taken in order -- `contact` if given, then primary,
	then billing, then oldest first -- and the email and the number are each the first
	one found on them. The row's `contact` link is the first contact in that order.
	"""
	if supplier:
		frappe.has_permission("Supplier", "read", supplier, throw=True)
	docs = [doc for doc in _ordered_contacts(supplier, contact) if doc.has_permission("read")]
	return {
		"contact": docs[0].name if docs else "",
		"email_id": next((email for email in map(_contact_email, docs) if email), ""),
		"contact_number": next((number for number in map(_contact_number, docs) if number), ""),
	}


def supplier_contact_number(supplier=None, contact=None):
	"""The number supplier_contact_details gives for this row, "" when none of its contacts has one."""
	return next((number for number in map(_contact_number, _ordered_contacts(supplier, contact)) if number), "")


def _ordered_contacts(supplier=None, contact=None):
	names = []
	if contact and frappe.db.exists("Contact", contact):
		names.append(contact)
	if supplier:
		for name in frappe.get_all(
			"Contact",
			filters=[
				["Dynamic Link", "link_doctype", "=", "Supplier"],
				["Dynamic Link", "link_name", "=", supplier],
			],
			pluck="name",
			order_by="is_primary_contact desc, is_billing_contact desc, creation asc",
		):
			if name not in names:
				names.append(name)
	return [frappe.get_doc("Contact", name) for name in names]


def _contact_email(doc):
	email = doc.email_id or next((row.email_id for row in doc.email_ids if row.email_id), "")
	return (email or "").strip()


def _contact_number(doc):
	# primary mobile, then primary phone, then the first number on the contact
	number = doc.mobile_no or doc.phone or next((row.phone for row in doc.phone_nos if row.phone), "")
	return (number or "").strip()


# Documents with no Purpose field of their own, and the item links that lead back to the
# documents they were raised from. Only links the child doctype actually has are used.
_PURPOSE_SOURCES = {
	"Purchase Indent": ("Purchase Indent Item", ("material_request",)),
	"Request for Quotation": ("Request for Quotation Item", ("purchase_indent", "material_request")),
	"Supplier Quotation": (
		"Supplier Quotation Item",
		("purchase_indent", "material_request", "request_for_quotation"),
	),
	"Purchase Order": ("Purchase Order Item", ("purchase_indent", "material_request", "supplier_quotation")),
	"Purchase Receipt": ("Purchase Receipt Item", ("purchase_order", "purchase_indent", "material_request")),
	"Purchase Invoice": (
		"Purchase Invoice Item",
		("purchase_order", "purchase_receipt", "purchase_indent", "material_request"),
	),
}
_PURPOSE_LINK_DOCTYPES = {
	"purchase_indent": "Purchase Indent",
	"material_request": "Material Request",
	"request_for_quotation": "Request for Quotation",
	"supplier_quotation": "Supplier Quotation",
	"purchase_order": "Purchase Order",
	"purchase_receipt": "Purchase Receipt",
}
_MAX_PURPOSE_NAMES = 200


@frappe.whitelist()
def document_purposes(doctype, names):
	"""Purpose (Purchase / Service) of each document in `names`, for the list views.

	A document's own `material_request_type` wins. Without one (an RFQ, a Supplier
	Quotation, a Purchase Invoice, or an older Purchase Order / Indent made before the
	field was added) it is read from the documents its items were raised from, following the chain
	back -- e.g. Purchase Invoice -> Purchase Order -> Purchase Indent. Several different
	sources come back joined, e.g. "Purchase, Service". Read-only.
	"""
	names = frappe.parse_json(names) if isinstance(names, str) else names
	names = [n for n in (names or []) if isinstance(n, str) and n][:_MAX_PURPOSE_NAMES]
	if not names:
		return {}

	frappe.has_permission(doctype, "read", throw=True)
	# get_list applies the user's permissions, so only documents they can open are answered.
	readable = frappe.get_list(doctype, filters={"name": ("in", names)}, pluck="name", limit_page_length=0)

	cache = {}
	return {name: ", ".join(_document_purposes(doctype, name, cache)) for name in readable}


def _document_purposes(doctype, name, cache, depth=0):
	key = (doctype, name)
	if key in cache:
		return cache[key]
	cache[key] = []  # a link cycle ends here instead of recursing forever

	own = None
	if frappe.get_meta(doctype).has_field("material_request_type"):
		own = frappe.db.get_value(doctype, name, "material_request_type")

	purposes = [own] if own else []
	if not own and doctype in _PURPOSE_SOURCES and depth < 4:
		child, link_fields = _PURPOSE_SOURCES[doctype]
		child_meta = frappe.get_meta(child)
		link_fields = [field for field in link_fields if child_meta.has_field(field)]
		if link_fields:
			rows = frappe.get_all(
				child, filters={"parent": name, "parenttype": doctype}, fields=link_fields
			)
			for row in rows:
				for field in link_fields:
					if not row.get(field):
						continue
					for purpose in _document_purposes(_PURPOSE_LINK_DOCTYPES[field], row[field], cache, depth + 1):
						if purpose not in purposes:
							purposes.append(purpose)
		purposes.sort()

	cache[key] = purposes
	return purposes
