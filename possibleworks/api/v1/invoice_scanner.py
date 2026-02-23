# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
AI-powered invoice scanning API.

Accepts a file URL (PDF, JPG, PNG attached to a Purchase Invoice),
sends it to OpenAI GPT-4o for structured data extraction, then
resolves supplier/item master data with smart matching before
returning form-ready JSON.
"""

import base64
import json
import re

import frappe
from frappe import _


# ──────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def scan_invoice(file_url: str) -> dict:
	"""Extract invoice data from an uploaded file using OpenAI vision.

	Args:
		file_url: The Frappe file URL (e.g. /private/files/invoice.pdf)

	Returns:
		dict with extracted and resolved invoice data ready for form population.
	"""
	_validate_file_url(file_url)
	settings = _get_ai_settings()
	image_list = _read_file(file_url)

	raw_data = _call_openai(settings, image_list)

	# Resolve master data (supplier + items) against existing records
	resolved = _resolve_master_data(raw_data, settings)

	return resolved


# ──────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _validate_file_url(file_url: str):
	"""Ensure the file URL is present and has an allowed extension."""
	if not file_url:
		frappe.throw(_("Please upload an invoice file first."))

	ext = "." + file_url.rsplit(".", 1)[-1].lower() if "." in file_url else ""
	if ext not in ALLOWED_EXTENSIONS:
		frappe.throw(
			_("Unsupported file type '{0}'. Allowed: PDF, JPG, PNG").format(ext)
		)


# ──────────────────────────────────────────────────────────────────
# Settings
# ──────────────────────────────────────────────────────────────────

def _get_ai_settings():
	"""Load PW AI Settings and validate the API key is configured."""
	try:
		settings = frappe.get_single("PW AI Settings")
	except Exception:
		frappe.throw(
			_("PW AI Settings not found. Please set up AI Settings first."),
			title=_("AI Not Configured"),
		)

	api_key = settings.get_password("openai_api_key")
	if not api_key:
		frappe.throw(
			_("OpenAI API key is not configured. Go to PW AI Settings to add it."),
			title=_("API Key Missing"),
		)

	return settings


# ──────────────────────────────────────────────────────────────────
# File reading and PDF conversion
# ──────────────────────────────────────────────────────────────────

def _read_file(file_url: str) -> list[dict]:
	"""Read file from Frappe and convert to dicts of base64 images.

	For images (JPG/PNG), returns a single item list.
	For PDFs, converts each page to an image and returns a list.

	Returns:
		List of dicts: [{"mime_type": "...", "base64_data": "..."}]
	"""
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	file_path = file_doc.get_full_path()

	ext = file_url.rsplit(".", 1)[-1].lower()

	if ext in ["jpg", "jpeg", "png"]:
		with open(file_path, "rb") as f:
			content = f.read()
		
		mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}
		return [{
			"mime_type": mime_map.get(ext, "image/jpeg"),
			"base64_data": base64.b64encode(content).decode("utf-8")
		}]
	
	if ext == "pdf":
		try:
			import fitz  # PyMuPDF
		except ImportError:
			frappe.throw(_("PyMuPDF is required to process PDFs. Please run 'bench pip install PyMuPDF'."))
		
		images = []
		doc = fitz.open(file_path)
		
		# Process up to first 5 pages to avoid massive token costs
		for page_num in range(min(len(doc), 5)):
			page = doc.load_page(page_num)
			# Render at ~150 DPI (zoom 2.0 = 144 DPI)
			matrix = fitz.Matrix(2.0, 2.0)
			pix = page.get_pixmap(matrix=matrix)
			img_data = pix.tobytes("jpeg", jpg_quality=85)
			
			images.append({
				"mime_type": "image/jpeg",
				"base64_data": base64.b64encode(img_data).decode("utf-8")
			})
			
		doc.close()
		return images

	frappe.throw(_("Unsupported file type."))


# ──────────────────────────────────────────────────────────────────
# OpenAI integration
# ──────────────────────────────────────────────────────────────────

EXTRACTION_PROMPT = """You are an expert accountant and invoice data extractor.

