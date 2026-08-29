import hashlib
import json

import frappe
from frappe.utils import add_days, get_datetime, get_url, now_datetime

from erpnext.accounts.party import get_party_account_currency


# =============================================================================
# RFQ -> Supplier Quotation guest portal
#
# Lets a supplier submit a quotation against an RFQ with no Frappe login at
# all, via a per-supplier, expiring "magic link" (same trick as a password
# reset email - the secret token IN the link is itself the credential).
#
# These methods are plain @frappe.whitelist() (NOT allow_guest) on purpose:
# the anonymous supplier's browser never talks to Frappe directly. Only
# pw-server-v3's public broker route calls these, authenticated with the
# tenant's own admin Frappe API key - the token is the per-supplier secret,
# the API key is a second, independent layer on top of it.
# =============================================================================

def _get_guest_quotation_portal_base_url():
	"""Reads the deployed frontend's origin from Possibleworks Settings (just
	the protocol + host, e.g. https://app.possibleworks.com - no path) instead
	of a hardcoded value, so this works the same in dev, staging, and every
	tenant's production deployment. Appends the frontend's fixed
	/supplier-quote route ourselves, so whoever fills in the setting only
	ever has to paste the bare origin."""
	origin = frappe.db.get_single_value(
		"Possibleworks Settings", "guest_quotation_portal_base_url"
	)
	origin = (origin or "").rstrip("/")
	if not origin:
		return ""
	return f"{origin}/supplier-quote"


def _resolve_tenant_uuid(company):
	"""pw-server-v3 stamps this on each Frappe Company when the org connects
	(same field frappeDoctypeOperations.ts already matches on, in reverse)."""
	return frappe.get_cached_value("Company", company, "custom_tenant_id")


def _hash_token(raw_token):
	return hashlib.sha256(raw_token.encode()).hexdigest()


def generate_and_store_token(rfq_supplier, validity_days):
	"""Mints a fresh guest-access token for one RFQ-supplier row, stores its
	hash + expiry on that row (never the raw token), and returns the raw
	token to embed in the email link."""
	raw_token = frappe.generate_hash(length=48)
	rfq_supplier.pw_quote_token_hash = _hash_token(raw_token)
	rfq_supplier.pw_quote_token_expires_on = add_days(now_datetime(), int(validity_days or 7))
	return raw_token


def build_guest_quotation_url(raw_token, company):
	tenant_uuid = _resolve_tenant_uuid(company)
	if not tenant_uuid:
		frappe.log_error(
			title="RFQ guest link: no custom_tenant_id on Company",
			message=f"Company {company} has no custom_tenant_id set - the guest link cannot be built.",
		)
		return None

	base_url = _get_guest_quotation_portal_base_url()
	if not base_url:
		frappe.log_error(
			title="RFQ guest link: Supplier Quotation Portal URL not configured",
			message=(
				"Possibleworks Settings has no Supplier Quotation Portal URL set - "
				"the guest link cannot be built. Set it under Possibleworks Settings "
				"> Supplier Portal."
			),
		)
		return None

	return f"{base_url}/{tenant_uuid}/{raw_token}"


def _find_supplier_row_by_token(raw_token):
	"""Looks up the Request for Quotation Supplier row this token belongs to.
	This table is small (one row per supplier per RFQ ever sent), so a
	filtered lookup on the hash is fine - no separate index needed."""
	token_hash = _hash_token(raw_token)
	return frappe.db.get_value(
		"Request for Quotation Supplier",
		{"pw_quote_token_hash": token_hash},
		[
			"name",
			"parent",
			"supplier",
			"supplier_name",
			"quote_status",
			"pw_quote_token_expires_on",
			"pw_quote_draft_items",
			"pw_quote_draft_terms",
		],
		as_dict=True,
	)


DEFAULT_NEW_ITEM_GROUP = "Products"


