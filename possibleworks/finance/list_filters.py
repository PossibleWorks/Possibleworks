# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Column filters for the finance list views.

A filter is keyed by a list column's field and is one of:
	{"in": [values]}             the column shows one of these values
	{"between": [from, to]}      a date column within the range (either end may be blank)
	{"min": number, "max": number}  a number column within the range (either may be blank)

Most columns are fields on the doctype and become ordinary Frappe filters. A few show a value
worked out from elsewhere -- a status badge that shows the workflow state, an RFQ's Purpose /
Requested By / Department taken from its Indent or Material Request, its suppliers -- and those
are matched against what the list actually shows, so a filter keeps exactly the rows that read
that way.
"""

import frappe
from frappe.utils import cint, cstr, flt

from possibleworks.finance.queries import _document_purposes, _is_column, _parse_list

DOCSTATUS_LABELS = {"0": "Draft", "1": "Submitted", "2": "Cancelled"}
_MAX_CANDIDATES = 5000
_MAX_OPTIONS = 200


# ── Shown values of worked-out columns ───────────────────────────────────────────


def _shown_status(doctype, names):
	"""The status badge: the workflow state once past Draft, else the status."""
	rows = frappe.get_all(doctype, filters={"name": ("in", names)}, fields=["name", "status", "workflow_state"])
	return {
		row.name: {
			row.workflow_state
			if row.workflow_state and row.workflow_state.lower() != "draft"
			else cstr(row.status)
		}
		for row in rows
	}


def _shown_purpose(doctype, names):
	cache = {}
	return {name: set(_document_purposes(doctype, name, cache)) for name in names}


def _rfq_source_values(names, indent_field, request_field):
	"""An RFQ's Requested By / Department: from its Purchase Indents, else its Material Requests
	(the same rule the list view uses to show them)."""
	links = frappe.get_all(
		"Request for Quotation Item",
		filters={"parent": ("in", names), "parenttype": "Request for Quotation"},
		fields=["parent", "purchase_indent", "material_request"],
	)
	indents = _values_by_name("Purchase Indent", {link.purchase_indent for link in links}, indent_field)
	requests = _values_by_name("Material Request", {link.material_request for link in links}, request_field)

	from_indents, from_requests = {}, {}
	for link in links:
		if link.purchase_indent in indents:
			from_indents.setdefault(link.parent, set()).add(indents[link.purchase_indent])
		if link.material_request in requests:
			from_requests.setdefault(link.parent, set()).add(requests[link.material_request])
	return {name: from_indents.get(name) or from_requests.get(name) or set() for name in names}


def _values_by_name(doctype, names, field):
	names = [name for name in names if name]
	if not names:
		return {}
	rows = frappe.get_all(doctype, filters={"name": ("in", names)}, fields=["name", field])
	return {row.name: row.get(field) for row in rows if row.get(field)}


def _rfq_suppliers(names):
	rows = frappe.get_all(
		"Request for Quotation Supplier",
		filters={"parent": ("in", names), "parenttype": "Request for Quotation"},
		fields=["parent", "supplier", "supplier_name"],
	)
	shown = {}
	for row in rows:
		shown.setdefault(row.parent, set()).add(row.supplier_name or row.supplier)
	return {name: shown.get(name, set()) for name in names}


# field -> (applies to doctype?, shown values {name: set(values)})
_SHOWN_VALUE_COLUMNS = {
	"status": (
		lambda doctype: frappe.get_meta(doctype).has_field("workflow_state"),
		_shown_status,
	),
	"purpose": (lambda doctype: True, _shown_purpose),
	"requested_by": (
		lambda doctype: doctype == "Request for Quotation",
		lambda doctype, names: _rfq_source_values(names, "requested_by", "custom_requested_by"),
	),
	"department": (
		lambda doctype: doctype == "Request for Quotation",
		lambda doctype, names: _rfq_source_values(names, "department", "custom_department"),
	),
	"suppliers": (
		lambda doctype: doctype == "Request for Quotation",
		lambda doctype, names: _rfq_suppliers(names),
	),
}


def _shown_values_reader(doctype, field):
	entry = _SHOWN_VALUE_COLUMNS.get(field)
	if entry and entry[0](doctype):
		return entry[1]
	return None


# ── Applying filters ──────────────────────────────────────────────────────────────


def parse_column_filters(column_filters):
	parsed = frappe.parse_json(column_filters) if isinstance(column_filters, str) else column_filters
	return {field: cond for field, cond in (parsed or {}).items() if isinstance(cond, dict) and _is_set(cond)}


def _is_set(cond):
	if "in" in cond:
		return bool(cond["in"])
	if "between" in cond:
		return any(cond["between"] or [])
	return cond.get("min") not in (None, "") or cond.get("max") not in (None, "")


def apply_column_filters(doctype, base_filters, column_filters):
	"""(Frappe filters, names or None): the filters to list with, plus -- when a worked-out
	column is filtered -- the only names that may be shown."""
	meta = frappe.get_meta(doctype)
	filters = list(base_filters or [])
	shown_value_filters = {}

	for field, cond in parse_column_filters(column_filters).items():
		reader = _shown_values_reader(doctype, field)
		if reader:
			shown_value_filters[field] = (reader, {cstr(v) for v in cond.get("in") or []})
		elif field == "docstatus" and "in" in cond:
			filters.append(["docstatus", "in", [cint(v) for v in cond["in"]]])
		elif _is_column(meta, field):
			filters.extend(_field_conditions(field, cond))

	if not shown_value_filters:
		return filters, None

	names = frappe.get_list(doctype, filters=filters, pluck="name", limit_page_length=_MAX_CANDIDATES)
	for reader, wanted in shown_value_filters.values():
		if not names:
			break
		shown = reader(doctype, names)
		names = [name for name in names if shown.get(name, set()) & wanted]
	return filters, set(names)


def _field_conditions(field, cond):
	if "in" in cond:
		return [[field, "in", [cstr(v) for v in cond["in"]]]]
	if "between" in cond:
		start, end = (list(cond["between"] or []) + [None, None])[:2]
		if start and end:
			return [[field, "between", [start, end]]]
		return [[field, ">=", start]] if start else [[field, "<=", end]]
	conditions = []
	if cond.get("min") not in (None, ""):
		conditions.append([field, ">=", flt(cond["min"])])
	if cond.get("max") not in (None, ""):
		conditions.append([field, "<=", flt(cond["max"])])
	return conditions


def row_matches(row, column_filters, shown=None):
	"""For lists built in Python (the grouped Supplier Quotation list): does the row pass every
	filter? `shown` maps a field to a function giving the row's shown values for it."""
	for field, cond in parse_column_filters(column_filters).items():
		values = shown[field](row) if shown and field in shown else {row.get(field)}
		if "in" in cond:
			if not {cstr(v) for v in values if v not in (None, "")} & {cstr(v) for v in cond["in"]}:
				return False
		elif "between" in cond:
			start, end = (list(cond["between"] or []) + [None, None])[:2]
			value = cstr(next(iter(values), "") or "")[:10]
			if not value or (start and value < start) or (end and value > end):
				return False
		else:
			value = flt(next(iter(values), 0))
			if cond.get("min") not in (None, "") and value < flt(cond["min"]):
				return False
			if cond.get("max") not in (None, "") and value > flt(cond["max"]):
				return False
	return True


