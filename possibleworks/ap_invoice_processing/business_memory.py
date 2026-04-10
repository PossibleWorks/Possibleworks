# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import difflib

import frappe
from possibleworks.ap_invoice_processing.smart_search import compute_item_similarity


SUPPLIER_HISTORY_SOURCES = (
	{
		"label": "Purchase Invoice",
		"parent_doctype": "Purchase Invoice",
		"party_fields": ("supplier",),
		"base_weight": 1.0,
		"date_fields": ("posting_date", "bill_date"),
	},
	{
		"label": "Purchase Order",
		"parent_doctype": "Purchase Order",
		"party_fields": ("supplier",),
		"base_weight": 0.88,
		"date_fields": ("transaction_date",),
	},
	{
		"label": "Purchase Receipt",
		"parent_doctype": "Purchase Receipt",
		"party_fields": ("supplier",),
		"base_weight": 0.82,
		"date_fields": ("posting_date",),
	},
	{
		"label": "Supplier Quotation",
		"parent_doctype": "Supplier Quotation",
		"party_fields": ("supplier",),
		"base_weight": 0.72,
		"date_fields": ("transaction_date",),
	},
)

CUSTOMER_HISTORY_SOURCES = (
	{
		"label": "Sales Invoice",
		"parent_doctype": "Sales Invoice",
		"party_fields": ("customer",),
		"base_weight": 1.0,
		"date_fields": ("posting_date",),
	},
	{
		"label": "Sales Order",
		"parent_doctype": "Sales Order",
		"party_fields": ("customer",),
		"base_weight": 0.92,
		"date_fields": ("transaction_date",),
	},
	{
		"label": "Delivery Note",
		"parent_doctype": "Delivery Note",
		"party_fields": ("customer",),
		"base_weight": 0.84,
		"date_fields": ("posting_date",),
	},
	{
		"label": "Quotation",
		"parent_doctype": "Quotation",
		"party_fields": ("party_name", "customer"),
		"base_weight": 0.72,
		"date_fields": ("transaction_date",),
	},
)


def _request_cache():
	cache = getattr(frappe.local, "_ai_document_business_memory_cache", None)
	if cache is None:
		cache = {}
		frappe.local._ai_document_business_memory_cache = cache
	return cache


def _normalize_text(value):
	if value is None:
		return ""
	return "".join(ch.lower() for ch in str(value).strip() if ch.isalnum())


def _normalize_code(value):
	if value is None:
		return ""
	text = "".join(ch.upper() for ch in str(value).strip() if ch.isalnum())
	return text


def _tokenize_for_similarity(value):
	if value is None:
		return set()
	clean_chars = []
	for ch in str(value).lower():
		clean_chars.append(ch if ch.isalnum() else " ")
	return {tok for tok in "".join(clean_chars).split() if len(tok) > 2}


def text_similarity(left, right):
	left_norm = _normalize_text(left)
	right_norm = _normalize_text(right)
	if not left_norm or not right_norm:
		return 0.0

	seq_ratio = difflib.SequenceMatcher(None, left_norm, right_norm).ratio()
	left_tokens = _tokenize_for_similarity(left)
	right_tokens = _tokenize_for_similarity(right)
	token_ratio = 0.0
	if left_tokens and right_tokens:
		token_ratio = len(left_tokens & right_tokens) / float(max(len(left_tokens), len(right_tokens)))

	blended = (seq_ratio * 0.7) + (token_ratio * 0.3)
	item_ratio = compute_item_similarity(left, right)
	return max(seq_ratio, token_ratio, blended, item_ratio)


def _doctype_exists(doctype):
	try:
		return bool(frappe.db.exists("DocType", doctype))
	except Exception:
		return False


def _available_fields(doctype, preferred_fields):
	cache_key = ("fields", doctype)
	cache = _request_cache()
	field_names = cache.get(cache_key)
	if field_names is None:
		try:
			meta = frappe.get_meta(doctype)
			field_names = {df.fieldname for df in meta.fields}
			field_names.update({"name", "owner", "creation", "modified", "docstatus", "parent"})
		except Exception:
			field_names = set()
		cache[cache_key] = field_names
	return [field for field in preferred_fields if field in field_names]