Analyze this invoice image(s) and extract ALL information into the following JSON structure.
Be extremely precise with numbers, dates, and tax calculations.

RULES:
- Dates must be in YYYY-MM-DD format
- All monetary amounts must be numbers (not strings), rounded to 2 decimal places
- If a field is not found on the invoice, use null
- For items, extract EVERY line item you can find across all pages
- HSN/SAC codes are Indian tax classification codes (4-8 digits)
- Identify tax types correctly: CGST, SGST, IGST, CESS, VAT, etc.
- UOM should be standard: "Nos", "Kg", "Ltr", "Mtr", "Box", "Set", "Pair", etc.

Return ONLY valid JSON (no markdown, no explanation):

{
  "supplier_name": "Full supplier/vendor name as printed",
  "supplier_gstin": "GSTIN if visible (15 chars) or null",
  "supplier_address": "Full address if visible or null",
  "bill_no": "Invoice/bill number",
  "bill_date": "YYYY-MM-DD",
  "due_date": "YYYY-MM-DD or null",
  "currency": "INR or other currency code",
  "items": [
    {
      "item_name": "Product/service name",
      "description": "Detailed description if available",
      "qty": 1.0,
      "uom": "Nos",
      "rate": 100.00,
      "amount": 100.00,
      "hsn_code": "HSN/SAC code or null",
      "discount_percentage": 0,
      "discount_amount": 0
    }
  ],
  "taxes": [
    {
      "tax_type": "CGST",
      "rate": 9.0,
      "amount": 9.00
    }
  ],
  "total": 100.00,
  "total_taxes": 18.00,
  "grand_total": 118.00,
  "rounding_adjustment": 0,
  "discount_amount": 0,
  "payment_terms": "Net 30 or null",
  "notes": "Any additional notes/remarks on the invoice"
}"""


def _call_openai(settings, image_list: list[dict]) -> dict:
	"""Call OpenAI GPT-4o with the invoice images and return parsed JSON."""
	import openai

	api_key = settings.get_password("openai_api_key")
	model = settings.model or "gpt-4o"

	# Build additional prompt hints if configured
	extra_prompt = ""
	if settings.extraction_prompt_hint:
		extra_prompt = f"\n\nADDITIONAL INSTRUCTIONS:\n{settings.extraction_prompt_hint}"

	client = openai.OpenAI(api_key=api_key)

	content_array = [
		{
			"type": "text",
			"text": EXTRACTION_PROMPT + extra_prompt,
		}
	]

	for img in image_list:
		content_array.append({
			"type": "image_url",
			"image_url": {
				"url": f"data:{img['mime_type']};base64,{img['base64_data']}",
				"detail": "high",
			}
		})

	try:
		response = client.chat.completions.create(
			model=model,
			messages=[
				{
					"role": "user",
					"content": content_array,
				}
			],
			max_tokens=4096,
			temperature=0.1,  # Low temperature for precise extraction
		)
	except openai.AuthenticationError:
		frappe.throw(
			_("OpenAI API key is invalid. Please check your key in PW AI Settings."),
			title=_("Authentication Failed"),
		)
	except openai.RateLimitError:
		frappe.throw(
			_("OpenAI rate limit reached. Please wait a moment and try again."),
			title=_("Rate Limited"),
		)
	except openai.APIError as e:
		frappe.throw(
			_("OpenAI API error: {0}").format(str(e)),
			title=_("API Error"),
		)

	raw_text = response.choices[0].message.content.strip()

	# Strip markdown code fences if present (GPT sometimes wraps JSON)
	raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
	raw_text = re.sub(r"\s*```$", "", raw_text)

	try:
		data = json.loads(raw_text)
	except json.JSONDecodeError:
		frappe.log_error(
			title="AI Invoice Scanner: JSON parse error",
			message=f"Raw response:\n{raw_text}",
		)
		frappe.throw(
			_("AI returned invalid data. Please try again or use a clearer invoice image."),
			title=_("Parse Error"),
		)

	return data


# ──────────────────────────────────────────────────────────────────
# Master data resolution (smart matching)
# ──────────────────────────────────────────────────────────────────

def _resolve_master_data(data: dict, settings) -> dict:
	"""Resolve supplier and item names against existing master data.

	Uses multi-tier matching to avoid creating duplicates:
	  1. GSTIN match (supplier) / HSN match (item)
	  2. Exact name match
	  3. Fuzzy partial match
	  4. Auto-create if toggle is on, else flag
	"""
	# Resolve supplier
	supplier_result = _resolve_supplier(
		data.get("supplier_name"),
		data.get("supplier_gstin"),
		auto_create=settings.auto_create_supplier,
	)
	data["_supplier"] = supplier_result

	# Resolve items
	resolved_items = []
	for item in data.get("items") or []:
		item_result = _resolve_item(
			item.get("item_name"),
			item.get("hsn_code"),
			item.get("uom", "Nos"),
			auto_create=settings.auto_create_item,
		)
		item["_resolved"] = item_result
		resolved_items.append(item)

	data["items"] = resolved_items
	return data


def _resolve_supplier(
	supplier_name: str | None,
	gstin: str | None,
	auto_create: bool = False,
) -> dict:
	"""Multi-tier supplier resolution.

	Returns dict with:
	  - supplier: matched/created supplier name (or None)
	  - match_type: 'gstin' | 'exact' | 'fuzzy' | 'created' | 'not_found'
	  - candidates: list of possible matches (for fuzzy)
	  - message: human-readable status
	"""
	if not supplier_name:
		return {"supplier": None, "match_type": "not_found", "candidates": [], "message": "No supplier name found on invoice"}

	# ── Tier 1: GSTIN match (most reliable) ────────────────────
	if gstin and len(gstin) == 15:
		match = frappe.db.get_value(
			"Supplier",
			{"tax_id": gstin},
			["name", "supplier_name"],
			as_dict=True,
		)
		if match:
			return {
				"supplier": match.name,
				"supplier_name": match.supplier_name,
				"match_type": "gstin",
				"candidates": [],
				"message": f"Matched by GSTIN: {match.supplier_name}",
			}

	# ── Tier 2: Exact name match (case-insensitive) ────────────
	exact = frappe.db.get_value(
		"Supplier",
		{"supplier_name": ("like", supplier_name)},
		["name", "supplier_name"],
		as_dict=True,
	)
	if exact:
		return {
			"supplier": exact.name,
			"supplier_name": exact.supplier_name,
			"match_type": "exact",
			"candidates": [],
			"message": f"Exact match: {exact.supplier_name}",
		}

	# ── Tier 3: Fuzzy / partial match ──────────────────────────
	keywords = _extract_keywords(supplier_name)
	candidates = []
	for keyword in keywords:
		if len(keyword) < 3:
			continue
		matches = frappe.db.get_all(
			"Supplier",
			filters={"supplier_name": ("like", f"%{keyword}%")},
			fields=["name", "supplier_name", "tax_id"],
			limit=10,
		)
		for m in matches:
			if m.name not in [c["name"] for c in candidates]:
				candidates.append(m)

	if len(candidates) == 1:
		# Single fuzzy match — high confidence
		return {
			"supplier": candidates[0].name,
			"supplier_name": candidates[0].supplier_name,
			"match_type": "fuzzy",
			"candidates": [],
			"message": f"Fuzzy match: {candidates[0].supplier_name}",
		}

	if len(candidates) > 1:
		# Multiple candidates — let the user decide
		return {
			"supplier": candidates[0].name,  # best guess
			"supplier_name": candidates[0].supplier_name,
			"match_type": "fuzzy_multiple",
			"candidates": [{"name": c.name, "supplier_name": c.supplier_name} for c in candidates[:5]],
			"message": f"Multiple possible matches found. Please verify.",
		}

	# ── Tier 4: No match — auto-create or flag ─────────────────
	if auto_create:
		new_supplier = _create_supplier(supplier_name, gstin)
		return {
			"supplier": new_supplier,
			"supplier_name": supplier_name,
			"match_type": "created",
			"candidates": [],
			"message": f"Created new supplier: {supplier_name}",
		}

	return {
		"supplier": None,
		"supplier_name": supplier_name,
		"match_type": "not_found",
		"candidates": [],
		"message": f"No matching supplier found for '{supplier_name}'. Please select manually.",
	}


def _resolve_item(
	item_name: str | None,
	hsn_code: str | None,
	uom: str = "Nos",
	auto_create: bool = False,
) -> dict:
	"""Multi-tier item resolution.

	Returns dict with:
	  - item_code: matched/created item code (or None)
	  - match_type: 'hsn' | 'exact' | 'fuzzy' | 'created' | 'not_found'
	  - candidates: list of possible matches
	  - message: human-readable status
	"""
	if not item_name:
		return {"item_code": None, "match_type": "not_found", "candidates": [], "message": "No item name found"}

	# ── Tier 1: Exact name match ───────────────────────────────
	exact = frappe.db.get_value(
		"Item",
		{"item_name": ("like", item_name)},
		["name", "item_name", "stock_uom"],
		as_dict=True,
	)
	if exact:
		return {
			"item_code": exact.name,
			"item_name": exact.item_name,
			"uom": exact.stock_uom or uom,
			"match_type": "exact",
			"candidates": [],
			"message": f"Exact match: {exact.item_name}",
		}

	# ── Tier 2: HSN code match ─────────────────────────────────
	if hsn_code:
		hsn_matches = frappe.db.get_all(
			"Item",
			filters={"gst_hsn_code": hsn_code},
			fields=["name", "item_name", "stock_uom"],
			limit=5,
		)
		if len(hsn_matches) == 1:
			m = hsn_matches[0]
			return {
				"item_code": m.name,
				"item_name": m.item_name,
				"uom": m.stock_uom or uom,
				"match_type": "hsn",
				"candidates": [],
				"message": f"Matched by HSN {hsn_code}: {m.item_name}",
			}
		if hsn_matches:
			# Multiple items with same HSN — try to narrow with name
			for m in hsn_matches:
				if _names_similar(item_name, m.item_name):
					return {
						"item_code": m.name,
						"item_name": m.item_name,
						"uom": m.stock_uom or uom,
						"match_type": "hsn_name",
						"candidates": [],
						"message": f"Matched by HSN + name similarity: {m.item_name}",
					}

	# ── Tier 3: Fuzzy / partial name match ─────────────────────
	keywords = _extract_keywords(item_name)
	candidates = []
	for keyword in keywords:
		if len(keyword) < 3:
			continue
		matches = frappe.db.get_all(
			"Item",
			filters={"item_name": ("like", f"%{keyword}%")},
			fields=["name", "item_name", "stock_uom"],
			limit=10,
		)
		for m in matches:
			if m.name not in [c["name"] for c in candidates]:
				candidates.append(m)

	if len(candidates) == 1:
		return {
			"item_code": candidates[0].name,
			"item_name": candidates[0].item_name,
			"uom": candidates[0].stock_uom or uom,
			"match_type": "fuzzy",
			"candidates": [],
			"message": f"Fuzzy match: {candidates[0].item_name}",
		}

	if len(candidates) > 1:
		# Pick best candidate by name similarity
		best = _pick_best_match(item_name, candidates, "item_name")
		return {
			"item_code": best["name"],
			"item_name": best["item_name"],
			"uom": best.get("stock_uom") or uom,
			"match_type": "fuzzy_best",
			"candidates": [{"name": c["name"], "item_name": c["item_name"]} for c in candidates[:5]],
			"message": f"Best fuzzy match: {best['item_name']}. Alternatives available.",
		}

	# ── Tier 4: No match — auto-create or flag ─────────────────
	if auto_create:
		new_item = _create_item(item_name, hsn_code, uom)
		return {
			"item_code": new_item,
			"item_name": item_name,
			"uom": uom,
			"match_type": "created",
			"candidates": [],
			"message": f"Created new item: {item_name}",
		}

	return {
		"item_code": None,
		"item_name": item_name,
		"uom": uom,
		"match_type": "not_found",
		"candidates": [],
		"message": f"No matching item found for '{item_name}'. Please link manually.",
	}


# ──────────────────────────────────────────────────────────────────
# Matching helpers
# ──────────────────────────────────────────────────────────────────

# Common words to ignore when extracting search keywords
_STOP_WORDS = {
	"pvt", "ltd", "limited", "private", "inc", "llc", "llp", "co",
	"and", "the", "of", "for", "in", "on", "at", "to", "by",
	"m/s", "ms", "mr", "mrs", "dr", "shri", "smt",
	"enterprise", "enterprises", "trading", "traders", "industries",
	"company", "corporation", "solutions", "services", "technologies",
}


def _extract_keywords(name: str) -> list[str]:
	"""Extract meaningful keywords from a name for fuzzy searching.

	Strips common suffixes (Pvt Ltd, etc.) and short words.
	"""
	if not name:
		return []
	# Remove special characters, keep alphanumeric and spaces
	clean = re.sub(r"[^\w\s]", " ", name)
	words = clean.split()
	# Filter out stop words and very short words
	keywords = [w for w in words if w.lower() not in _STOP_WORDS and len(w) >= 2]
	return keywords


def _names_similar(name1: str, name2: str) -> bool:
	"""Simple similarity check: do the significant keywords overlap?"""
	kw1 = set(w.lower() for w in _extract_keywords(name1))
	kw2 = set(w.lower() for w in _extract_keywords(name2))
	if not kw1 or not kw2:
		return False
	overlap = kw1 & kw2
	return len(overlap) >= min(len(kw1), len(kw2)) * 0.5


def _pick_best_match(target_name: str, candidates: list[dict], name_field: str) -> dict:
	"""Pick the candidate whose name is most similar to the target."""
	target_kw = set(w.lower() for w in _extract_keywords(target_name))
	best = candidates[0]
	best_score = 0

	for c in candidates:
		c_kw = set(w.lower() for w in _extract_keywords(c.get(name_field, "")))
		overlap = len(target_kw & c_kw)
		if overlap > best_score:
			best_score = overlap
			best = c

	return best


# ──────────────────────────────────────────────────────────────────
# Auto-creation (last resort)
# ──────────────────────────────────────────────────────────────────

def _create_supplier(supplier_name: str, gstin: str | None) -> str:
	"""Create a new Supplier with minimal required fields."""
	doc = frappe.get_doc({
		"doctype": "Supplier",
		"supplier_name": supplier_name,
		"supplier_group": _get_default_supplier_group(),
		"supplier_type": "Company",
		"tax_id": gstin or "",
	})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert()
	frappe.db.commit()
	frappe.msgprint(
		_("Auto-created Supplier: {0}").format(supplier_name),
		indicator="blue",
		alert=True,
	)
	return doc.name


def _create_item(item_name: str, hsn_code: str | None, uom: str = "Nos") -> str:
	"""Create a new Item with minimal required fields."""
	# Ensure UOM exists
	if uom and not frappe.db.exists("UOM", uom):
		uom = "Nos"

	doc = frappe.get_doc({
		"doctype": "Item",
		"item_name": item_name,
		"item_code": item_name,  # use name as code
		"item_group": _get_default_item_group(),
		"stock_uom": uom,
		"is_stock_item": 1,
		"gst_hsn_code": hsn_code or "",
	})
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.insert()
	frappe.db.commit()
	frappe.msgprint(
		_("Auto-created Item: {0}").format(item_name),
		indicator="blue",
		alert=True,
	)
	return doc.name


def _get_default_supplier_group() -> str:
	"""Get the default supplier group, fallback to first available."""
	default = frappe.db.get_single_value("Buying Settings", "supplier_group")
	if default:
		return default
	first = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
	return first or "All Supplier Groups"


def _get_default_item_group() -> str:
	"""Get a default item group for auto-created items."""
	default = frappe.db.get_single_value("Stock Settings", "item_group")
	if default:
		return default
	first = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
	return first or "All Item Groups"
