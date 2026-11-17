# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Supplier Quotation list grouped by the Purchase Indents and RFQs it came from.

Backs the React Supplier Quotation list: its merged PI ID / RFQ ID cells and its
"Both RFQ and PI / Only RFQ / Only PI / None" filter. Those links live on the
Supplier Quotation Item rows, so neither the filter nor "never split a group across
pages" fits a plain get_list page. Quotations are read through get_list, so the
user's permissions still apply, and are then grouped, filtered, sorted and paged
here.
"""

import frappe
from frappe import _
from frappe.utils import cint

LINK_FILTERS = ("both", "rfq_only", "pi_only", "none")

# PI ID / RFQ ID headers order the groups themselves; every other column orders the
# rows inside each group, and the groups by their best row.
GROUP_SORT_FIELDS = {"purchase_indent": "purchase_indents", "request_for_quotation": "rfqs"}
ROW_SORT_FIELDS = (
	"modified",
	"supplier_name",
	"status",
	"transaction_date",
	"grand_total",
	"department",
	"name",
)

SEARCH_FIELDS = ("name", "supplier_name", "status", "department", "transaction_date", "grand_total")


@frappe.whitelist()
def get_supplier_quotation_groups(
	link_filter=None, search=None, sort_by="modified", sort_order="desc", page=0, page_size=10
):
	if link_filter and link_filter not in LINK_FILTERS:
		frappe.throw(_("Unknown link filter: {0}").format(link_filter))
	if sort_by not in GROUP_SORT_FIELDS and sort_by not in ROW_SORT_FIELDS:
		sort_by = "modified"
	descending = sort_order != "asc"

	rows = [
		row
		for row in _load_rows()
		if (not link_filter or link_type(row) == link_filter) and _matches_search(row, search)
	]
	pages = paginate(sort_groups(group_rows(rows), sort_by, descending), max(1, cint(page_size)))
	page = min(max(cint(page), 0), max(len(pages) - 1, 0))

	return {
		"groups": pages[page] if pages else [],
		"page": page,
		"total_pages": len(pages),
		"total_rows": len(rows),
		"rows_through_page": sum(len(group["rows"]) for groups in pages[: page + 1] for group in groups),
	}


def get_department_labels(purchase_indents):
	"""{Purchase Indent: its department's display name} for the given indents."""
	if not purchase_indents:
		return {}

	departments = dict(
		frappe.get_all(
			"Purchase Indent",
			filters={"name": ["in", list(purchase_indents)], "department": ["is", "set"]},
			fields=["name", "department"],
			as_list=True,
		)
	)
	# Department names carry a company suffix ("IT - GVS"); the list shows department_name.
	labels = dict(
		frappe.get_all(
			"Department",
			filters={"name": ["in", list(set(departments.values()))]},
			fields=["name", "department_name"],
			as_list=True,
		)
	) if departments else {}
	return {indent: labels.get(department) or department for indent, department in departments.items()}


def link_type(row):
	if row["purchase_indents"] and row["rfqs"]:
		return "both"
	if row["rfqs"]:
		return "rfq_only"
	if row["purchase_indents"]:
		return "pi_only"
	return "none"


def group_rows(rows):
	"""One group per distinct (Purchase Indents, RFQs) pair, in first-seen order.

	A quotation linked to neither is a group of its own: merging every unlinked
	quotation into one "—" cell would say they belong together, which they don't.
	"""
	groups = {}
	for row in rows:
		key = (tuple(row["purchase_indents"]), tuple(row["rfqs"]))
		if key == ((), ()):
			key = row["name"]
		group = groups.setdefault(
			key, {"purchase_indents": row["purchase_indents"], "rfqs": row["rfqs"], "rows": []}
		)
		group["rows"].append(row)
	return list(groups.values())


def sort_groups(groups, sort_by, descending):
	if sort_by in GROUP_SORT_FIELDS:
		ids = GROUP_SORT_FIELDS[sort_by]
		for group in groups:
			group["rows"] = _sorted(group["rows"], lambda row: row.get("modified"), True)
		return _sorted(groups, lambda group: ", ".join(group[ids]), descending)

	for group in groups:
		group["rows"] = _sorted(group["rows"], lambda row: row.get(sort_by), descending)
	# Rows are already sorted, so a group's first row holds its best value.
	return _sorted(groups, lambda group: group["rows"][0].get(sort_by), descending)


def paginate(groups, page_size):
	"""Whole groups per page: a page closes once it holds page_size rows, so it can
	run over page_size rather than split a group across two pages."""
	pages, current, count = [], [], 0
	for group in groups:
		current.append(group)
		count += len(group["rows"])
		if count >= page_size:
			pages.append(current)
			current, count = [], 0
	if current:
		pages.append(current)
	return pages


def _sorted(items, value, descending):
	"""Stable sort on value(item), with blank values always last in either direction."""
	present = [item for item in items if value(item) not in (None, "")]
	blank = [item for item in items if value(item) in (None, "")]

	def comparable(item):
		v = value(item)
		return v.lower() if isinstance(v, str) else v

	return sorted(present, key=comparable, reverse=descending) + blank


def _matches_search(row, search):
	term = (search or "").strip().lower()
	if not term:
		return True
	values = [str(row.get(field) or "") for field in SEARCH_FIELDS] + row["purchase_indents"] + row["rfqs"]
	return any(term in value.lower() for value in values)


def _load_rows():
	fields = ["name", "supplier_name", "status", "docstatus", "transaction_date", "grand_total", "modified"]
	if frappe.get_meta("Supplier Quotation").has_field("workflow_state"):
		fields.append("workflow_state")

	# shortcut: reads every permitted quotation per call; move the paging into SQL if
	# a tenant's Supplier Quotations reach the tens of thousands.
	quotations = frappe.get_list(
		"Supplier Quotation", fields=fields, order_by="modified desc", limit_page_length=0
	)
	if not quotations:
		return []

	items = frappe.get_all(
		"Supplier Quotation Item",
		filters={"parent": ["in", [quotation.name for quotation in quotations]]},
		fields=["parent", "purchase_indent", "request_for_quotation"],
	)
	indents, rfqs = {}, {}
	for item in items:
		if item.purchase_indent:
			indents.setdefault(item.parent, set()).add(item.purchase_indent)
		if item.request_for_quotation:
			rfqs.setdefault(item.parent, set()).add(item.request_for_quotation)

	departments = get_department_labels({indent for names in indents.values() for indent in names})

	rows = []
	for quotation in quotations:
		quotation_indents = sorted(indents.get(quotation.name, ()))
		rows.append(
			{
				**quotation,
				"purchase_indents": quotation_indents,
				"rfqs": sorted(rfqs.get(quotation.name, ())),
				"department": ", ".join(
					sorted({departments[indent] for indent in quotation_indents if indent in departments})
				)
				or None,
			}
		)
	return rows