def _resolve_party_field(parent_doctype, candidates):
	fields = set(_available_fields(parent_doctype, candidates))
	for fieldname in candidates:
		if fieldname in fields:
			return fieldname
	return None


def _get_child_doctype(parent_doctype):
	cache_key = ("child_doctype", parent_doctype)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]

	child_doctype = None
	try:
		meta = frappe.get_meta(parent_doctype)
		if meta.has_field("items"):
			child_doctype = meta.get_field("items").options
	except Exception:
		child_doctype = None
	cache[cache_key] = child_doctype
	return child_doctype


def _history_sources_for_party(party_type):
	if str(party_type or "").strip().lower() == "customer":
		return CUSTOMER_HISTORY_SOURCES
	return SUPPLIER_HISTORY_SOURCES


def _pick_date_field(parent_doctype, date_fields):
	for fieldname in date_fields or ():
		if fieldname in _available_fields(parent_doctype, (fieldname,)):
			return fieldname
	return None


def _fetch_history_rows(party_type, party_name, company=None):
	if not party_name:
		return []

	cache_key = ("history_rows", party_type, party_name, company)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]

	rows = []
	for source in _history_sources_for_party(party_type):
		parent_doctype = source["parent_doctype"]
		if not _doctype_exists(parent_doctype):
			continue

		child_doctype = _get_child_doctype(parent_doctype)
		if not child_doctype or not _doctype_exists(child_doctype):
			continue

		party_field = _resolve_party_field(parent_doctype, source.get("party_fields") or ())
		if not party_field:
			continue

		date_field = _pick_date_field(parent_doctype, source.get("date_fields") or ())
		parent_fields = ["name", "docstatus", "creation"]
		if date_field:
			parent_fields.append(date_field)
		if "company" in _available_fields(parent_doctype, ("company",)):
			parent_fields.append("company")

		parent_docs = frappe.get_all(
			parent_doctype,
			filters={party_field: party_name, "docstatus": ["<", 2]},
			fields=parent_fields,
			order_by=(f"{date_field} desc, creation desc" if date_field else "creation desc"),
			limit_page_length=60,
		)
		if not parent_docs:
			continue

		parent_map = {}
		for idx, parent in enumerate(parent_docs):
			parent_map[parent["name"]] = {
				"name": parent.get("name"),
				"docstatus": parent.get("docstatus", 0),
				"company": parent.get("company"),
				"rank": idx,
			}

		child_fields = ["parent"]
		child_fields.extend(
			_available_fields(
				child_doctype,
				(
					"item_code",
					"item_name",
					"description",
					"expense_account",
					"income_account",
					"cost_center",
					"warehouse",
					"uom",
					"stock_uom",
					"gst_hsn_code",
					"hsn_sac_code",
					"gst_hsn",
					"item_group",
				),
			)
		)

		child_rows = frappe.get_all(
			child_doctype,
			filters={"parent": ["in", list(parent_map.keys())]},
			fields=list(dict.fromkeys(child_fields)),
			order_by="creation desc",
			limit_page_length=1000,
		)

		for row in child_rows:
			parent = parent_map.get(row.get("parent"))
			if not parent:
				continue
			if company and parent.get("company") and parent.get("company") != company:
				continue

			item_code = row.get("item_code")
			description = row.get("description") or row.get("item_name") or item_code
			if not item_code and not description:
				continue

			weight = float(source.get("base_weight") or 1.0)
			weight += 0.35 if int(parent.get("docstatus") or 0) == 1 else -0.08
			weight += max(0.0, 0.12 - (float(parent.get("rank") or 0) * 0.003))

			rows.append(
				{
					"source_label": source["label"],
					"parent_doctype": parent_doctype,
					"parent_name": row.get("parent"),
					"company": parent.get("company"),
					"docstatus": parent.get("docstatus", 0),
					"item_code": item_code,
					"item_name": row.get("item_name"),
					"description": row.get("description"),
					"expense_account": row.get("expense_account"),
					"income_account": row.get("income_account"),
					"cost_center": row.get("cost_center"),
					"warehouse": row.get("warehouse"),
					"uom": row.get("uom") or row.get("stock_uom"),
					"hsn_sac_code": row.get("hsn_sac_code") or row.get("gst_hsn_code") or row.get("gst_hsn"),
					"item_group": row.get("item_group"),
					"weight": round(weight, 4),
				}
			)

	cache[cache_key] = rows
	return rows


