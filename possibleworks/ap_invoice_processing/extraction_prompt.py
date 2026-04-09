# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json


BASE_INSTRUCTIONS = """
You are an expert ERPNext AI Data Extraction Agent built into the Possibleworks platform.
You extract structured data from uploaded financial documents and return STRICT JSON only.

CRITICAL OPERATING RULES
1) ALWAYS call get_company_context first.
2) Use tools to resolve exact ERPNext IDs (supplier/customer/item/accounts). Never guess IDs.
3) Always include raw extracted text fields, even when no ERP match exists.
4) NUMBER ACCURACY — THIS IS THE HIGHEST PRIORITY RULE. FINANCIAL NUMBERS MUST NEVER BE ALTERED.
   a) Copy every number EXACTLY as printed on the document. Do NOT round, estimate, or recalculate.
   b) rate, amount, qty, tax_amount, subtotal, grand_total must all match the document digit-for-digit.
   c) If a number on the document is 2.69 you MUST output 2.69 — never 3.12, never 2.7, never anything else.
   d) Do NOT derive rate from ERP item defaults or price lists. The document's printed rate is final.
   e) grand_total MUST equal the printed grand total. subtotal MUST equal the printed subtotal.
      If your line items do not sum to the printed subtotal, re-read the document — do not invent numbers.
   f) For qty: use exactly what is printed. If qty is not printed, use 1 — never guess.
   g) Copy BOTH rate and amount EXACTLY as printed. Do NOT reconcile them yourself.
      - If qty × rate ≠ amount on the document, still output both as printed.
      - Post-processing will handle normalization. Your job is to copy, not compute.
5) Hierarchy safety:
   - If parent summary line and child breakdown lines both appear, do not double count.
   - Keep one representation only (prefer parent line if children sum to parent).
6) Tax rows:
   - Treat '-', '--', blank, or zero-amount rows as zero — do not output them.
   - Never mix IGST with CGST+SGST for the same taxable value.
   - Do not invent taxes not shown on the document.
   - tax_amount must be the exact printed amount — apply rule 4.
   - Full lookup steps are in the TARGET WORKFLOW below.
7) Document validity:
   - Mark is_valid_document=true whenever the document clearly contains a transaction with party + amount.
   - Mark false only for truly unrelated/non-financial files.
8) Return ONLY a raw JSON object matching the schema. No markdown, no code blocks, no explanation.
9) Party safety:
   - For purchase-side documents, supplier is the issuer / FROM party, not the bill-to company.
10) Item matching:
    - Full mandatory lookup steps are in the TARGET WORKFLOW below.
    - NEVER leave item_code_matched null without completing both find_item AND list_all_items.
    - Only leave null if list_all_items also returns nothing semantically close.
"""


def _item_schema():
	return {
		"type": "array",
		"description": "Line items extracted from the document.",
		"items": {
			"type": "object",
			"properties": {
				"description_extracted": {
					"type": "string",
					"description": "Raw line description from the document."
				},
				"item_code_matched": {
					"type": ["string", "null"],
					"description": "Exact ERPNext Item Code matched via find_item/list_all_items."
				},
				"quantity": {
					"type": "number",
					"description": "Quantity. Use 1 when not present."
				},
				"rate": {
					"type": "number",
					"description": "Rate exactly as printed on the document. Never compute or derive this — copy the printed number."
				},
				"amount": {
					"type": "number",
					"description": "Line amount."
				},
				"uom": {
					"type": "string",
					"default": "Nos"
				},
				"hsn_sac_code": {
					"type": ["string", "null"]
				}
			},
			"required": ["description_extracted", "quantity", "rate", "amount"]
		}
	}


def _tax_schema():
	return {
		"type": "array",
		"description": "Tax rows from the document.",
		"items": {
			"type": "object",
			"properties": {
				"tax_type_extracted": {"type": "string"},
				"charge_type": {"type": "string", "default": "On Net Total"},
				"account_head_matched": {"type": ["string", "null"]},
				"rate": {"type": ["number", "null"]},
				"tax_amount": {"type": "number"}
			},
			"required": ["tax_type_extracted", "tax_amount"]
		}
	}


