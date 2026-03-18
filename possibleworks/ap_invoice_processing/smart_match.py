# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import difflib
import itertools
import frappe
from possibleworks.ap_invoice_processing.ap_agent_tools import (
	_check_duplicate_invoice,
	_find_purchase_receipt,
	_find_tax_account,
)
from possibleworks.ap_invoice_processing.business_memory import (
	get_document_item_candidates,
	get_party_history_item_candidates,
)
from possibleworks.ap_invoice_processing.constants import (
	CUSTOMER_SIDE_DOCTYPES,
	PAYMENT_ENTRY_DOCTYPE,
	SUPPLIER_SIDE_DOCTYPES,
)
from possibleworks.ap_invoice_processing.smart_search import (
	compute_item_similarity,
	extract_meaningful_tokens,
	execute_smart_search,
	SIMILARITY_THRESHOLD_ITEM,
	SIMILARITY_THRESHOLD_PARTY,
)
from possibleworks.ap_invoice_processing.semantic_item_matcher import semantic_rank


def _normalize_text(value):
	if value is None:
		return ""
	return "".join(ch.lower() for ch in str(value).strip() if ch.isalnum())


def _get_item_group_catalog():
	"""
	Return a compact list of item groups that are actually used by active items.

	Each entry:
	- group: Item Group name
	- text: full path string (used for semantic ranking)
	- depth: path depth (prefer deeper groups when scores are close)
	- lft/rgt: tree bounds (used to get descendants)
	"""
	cache = getattr(frappe.local, "_ai_item_group_catalog", None)
	if cache is not None:
		return cache

	try:
		group_rows = frappe.get_all(
			"Item Group",
			fields=["name", "parent_item_group", "lft", "rgt"],
			limit_page_length=5000,
		)
	except Exception:
		group_rows = []

	if not group_rows:
		frappe.local._ai_item_group_catalog = []
		frappe.local._ai_item_group_index = {}
		return []

	parent_map = {row.get("name"): row.get("parent_item_group") for row in group_rows}
	bounds_map = {
		row.get("name"): (row.get("lft"), row.get("rgt"))
		for row in group_rows
		if row.get("name") and row.get("lft") is not None and row.get("rgt") is not None
	}

	# Only groups that actually have active items (plus ancestors) to keep the catalog small.
	try:
		used_rows = frappe.db.sql(
			"""
			SELECT DISTINCT item_group
			FROM `tabItem`
			WHERE disabled = 0
			  AND item_group IS NOT NULL
			  AND item_group != ''
			""",
			as_list=True,
		)
		used_groups = {row[0] for row in used_rows if row and row[0]}
	except Exception:
		used_groups = set()

	# Include ancestors so semantic routing can land on parent groups too.
	for group in list(used_groups):
		current = group
		seen = set()
		while True:
			parent = parent_map.get(current)
			if not parent or parent in seen:
				break
			used_groups.add(parent)
			seen.add(parent)
			current = parent

	def _build_path(name):
		parts = [name]
		current = name
		seen = set()
		while True:
			parent = parent_map.get(current)
			if not parent or parent in seen:
				break
			parts.append(parent)
			seen.add(parent)
			current = parent
		return " > ".join(reversed(parts))

	catalog = []
	index = {}
	for name in sorted(used_groups):
		if not name:
			continue
		path = _build_path(name)
		depth = path.count(" > ") + 1 if path else 1
		lft, rgt = bounds_map.get(name, (None, None))
		entry = {
			"group": name,
			"text": path or name,
			"depth": depth,
			"lft": lft,
			"rgt": rgt,
		}
		catalog.append(entry)
		index[name] = entry

	frappe.local._ai_item_group_catalog = catalog
	frappe.local._ai_item_group_index = index
	return catalog


def _get_descendant_item_groups(group_name):
	"""Return group_name + descendants using Item Group nested-set bounds."""
	if not group_name:
		return []

	cache = getattr(frappe.local, "_ai_item_group_descendants", None)
	if cache is None:
		cache = {}
		frappe.local._ai_item_group_descendants = cache
	if group_name in cache:
		return cache[group_name]

	index = getattr(frappe.local, "_ai_item_group_index", None) or {}
	entry = index.get(group_name) or {}
	lft = entry.get("lft")
	rgt = entry.get("rgt")

	if lft is None or rgt is None:
		try:
			row = frappe.db.get_value("Item Group", group_name, ["lft", "rgt"], as_dict=True)
			if row:
				lft = row.get("lft")
				rgt = row.get("rgt")
		except Exception:
			lft = None
			rgt = None

	if lft is None or rgt is None:
		cache[group_name] = [group_name]
		return cache[group_name]

	try:
		rows = frappe.get_all(
			"Item Group",
			filters=[["lft", ">=", lft], ["rgt", "<=", rgt]],
			fields=["name"],
			limit_page_length=5000,
		)
		desc = [row.get("name") for row in rows if row.get("name")]
	except Exception:
		desc = []

	cache[group_name] = desc or [group_name]
	return cache[group_name]


def _pick_item_groups_for_description(description, *, max_groups=2):
	"""
	Pick the most likely item groups for `description` using semantic embeddings.

	This is the "category-first" step:
	- we rank against item-group paths (not items)
	- we bias toward deeper groups when scores are close
	"""
	catalog = _get_item_group_catalog()
	if not catalog:
		return []

	ranked = semantic_rank(description, catalog, text_key="text", top_k=max(6, int(max_groups or 2) * 3))
	if not ranked:
		return []

	best_score = float(ranked[0].get("semantic_score") or 0.0)
	close = [row for row in ranked if float(row.get("semantic_score") or 0.0) >= (best_score - 0.03)]
	close.sort(key=lambda row: (float(row.get("semantic_score") or 0.0), int(row.get("depth") or 1)), reverse=True)
	return close[: max(1, int(max_groups or 2))]


def _item_text_for_embedding(item_row):
	parts = []
	for key in ("item_name", "item_code", "description"):
		val = (item_row or {}).get(key)
		if val and str(val).strip():
			parts.append(str(val).strip())
	text = " | ".join(parts)
	return text[:600]


def _get_group_item_candidates(group_name, description, *, max_items_full=250):
	"""
	Return a list of candidate Items (dicts) scoped to the group.

	Strategy:
	- If the group is small (<= max_items_full), pull all items in group+descendants.
	- Otherwise, use a token-based + fuzzy DB prefilter to keep candidate set small.
	"""
	if not group_name:
		return []

	desc_groups = _get_descendant_item_groups(group_name)
	if not desc_groups:
		return []

	# Group-level cache only for small groups (safe to reuse across multiple lines).
	cache = getattr(frappe.local, "_ai_items_by_group", None)
	if cache is None:
		cache = {}
		frappe.local._ai_items_by_group = cache

	if group_name in cache and cache[group_name] is not None:
		return cache[group_name]

	item_filters = {"disabled": 0, "item_group": ["in", tuple(desc_groups)]}

	try:
		item_count = frappe.db.count("Item", filters=item_filters)
	except Exception:
		item_count = None

	fields = ["name", "item_code", "item_name", "description", "item_group", "gst_hsn_code", "hsn_sac_code"]

	if item_count is not None and item_count <= max_items_full:
		try:
			rows = frappe.get_all(
				"Item",
				filters=item_filters,
				fields=fields,
				limit_page_length=max_items_full,
				order_by="item_name asc",
			)
		except Exception:
			rows = []
		cache[group_name] = rows
		return rows

	# Mark as "large group" so we don't attempt to cache huge lists.
	cache[group_name] = None

	candidates = []
	seen = set()

	# 1) Fuzzy search in-group (fast) to get a high-quality shortlist.
	try:
		search_results = execute_smart_search(
			"Item",
			["item_name", "item_code", "description"],
			description,
			filters=item_filters,
			return_fields=fields,
			similarity_threshold=0.0,
			limit=25,
		)
	except Exception:
		search_results = []

	for row in search_results or []:
		code = row.get("item_code") or row.get("name")
		if not code or code in seen:
			continue
		seen.add(code)
		candidates.append(row)

	# 2) Meaningful-token prefilter in-group (covers partial phrases).
	tokens = extract_meaningful_tokens(description)[:6]
	for token in tokens:
		try:
			rows = frappe.get_all(
				"Item",
				filters=item_filters,
				or_filters={
					"item_code": ["like", f"%{token}%"],
					"item_name": ["like", f"%{token}%"],
					"description": ["like", f"%{token}%"],
				},
				fields=fields,
				limit_page_length=25,
			)
		except Exception:
			rows = []

		for row in rows or []:
			code = row.get("item_code") or row.get("name")
			if not code or code in seen:
				continue
			seen.add(code)
			candidates.append(row)

		if len(candidates) >= 160:
			break

	return candidates


def _get_company_identity_tokens(primary_company=None):
	"""Collect normalized company identifiers to avoid matching own company as supplier."""
	tokens = set()
	if primary_company:
		tokens.add(_normalize_text(primary_company))

	try:
		companies = frappe.get_all("Company", fields=["name", "abbr"], limit_page_length=200)
		for c in companies:
			tokens.add(_normalize_text(c.get("name")))
			tokens.add(_normalize_text(c.get("abbr")))
	except Exception:
		pass

	return {t for t in tokens if t}


def _looks_like_company_name(value, company_tokens):
	norm = _normalize_text(value)
	if not norm or not company_tokens:
		return False
	if norm in company_tokens:
		return True
	# Handle variants like "AskYourDealProcurementLLP" vs "AskYourDeal".
	return any(norm.startswith(token) or token.startswith(norm) for token in company_tokens if len(token) >= 4)


def _normalize_gstin(value):
	if value is None:
		return None
	text = str(value).strip().upper()
	return text or None


