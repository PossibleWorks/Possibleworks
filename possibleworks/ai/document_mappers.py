# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Document mapper registry.

Each DocType that supports AI scanning has a corresponding Mapper class.
The mapper is responsible for:
  1. Providing the extraction prompt describing the JSON schema the AI should return
  2. Receiving the raw AI JSON and resolving master data (Supplier / Customer / Item)
     against the ERPNext database using multi-tier matching (no duplicates)
  3. Returning a form-ready dict the client JS can consume directly

To add a new DocType: create a subclass, add it to MAPPER_REGISTRY.
Zero if/else chains anywhere.
"""

import re

import frappe
from frappe import _


# ──────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────

def get_mapper(doctype: str) -> "DocumentMapper":
	"""Return the mapper instance for the given doctype."""
	cls = MAPPER_REGISTRY.get(doctype)
	if not cls:
		frappe.throw(
			_("AI scanning is not configured for DocType: {0}").format(doctype),
			title=_("Unsupported DocType"),
		)
	return cls()


# ──────────────────────────────────────────────────────────────────
# Base Mapper
# ──────────────────────────────────────────────────────────────────

_BASE_RULES = """
RULES:
- Dates must be in YYYY-MM-DD format
- All monetary amounts must be numbers (not strings), rounded to 2 decimal places
- `rate` is the UNIT PRICE per single item (often labeled Rate or Price on the invoice).
- `amount` is the LINE TOTAL for that row (amount = qty × rate).
- If a field is not found on the document, use null
- If you cannot see a tax amount clearly printed in the document, do not include it. Return an empty array `[]` for taxes. Never guess or assume a tax rate.
- HSN/SAC codes are 4-8 digit Indian tax classification codes
- Tax types: CGST, SGST, IGST, CESS, VAT, TDS, TCS etc.
- UOM: "Nos", "Kg", "Ltr", "Mtr", "Box", "Set", "Pair", "Pcs", etc.
- Return ONLY valid JSON — no markdown, no explanation
"""


class DocumentMapper:
	"""Base class for all DocType mappers."""

	#: Human-readable party label, used in messages
	party_label = "Party"

	def get_default_prompt(self) -> str:
		raise NotImplementedError

	def build_prompt(self, custom_prompt: str | None = None, global_hint: str | None = None) -> str:
		base = custom_prompt.strip() if custom_prompt and custom_prompt.strip() else self.get_default_prompt()
		if global_hint and global_hint.strip():
			base += f"\n\nADDITIONAL INSTRUCTIONS:\n{global_hint.strip()}"
		return base

	def resolve_and_return(self, raw: dict, settings) -> dict:
		"""Post-process AI JSON: resolve master data, return clean dict."""
		raise NotImplementedError


# ──────────────────────────────────────────────────────────────────
# Shared prompt JSON schema blocks
# ──────────────────────────────────────────────────────────────────

_ITEMS_SCHEMA = """  "items": [
    {
      "item_name": "Product/service name",
      "description": "Detailed description if any",
      "qty": 1.0,
      "uom": "Nos",
      "rate": 100.00,  // The unit price for ONE item
      "amount": 100.00,  // The line total: qty * rate
      "hsn_code": "HSN/SAC code or null",
      "discount_percentage": 0,
      "discount_amount": 0
    }
  ],"""

_TAXES_SCHEMA = """  "taxes": [
    { "tax_type": "CGST", "rate": 9.0, "amount": 9.00 }
  ], // Or an empty array [] if no taxes are printed on the document"""

_TOTALS_SCHEMA = """  "total": 100.00,
  "total_taxes": 18.00,
  "grand_total": 118.00,
  "discount_amount": 0,"""


# ──────────────────────────────────────────────────────────────────
# Purchase Invoice Mapper
# ──────────────────────────────────────────────────────────────────

class PurchaseInvoiceMapper(DocumentMapper):
	party_label = "Supplier"

	def get_default_prompt(self) -> str:
		return f"""You are an expert accountant extracting data from a supplier Purchase Invoice.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "supplier_name": "Full supplier/vendor name as printed",
  "supplier_gstin": "GSTIN (15 chars) or null",
  "supplier_address": "Full address or null",
  "bill_no": "Invoice/bill number",
  "bill_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TAXES_SCHEMA}
{_TOTALS_SCHEMA}
  "payment_terms": "Net 30 or null",
  "notes": "Any remarks on the invoice"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_supplier"] = _resolve_supplier(
			raw.get("supplier_name"),
			raw.get("supplier_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		raw["taxes"] = _clean_taxes(raw.get("taxes"))
		return raw


# ──────────────────────────────────────────────────────────────────
# Sales Invoice Mapper
# ──────────────────────────────────────────────────────────────────

class SalesInvoiceMapper(DocumentMapper):
	party_label = "Customer"

	def get_default_prompt(self) -> str:
		return f"""You are an expert accountant extracting data from a Sales Invoice (customer invoice).

