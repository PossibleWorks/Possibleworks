# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.mapper import get_mapped_doc
from frappe.query_builder import Order
from frappe.utils import cint, flt, formatdate, get_link_to_form, getdate, nowdate

from erpnext.stock.doctype.item.item import get_item_defaults, get_uom_conv_factor

# Statuses a submitted indent can legitimately hold. `set_status` leaves every one
# of these alone, so the Workflow attached later owns the transitions between them
# without this controller fighting it on each save.
SUBMITTED_STATUSES = frozenset(
	{
		"Pending",
		"Partially Ordered",
		"Partially Received",
		"Ordered",
		"Issued",
		"Transferred",
		"Received",
		"Stopped",
	}
)


class PurchaseIndent(Document):
	"""Material Request, narrowed to purchasing.

	The schema is a generated clone of Material Request minus `material_request_type`
	("Purpose") -- see `_generate_from_material_request.py` alongside this file. Because
	that field is gone, this deliberately does NOT subclass erpnext's BuyingController:
	almost every validation there branches on the purpose, and the tax, currency and
	party machinery it drags along has no meaning for an indent that never names a
	supplier. The few validations that do apply are written out below.

	Deliberately absent: no write-back to the source Material Request (pulling items
	leaves its `% Ordered` and status untouched), and no duplicate-item check -- the
	same item arriving from two different requests is the normal case here, not an error.
	"""

	def validate(self):
		self.set_defaults_on_items()
		self.validate_items()
		self.validate_schedule_dates()
		self.set_status()

	def on_cancel(self):
		# `validate` does not run on cancel, and `on_cancel` fires after the row has
		# already been written, so this has to go straight to the column.
		self.db_set("status", "Cancelled")

	def set_defaults_on_items(self):
		"""Fill row blanks from the header, then reflect the rows back up.

		`schedule_date` and `warehouse` are required per row, so without this anyone
		who filled the header once would still have to retype both on every line.
		"""
		for row in self.items:
			if not row.schedule_date:
				row.schedule_date = self.schedule_date
			if not row.warehouse:
				row.warehouse = self.set_warehouse

		# Rows carry their own dates when they arrive from several Material Requests;
		# the header then reports the earliest of them.
		if not self.schedule_date:
			row_dates = [getdate(row.schedule_date) for row in self.items if row.schedule_date]
			if row_dates:
				self.schedule_date = min(row_dates)

	def validate_items(self):
		if not self.items:
			frappe.throw(_("Add at least one item before saving."))

		for row in self.items:
			if flt(row.qty) <= 0:
				frappe.throw(_("Row #{0}: Quantity must be greater than zero.").format(row.idx))

			# `stock_uom` arrives by fetch_from on item_code. Defaulting uom to it keeps
			# the row saveable for anyone who never touches the UOM column, since both
			# uom and conversion_factor are mandatory.
			if not row.uom:
				row.uom = row.stock_uom

			if not flt(row.conversion_factor):
				factor = get_uom_conv_factor(row.uom, row.stock_uom)
				if not factor:
					frappe.throw(
						_(
							"Row #{0}: No conversion is defined from {1} to the stock UOM {2} for item {3}. "
							"Add a UOM Conversion Factor, or enter the factor on the row."
						).format(row.idx, row.uom, row.stock_uom, row.item_code)
					)
				row.conversion_factor = factor

			row.stock_qty = flt(row.qty) * flt(row.conversion_factor)
			row.amount = flt(row.qty) * flt(row.rate)

	def validate_schedule_dates(self):
		"""Nothing may be required before the indent that asks for it was raised."""
		transaction_date = getdate(self.transaction_date)

		if self.schedule_date and getdate(self.schedule_date) < transaction_date:
			frappe.throw(
				_("Required By ({0}) cannot be earlier than the Transaction Date ({1}).").format(
					formatdate(self.schedule_date), formatdate(self.transaction_date)
				)
			)

		for row in self.items:
			if row.schedule_date and getdate(row.schedule_date) < transaction_date:
				frappe.throw(
					_(
						"Row #{0}: Required By ({1}) cannot be earlier than the Transaction Date ({2})."
					).format(row.idx, formatdate(row.schedule_date), formatdate(self.transaction_date))
				)

	def set_status(self):
		"""Draft and Cancelled track docstatus; a fresh submission lands on Pending.

		A submitted indent that already carries a meaningful status is left as it is,
		so a Workflow can move it on without this resetting it on every save.
		"""
		if self.docstatus == 0:
			self.status = "Draft"
		elif self.docstatus == 1 and self.status not in SUBMITTED_STATUSES:
			self.status = "Pending"