def _get_company_tax_ids():
	"""Collect company GST/tax IDs so we can ignore self-GSTIN in supplier lookup."""
	tax_ids = set()
	try:
		meta = frappe.get_meta("Company")
		fields = [f for f in ("tax_id", "gstin") if meta.has_field(f)]
		if not fields:
			return tax_ids
		companies = frappe.get_all("Company", fields=fields, limit_page_length=200)
		for c in companies:
			for fieldname in fields:
				val = _normalize_gstin(c.get(fieldname))
				if val:
					tax_ids.add(val)
	except Exception:
		pass
	return tax_ids


def _find_supplier_by_tax_id(gstin):
	if not gstin:
		return None
	try:
		return frappe.db.get_value("Supplier", {"tax_id": gstin, "disabled": 0}, "name")
	except Exception:
		return None


def _tokenize_for_similarity(value):
	if value is None:
		return set()
	clean_chars = []
	for ch in str(value).lower():
		clean_chars.append(ch if ch.isalnum() else " ")
	return {tok for tok in "".join(clean_chars).split() if len(tok) > 2}


def _text_similarity(left, right):
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


def _score_party_candidate_by_history(party_type, candidate_name, parsed_data, company=None):
	item_descs = []
	for item in (parsed_data or {}).get("items", []) or []:
		if not isinstance(item, dict):
			continue
		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if desc:
			item_descs.append(desc)
		if len(item_descs) >= 3:
			break

	if not item_descs:
		return 0.0

	history_candidates = get_party_history_item_candidates(party_type, candidate_name, company=company)
	if not history_candidates:
		return 0.0

	line_scores = []
	for desc in item_descs:
		best_line_score = 0.0
		for candidate in history_candidates[:20]:
			for text in candidate.get("texts") or [candidate.get("item_code")]:
				best_line_score = max(best_line_score, _text_similarity(desc, text))
		line_scores.append(best_line_score)

	if not line_scores:
		return 0.0

	avg_line_score = sum(line_scores) / len(line_scores)
	dominance = float(history_candidates[0].get("weighted_count") or history_candidates[0].get("count") or 0.0)
	return min(0.18, (avg_line_score * 0.18) + min(0.04, dominance * 0.004))


def _pick_best_party_candidate(party_type, candidates, parsed_data, company=None):
	best = None
	best_score = 0.0
	second_score = 0.0

	for candidate in candidates or []:
		base_score = float(candidate.get("similarity_score") or 0.0)
		history_boost = _score_party_candidate_by_history(
			party_type,
			candidate.get("name"),
			parsed_data,
			company=company,
		)
		combined = min(1.0, base_score + history_boost)
		if combined > best_score:
			second_score = best_score
			best_score = combined
			best = candidate
		elif combined > second_score:
			second_score = combined

	return best, best_score, second_score


def _aggregate_item_candidates(rows):
	"""Build ranked item candidates from historical line rows."""
	candidate_map = {}

	for row in rows or []:
		item_code = row.get("item_code")
		if not item_code:
			continue

		entry = candidate_map.setdefault(item_code, {"item_code": item_code, "texts": [], "count": 0})
		text = str(row.get("description") or row.get("item_name") or item_code).strip()
		if text and text not in entry["texts"] and len(entry["texts"]) < 6:
			entry["texts"].append(text)
		entry["count"] += 1

	candidates = list(candidate_map.values())
	candidates.sort(key=lambda x: x["count"], reverse=True)
	return candidates


def _get_po_item_candidates(po_id):
	if not po_id:
		return []
	return get_document_item_candidates("Purchase Order", po_id)


def _get_supplier_history_item_candidates(supplier, company=None):
	return get_party_history_item_candidates("Supplier", supplier, company=company)


def _get_customer_history_item_candidates(customer, company=None):
	return get_party_history_item_candidates("Customer", customer, company=company)


def _normalize_tax_label(value):
	if value is None:
		return ""
	return " ".join(str(value).strip().split())


def _classify_tax_type(label):
	text = _normalize_tax_label(label).lower()
	if not text:
		return None
	if "igst" in text or "integrated gst" in text:
		return "IGST"
	if "cgst" in text or "central gst" in text:
		return "CGST"
	if "sgst" in text or "state gst" in text or "utgst" in text:
		return "SGST"
	if "gst" in text and "integrated" not in text and "central" not in text:
		# Labels like "Telangana GST" are state GST rows in practice.
		return "SGST"
	if "tds" in text:
		return "TDS"
	return None


def _get_taxable_subtotal(parsed_data):
	subtotal = _to_float((parsed_data or {}).get("subtotal"), 0.0)
	if subtotal > 0:
		return subtotal

	items = (parsed_data or {}).get("items", []) or []
	items_total = sum(
		_to_float((row or {}).get("amount"), 0.0)
		for row in items
		if isinstance(row, dict)
	)
	return items_total if items_total > 0 else 0.0


def _get_expected_tax_total(parsed_data):
	subtotal = _get_taxable_subtotal(parsed_data)
	grand_total = _to_float((parsed_data or {}).get("grand_total"), 0.0)
	if subtotal <= 0 or grand_total <= 0:
		return 0.0

	delta = round(grand_total - subtotal, 2)
	if delta <= 0.5:
		return 0.0
	return delta


def _tax_subset_penalty(subset):
	kinds = {row.get("_kind") for row in subset if row.get("_kind")}
	penalty = 0.0

	if "IGST" in kinds and ("CGST" in kinds or "SGST" in kinds):
		penalty += 10.0
	if ("CGST" in kinds) ^ ("SGST" in kinds):
		penalty += 2.0

	cgst_rates = [row.get("_rate", 0.0) for row in subset if row.get("_kind") == "CGST"]
	sgst_rates = [row.get("_rate", 0.0) for row in subset if row.get("_kind") == "SGST"]
	if cgst_rates and sgst_rates:
		if abs(sum(cgst_rates) - sum(sgst_rates)) <= 0.5:
			penalty -= 0.5

	return penalty


_MAX_TAX_ROWS_FOR_SUBSET = 8  # 2^8 = 256 combinations — safe upper bound


def _choose_tax_subset(rows, expected_total, subtotal, prefer_printed_amounts):
	if not rows:
		return []

	# Guard against O(2^n) blow-up on pathological documents.
	# For >8 rows skip subset search and return the rows as-is; the caller
	# will fall back to the tolerance check which is fast.
	if len(rows) > _MAX_TAX_ROWS_FOR_SUBSET:
		return rows

	best_subset = []
	best_score = None
	tolerance = max(1.0, abs(expected_total) * 0.03)

	for size in range(1, len(rows) + 1):
		for subset in itertools.combinations(rows, size):
			if prefer_printed_amounts:
				implied_total = round(
					sum(abs(_to_float(row.get("_amount"), 0.0)) for row in subset),
					2,
				)
				zero_penalty = sum(1 for row in subset if abs(_to_float(row.get("_amount"), 0.0)) < 0.001)
			else:
				total_rate = sum(max(_to_float(row.get("_rate"), 0.0), 0.0) for row in subset)
				if total_rate <= 0 or subtotal <= 0:
					continue
				implied_total = round((subtotal * total_rate) / 100.0, 2)
				zero_penalty = 0

			score = (
				round(abs(implied_total - expected_total), 4),
				round(_tax_subset_penalty(subset) + (zero_penalty * 1.5), 4),
				len(subset),
			)
			if best_score is None or score < best_score:
				best_score = score
				best_subset = list(subset)

	if best_score and best_score[0] <= tolerance:
		return best_subset
	return []


def _allocate_tax_amounts(rows, expected_total):
	if not rows:
		return rows

	expected_total = round(expected_total, 2)
	if len(rows) == 1:
		rows[0]["tax_amount"] = expected_total
		rows[0]["_amount"] = expected_total
		return rows

	kinds = {row.get("_kind") for row in rows if row.get("_kind")}
	cgst_rows = [row for row in rows if row.get("_kind") == "CGST"]
	sgst_rows = [row for row in rows if row.get("_kind") == "SGST"]
	if (
		len(rows) == 2
		and kinds == {"CGST", "SGST"}
		and len(cgst_rows) == 1
		and len(sgst_rows) == 1
		and abs(_to_float(cgst_rows[0].get("_rate"), 0.0) - _to_float(sgst_rows[0].get("_rate"), 0.0)) <= 0.5
	):
		first_amount = round(expected_total / 2.0, 2)
		second_amount = round(expected_total - first_amount, 2)
		rows[0]["tax_amount"] = first_amount
		rows[0]["_amount"] = first_amount
		rows[1]["tax_amount"] = second_amount
		rows[1]["_amount"] = second_amount
		return rows

	total_rate = sum(max(_to_float(row.get("_rate"), 0.0), 0.0) for row in rows)
	if total_rate <= 0:
		total_rate = float(len(rows))
		for row in rows:
			row["_rate_basis"] = 1.0
	else:
		for row in rows:
			row["_rate_basis"] = max(_to_float(row.get("_rate"), 0.0), 0.0)

	remaining = expected_total
	for idx, row in enumerate(rows):
		if idx == len(rows) - 1:
			amount = round(remaining, 2)
		else:
			share = row.get("_rate_basis", 0.0) / total_rate if total_rate else 0.0
			amount = round(expected_total * share, 2)
			remaining = round(remaining - amount, 2)
		row["tax_amount"] = amount
		row["_amount"] = amount

	for row in rows:
		row.pop("_rate_basis", None)
	return rows


