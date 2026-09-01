import json

import frappe
from frappe.utils import flt

def _normalize_value(value):
	if value is None:
		return ""
	if isinstance(value, (int, float)):
		return round(flt(value), 6)
	return str(value).strip()


def _row_signature(row, fields):
	return tuple(_normalize_value(row.get(f)) for f in fields)


@frappe.whitelist()
def check_duplicate(
	doctype,
	header_filters,
	table_doctype,
	table_fieldname,
	table_rows,
	table_compare_fields,
	exclude_name=None,
):
	try:
		if isinstance(header_filters, str):
			header_filters = json.loads(header_filters)
		if isinstance(table_rows, str):
			table_rows = json.loads(table_rows)
		if isinstance(table_compare_fields, str):
			table_compare_fields = json.loads(table_compare_fields)

		current_signature = sorted(
			_row_signature(row, table_compare_fields) for row in (table_rows or [])
		)
		if not current_signature:
			return {"duplicates": []}

		# Fields left blank on the form (None/"") are dropped from the filter -
		# a blank value on the form can't be distinguished from a blank value
		# stored as SQL NULL vs empty string, so filtering on it would only
		# produce false negatives. The child-table exact match plus the
		# remaining concrete header fields still carry the real matching weight.
		filters = {k: v for k, v in (header_filters or {}).items() if v not in (None, "")}
		filters["docstatus"] = ["in", [0, 1]]
		if exclude_name:
			filters["name"] = ["!=", exclude_name]

		candidates = frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=50)
		if not candidates:
			return {"duplicates": []}

		matches = []
		for name in candidates:
			existing_rows = frappe.get_all(
				table_doctype,
				filters={"parent": name, "parenttype": doctype, "parentfield": table_fieldname},
				fields=table_compare_fields,
				order_by="idx",
			)
			existing_signature = sorted(
				_row_signature(row, table_compare_fields) for row in existing_rows
			)
			if existing_signature and existing_signature == current_signature:
				meta = frappe.db.get_value(doctype, name, ["creation", "docstatus"], as_dict=True)
				matches.append(
					{
						"name": name,
						"creation": meta.creation,
						"status": "Submitted" if meta.docstatus == 1 else "Draft",
					}
				)

		return {"duplicates": matches}
	except Exception:
		frappe.log_error(title="Duplicate check failed")
		return {"duplicates": []}