# The buying documents that can be raised straight off a Material Request. A row in one
# of these whose `purchase_indent` is blank means someone went direct, bypassing the
# indent -- and that request must not then be indented as well, or the same line gets
# ordered twice. A row WITH `purchase_indent` set came through the sanctioned route and
# is fine: a part-ordered request has to stay available for the balance.
DIRECT_BUYING_CHILD_TABLES = (
	"Purchase Order Item",
	"Request for Quotation Item",
	"Supplier Quotation Item",
)


def served_directly(material_request):
	"""Buying documents raised straight from this Material Request, bypassing an indent."""
	served = []

	for child_table in DIRECT_BUYING_CHILD_TABLES:
		child = frappe.qb.DocType(child_table)
		served.extend(
			frappe.qb.from_(child)
			.select(child.parent, child.parenttype)
			.distinct()
			.where(
				(child.material_request == material_request)
				& (child.docstatus < 2)
				& (child.purchase_indent.isnull() | (child.purchase_indent == ""))
			)
			.run(as_dict=True)
		)

	return served


def _not_served_directly(query, material_request):
	"""Add one NOT IN per direct-buying table to a Material Request query.

	`material_request.notnull()` inside each subquery is load-bearing, not defensive:
	`NOT IN` against a set containing NULL evaluates to NULL for every row, so a single
	unlinked row would silently empty the whole picker.
	"""
	for child_table in DIRECT_BUYING_CHILD_TABLES:
		child = frappe.qb.DocType(child_table)
		subquery = (
			frappe.qb.from_(child)
			.select(child.material_request)
			.distinct()
			.where(
				child.material_request.notnull()
				& (child.material_request != "")
				& (child.docstatus < 2)
				& (child.purchase_indent.isnull() | (child.purchase_indent == ""))
			)
		)
		query = query.where(material_request.name.notin(subquery))

	return query


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_indentable_material_requests(doctype, txt, searchfield, start, page_len, filters):
	"""Material Requests the picker on a Purchase Indent may offer.

	A plain `get_query_filters` cannot express "has no buying document raised directly
	against it", which is why this is a custom query rather than a filter dict. Purpose
	is deliberately unfiltered -- an indent may be raised against a request of any type.
	"""
	material_request = frappe.qb.DocType("Material Request")

	query = (
		frappe.qb.from_(material_request)
		.select(material_request.name, material_request.schedule_date)
		.where(
			(material_request.docstatus == 1)
			& (material_request.status != "Stopped")
			& (material_request.company == filters.get("company"))
		)
		.orderby(material_request.transaction_date, order=Order.desc)
		.limit(cint(page_len))
		.offset(cint(start))
	)

	query = _not_served_directly(query, material_request)

	if txt:
		query = query.where(material_request.name.like(f"%{txt}%"))

	if filters.get("schedule_date"):
		query = query.where(material_request.schedule_date == filters.get("schedule_date"))

	return query.run(as_dict=True)