def _ensure_uom_exists(uom):
	"""uom is a Link field (on both Item.stock_uom and Supplier Quotation
	Item.uom) - a supplier typing an ad-hoc unit (e.g. "box", "crate") that
	isn't already a known UOM would otherwise fail link validation. Auto-create
	it, same rationale as _ensure_item_exists below."""
	uom = (uom or "").strip()
	if not uom or frappe.db.exists("UOM", uom):
		return uom

	frappe.get_doc({"doctype": "UOM", "uom_name": uom}).insert(ignore_permissions=True)
	return uom


def _ensure_item_exists(item_code, item_name, uom):
	"""Suppliers can quote on an item that doesn't exist in the buyer's item
	master at all ("full flexibility" per the guest form). item_code on
	Supplier Quotation Item is a Link to Item, so we auto-create a minimal
	Item record the first time a given item_code is used this way."""
	item_code = (item_code or "").strip()
	if not item_code or frappe.db.exists("Item", item_code):
		return

	item_group = DEFAULT_NEW_ITEM_GROUP
	if not frappe.db.exists("Item Group", item_group):
		item_group = frappe.db.get_value("Item Group", {"is_group": 0}) or frappe.db.get_value(
			"Item Group", {}
		)

	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_name or item_code,
			"item_group": item_group,
			"stock_uom": _ensure_uom_exists(uom) or "Nos",
			"is_purchase_item": 1,
		}
	)
	item.flags.ignore_permissions = True
	item.insert(ignore_permissions=True)


def _validate_token(raw_token):
	"""Returns (row, error_reason) - error_reason is None when the token is good."""
	if not raw_token:
		return None, "invalid"

	row = _find_supplier_row_by_token(raw_token)
	if not row:
		return None, "invalid"

	if row.quote_status == "Received":
		return row, "already_submitted"

	if row.pw_quote_token_expires_on and get_datetime(row.pw_quote_token_expires_on) < now_datetime():
		return row, "expired"

	return row, None


@frappe.whitelist()
def get_quotation_link_context(token):
	"""Read-only: what the guest form should render. Never throws - a bad/
	expired/reused token comes back as {"ok": False, "reason": ...}."""
	row, error = _validate_token(token)
	if error:
		return {"ok": False, "reason": error}

	rfq = frappe.get_doc("Request for Quotation", row.parent)

	# A saved draft (see save_quotation_draft below) takes priority over the
	# RFQ's original item list - it's already in the full editable shape
	# (rate, lead_time_days etc. included), so the guest form can use it as-is.
	if row.pw_quote_draft_items:
		items = json.loads(row.pw_quote_draft_items)
	else:
		items = [
			{
				"rfq_item_name": item.name,
				"item_code": item.item_code,
				"item_name": item.item_name,
				"description": item.description,
				"qty": item.qty,
				"uom": item.uom,
				"schedule_date": item.schedule_date,
			}
			for item in rfq.items
		]

	company_logo = frappe.get_cached_value("Company", rfq.company, "company_logo")

	return {
		"ok": True,
		"rfq": rfq.name,
		"company": rfq.company,
		"company_logo": get_url(company_logo) if company_logo else None,
		"supplier": row.supplier,
		"supplier_name": row.supplier_name,
		"schedule_date": rfq.schedule_date,
		"currency": frappe.get_cached_value("Company", rfq.company, "default_currency"),
		"items": items,
		"terms": row.pw_quote_draft_terms or "",
	}


@frappe.whitelist()
def save_quotation_draft(token, items, terms=None):
	"""Re-validates the token, then stores the in-progress form state
	(items + terms) on the same Request for Quotation Supplier row the token
	belongs to - so reopening the same guest link later (get_quotation_link_context
	above) comes back prefilled instead of blank. Never submits anything."""
	row, error = _validate_token(token)
	if error:
		return {"ok": False, "reason": error}

	if isinstance(items, str):
		items = json.loads(items)

	frappe.db.set_value(
		"Request for Quotation Supplier",
		row.name,
		{
			"pw_quote_draft_items": json.dumps(items or []),
			"pw_quote_draft_terms": terms or "",
		},
		update_modified=False,
	)
	return {"ok": True}


