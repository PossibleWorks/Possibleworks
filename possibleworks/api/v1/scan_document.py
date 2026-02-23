# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""
Generic AI document scanning API.

Entry point: scan_document(file_url, doctype)
  - Reads and converts the file (PDF→images via PyMuPDF, or JPEG/PNG as-is)
  - Loads AI settings + the per-doctype mapper
  - Calls OpenAI GPT-4o vision with the mapper's prompt
  - Returns mapper-resolved JSON (supplier/customer/items matched against ERPNext)
"""

import base64
import json
import re

import frappe
from frappe import _

from possibleworks.ai.document_mappers import get_mapper


# ──────────────────────────────────────────────────────────────────
# Public entry point
# ──────────────────────────────────────────────────────────────────

@frappe.whitelist()
def scan_document(file_url: str, doctype: str) -> dict:
	"""AI-scan an uploaded document and return form-ready JSON.

	Args:
		file_url: Frappe file URL (e.g. /private/files/invoice.pdf)
		doctype:  Target ERPNext DocType (e.g. "Purchase Invoice")

	Returns:
		Mapper-resolved dict ready for client-side form population.
	"""
	_validate_file_url(file_url)
	settings = _get_ai_settings()
	mapper = get_mapper(doctype)

	image_list = _read_file(file_url)
	prompt = mapper.build_prompt(
		custom_prompt=None,  # custom_prompt from doctype config is resolved on client
		global_hint=settings.extraction_prompt_hint,
	)

	raw_data = _call_openai(settings, image_list, prompt)
	resolved = mapper.resolve_and_return(raw_data, settings)
	return resolved


# ──────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────

ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}


def _validate_file_url(file_url: str):
	if not file_url:
		frappe.throw(_("Please upload a file first."))
	ext = ("." + file_url.rsplit(".", 1)[-1].lower()) if "." in file_url else ""
	if ext not in ALLOWED_EXTENSIONS:
		frappe.throw(
			_("Unsupported file type '{0}'. Allowed: PDF, JPG, PNG").format(ext)
		)


# ──────────────────────────────────────────────────────────────────
# AI Settings
# ──────────────────────────────────────────────────────────────────

def _get_ai_settings():
	try:
		settings = frappe.get_single("PW AI Settings")
	except Exception:
		frappe.throw(_("PW AI Settings not found. Please configure it first."), title=_("AI Not Configured"))

	api_key = settings.get_password("openai_api_key", raise_exception=False)
	if not api_key:
		frappe.throw(
			_("OpenAI API key is not configured. Go to PW AI Settings to add it."),
			title=_("API Key Missing"),
		)
	return settings


# ──────────────────────────────────────────────────────────────────
# File reading (PDF → image list, or single image)
# ──────────────────────────────────────────────────────────────────

def _read_file(file_url: str) -> list[dict]:
	"""Read file from Frappe and return a list of base64 image dicts.

	PDFs are rasterised page-by-page via PyMuPDF (up to 5 pages).
	Images are returned as a single-element list.
	"""
	file_doc = frappe.get_doc("File", {"file_url": file_url})
	file_path = file_doc.get_full_path()
	ext = file_url.rsplit(".", 1)[-1].lower()

	if ext in ("jpg", "jpeg", "png"):
		with open(file_path, "rb") as f:
			content = f.read()
		mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
		return [{"mime_type": mime, "base64_data": base64.b64encode(content).decode()}]

	if ext == "pdf":
		try:
			import fitz
		except ImportError:
			frappe.throw(_("PyMuPDF is required to process PDFs. Run: bench pip install PyMuPDF"))

		images = []
		doc = fitz.open(file_path)
		for i in range(min(len(doc), 5)):
			page = doc.load_page(i)
			pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
			images.append({
				"mime_type": "image/jpeg",
				"base64_data": base64.b64encode(pix.tobytes("jpeg", jpg_quality=85)).decode(),
			})
		doc.close()
		return images

	frappe.throw(_("Unsupported file type."))


# ──────────────────────────────────────────────────────────────────
# OpenAI call
# ──────────────────────────────────────────────────────────────────

def _call_openai(settings, image_list: list[dict], prompt: str) -> dict:
	"""Call GPT-4o with the image list and extraction prompt."""
	import openai

	api_key = settings.get_password("openai_api_key", raise_exception=False)
	model = settings.model or "gpt-4o"

	client = openai.OpenAI(api_key=api_key)

	content = [{"type": "text", "text": prompt}]
	for img in image_list:
		content.append({
			"type": "image_url",
			"image_url": {
				"url": f"data:{img['mime_type']};base64,{img['base64_data']}",
				"detail": "high",
			},
		})

	try:
		response = client.chat.completions.create(
			model=model,
			messages=[{"role": "user", "content": content}],
			max_tokens=4096,
			temperature=0.1,
		)
	except openai.AuthenticationError:
		frappe.throw(_("Invalid OpenAI API key. Check PW AI Settings."), title=_("Authentication Failed"))
	except openai.RateLimitError:
		frappe.throw(_("OpenAI rate limit reached. Please wait and retry."), title=_("Rate Limited"))
	except openai.APIError as e:
		frappe.throw(_("OpenAI API error: {0}").format(str(e)), title=_("API Error"))

	raw_text = response.choices[0].message.content.strip()
	raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
	raw_text = re.sub(r"\s*```$", "", raw_text)

	try:
		return json.loads(raw_text)
	except json.JSONDecodeError:
		frappe.log_error(title="AI Scanner: JSON parse error", message=raw_text)
		frappe.throw(_("AI returned invalid data. Try again with a clearer image."), title=_("Parse Error"))