@frappe.whitelist()
def make_purchase_indent(source_name, target_doc=None, args=None):
	"""Map one Material Request onto a Purchase Indent.

	`erpnext.utils.map_current_doc` calls this once per ticked Material Request and
	threads the in-progress indent back in through `target_doc`. That is what lets
	several requests accumulate into one indent instead of replacing each other.

	`args["filtered_children"]` is populated only when the picker was used to tick
	individual item rows instead of whole requests.

	The picker already hides ineligible requests; this repeats the check because the
	picker is a convenience and this is the authority -- the method is whitelisted, so a
	stale form or a direct call must not be able to double-order a request either.
	"""
	served = served_directly(source_name)
	if served:
		frappe.throw(
			_(
				"{0} cannot be added to a Purchase Indent because {1} was raised directly "
				"against it. Indenting it as well would order the same lines twice."
			).format(
				get_link_to_form("Material Request", source_name),
				", ".join(get_link_to_form(row.parenttype, row.parent) for row in served),
			),
			title=_("Already Ordered Directly"),
		)

	args = frappe.parse_json(args) if args else {}
	filtered_children = args.get("filtered_children") or []

	def select_item(source_row):
		# Empty means whole requests were ticked, so every row maps. Non-empty means
		# the user drilled into the grid, and only what they chose may come through.
		return source_row.name in filtered_children if filtered_children else True

	def postprocess(source, target):
		# `map_doc` copies each source row's idx verbatim, so pulling a second Material
		# Request would otherwise hand the grid a table with duplicate idx values.
		for position, row in enumerate(target.items, start=1):
			row.idx = position

	return get_mapped_doc(
		"Material Request",
		source_name,
		{
			"Material Request": {
				"doctype": "Purchase Indent",
				# Only the submitted state is enforced. Purpose is deliberately not
				# filtered: an indent may be raised against a request of any type.
				"validation": {"docstatus": ["=", 1]},
				"field_map": {
					"custom_department": "department",
					"custom_purpose_note": "purpose_note",
				},
			},
			"Material Request Item": {
				"doctype": "Purchase Indent Item",
				"field_map": {
					"name": "material_request_item",
					"parent": "material_request",
					"custom_mis": "mis",
					"custom_area_of_application": "area_of_application",
					"custom_additional_details": "additional_details",
				},
				"condition": select_item,
			},
		},
		target_doc,
		postprocess,
	)


# ------------------------------------------------------------------ outbound mappers
#
# MR -> PI -> PO / RFQ / SQ. Every mapper below leaves `material_request` and
# `material_request_item` to copy across by name, which is the whole reason the indent
# can sit in the middle of that chain without disturbing it: Purchase Order's
# `status_updater` joins on `material_request_item` to drive the original request's
# `ordered_qty` and `per_ordered`, so the request still closes out exactly as it did
# when Purchase Orders were raised from it directly.
#
# The `purchase_indent` / `purchase_indent_item` pair going the other way is added by
# `patches/v1_6/create_purchase_indent_link_fields` and is what `purchase_indent_status`
# aggregates to keep this indent's own `% Ordered` current.


def _mapped_child(field_map=None, **extra):
	"""Child-table map shared by all three targets, with the per-target bits merged in.

	None-valued entries are dropped rather than passed through. `get_mapped_doc` tests
	`if "condition" in table_map` -- a presence check, not a truthiness one -- so a
	`condition` of None is still called, and raises TypeError instead of meaning
	"no condition".
	"""
	return {
		"field_map": {
			"name": "purchase_indent_item",
			"parent": "purchase_indent",
			**(field_map or {}),
		},
		**{key: value for key, value in extra.items() if value is not None},
	}


def _outbound_args(args):
	"""Normalise the `args` an outbound mapper can arrive with.

	Two entry points, two shapes. The `Create` buttons on a Purchase Indent go through
	`open_mapped_doc`, which stashes its args on `frappe.flags.args` and passes nothing
	positionally. The `Get Items From > Purchase Indent` pickers on the buying forms go
	through `map_docs`, which passes them as the third positional argument. Missing
	either one means a supplier choice or a row selection is silently ignored.
	"""
	return frappe.parse_json(args) if args else (frappe.flags.args or frappe._dict())


def _row_selected(filtered_children):
	"""Row filter for the pickers' `allow_child_item_selection`.

	Empty means whole indents were ticked, so every row maps; non-empty means the user
	expanded one and chose specific lines, and only those may come through.
	"""
	return lambda source_row: source_row.name in filtered_children