{_BASE_RULES}

Extract into this exact JSON:
{{
  "customer_name": "Full customer name as printed",
  "customer_gstin": "Customer GSTIN (15 chars) or null",
  "bill_no": "Invoice number (our reference)",
  "posting_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TAXES_SCHEMA}
{_TOTALS_SCHEMA}
  "payment_terms": "Net 30 or null",
  "notes": "Any remarks"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_customer"] = _resolve_customer(
			raw.get("customer_name"),
			raw.get("customer_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		raw["taxes"] = _clean_taxes(raw.get("taxes"))
		return raw


# ──────────────────────────────────────────────────────────────────
# Purchase Order Mapper
# ──────────────────────────────────────────────────────────────────

class PurchaseOrderMapper(DocumentMapper):
	party_label = "Supplier"

	def get_default_prompt(self) -> str:
		return f"""You are an expert buyer extracting data from a Purchase Order document.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "supplier_name": "Supplier name",
  "supplier_gstin": "Supplier GSTIN or null",
  "transaction_date": "PO date YYYY-MM-DD",
  "schedule_date": "Required delivery date YYYY-MM-DD or null",
  "currency": "INR",
  "po_no": "PO reference number or null",
{_ITEMS_SCHEMA}
{_TAXES_SCHEMA}
{_TOTALS_SCHEMA}
  "payment_terms": "Payment terms or null",
  "notes": "Any terms, remarks or instructions"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_supplier"] = _resolve_supplier(
			raw.get("supplier_name"),
			raw.get("supplier_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		raw["taxes"] = _clean_taxes(raw.get("taxes"))
		return raw


# ──────────────────────────────────────────────────────────────────
# Sales Order Mapper
# ──────────────────────────────────────────────────────────────────

class SalesOrderMapper(DocumentMapper):
	party_label = "Customer"

	def get_default_prompt(self) -> str:
		return f"""You are an expert sales executive extracting data from a Sales Order or customer Purchase Order.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "customer_name": "Customer name",
  "customer_gstin": "Customer GSTIN or null",
  "transaction_date": "Order date YYYY-MM-DD",
  "delivery_date": "Requested delivery date YYYY-MM-DD or null",
  "po_no": "Customer's PO number or null",
  "po_date": "Customer's PO date YYYY-MM-DD or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TAXES_SCHEMA}
{_TOTALS_SCHEMA}
  "payment_terms": "Payment terms or null",
  "notes": "Any special instructions or remarks"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_customer"] = _resolve_customer(
			raw.get("customer_name"),
			raw.get("customer_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		raw["taxes"] = _clean_taxes(raw.get("taxes"))
		return raw


# ──────────────────────────────────────────────────────────────────
# Payment Entry Mapper
# ──────────────────────────────────────────────────────────────────

class PaymentEntryMapper(DocumentMapper):
	"""
	Payment Entry is structurally different from the transactional DocTypes:
	- No items table
	- party_type is generic (Supplier / Customer / Employee)
	- Uploaded document is typically a remittance slip, payment advice, cheque image, or receipt

	What we extract:
	  - party_type: inferred from context ("Supplier" if it's a payment advice/remittance, "Customer" if a receipt)
	  - party: the name of the paying/receiving party
	  - payment_type: "Pay" if paying a supplier, "Receive" if receiving from customer
	  - paid_amount: the total amount
	  - reference_no: cheque number / UTR / NEFT / IMPS reference
	  - reference_date: date of the cheque / bank transfer
	  - mode_of_payment: Cheque / Bank Transfer / Cash / NEFT / RTGS / UPI
	  - posting_date: date on the document
	  - remarks: any narration / description
	"""
	party_label = "Party"

	def get_default_prompt(self) -> str:
		return f"""You are an expert accountant extracting data from a payment document.
This could be a remittance slip, payment advice, cheque image, bank receipt, or NEFT/RTGS/UPI confirmation.

{_BASE_RULES}

Determine:
- If this is a PAYMENT MADE to a supplier/vendor → payment_type = "Pay", party_type = "Supplier"
- If this is a RECEIPT RECEIVED from a customer → payment_type = "Receive", party_type = "Customer"
- If unclear, use "Pay" and "Supplier" as defaults

Extract into this exact JSON:
{{
  "payment_type": "Pay or Receive",
  "party_type": "Supplier or Customer",
  "party_name": "Full name of the paying/receiving party",
  "party_gstin": "GSTIN of the party (15 chars) or null",
  "posting_date": "YYYY-MM-DD",
  "paid_amount": 10000.00,
  "received_amount": 10000.00,
  "reference_no": "Cheque no / UTR / NEFT reference / UPI transaction ID or null",
  "reference_date": "YYYY-MM-DD date on cheque or transfer or null",
  "mode_of_payment": "Bank Transfer or Cheque or NEFT or RTGS or UPI or Cash or null",
  "bank_name": "Bank name if visible or null",
  "bank_account_no": "Account number if visible or null",
  "remarks": "Narration, description, purpose of payment or null"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		party_type = raw.get("party_type", "Supplier")

		if party_type == "Supplier":
			resolved = _resolve_supplier(
				raw.get("party_name"),
				raw.get("party_gstin"),
				auto_create=settings.auto_create_master_data,
			)
			raw["_party"] = resolved
			raw["_party_type"] = "Supplier"
			raw["_party_name"] = resolved.get("supplier_name") or raw.get("party_name")
		else:
			resolved = _resolve_customer(
				raw.get("party_name"),
				raw.get("party_gstin"),
				auto_create=settings.auto_create_master_data,
			)
			raw["_party"] = resolved
			raw["_party_type"] = "Customer"
			raw["_party_name"] = resolved.get("customer_name") or raw.get("party_name")

		return raw


# ──────────────────────────────────────────────────────────────────
# Quotation Mapper
# ──────────────────────────────────────────────────────────────────

class QuotationMapper(DocumentMapper):
	party_label = "Customer / Lead"

	def get_default_prompt(self) -> str:
		return f"""You are extracting data from a Quotation or Price Quote document.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "quotation_to": "Customer or Lead (use Customer if a registered company, Lead if an individual/prospect)",
  "party_name": "Name of the customer or lead",
  "transaction_date": "Quote date YYYY-MM-DD",
  "valid_till": "Quote validity date YYYY-MM-DD or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TAXES_SCHEMA}
{_TOTALS_SCHEMA}
  "payment_terms": "Payment terms or null",
  "notes": "Any terms, conditions or remarks"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		# Quotation can target Customer or Lead — just resolve Customer for now
		raw["_customer"] = _resolve_customer(
			raw.get("party_name"),
			None,
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		raw["taxes"] = _clean_taxes(raw.get("taxes"))
		return raw


# ──────────────────────────────────────────────────────────────────
# Delivery Note Mapper
# ──────────────────────────────────────────────────────────────────

class DeliveryNoteMapper(DocumentMapper):
	party_label = "Customer"

	def get_default_prompt(self) -> str:
		return f"""You are extracting data from a Delivery Note, packing slip, or delivery challan.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "customer_name": "Customer name",
  "customer_gstin": "Customer GSTIN or null",
  "posting_date": "Delivery date YYYY-MM-DD",
  "lr_no": "LR (Lorry Receipt) number or transport document number or null",
  "lr_date": "LR date YYYY-MM-DD or null",
  "transporter_name": "Transporter / courier name or null",
  "vehicle_no": "Vehicle number or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TOTALS_SCHEMA}
  "notes": "Any remarks, dispatch instructions"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_customer"] = _resolve_customer(
			raw.get("customer_name"),
			raw.get("customer_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		return raw


# ──────────────────────────────────────────────────────────────────
# Purchase Receipt Mapper
# ──────────────────────────────────────────────────────────────────

class PurchaseReceiptMapper(DocumentMapper):
	party_label = "Supplier"

	def get_default_prompt(self) -> str:
		return f"""You are extracting data from a Purchase Receipt, Goods Receipt Note (GRN), or delivery receipt from a supplier.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "supplier_name": "Supplier name",
  "supplier_gstin": "Supplier GSTIN or null",
  "posting_date": "Receipt date YYYY-MM-DD",
  "supplier_delivery_note": "Supplier's delivery challan / DC number or null",
  "lr_no": "LR number if visible or null",
  "lr_date": "LR date YYYY-MM-DD or null",
  "currency": "INR",
{_ITEMS_SCHEMA}
{_TOTALS_SCHEMA}
  "notes": "Any remarks"
}}"""

	def resolve_and_return(self, raw: dict, settings) -> dict:
		raw["_supplier"] = _resolve_supplier(
			raw.get("supplier_name"),
			raw.get("supplier_gstin"),
			auto_create=settings.auto_create_master_data,
		)
		raw["items"] = [_resolve_item_row(item, settings) for item in raw.get("items") or []]
		return raw


# ──────────────────────────────────────────────────────────────────
# Mapper Registry
# ──────────────────────────────────────────────────────────────────

MAPPER_REGISTRY: dict[str, type[DocumentMapper]] = {
	"Purchase Invoice": PurchaseInvoiceMapper,
	"Sales Invoice":    SalesInvoiceMapper,
	"Purchase Order":   PurchaseOrderMapper,
	"Sales Order":      SalesOrderMapper,
	"Payment Entry":    PaymentEntryMapper,
	"Quotation":        QuotationMapper,
	"Delivery Note":    DeliveryNoteMapper,
	"Purchase Receipt": PurchaseReceiptMapper,
}


# ──────────────────────────────────────────────────────────────────
# Shared master-data resolution helpers
# ──────────────────────────────────────────────────────────────────

_STOP_WORDS = {
	"pvt", "ltd", "limited", "private", "inc", "llc", "llp", "co",
	"and", "the", "of", "for", "in", "on", "at", "to", "by",
	"m/s", "ms", "mr", "mrs", "dr", "shri", "smt",
	"enterprise", "enterprises", "trading", "traders", "industries",
	"company", "corporation", "solutions", "services", "technologies",
}


def _clean_taxes(taxes: list | None) -> list[dict]:
	"""Filter out completely empty/zero tax rows to prevent hallucination noise."""
	if not taxes or not isinstance(taxes, list):
		return []
	clean = []
	for t in taxes:
		try:
			amt = float(t.get("amount") or 0)
			if amt > 0:
				clean.append(t)
		except (ValueError, TypeError):
			continue
	return clean


def _extract_keywords(name: str) -> list[str]:
	if not name:
		return []
	clean = re.sub(r"[^\w\s]", " ", name)
	return [w for w in clean.split() if w.lower() not in _STOP_WORDS and len(w) >= 2]


def _names_similar(name1: str, name2: str) -> bool:
	kw1 = set(w.lower() for w in _extract_keywords(name1))
	kw2 = set(w.lower() for w in _extract_keywords(name2))
	if not kw1 or not kw2:
		return False
	return len(kw1 & kw2) >= min(len(kw1), len(kw2)) * 0.5


def _pick_best_match(target: str, candidates: list[dict], name_field: str) -> dict:
	target_kw = set(w.lower() for w in _extract_keywords(target))
	best, best_score = candidates[0], 0
	for c in candidates:
		overlap = len(target_kw & set(w.lower() for w in _extract_keywords(c.get(name_field, ""))))
		if overlap > best_score:
			best_score = overlap
			best = c
	return best


def _resolve_supplier(supplier_name, gstin, auto_create=False) -> dict:
	"""Multi-tier supplier resolution: GSTIN → exact → fuzzy → auto-create."""
	if not supplier_name:
		return {"supplier": None, "match_type": "not_found", "candidates": [], "message": "No supplier name found on document"}

	# Tier 1: GSTIN
	if gstin and len(str(gstin)) == 15:
		m = frappe.db.get_value("Supplier", {"tax_id": gstin}, ["name", "supplier_name"], as_dict=True)
		if m:
			return {"supplier": m.name, "supplier_name": m.supplier_name, "match_type": "gstin", "candidates": [], "message": f"Matched by GSTIN: {m.supplier_name}"}

	# Tier 2: Exact name
	m = frappe.db.get_value("Supplier", {"supplier_name": ("like", supplier_name)}, ["name", "supplier_name"], as_dict=True)
	if m:
		return {"supplier": m.name, "supplier_name": m.supplier_name, "match_type": "exact", "candidates": [], "message": f"Exact match: {m.supplier_name}"}

	# Tier 3: Fuzzy keyword
	candidates = []
	for kw in _extract_keywords(supplier_name):
		if len(kw) < 3:
			continue
		for m in frappe.db.get_all("Supplier", filters={"supplier_name": ("like", f"%{kw}%")}, fields=["name", "supplier_name", "tax_id"], limit=10):
			if m.name not in [c["name"] for c in candidates]:
				candidates.append(m)

	if len(candidates) == 1:
		c = candidates[0]
		return {"supplier": c.name, "supplier_name": c.supplier_name, "match_type": "fuzzy", "candidates": [], "message": f"Fuzzy match: {c.supplier_name}"}
	if candidates:
		best = _pick_best_match(supplier_name, candidates, "supplier_name")
		return {"supplier": best["name"], "supplier_name": best["supplier_name"], "match_type": "fuzzy_multiple",
				"candidates": [{"name": c["name"], "supplier_name": c["supplier_name"]} for c in candidates[:5]],
				"message": "Multiple possible matches. Please verify."}

	# Tier 4: Auto-create or flag
	if auto_create:
		name = _create_supplier(supplier_name, gstin)
		return {"supplier": name, "supplier_name": supplier_name, "match_type": "created", "candidates": [], "message": f"Created: {supplier_name}"}
	return {"supplier": None, "supplier_name": supplier_name, "match_type": "not_found", "candidates": [], "message": f"No supplier found for '{supplier_name}'. Select manually."}


def _resolve_customer(customer_name, gstin, auto_create=False) -> dict:
	"""Multi-tier customer resolution: GSTIN → exact → fuzzy → auto-create."""
	if not customer_name:
		return {"customer": None, "match_type": "not_found", "candidates": [], "message": "No customer name found"}

	# Tier 1: GSTIN
	if gstin and len(str(gstin)) == 15:
		m = frappe.db.get_value("Customer", {"tax_id": gstin}, ["name", "customer_name"], as_dict=True)
		if m:
			return {"customer": m.name, "customer_name": m.customer_name, "match_type": "gstin", "candidates": [], "message": f"Matched by GSTIN: {m.customer_name}"}

	# Tier 2: Exact name
	m = frappe.db.get_value("Customer", {"customer_name": ("like", customer_name)}, ["name", "customer_name"], as_dict=True)
	if m:
		return {"customer": m.name, "customer_name": m.customer_name, "match_type": "exact", "candidates": [], "message": f"Exact match: {m.customer_name}"}

	# Tier 3: Fuzzy
	candidates = []
	for kw in _extract_keywords(customer_name):
		if len(kw) < 3:
			continue
		for m in frappe.db.get_all("Customer", filters={"customer_name": ("like", f"%{kw}%")}, fields=["name", "customer_name"], limit=10):
			if m.name not in [c["name"] for c in candidates]:
				candidates.append(m)

	if len(candidates) == 1:
		c = candidates[0]
		return {"customer": c.name, "customer_name": c.customer_name, "match_type": "fuzzy", "candidates": [], "message": f"Fuzzy match: {c.customer_name}"}
	if candidates:
		best = _pick_best_match(customer_name, candidates, "customer_name")
		return {"customer": best["name"], "customer_name": best["customer_name"], "match_type": "fuzzy_multiple",
				"candidates": [{"name": c["name"], "customer_name": c["customer_name"]} for c in candidates[:5]],
				"message": "Multiple possible matches. Please verify."}

	# Tier 4: Auto-create or flag
	if auto_create:
		name = _create_customer(customer_name, gstin)
		return {"customer": name, "customer_name": customer_name, "match_type": "created", "candidates": [], "message": f"Created: {customer_name}"}
	return {"customer": None, "customer_name": customer_name, "match_type": "not_found", "candidates": [], "message": f"No customer found for '{customer_name}'. Select manually."}


def _resolve_item_row(item: dict, settings) -> dict:
	"""Resolve a single item dict against Item master."""
	item_name = item.get("item_name")
	hsn_code = item.get("hsn_code")
	uom = item.get("uom", "Nos")

	if not item_name:
		item["_resolved"] = {"item_code": None, "match_type": "not_found", "candidates": [], "message": "No item name"}
		return item

	# Tier 1: Exact name
	m = frappe.db.get_value("Item", {"item_name": ("like", item_name)}, ["name", "item_name", "stock_uom"], as_dict=True)
	if m:
		item["_resolved"] = {"item_code": m.name, "item_name": m.item_name, "uom": m.stock_uom or uom, "match_type": "exact", "candidates": [], "message": f"Exact: {m.item_name}"}
		return item

	# Tier 2: HSN
	if hsn_code:
		matches = frappe.db.get_all("Item", filters={"gst_hsn_code": hsn_code}, fields=["name", "item_name", "stock_uom"], limit=5)
		if len(matches) == 1:
			m = matches[0]
			item["_resolved"] = {"item_code": m.name, "item_name": m.item_name, "uom": m.stock_uom or uom, "match_type": "hsn", "candidates": [], "message": f"HSN match: {m.item_name}"}
			return item
		if matches:
			for m in matches:
				if _names_similar(item_name, m.item_name):
					item["_resolved"] = {"item_code": m.name, "item_name": m.item_name, "uom": m.stock_uom or uom, "match_type": "hsn_name", "candidates": [], "message": f"HSN+name: {m.item_name}"}
					return item

	# Tier 3: Fuzzy
	candidates = []
	for kw in _extract_keywords(item_name):
		if len(kw) < 3:
			continue
		for m in frappe.db.get_all("Item", filters={"item_name": ("like", f"%{kw}%")}, fields=["name", "item_name", "stock_uom"], limit=10):
			if m.name not in [c["name"] for c in candidates]:
				candidates.append(m)

	if len(candidates) == 1:
		c = candidates[0]
		item["_resolved"] = {"item_code": c["name"], "item_name": c["item_name"], "uom": c.get("stock_uom") or uom, "match_type": "fuzzy", "candidates": [], "message": f"Fuzzy: {c['item_name']}"}
		return item
	if candidates:
		best = _pick_best_match(item_name, candidates, "item_name")
		item["_resolved"] = {"item_code": best["name"], "item_name": best["item_name"], "uom": best.get("stock_uom") or uom,
							 "match_type": "fuzzy_best",
							 "candidates": [{"name": c["name"], "item_name": c["item_name"]} for c in candidates[:5]],
							 "message": f"Best fuzzy: {best['item_name']}. Alternatives available."}
		return item

	# Tier 4: Auto-create or flag
	if settings.auto_create_master_data:
		code = _create_item(item_name, hsn_code, uom)
		item["_resolved"] = {"item_code": code, "item_name": item_name, "uom": uom, "match_type": "created", "candidates": [], "message": f"Created: {item_name}"}
	else:
		item["_resolved"] = {"item_code": None, "item_name": item_name, "uom": uom, "match_type": "not_found", "candidates": [], "message": f"No item for '{item_name}'. Link manually."}

	return item


# ──────────────────────────────────────────────────────────────────
# Auto-creation helpers (last resort)
# ──────────────────────────────────────────────────────────────────

def _create_supplier(supplier_name: str, gstin=None) -> str:
	grp = frappe.db.get_single_value("Buying Settings", "supplier_group") or \
		frappe.db.get_value("Supplier Group", {"is_group": 0}, "name") or "All Supplier Groups"
	doc = frappe.get_doc({"doctype": "Supplier", "supplier_name": supplier_name, "supplier_group": grp, "supplier_type": "Company", "tax_id": gstin or ""})
	doc.flags.ignore_permissions = doc.flags.ignore_mandatory = True
	doc.insert()
	frappe.db.commit()
	return doc.name


def _create_customer(customer_name: str, gstin=None) -> str:
	grp = frappe.db.get_single_value("Selling Settings", "customer_group") or \
		frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or "All Customer Groups"
	doc = frappe.get_doc({"doctype": "Customer", "customer_name": customer_name, "customer_group": grp, "customer_type": "Company", "tax_id": gstin or ""})
	doc.flags.ignore_permissions = doc.flags.ignore_mandatory = True
	doc.insert()
	frappe.db.commit()
	return doc.name


def _create_item(item_name: str, hsn_code=None, uom="Nos") -> str:
	if uom and not frappe.db.exists("UOM", uom):
		uom = "Nos"
	grp = frappe.db.get_single_value("Stock Settings", "item_group") or \
		frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	doc = frappe.get_doc({"doctype": "Item", "item_name": item_name, "item_code": item_name, "item_group": grp, "stock_uom": uom, "is_stock_item": 1, "gst_hsn_code": hsn_code or ""})
	doc.flags.ignore_permissions = doc.flags.ignore_mandatory = True
	doc.insert()
	frappe.db.commit()
	return doc.name
