# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Stores Manager leg of the procurement flow.

Once the Principal approves a Material Request it lands with the Stores Manager, who
decides per line how much can be issued from stock and how much has to be bought. This
module supplies the two things that decision needs: the live stock picture, and a
Material Issue mapper.

Why a mapper of our own rather than erpnext's `make_stock_entry`: that one does
`target.purpose = source.material_request_type` and then
`target.stock_entry_type = target.purpose` (material_request.py:776-790). These requests
are raised as `Purchase`, so it would produce `stock_entry_type = "Purchase"` -- not a
valid Stock Entry purpose -- and fail validation.

Keeping the requests as `Purchase` is deliberate. `MaterialRequest.update_completed_qty`
returns early for that type, so the Stock Entry rows this creates cannot fight the
Purchase Order raised through the indent, which writes the same `ordered_qty` column via
Purchase Order's `status_updater`. With type `Material Issue` both would write it and a
split line (some issued, some ordered) would flip between the two figures.
`StockEntry.validate_with_material_request` only checks `item_code`, never the request's
type, so a Material Issue against a Purchase request is perfectly legal.
"""

import frappe
from frappe import _
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder.functions import Sum
from frappe.utils import flt, nowdate, nowtime

MATERIAL_ISSUE = "Material Issue"


def _consumed_stock_qty(child_doctype, qty_field, material_request_item):
	"""Stock-UOM quantity already committed against one request row.

	Drafts count for indents but not for issues: a draft Purchase Indent is a live claim
	on the shortage that the Stores Manager should not indent twice, whereas an unsubmitted
	Stock Entry has moved nothing.
	"""
	child = frappe.qb.DocType(child_doctype)
	docstatus_filter = child.docstatus == 1 if child_doctype == "Stock Entry Detail" else child.docstatus < 2

	result = (
		frappe.qb.from_(child)
		.select(Sum(qty_field(child)))
		.where((child.material_request_item == material_request_item) & docstatus_filter)
		.run()
	)
	return flt(result[0][0]) if result and result[0] else 0.0


def _available_stock_qty(row):
	"""Live stock at the warehouse the row names, in stock UOM."""
	return flt(
		frappe.db.get_value(
			"Bin", {"item_code": row.item_code, "warehouse": row.warehouse}, "actual_qty"
		)
	)


def _outstanding_stock_qty(row):
	"""What is still uncovered on a request row, in stock UOM.

	Derived from actual Stock Entry and Purchase Indent consumption rather than the row's
	`ordered_qty`. That column is inert here by design: these requests are `Purchase`
	type, for which `MaterialRequest.update_completed_qty` returns early, so it never
	reflects an issue. Reading it would report a covered line as fully outstanding.
	"""
	issued = _consumed_stock_qty("Stock Entry Detail", lambda c: c.transfer_qty, row.name)
	indented = _consumed_stock_qty("Purchase Indent Item", lambda c: c.stock_qty, row.name)
	return max(flt(row.stock_qty) - issued - indented, 0.0)


@frappe.whitelist()
def get_material_request_stock_position(material_request):
	"""Per-line requested / available / issuable / shortage for one Material Request.

	All arithmetic is done in stock UOM -- `Bin.actual_qty`, `Stock Entry Detail.transfer_qty`
	and `Purchase Indent Item.stock_qty` are all stock-UOM figures, while the row's own
	`qty` is in its transaction UOM. The issuable and shortage numbers are converted back
	to the row's UOM on the way out, because that is what the card displays and what the
	mappers expect in `qty_overrides`.

	Live from `Bin` on purpose: `Material Request Item.actual_qty` is a snapshot written
	when the row was entered and is routinely stale by the time stores sees it.
	"""
	request = frappe.get_doc("Material Request", material_request)
	request.check_permission("read")

	lines = []
	for row in request.items:
		factor = flt(row.conversion_factor) or 1.0

		available = _available_stock_qty(row)
		issued = _consumed_stock_qty("Stock Entry Detail", lambda c: c.transfer_qty, row.name)
		indented = _consumed_stock_qty("Purchase Indent Item", lambda c: c.stock_qty, row.name)

		outstanding = _outstanding_stock_qty(row)
		issuable = max(min(available, outstanding), 0.0)
		shortage = max(outstanding - available, 0.0)

		lines.append(
			{
				"idx": row.idx,
				"material_request_item": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"description": row.description,
				"warehouse": row.warehouse,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": factor,
				"schedule_date": row.schedule_date,
				"rate": flt(row.rate),
				# Display figures, in the row's own UOM.
				"requested_qty": flt(row.qty),
				"available_qty": available / factor,
				"issued_qty": issued / factor,
				"indented_qty": indented / factor,
				"outstanding_qty": outstanding / factor,
				"issuable_qty": issuable / factor,
				"shortage_qty": shortage / factor,
			}
		)

	return {
		"material_request": request.name,
		"company": request.company,
		"workflow_state": request.get("workflow_state"),
		"transaction_date": request.transaction_date,
		"schedule_date": request.schedule_date,
		"requested_by": request.owner,
		"department": request.get("custom_department"),
		"purpose_note": request.get("custom_purpose_note"),
		"items": lines,
		"has_shortage": any(line["shortage_qty"] > 0 for line in lines),
		"has_issuable": any(line["issuable_qty"] > 0 for line in lines),
	}


@frappe.whitelist()
def make_material_issue(source_name, target_doc=None, args=None):
	"""Material Request -> Stock Entry (Material Issue).

	`args["qty_overrides"]` maps a Material Request Item name to the quantity to issue,
	in that row's UOM -- the Stores Manager card sends what stock can actually cover. With
	no override a row falls back to its outstanding quantity, matching erpnext's own
	mapper.
	"""
	args = frappe.parse_json(args) if args else {}
	qty_overrides = args.get("qty_overrides") or {}
	filtered_children = args.get("filtered_children") or []

	def issue_qty(source_row):
		"""How much to issue for a row, in that row's UOM.

		With no override this is what stock can actually cover of what is still
		uncovered -- the same figure `get_material_request_stock_position` reports as
		`issuable_qty`, so the card and a direct call can never disagree.
		"""
		factor = flt(source_row.conversion_factor) or 1.0
		if source_row.name in qty_overrides:
			return flt(qty_overrides[source_row.name])
		issuable = min(_available_stock_qty(source_row), _outstanding_stock_qty(source_row))
		return max(issuable, 0.0) / factor

	def select_item(source_row):
		# A row stock cannot cover at all is dropped rather than issued at qty 0, which
		# Stock Entry would reject anyway.
		if issue_qty(source_row) <= 0:
			return False
		return source_row.name in filtered_children if filtered_children else True

	def update_item(source_row, target_row, source_parent):
		factor = flt(source_row.conversion_factor) or 1.0
		target_row.qty = issue_qty(source_row)
		target_row.conversion_factor = factor
		target_row.transfer_qty = target_row.qty * factor
		# Issuing moves stock OUT of the store the request names, so it is the source
		# warehouse here. Only a transfer or a receipt would populate t_warehouse.
		target_row.s_warehouse = source_row.warehouse
		target_row.t_warehouse = None

	def postprocess(source, target):
		target.purpose = MATERIAL_ISSUE
		target.stock_entry_type = MATERIAL_ISSUE
		target.from_warehouse = source.set_warehouse
		target.to_warehouse = None

		# Load-bearing, not defensive. The client hands `map_docs` a partial target doc
		# built from its form config, which carries `posting_date` but no `posting_time`
		# -- and `frappe.get_doc` on a dict applies no field defaults, so it stays None.
		# `set_actual_qty` below feeds both into `get_combine_datetime`
		# (stock/utils.py:248), which then raises
		# "combine() argument 2 must be datetime.time, not None". The mapper has to
		# return a valid document whatever partial target it is handed.
		if not target.posting_date:
			target.posting_date = nowdate()
		if not target.posting_time:
			target.posting_time = nowtime()

		if not target.items:
			frappe.throw(
				_("Nothing on {0} can be issued from stock right now.").format(
					frappe.utils.get_link_to_form("Material Request", source.name)
				),
				title=_("No Stock to Issue"),
			)

		target.set_transfer_qty()
		target.set_actual_qty()
		target.calculate_rate_and_amount(raise_error_if_no_rate=False)

	return get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Stock Entry",
				"validation": {"docstatus": ["=", 1]},
			},
			"Material Request Item": {
				"doctype": "Stock Entry Detail",
				"field_map": {
					"name": "material_request_item",
					"parent": "material_request",
					"uom": "uom",
				},
				"postprocess": update_item,
				"condition": select_item,
			},
		},
		target_doc,
		postprocess,
	)