def _top_weighted_entry(score_map):
	best_name = None
	best_score = 0.0
	for key, score in (score_map or {}).items():
		if score > best_score:
			best_name = key
			best_score = score
	return best_name, round(best_score, 4)


def _aggregate_candidates(rows, source_label):
	candidate_map = {}

	for row in rows or []:
		item_code = row.get("item_code")
		if not item_code:
			continue

		entry = candidate_map.setdefault(
			item_code,
			{
				"item_code": item_code,
				"item_name": row.get("item_name") or item_code,
				"texts": [],
				"text_keys": set(),
				"count": 0,
				"weighted_count": 0.0,
				"source_label": source_label,
				"source_doctypes": set(),
				"hsn_scores": {},
				"expense_account_scores": {},
				"cost_center_scores": {},
			},
		)

		entry["count"] += 1
		entry["weighted_count"] += float(row.get("weight") or 1.0)
		entry["source_doctypes"].add(row.get("parent_doctype"))

		for text in (row.get("description"), row.get("item_name"), item_code):
			if not text:
				continue
			text_key = _normalize_text(text)
			if text_key and text_key not in entry["text_keys"] and len(entry["texts"]) < 10:
				entry["texts"].append(str(text).strip())
				entry["text_keys"].add(text_key)

		hsn_code = _normalize_code(row.get("hsn_sac_code"))
		if hsn_code:
			entry["hsn_scores"][hsn_code] = entry["hsn_scores"].get(hsn_code, 0.0) + float(row.get("weight") or 1.0)

		expense_account = row.get("expense_account") or row.get("income_account")
		if expense_account:
			entry["expense_account_scores"][expense_account] = entry["expense_account_scores"].get(
				expense_account, 0.0
			) + float(row.get("weight") or 1.0)

		cost_center = row.get("cost_center")
		if cost_center:
			entry["cost_center_scores"][cost_center] = entry["cost_center_scores"].get(
				cost_center, 0.0
			) + float(row.get("weight") or 1.0)

	candidates = []
	for entry in candidate_map.values():
		entry.pop("text_keys", None)
		entry["weighted_count"] = round(entry["weighted_count"], 4)
		entry["source_doctypes"] = sorted(dt for dt in entry["source_doctypes"] if dt)
		entry["hsn_sac_codes"] = sorted(entry["hsn_scores"], key=entry["hsn_scores"].get, reverse=True)
		top_expense, top_expense_score = _top_weighted_entry(entry["expense_account_scores"])
		top_cc, top_cc_score = _top_weighted_entry(entry["cost_center_scores"])
		entry["top_expense_account"] = top_expense
		entry["top_expense_score"] = top_expense_score
		entry["top_cost_center"] = top_cc
		entry["top_cost_center_score"] = top_cc_score
		candidates.append(entry)

	candidates.sort(key=lambda row: (float(row.get("weighted_count") or 0.0), row.get("count") or 0), reverse=True)
	return candidates


def get_document_item_candidates(parent_doctype, document_name):
	if not parent_doctype or not document_name or not _doctype_exists(parent_doctype):
		return []

	cache_key = ("document_candidates", parent_doctype, document_name)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]

	child_doctype = _get_child_doctype(parent_doctype)
	if not child_doctype or not _doctype_exists(child_doctype):
		cache[cache_key] = []
		return []

	fields = ["parent"]
	fields.extend(
		_available_fields(
			child_doctype,
			("item_code", "item_name", "description", "gst_hsn_code", "hsn_sac_code", "gst_hsn"),
		)
	)
	rows = frappe.get_all(
		child_doctype,
		filters={"parent": document_name},
		fields=list(dict.fromkeys(fields)),
		order_by="creation asc",
		limit_page_length=500,
	)

	normalized_rows = []
	for row in rows:
		normalized_rows.append(
			{
				"parent_doctype": parent_doctype,
				"item_code": row.get("item_code"),
				"item_name": row.get("item_name"),
				"description": row.get("description"),
				"hsn_sac_code": row.get("hsn_sac_code") or row.get("gst_hsn_code") or row.get("gst_hsn"),
				"weight": 1.2,
			}
		)

	cache[cache_key] = _aggregate_candidates(normalized_rows, f"{parent_doctype} history")
	return cache[cache_key]


