# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Item-level GST and TDS for Purchase Invoices created outside the desk.

ERPNext v16 already does the work: each item row carries its own
`item_tax_template` (GST slab) and `tax_withholding_category` (TDS section),
GST rows marked "included in print rate" split the entered price into
principal + GST, and TDS is computed per section on the principal. Two gaps
appear when the invoice comes from the PossibleWorks app or an API instead of
the desk:

1. Header `apply_tds` is only switched on by the desk's JS (or by
   `set_missing_values(for_validate=False)`, which a REST insert never calls),
   and only for suppliers with TDS set up, so TDS is silently skipped.
   `apply_tds_from_supplier` ticks it when the supplier has TDS set up or any
   line's item carries a TDS section, and fills a blank Tax Withholding Group
   from the supplier's type so the section's rate row can be found.
2. The app needs to show taxes before saving. `preview_purchase_invoice` runs
   the same steps as `PurchaseInvoice.validate` on an unsaved copy, so the
   numbers on screen are exactly what a save would produce.
"""

import json

import frappe
from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import PurchaseTaxWithholding
from frappe import _

# Header fields resolved from the supplier. Cleared on `refresh_party` so a
# change of supplier picks up the new supplier's GST template and TDS group.
PARTY_TAX_FIELDS = ("tax_category", "taxes_and_charges", "tax_withholding_group")

PREVIEW_NAME = "__pw_preview__"


def supplier_has_tds(supplier):
	if not supplier:
		return False
	category, group = frappe.get_cached_value(
		"Supplier", supplier, ["tax_withholding_category", "tax_withholding_group"]
	) or (None, None)
	return bool(category or group)


def supplier_tds_group(supplier):
	"""The supplier's Tax Withholding Group, else its Supplier Type (Company,
	Individual...) when a group of that name exists. TDS rates differ by the
	deductee's type, and every supplier records that type, so suppliers never
	set up for TDS still get the right rate row."""
	if not supplier:
		return None
	group, supplier_type = frappe.get_cached_value(
		"Supplier", supplier, ["tax_withholding_group", "supplier_type"]
	) or (None, None)
	if group:
		return group
	if supplier_type and frappe.db.exists("Tax Withholding Group", supplier_type):
		return supplier_type
	return None


def line_tds_sections(doc):
	"""TDS sections the invoice's lines fall under. On save this runs before
	ERPNext fills each line's section during validate, so a missing one is
	resolved the way get_item_details does: the item's, else the supplier's."""
	supplier_section = (
		frappe.get_cached_value("Supplier", doc.supplier, "tax_withholding_category") if doc.supplier else None
	)
	sections = set()
	for row in doc.items:
		if not row.item_code or row.apply_tds == 0:
			continue
		section = (
			row.tax_withholding_category
			or frappe.get_cached_value("Item", row.item_code, "purchase_tax_withholding_category")
			or supplier_section
		)
		if section:
			sections.add(section)
	return sections


def invoice_needs_tds(doc):
	return bool(supplier_has_tds(doc.supplier) or line_tds_sections(doc))


def apply_tds_from_supplier(doc, method=None):
	"""`doc_events` entry point for Purchase Invoice before_validate.

	A new invoice gets `apply_tds` ticked when the supplier has TDS set up or
	any line's item carries a TDS section, and a blank Tax Withholding Group
	falls back to the supplier's type. `apply_tds` is only set on new
	documents, so a desk user who unticks it on a draft keeps their choice.
	Returns are left alone, as in ERPNext.
	"""
	if doc.get("is_return"):
		return

	if not doc.get("tax_withholding_group"):
		doc.tax_withholding_group = supplier_tds_group(doc.supplier)

	if doc.is_new() and not doc.apply_tds and invoice_needs_tds(doc):
		doc.apply_tds = 1


def validate_tds_rates(doc, method=None):
	"""`doc_events` entry point for Purchase Invoice before_validate.

	ERPNext picks a TDS section's rate row by the supplier's Tax Withholding
	Group, and when none matches it throws "No Tax Withholding data found for
	the current posting date", which names neither the section nor the cause.
	This checks the same thing first and says what to fix.
	"""
	if not doc.apply_tds or doc.get("is_opening") == "Yes":
		return

	group = doc.get("tax_withholding_group") or supplier_tds_group(doc.supplier)
	posting_date = frappe.utils.getdate(doc.posting_date or frappe.utils.today())
	sections = line_tds_sections(doc)

	missing = []
	for section in sorted(sections):
		category = frappe.get_cached_doc("Tax Withholding Category", section)
		if not any(
			frappe.utils.getdate(rate.from_date) <= posting_date <= frappe.utils.getdate(rate.to_date)
			and (rate.tax_withholding_group or "") == (group or "")
			for rate in category.rates
		):
			missing.append(section)

	if missing:
		frappe.throw(
			_(
				"TDS section {0} has no rate for supplier type {1} on {2}. Set the Tax Withholding "
				"Group on supplier {3}, or add a rate row for it in the section."
			).format(
				", ".join(frappe.bold(s) for s in missing),
				frappe.bold(group or _("(not set)")),
				frappe.utils.formatdate(posting_date),
				frappe.bold(doc.supplier),
			),
			title=_("Missing TDS Rate"),
		)


@frappe.whitelist()
def preview_purchase_invoice(doc, refresh_party=0):
	"""Dry run of a Purchase Invoice's taxes and totals. Never saves.

	`doc` is the invoice as the form holds it. Item rows whose item changed
	should be sent with `item_tax_template`, `item_tax_rate` and
	`tax_withholding_category` cleared, so the new item's values are fetched.
	Pass `refresh_party=1` after a change of supplier: the supplier-derived
	header fields and the template-derived tax rows are dropped and resolved
	again for the new supplier.

	Returns the computed document (items, taxes, tax_withholding_entries and
	totals). TDS rows are always recomputed, so any sent by the client are
	ignored.
	"""
	if isinstance(doc, str):
		doc = json.loads(doc)

	doc = frappe._dict(doc)
	doc.doctype = "Purchase Invoice"

	is_saved = bool(doc.get("name") and frappe.db.exists("Purchase Invoice", doc.name))
	ptype = "write" if is_saved else "create"
	if not frappe.has_permission("Purchase Invoice", ptype):
		frappe.throw(_("Not permitted to {0} Purchase Invoice").format(ptype), frappe.PermissionError)

	taxes = [row for row in (doc.get("taxes") or []) if not row.get("is_tax_withholding_account")]
	refresh_party = frappe.utils.cint(refresh_party)
	if refresh_party:
		# Rows from the old supplier's GST template go; rows added by hand stay.
		old_accounts = template_accounts(doc.get("taxes_and_charges"))
		taxes = [row for row in taxes if row.get("account_head") not in old_accounts]
		for field in PARTY_TAX_FIELDS:
			doc[field] = None

	doc.taxes = taxes
	doc.tax_withholding_entries = []

	pi = frappe.get_doc(doc)
	# TDS entries are matched to the invoice by name, which insert() assigns
	# before validate. An unsaved preview needs a stand-in.
	if not is_saved:
		pi.name = PREVIEW_NAME
		pi.set("__islocal", 1)

	# insert() fills field defaults (e.g. each item row's apply_tds = 1) before
	# validate; do the same for unsaved rows.
	for df in pi.meta.get_table_fields():
		for row in pi.get(df.fieldname) or []:
			if not row.name or not is_saved:
				row.set("__islocal", 1)
	pi._set_defaults()

	# The app has no Apply TDS control: TDS follows the supplier and the lines'
	# sections, re-decided on every preview (saved drafts included).
	if not pi.get("is_return"):
		if not pi.get("tax_withholding_group"):
			pi.tax_withholding_group = supplier_tds_group(pi.supplier)
		pi.apply_tds = 1 if invoice_needs_tds(pi) else 0

	# Same order as PurchaseInvoice.validate -> AccountsController.validate.
	pi.set_missing_values(for_validate=True)
	pi.set_taxes_and_charges()
	validate_item_tax_templates(pi)
	pi.calculate_taxes_and_totals()
	validate_tds_rates(pi)
	PurchaseTaxWithholding(pi).on_validate()

	result = pi.as_dict(convert_dates_to_str=True)
	for row, item in zip(result.get("items") or [], pi.items, strict=False):
		row["allowed_item_tax_templates"] = allowed_item_tax_templates(pi, item)
	# The markers set above must not travel back to the client and into a save.
	result.pop("__islocal", None)
	for df in pi.meta.get_table_fields():
		for row in result.get(df.fieldname) or []:
			row.pop("__islocal", None)
	if not is_saved:
		result["name"] = None
		for entry in result.get("tax_withholding_entries") or []:
			for field in ("withholding_name", "taxable_name"):
				if entry.get(field) == PREVIEW_NAME:
					entry[field] = None
	return result


def allowed_item_tax_templates(doc, item):
	"""GST slabs a line may use. ERPNext's calculate_taxes_and_totals resets a
	line's item_tax_template to the item's first template unless it is one of
	the templates listed on the item (or its item groups) for the invoice's tax
	category and dates; this returns that same list so the form only offers
	slabs that will stick. When the item lists none, ERPNext accepts any
	template, so the slabs offered are those with a rate on at least one of the
	invoice's GST accounts (a template on other accounts would silently fall
	back to the tax rows' default rates). Empty means no restriction."""
	if not item.item_code:
		return []
	from erpnext.stock.get_item_details import ItemDetailsCtx, _get_item_tax_template

	item_doc = frappe.get_cached_doc("Item", item.item_code)
	taxes = list(item_doc.taxes or [])
	item_group = item_doc.item_group
	while item_group:
		group_doc = frappe.get_cached_doc("Item Group", item_group)
		taxes += group_doc.taxes or []
		item_group = group_doc.parent_item_group
	if not taxes:
		return templates_matching_invoice(doc)

	ctx = ItemDetailsCtx(
		{
			"net_rate": item.net_rate or item.rate,
			"base_net_rate": item.base_net_rate or item.base_rate,
			"tax_category": doc.get("tax_category"),
			"posting_date": doc.get("posting_date"),
			"bill_date": doc.get("bill_date"),
			"company": doc.get("company"),
		}
	)
	return _get_item_tax_template(ctx, taxes, for_validate=True) or []


