# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import base64
import json
import re
import time
from io import BytesIO

import frappe
from frappe.utils.file_manager import get_file
import openai
from pdf2image import convert_from_bytes
from pdf2image.exceptions import PDFPageCountError, PDFSyntaxError

from possibleworks.ap_invoice_processing.bin_paths import POPPLER_PATH

from possibleworks.ap_invoice_processing.doctype.ai_document_processor_settings.ai_document_processor_settings import (
	APProcessorSettings,
)
from possibleworks.ap_invoice_processing.extraction_prompt import EXTRACTION_SCHEMAS, get_extraction_prompt
from possibleworks.ap_invoice_processing.ap_agent_tools import OPENAI_TOOLS, execute_tool


def extract_data_from_file(file_name, target_doctype="Purchase Invoice"):
	"""
	Reads the file attached in ERPNext, converts to base64 images,
	and sends to OpenAI Agent loop for tool-calling extraction.
	Returns a dictionary with parsed data, raw JSON, and page count.
	"""

	if not APProcessorSettings.is_enabled():
		frappe.throw("AI Document Processing is disabled in AI Document Processor Settings.")

	if not APProcessorSettings.is_doctype_supported(target_doctype):
		frappe.throw(f"DocType '{target_doctype}' is not enabled for AI Document Processing.")

	# 1. Fetch file from ERPNext
	fname, fcontent = get_file(file_name)

	file_ext = fname.split(".")[-1].lower() if "." in fname else ""
	allowed_exts = APProcessorSettings.get_allowed_extensions()

	if file_ext not in allowed_exts:
		frappe.throw(f"File type '{file_ext}' is not allowed. Allowed types: {', '.join(allowed_exts)}")

	max_bytes = APProcessorSettings.get_max_file_size_bytes()
	if len(fcontent) > max_bytes:
		frappe.throw(f"File size exceeds the maximum allowed limit of {max_bytes / (1024*1024)} MB.")

	# 2. Process File into Base64 Images
	base64_images = []
	page_count = 1

	_MAX_PDF_PAGES = 30

	if file_ext == "pdf":
		try:
			# Convert PDF pages to images. Cap at _MAX_PDF_PAGES and use 150 DPI
			# to keep memory usage bounded for large files.
			# When last_page is supplied, pdf2image first calls pdfinfo to validate
			# the page count. Some government/portal PDFs (e.g. Kaveri, MCA) have
			# unusual internal structure that causes pdfinfo to fail even though
			# pdftoppm can render them fine. On PDFPageCountError we retry without
			# last_page (skips pdfinfo) and slice the result to the cap.
			try:
				images = convert_from_bytes(fcontent, dpi=150, first_page=1, last_page=_MAX_PDF_PAGES, poppler_path=POPPLER_PATH)
			except PDFPageCountError:
				images = convert_from_bytes(fcontent, dpi=150, first_page=1, poppler_path=POPPLER_PATH)
				images = images[:_MAX_PDF_PAGES]
			page_count = len(images)
			for img in images:
				buffered = BytesIO()
				img.save(buffered, format="JPEG")
				img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
				base64_images.append(img_b64)
		except PDFPageCountError:
			frappe.throw("Failed to read PDF. It might be password-protected or corrupted.")
		except Exception as e:
			frappe.throw(f"Error converting PDF to images: {str(e)}")
	else:
		# Native image (JPG, PNG, WEBP)
		img_b64 = base64.b64encode(fcontent).decode('utf-8')
		base64_images.append(img_b64)

	# 3. Prepare OpenAI Agent Messages
	openai_config = APProcessorSettings.get_openai_config()
	api_key = openai_config.get("api_key")
	
	if not api_key:
		frappe.throw(
			"OpenAI API key is not configured.\n\n"
			"Set `openai_api_key` in either:\n"
			"1) sites/common_site_config.json (recommended for all sites), or\n"
			"2) sites/<your-site>/site_config.json\n\n"
			"Then restart bench."
		)

	client = openai.OpenAI(api_key=api_key)

	if target_doctype not in EXTRACTION_SCHEMAS:
		frappe.throw(f"No extraction schema is configured for target DocType: {target_doctype}")
	schema = EXTRACTION_SCHEMAS[target_doctype]

	# Build the full system prompt with schema injected
	system_prompt = get_extraction_prompt(target_doctype)

	# Append all item groups so the AI can pick the right one before calling
	# list_all_items — no extra tool call needed.
	system_prompt += _build_item_groups_section()

	messages: list[dict] = [
		{"role": "system", "content": system_prompt}
	]

	# Add images as user message
	content_array: list[dict] = [{
		"type": "text",
		"text": (
			f"Extract all data from these document pages for target DocType '{target_doctype}'. "
			"IMPORTANT: Start by calling get_company_context, then use the available tools to resolve exact ERPNext IDs. "
			"After gathering all data, output the final structured JSON."
		),
	}]
	for b64 in base64_images:
		content_array.append({
			"type": "image_url",
			"image_url": {
				"url": f"data:image/jpeg;base64,{b64}",
				"detail": "high"
			}
		})
	messages.append({"role": "user", "content": content_array})

	# 4. Agent Loop Execution (Max 3 API call retries for entire process, max 25 steps per extraction)
	max_retries = 3
	max_agent_steps = 25
	model = openai_config.get("model") or openai_config.get("openai_model") or "gpt-4o"

	for attempt in range(max_retries):
		try:
			return _run_agent_loop(client, model, messages, schema, max_agent_steps, page_count)

		except Exception as e:
			if attempt == max_retries - 1:
				frappe.log_error("OpenAI Agent Extraction Failed", str(e))
				frappe.throw(f"Failed to process document with AI after {max_retries} attempts: {str(e)}")
			# Exponential backoff: 2s, 4s, 8s … respects OpenAI rate limit windows.
			wait_seconds = 2 ** (attempt + 1)
			time.sleep(wait_seconds)


