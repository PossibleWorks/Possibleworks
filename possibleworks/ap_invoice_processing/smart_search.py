# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import re
import frappe
from difflib import SequenceMatcher


# Similarity thresholds
SIMILARITY_THRESHOLD_PARTY = 0.55   # Supplier / Customer names
SIMILARITY_THRESHOLD_ITEM = 0.50    # Item names / descriptions
SIMILARITY_THRESHOLD_DEFAULT = 0.55 # Everything else

GENERIC_STOPWORDS = {
	"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into",
	"is", "it", "of", "on", "or", "that", "the", "their", "this", "to", "with",
}

def _basic_similarity_ratio(a, b):
	"""Compute normalized similarity between two strings using SequenceMatcher."""
	if not a or not b:
		return 0.0
	return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _normalize_similarity_text(value):
	if value is None:
		return ""
	text = re.sub(r"[^a-z0-9]+", " ", str(value).lower())
	return re.sub(r"\s+", " ", text).strip()


def _tokenize_similarity_text(value):
	text = _normalize_similarity_text(value)
	if not text:
		return []
	return [token for token in text.split() if token]


def _stem_token(token):
	token = str(token or "").strip().lower()
	if len(token) <= 3:
		return token

	suffix_rules = (
		("ization", "ize", 8),
		("ational", "ate", 8),
		("fulness", "ful", 7),
		("ousness", "ous", 7),
		("iveness", "ive", 7),
		("tional", "tion", 7),
		("biliti", "ble", 7),
		("lessly", "less", 7),
		("ically", "ic", 7),
		("ancies", "ancy", 7),
		("encies", "ency", 7),
		("ingly", "", 6),
		("edly", "", 6),
		("ation", "ate", 6),
		("ment", "", 6),
		("ancy", "ant", 6),
		("ency", "ent", 6),
		("ings", "", 6),
		("ing", "", 5),
		("ized", "ize", 5),
		("izer", "ize", 5),
		("ised", "ise", 5),
		("ers", "er", 5),
		("ies", "y", 5),
		("ied", "y", 5),
		("ants", "ant", 5),
		("ents", "ent", 5),
		("ors", "or", 5),
		("ers", "er", 5),
		("ed", "", 4),
		("es", "", 4),
		("s", "", 4),
	)

	for suffix, replacement, minimum_length in suffix_rules:
		if token.endswith(suffix) and len(token) >= minimum_length:
			candidate = f"{token[:-len(suffix)]}{replacement}"
			if len(candidate) >= 3:
				return candidate

	return token


def extract_meaningful_tokens(value):
	# Preserve uppercase abbreviations (e.g. "AMC", "AC", "IT") from the original
	# before lowercasing so they survive the length filter below.
	original_upper_abbrevs = {
		tok.lower()
		for tok in str(value or "").split()
		if tok.isupper() and len(tok) >= 2
	}
	tokens = []
	for token in _tokenize_similarity_text(value):
		if token in original_upper_abbrevs:
			# Keep abbreviation regardless of length (e.g. "amc", "ac")
			pass
		elif len(token) < 4:
			continue
		if token.isdigit():
			continue
		if token in GENERIC_STOPWORDS:
			continue
		tokens.append(token)
	return list(dict.fromkeys(tokens))


def _char_ngram_set(value, size=3):
	text = _normalize_similarity_text(value).replace(" ", "")
	if not text:
		return set()
	if len(text) <= size:
		return {text}
	return {text[idx : idx + size] for idx in range(len(text) - size + 1)}


def _item_signature_key(value):
	tokens = _tokenize_similarity_text(value)
	stems = sorted({_stem_token(token) for token in tokens if token})
	return " ".join(stems)


def compute_item_similarity(a, b):
	tokens_a = _tokenize_similarity_text(a)
	tokens_b = _tokenize_similarity_text(b)
	if not tokens_a or not tokens_b:
		return 0.0

	stems_a = {_stem_token(token) for token in tokens_a if token}
	stems_b = {_stem_token(token) for token in tokens_b if token}
	if not stems_a or not stems_b:
		return 0.0

	base = _basic_similarity_ratio(_normalize_similarity_text(a), _normalize_similarity_text(b))
	token_overlap = len(stems_a & stems_b) / float(max(len(stems_a), len(stems_b)))
	sorted_ratio = _basic_similarity_ratio(" ".join(sorted(stems_a)), " ".join(sorted(stems_b)))
	char_ngrams_a = _char_ngram_set(a)
	char_ngrams_b = _char_ngram_set(b)
	char_overlap = 0.0
	if char_ngrams_a and char_ngrams_b:
		char_overlap = len(char_ngrams_a & char_ngrams_b) / float(max(len(char_ngrams_a), len(char_ngrams_b)))

	blended = (base * 0.4) + (sorted_ratio * 0.25) + (token_overlap * 0.2) + (char_overlap * 0.15)
	subset_match = 0.0
	if (stems_a <= stems_b or stems_b <= stems_a) and len(stems_a & stems_b) >= 2:
		subset_match = 0.78

	return min(1.0, max(base, token_overlap, sorted_ratio, char_overlap, blended, subset_match))