def _normalize_tax_rows(parsed_data, warnings_list, messages_list):
	rows = []
	for tax in (parsed_data.get("taxes", []) or []):
		if not isinstance(tax, dict):
			continue

		row = dict(tax)
		label = _normalize_tax_label(row.get("tax_type_extracted"))
		rate = _to_float(row.get("rate"), 0.0)
		amount = _to_float(row.get("tax_amount"), 0.0)
		if not label and rate <= 0 and abs(amount) < 0.001:
			continue

		row["tax_type_extracted"] = label or "Tax"
		row["charge_type"] = row.get("charge_type") or "On Net Total"
		row["_kind"] = _classify_tax_type(label)
		row["_rate"] = rate
		row["_amount"] = amount
		rows.append(row)

	if not rows:
		parsed_data["taxes"] = []
		return []

	expected_total = _get_expected_tax_total(parsed_data)
	subtotal = _get_taxable_subtotal(parsed_data)
	printed_rows = [row for row in rows if abs(_to_float(row.get("_amount"), 0.0)) >= 0.001]
	normalized = []
	used_rate_inference = False

	if printed_rows:
		if expected_total > 0:
			normalized = _choose_tax_subset(
				printed_rows,
				expected_total=expected_total,
				subtotal=subtotal,
				prefer_printed_amounts=True,
			) or printed_rows
		else:
			normalized = printed_rows
	elif expected_total > 0:
		rate_rows = [row for row in rows if _to_float(row.get("_rate"), 0.0) > 0]
		normalized = _choose_tax_subset(
			rate_rows,
			expected_total=expected_total,
			subtotal=subtotal,
			prefer_printed_amounts=False,
		)
		if normalized:
			normalized = _allocate_tax_amounts(list(normalized), expected_total)
			used_rate_inference = True

	if expected_total > 0:
		rate_rows = [row for row in rows if _to_float(row.get("_rate"), 0.0) > 0]
		rate_candidate = _choose_tax_subset(
			rate_rows,
			expected_total=expected_total,
			subtotal=subtotal,
			prefer_printed_amounts=False,
		)
		if rate_candidate:
			current_penalty = _tax_subset_penalty(normalized) if normalized else float("inf")
			candidate_penalty = _tax_subset_penalty(rate_candidate)
			if candidate_penalty + 0.25 < current_penalty:
				normalized = _allocate_tax_amounts(list(rate_candidate), expected_total)
				used_rate_inference = True

	dropped_rows = len(rows) - len(normalized)
	if dropped_rows > 0:
		messages_list.append(
			f"Dropped {dropped_rows} blank or conflicting tax row(s) during tax normalization."
		)

	if used_rate_inference:
		messages_list.append(
			"Derived tax amounts from printed tax rates and the document total delta."
		)

	final_rows = []
	for row in normalized:
		clean = {k: v for k, v in row.items() if not k.startswith("_")}
		rate = round(_to_float(clean.get("rate"), 0.0), 4)
		amount = round(_to_float(clean.get("tax_amount"), 0.0), 2)
		clean["tax_amount"] = amount
		if rate > 0:
			clean["rate"] = rate
		else:
			clean["rate"] = None
		final_rows.append(clean)

	if final_rows and expected_total > 0:
		final_total = round(sum(abs(_to_float(row.get("tax_amount"), 0.0)) for row in final_rows), 2)
		if abs(final_total - expected_total) > max(1.0, expected_total * 0.03):
			warnings_list.append(
				f"Tax rows ({final_total:.2f}) do not fully reconcile to document tax total ({expected_total:.2f}). "
				"Please verify taxes before saving."
			)

	parsed_data["taxes"] = final_rows
	return final_rows


def _resolve_tax_accounts(taxes, messages_list):
	resolved = 0
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	for tax in taxes or []:
		if not isinstance(tax, dict) or tax.get("account_head_matched"):
			continue

		tax_label = tax.get("tax_type_extracted")
		tax_type = _classify_tax_type(tax_label)
		rate = _to_float(tax.get("rate"), 0.0)
		account_match = None

		if tax_type:
			account_candidates = _find_tax_account(tax_type, rate)
			if account_candidates:
				account_match = account_candidates[0].get("name")

		if not account_match and tax_label:
			# Try with account_type=Tax first, then without as fallback
			for extra in ({"account_type": "Tax"}, {}):
				sf = {"is_group": 0}
				if company:
					sf["company"] = company
				sf.update(extra)
				account_candidates = execute_smart_search(
					"Account",
					["account_name", "name"],
					tax_label,
					filters=sf,
					return_fields=["name", "account_name"],
					limit=1,
				)
				if account_candidates:
					account_match = account_candidates[0].get("name")
					break

		if account_match:
			tax["account_head_matched"] = account_match
			resolved += 1

	if resolved:
		messages_list.append(f"{resolved} tax account(s) auto-matched from ERPNext masters.")


def _get_tax_template_doctype(target_doctype):
	# Payment Entry uses the purchase-side template when paying a supplier
	# and the sales-side template when receiving from a customer.  Because
	# direction isn't known at this point we default to the purchase template;
	# the tax normalizer will validate the match against document math anyway.
	if target_doctype in CUSTOMER_SIDE_DOCTYPES:
		return "Sales Taxes and Charges Template"
	return "Purchase Taxes and Charges Template"


def _template_matches_document_tax(target_doctype, template_name, parsed_data):
	if not template_name:
		return False

	expected_total = _get_expected_tax_total(parsed_data)
	subtotal = _get_taxable_subtotal(parsed_data)
	if expected_total <= 0 or subtotal <= 0:
		return False

	template_doctype = _get_tax_template_doctype(target_doctype)
	try:
		doc = frappe.get_cached_doc(template_doctype, template_name)
	except Exception:
		return False

	total_rate = 0.0
	for row in getattr(doc, "taxes", []) or []:
		charge_type = str(getattr(row, "charge_type", "") or "").strip()
		if charge_type and charge_type != "On Net Total":
			return False
		total_rate += max(_to_float(getattr(row, "rate", 0.0), 0.0), 0.0)

	if total_rate <= 0:
		return False

	implied_total = round((subtotal * total_rate) / 100.0, 2)
	return abs(implied_total - expected_total) <= max(1.0, expected_total * 0.03)


def _map_unmatched_items_from_catalog(items, min_confidence=0.82):
	remapped = 0
	for item in items or []:
		if not isinstance(item, dict) or item.get("item_code_matched"):
			continue

		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if len(desc) < 3:
			continue

		candidates = execute_smart_search(
			"Item",
			["item_name", "item_code", "description"],
			desc,
			filters={"disabled": 0},
			return_fields=["name", "item_code", "item_name"],
			similarity_threshold=SIMILARITY_THRESHOLD_ITEM,
			limit=2,
		)
		if not candidates:
			continue

		top = candidates[0]
		top_score = float(top.get("similarity_score") or 0.0)
		second_score = float(candidates[1].get("similarity_score") or 0.0) if len(candidates) > 1 else 0.0
		item_code = top.get("item_code") or top.get("name")
		if not item_code:
			continue

		if top_score >= min_confidence or (top_score >= 0.76 and (top_score - second_score) >= 0.12):
			item["item_code_matched"] = item_code
			remapped += 1

	return remapped


def _map_unmatched_items_from_candidates(items, candidates, min_confidence=0.62):
	if not items or not candidates:
		return 0

	total_weight = sum(float(c.get("weighted_count") or c.get("count") or 0.0) for c in candidates[:80]) or 1.0

	remapped = 0
	for item in items:
		if item.get("item_code_matched"):
			continue

		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if not desc:
			continue

		best_code = None
		best_score = 0.0
		second_score = 0.0
		item_hsn = _normalize_text(item.get("hsn_sac_code"))

		for candidate in candidates[:80]:
			candidate_texts = candidate.get("texts") or [candidate.get("item_code")]
			candidate_best = 0.0
			for text in candidate_texts:
				score = _text_similarity(desc, text)
				if score > candidate_best:
					candidate_best = score

			usage_weight = float(candidate.get("weighted_count") or candidate.get("count") or 0.0)
			usage_boost = min(0.10, usage_weight * 0.012)
			dominance_boost = min(0.08, (usage_weight / total_weight) * 0.25)
			hsn_codes = {_normalize_text(code) for code in (candidate.get("hsn_sac_codes") or []) if code}
			hsn_boost = 0.10 if item_hsn and item_hsn in hsn_codes else 0.0
			candidate_score = min(1.0, candidate_best + usage_boost + dominance_boost + hsn_boost)
			if candidate_score > best_score:
				second_score = best_score
				best_score = candidate_score
				best_code = candidate.get("item_code")
			elif candidate_score > second_score:
				second_score = candidate_score

		dominance = 0.0
		if best_code:
			for candidate in candidates[:80]:
				if candidate.get("item_code") != best_code:
					continue
				dominance = float(candidate.get("weighted_count") or candidate.get("count") or 0.0) / total_weight
				break

		threshold = min_confidence
		if dominance >= 0.55:
			threshold -= 0.08
		elif dominance >= 0.35:
			threshold -= 0.04
		margin = best_score - second_score
		margin_required = 0.04 if dominance >= 0.50 else 0.07
		if best_code and best_score >= threshold and (best_score >= 0.82 or margin >= margin_required):
			item["item_code_matched"] = best_code
			item["item_match_source"] = "business_memory"
			item["item_match_confidence"] = round(best_score, 4)
			remapped += 1

	return remapped


def _get_item_search_return_fields():
	try:
		meta = frappe.get_meta("Item")
		available = {df.fieldname for df in meta.fields}
	except Exception:
		available = set()

	fields = ["name", "item_code", "item_name"]
	for optional_field in ("description", "item_group", "gst_hsn_code", "hsn_sac_code"):
		if optional_field in available:
			fields.append(optional_field)
	return list(dict.fromkeys(fields))