def get_party_history_item_candidates(party_type, party_name, company=None):
	if not party_name:
		return []

	cache_key = ("party_candidates", party_type, party_name, company)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]

	rows = _fetch_history_rows(party_type, party_name, company=company)
	label = "customer history" if str(party_type or "").strip().lower() == "customer" else "supplier history"
	cache[cache_key] = _aggregate_candidates(rows, label)
	return cache[cache_key]


def resolve_purchase_history_defaults(company, supplier, item_code=None, description=None, hsn_sac_code=None):
	if not supplier:
		return {}

	candidates = get_party_history_item_candidates("Supplier", supplier, company=company)
	if not candidates:
		return {}

	if item_code:
		for candidate in candidates:
			if candidate.get("item_code") != item_code:
				continue
			return {
				"item_code": item_code,
				"expense_account": candidate.get("top_expense_account"),
				"cost_center": candidate.get("top_cost_center"),
				"confidence": round(min(0.97, 0.68 + (float(candidate.get("weighted_count") or 0.0) * 0.03)), 4),
				"source": "supplier_history_item_exact",
			}

	if not description:
		return {}

	hsn_code = _normalize_code(hsn_sac_code)
	best_candidate = None
	best_score = 0.0
	second_score = 0.0
	total_weight = sum(float(candidate.get("weighted_count") or 0.0) for candidate in candidates[:80]) or 1.0

	for candidate in candidates[:80]:
		candidate_texts = candidate.get("texts") or [candidate.get("item_code")]
		text_score = 0.0
		for text in candidate_texts:
			text_score = max(text_score, text_similarity(description, text))

		score = text_score
		score += min(0.12, float(candidate.get("weighted_count") or 0.0) * 0.015)
		if hsn_code and hsn_code in {_normalize_code(code) for code in candidate.get("hsn_sac_codes") or []}:
			score += 0.10

		if score > best_score:
			second_score = best_score
			best_score = score
			best_candidate = candidate
		elif score > second_score:
			second_score = score

	dominance = 0.0
	if best_candidate:
		dominance = float(best_candidate.get("weighted_count") or 0.0) / total_weight
	threshold = 0.70
	if dominance >= 0.55:
		threshold -= 0.08
	elif dominance >= 0.35:
		threshold -= 0.04
	if hsn_code and best_candidate and hsn_code in {_normalize_code(code) for code in best_candidate.get("hsn_sac_codes") or []}:
		threshold -= 0.04
	margin = best_score - second_score
	margin_required = 0.04 if dominance >= 0.50 else 0.07
	if not best_candidate or best_score < threshold or (best_score < 0.84 and margin < margin_required):
		return {}

	return {
		"item_code": best_candidate.get("item_code"),
		"expense_account": best_candidate.get("top_expense_account"),
		"cost_center": best_candidate.get("top_cost_center"),
		"confidence": round(min(0.99, best_score), 4),
		"source": "supplier_history_description",
	}


# Tax-type pattern table.  Ordered from most specific to least so that
# "integrated" doesn't accidentally match a CGST account containing "gst".
_TAX_TYPE_PATTERNS = [
    ("IGST",  ["igst", "integrated gst", "integrated goods and service"]),
    ("CGST",  ["cgst", "central gst", "central goods and service"]),
    ("SGST",  ["sgst", "utgst", "state gst", "union territory gst", "state goods and service"]),
    ("TDS",   ["tds", "tax deducted at source", "tds u/s", "tds @"]),
    ("TCS",   ["tcs", "tax collected at source"]),
    ("Cess",  ["cess", "gst cess", "health and education cess"]),
]


def _classify_tax_type(account_head, description):
    """Classify a tax row by type.

    Checks account_name (canonical, set by the accountant) before
    falling back to the invoice description string.  Returns one of:
    IGST | CGST | SGST | TDS | TCS | Cess | Other.
    """
    account_name = account_head.split(" - ")[0].lower() if account_head else ""
    desc_lower = (description or account_head or "").lower()

    for tax_type, keywords in _TAX_TYPE_PATTERNS:
        if any(kw in account_name for kw in keywords):
            return tax_type
    for tax_type, keywords in _TAX_TYPE_PATTERNS:
        if any(kw in desc_lower for kw in keywords):
            return tax_type
    return "Other"