def _similarity_ratio(a, b, semantic_mode=None):
	base = _basic_similarity_ratio(a, b)
	if semantic_mode == "item":
		return max(base, compute_item_similarity(a, b))
	return base


def execute_smart_search(doctype, search_fields, search_term, filters=None,
						 return_fields=None, similarity_threshold=None, limit=3):
	"""
	Production-grade 5-step reduction search algorithm against a Frappe DocType.

	Algorithm:
	1. Exact match (Full text)           → confidence: "exact"
	2. Case-insensitive LIKE match       → confidence: "high"
	3. Suffix/Prefix strip match         → confidence: "high"
	4. Split word reduction match        → confidence: "medium"
	5. Fuzzy similarity match (NEW)      → confidence: "fuzzy"

	Returns: list of dicts with requested fields + 'confidence' + 'similarity_score'.
	"""
	if not search_term or not isinstance(search_term, str):
		return []

	if return_fields is None:
		return_fields = ["name"]

	search_term = search_term.strip()
	if not search_term:
		return []

	filters = filters or {}
	threshold = similarity_threshold or SIMILARITY_THRESHOLD_DEFAULT
	semantic_mode = "item" if doctype == "Item" else None

	# Internal helper to construct and run the OR query across all search_fields
	def _run_query(term, exact=False, query_limit=None):
		ql = query_limit or limit
		conditions = []
		values = {}

		# Add base filters (handle both dict and list-style filters)
		if isinstance(filters, dict):
			for k, v in filters.items():
				if isinstance(v, (list, tuple)):
					# e.g. {"status": ["not in", ["Closed", "Cancelled"]]}
					conditions.append(f"`{k}` {v[0]} %({k})s")
					values[k] = v[1] if isinstance(v[1], str) else tuple(v[1])
				else:
					conditions.append(f"`{k}` = %({k})s")
					values[k] = v

		# Add OR conditions for search fields
		or_conds = []
		idx = 0
		for field in search_fields:
			val_key = f"search_val_{idx}"
			if exact:
				or_conds.append(f"`{field}` = %({val_key})s")
				values[val_key] = term
			else:
				or_conds.append(f"`{field}` LIKE %({val_key})s")
				values[val_key] = f"%{term}%"
			idx += 1

		if or_conds:
			conditions.append("(" + " OR ".join(or_conds) + ")")

		where_clause = " AND ".join(conditions) if conditions else "1=1"

		# Ensure 'name' is always in return fields for deduplication
		all_fields = list(set(return_fields + ["name"]))
		fields_str = ", ".join([f"`{f}`" for f in all_fields])

		sql = f"""
			SELECT {fields_str}
			FROM `tab{doctype}`
			WHERE {where_clause}
			LIMIT {ql}
		"""
		try:
			return frappe.db.sql(sql, values, as_dict=True)
		except Exception as e:
			frappe.log_error("Smart Search SQL Error", str(e))
			return []

	# ==========================================
	# Step 1: Exact Match (confidence: "exact")
	# ==========================================
	results = _run_query(search_term, exact=True)
	if results:
		return _format_results(results, "exact", 1.0)

	# ==========================================
	# Step 2: Case-Insensitive LIKE Match (confidence: "high")
	# ==========================================
	results = _run_query(search_term, exact=False)
	if results:
		# Score each result for relevance
		scored = []
		for r in results:
			best_score = 0
			for field in search_fields:
				val = r.get(field, "")
				if val:
					score = _similarity_ratio(search_term, str(val), semantic_mode=semantic_mode)
					best_score = max(best_score, score)
			scored.append((r, best_score))
		scored.sort(key=lambda x: x[1], reverse=True)
		return _format_results([s[0] for s in scored], "high", scored[0][1] if scored else 0.9)

	# ==========================================
	# Step 3: Suffix/Prefix Strip Match (confidence: "high")
	# ==========================================
	stopwords = [
		r"\b(ltd|limited)\b",
		r"\b(pvt|private)\b",
		r"\b(inc|incorporated)\b",
		r"\b(llp)\b",
		r"\b(& co|and co)\b",
		r"\b(technologies|tech)\b",
		r"\b(services|service)\b",
		r"\b(corp|corporation)\b",
		r"\b(llc)\b",
		r"\b(india|international)\b",
		r"\b(solutions)\b",
		r"\b(enterprises|enterprise)\b",
		r"\b(industries|industrial)\b",
		r"\b(company)\b",
		r"\b(group)\b",
	]

	stripped_term = search_term.lower()
	for pattern in stopwords:
		stripped_term = re.sub(pattern, "", stripped_term, flags=re.IGNORECASE).strip()

	# Clean up double spaces or punctuation left behind
	stripped_term = re.sub(r"[^\w\s]", " ", stripped_term)
	stripped_term = re.sub(r"\s+", " ", stripped_term).strip()

	if stripped_term and stripped_term != search_term.lower():
		results = _run_query(stripped_term, exact=False)
		if results:
			scored = []
			for r in results:
				best_score = 0
				for field in search_fields:
					val = r.get(field, "")
					if val:
						score = _similarity_ratio(search_term, str(val), semantic_mode=semantic_mode)
						best_score = max(best_score, score)
				scored.append((r, best_score))
			scored.sort(key=lambda x: x[1], reverse=True)
			return _format_results([s[0] for s in scored], "high", scored[0][1] if scored else 0.8)

	# ==========================================
	# Step 4: Split Word Reduction Match (confidence: "medium")
	# ==========================================
	words = stripped_term.split() if stripped_term else search_term.split()

	while len(words) > 1:
		words.pop()
		reduced_term = " ".join(words).strip()

		if len(reduced_term) <= 2:
			continue

		results = _run_query(reduced_term, exact=False)
		if results:
			scored = []
			for r in results:
				best_score = 0
				for field in search_fields:
					val = r.get(field, "")
					if val:
						score = _similarity_ratio(search_term, str(val), semantic_mode=semantic_mode)
						best_score = max(best_score, score)
				scored.append((r, best_score))
			scored.sort(key=lambda x: x[1], reverse=True)
			return _format_results([s[0] for s in scored], "medium", scored[0][1] if scored else 0.6)

	# ==========================================
	# Step 5: Fuzzy Similarity Match (confidence: "fuzzy")
	# Fetch a broad set of records, compute similarity scores,
	# return the best match above threshold.
	# ==========================================
	try:
		# Fetch a broad set of records to compare against
		all_fields = list(set(return_fields + ["name"] + search_fields))
		fields_str = ", ".join([f"`{f}`" for f in all_fields])

		# Build base filter conditions
		conditions = []
		values = {}
		if isinstance(filters, dict):
			for k, v in filters.items():
				if isinstance(v, (list, tuple)):
					conditions.append(f"`{k}` {v[0]} %({k})s")
					values[k] = v[1] if isinstance(v[1], str) else tuple(v[1])
				else:
					conditions.append(f"`{k}` = %({k})s")
					values[k] = v

		where_clause = " AND ".join(conditions) if conditions else "1=1"

		sql = f"""
			SELECT {fields_str}
			FROM `tab{doctype}`
			WHERE {where_clause}
			LIMIT 100
		"""
		all_records = frappe.db.sql(sql, values, as_dict=True)

		if not all_records:
			return []

		# Score every record against the search term
		scored_records = []
		search_lower = search_term.lower().strip()

		for record in all_records:
			best_score = 0
			best_field = None
			for field in search_fields:
				val = record.get(field)
				if val:
					val_str = str(val).lower().strip()
					score = _similarity_ratio(search_lower, val_str, semantic_mode=semantic_mode)

					# Bonus: if search term is fully contained in the field value (or vice versa)
					if search_lower in val_str or val_str in search_lower:
						score = max(score, 0.75)
					elif semantic_mode == "item":
						signature_search = _item_signature_key(search_lower)
						signature_val = _item_signature_key(val_str)
						if signature_search and signature_val and (
							signature_search in signature_val or signature_val in signature_search
						):
							score = max(score, 0.80)

					if score > best_score:
						best_score = score
						best_field = field

			if best_score >= threshold:
				scored_records.append((record, best_score, best_field))

		if not scored_records:
			return []

		# Sort by score descending
		scored_records.sort(key=lambda x: x[1], reverse=True)

		# Return top matches
		top = scored_records[:limit]
		results = []
		for record, score, matched_field in top:
			record["confidence"] = "fuzzy"
			record["similarity_score"] = round(score, 3)
			record["matched_field"] = matched_field
			results.append(record)

		return results

	except Exception as e:
		frappe.log_error("Smart Search Fuzzy Error", str(e))
		return []


def _format_results(results, confidence, top_score=None):
	"""Attaches confidence score and similarity_score to results."""
	formatted = []
	for r in results:
		r["confidence"] = confidence
		r["similarity_score"] = round(top_score, 3) if top_score else 1.0
		formatted.append(r)
	return formatted
