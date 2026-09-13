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


# --------------------------------------------------------------------------- payment leg
#
# The chain a purchase actually travels is
#   Material Request -> Purchase Indent -> Purchase Order -> Purchase Receipt
#                    -> Purchase Invoice -> Payment Entry
# and only at the end of it are the goods both in the store and paid for. The Stores
# Manager is then the one who still owes the original requester their items, so the
# Payment Entry is where the request finally closes -- by issuing what was asked for.
#
# The issue is mapped from the MATERIAL REQUEST, not from the payment: a Payment Entry
# carries money, not items, and the request is the only document in the chain that knows
# what is still outstanding. Reusing `make_material_issue` also means the quantities come
# out of the same live-stock calculation the Stores Manager card uses, so the two can
# never disagree.


def _payment_reference_names(payment_entry, reference_doctype):
	"""Names of one reference doctype on a Payment Entry, in row order."""
	rows = frappe.get_all(
		"Payment Entry Reference",
		filters={"parent": payment_entry, "reference_doctype": reference_doctype},
		fields=["reference_name"],
		order_by="idx",
	)
	names = []
	for row in rows:
		if row["reference_name"] and row["reference_name"] not in names:
			names.append(row["reference_name"])
	return names


def _child_links(child_doctype, parents, fieldname):
	"""Distinct non-empty values of one field across several parents' child rows."""
	if not parents:
		return []
	rows = frappe.get_all(
		child_doctype,
		filters={"parent": ["in", parents], fieldname: ["is", "set"]},
		fields=[fieldname],
		order_by="parent, idx",
	)
	values = []
	for row in rows:
		value = row[fieldname]
		if value and value not in values:
			values.append(value)
	return values


def _material_requests_behind_payment(payment_entry):
	"""Walk a Payment Entry back to the Material Request(s) that started it.

	Payment Entry -> Purchase Invoice -> Purchase Receipt -> Material Request, which is
	all standard erpnext link fields. The Purchase Order route is tried as well because a
	Purchase Invoice can be raised straight off the order with no receipt in between, and
	from an order the request is reachable either directly (erpnext's own
	`material_request` on the order line) or through our Purchase Indent.

	Returns the receipts alongside the requests so a caller can fall back to what
	physically arrived when no request is in the chain -- a Purchase Order raised on its
	own, with nobody having requested anything.
	"""
	invoices = _payment_reference_names(payment_entry, "Purchase Invoice")
	receipts = _child_links("Purchase Invoice Item", invoices, "purchase_receipt")
	orders = _child_links("Purchase Invoice Item", invoices, "purchase_order")

	requests = _child_links("Purchase Receipt Item", receipts, "material_request")
	if not requests:
		requests = _child_links("Purchase Order Item", orders, "material_request")
	if not requests:
		indents = _child_links("Purchase Order Item", orders, "purchase_indent")
		requests = _child_links("Purchase Indent Item", indents, "material_request")

	return requests, receipts


def _unissued_stock_qty(row):
	"""What a request row still owes its requester, in stock UOM.

	Deliberately NOT `_outstanding_stock_qty`. That one also subtracts the quantity an
	indent has claimed, which is right while the indent is still a pending claim -- it
	stops the Stores Manager indenting or issuing the same shortage twice. By the time a
	payment has been made, though, that claim has been fulfilled: the indent became a
	Purchase Order, the order became a receipt, and the goods are now the very stock
	being issued. Subtracting it again would count it twice and leave a fully indented
	request with nothing issuable, which is exactly what happens if this calls the other
	helper.

	Only issues are subtracted, so a line part-issued from existing stock at approval
	time correctly owes just the remainder.
	"""
	issued = _consumed_stock_qty("Stock Entry Detail", lambda c: c.transfer_qty, row.name)
	return max(flt(row.stock_qty) - issued, 0.0)