@frappe.whitelist()
def make_purchase_order(source_name, target_doc=None, args=None):
	"""Purchase Indent -> Purchase Order.

	`default_supplier` comes from the optional prompt on the indent's own button and
	matches Material Request's behaviour: choosing a supplier keeps only the lines whose
	item defaults to them, so one indent can be split into a Purchase Order per supplier.
	"""
	args = _outbound_args(args)
	default_supplier = args.get("default_supplier")
	filtered_children = args.get("filtered_children") or []

	def select_item(source_row):
		# Lines a Purchase Order has already covered in full are never offered again.
		if flt(source_row.ordered_qty) >= flt(source_row.stock_qty):
			return False
		return _row_selected(filtered_children)(source_row) if filtered_children else True

	def update_item(source_row, target_row, source_parent):
		target_row.conversion_factor = flt(source_row.conversion_factor) or 1.0
		outstanding = flt(source_row.stock_qty) - flt(source_row.ordered_qty)
		target_row.qty = outstanding / target_row.conversion_factor
		target_row.stock_qty = outstanding

	def postprocess(source, target):
		if default_supplier:
			target.items = [
				row
				for row in target.items
				if get_item_defaults(row.item_code, target.company).get("default_supplier")
				== default_supplier
			]

		# A Purchase Order refuses a schedule date in the past, which an older indent
		# will happily still be carrying.
		if target.schedule_date and getdate(target.schedule_date) < getdate(nowdate()):
			target.schedule_date = None

		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	return get_mapped_doc(
		"Purchase Indent",
		source_name,
		{
			"Purchase Indent": {
				"doctype": "Purchase Order",
				"validation": {"docstatus": ["=", 1]},
			},
			"Purchase Indent Item": _mapped_child(
				doctype="Purchase Order Item",
				postprocess=update_item,
				condition=select_item,
			),
		},
		target_doc,
		postprocess,
	)


@frappe.whitelist()
def make_request_for_quotation(source_name, target_doc=None, args=None):
	"""Purchase Indent -> Request for Quotation."""
	filtered_children = _outbound_args(args).get("filtered_children") or []

	return get_mapped_doc(
		"Purchase Indent",
		source_name,
		{
			"Purchase Indent": {
				"doctype": "Request for Quotation",
				"validation": {"docstatus": ["=", 1]},
			},
			"Purchase Indent Item": _mapped_child(
				doctype="Request for Quotation Item",
				# RFQ Item calls it project_name; every other target uses `project`.
				field_map={"project": "project_name"},
				condition=_row_selected(filtered_children) if filtered_children else None,
			),
		},
		target_doc,
	)


@frappe.whitelist()
def make_supplier_quotation(source_name, target_doc=None, args=None):
	"""Purchase Indent -> Supplier Quotation."""
	filtered_children = _outbound_args(args).get("filtered_children") or []

	def postprocess(source, target):
		target.run_method("set_missing_values")
		target.run_method("calculate_taxes_and_totals")

	doclist = get_mapped_doc(
		"Purchase Indent",
		source_name,
		{
			"Purchase Indent": {
				"doctype": "Supplier Quotation",
				"validation": {"docstatus": ["=", 1]},
			},
			"Purchase Indent Item": _mapped_child(
				doctype="Supplier Quotation Item",
				condition=_row_selected(filtered_children) if filtered_children else None,
			),
		},
		target_doc,
		postprocess,
	)

	# Mirrors Material Request: the supplier is still unknown at this point, so the
	# client-side item-details refresh has nothing to price against and only clears
	# what the mapping just set.
	doclist.set_onload("load_after_mapping", False)
	return doclist


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def get_default_supplier_query(doctype, txt, searchfield, start, page_len, filters):
	"""Suppliers that are the default for at least one item on the given indent."""
	item_codes = frappe.get_all(
		"Purchase Indent Item", filters={"parent": filters.get("doc")}, pluck="item_code"
	)
	if not item_codes:
		return []

	supplier = frappe.qb.DocType("Supplier")
	item_default = frappe.qb.DocType("Item Default")

	query = (
		frappe.qb.from_(supplier)
		.left_join(item_default)
		.on(supplier.name == item_default.default_supplier)
		.select(item_default.default_supplier)
		.distinct()
		.where(
			item_default.parent.isin(item_codes)
			& item_default.default_supplier.notnull()
			& supplier[searchfield].like(f"%{txt}%")
		)
		.offset(start)
		.limit(page_len)
	)

	return query.run(as_dict=False)