def _map_unmatched_items_from_meaningful_tokens(items):
	if not isinstance(items, list):
		return 0

	item_fields = _get_item_search_return_fields()
	remapped = 0

	for item in items:
		if not isinstance(item, dict) or item.get("item_code_matched"):
			continue

		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if len(desc) < 3:
			continue

		tokens = extract_meaningful_tokens(desc)[:6]
		if not tokens:
			continue

		candidate_scores = {}
		item_hsn = _normalize_text(item.get("hsn_sac_code"))

		for token in tokens:
			try:
				token_results = frappe.get_all(
					"Item",
					filters={"disabled": 0},
					or_filters={
						"item_code": ["like", f"%{token}%"],
						"item_name": ["like", f"%{token}%"],
						"description": ["like", f"%{token}%"],
					},
					fields=item_fields,
					limit_page_length=30,
				)
			except Exception:
				token_results = []

			match_count = len(token_results)
			if match_count == 0 or match_count > 20:
				continue

			token_weight = 1.0 / float(match_count)

			for candidate in token_results:
				item_code = candidate.get("item_code") or candidate.get("name")
				if not item_code:
					continue

				entry = candidate_scores.setdefault(
					item_code,
					{
						"token_score": 0.0,
						"similarity_score": 0.0,
						"candidate": candidate,
					},
				)
				entry["token_score"] += token_weight

				candidate_texts = [
					candidate.get("item_name"),
					candidate.get("description"),
					candidate.get("item_code"),
				]
				entry["similarity_score"] = max(
					entry["similarity_score"],
					max(_text_similarity(desc, text) for text in candidate_texts if text),
				)

				candidate_hsn = _normalize_text(candidate.get("gst_hsn_code") or candidate.get("hsn_sac_code"))
				if item_hsn and candidate_hsn and item_hsn == candidate_hsn:
					entry["token_score"] += 0.12

		if not candidate_scores:
			continue

		ranked = []
		for item_code, payload in candidate_scores.items():
			final_score = min(
				1.0,
				(payload["similarity_score"] * 0.55) + min(0.45, payload["token_score"]),
			)
			ranked.append((item_code, final_score, payload))

		ranked.sort(key=lambda row: row[1], reverse=True)
		best_code, best_score, _best_payload = ranked[0]
		second_score = ranked[1][1] if len(ranked) > 1 else 0.0

		if best_score >= 0.66 and (best_score >= 0.78 or (best_score - second_score) >= 0.08):
			item["item_code_matched"] = best_code
			item["item_match_source"] = "meaningful_token_fallback"
			item["item_match_confidence"] = round(best_score, 4)
			remapped += 1

	return remapped


def _map_small_unmatched_lines_to_dominant_item(items):
	if not isinstance(items, list):
		return 0

	matched_items = []
	code_amounts = {}
	for item in items:
		if not isinstance(item, dict) or not item.get("item_code_matched"):
			continue
		amount = abs(_to_float(item.get("amount"), 0.0))
		if amount <= 0:
			continue
		matched_items.append((item, amount))
		code = item.get("item_code_matched")
		code_amounts[code] = code_amounts.get(code, 0.0) + amount

	if not matched_items or not code_amounts:
		return 0

	total_matched_amount = sum(code_amounts.values()) or 0.0
	dominant_code, dominant_amount = max(code_amounts.items(), key=lambda row: row[1])
	if total_matched_amount <= 0:
		return 0
	dominance_ratio = dominant_amount / total_matched_amount
	if dominance_ratio < 0.70:
		return 0

	dominant_line_amount = max(
		amount for item, amount in matched_items if item.get("item_code_matched") == dominant_code
	)
	if dominant_line_amount <= 0:
		return 0

	remapped = 0
	amount_threshold = max(250.0, dominant_line_amount * 0.05)

	for item in items:
		if not isinstance(item, dict) or item.get("item_code_matched"):
			continue
		line_amount = abs(_to_float(item.get("amount"), 0.0))
		if line_amount <= 0 or line_amount > amount_threshold:
			continue
		item["item_code_matched"] = dominant_code
		item["item_match_source"] = "small_amount_dominant_fallback"
		item["item_match_confidence"] = round(min(0.74, 0.52 + (dominance_ratio * 0.2)), 4)
		remapped += 1

	return remapped


def _get_item_master_candidate(item_code):
	if not item_code:
		return None

	fields = _get_item_search_return_fields()
	try:
		item_doc = frappe.db.get_value("Item", item_code, fields, as_dict=True)
	except Exception:
		item_doc = None
	if not item_doc:
		return None

	texts = []
	for text in (item_doc.get("item_name"), item_doc.get("description"), item_doc.get("item_code")):
		if text and text not in texts:
			texts.append(text)

	hsn_codes = []
	for code in (item_doc.get("gst_hsn_code"), item_doc.get("hsn_sac_code")):
		if code and code not in hsn_codes:
			hsn_codes.append(code)

	return {
		"item_code": item_doc.get("item_code") or item_doc.get("name"),
		"item_name": item_doc.get("item_name") or item_doc.get("item_code") or item_doc.get("name"),
		"texts": texts,
		"count": 0,
		"weighted_count": 0.0,
		"hsn_sac_codes": hsn_codes,
	}


def _map_unmatched_items_from_item_groups(items, reference_candidates, history_candidates, messages_list, *, invoice_context=None):
	"""
	Category-first + semantic item matching.

	This implements the accountant's "two-pass" approach:
	1) pick the most likely Item Group for the line (semantic)
	2) search items within that group, then choose the best item by weighted evidence

	`invoice_context` (optional) is a short string of enrichment text (e.g. invoice
	notes, expanded acronym) that is appended to short/ambiguous descriptions before
	running the group-picker and semantic ranker so that bare abbreviations like "AMC"
	carry enough signal to land in the right item group and match the right item.

	We only apply this to currently-unmatched rows so we don't destabilize strong matches.
	"""
	if not isinstance(items, list):
		return 0

	unmatched = []
	for item in items:
		if not isinstance(item, dict) or item.get("item_code_matched"):
			continue
		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if len(desc) < 3:
			continue
		unmatched.append(item)

	if not unmatched:
		return 0

	history_lookup = _candidate_lookup(reference_candidates or [], history_candidates or [])
	total_weight = (
		sum(float(candidate.get("weighted_count") or candidate.get("count") or 0.0) for candidate in history_lookup.values())
		or 1.0
	)

	remapped = 0

	ctx_str = str(invoice_context or "").strip()

	for item in unmatched:
		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		item_hsn = _normalize_text(item.get("hsn_sac_code"))

		# For very short / acronym descriptions (≤ 8 chars), enrich the query with
		# the invoice-level context so the group picker and semantic ranker get a
		# meaningful signal (e.g. "AMC" → "AMC Annual Maintenance Contract repair").
		query_desc = f"{desc} {ctx_str}".strip() if ctx_str and len(desc) <= 8 else desc

		group_choices = _pick_item_groups_for_description(query_desc, max_groups=2)
		if not group_choices:
			continue

		# Build a scoped candidate pool: items from top groups + any strong history/catalog candidates.
		candidate_pool = []
		seen_codes = set()

		# Include group-scoped items (primary intent of this feature).
		group_conf_by_group = {}
		in_group_item_groups = set()
		for group_row in group_choices:
			group_name = group_row.get("group")
			group_conf = float(group_row.get("semantic_score") or 0.0)
			if not group_name:
				continue
			group_conf_by_group[group_name] = max(group_conf_by_group.get(group_name, 0.0), group_conf)
			desc_groups = _get_descendant_item_groups(group_name)
			in_group_item_groups.update(desc_groups)
			for row in _get_group_item_candidates(group_name, desc, max_items_full=250):
				code = row.get("item_code") or row.get("name")
				if not code or code in seen_codes:
					continue
				seen_codes.add(code)
				candidate_pool.append(
					{
						"item_code": code,
						"item_name": row.get("item_name") or code,
						"description": row.get("description"),
						"item_group": row.get("item_group"),
						"text": _item_text_for_embedding(
							{
								"item_code": code,
								"item_name": row.get("item_name") or code,
								"description": row.get("description") or "",
							}
						),
					}
				)

		# Include the strongest history candidates (helps when the group routing is imperfect).
		for idx, (code, candidate) in enumerate(history_lookup.items()):
			if idx >= 18:
				break
			if not code or code in seen_codes:
				continue
			master = _get_item_master_candidate(code)
			if not master:
				continue
			seen_codes.add(code)
			candidate_pool.append(
				{
					"item_code": master.get("item_code") or code,
					"item_name": master.get("item_name") or code,
					"description": (master.get("texts") or [""])[0],
					"text": " | ".join(master.get("texts") or [code]),
				}
			)

		# Global catalog shortlist (tiny). This rescues cases where the group routing misses.
		try:
			global_shortlist = execute_smart_search(
				"Item",
				["item_name", "item_code", "description"],
				desc,
				filters={"disabled": 0},
				return_fields=_get_item_search_return_fields(),
				similarity_threshold=0.0,
				limit=8,
			)
		except Exception:
			global_shortlist = []

		for row in global_shortlist or []:
			code = row.get("item_code") or row.get("name")
			if not code or code in seen_codes:
				continue
			seen_codes.add(code)
			candidate_pool.append(
				{
					"item_code": code,
					"item_name": row.get("item_name") or code,
					"description": row.get("description"),
					"item_group": row.get("item_group"),
					"text": _item_text_for_embedding(row),
					"catalog_similarity": float(row.get("similarity_score") or 0.0),
				}
			)

		# Semantic global safety net: when the candidate pool is small it means lexical
		# search found few matches — likely a synonym pair ("lease" / "rent",
		# "repair" / "maintenance") where the item name shares zero characters with the
		# invoice description.  Pull ALL active items (≤ 120) and add any not yet in
		# the pool. semantic_rank will then surface the true match via cosine similarity.
		if len(candidate_pool) < 6:
			try:
				all_items = frappe.get_all(
					"Item",
					filters={"disabled": 0},
					fields=_get_item_search_return_fields(),
					limit_page_length=120,
					order_by="item_name asc",
				)
				for row in all_items or []:
					code = row.get("item_code") or row.get("name")
					if not code or code in seen_codes:
						continue
					seen_codes.add(code)
					candidate_pool.append(
						{
							"item_code": code,
							"item_name": row.get("item_name") or code,
							"description": row.get("description"),
							"item_group": row.get("item_group"),
							"text": _item_text_for_embedding(row),
						}
					)
			except Exception:
				pass

		if not candidate_pool:
			continue

		# Semantic ranking across the pool (this handles synonyms like "lease" vs "rent").
		# Use query_desc (enriched with invoice context for short descriptions) so that
		# bare abbreviations embed close to the correct service item.
		sem_ranked = semantic_rank(query_desc, candidate_pool, text_key="text", top_k=14)
		if not sem_ranked:
			continue

		scored = []
		for cand in sem_ranked:
			code = cand.get("item_code")
			if not code:
				continue

			semantic_score = float(cand.get("semantic_score") or 0.0)
			lexical_score = compute_item_similarity(desc, cand.get("text") or "")

			history_candidate = history_lookup.get(code)
			if not history_candidate:
				history_candidate = history_lookup.get(cand.get("item_code"))
			history_score = _score_item_candidate(
				desc,
				item_hsn,
				history_candidate or _get_item_master_candidate(code),
				total_weight=total_weight,
			)

			in_group = bool(cand.get("item_group") and cand.get("item_group") in in_group_item_groups)
			group_conf = 0.0
			if in_group and group_conf_by_group:
				# Use the strongest selected group confidence as a light bias.
				group_conf = max(group_conf_by_group.values())

			group_bonus = min(0.06, group_conf * 0.06) if in_group else 0.0
			total_score = min(
				1.0,
				(semantic_score * 0.65) + (history_score * 0.25) + (lexical_score * 0.10) + group_bonus,
			)

			scored.append((code, total_score, semantic_score, history_score))

		if not scored:
			continue

		scored.sort(key=lambda row: row[1], reverse=True)
		best_code, best_total, best_sem, best_hist = scored[0]
		second_total = scored[1][1] if len(scored) > 1 else 0.0

		margin = best_total - second_total
		if best_total >= 0.62 and (best_total >= 0.76 or margin >= 0.08) and (best_sem >= 0.60 or best_hist >= 0.74):
			item["item_code_matched"] = best_code
			item["item_match_source"] = "semantic_group_weighted"
			item["item_match_confidence"] = round(best_total, 4)
			remapped += 1

	if remapped:
		messages_list.append(
			f"{remapped} line item(s) matched using category-first semantic item routing."
		)

	return remapped