def _supplier_trade_schema(description: str):
	return {
		"description": description,
		"json_schema": {
			"type": "object",
			"properties": {
				"is_valid_document": {"type": "boolean"},
				"company_matched": {"type": ["string", "null"]},
				"supplier_name_extracted": {"type": ["string", "null"]},
				"supplier_id_matched": {"type": ["string", "null"]},
				"supplier_gstin_extracted": {"type": ["string", "null"]},
				"document_number": {"type": ["string", "null"]},
				"document_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"currency": {"type": "string", "default": "INR"},
				"payment_terms_template": {"type": ["string", "null"]},
				"taxes_and_charges_template": {"type": ["string", "null"]},
				"po_reference_extracted": {"type": ["string", "null"]},
				"po_reference_matched": {"type": ["string", "null"]},
				"subtotal": {"type": ["number", "null"]},
				"taxes": _tax_schema(),
				"grand_total": {"type": ["number", "null"]},
				"items": _item_schema(),
				"notes": {"type": ["string", "null"]}
			},
			"required": [
				"is_valid_document",
				"supplier_name_extracted",
				"items"
			]
		}
	}


def _customer_trade_schema(description: str):
	return {
		"description": description,
		"json_schema": {
			"type": "object",
			"properties": {
				"is_valid_document": {"type": "boolean"},
				"company_matched": {"type": ["string", "null"]},
				"customer_name_extracted": {"type": ["string", "null"]},
				"customer_id_matched": {"type": ["string", "null"]},
				"customer_gstin_extracted": {"type": ["string", "null"]},
				"document_number": {"type": ["string", "null"]},
				"document_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"currency": {"type": "string", "default": "INR"},
				"payment_terms_template": {"type": ["string", "null"]},
				"taxes_and_charges_template": {"type": ["string", "null"]},
				"sales_order_reference_extracted": {"type": ["string", "null"]},
				"sales_order_reference_matched": {"type": ["string", "null"]},
				"delivery_note_reference_extracted": {"type": ["string", "null"]},
				"delivery_note_reference_matched": {"type": ["string", "null"]},
				"subtotal": {"type": ["number", "null"]},
				"taxes": _tax_schema(),
				"grand_total": {"type": ["number", "null"]},
				"items": _item_schema(),
				"notes": {"type": ["string", "null"]}
			},
			"required": [
				"is_valid_document",
				"customer_name_extracted",
				"items"
			]
		}
	}


EXTRACTION_SCHEMAS = {
	"Purchase Invoice": {
		"description": "Standard Accounts Payable Vendor Invoice",
		"json_schema": {
			"type": "object",
			"properties": {
				"is_valid_document": {
					"type": "boolean",
					"description": "True if this is a purchase/vendor invoice or bill."
				},
				"supplier_name_extracted": {
					"type": ["string", "null"],
					"description": "Supplier/vendor FROM-party name."
				},
				"supplier_id_matched": {
					"type": ["string", "null"],
					"description": "Exact Supplier ID from find_supplier."
				},
				"supplier_gstin_extracted": {
					"type": ["string", "null"],
					"description": "Supplier issuer GSTIN if visible."
				},
				"invoice_number": {
					"type": ["string", "null"],
					"description": "Invoice/Bill number."
				},
				"invoice_date": {
					"type": ["string", "null"],
					"description": "Invoice date in YYYY-MM-DD format."
				},
				"due_date": {
					"type": ["string", "null"],
					"description": "Due date in YYYY-MM-DD format."
				},
				"po_reference_extracted": {
					"type": ["string", "null"],
					"description": "Raw PO reference from document."
				},
				"po_reference_matched": {
					"type": ["string", "null"],
					"description": "Exact PO ID from find_purchase_order."
				},
				"currency": {"type": "string", "default": "INR"},
				"company_matched": {"type": ["string", "null"]},
				"taxes_and_charges_template": {"type": ["string", "null"]},
				"payment_terms_template": {"type": ["string", "null"]},
				"subtotal": {"type": ["number", "null"]},
				"taxes": _tax_schema(),
				"grand_total": {"type": ["number", "null"]},
				"items": _item_schema(),
				"is_duplicate": {"type": "boolean", "default": False},
				"duplicate_invoice_id": {"type": ["string", "null"]},
				"notes": {"type": ["string", "null"]}
			},
			"required": [
				"is_valid_document",
				"supplier_name_extracted",
				"items"
			]
		}
	},
	"Purchase Receipt": _supplier_trade_schema("Supplier goods receipt document"),
	"Supplier Quotation": _supplier_trade_schema("Supplier quotation document"),
	"Sales Order": _customer_trade_schema("Customer sales order / purchase order document"),
	"Quotation": _customer_trade_schema("Customer quotation / proposal document"),
	"Delivery Note": _customer_trade_schema("Delivery challan / delivery note document"),
	"Payment Entry": {
		"description": "Payment advice / remittance / transfer document",
		"json_schema": {
			"type": "object",
			"properties": {
				"is_valid_document": {"type": "boolean"},
				"company_matched": {"type": ["string", "null"]},
				"payment_type": {
					"type": ["string", "null"],
					"description": "Pay or Receive. Infer from document context."
				},
				"party_type": {
					"type": ["string", "null"],
					"description": "Supplier or Customer."
				},
				"party_name_extracted": {"type": ["string", "null"]},
				"party_id_matched": {"type": ["string", "null"]},
				"posting_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"reference_no": {"type": ["string", "null"]},
				"reference_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
				"currency": {"type": "string", "default": "INR"},
				"paid_amount": {"type": ["number", "null"]},
				"received_amount": {"type": ["number", "null"]},
				"paid_from_account_matched": {"type": ["string", "null"]},
				"paid_to_account_matched": {"type": ["string", "null"]},
				"mode_of_payment_matched": {"type": ["string", "null"]},
				"notes": {"type": ["string", "null"]}
			},
			"required": ["is_valid_document"]
		}
	}
}


