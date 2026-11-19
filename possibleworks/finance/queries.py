# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.model import no_value_fields
from frappe.utils import cint, cstr, strip_html


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


# ── List view search ─────────────────────────────────────────────────────────────

_TEXT_FIELDTYPES = {
	"Data",
	"Link",
	"Select",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"Read Only",
	"Autocomplete",
	"Phone",
}
_STANDARD_COLUMNS = {"name", "owner", "modified_by", "creation", "modified", "docstatus"}
_MAX_SEARCH_WORDS = 6
_MAX_SEARCH_MATCHES = 5000
_MAX_TITLE_MATCHES = 200
_VOCABULARY_DOCS = 30
_VOCABULARY_SIZE = 150
_MAX_VOCABULARY_VALUE = 60


@frappe.whitelist()
def list_search(
	doctype,
	txt=None,
	fields=None,
	search_fields=None,
	filters=None,
	order_by=None,
	start=0,
	page_length=20,
	column_filters=None,
):
	"""One page of `doctype` for the list view search and column filters, plus words to suggest
	while typing. Column filters are described in `list_filters`.

	Every word typed must appear somewhere in the document, in any order: its own fields (and
	the list's columns), the display name of anything it links to (a user's full name, a
	department, a supplier, an item), any of its child tables (items, suppliers, ...), or the
	documents its items were raised from (an RFQ's Indent / Material Request, which give it its
	Purpose, Requested By and Department). Only documents the user can read are returned. Read-only.
	"""
	from possibleworks.finance.list_filters import apply_column_filters

	frappe.has_permission(doctype, "read", throw=True)
	meta = frappe.get_meta(doctype)
	words = search_words(txt)
	base_filters = _as_filter_list(frappe.parse_json(filters) if isinstance(filters, str) else filters)
	columns = [f for f in _parse_list(fields) if _is_column(meta, f)] or ["name"]
	search_columns = [f for f in _parse_list(search_fields) if _is_column(meta, f)]

	filters, allowed = apply_column_filters(doctype, base_filters, column_filters)
	matched = allowed
	if words:
		matched = search_document_names(doctype, words, filters, search_columns)
		if allowed is not None:
			matched &= allowed

	page = {
		"fields": columns,
		"order_by": _safe_order_by(meta, order_by),
		"limit_start": cint(start),
		"limit_page_length": cint(page_length) or 20,
	}
	if matched is None:
		# Only plain field filters: list them directly.
		rows = frappe.get_list(doctype, filters=filters, **page)
		total = frappe.get_list(doctype, filters=filters, fields=[{"COUNT": "*", "as": "total"}])[0].total
	else:
		rows = frappe.get_list(doctype, filters=[*filters, ["name", "in", list(matched)]], **page) if matched else []
		total = len(matched)

	shown_first = [row.get("name") for row in rows if row.get("name")]
	vocabulary_names = shown_first + sorted((matched or set()) - set(shown_first))
	return {"rows": rows, "total": total, "vocabulary": search_vocabulary(doctype, vocabulary_names, words)}


def search_words(txt):
	return [word for word in cstr(txt).lower().split() if word][:_MAX_SEARCH_WORDS]


def search_document_names(doctype, words, filters=None, extra_fields=()):
	"""Names of readable `doctype` documents (passing `filters`) that contain every word."""
	matched = None
	title_cache = {}
	for word in words:
		names = _names_matching_word(doctype, word, filters or [], extra_fields, title_cache)
		matched = names if matched is None else matched & names
		if not matched:
			return set()
	return matched or set()


def _names_matching_word(doctype, word, filters, extra_fields, title_cache):
	like = f"%{word}%"
	meta = frappe.get_meta(doctype)

	conditions = _like_conditions(meta, like, title_cache, ["name", *extra_fields])
	user_ids = _title_matches("User", like, title_cache)
	if user_ids:
		conditions.append(["owner", "in", user_ids])
	names = set(
		frappe.get_list(
			doctype, filters=filters, or_filters=conditions, pluck="name", limit_page_length=_MAX_SEARCH_MATCHES
		)
	)

	# Child tables and source documents are matched without the list's filters, so the
	# candidates go back through get_list below to apply filters and permissions.
	candidates = set()
	for table_df in meta.get_table_fields():
		child_conditions = _like_conditions(frappe.get_meta(table_df.options), like, title_cache)
		if child_conditions:
			candidates.update(
				frappe.get_all(
					table_df.options,
					filters={"parenttype": doctype, "parentfield": table_df.fieldname},
					or_filters=child_conditions,
					pluck="parent",
					distinct=True,
					limit=_MAX_SEARCH_MATCHES,
				)
			)

	if doctype in _PURPOSE_SOURCES:
		child, link_fields = _PURPOSE_SOURCES[doctype]
		child_meta = frappe.get_meta(child)
		for link_field in link_fields:
			if not child_meta.has_field(link_field):
				continue
			source_doctype = _PURPOSE_LINK_DOCTYPES[link_field]
			sources = frappe.get_all(
				source_doctype,
				or_filters=_like_conditions(frappe.get_meta(source_doctype), like, title_cache, ["name"]),
				pluck="name",
				limit=_MAX_SEARCH_MATCHES,
			)
			if sources:
				candidates.update(
					frappe.get_all(
						child,
						filters={"parenttype": doctype, link_field: ("in", sources)},
						pluck="parent",
						distinct=True,
						limit=_MAX_SEARCH_MATCHES,
					)
				)

	candidates -= names
	if candidates:
		names.update(
			frappe.get_list(
				doctype,
				filters=[*filters, ["name", "in", list(candidates)]],
				pluck="name",
				limit_page_length=_MAX_SEARCH_MATCHES,
			)
		)
	return names