def invoice_gst_accounts(doc):
	"""Accounts of the invoice's own tax rows (GST and manual charges), not TDS."""
	return {
		row.account_head
		for row in doc.get("taxes") or []
		if row.account_head and not row.get("is_tax_withholding_account")
	}


def item_tax_template_accounts(template):
	return set(
		frappe.get_all(
			"Item Tax Template Detail",
			filters={"parent": template, "parenttype": "Item Tax Template"},
			pluck="tax_type",
		)
	)


def templates_matching_invoice(doc):
	accounts = invoice_gst_accounts(doc)
	if not accounts:
		return []
	return sorted(
		set(
			frappe.get_all(
				"Item Tax Template Detail",
				filters={"parenttype": "Item Tax Template", "tax_type": ["in", list(accounts)]},
				pluck="parent",
			)
		)
		& set(frappe.get_all("Item Tax Template", filters={"company": doc.company, "disabled": 0}, pluck="name"))
	)


def validate_item_tax_templates(doc, method=None):
	"""`doc_events` entry point for Purchase Invoice before_validate.

	A line's GST slab only applies through accounts the invoice has tax rows
	for; a slab on other accounts (e.g. an old template posting to an expense
	account) is silently replaced by the tax rows' default rates. Refuse it
	instead, naming the line. Skipped while the invoice has no tax rows yet.
	"""
	accounts = invoice_gst_accounts(doc)
	if not accounts:
		return
	for row in doc.items:
		if row.item_tax_template and not (item_tax_template_accounts(row.item_tax_template) & accounts):
			frappe.throw(
				_(
					"Row {0}: GST slab {1} has no rate for this invoice's tax accounts ({2}). "
					"Choose a slab that uses them."
				).format(row.idx, frappe.bold(row.item_tax_template), ", ".join(sorted(accounts))),
				title=_("GST Slab Does Not Match"),
			)


def template_accounts(template):
	if not template:
		return set()
	return set(
		frappe.get_all(
			"Purchase Taxes and Charges",
			filters={"parent": template, "parenttype": "Purchase Taxes and Charges Template"},
			pluck="account_head",
		)
	)