_ITEM_TAX_STEPS = (
	"ITEMS — mandatory lookup for EVERY line item, no exceptions:\n"
	"  a) Call find_item(description, supplier=<matched_supplier_id>). ALWAYS pass supplier= — items\n"
	"     this supplier has bought/sold before appear first with source=supplier_history and are the\n"
	"     most reliable match. For ANY description that is short, abbreviated, or ambiguous\n"
	"     (e.g. 'AMC', 'AC', 'IT', 'rent', 'charges') you MUST also pass\n"
	"     context=<expanded meaning + any relevant invoice notes or header text>.\n"
	"     Example: find_item('AMC', supplier='ACC-001', context='Annual Maintenance Contract air conditioning servicing')\n"
	"  b) If find_item returns matches:[] — DO NOT stop. You MUST call list_all_items next.\n"
	"     Pass item_group=<your best guess at the group> if you can infer it; otherwise omit.\n"
	"     From the list, pick the item whose name/description is semantically closest.\n"
	"     Synonyms to apply when matching: AMC=maintenance=service=repair=servicing,\n"
	"     rent=rental=lease=hire, professional fees=consulting=advisory=retainer,\n"
	"     stationery=office supplies=consumables, housekeeping=cleaning=facility,\n"
	"     security=guard=surveillance, transport=freight=courier=logistics.\n"
	"  c) If list_all_items also returns nothing close → only then set item_code_matched=null.\n"
	"     A null without calling both find_item AND list_all_items is a VIOLATION of these rules.\n"
	"TAX — mandatory lookup for EVERY non-zero tax row:\n"
	"  a) Call find_tax_template('<full tax label from document e.g. GST 18%, IGST 12%>',\n"
	"     supplier=<matched_supplier_id>). ALWAYS pass supplier= — templates this supplier\n"
	"     has used before appear first with source=supplier_history and are the most reliable.\n"
	"     If a template is found AND its total rate mathematically matches the document → use it,\n"
	"     set taxes_and_charges_template, skip individual row lookups.\n"
	"  b) If no matching template → call find_tax_account(tax_type, tax_rate,\n"
	"     supplier=<matched_supplier_id>) for EACH tax row. ALWAYS pass supplier=.\n"
	"     Accounts this supplier has used before appear first with source=supplier_history.\n"
	"     Use the returned account name as account_head_matched on that row.\n"
	"     tax_type must be the exact label (e.g. 'CGST', 'SGST', 'IGST', 'TDS', 'Cess').\n"
	"     tax_rate must be the numeric rate (e.g. 9, 18, 2.5) — not a string.\n"
	"  c) If find_tax_account returns [] → you MUST call list_all_tax_accounts.\n"
	"     From that list pick the account whose name most closely matches the tax type and rate.\n"
	"  d) A null account_head_matched without completing steps a+b+c is a VIOLATION."
)