def _validate_extraction_response(parsed_data, json_schema):
	"""Raise if the model response is missing required top-level fields."""
	required_fields = json_schema.get("required", [])
	missing = [f for f in required_fields if f not in parsed_data]
	if missing:
		raise Exception(
			f"AI response is missing required fields: {missing}. "
			"The model may have returned an incomplete or unexpected JSON structure."
		)


def _build_item_groups_section():
	"""
	Fetch all non-empty item groups from ERPNext and return a prompt section
	that tells the AI exactly which groups exist — so it can immediately pass
	the right item_group to list_all_items without a separate tool call.

	Uses a single GROUP BY SQL query instead of one COUNT per group.
	"""
	try:
		# One query: count active items per group.
		rows = frappe.db.sql(
			"""
			SELECT item_group, COUNT(*) AS cnt
			FROM `tabItem`
			WHERE disabled = 0
			  AND item_group IS NOT NULL
			  AND item_group != ''
			GROUP BY item_group
			ORDER BY item_group ASC
			""",
			as_dict=True,
		)
		non_empty = [f"  - {r.item_group} ({r.cnt} items)" for r in rows if r.cnt > 0]
		if not non_empty:
			return ""
		lines = "\n".join(non_empty)
		return (
			f"\n\nAVAILABLE ITEM GROUPS IN THIS ERPPNEXT SITE:\n"
			f"{lines}\n\n"
			f"ITEM MATCHING RULE (MANDATORY):\n"
			f"- When find_item returns matches:[] → you MUST call list_all_items before outputting null.\n"
			f"- Use SEMANTIC reasoning to pick item_group: read supplier name, description, SAC/HSN code.\n"
			f"  Examples: AC supplier + 'AMC' → pick group with maintenance/services items.\n"
			f"            'rent'/'rental' → pick group with lease/rent items.\n"
			f"            'repair'/'repairing' → pick group with repair/maintenance items.\n"
			f"- If no specific group fits, call list_all_items() WITHOUT item_group to see all items.\n"
			f"- Only set item_code_matched=null if list_all_items truly returns nothing relevant."
		)
	except Exception:
		return ""


# Per-model pricing table: (input_per_1m, cached_per_1m, output_per_1m) in USD
_MODEL_PRICING = {
	"gpt-4o":                 (2.50, 1.25, 10.00),
	"gpt-4o-2024-11-20":      (2.50, 1.25, 10.00),
	"gpt-4o-2024-08-06":      (2.50, 1.25, 10.00),
	"gpt-4o-mini":            (0.15, 0.075, 0.60),
	"gpt-4o-mini-2024-07-18": (0.15, 0.075, 0.60),
	"gpt-4.1":                (2.00, 0.50,  8.00),
	"gpt-4.1-mini":           (0.40, 0.10,  1.60),
}