def _like_conditions(meta, like, title_cache, extra_fields=()):
	conditions = []
	seen = set()
	for df in _text_fields(meta):
		conditions.append([df.fieldname, "like", like])
		seen.add(df.fieldname)
		if df.fieldtype == "Link" and df.options:
			ids = _title_matches(df.options, like, title_cache)
			if ids:
				conditions.append([df.fieldname, "in", ids])
	for fieldname in extra_fields:
		if fieldname not in seen:
			conditions.append([fieldname, "like", like])
	return conditions


def _text_fields(meta):
	return [df for df in meta.fields if df.fieldtype in _TEXT_FIELDTYPES and not df.get("is_virtual")]


def _title_field(doctype):
	if doctype == "User":
		return "full_name"
	meta = frappe.get_meta(doctype)
	title = meta.title_field
	return title if title and title != "name" and meta.has_field(title) else None


def _title_matches(doctype, like, title_cache):
	"""Ids of `doctype` records whose display name matches, e.g. users by full name."""
	key = (doctype, like)
	if key not in title_cache:
		title = _title_field(doctype)
		title_cache[key] = (
			frappe.get_all(doctype, filters={title: ("like", like)}, pluck="name", limit=_MAX_TITLE_MATCHES)
			if title
			else []
		)
	return title_cache[key]


def search_vocabulary(doctype, names, words):
	"""Short values from the first matched documents that contain the last word typed, for
	completing it: document numbers, parties, departments, people, item codes and names."""
	if not names or not words:
		return []
	last = words[-1]
	docs = names[:_VOCABULARY_DOCS]
	meta = frappe.get_meta(doctype)

	values = set(docs)
	values.update(_field_values(doctype, {"name": ("in", docs)}))
	values.update(_title_values(doctype, {"name": ("in", docs)}, ["owner"]))
	for table_df in meta.get_table_fields():
		child_filters = {"parent": ("in", docs), "parenttype": doctype, "parentfield": table_df.fieldname}
		values.update(_field_values(table_df.options, child_filters))
	if doctype in _PURPOSE_SOURCES:
		child, link_fields = _PURPOSE_SOURCES[doctype]
		child_meta = frappe.get_meta(child)
		for link_field in [f for f in link_fields if child_meta.has_field(f)]:
			sources = frappe.get_all(
				child, filters={"parent": ("in", docs), "parenttype": doctype}, pluck=link_field, distinct=True
			)
			sources = [s for s in sources if s]
			if sources:
				values.update(_field_values(_PURPOSE_LINK_DOCTYPES[link_field], {"name": ("in", sources)}))

	# Email ids (user links) are searchable but read badly as a completion.
	matching = [
		v for v in values if v and len(v) <= _MAX_VOCABULARY_VALUE and "@" not in v and last in v.lower()
	]
	return sorted(matching, key=lambda v: (len(v), v.lower()))[:_VOCABULARY_SIZE]


def _field_values(doctype, filters):
	meta = frappe.get_meta(doctype)
	text_fields = [df for df in _text_fields(meta) if df.fieldtype not in ("Text Editor", "Long Text", "Text")]
	if not text_fields:
		return set()
	rows = frappe.get_all(doctype, filters=filters, fields=[df.fieldname for df in text_fields], limit=500)
	values = {strip_html(cstr(row.get(df.fieldname))).strip() for row in rows for df in text_fields}
	link_fields = [df.fieldname for df in text_fields if df.fieldtype == "Link"]
	return values | _title_values(doctype, filters, link_fields, rows)


def _title_values(doctype, filters, link_fields, rows=None):
	"""Display names of what `link_fields` point to, e.g. a user's full name for `owner`."""
	if not link_fields:
		return set()
	meta = frappe.get_meta(doctype)
	if rows is None:
		rows = frappe.get_all(doctype, filters=filters, fields=link_fields, limit=500)
	titles = set()
	for fieldname in link_fields:
		target = "User" if fieldname in ("owner", "modified_by") else (meta.get_field(fieldname) or {}).get("options")
		title = _title_field(target) if target else None
		ids = list({row.get(fieldname) for row in rows if row.get(fieldname)})
		if title and ids:
			titles.update(frappe.get_all(target, filters={"name": ("in", ids)}, pluck=title))
	return {cstr(t).strip() for t in titles if t}


def _parse_list(value):
	value = frappe.parse_json(value) if isinstance(value, str) else value
	return [v for v in (value or []) if isinstance(v, str)]


def _is_column(meta, fieldname):
	if fieldname in _STANDARD_COLUMNS:
		return True
	df = meta.get_field(fieldname)
	return bool(df and not df.get("is_virtual") and df.fieldtype not in no_value_fields)


def _as_filter_list(filters):
	if not filters:
		return []
	if isinstance(filters, dict):
		return [
			[key, value[0], value[1]] if isinstance(value, (list, tuple)) else [key, "=", value]
			for key, value in filters.items()
		]
	return list(filters)


def _safe_order_by(meta, order_by):
	parts = cstr(order_by).split()
	if len(parts) == 2 and parts[1].lower() in ("asc", "desc") and _is_column(meta, parts[0]):
		return f"`tab{meta.name}`.`{parts[0]}` {parts[1].lower()}"
	return f"`tab{meta.name}`.`modified` desc"