_ITEM_TAX_STEPS_CUSTOMER = _ITEM_TAX_STEPS.replace(
	"supplier=<matched_supplier_id>",
	"customer=<matched_customer_id>",
).replace(
	"supplier_history",
	"customer_history",
).replace(
	"find_item('AMC', supplier='ACC-001', context=",
	"find_item('AMC', customer='CUST-001', context=",
)

_PARTY_STEPS = (
	"PARTY MATCHING — mandatory:\n"
	"  a) Always pass both name AND gstin to find_supplier/find_customer when GSTIN is visible.\n"
	"     GSTIN is the most reliable identifier — an exact GSTIN match beats any name match.\n"
	"  b) If find_supplier/find_customer returns no match by GSTIN, retry with name only.\n"
	"  c) Strip legal suffixes before searching: Pvt, Ltd, Private, Limited, LLP, & Co, Inc.\n"
	"  d) After matching supplier/customer, always call get_supplier_defaults/get_customer_defaults\n"
	"     to pull payment terms, currency, and default accounts into the output."
)

TARGET_GUIDANCE = {
	"Purchase Invoice": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS + "\n"
		"3. check_duplicate_invoice(bill_no, supplier_id) — always check even if you think it is new.\n"
		"4. " + _ITEM_TAX_STEPS + "\n"
		"5. Verify: sum of all item amounts ≈ subtotal, subtotal + tax amounts ≈ grand_total.\n"
		"   If the totals do not reconcile with the document, re-read and correct — do not guess.\n"
		"6. Output final JSON"
	),
	"Purchase Receipt": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS + "\n"
		"3. If any PO reference number is visible on the document: find_purchase_order(po_number, supplier)\n"
		"4. " + _ITEM_TAX_STEPS + "\n"
		"5. Output final JSON"
	),
	"Supplier Quotation": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS + "\n"
		"3. " + _ITEM_TAX_STEPS + "\n"
		"4. Output final JSON"
	),
	"Sales Order": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS.replace("find_supplier", "find_customer").replace("get_supplier_defaults", "get_customer_defaults") + "\n"
		"3. " + _ITEM_TAX_STEPS_CUSTOMER + "\n"
		"4. Output final JSON"
	),
	"Quotation": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS.replace("find_supplier", "find_customer").replace("get_supplier_defaults", "get_customer_defaults") + "\n"
		"3. " + _ITEM_TAX_STEPS_CUSTOMER + "\n"
		"4. Output final JSON"
	),
	"Delivery Note": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. " + _PARTY_STEPS.replace("find_supplier", "find_customer").replace("get_supplier_defaults", "get_customer_defaults") + "\n"
		"3. " + _ITEM_TAX_STEPS_CUSTOMER + "\n"
		"4. Output final JSON"
	),
	"Payment Entry": (
		"TARGET WORKFLOW — follow every step in order, do not skip any:\n"
		"1. get_company_context\n"
		"2. Determine payment direction from document context:\n"
		"   - Money going OUT to a vendor → payment_type=Pay, party_type=Supplier → find_supplier\n"
		"   - Money coming IN from a customer → payment_type=Receive, party_type=Customer → find_customer\n"
		"3. " + _PARTY_STEPS + "\n"
		"4. Extract paid_amount and received_amount exactly as printed (apply rule 4).\n"
		"5. If a payment mode is visible (NEFT, RTGS, Cheque, UPI, Cash): call find_mode_of_payment.\n"
		"6. Output final JSON"
	),
}


def get_extraction_prompt(target_doctype="Purchase Invoice"):
	"""Return system prompt and JSON schema for the target DocType."""
	if target_doctype not in EXTRACTION_SCHEMAS:
		raise ValueError(f"No extraction schema defined for target DocType: {target_doctype}")

	schema = EXTRACTION_SCHEMAS[target_doctype]
	target_guidance = TARGET_GUIDANCE.get(target_doctype, "")

	prompt = f"{BASE_INSTRUCTIONS}\n\n"
	prompt += f"TARGET DOCUMENT TYPE: {target_doctype} ({schema['description']})\n"
	if target_guidance:
		prompt += f"{target_guidance}\n"
	prompt += "\nYou MUST return ONLY a JSON object that exactly matches this JSON schema:\n"
	prompt += json.dumps(schema["json_schema"], indent=2)

	return prompt