def get_party_tax_history(party_type, party_name, company=None):
	"""Return the most-used tax template and tax accounts for this party.

	Returns a dict:
	  templates: [{template_name, count, is_default}]  -- sorted by frequency
	  tax_accounts: {CGST: [{account, rate, count}], SGST: [...], ...}
	"""
	if not party_name:
		return {"templates": [], "tax_accounts": {}}

	cache_key = ("party_tax_history", party_type, party_name, company)
	cache = _request_cache()
	if cache_key in cache:
		return cache[cache_key]

	sources = _history_sources_for_party(party_type)
	# Only look at the first source (most authoritative: Purchase Invoice / Sales Invoice)
	primary_source = sources[0] if sources else None
	if not primary_source or not _doctype_exists(primary_source["parent_doctype"]):
		cache[cache_key] = {"templates": [], "tax_accounts": {}}
		return cache[cache_key]

	parent_doctype = primary_source["parent_doctype"]
	party_field = _resolve_party_field(parent_doctype, primary_source.get("party_fields") or ())
	if not party_field:
		cache[cache_key] = {"templates": [], "tax_accounts": {}}
		return cache[cache_key]

	# ── Fetch parent documents ────────────────────────────────────────────────
	parent_fields = ["name", "docstatus"]
	if "taxes_and_charges" in _available_fields(parent_doctype, ("taxes_and_charges",)):
		parent_fields.append("taxes_and_charges")
	if company and "company" in _available_fields(parent_doctype, ("company",)):
		parent_fields.append("company")

	try:
		parent_docs = frappe.get_all(
			parent_doctype,
			filters={party_field: party_name, "docstatus": ["<", 2]},
			fields=parent_fields,
			order_by="creation desc",
			limit_page_length=60,
		)
	except Exception:
		parent_docs = []

	if not parent_docs:
		cache[cache_key] = {"templates": [], "tax_accounts": {}}
		return cache[cache_key]

	# Filter by company if provided
	if company:
		parent_docs = [p for p in parent_docs if not p.get("company") or p.get("company") == company]

	# ── Aggregate tax templates ───────────────────────────────────────────────
	template_counts = {}
	parent_names = [p["name"] for p in parent_docs]
	for p in parent_docs:
		tmpl = p.get("taxes_and_charges")
		if tmpl:
			template_counts[tmpl] = template_counts.get(tmpl, 0) + 1

	templates = [
		{"template_name": name, "count": count}
		for name, count in sorted(template_counts.items(), key=lambda x: x[1], reverse=True)
	]

	# ── Fetch tax rows ────────────────────────────────────────────────────────
	tax_accounts_by_type: dict = {}
	if parent_names:
		# Determine child tax table name
		if party_type.lower() == "customer":
			child_tax_doctype = "Sales Taxes and Charges"
		else:
			child_tax_doctype = "Purchase Taxes and Charges"

		if _doctype_exists(child_tax_doctype):
			child_fields = ["parent", "account_head", "description", "charge_type"]
			for f in ("rate",):
				if f in _available_fields(child_tax_doctype, (f,)):
					child_fields.append(f)

			try:
				tax_rows = frappe.get_all(
					child_tax_doctype,
					filters={"parent": ["in", parent_names[:50]]},
					fields=list(dict.fromkeys(child_fields)),
					limit_page_length=500,
				)
			except Exception:
				tax_rows = []

			# Identify tax type from description/account_head
			for row in tax_rows or []:
				account = row.get("account_head")
				if not account:
					continue
				tax_type = _classify_tax_type(account, row.get("description"))
				rate = float(row.get("rate") or 0.0)
				bucket = tax_accounts_by_type.setdefault(tax_type, {})
				key = (account, rate)
				bucket[key] = bucket.get(key, 0) + 1

	# Convert to sorted list format
	tax_accounts = {}
	for tax_type, bucket in tax_accounts_by_type.items():
		tax_accounts[tax_type] = sorted(
			[{"account": acct, "rate": rate, "count": cnt} for (acct, rate), cnt in bucket.items()],
			key=lambda x: x["count"],
			reverse=True,
		)

	result = {"templates": templates, "tax_accounts": tax_accounts}
	cache[cache_key] = result
	return result
