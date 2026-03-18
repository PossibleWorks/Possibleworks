# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json
import frappe
from possibleworks.ap_invoice_processing.smart_search import (
	compute_item_similarity,
	execute_smart_search,
	SIMILARITY_THRESHOLD_PARTY,
	SIMILARITY_THRESHOLD_ITEM,
)
from possibleworks.ap_invoice_processing.business_memory import (
	get_party_history_item_candidates,
	get_party_tax_history,
)

# ==========================================
# Tool Definitions (JSON Schemas for OpenAI)
# ==========================================

OPENAI_TOOLS = [
	# ──────────────────────────────────────────
	# Tier 1: Core Extraction Tools
	# ──────────────────────────────────────────
	{
		"type": "function",
		"function": {
			"name": "get_company_context",
			"description": (
				"Returns the default company context for this ERPNext site. "
				"ALWAYS call this FIRST before any other tool so you know the company name, "
				"abbreviation (needed for account suffixes like '- PW'), default currency, and country. "
				"No parameters required."
			),
			"parameters": {
				"type": "object",
				"properties": {},
				"required": []
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_supplier",
			"description": (
				"Search ERPNext for a Supplier by name or GSTIN/Tax ID. Returns top 3 matches with confidence scores.\n\n"
				"🔍 Search Methods:\n"
				"- By supplier name (intelligent fuzzy matching - 'Schindler' will match 'Schindler India Pvt Ltd')\n"
				"- By GSTIN/Tax ID (exact match for precise identification)\n\n"
				"📋 Use Cases:\n"
				"- Identifying the vendor on a purchase invoice\n"
				"- Validating supplier information from scanned documents\n\n"
				"✅ Returns: Top 3 matches with name, supplier_name, confidence level, and similarity_score.\n\n"
				"⚠️ IMPORTANT: After finding a supplier, ALWAYS call get_supplier_defaults with the matched ID "
				"to fetch their configured tax template, payment terms, and other defaults."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"name": {"type": "string", "description": "The supplier name as printed on the invoice."},
					"gstin": {"type": "string", "description": "The GSTIN (15-char alphanumeric) of the supplier if visible on the document."}
				},
				"required": ["name"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "get_supplier_defaults",
			"description": (
				"After finding a supplier via find_supplier, call this to fetch all their configured defaults.\n\n"
				"Returns: payment terms template, default currency, credit-to account, "
				"price list, and cost center if explicitly configured for that supplier.\n\n"
				"Do NOT assume a generic company tax template from this tool. "
				"Tax templates/accounts must come from the printed tax section of the document "
				"or from find_tax_template / find_tax_account."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"supplier_id": {"type": "string", "description": "The exact Supplier ID/name from find_supplier."}
				},
				"required": ["supplier_id"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_item",
			"description": (
				"Search for an Item in ERPNext by name, code, or description. Returns top 3 matches.\n\n"				"🏆 ALWAYS pass supplier= when you have a matched supplier. Items this supplier has used before appear first with source=supplier_history and are the most reliable matches — prefer them over catalog results.\n\n"
				"🔍 Search Methods:\n"
				"- By item code/SKU (exact or fuzzy match)\n"
				"- By item name (fuzzy matching - 'Repair' will match 'Repair Service')\n"
				"- By description text (semantic similarity)\n\n"
				"📋 What IS an item:\n"
				"✓ Physical products: 'MacBook Pro', 'Steel Rods 12mm'\n"
				"✓ Services: 'Annual Maintenance', 'Repair Service', 'Consulting'\n"
				"✓ Software/Digital: 'OpenAI API Credits', 'License Fee'\n\n"
				"📋 What is NOT an item (NEVER search for these):\n"
				"✗ Tax lines: 'CGST', 'SGST', 'IGST', 'Service Tax'\n"
				"✗ Financial labels: 'Subtotal', 'Grand Total', 'Discount'\n"
				"✗ Headers: 'Description', 'Amount', 'Quantity'\n\n"
				"✅ Returns: Top 3 matches with item_code, item_name, stock_uom, and similarity_score.\n\n"
				"⚠️ Confidence rule: Do not set item_code_matched when the best result is only a weak fuzzy guess. "
				"If similarity is low or multiple items are close, leave item_code_matched as null.\n\n"
				"🚨 MANDATORY FALLBACK: When find_item returns matches:[] or no high-confidence match:\n"
				"   → You MUST call list_all_items next. Do NOT output null item_code without trying.\n"
				"   → Think semantically: 'AMC'='Annual Maintenance Contract', 'rent'='lease'='rental'\n"
				"   → Pick the best item_group from your system instructions (or omit for all items)\n"
				"   → Then map the description to the closest item in that list."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"description": {"type": "string", "description": "The line item description, product name, or service name from the invoice."},
					"context": {
						"type": "string",
						"description": (
							"Optional: extra context to improve matching for short or ambiguous descriptions. "
							"Include the expanded meaning of any acronym, the invoice subject/notes, "
							"and related words that indicate the service type. "
							"Example: for 'AMC' pass 'Annual Maintenance Contract air conditioning servicing repair gas charging'."
						)
					},
					"supplier": {
						"type": "string",
						"description": "Pass matched supplier ERPNext ID for purchase-side documents. History items appear first."
					},
					"customer": {
						"type": "string",
						"description": "Pass matched customer ERPNext ID for sales-side documents. History items appear first."
					}
				},
				"required": ["description"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "list_all_items",
			"description": (
				"Returns active items in ERPNext. Use this ALWAYS after find_item returns no matches.\n\n"
				"🔍 SEMANTIC MATCHING — think beyond exact words:\n"
				"- 'AMC' = 'Annual Maintenance Contract' = 'Maintenance Charges'\n"
				"- 'rent' = 'lease' = 'rental' = 'office rent'\n"
				"- 'repair' = 'repairing' = 'maintenance' = 'servicing'\n"
				"- 'professional fees' = 'consulting' = 'advisory charges'\n\n"
				"📋 item_group (optional but preferred):\n"
				"- Pass item_group to filter to a relevant category (faster, fewer tokens)\n"
				"- Groups are listed in your system instructions — pick the best fit\n"
				"- If unsure or no groups listed, OMIT item_group to get all items\n\n"
				"📋 MAPPING STRATEGY:\n"
				"- Match by meaning, not just spelling\n"
				"- Map ALL unmatched lines — call this once per invoice\n"
				"- Set item_code_matched to the matched item's item_code\n"
				"- Only leave item_code_matched null if truly nothing fits semantically"
			),
			"parameters": {
				"type": "object",
				"properties": {
					"item_group": {"type": "string", "description": "Optional: item group name to filter results (e.g., 'Services', 'Maintenance'). Omit to get all items."}
				},
				"required": []
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_purchase_order",
			"description": (
				"Search for open Purchase Orders. You can search by PO number OR by Supplier.\n\n"
				"📋 Use Cases:\n"
				"- When invoice references a PO number\n"
				"- When invoice lacks a PO number, search by Supplier to check for open orders\n"
				"- Linking invoice items to PO items\n\n"
				"✅ Returns: Up to 5 open POs with their line items and billed status."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"po_number": {"type": "string", "description": "The PO Number extracted from the invoice (optional)."},
					"supplier": {"type": "string", "description": "The exact Supplier ID to find open orders for (required if no po_number)."}
				}
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "check_duplicate_invoice",
			"description": (
				"Checks if a Purchase Invoice has already been created for this supplier with this exact bill number. "
				"If this returns a match, the current document is likely a duplicate.\n\n"
				"⚠️ Call this AFTER finding supplier to prevent double-entry."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"bill_no": {"type": "string", "description": "The invoice/bill number from the document."},
					"supplier": {"type": "string", "description": "The exact Supplier ID in ERPNext."}
				},
				"required": ["bill_no", "supplier"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_purchase_receipt",
			"description": "Checks if any Purchase Receipts exist for a given Purchase Order OR Supplier. Returns unbilled receipts.",
			"parameters": {
				"type": "object",
				"properties": {
					"purchase_order": {"type": "string", "description": "The exact Purchase Order ID."},
					"supplier": {"type": "string", "description": "The exact Supplier ID if PO is unknown."}
				}
			}
		}
	},
	# ──────────────────────────────────────────
	# Tier 2: Tax & Financial Tools
	# ──────────────────────────────────────────
	{
		"type": "function",
		"function": {
			"name": "find_tax_template",
			"description": (
				"Search for a Purchase Taxes and Charges Template by keyword.\n\n"
				"💡 Try this FIRST before using find_tax_account — a matching template "
				"auto-fills all tax rows at once.\n\n"
				"🔍 Search examples: 'GST 18%', 'GST 28%', 'Input GST', 'IGST', 'Tax Free', 'Zero Rated'\n\n"
				"✅ If found: set taxes_and_charges_template to the template name.\n"
				"   You do NOT need to call find_tax_account individually when a template is used.\n\n"
				"❌ If not found: fall back to find_tax_account for EACH individual tax row "
				"and set account_head_matched on each tax entry."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"keyword": {
						"type": "string",
						"description": "The tax description from the invoice (e.g., 'GST 18%', 'IGST 12%', 'Input GST')."
					},
					"supplier": {
						"type": "string",
						"description": "Pass matched supplier ERPNext ID for purchase-side documents. Previously used templates by this supplier appear first."
					},
					"customer": {
						"type": "string",
						"description": "Pass matched customer ERPNext ID for sales-side documents. Previously used templates by this customer appear first."
					}
				},
				"required": ["keyword"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_tax_account",
			"description": (
				"Finds the exact GL Account name for a specific tax type in the default company.\n\n"
				"🔍 Call this for EACH non-zero tax row on the invoice to get account_head_matched.\n\n"
				"📋 Examples:\n"
				"- CGST 9%  → find_tax_account(tax_type='CGST', tax_rate=9)\n"
				"- SGST 9%  → find_tax_account(tax_type='SGST', tax_rate=9)\n"
				"- IGST 18% → find_tax_account(tax_type='IGST', tax_rate=18)\n"
				"- TDS 2%   → find_tax_account(tax_type='TDS',  tax_rate=2)\n\n"
				"✅ Returns: Up to 3 matching accounts like 'Input Tax CGST - PW'.\n"
				"   Use the first result's `name` as account_head_matched.\n\n"
				"🚨 MANDATORY FALLBACK: If this returns [] → call list_all_tax_accounts "
				"to browse all tax accounts and pick the closest match manually."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"tax_type": {
						"type": "string",
						"enum": ["CGST", "SGST", "IGST", "TDS", "VAT", "Cess", "TCS", "Other"],
						"description": "The type of tax. Use 'Other' for cess, surcharge, or non-standard taxes."
					},
					"tax_rate": {
						"type": "number",
						"description": "The percentage rate of the tax (e.g., 9 for 9%). Use 0 if rate is not visible."
					},
					"supplier": {
						"type": "string",
						"description": "Pass matched supplier ERPNext ID. Previously used accounts for this tax type appear first."
					},
					"customer": {
						"type": "string",
						"description": "Pass matched customer ERPNext ID for sales-side documents. Previously used accounts appear first."
					}
				},
				"required": ["tax_type"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "list_all_tax_accounts",
			"description": (
				"Returns ALL tax accounts configured in ERPNext for the default company.\n\n"
				"🚨 MANDATORY FALLBACK: Call this when find_tax_account returns [] or wrong results.\n\n"
				"📋 Use this to:\n"
				"- Browse all available tax heads and pick the closest one\n"
				"- Handle unusual taxes (Cess, Surcharge, TCS, custom state taxes)\n"
				"- Confirm account names when find_tax_account gives low-confidence results\n\n"
				"✅ Returns: All accounts with account_type='Tax' for the company.\n\n"
				"💡 After calling this, set account_head_matched to the exact account name "
				"that matches the tax label on the invoice."
			),
			"parameters": {
				"type": "object",
				"properties": {},
				"required": []
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_expense_account",
			"description": (
				"Find the appropriate expense head (GL Account) for purchase line items.\n\n"
				"🔍 Search by keywords like 'repair', 'maintenance', 'office supplies', 'travel', etc.\n"
				"✅ Returns: Account name like 'Repair and Maintenance - PW'.\n\n"
				"💡 Use this when items don't have a default expense account configured."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"keyword": {"type": "string", "description": "Keyword describing the expense type (e.g., 'repair', 'office supplies', 'rent')."}
				},
				"required": ["keyword"]
			}
		}
	},
	# ──────────────────────────────────────────
	# Tier 3: Context & Linking Tools
	# ──────────────────────────────────────────
	{
		"type": "function",
		"function": {
			"name": "find_cost_center",
			"description": (
				"Find a Cost Center for department/project allocation.\n"
				"🔍 Search by name like 'Head Office', 'Marketing', 'Production'.\n"
				"✅ Returns: Full cost center name like 'Main - PW'."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"name": {"type": "string", "description": "Cost center name or keyword to search."}
				},
				"required": ["name"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_warehouse",
			"description": (
				"Find a Warehouse for stock item receipt.\n"
				"🔍 Search by name like 'Stores', 'Main Warehouse', 'Finished Goods'.\n"
				"✅ Returns: Full warehouse name like 'Stores - PW'."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"name": {"type": "string", "description": "Warehouse name or keyword to search."}
				},
				"required": ["name"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_project",
			"description": (
				"Find an active Project to link the invoice to.\n"
				"🔍 Search by project name or ID.\n"
				"✅ Returns: Project name and status."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"name": {"type": "string", "description": "Project name or keyword to search."}
				},
				"required": ["name"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_payment_terms",
			"description": (
				"Find a Payment Terms Template (e.g., 'Net 30', 'Net 60', '50% Advance').\n"
				"🔍 Search by keyword.\n"
				"✅ Returns: Template name and its schedule."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"keyword": {"type": "string", "description": "Payment terms keyword (e.g., 'Net 30', 'Immediate', 'Advance')."}
				},
				"required": ["keyword"]
			}
		}
	},
	# ──────────────────────────────────────────
	# Tier 4: AR (Accounts Receivable) Tools
	# ──────────────────────────────────────────
	{
		"type": "function",
		"function": {
			"name": "find_customer",
			"description": (
				"Search ERPNext for a Customer by name or GSTIN/Tax ID. Returns top 3 matches.\n\n"
				"🔍 Search Methods:\n"
				"- By customer name (intelligent fuzzy matching)\n"
				"- By GSTIN/Tax ID (exact match)\n\n"
				"📋 Use Cases:\n"
				"- Identifying the buyer on a Sales Invoice or quotation\n"
				"- Finding the customer for a Payment Entry\n\n"
				"✅ Returns: Top 3 matches with name, customer_name, confidence, similarity_score.\n\n"
				"⚠️ After finding a customer, call get_customer_defaults to fetch their tax template and payment terms."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"name": {"type": "string", "description": "The customer name as printed on the document."},
					"gstin": {"type": "string", "description": "The GSTIN (15-char alphanumeric) if visible."}
				},
				"required": ["name"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "get_customer_defaults",
			"description": (
				"After finding a customer via find_customer, call this to fetch all their configured defaults.\n\n"
				"Returns: payment terms template, default currency, debit-to account, "
				"default price list, customer group, and territory when explicitly configured.\n\n"
				"Do NOT assume a generic company sales tax template from this tool. "
				"Tax templates/accounts must come from the printed tax section of the document "
				"or from find_tax_template / find_tax_account."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"customer_id": {"type": "string", "description": "The exact Customer ID/name from find_customer."}
				},
				"required": ["customer_id"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_sales_order",
			"description": (
				"Search for open Sales Orders by SO number OR Customer ID.\n\n"
				"📋 Use Cases:\n"
				"- Linking a Sales Invoice to its originating Sales Order\n"
				"- Checking billed vs delivered status when SO number is missing from document\n\n"
				"✅ Returns: Up to 5 open SOs with their line items and billed/delivered quantities."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"so_number": {"type": "string", "description": "The Sales Order number (optional)."},
					"customer": {"type": "string", "description": "Exact Customer ID to search if so_number is missing."}
				}
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_delivery_note",
			"description": (
				"Find unbilled Delivery Notes linked to a Sales Order OR a specific Customer.\n\n"
				"✅ Returns: List of open Delivery Note IDs and their billed status."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"sales_order": {"type": "string", "description": "The exact Sales Order ID."},
					"customer": {"type": "string", "description": "Exact Customer ID if SO is unknown."}
				}
			}
		}
	},
	# ──────────────────────────────────────────
	# Tier 5: Payment & Banking Tools
	# ──────────────────────────────────────────
	{
		"type": "function",
		"function": {
			"name": "find_mode_of_payment",
			"description": (
				"Find a Mode of Payment configured in ERPNext.\n\n"
				"🔍 Search: 'Cash', 'Bank Transfer', 'Cheque', 'Wire Transfer', 'UPI', 'NEFT/RTGS'.\n"
				"✅ Returns: Mode of Payment name and its linked default account."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"keyword": {"type": "string", "description": "Payment method keyword (e.g., 'Cash', 'Bank Transfer', 'UPI')."}
				},
				"required": ["keyword"]
			}
		}
	},
	{
		"type": "function",
		"function": {
			"name": "find_bank_account",
			"description": (
				"Find a Bank Account (GL Account of type Bank/Cash) for payment processing.\n\n"
				"🔍 Search by bank name or account keyword.\n"
				"✅ Returns: Full GL account name like 'HDFC Bank - PW' or 'Cash - PW'."
			),
			"parameters": {
				"type": "object",
				"properties": {
					"keyword": {"type": "string", "description": "Bank name or keyword (e.g., 'HDFC', 'SBI', 'Cash', 'Petty Cash')."}
				},
				"required": ["keyword"]
			}
		}
	}
]


# ==========================================
# Tool Execution Logic
# ==========================================

def execute_tool(tool_name, arguments):
	"""Dispatcher for OpenAI tool calls."""
	if isinstance(arguments, str):
		args = json.loads(arguments)
	else:
		args = arguments

	try:
		if tool_name == "get_company_context":
			return json.dumps(_get_company_context())

		elif tool_name == "find_supplier":
			return json.dumps(_find_supplier(args.get("name"), args.get("gstin")))

		elif tool_name == "get_supplier_defaults":
			return json.dumps(_get_supplier_defaults(args.get("supplier_id")))

		elif tool_name == "find_item":
			return json.dumps(_find_item(args.get("description"), context=args.get("context"), supplier=args.get("supplier"), customer=args.get("customer")))

		elif tool_name == "list_all_items":
			return json.dumps(_list_all_items(args.get("item_group")))

		elif tool_name == "find_purchase_order":
			return json.dumps(
				_find_purchase_order(
					args.get("po_number"),
					args.get("supplier") or args.get("supplier_name")
				)
			)

		elif tool_name == "find_tax_account":
			return json.dumps(_find_tax_account(
			args.get("tax_type"), args.get("tax_rate"),
			supplier=args.get("supplier"), customer=args.get("customer")
		))

		elif tool_name == "list_all_tax_accounts":
			return json.dumps(_list_all_tax_accounts())

		elif tool_name == "find_tax_template":
			return json.dumps(_find_tax_template(
			args.get("keyword"),
			supplier=args.get("supplier"), customer=args.get("customer")
		))

		elif tool_name == "find_expense_account":
			return json.dumps(_find_expense_account(args.get("keyword")))

		elif tool_name == "find_purchase_receipt":
			return json.dumps(
				_find_purchase_receipt(
					purchase_order=args.get("purchase_order") or args.get("purchase_order_name"),
					supplier=args.get("supplier")
				)
			)

		elif tool_name == "check_duplicate_invoice":
			return json.dumps(_check_duplicate_invoice(args.get("bill_no"), args.get("supplier")))

		elif tool_name == "find_cost_center":
			return json.dumps(_find_cost_center(args.get("name")))

		elif tool_name == "find_warehouse":
			return json.dumps(_find_warehouse(args.get("name")))

		elif tool_name == "find_project":
			return json.dumps(_find_project(args.get("name")))

		elif tool_name == "find_payment_terms":
			return json.dumps(_find_payment_terms(args.get("keyword")))

		elif tool_name == "find_customer":
			return json.dumps(_find_customer(args.get("name"), args.get("gstin")))

		elif tool_name == "get_customer_defaults":
			return json.dumps(_get_customer_defaults(args.get("customer_id")))

		elif tool_name == "find_sales_order":
			return json.dumps(_find_sales_order(args.get("so_number"), args.get("customer")))

		elif tool_name == "find_delivery_note":
			return json.dumps(_find_delivery_note(args.get("sales_order_name")))

		elif tool_name == "find_mode_of_payment":
			return json.dumps(_find_mode_of_payment(args.get("keyword")))

		elif tool_name == "find_bank_account":
			return json.dumps(_find_bank_account(args.get("keyword")))

		else:
			return json.dumps({"error": f"Unknown tool: {tool_name}"})

	except Exception as e:
		frappe.log_error(f"Agent Tool Error: {tool_name}", str(e))
		return json.dumps({"error": str(e)})


# ==========================================
# Tool Implementations
# ==========================================

def _get_company_context():
	"""Returns default company info for context injection."""
	company = frappe.defaults.get_user_default("Company")
	if not company:
		company = frappe.db.get_single_value("Global Defaults", "default_company")

	if not company:
		# Fallback: get the first company
		companies = frappe.get_all("Company", limit=1, fields=["name", "abbr", "default_currency", "country"])
		if companies:
			c = companies[0]
			return {
				"company": c.name,
				"abbreviation": c.abbr,
				"default_currency": c.default_currency,
				"country": c.country
			}
		return {"error": "No company found in ERPNext."}

	doc = frappe.get_cached_doc("Company", company)
	return {
		"company": doc.name,
		"abbreviation": doc.abbr,
		"default_currency": doc.default_currency,
		"country": doc.country
	}


def _find_supplier(name, gstin=None):
	"""Returns top 3 matches using smart search with fuzzy matching."""
	# Try exact GSTIN match first (highest priority)
	if gstin:
		gstin = gstin.strip().upper()
		try:
			exact = frappe.db.get_value(
				"Supplier",
				{"tax_id": gstin, "disabled": 0},
				["name", "supplier_name"],
				as_dict=True
			)
			if exact:
				return [{
					"name": exact.name,
					"supplier_name": exact.supplier_name,
					"confidence": "exact",
					"similarity_score": 1.0,
					"matched_on": "GSTIN"
				}]
		except Exception:
			pass

	# Search by name with fuzzy matching
	search_fields = ["supplier_name", "name"]

	# Check if custom fields exist
	meta = frappe.get_meta("Supplier")
	if meta.has_field("alias"):
		search_fields.append("alias")
	if meta.has_field("abbreviation"):
		search_fields.append("abbreviation")

	results = execute_smart_search(
		"Supplier",
		search_fields,
		name,
		filters={"disabled": 0},
		return_fields=["name", "supplier_name"],
		similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
		limit=3
	)
	return results[:3]


def _list_all_items(item_group=None):
	"""Returns ALL active items so the AI can make contextual mapping decisions."""
	filters = {"disabled": 0}
	if item_group:
		filters["item_group"] = ["like", f"%{item_group}%"]

	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["item_code", "item_name", "item_group", "description", "stock_uom"],
		limit_page_length=200,
		order_by="item_name asc"
	)

	# Return a compact format for token efficiency
	return [{
		"item_code": i.item_code,
		"item_name": i.item_name,
		"item_group": i.item_group,
		"uom": i.stock_uom,
		"description": (i.description or "")[:100]  # Truncate long descriptions
	} for i in items]


def _get_supplier_defaults(supplier_id):
	"""Fetch supplier's configured defaults for auto-fill."""
	try:
		doc = frappe.get_cached_doc("Supplier", supplier_id)

		result = {
			"supplier_id": supplier_id,
			"supplier_name": doc.supplier_name,
			"default_currency": doc.default_currency or None,
			"tax_id": doc.tax_id or None,
			"supplier_group": doc.supplier_group or None,
			"payment_terms": doc.payment_terms or None,
		}

		# Get company-specific defaults
		company = frappe.defaults.get_user_default("Company") or \
				  frappe.db.get_single_value("Global Defaults", "default_company")

		if company and hasattr(doc, "accounts") and doc.accounts:
			for acc in doc.accounts:
				if acc.company == company:
					result["default_account"] = acc.account or None

		# Try to find default tax template from supplier group
		if doc.supplier_group:
			try:
				sg = frappe.get_cached_doc("Supplier Group", doc.supplier_group)
				if hasattr(sg, "accounts") and sg.accounts:
					for acc in sg.accounts:
						if acc.company == company:
							result["default_account_from_group"] = acc.account or None
			except Exception:
				pass

		return result

	except Exception as e:
		return {"error": f"Could not fetch supplier defaults: {str(e)}"}


_NO_MATCH_DIRECTIVE = (
	"No direct match found. MANDATORY NEXT STEP: call list_all_items to find the item semantically. "
	"Think beyond exact words — 'AMC'='Annual Maintenance', 'rent'='lease', 'repair'='maintenance'. "
	"Pick the best item_group from your system instructions and call list_all_items. "
	"Only set item_code_matched=null if list_all_items also returns nothing relevant."
)


def _find_item(description, context=None, supplier=None, customer=None):
	"""Returns top 5 matches combining party history + catalog search.

	Party history (supplier or customer) is checked first and always surfaces
	items that were used in previous transactions, even when text similarity is
	low.  This allows the AI to recognise semantic matches such as
	"AMC" → "Annual Maintenance Contract" that character-level search would miss.

	Result priority:
	  1. History items with strong text similarity (high/medium confidence)
	  2. Top history items by usage frequency (history_only confidence — let AI judge)
	  3. Catalog search results not already in history

	When `context` is provided it is appended to the enriched search query so
	short/ambiguous descriptions get a richer signal for catalog search.
	"""
	if not description:
		return {"matches": [], "directive": _NO_MATCH_DIRECTIVE}

	context_str = str(context or "").strip()
	enriched = f"{description.strip()} {context_str}".strip() if context_str else description.strip()

	# ── Party history pass ────────────────────────────────────────────────────
	# Determine which party to look up.
	party_id = supplier or customer
	party_type = "Customer" if (customer and not supplier) else "Supplier"
	history_matches = []
	seen_codes: set = set()

	if party_id:
		try:
			from possibleworks.ap_invoice_processing.business_memory import (
				get_party_history_item_candidates,
			)
			candidates = get_party_history_item_candidates(party_type, party_id)

			# Score every history candidate by text similarity.
			scored_history = []
			for candidate in (candidates or [])[:50]:
				item_code = candidate.get("item_code")
				if not item_code:
					continue
				texts = candidate.get("texts") or [item_code]
				text_score = max(
					(compute_item_similarity(description.strip(), t) for t in texts if t),
					default=0.0,
				)
				freq = float(candidate.get("weighted_count") or candidate.get("count") or 0.0)
				# Combined rank: 60% text similarity + 40% frequency signal (capped)
				rank_score = (text_score * 0.60) + min(freq * 0.05, 0.40)
				scored_history.append((rank_score, text_score, freq, candidate))

			# Sort by rank_score so strong text matches rise to the top.
			scored_history.sort(key=lambda x: x[0], reverse=True)

			# Always include top-5 from history regardless of text score.
			# The AI can recognise a semantic match that text similarity misses.
			for rank_score, text_score, freq, candidate in scored_history[:5]:
				item_code = candidate.get("item_code")
				if item_code in seen_codes:
					continue
				seen_codes.add(item_code)
				if text_score >= 0.72:
					conf = "high"
				elif text_score >= 0.50:
					conf = "medium"
				else:
					# Low text similarity but in history — show so AI can judge semantics.
					conf = "history_only"
				desc_preview = (candidate.get("texts") or [None])[0]
				history_matches.append({
					"item_code": item_code,
					"item_name": candidate.get("item_name") or item_code,
					"similarity_score": round(text_score, 4),
					"confidence": conf,
					"source": f"{party_type.lower()}_history",
					"times_used": int(candidate.get("count") or 0),
					"description_in_erp": (desc_preview or "")[:80],
				})
		except Exception:
			pass

	# ── Catalog search ────────────────────────────────────────────────────────
	results = execute_smart_search(
		"Item",
		["item_name", "item_code", "description"],
		enriched,
		filters={"disabled": 0},
		return_fields=["name", "item_name", "item_code", "stock_uom", "description", "item_group"],
		similarity_threshold=SIMILARITY_THRESHOLD_ITEM,
		limit=3,
	)
	catalog_results = []
	for result in (results or []):
		score = float(result.get("similarity_score") or 0.0)
		confidence = str(result.get("confidence") or "")
		if confidence in {"exact", "high", "medium"} or score >= 0.55:
			item_code = result.get("item_code") or result.get("name")
			if item_code and item_code not in seen_codes:
				seen_codes.add(item_code)
				catalog_results.append(result)

	# ── Merge and respond ─────────────────────────────────────────────────────
	combined = history_matches + catalog_results
	if not combined:
		return {"matches": [], "directive": _NO_MATCH_DIRECTIVE}

	response = {"matches": combined[:5]}
	if history_matches:
		response["history_note"] = (
			f"Items with source={party_type.lower()}_history are from previous transactions with this "
			f"{party_type.lower()}. Even when similarity_score is low (confidence=history_only), "
			"check if item_name semantically matches the invoice description before trying list_all_items."
		)
	return response


def _find_purchase_order(po_number=None, supplier=None):
	"""Finds PO and returns its items with billed quantity mapping."""
	if not po_number and not supplier:
		return {"error": "Must provide either po_number or supplier."}

	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled"]]}
	
	po_list = []
	if po_number:
		po_results = execute_smart_search(
			"Purchase Order",
			["name"],
			po_number,
			filters=filters,
			return_fields=["name", "supplier", "grand_total", "status"]
		)
		if po_results:
			po_list = [po_results[0]]
	elif supplier:
		filters["supplier"] = supplier
		po_list = frappe.db.get_all(
			"Purchase Order",
			filters=filters,
			fields=["name", "supplier", "grand_total", "status"],
			order_by="creation desc",
			limit=5
		)

	if not po_list:
		return {"error": "No open Purchase Orders found matching criteria."}

	results = []
	for po in po_list:
		po_name = po["name"]
		# Fetch Line Items and their billed status
		items = frappe.db.get_all(
			"Purchase Order Item",
			filters={"parent": po_name},
			fields=["item_code", "item_name", "qty", "rate", "amount", "billed_amt", "received_qty"]
		)
		results.append({
			"purchase_order": po_name,
			"supplier": po["supplier"],
			"grand_total": po["grand_total"],
			"status": po["status"],
			"items": items
		})

	return results


def _find_purchase_receipt(purchase_order=None, supplier=None):
	"""Returns list of unbilled Purchase Receipts."""
	if not purchase_order and not supplier:
		return {"error": "Must provide either purchase_order or supplier."}

	filters = {
		"docstatus": 1,
		"status": ["!=", "Closed"],
		"per_billed": ["<", 100]
	}
	if purchase_order:
		# Search items for parent
		pr_items = frappe.db.get_all(
			"Purchase Receipt Item",
			filters={"purchase_order": purchase_order, "docstatus": 1},
			fields=["parent"],
			distinct=True
		)
		prs = [i.parent for i in pr_items]
		if prs:
			filters["name"] = ["in", prs]
		else:
			return []
	elif supplier:
		filters["supplier"] = supplier

	return frappe.db.get_all(
		"Purchase Receipt",
		filters=filters,
		fields=["name", "supplier", "grand_total", "per_billed", "status"],
		order_by="creation desc",
		limit=5
	)


def _find_tax_template(keyword, supplier=None, customer=None):
	"""Search Purchase Taxes and Charges Templates.
	Checks party history first so previously-used templates surface at the top.
	Tries company-scoped search, then falls back to any matching template.
	"""
	if not keyword:
		return {"error": "No keyword provided."}

	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	# ── Party history: surface previously-used templates first ───────────────
	history_templates = []
	party_id = supplier or customer
	if party_id:
		party_type = "Customer" if (customer and not supplier) else "Supplier"
		try:
			tax_hist = get_party_tax_history(party_type, party_id, company=company)
			for tmpl_entry in (tax_hist.get("templates") or [])[:3]:
				tname = tmpl_entry.get("template_name")
				if tname:
					history_templates.append({
						"name": tname,
						"title": tname,
						"is_default": 0,
						"confidence": "high",
						"source": f"{party_type.lower()}_history",
						"times_used": tmpl_entry.get("count", 0),
					})
		except Exception:
			pass

	def _search_templates(extra_filters):
		f = {"disabled": 0}
		f.update(extra_filters)
		return execute_smart_search(
			"Purchase Taxes and Charges Template",
			["name", "title"],
			keyword,
			filters=f,
			return_fields=["name", "title", "is_default"],
			limit=3,
		)

	catalog_results = _search_templates({"company": company} if company else {})
	if not catalog_results and company:
		catalog_results = _search_templates({})

	# Merge: history first, catalog fills without duplicates
	seen_names = {h["name"] for h in history_templates}
	for r in (catalog_results or []):
		if r.get("name") not in seen_names:
			history_templates.append(r)
			seen_names.add(r.get("name"))

	results = history_templates
	if not results:
		return {"found": False, "message": f"No tax template found matching '{keyword}'. Use find_tax_account to match individual tax rows instead."}

	best = results[0]
	try:
		doc = frappe.get_doc("Purchase Taxes and Charges Template", best["name"])
		tax_rows = []
		for row in doc.taxes:
			tax_rows.append({
				"charge_type": row.charge_type,
				"account_head": row.account_head,
				"description": row.description,
				"rate": row.rate,
			})
		response = {
			"found": True,
			"template_name": best["name"],
			"title": best.get("title", ""),
			"is_default": best.get("is_default", 0),
			"confidence": best.get("confidence", "high"),
			"tax_rows": tax_rows,
		}
		if best.get("source"):
			response["source"] = best["source"]
			response["times_used"] = best.get("times_used", 0)
		if len(results) > 1:
			response["other_candidates"] = [r.get("name") for r in results[1:3]]
		return response
	except Exception as e:
		return {"found": True, "template_name": best["name"], "error": str(e)}


def _find_tax_account(tax_type, tax_rate, supplier=None, customer=None):
	"""Finds tax GL accounts filtered by account_type='Tax' for the default company.
	Checks party history first to surface previously-used accounts for this tax type.
	Falls back to catalog search with multiple strategies.
	Returns top 3 candidates.
	"""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	# ── Party history: surface previously-used accounts for this tax type ─────
	history_accounts = []
	party_id = supplier or customer
	if party_id and tax_type:
		party_type = "Customer" if (customer and not supplier) else "Supplier"
		try:
			tax_hist = get_party_tax_history(party_type, party_id, company=company)
			# tax_accounts is a dict keyed by tax type: {CGST: [{account, rate, count}], ...}
			bucket = (tax_hist.get("tax_accounts") or {}).get(tax_type, [])
			for entry in bucket[:3]:
				account = entry.get("account")
				if account:
					history_accounts.append({
						"name": account,
						"account_name": account.split(" - ")[0] if " - " in account else account,
						"account_type": "Tax",
						"confidence": "high",
						"source": f"{party_type.lower()}_history",
						"times_used": entry.get("count", 0),
						"historical_rate": entry.get("rate", 0),
					})
		except Exception:
			pass

	base_filters = {"company": company, "is_group": 0, "account_type": "Tax"}

	search_attempts = []
	if tax_rate and float(tax_rate) > 0:
		search_attempts.append(f"{tax_type} {float(tax_rate):.0f}%")
		search_attempts.append(f"{tax_type} {float(tax_rate):.0f}")
	search_attempts.append(f"Input {tax_type}")
	search_attempts.append(f"Input Tax {tax_type}")
	search_attempts.append(tax_type)

	seen = {h["name"] for h in history_accounts}
	catalog_results = []
	for term in search_attempts:
		if len(catalog_results) >= 3:
			break
		candidates = execute_smart_search(
			"Account",
			["account_name", "name"],
			term,
			filters=base_filters,
			return_fields=["name", "account_name", "account_type"],
			limit=3,
		)
		for c in (candidates or []):
			name = c.get("name")
			if name and name not in seen:
				seen.add(name)
				catalog_results.append(c)

	# Fallback: try without account_type filter in case chart of accounts
	# doesn't have account_type set (common in customised ERPNext setups).
	if not catalog_results:
		loose_filters = {"company": company, "is_group": 0}
		for term in [tax_type, f"Input {tax_type}"]:
			if len(catalog_results) >= 3:
				break
			candidates = execute_smart_search(
				"Account",
				["account_name", "name"],
				term,
				filters=loose_filters,
				return_fields=["name", "account_name", "account_type"],
				limit=3,
			)
			for c in (candidates or []):
				name = c.get("name")
				if name and name not in seen:
					seen.add(name)
					catalog_results.append(c)

	# Merge: history first, catalog fills remaining
	combined = history_accounts + catalog_results
	return combined[:3]
def _find_expense_account(keyword):
	"""Find expense head GL account by keyword."""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	filters = {"company": company, "is_group": 0, "root_type": "Expense"}

	results = execute_smart_search(
		"Account",
		["account_name", "name"],
		keyword,
		filters=filters,
		return_fields=["name", "account_name"],
		limit=3
	)

	return results[:3] if results else []


def _list_all_tax_accounts():
	"""Returns all tax-type accounts for the default company."""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	filters = {"is_group": 0, "account_type": "Tax"}
	if company:
		filters["company"] = company

	accounts = frappe.get_all(
		"Account",
		filters=filters,
		fields=["name", "account_name", "account_type", "root_type"],
		order_by="account_name asc",
		limit_page_length=100,
	)

	# Fallback: if no account_type=Tax accounts found, fetch likely tax accounts by name
	if not accounts and company:
		all_co_accounts = frappe.get_all(
			"Account",
			filters={"company": company, "is_group": 0},
			fields=["name", "account_name", "account_type", "root_type"],
			order_by="account_name asc",
			limit_page_length=200,
		)
		tax_keywords = {"gst", "cgst", "sgst", "igst", "tds", "tax", "cess", "tcs", "vat"}
		accounts = [
			a for a in all_co_accounts
			if any(kw in (a.account_name or "").lower() for kw in tax_keywords)
		]

	return [
		{"name": a.name, "account_name": a.account_name, "account_type": a.account_type}
		for a in accounts
	]


def _check_duplicate_invoice(bill_no, supplier):
	"""Checks for duplicate invoice (submitted or draft)."""
	if not bill_no or not supplier:
		return {"is_duplicate": False}

	bill_no_clean = str(bill_no).strip()

	# Check for submitted duplicate first (highest concern).
	submitted = frappe.db.get_value(
		"Purchase Invoice",
		{"supplier": supplier, "bill_no": bill_no_clean, "docstatus": 1},
		"name"
	)
	if submitted:
		return {
			"is_duplicate": True,
			"duplicate_invoice_id": submitted,
			"duplicate_status": "Submitted",
			"note": f"A submitted Purchase Invoice '{submitted}' already exists for this supplier with this bill number."
		}

	# Also check drafts — not a hard block but worth flagging.
	draft = frappe.db.get_value(
		"Purchase Invoice",
		{"supplier": supplier, "bill_no": bill_no_clean, "docstatus": 0},
		"name"
	)
	if draft:
		return {
			"is_duplicate": True,
			"duplicate_invoice_id": draft,
			"duplicate_status": "Draft",
			"note": f"A draft Purchase Invoice '{draft}' exists for this supplier with this bill number. It has not been submitted yet — this may not be a true duplicate."
		}

	return {"is_duplicate": False}


def _find_cost_center(name):
	"""Find cost center by name."""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	filters = {"is_group": 0}
	if company:
		filters["company"] = company

	results = execute_smart_search(
		"Cost Center",
		["cost_center_name", "name"],
		name,
		filters=filters,
		return_fields=["name", "cost_center_name"],
		limit=3
	)
	return results[:1] if results else []


def _find_warehouse(name):
	"""Find warehouse by name."""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	filters = {"is_group": 0}
	if company:
		filters["company"] = company

	results = execute_smart_search(
		"Warehouse",
		["warehouse_name", "name"],
		name,
		filters=filters,
		return_fields=["name", "warehouse_name"],
		limit=3
	)
	return results[:1] if results else []


def _find_project(name):
	"""Find an active project by name."""
	results = execute_smart_search(
		"Project",
		["project_name", "name"],
		name,
		filters={"status": "Open"},
		return_fields=["name", "project_name", "status"],
		limit=3
	)
	return results[:1] if results else []


def _find_payment_terms(keyword):
	"""Find a payment terms template by keyword."""
	results = execute_smart_search(
		"Payment Terms Template",
		["template_name", "name"],
		keyword,
		filters={},
		return_fields=["name", "template_name"],
		limit=3
	)
	return results[:1] if results else []


# ==========================================
# AR (Accounts Receivable) Tool Implementations
# ==========================================

def _find_customer(name, gstin=None):
	"""Returns top 3 customer matches using smart search with fuzzy matching."""
	# Try exact GSTIN match first
	if gstin:
		gstin = gstin.strip().upper()
		try:
			exact = frappe.db.get_value(
				"Customer",
				{"tax_id": gstin, "disabled": 0},
				["name", "customer_name"],
				as_dict=True
			)
			if exact:
				return [{
					"name": exact.name,
					"customer_name": exact.customer_name,
					"confidence": "exact",
					"similarity_score": 1.0,
					"matched_on": "GSTIN"
				}]
		except Exception:
			pass

	search_fields = ["customer_name", "name"]

	# Check for alias field
	meta = frappe.get_meta("Customer")
	if meta.has_field("alias"):
		search_fields.append("alias")

	results = execute_smart_search(
		"Customer",
		search_fields,
		name,
		filters={"disabled": 0},
		return_fields=["name", "customer_name", "customer_group", "territory"],
		similarity_threshold=SIMILARITY_THRESHOLD_PARTY,
		limit=3
	)
	return results[:3]


def _get_customer_defaults(customer_id):
	"""Fetch customer's configured defaults for auto-fill."""
	try:
		doc = frappe.get_cached_doc("Customer", customer_id)

		result = {
			"customer_id": customer_id,
			"customer_name": doc.customer_name,
			"customer_group": doc.customer_group or None,
			"territory": doc.territory or None,
			"default_currency": doc.default_currency or None,
			"tax_id": doc.tax_id or None,
			"payment_terms": doc.payment_terms or None,
			"default_price_list": doc.default_price_list or None,
		}

		# Get company-specific defaults
		company = frappe.defaults.get_user_default("Company") or \
				  frappe.db.get_single_value("Global Defaults", "default_company")

		if company and hasattr(doc, "accounts") and doc.accounts:
			for acc in doc.accounts:
				if acc.company == company:
					result["default_account"] = acc.account or None

		return result

	except Exception as e:
		return {"error": f"Could not fetch customer defaults: {str(e)}"}


def _find_sales_order(so_number=None, customer=None):
	"""Finds Sales Order and returns its items with billed/delivered status."""
	filters = {"docstatus": 1, "status": ["not in", ["Closed", "Completed", "Cancelled"]]}

	if not so_number and not customer:
		return {"error": "Must provide either so_number or customer."}

	if so_number:
		so_results = execute_smart_search(
			"Sales Order",
			["name"],
			so_number,
			filters=filters,
			return_fields=["name", "customer", "grand_total", "status"]
		)
		if not so_results:
			return {"error": "No open Sales Order found matching that reference."}
		so = so_results[0]
		so_name = so["name"]
		items = frappe.db.get_all(
			"Sales Order Item",
			filters={"parent": so_name},
			fields=["item_code", "item_name", "qty", "rate", "amount", "billed_amt", "delivered_qty"]
		)
		return {
			"sales_order": so_name,
			"customer": so["customer"],
			"grand_total": so["grand_total"],
			"status": so["status"],
			"confidence": so.get("confidence", "high"),
			"items": items
		}

	# Customer-only lookup — return list of open SOs
	filters["customer"] = customer
	sos = frappe.db.get_all(
		"Sales Order",
		filters=filters,
		fields=["name", "customer", "grand_total", "status"],
		order_by="creation desc",
		limit=5
	)
	if not sos:
		return {"error": f"No open Sales Orders found for customer '{customer}'."}
	return [{"sales_order": s.name, "customer": s.customer, "grand_total": s.grand_total, "status": s.status} for s in sos]


def _find_delivery_note(sales_order_name):
	"""Checks if Delivery Notes exist for a Sales Order."""
	dns = frappe.db.sql("""
		SELECT DISTINCT parent
		FROM `tabDelivery Note Item`
		WHERE against_sales_order = %s AND docstatus = 1
	""", (sales_order_name,), as_dict=True)

	return [dn.parent for dn in dns]


# ==========================================
# Payment & Banking Tool Implementations
# ==========================================

def _find_mode_of_payment(keyword):
	"""Find Mode of Payment and its linked default account."""
	results = execute_smart_search(
		"Mode of Payment",
		["name"],
		keyword,
		filters={},
		return_fields=["name", "type"],
		limit=3
	)

	if not results:
		return []

	# Enrich top result with the linked default account
	best = results[0]
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	try:
		doc = frappe.get_doc("Mode of Payment", best["name"])
		for acc in doc.accounts:
			if acc.company == company:
				best["default_account"] = acc.default_account
				break
	except Exception:
		pass

	return results[:3]


def _find_bank_account(keyword):
	"""Find a Bank/Cash GL account for payment processing."""
	company = frappe.defaults.get_user_default("Company") or \
			  frappe.db.get_single_value("Global Defaults", "default_company")

	filters = {
		"company": company,
		"is_group": 0,
		"account_type": ["in", ["Bank", "Cash"]],
	}

	results = execute_smart_search(
		"Account",
		["account_name", "name"],
		keyword,
		filters=filters,
		return_fields=["name", "account_name", "account_type"],
		limit=3
	)
	return results[:3] if results else []