# ── Options ──────────────────────────────────────────────────────────────────────


@frappe.whitelist()
def list_filter_options(doctype, fields, user_fields=None):
	"""The values to offer for each option-type filter: {field: [{"value", "label"}]}, taken
	from the documents the user can read. `user_fields` hold user ids, labelled by full name."""
	frappe.has_permission(doctype, "read", throw=True)
	fields = _parse_list(fields)
	user_fields = set(_parse_list(user_fields))

	if doctype == "Supplier Quotation":
		from possibleworks.finance.supplier_quotation_list import filter_options

		return filter_options(fields)

	meta = frappe.get_meta(doctype)
	names = None
	options = {}
	for field in fields:
		reader = _shown_values_reader(doctype, field)
		if reader:
			if names is None:
				names = frappe.get_list(doctype, pluck="name", limit_page_length=_MAX_CANDIDATES)
			values = set().union(*reader(doctype, names).values()) if names else set()
		elif field == "docstatus":
			values = {cstr(v) for v in frappe.get_list(doctype, pluck="docstatus", distinct=True)}
		elif _is_column(meta, field):
			values = set(
				frappe.get_list(
					doctype,
					fields=[field],
					filters=[[field, "is", "set"]],
					pluck=field,
					distinct=True,
					limit_page_length=_MAX_OPTIONS,
				)
			)
		else:
			continue
		options[field] = _labelled(field, values, field in user_fields)
	return options


def _labelled(field, values, is_user):
	values = sorted({cstr(v) for v in values if v not in (None, "")}, key=str.lower)[:_MAX_OPTIONS]
	if field == "docstatus":
		return [{"value": v, "label": DOCSTATUS_LABELS.get(v, v)} for v in values]
	if is_user and values:
		names = dict(frappe.get_all("User", filters={"name": ("in", values)}, fields=["name", "full_name"], as_list=True))
		labelled = [{"value": v, "label": names.get(v) or v} for v in values]
		return sorted(labelled, key=lambda option: option["label"].lower())
	return [{"value": v, "label": v} for v in values]