def _score_item_candidate(desc, item_hsn, candidate, total_weight=1.0):
	if not candidate:
		return 0.0

	candidate_texts = candidate.get("texts") or [candidate.get("item_code")]
	text_score = 0.0
	for text in candidate_texts:
		text_score = max(text_score, _text_similarity(desc, text))

	usage_weight = float(candidate.get("weighted_count") or candidate.get("count") or 0.0)
	usage_boost = min(0.10, usage_weight * 0.012)
	dominance_boost = 0.0
	if total_weight > 0:
		dominance_boost = min(0.08, (usage_weight / total_weight) * 0.25)
	hsn_codes = {_normalize_text(code) for code in (candidate.get("hsn_sac_codes") or []) if code}
	hsn_boost = 0.10 if item_hsn and item_hsn in hsn_codes else 0.0
	return min(1.0, text_score + usage_boost + dominance_boost + hsn_boost)


def _get_catalog_item_candidates(desc, limit=5):
	if not desc:
		return []

	try:
		results = execute_smart_search(
			"Item",
			["item_name", "item_code", "description"],
			desc,
			filters={"disabled": 0},
			return_fields=_get_item_search_return_fields(),
			similarity_threshold=0.0,
			limit=limit,
		)
	except Exception:
		results = []

	candidates = []
	for result in results or []:
		item_code = result.get("item_code") or result.get("name")
		if not item_code:
			continue
		texts = []
		for text in (result.get("item_name"), result.get("description"), item_code):
			if text and text not in texts:
				texts.append(text)
		hsn_codes = []
		for code in (result.get("gst_hsn_code"), result.get("hsn_sac_code")):
			if code and code not in hsn_codes:
				hsn_codes.append(code)
		candidates.append(
			{
				"item_code": item_code,
				"item_name": result.get("item_name") or item_code,
				"texts": texts,
				"count": 0,
				"weighted_count": 0.0,
				"hsn_sac_codes": hsn_codes,
			}
		)

	return candidates


def _candidate_lookup(*candidate_sets):
	lookup = {}
	for candidate_set in candidate_sets:
		for candidate in candidate_set or []:
			item_code = candidate.get("item_code")
			if not item_code or item_code in lookup:
				continue
			lookup[item_code] = candidate
	return lookup


def _revalidate_existing_item_matches(items, candidate_sets, messages_list):
	if not isinstance(items, list):
		return 0

	lookup = _candidate_lookup(*candidate_sets)
	total_weight = sum(
		float(candidate.get("weighted_count") or candidate.get("count") or 0.0)
		for candidate in lookup.values()
	) or 1.0

	overrides = 0
	cleared = 0

	for item in items:
		if not isinstance(item, dict) or not item.get("item_code_matched"):
			continue

		desc = str(item.get("description_extracted") or item.get("description") or "").strip()
		if not desc:
			continue

		item_hsn = _normalize_text(item.get("hsn_sac_code"))
		current_code = item.get("item_code_matched")
		current_candidate = lookup.get(current_code) or _get_item_master_candidate(current_code)
		current_score = _score_item_candidate(desc, item_hsn, current_candidate, total_weight=total_weight)

		pool = dict(lookup)
		pool[current_code] = current_candidate or {"item_code": current_code, "texts": [current_code]}
		for candidate in _get_catalog_item_candidates(desc, limit=5):
			pool.setdefault(candidate.get("item_code"), candidate)

		best_code = current_code
		best_score = current_score
		second_score = 0.0

		for candidate_code, candidate in pool.items():
			candidate_score = _score_item_candidate(desc, item_hsn, candidate, total_weight=total_weight)
			if candidate_score > best_score:
				second_score = best_score
				best_score = candidate_score
				best_code = candidate_code
			elif candidate_code != best_code and candidate_score > second_score:
				second_score = candidate_score

		if (
			best_code
			and best_code != current_code
			and best_score >= max(0.68, current_score + 0.12)
			and (best_score - second_score) >= 0.05
		):
			item["item_code_matched"] = best_code
			item["item_match_source"] = "validated_override"
			item["item_match_confidence"] = round(best_score, 4)
			overrides += 1
			continue

		if current_score < 0.24:
			item["item_code_matched"] = None
			item.pop("item_match_source", None)
			item.pop("item_match_confidence", None)
			cleared += 1

	if overrides:
		messages_list.append(f"{overrides} AI-selected item match(es) were corrected using ERP evidence.")
	if cleared:
		messages_list.append(f"{cleared} weak AI-selected item match(es) were cleared for re-matching.")

	return overrides + cleared


def _to_float(value, default=0.0):
	if value is None:
		return float(default)
	if isinstance(value, (int, float)):
		return float(value)
	text = str(value).strip()
	if not text or text in {"-", "--", "NA", "N/A", "None", "null"}:
		return float(default)
	text = text.replace(",", "").replace("%", "")
	try:
		return float(text)
	except Exception:
		return float(default)


def _collapse_hierarchical_items(parsed_data, messages_list=None):
	"""
	Collapse parent/child breakdown rows where:
	- one parent line amount ~= sum(child lines)
	- child lines are immediately below parent

	This prevents double counting from invoices that show both summary and breakup.
	"""
	items = parsed_data.get("items", []) if isinstance(parsed_data, dict) else []
	if not isinstance(items, list) or len(items) < 3:
		return items

	grand_total = _to_float(parsed_data.get("grand_total"), 0.0)
	items_sum_before = sum(_to_float((row or {}).get("amount"), 0.0) for row in items if isinstance(row, dict))
	removed_indices = set()
	collapsed_groups = []

	for parent_idx in range(len(items) - 2):
		if parent_idx in removed_indices:
			continue

		parent_row = items[parent_idx] if isinstance(items[parent_idx], dict) else {}
		parent_amount = _to_float(parent_row.get("amount"), 0.0)
		parent_abs = abs(parent_amount)
		if parent_abs < 0.5:
			continue

		child_sum = 0.0
		child_idxs = []

		# Look only a few rows ahead for contiguous breakdown lines.
		for child_idx in range(parent_idx + 1, min(parent_idx + 6, len(items))):
			if child_idx in removed_indices:
				break
			child_row = items[child_idx] if isinstance(items[child_idx], dict) else {}
			child_amount = _to_float(child_row.get("amount"), 0.0)
			child_abs = abs(child_amount)

			if child_abs < 0.5:
				continue
			if child_abs > parent_abs * 1.05:
				break

			child_sum += child_abs
			child_idxs.append(child_idx)

			local_tolerance = max(0.75, parent_abs * 0.02)
			if len(child_idxs) >= 2 and abs(child_sum - parent_abs) <= local_tolerance:
				# Decide whether to keep parent or keep children by checking closeness to grand total.
				keep_parent = True
				if grand_total > 0:
					sum_keep_parent = items_sum_before - sum(
						_to_float((items[c] or {}).get("amount"), 0.0) for c in child_idxs
					)
					sum_keep_children = items_sum_before - parent_amount
					delta_parent = abs(sum_keep_parent - grand_total)
					delta_children = abs(sum_keep_children - grand_total)
					if delta_children + 0.25 < delta_parent:
						keep_parent = False

				if keep_parent:
					# Preserve context by appending compact breakdown note to parent description.
					parent_desc = str(parent_row.get("description_extracted") or parent_row.get("description") or "").strip()
					breakdown_bits = []
					for c in child_idxs[:3]:
						row = items[c] if isinstance(items[c], dict) else {}
						desc = str(row.get("description_extracted") or row.get("description") or "").strip()
						amt = _to_float(row.get("amount"), 0.0)
						if desc:
							breakdown_bits.append(f"{desc} ({amt:.2f})")
					if breakdown_bits:
						merged_desc = parent_desc or "Service Line"
						merged_desc = f"{merged_desc} | Breakdown: {'; '.join(breakdown_bits)}"
						parent_row["description_extracted"] = merged_desc

					removed_indices.update(child_idxs)
					collapsed_groups.append((parent_idx, child_idxs))
				else:
					removed_indices.add(parent_idx)
					collapsed_groups.append((child_idxs[0], [parent_idx]))
				break

			if child_sum > parent_abs * 1.12:
				break

	if not removed_indices:
		return items

	new_items = [row for idx, row in enumerate(items) if idx not in removed_indices]
	parsed_data["items"] = new_items

	if messages_list is not None:
		messages_list.append(
			f"Collapsed {len(removed_indices)} hierarchical breakdown row(s) to avoid double counting."
		)

	return new_items