def _compute_cost_usd(model, input_tokens, cached_tokens, output_tokens):
	"""Estimate USD cost. Cached tokens are billed at ~50% of the regular input rate."""
	key = (model or "").lower()
	pricing = _MODEL_PRICING.get(key)
	if not pricing:
		for prefix, p in _MODEL_PRICING.items():
			if key.startswith(prefix):
				pricing = p
				break
	if not pricing:
		pricing = (2.50, 1.25, 10.00)  # fallback: gpt-4o rates

	input_per_1m, cached_per_1m, output_per_1m = pricing
	non_cached = max(0, input_tokens - cached_tokens)
	return round(
		(non_cached    * input_per_1m  / 1_000_000) +
		(cached_tokens * cached_per_1m / 1_000_000) +
		(output_tokens * output_per_1m / 1_000_000),
		6
	)


def _extract_json(content):
	"""Parse JSON from model output, stripping markdown code fences if present.
	Without response_format='json_object' the model occasionally wraps output
	in ```json ... ``` — this handles both cases safely.
	"""
	text = (content or "").strip()
	# Strip ```json ... ``` or ``` ... ``` fences
	text = re.sub(r"^```(?:json)?\s*", "", text)
	text = re.sub(r"\s*```$", "", text)
	return json.loads(text.strip())


def _run_agent_loop(client, model, messages, json_schema, max_steps, page_count):
	"""Runs the tool-calling loop until a final JSON response format is produced."""

	step_count = 0
	total_input_tokens = 0
	total_output_tokens = 0
	total_cached_tokens = 0
	tool_calls_log = []   # [{step, tool, arguments, result}, ...]

	while step_count < max_steps:
		step_count += 1

		response = client.chat.completions.create(
			model=model,
			messages=messages,
			tools=OPENAI_TOOLS,
			tool_choice="auto",
		)

		# Accumulate token usage across every agent step
		usage = response.usage
		if usage:
			total_input_tokens  += getattr(usage, "prompt_tokens", 0) or 0
			total_output_tokens += getattr(usage, "completion_tokens", 0) or 0
			details = getattr(usage, "prompt_tokens_details", None)
			if details:
				total_cached_tokens += getattr(details, "cached_tokens", 0) or 0

		choice = response.choices[0]
		msg = choice.message
		messages.append(msg)

		# If the model hit the token limit mid-response, the output is incomplete — raise immediately
		# rather than trying to parse a truncated JSON.
		if choice.finish_reason == "length":
			raise Exception("Model hit token limit mid-response. Try a shorter document or fewer pages.")

		# If the model called tools, execute them and attach the results
		if msg.tool_calls:
			_logger = frappe.logger("ai_document_processing", allow_site=True)
			for tool_call in msg.tool_calls:
				tool_name = tool_call.function.name
				arguments = tool_call.function.arguments

				_logger.debug(f"Agent Tool Called: {tool_name} (Step {step_count}) | args={arguments}")

				# Execute local db routing
				result_str = execute_tool(tool_name, arguments)

				_logger.debug(f"Agent Tool Result: {tool_name} (Step {step_count}) | result={result_str[:500]}")

				# Record for audit log
				try:
					args_obj = json.loads(arguments) if isinstance(arguments, str) else arguments
					result_obj = json.loads(result_str) if isinstance(result_str, str) else result_str
				except Exception:
					args_obj = arguments
					result_obj = result_str
				tool_calls_log.append({
					"step": step_count,
					"tool": tool_name,
					"arguments": args_obj,
					"result": result_obj,
				})

				# Pass back to model
				messages.append({
					"role": "tool",
					"tool_call_id": tool_call.id,
					"name": tool_name,
					"content": result_str
				})
			# Continue the while loop so the model can process the tool results
			continue

		# If no tool calls, it means the model thinks it's done and has output the final JSON
		if msg.content:
			frappe.logger("ai_document_processing", allow_site=True).debug(
				f"Agent Final Exit (Step {step_count}) | length={len(msg.content)}"
			)
			try:
				parsed_data = _extract_json(msg.content)
				_validate_extraction_response(parsed_data, json_schema)
				total_tokens = total_input_tokens + total_output_tokens
				return {
					"parsed": parsed_data,
					"raw_response": msg.content,
					"page_count": page_count,
					"model_used": model,
					"agent_steps": step_count,
					"input_tokens": total_input_tokens,
					"output_tokens": total_output_tokens,
					"cached_tokens": total_cached_tokens,
					"total_tokens": total_tokens,
					"estimated_cost_usd": _compute_cost_usd(
						model, total_input_tokens, total_cached_tokens, total_output_tokens
					),
					"tool_calls_log": tool_calls_log,
				}
			except (json.JSONDecodeError, ValueError):
				raise Exception("OpenAI did not return valid JSON.")

	raise Exception("Agent exceeded maximum allowed tool steps without producing a final JSON.")