@frappe.whitelist()
def search_items(token, query=""):
	"""Guest-safe item search for the "Add Item" box - gated by a live token
	so it can't be scraped outside an active quotation session."""
	_, error = _validate_token(token)
	if error:
		return {"ok": False, "reason": error}

	filters = [["disabled", "=", 0], ["is_purchase_item", "=", 1]]
	or_filters = None
	if query:
		or_filters = [["item_code", "like", f"%{query}%"], ["item_name", "like", f"%{query}%"]]

	items = frappe.get_all(
		"Item",
		filters=filters,
		or_filters=or_filters,
		fields=["item_code", "item_name", "stock_uom"],
		limit_page_length=20,
	)
	return {"ok": True, "items": items}


@frappe.whitelist()
def submit_quotation(token, items, terms=None):
	"""Re-validates the token, then builds a Supplier Quotation using the same
	field-mapping shape as ERPNext's own create_supplier_quotation() (the
	function its native supplier portal already uses) - extended with
	lead_time_days, which that function doesn't carry over. Auto-submits
	immediately and flips quote_status to Received (single-use gate)."""
	row, error = _validate_token(token)
	if error:
		return {"ok": False, "reason": error}

	if isinstance(items, str):
		items = json.loads(items)

	rfq_supplier = frappe.get_doc("Request for Quotation Supplier", row.name)
	rfq = frappe.get_doc("Request for Quotation", row.parent)

	# Only trust RFQ item back-links the supplier could actually have been
	# shown - guards against a tampered rfq_item_name pointing elsewhere.
	valid_rfq_item_names = {i.name for i in rfq.items}

	try:
		sq_doc = frappe.get_doc(
			{
				"doctype": "Supplier Quotation",
				"supplier": row.supplier,
				"company": rfq.company,
				"terms": terms,
				"currency": get_party_account_currency("Supplier", row.supplier, rfq.company),
				"buying_price_list": frappe.db.get_single_value("Buying Settings", "buying_price_list"),
			}
		)

		for data in items:
			data = frappe._dict(data)
			is_rfq_item = data.get("rfq_item_name") in valid_rfq_item_names

			args = {}
			for field in [
				"item_code",
				"item_name",
				"description",
				"qty",
				"rate",
				"conversion_factor",
				"warehouse",
				"uom",
			]:
				args[field] = data.get(field)
			args["lead_time_days"] = data.get("lead_time_days")

			# uom is editable even on RFQ-original rows, so a supplier could
			# type an ad-hoc unit there too - always make sure it resolves.
			_ensure_uom_exists(args.get("uom"))

			if not is_rfq_item:
				_ensure_item_exists(args.get("item_code"), data.get("item_name"), args.get("uom"))

			if is_rfq_item:
				args["request_for_quotation_item"] = data.get("rfq_item_name")
				args["request_for_quotation"] = rfq.name

			args["supplier_part_no"] = frappe.db.get_value(
				"Item Supplier",
				{"parent": args.get("item_code"), "supplier": row.supplier},
				"supplier_part_no",
			)

			sq_doc.append("items", args)

		if not sq_doc.items:
			return {"ok": False, "reason": "no_items"}

		sq_doc.flags.ignore_permissions = True
		sq_doc.run_method("set_missing_values")
		sq_doc.save(ignore_permissions=True)
		sq_doc.submit()

		rfq_supplier.db_set("quote_status", "Received", update_modified=False)

		return {"ok": True, "supplier_quotation": sq_doc.name}
	except Exception:
		frappe.log_error(title=f"Guest supplier quotation submit failed for {row.name}")
		return {"ok": False, "reason": "error"}