def _apply_common_item_matching(
	items,
	warnings_list,
	messages_list,
	*,
	party_type=None,
	party=None,
	company=None,
	linked_reference_doctype=None,
	linked_reference_name=None,
	invoice_context=None,
):
	"""Apply shared item-matching checks used across trade document doctypes."""
	if not isinstance(items, list):
		items = []

	reference_candidates = []
	if linked_reference_doctype and linked_reference_name:
		reference_candidates = get_document_item_candidates(linked_reference_doctype, linked_reference_name)
		reference_remapped = _map_unmatched_items_from_candidates(items, reference_candidates, min_confidence=0.58)
		if reference_remapped:
			messages_list.append(
				f"{reference_remapped} line item(s) mapped from linked {linked_reference_doctype} history."
			)

	history_candidates = []
	if party_type and party:
		history_candidates = (
			_get_customer_history_item_candidates(party, company=company)
			if str(party_type).lower() == "customer"
			else _get_supplier_history_item_candidates(party, company=company)
		)
		history_remapped = _map_unmatched_items_from_candidates(items, history_candidates, min_confidence=0.64)
		if history_remapped:
			label = "customer" if str(party_type).lower() == "customer" else "supplier"
			messages_list.append(
				f"{history_remapped} line item(s) mapped from previous {label} documents."
			)

	_revalidate_existing_item_matches(items, [reference_candidates, history_candidates], messages_list)

	post_validation_history_remapped = _map_unmatched_items_from_candidates(items, history_candidates, min_confidence=0.64)
	if post_validation_history_remapped:
		label = "customer" if str(party_type).lower() == "customer" else "supplier"
		messages_list.append(
			f"{post_validation_history_remapped} line item(s) remapped from previous {label} documents after validation."
		)

	# Category-first semantic routing: pick an Item Group first, then map within that scope.
	_map_unmatched_items_from_item_groups(items, reference_candidates, history_candidates, messages_list, invoice_context=invoice_context)

	token_remapped = _map_unmatched_items_from_meaningful_tokens(items)
	if token_remapped:
		messages_list.append(
			f"{token_remapped} line item(s) matched using meaningful item-word fallback."
		)

	dominant_remapped = _map_small_unmatched_lines_to_dominant_item(items)
	if dominant_remapped:
		messages_list.append(
			f"{dominant_remapped} small line item(s) were mapped to the dominant matched item."
		)

	matched_items = [i for i in items if isinstance(i, dict) and i.get("item_code_matched")]
	unmatched_items = [i for i in items if isinstance(i, dict) and not i.get("item_code_matched")]

	if matched_items:
		messages_list.append(
			f"{len(matched_items)} of {len(items)} line items matched to ERPNext Item Codes."
		)

	if unmatched_items:
		unmatched_names = [str(i.get("description_extracted", "Unknown")) for i in unmatched_items[:3]]
		warnings_list.append(
			f"{len(unmatched_items)} line item(s) could not be matched: {', '.join(unmatched_names)}. "
			f"They will be added with the raw description."
		)


def _warn_if_double_count(parsed_data, warnings_list):
	items = parsed_data.get("items", [])
	taxes = parsed_data.get("taxes", [])
	grand_total = _to_float(parsed_data.get("grand_total"), 0.0)
	if grand_total <= 0 or not items:
		return

	try:
		items_sum = sum(_to_float((i or {}).get("amount"), 0.0) for i in items if isinstance(i, dict))
		taxes_sum = sum(_to_float((t or {}).get("tax_amount"), 0.0) for t in taxes if isinstance(t, dict))
		calculated_total = items_sum + taxes_sum
		if calculated_total > (grand_total * 1.3):
			warnings_list.append(
				f"ℹ️ HIarchy Warning: The sum of extracted rows ({calculated_total:.2f}) "
				f"is much higher than the Grand Total ({grand_total}). "
				f"This usually happens when both summary and breakdown rows are captured."
			)
	except Exception:
		pass


def _resolve_customer(parsed_data, result, warnings_list, messages_list):
	"""Resolve customer using matched value first, then fuzzy fallback."""
	customer_id = parsed_data.get("customer_id_matched")
	customer_name = parsed_data.get("customer_name_extracted")

	if customer_id:
		result["matches"]["customer"] = customer_id
		confidence_info = f" (extracted: '{customer_name}')" if customer_name and customer_name != customer_id else ""
		messages_list.append(f"Customer matched: {customer_id}{confidence_info}")
		return

	if customer_name:
		try:
			candidates = execute_smart_search(
				"Customer",
				["customer_name", "name"],
				customer_name,
				filters={"disabled": 0},
				return_fields=["name", "customer_name"],
				similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
				limit=3,
			)
			if candidates:
				top, score, _second_score = _pick_best_party_candidate(
					"Customer",
					candidates,
					parsed_data,
					company=result["matches"].get("company"),
				)
				if score >= 0.80:
					result["matches"]["customer"] = top.get("name")
					messages_list.append(
						f"Customer fallback matched: {top.get('name')} (similarity {score:.2f})"
					)
					return
		except Exception:
			pass

		warnings_list.append(
			f"Customer '{customer_name}' was extracted but could not be confidently matched."
		)
	else:
		warnings_list.append("No customer name could be extracted from the document.")


def _perform_supplier_trade_match(parsed_data, target_doctype, result, warnings_list, messages_list):
	"""Shared matching logic for supplier-side trade documents."""
	is_valid = parsed_data.get("is_valid_document", False)
	has_supplier = bool(parsed_data.get("supplier_name_extracted"))
	items = parsed_data.get("items", [])
	has_items = bool(items)
	has_total = bool(parsed_data.get("grand_total"))
	if not is_valid and (has_supplier or has_items or has_total):
		parsed_data["is_valid_document"] = True
		warnings_list.append("AI initially flagged this as invalid, but extracted data was found. Overriding to valid.")

	items = _collapse_hierarchical_items(parsed_data, messages_list)
	taxes = _normalize_tax_rows(parsed_data, warnings_list, messages_list)
	_resolve_tax_accounts(taxes, messages_list)

	company = parsed_data.get("company_matched")
	if company:
		result["matches"]["company"] = company

	supplier_id = parsed_data.get("supplier_id_matched")
	supplier_name = parsed_data.get("supplier_name_extracted")

	if supplier_id:
		result["matches"]["supplier"] = supplier_id
		confidence_info = f" (extracted: '{supplier_name}')" if supplier_name and supplier_name != supplier_id else ""
		messages_list.append(f"Supplier matched: {supplier_id}{confidence_info}")
	else:
		if supplier_name:
			try:
				candidates = execute_smart_search(
					"Supplier",
					["supplier_name", "name"],
					supplier_name,
					filters={"disabled": 0},
					return_fields=["name", "supplier_name"],
					similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
					limit=3,
				)
				if candidates:
					top, score, _second_score = _pick_best_party_candidate(
						"Supplier",
						candidates,
						parsed_data,
						company=result["matches"].get("company"),
					)
					if score >= 0.80:
						result["matches"]["supplier"] = top.get("name")
						messages_list.append(
							f"Supplier fallback matched: {top.get('name')} (similarity {score:.2f})"
						)
			except Exception:
				pass
		if not result["matches"]["supplier"]:
			warnings_list.append(
				f"Supplier '{supplier_name}' could not be matched. Please set supplier manually."
				if supplier_name else "No supplier name could be extracted from the document."
			)

	tax_template = parsed_data.get("taxes_and_charges_template")
	if tax_template and not taxes and _template_matches_document_tax(target_doctype, tax_template, parsed_data):
		result["matches"]["taxes_and_charges"] = tax_template
		messages_list.append(f"Verified tax template: {tax_template}")
	if parsed_data.get("payment_terms_template"):
		result["matches"]["payment_terms_template"] = parsed_data.get("payment_terms_template")

	po_ref = parsed_data.get("po_reference_matched")
	if po_ref:
		result["matches"]["purchase_order"] = po_ref
		messages_list.append(f"Purchase Order linked: {po_ref}")
	elif parsed_data.get("po_reference_extracted"):
		warnings_list.append(
			f"PO reference '{parsed_data.get('po_reference_extracted')}' was extracted but not matched."
		)

	# Duplicate check only for Purchase Invoice
	if target_doctype == "Purchase Invoice":
		invoice_number = parsed_data.get("invoice_number") or parsed_data.get("document_number")
		supplier_for_dup = result["matches"].get("supplier")
		if invoice_number and supplier_for_dup:
			dup_check = _check_duplicate_invoice(invoice_number, supplier_for_dup)
			if dup_check.get("is_duplicate"):
				result["is_duplicate"] = True
				result["duplicate_invoice_id"] = dup_check["duplicate_invoice_id"]
				result["duplicate_status"] = dup_check.get("duplicate_status")
				result["note"] = dup_check.get("note")
				warnings_list.append(
					dup_check.get("note") or
					f"⚠️ Possible Duplicate! Invoice '{invoice_number}' already exists for {supplier_for_dup} "
					f"as {dup_check['duplicate_invoice_id']}."
				)

	catalog_remapped = _map_unmatched_items_from_catalog(items, min_confidence=0.84)
	if catalog_remapped:
		messages_list.append(
			f"{catalog_remapped} line item(s) matched from the ERPNext item catalog."
		)

	_apply_common_item_matching(
		items,
		warnings_list,
		messages_list,
		party_type="Supplier",
		party=result["matches"].get("supplier"),
		company=result["matches"].get("company"),
		linked_reference_doctype="Purchase Order" if po_ref else None,
		linked_reference_name=po_ref,
		invoice_context=str(parsed_data.get("notes") or "").strip() or None,
	)
	_warn_if_double_count(parsed_data, warnings_list)