def _material_issue_for_requests(requests, target_doc=None):
	"""Build one Material Issue covering every request behind a payment.

	One Stock Entry rather than one per request: a payment settles invoices that can draw
	on several requests, and the store issues the goods once. Rows carry their
	`material_request` / `material_request_item` back-links so
	`StockEntry.validate_with_material_request` is satisfied and the request's own
	consumption arithmetic sees the issue.

	Quantities come from the same `_available_stock_qty` / `_consumed_stock_qty`
	primitives the Stores Manager card uses, so the two can never report different stock.
	"""
	target = frappe.get_doc(frappe.parse_json(target_doc)) if target_doc else frappe.new_doc("Stock Entry")
	target.purpose = MATERIAL_ISSUE
	target.stock_entry_type = MATERIAL_ISSUE

	company = None
	for request_name in requests:
		request = frappe.get_doc("Material Request", request_name)
		if request.docstatus != 1:
			continue
		company = company or request.company
		for row in request.items:
			factor = flt(row.conversion_factor) or 1.0
			issuable = min(_available_stock_qty(row), _unissued_stock_qty(row))
			if issuable <= 0:
				continue
			target.append("items", {
				"item_code": row.item_code,
				"item_name": row.item_name,
				"description": row.description,
				"qty": issuable / factor,
				"uom": row.uom,
				"stock_uom": row.stock_uom,
				"conversion_factor": factor,
				# Issuing moves stock OUT of the store the request names.
				"s_warehouse": row.warehouse,
				"t_warehouse": None,
				"material_request": request_name,
				"material_request_item": row.name,
			})

	if not target.get("items"):
		frappe.throw(
			_("Nothing left to issue against {0} -- either it has all been issued already, or the stock is no longer on hand.").format(
				", ".join(requests)
			),
			title=_("Nothing to Issue"),
		)

	_apply_stock_entry_defaults(target, company)
	return target


@frappe.whitelist()
def make_material_issue_from_payment(source_name, target_doc=None, args=None):
	"""Payment Entry -> Stock Entry (Material Issue), via the request that started it."""
	requests, receipts = _material_requests_behind_payment(source_name)

	if requests:
		return _material_issue_for_requests(requests, target_doc)

	if receipts:
		return _material_issue_from_receipts(receipts, target_doc)

	frappe.throw(
		_("Nothing to issue for Payment Entry {0}: no Purchase Receipt or Material Request is linked to the invoices it settles.").format(
			source_name
		)
	)


def _apply_stock_entry_defaults(target, company=None):
	"""Fill what a hand-built Stock Entry needs before its own validation runs.

	Same trap as `make_material_issue`: the client hands over a partial target doc and
	`frappe.get_doc` on a dict applies no field defaults, so posting_date/posting_time
	stay None and `set_actual_qty` raises out of `get_combine_datetime`.
	"""
	if not target.posting_date:
		target.posting_date = nowdate()
	if not target.posting_time:
		target.posting_time = nowtime()
	if not target.company and company:
		target.company = company

	# The rows carry s_warehouse, which is what actually posts, but the form also shows a
	# header "Source Warehouse" and erpnext's own mappers fill it. Left blank it reads as
	# a half-built document. Only set when every row agrees: a single header value for an
	# issue drawn from two warehouses would be wrong, and blank is the honest answer.
	if not target.from_warehouse:
		sources = set()
		for row in target.get("items") or []:
			sources.add(row.get("s_warehouse"))
		if len(sources) == 1:
			only = sources.pop()
			if only:
				target.from_warehouse = only

	target.set_actual_qty()
	target.calculate_rate_and_amount()


def _material_issue_from_receipts(receipts, target_doc=None):
	"""Fallback: issue what physically arrived, when no request is behind the payment.

	Built directly rather than through `get_mapped_doc` because there is no single source
	document here -- a payment can settle invoices drawn from several receipts, and the
	rows have to be gathered across all of them.
	"""
	target = frappe.get_doc(frappe.parse_json(target_doc)) if target_doc else frappe.new_doc("Stock Entry")
	target.purpose = MATERIAL_ISSUE
	target.stock_entry_type = MATERIAL_ISSUE

	rows = frappe.get_all(
		"Purchase Receipt Item",
		filters={"parent": ["in", receipts], "docstatus": 1},
		fields=["item_code", "item_name", "description", "stock_uom", "uom",
		        "conversion_factor", "stock_qty", "warehouse", "parent"],
		order_by="parent, idx",
	)
	for row in rows:
		if flt(row["stock_qty"]) <= 0:
			continue
		target.append("items", {
			"item_code": row["item_code"],
			"item_name": row["item_name"],
			"description": row["description"],
			# stock_qty, so the issue is in stock UOM and needs no conversion factor of
			# its own -- the receipt's factor applied to the purchase UOM, not this one.
			"qty": flt(row["stock_qty"]),
			"uom": row["stock_uom"],
			"stock_uom": row["stock_uom"],
			"conversion_factor": 1.0,
			"s_warehouse": row["warehouse"],
			"t_warehouse": None,
		})

	if not target.get("items"):
		frappe.throw(_("The receipts behind this payment have no quantity left to issue."))

	_apply_stock_entry_defaults(
		target, frappe.db.get_value("Purchase Receipt", receipts[0], "company")
	)
	return target