def _perform_customer_trade_match(parsed_data, target_doctype, result, warnings_list, messages_list):
	"""Shared matching logic for customer-side trade documents."""
	is_valid = parsed_data.get("is_valid_document", False)
	has_customer = bool(parsed_data.get("customer_name_extracted"))
	items = parsed_data.get("items", [])
	has_items = bool(items)
	has_total = bool(parsed_data.get("grand_total"))
	if not is_valid and (has_customer or has_items or has_total):
		parsed_data["is_valid_document"] = True
		warnings_list.append("AI initially flagged this as invalid, but extracted data was found. Overriding to valid.")

	items = _collapse_hierarchical_items(parsed_data, messages_list)
	taxes = _normalize_tax_rows(parsed_data, warnings_list, messages_list)
	_resolve_tax_accounts(taxes, messages_list)

	company = parsed_data.get("company_matched")
	if company:
		result["matches"]["company"] = company

	_resolve_customer(parsed_data, result, warnings_list, messages_list)

	tax_template = parsed_data.get("taxes_and_charges_template")
	if tax_template and not taxes and _template_matches_document_tax(target_doctype, tax_template, parsed_data):
		result["matches"]["taxes_and_charges"] = tax_template
		messages_list.append(f"Verified tax template: {tax_template}")
	if parsed_data.get("payment_terms_template"):
		result["matches"]["payment_terms_template"] = parsed_data.get("payment_terms_template")

	so_ref = parsed_data.get("sales_order_reference_matched")
	if so_ref:
		result["matches"]["sales_order"] = so_ref
		messages_list.append(f"Sales Order linked: {so_ref}")

	dn_ref = parsed_data.get("delivery_note_reference_matched")
	if dn_ref:
		result["matches"]["delivery_note"] = dn_ref
		messages_list.append(f"Delivery Note linked: {dn_ref}")

	catalog_remapped = _map_unmatched_items_from_catalog(items, min_confidence=0.84)
	if catalog_remapped:
		messages_list.append(
			f"{catalog_remapped} line item(s) matched from the ERPNext item catalog."
		)

	linked_reference_doctype = None
	linked_reference_name = None
	if so_ref:
		linked_reference_doctype = "Sales Order"
		linked_reference_name = so_ref
	elif dn_ref:
		linked_reference_doctype = "Delivery Note"
		linked_reference_name = dn_ref

	_apply_common_item_matching(
		items,
		warnings_list,
		messages_list,
		party_type="Customer",
		party=result["matches"].get("customer"),
		company=result["matches"].get("company"),
		linked_reference_doctype=linked_reference_doctype,
		linked_reference_name=linked_reference_name,
		invoice_context=str(parsed_data.get("notes") or "").strip() or None,
	)
	_warn_if_double_count(parsed_data, warnings_list)


def _perform_payment_entry_match(parsed_data, result, warnings_list, messages_list):
	"""Matching logic for Payment Entry extraction."""
	company = parsed_data.get("company_matched")
	if company:
		result["matches"]["company"] = company

	party_type = str(parsed_data.get("party_type") or "").strip().title()
	if party_type not in {"Supplier", "Customer"}:
		party_type = "Supplier" if parsed_data.get("payment_type") == "Pay" else "Customer"
		if parsed_data.get("payment_type") not in {"Pay", "Receive"}:
			party_type = None

	party_id = parsed_data.get("party_id_matched")
	party_name = parsed_data.get("party_name_extracted")

	if not party_id and party_name and party_type in {"Supplier", "Customer"}:
		try:
			party_doctype = "Supplier" if party_type == "Supplier" else "Customer"
			search_fields = ["supplier_name", "name"] if party_type == "Supplier" else ["customer_name", "name"]
			candidates = execute_smart_search(
				party_doctype,
				search_fields,
				party_name,
				filters={"disabled": 0},
				return_fields=["name"],
				similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
				limit=3,
			)
			if candidates:
				top, score, _second_score = _pick_best_party_candidate(
					party_type,
					candidates,
					parsed_data,
					company=company,
				)
				if score >= 0.80:
					party_id = top.get("name")
		except Exception:
			pass

	if party_type:
		result["matches"]["party_type"] = party_type
	if party_id:
		result["matches"]["party"] = party_id
		messages_list.append(f"Payment party matched: {party_type or 'Party'} {party_id}")
	else:
		warnings_list.append("Could not confidently match payment party. Please verify party before saving.")

	if parsed_data.get("paid_from_account_matched"):
		result["matches"]["paid_from"] = parsed_data.get("paid_from_account_matched")
	if parsed_data.get("paid_to_account_matched"):
		result["matches"]["paid_to"] = parsed_data.get("paid_to_account_matched")
	if parsed_data.get("mode_of_payment_matched"):
		result["matches"]["mode_of_payment"] = parsed_data.get("mode_of_payment_matched")

	is_valid = parsed_data.get("is_valid_document", False)
	has_amount = bool(_to_float(parsed_data.get("paid_amount"), 0.0) or _to_float(parsed_data.get("received_amount"), 0.0))
	if not is_valid and (has_amount or party_name):
		parsed_data["is_valid_document"] = True
		warnings_list.append("AI initially flagged this as invalid, but payment signals were found. Overriding to valid.")


def perform_smart_match(parsed_data, target_doctype="Purchase Invoice"):
	"""
	Post-processes the Agent's extracted + matched data for the frontend.
	- Validates matches
	- Generates warnings and success messages
	- Handles the NEVER-GIVE-UP override for is_valid_document
	- Returns structured result for the UI
	"""
	if not isinstance(parsed_data, dict):
		parsed_data = {}

	result = {
		"matches": {
			"supplier": None,
			"customer": None,
			"purchase_order": None,
			"purchase_receipts": [],
			"sales_order": None,
			"delivery_note": None,
			"company": None,
			"taxes_and_charges": None,
			"payment_terms_template": None,
			"party_type": None,
			"party": None,
			"paid_from": None,
			"paid_to": None,
			"mode_of_payment": None,
		},
		"warnings": [],
		"messages": [],
		"is_duplicate": False,
		"duplicate_invoice_id": None,
		"duplicate_status": None,
		"note": None
	}

	warnings_list = result["warnings"] # Local reference for type safety
	messages_list = result["messages"]

	if target_doctype == "Payment Entry":
		_perform_payment_entry_match(parsed_data, result, warnings_list, messages_list)
		return result

	if target_doctype in SUPPLIER_SIDE_DOCTYPES and target_doctype != "Purchase Invoice":
		_perform_supplier_trade_match(parsed_data, target_doctype, result, warnings_list, messages_list)
		return result

	if target_doctype in CUSTOMER_SIDE_DOCTYPES:
		_perform_customer_trade_match(parsed_data, target_doctype, result, warnings_list, messages_list)
		return result

	if target_doctype != "Purchase Invoice":
		return result

	# ──────────────────────────────────────
	# NEVER-GIVE-UP Override
	# ──────────────────────────────────────
	# If the AI said is_valid_document = false but we have meaningful data,
	# override it because the AI was too conservative
	is_valid = parsed_data.get("is_valid_document", False)
	has_supplier = bool(parsed_data.get("supplier_name_extracted"))
	items = parsed_data.get("items", [])
	has_items = bool(items) and len(items) > 0
	has_total = bool(parsed_data.get("grand_total"))

	if not is_valid and (has_supplier or has_items or has_total):
		parsed_data["is_valid_document"] = True
		warnings_list.append(
			"AI initially flagged this as invalid, but extracted data was found. Overriding to valid."
		)

	# Pre-clean: collapse parent/breakdown duplicate item rows from table hierarchy.
	items = _collapse_hierarchical_items(parsed_data, messages_list)
	taxes = _normalize_tax_rows(parsed_data, warnings_list, messages_list)
	_resolve_tax_accounts(taxes, messages_list)

	# ──────────────────────────────────────
	# 1. Company
	# ──────────────────────────────────────
	company = parsed_data.get("company_matched")
	if company:
		result["matches"]["company"] = company

	# ──────────────────────────────────────
	# 2. Supplier Processing
	# ──────────────────────────────────────
	supplier_id = parsed_data.get("supplier_id_matched")
	supplier_name = parsed_data.get("supplier_name_extracted")
	supplier_gstin = _normalize_gstin(parsed_data.get("supplier_gstin_extracted"))
	invoice_number = parsed_data.get("invoice_number")
	po_id = parsed_data.get("po_reference_matched")
	company_tokens = _get_company_identity_tokens(company)
	company_tax_ids = _get_company_tax_ids()
	supplier_looks_like_company = _looks_like_company_name(supplier_name, company_tokens)

	if supplier_id and not _looks_like_company_name(supplier_id, company_tokens):
		result["matches"]["supplier"] = supplier_id
		confidence_info = ""
		# Check if the AI returned confidence info
		if supplier_name and supplier_id != supplier_name:
			confidence_info = f" (extracted: '{supplier_name}')"
		messages_list.append(f"Supplier matched: {supplier_id}{confidence_info}")

		# Duplicate check
		if invoice_number:
			dup_check = _check_duplicate_invoice(invoice_number, supplier_id)
			if dup_check.get("is_duplicate"):
				result["is_duplicate"] = True
				result["duplicate_invoice_id"] = dup_check["duplicate_invoice_id"]
				result["duplicate_status"] = dup_check.get("duplicate_status")
				result["note"] = dup_check.get("note")
				warnings_list.append(
					dup_check.get("note") or
					f"⚠️ Possible Duplicate! Invoice '{invoice_number}' already exists for {supplier_id} "
					f"as {dup_check['duplicate_invoice_id']}."
				)
	else:
		fallback_supplier = None
		if supplier_id and _looks_like_company_name(supplier_id, company_tokens):
			warnings_list.append(
				"Extracted supplier matched your own company context. Ignoring it and retrying supplier identification."
			)

		# Fallback 0: PO-linked supplier is the strongest signal if PO is matched.
		if po_id:
			try:
				po_supplier = frappe.db.get_value("Purchase Order", po_id, "supplier")
				if po_supplier:
					fallback_supplier = po_supplier
			except Exception:
				pass

		# Fallback 0.5: Exact GSTIN match, but ignore own-company GSTIN.
		if not fallback_supplier and supplier_gstin:
			if supplier_gstin in company_tax_ids:
				warnings_list.append(
					f"Extracted GSTIN '{supplier_gstin}' belongs to your own company context. Looking for alternate supplier signals."
				)
			else:
				fallback_supplier = _find_supplier_by_tax_id(supplier_gstin)

		# Fallback 1: If same bill_no already exists in exactly one supplier context, reuse that supplier.
		if not fallback_supplier and invoice_number:
			existing = frappe.get_all(
				"Purchase Invoice",
				filters={"bill_no": str(invoice_number).strip(), "docstatus": ["<", 2]},
				fields=["supplier"],
				order_by="creation desc",
				limit=5
			)
			unique_suppliers = []
			for row in existing:
				s = row.get("supplier")
				if s and s not in unique_suppliers:
					unique_suppliers.append(s)
			if len(unique_suppliers) == 1:
				fallback_supplier = unique_suppliers[0]

		# Fallback 2: High-confidence fuzzy supplier match.
		if not fallback_supplier and supplier_name and not supplier_looks_like_company:
			try:
				supplier_candidates = execute_smart_search(
					"Supplier",
					["supplier_name", "name"],
					supplier_name,
					filters={"disabled": 0},
					return_fields=["name", "supplier_name"],
					similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
					limit=3
				)
				if supplier_candidates:
					top, score, second_score = _pick_best_party_candidate(
						"Supplier",
						supplier_candidates,
						parsed_data,
						company=company,
					)
					if score >= 0.80 and not _looks_like_company_name(top.get("name"), company_tokens):
						fallback_supplier = top.get("name")
			except Exception:
				pass

		if fallback_supplier:
			result["matches"]["supplier"] = fallback_supplier
			messages_list.append(f"Supplier fallback matched: {fallback_supplier}")

			if invoice_number:
				dup_check = _check_duplicate_invoice(invoice_number, fallback_supplier)
				if dup_check.get("is_duplicate"):
					result["is_duplicate"] = True
					result["duplicate_invoice_id"] = dup_check["duplicate_invoice_id"]
					result["duplicate_status"] = dup_check.get("duplicate_status")
					result["note"] = dup_check.get("note")
					warnings_list.append(
						dup_check.get("note") or
						f"⚠️ Possible Duplicate! Invoice '{invoice_number}' already exists for {fallback_supplier} "
						f"as {dup_check['duplicate_invoice_id']}."
					)
		elif supplier_name and supplier_looks_like_company:
			warnings_list.append(
				f"Supplier '{supplier_name}' appears to be your own company header (Bill To/Ship To). "
				f"Could not confidently identify the vendor. Please set supplier manually."
			)
		elif supplier_name:
			warnings_list.append(
				f"Supplier '{supplier_name}' was extracted from the document but could not be matched "
				f"to any Supplier in ERPNext. Please create or link one manually."
			)
		else:
			warnings_list.append(
				"No supplier name could be extracted from the document."
			)

	# ──────────────────────────────────────
	# 3. Tax Template
	# ──────────────────────────────────────
	tax_template = parsed_data.get("taxes_and_charges_template")
	if tax_template and not taxes and _template_matches_document_tax(target_doctype, tax_template, parsed_data):
		result["matches"]["taxes_and_charges"] = tax_template
		messages_list.append(f"Verified tax template: {tax_template}")

	# ──────────────────────────────────────
	# 4. Payment Terms
	# ──────────────────────────────────────
	payment_terms = parsed_data.get("payment_terms_template")
	if payment_terms:
		result["matches"]["payment_terms_template"] = payment_terms
		messages_list.append(f"Payment terms: {payment_terms}")

	# ──────────────────────────────────────
	# 5. Purchase Order Processing
	# ──────────────────────────────────────
	po_id = parsed_data.get("po_reference_matched")
	po_extracted = parsed_data.get("po_reference_extracted")

	if po_id:
		result["matches"]["purchase_order"] = po_id
		messages_list.append(f"Purchase Order linked: {po_id}")

		try:
			po_doc = frappe.get_doc("Purchase Order", po_id)

			# Amount mismatch check
			po_total = po_doc.grand_total
			inv_total = parsed_data.get("grand_total")
			if po_total and inv_total and abs(po_total - inv_total) > 1.0:
				warnings_list.append(
					f"Amount mismatch — PO total: {po_total}, Invoice total: {inv_total}."
				)

			# Check for receipts
			prs = _find_purchase_receipt(po_id)
			if prs:
				result["matches"]["purchase_receipts"] = prs
				messages_list.append(f"Found {len(prs)} linked Purchase Receipt(s).")
			else:
				warnings_list.append(f"No submitted Purchase Receipts found for PO {po_id}.")
		except Exception:
			pass
	elif po_extracted:
		warnings_list.append(
			f"PO reference '{po_extracted}' was extracted but could not be matched to any open Purchase Order."
		)

	# ──────────────────────────────────────
	# 6. Item Processing
	# ──────────────────────────────────────
	catalog_remapped = _map_unmatched_items_from_catalog(items, min_confidence=0.84)
	if catalog_remapped:
		messages_list.append(
			f"{catalog_remapped} line item(s) matched from the ERPNext item catalog."
		)

	# Use the invoice notes field as enrichment context for short/acronym item descriptions.
	invoice_context = str(parsed_data.get("notes") or "").strip() or None

	_apply_common_item_matching(
		items,
		warnings_list,
		messages_list,
		party_type="Supplier",
		party=result["matches"].get("supplier"),
		company=result["matches"].get("company"),
		linked_reference_doctype="Purchase Order" if result["matches"].get("purchase_order") else None,
		linked_reference_name=result["matches"].get("purchase_order") or parsed_data.get("po_reference_matched"),
		invoice_context=invoice_context,
	)

	# ──────────────────────────────────────
	# 7. Tax Row Validation
	# ──────────────────────────────────────
	taxes = parsed_data.get("taxes", [])
	unmatched_taxes = [t for t in taxes if not t.get("account_head_matched")]
	if unmatched_taxes:
		tax_labels = [str(t.get("tax_type_extracted", "Unknown")) for t in unmatched_taxes]
		warnings_list.append(
			f"Tax account(s) not matched: {', '.join(tax_labels)}. "
			f"Please verify the tax accounts before saving."
		)

	# ──────────────────────────────────────
	# 8. Double Counting Detection (Safety)
	# ──────────────────────────────────────
	grand_total = parsed_data.get("grand_total")
	if grand_total and items:
		try:
			items_sum = sum(float(i.get("amount", 0)) for i in items)
			taxes_sum = sum(float(t.get("tax_amount", 0)) for t in taxes)
			calculated_total = items_sum + taxes_sum

			# If calculated sum is significantly higher than printed Grand Total (~1.3x or more)
			# it indicates both parent summary + breakdown rows were likely extracted.
			if calculated_total > (float(grand_total) * 1.3):
				warnings_list.append(
					f"ℹ️ HIarchy Warning: The sum of extracted rows ({calculated_total:.2f}) "
					f"is much higher than the Grand Total ({grand_total}). "
					f"This usually happens when both summary and breakdown rows are captured. "
					f"Please remove either the summary row or the breakdown rows before saving."
				)
		except (ValueError, TypeError):
			pass

	# ──────────────────────────────────────
	# 9. Duplicate Invoice Detection
	# ──────────────────────────────────────
	if parsed_data.get("is_duplicate") and not result["is_duplicate"]:
		result["is_duplicate"] = True
		result["duplicate_invoice_id"] = str(parsed_data.get("duplicate_invoice_id"))
		warnings_list.append(f"⚠️ Duplicate Check: This invoice number already exists in ERPNext ({result['duplicate_invoice_id']}).")

	return result
