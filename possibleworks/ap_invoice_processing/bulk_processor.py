# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json
import time
import traceback
import zipfile
from io import BytesIO

import frappe
from frappe.utils.file_manager import save_file

from possibleworks.ap_invoice_processing.constants import (
	CUSTOMER_SIDE_DOCTYPES,
	ROLLOUT_DOCTYPES,
	SUPPLIER_SIDE_DOCTYPES,
	get_queue_doctype,
)
from possibleworks.ap_invoice_processing.business_memory import (
	resolve_purchase_history_defaults,
)
from possibleworks.ap_invoice_processing.doctype.ai_document_processor_settings.ai_document_processor_settings import (
	APProcessorSettings,
)
from possibleworks.ap_invoice_processing.openai_service import extract_data_from_file
from possibleworks.ap_invoice_processing.smart_match import (
	_template_matches_document_tax,
	perform_smart_match,
)
from possibleworks.ap_invoice_processing.smart_search import execute_smart_search


def _normalize_file_urls(value):
	"""Normalize file URLs from list/json-string/single-string into a clean list."""
	if not value:
		return []

	if isinstance(value, (list, tuple, set)):
		return [str(v).strip() for v in value if str(v).strip()]

	if isinstance(value, str):
		raw = value.strip()
		if not raw:
			return []

		# Try JSON first (e.g. '["/files/a.pdf", "/files/b.pdf"]')
		try:
			parsed = json.loads(raw)
			if isinstance(parsed, list):
				return [str(v).strip() for v in parsed if str(v).strip()]
			if isinstance(parsed, str) and parsed.strip():
				return [parsed.strip()]
		except Exception:
			pass

		# Fallback: comma/newline separated text
		parts = raw.replace("\n", ",").split(",")
		return [p.strip() for p in parts if p.strip()]

	return []


@frappe.whitelist()
def enqueue_bulk_processing(file_urls=None, zip_file_url=None, target_doctype="Purchase Invoice"):
	"""
	API called from the frontend "Bulk Process" dialog.
	Creates ONE queue record for the entire batch, then enqueues a background job.
	"""
	batch_id = frappe.generate_hash(length=10)
	user = frappe.session.user

	if target_doctype not in ROLLOUT_DOCTYPES:
		frappe.throw(
			f"Target DocType '{target_doctype}' is not enabled for AI bulk creation. "
			f"Allowed doctypes: {', '.join(ROLLOUT_DOCTYPES)}"
		)

	if not APProcessorSettings.is_enabled():
		frappe.throw("AI Document Processing is disabled in AI Document Processor Settings.")

	if not APProcessorSettings.is_doctype_supported(target_doctype):
		frappe.throw(
			f"DocType '{target_doctype}' is disabled in AI Document Processor Settings."
		)

	process_urls = []

	if file_urls:
		process_urls.extend(_normalize_file_urls(file_urls))

	if zip_file_url:
		extracted_urls = _extract_zip_file(zip_file_url)
		process_urls.extend(extracted_urls)

	# Keep ordering stable while removing accidental duplicates
	process_urls = list(dict.fromkeys(process_urls))

	if not process_urls:
		frappe.throw("No valid files found for processing.")

	queue_doctype = get_queue_doctype()
	queue_meta = frappe.get_meta(queue_doctype)

	# Create ONE queue record for the whole batch
	queue_payload = {
		"doctype": queue_doctype,
		"status": "Queued",
	}
	if queue_meta.has_field("batch_id"):
		queue_payload["batch_id"] = batch_id
	if queue_meta.has_field("file_count"):
		queue_payload["file_count"] = len(process_urls)
	if queue_meta.has_field("file_urls"):
		queue_payload["file_urls"] = json.dumps(process_urls)
	if queue_meta.has_field("file_url"):
		# Legacy schema support (single URL field)
		queue_payload["file_url"] = process_urls[0]
	if queue_meta.has_field("target_doctype"):
		queue_payload["target_doctype"] = target_doctype
	if queue_meta.has_field("triggered_by"):
		queue_payload["triggered_by"] = user

	queue_doc = frappe.get_doc(queue_payload)
	queue_doc.insert(ignore_permissions=True)
	frappe.db.commit()

	# Enqueue a single background job for the entire batch
	frappe.enqueue(
		"possibleworks.ap_invoice_processing.bulk_processor.process_batch",
		queue_entry_name=queue_doc.name,
		file_urls=json.dumps(process_urls),  # Pass URLs directly so processing does not depend on DocType schema
		target_doctype=target_doctype,
		queue="long",
		timeout=1800  # 30 min timeout for multi-file batches
	)

	return {"batch_id": batch_id, "count": len(process_urls)}


def process_batch(queue_entry_name, file_urls=None, target_doctype="Purchase Invoice"):
	"""
	Background job that processes ALL files in a single queue record.
	Each file is processed independently — one failure doesn't kill the batch.
	"""
	batch_start = time.time()

	try:
		queue_doctype = get_queue_doctype()
		queue_doc = frappe.get_doc(queue_doctype, queue_entry_name)
	except Exception:
		frappe.log_error(f"Queue record {queue_entry_name} not found", "AI Bulk Scan")
		return

	queue_meta = frappe.get_meta(queue_doc.doctype)

	def _has(fieldname):
		return bool(queue_meta.has_field(fieldname))

	def _safe_db_set(fieldname, value):
		if _has(fieldname):
			queue_doc.db_set(fieldname, value)
			return True
		return False

	# Atomic check-and-set: only one worker can transition Queued → Processing.
	# frappe.db.sql returns () for non-SELECT queries (cursor.description is None),
	# so we cannot rely on its return value for rowcount. Instead we issue a
	# parameterised UPDATE then immediately re-read the status to confirm we won.
	frappe.db.sql(
		"UPDATE `tabAI Document Queue` SET `status`='Processing'"
		" WHERE `name`=%s AND `status`='Queued'",
		(queue_entry_name,),
	)
	frappe.db.commit()
	confirmed_status = frappe.db.get_value(queue_doc.doctype, queue_entry_name, "status")
	if confirmed_status != "Processing":
		# Another worker already picked this batch up, or the entry was cancelled.
		return
	queue_doc.status = "Processing"

	# Resolve file URLs robustly:
	# 1) explicit worker payload (new path), 2) file_urls field, 3) legacy file_url field
	resolved_urls = _normalize_file_urls(file_urls)
	if not resolved_urls:
		resolved_urls = _normalize_file_urls(getattr(queue_doc, "file_urls", None))
	if not resolved_urls:
		resolved_urls = _normalize_file_urls(getattr(queue_doc, "file_url", None))

	if not resolved_urls:
		_safe_db_set("status", "Failed")
		_safe_db_set("error_message", "No file URLs found in queue record.")
		frappe.db.commit()
		return

	file_urls = resolved_urls
	resolved_target_doctype = getattr(queue_doc, "target_doctype", None) or target_doctype or "Purchase Invoice"
	if resolved_target_doctype not in ROLLOUT_DOCTYPES:
		_safe_db_set("status", "Failed")
		_safe_db_set(
			"error_message",
			f"Unsupported target doctype '{resolved_target_doctype}'. Allowed: {', '.join(ROLLOUT_DOCTYPES)}",
		)
		frappe.db.commit()
		return

	processing_log = []
	created_documents = []
	success_count = 0
	fail_count = 0

	for idx, file_url in enumerate(file_urls):
		file_name = file_url.split("/")[-1]
		file_log = {
			"file_name": file_name,
			"file_url": file_url,
			"index": idx + 1,
			"status": "Processing",
			"error": None,
			"purchase_invoice": None,  # Backward compatibility for dashboard
			"created_document": None,
			"created_doctype": resolved_target_doctype,
			"warnings": [],
			"messages": [],
			"processing_time": 0
		}

		file_start = time.time()

		try:
			# 1. Find the File document
			file_doc = frappe.get_all("File", filters={"file_url": file_url}, limit=1)
			if not file_doc:
				file_doc = frappe.get_all("File", filters={"file_name": file_name}, limit=1)
			if not file_doc:
				raise Exception(f"File document not found for: {file_url}")

			file_doc_name = file_doc[0].name

			# 2. Extract data via AI
			extraction_result = extract_data_from_file(file_doc_name, target_doctype=resolved_target_doctype)
			parsed_data = extraction_result.get("parsed", {})

			# 3. Smart match
			match_result = perform_smart_match(parsed_data, target_doctype=resolved_target_doctype)

			file_log["warnings"] = list(match_result.get("warnings", []))
			file_log["messages"] = match_result.get("messages", [])

			# Annotate unmatched items clearly so the user knows what to fix.
			unmatched_items = [
				item.get("description_extracted") or item.get("description") or "Unknown Item"
				for item in (parsed_data.get("items") or [])
				if not item.get("item_code_matched")
			]
			if unmatched_items:
				file_log["warnings"].append(
					f"⚠️ {len(unmatched_items)} item(s) not found in ERPNext — "
					f"please assign Item Codes manually before submitting: "
					+ ", ".join(f'"{d}"' for d in unmatched_items[:5])
					+ ("…" if len(unmatched_items) > 5 else "")
				)

			if not match_result.get("matches", {}).get("supplier"):
				file_log["warnings"].append(
					"⚠️ Supplier not found in ERPNext — please set the Supplier field before submitting."
				)

			# 4. Create draft target document
			created_name = _create_draft_document_for_target(
				target_doctype=resolved_target_doctype,
				parsed_data=parsed_data,
				match_result=match_result,
				file_url=file_url,
			)
			file_log["created_document"] = created_name
			if resolved_target_doctype == "Purchase Invoice":
				file_log["purchase_invoice"] = created_name
			created_documents.append(created_name)

			# Commit the created draft immediately so a subsequent file's rollback
			# cannot destroy it (Critical: per-file transaction isolation).
			frappe.db.commit()

			if match_result.get("is_duplicate"):
				file_log["status"] = "Flagged"
				file_log["warnings"].append("Duplicate document detected.")
			else:
				file_log["status"] = "AI Draft Created"

			success_count += 1

		except Exception as e:
			file_log["status"] = "Failed"
			file_log["error"] = str(e)[:500]
			file_log["error_traceback"] = traceback.format_exc()[:4000]
			fail_count += 1
			frappe.log_error(
				title=f"AI Bulk Scan — {file_name}",
				message=traceback.format_exc()
			)
			# Rollback this file's failed transaction but continue with the next
			frappe.db.rollback()

		finally:
			file_log["processing_time"] = round(time.time() - file_start, 2)
			processing_log.append(file_log)

			# Save progress after each file so the user can see live updates
			try:
				debug_log_json = json.dumps(processing_log, indent=2)
				if not _safe_db_set("processing_log", debug_log_json):
					# Backward-compatible fallback for old schema
					_safe_db_set("extraction_result", debug_log_json)

					if not _safe_db_set("created_invoices", json.dumps(created_documents)):
						if created_documents:
							_safe_db_set("purchase_invoice", created_documents[-1])

				_safe_db_set("total_invoices_created", success_count)
				_safe_db_set("total_failed", fail_count)
				frappe.db.commit()
			except Exception:
				frappe.log_error(
					title="AI Bulk Scan — Progress Save Error",
					message=traceback.format_exc()
				)

	# Final status
	batch_end = time.time()
	try:
		_safe_db_set("processing_time_seconds", round(batch_end - batch_start, 2))

		if fail_count == 0:
			_safe_db_set("status", "Done")
		elif success_count == 0:
			_safe_db_set("status", "Failed")
			_safe_db_set("error_message", f"All {fail_count} file(s) failed. Check log.")
		else:
			_safe_db_set("status", "Partially Done")
			_safe_db_set("error_message", f"{success_count} succeeded, {fail_count} failed.")

		# Persist detailed debug log in whichever field exists.
		debug_log_json = json.dumps(processing_log, indent=2)
		if not _safe_db_set("processing_log", debug_log_json):
			_safe_db_set("extraction_result", debug_log_json)

		frappe.db.commit()
	except Exception:
		frappe.log_error(title="AI Bulk Scan — Final Save Error", message=traceback.format_exc())

	# Notify the user
	_notify_user(queue_doc.triggered_by, success_count, fail_count, queue_doc.name, resolved_target_doctype)


def _notify_user(user, success_count, fail_count, queue_name, target_doctype):
	"""Send a realtime notification to the user when batch is complete."""
	total = success_count + fail_count
	message = (
		f"AI Bulk Scan complete for {target_doctype}: {success_count}/{total} documents created successfully."
	)
	if fail_count > 0:
		message += f" {fail_count} failed — check queue {queue_name} for details."

	frappe.publish_realtime(
			event="msgprint",
			message={
				"title": "AI Document Processing Complete",
				"message": message,
				"indicator": "green" if fail_count == 0 else "orange"
			},
		user=user
	)


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


def _normalize_item_math(item_row):
	"""Normalize qty/rate/amount.
	Rate is the source of truth (it is printed on the document).
	Amount is always recomputed as qty * rate.
	Only derive rate from amount when rate is genuinely missing/zero.
	"""
	qty = _to_float(item_row.get("quantity"), 1.0)
	if abs(qty) < 0.000001:
		qty = 1.0

	rate = _to_float(item_row.get("rate"), 0.0)
	amount = _to_float(item_row.get("amount"), 0.0)

	# If rate is missing, derive it from amount.
	if abs(rate) < 0.000001 and abs(amount) >= 0.000001:
		rate = amount / qty

	# Rate is source of truth: recompute amount so ERPNext math is consistent.
	if abs(rate) >= 0.000001:
		amount = qty * rate

	return qty, rate, amount


def _first_non_group(doctype, filters, fieldname="name"):
	f = dict(filters or {})
	f["is_group"] = 0
	return frappe.db.get_value(doctype, f, fieldname)


def _default_company():
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def _default_warehouse(company):
	if not company:
		return None
	try:
		company_doc = frappe.get_cached_doc("Company", company)
		default_wh = getattr(company_doc, "default_warehouse", None)
		if default_wh:
			return default_wh
	except Exception:
		pass
	return _first_non_group("Warehouse", {"company": company, "disabled": 0})


def _get_account_currency(account):
	if not account:
		return None
	return frappe.db.get_value("Account", account, "account_currency")


def _append_ai_note(doc, base_note=None):
	note = base_note or "Draft created automatically via AI Bulk Upload."
	if doc.meta.has_field("remarks"):
		doc.remarks = f"{(doc.remarks or '').strip()}\n\n{note}".strip()
	elif doc.meta.has_field("note"):
		doc.note = f"{(doc.note or '').strip()}\n\n{note}".strip()


def _attach_file_to_doc(target_doctype, target_name, file_url):
	src_file_name = file_url.split("/")[-1]
	try:
		frappe.get_doc({
			"doctype": "File",
			"file_name": src_file_name,
			"file_url": file_url,
			"attached_to_doctype": target_doctype,
			"attached_to_name": target_name,
			"is_private": 1
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"AI Bulk Scan — File Attach Failed ({target_name})",
			message=traceback.format_exc()
		)


def _iter_non_zero_taxes(parsed_data):
	for tax in (parsed_data.get("taxes", []) or []):
		if not isinstance(tax, dict):
			continue
		tax_amount = _to_float(tax.get("tax_amount"), 0.0)
		if abs(tax_amount) < 0.000001:
			continue
		yield tax, tax_amount


def _has_meaningful_tax_rows(parsed_data):
	return any(True for _tax, _amount in _iter_non_zero_taxes(parsed_data))


def _find_expense_account_by_description(company, description):
	if not company or not description:
		return None

	results = execute_smart_search(
		"Account",
		["account_name", "name"],
		description,
		filters={"company": company, "is_group": 0, "root_type": "Expense"},
		return_fields=["name", "account_name"],
		limit=2,
	)
	if not results:
		return None

	top = results[0]
	score = _to_float(top.get("similarity_score"), 0.0)
	if score >= 0.74:
		return top.get("name")
	return None


def _append_trade_items(doc, parsed_data):
	items = parsed_data.get("items", []) or []
	company = getattr(doc, "company", None)
	supplier = getattr(doc, "supplier", None)
	default_wh = _default_warehouse(company)
	item_child_meta = frappe.get_meta(doc.meta.get_field("items").options)
	default_expense_account = None
	if item_child_meta.has_field("expense_account"):
		default_expense_account = _first_non_group(
			"Account",
			{"company": company, "root_type": "Expense", "disabled": 0},
		)

	for item in items:
		if not isinstance(item, dict):
			continue
		desc = str(item.get("description_extracted") or item.get("description") or "Item").strip()
		history_defaults = (
			resolve_purchase_history_defaults(
				company,
				supplier,
				item_code=item.get("item_code_matched"),
				description=desc,
				hsn_sac_code=item.get("hsn_sac_code"),
			)
			if supplier and item_child_meta.has_field("expense_account")
			else {}
		)
		item_code = item.get("item_code_matched") or history_defaults.get("item_code")
		item_name_val = desc
		item_master_uom = None
		if item_code:
			_master = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True) or {}
			item_name_val = _master.get("item_name") or desc
			item_master_uom = _master.get("stock_uom")

		qty, rate, amount = _normalize_item_math(item)

		row = {
			"item_name": item_name_val[:140] if item_name_val else "Item",
			"description": desc,
			"qty": qty,
			"rate": rate,
			"amount": amount,
			"uom": item_master_uom or item.get("uom") or "Nos",
		}
		if item_code and item_child_meta.has_field("item_code"):
			row["item_code"] = item_code
		if item_child_meta.has_field("price_list_rate"):
			row["price_list_rate"] = rate
		if item_child_meta.has_field("warehouse") and default_wh:
			row["warehouse"] = default_wh
		if item_child_meta.has_field("expense_account"):
			exp_acc = frappe.db.get_value(
				"Item Default", {"parent": item_code, "company": company}, "expense_account"
			) if item_code else None
			if not exp_acc:
				exp_acc = (
					history_defaults.get("expense_account")
					or _find_expense_account_by_description(company, desc)
					or default_expense_account
				)
			if exp_acc:
				row["expense_account"] = exp_acc
		if item_child_meta.has_field("cost_center"):
			cc = frappe.db.get_value(
				"Item Default", {"parent": item_code, "company": company}, "buying_cost_center"
			) if item_code else None
			if not cc:
				cc = history_defaults.get("cost_center")
			if cc:
				row["cost_center"] = cc

		doc.append("items", row)


def _append_trade_taxes(doc, parsed_data):
	if not doc.meta.has_field("taxes"):
		return
	tax_child_meta = frappe.get_meta(doc.meta.get_field("taxes").options)
	for tax, tax_amount in _iter_non_zero_taxes(parsed_data):
		account_head = tax.get("account_head_matched")
		if not account_head:
			continue
		row = {
			"charge_type": tax.get("charge_type") or "On Net Total",
			"account_head": account_head,
			"description": str(tax.get("tax_type_extracted") or "Tax")[:140],
			"tax_amount": tax_amount,
		}
		rate = _to_float(tax.get("rate"), 0.0)
		if rate > 0 and tax_child_meta.has_field("rate"):
			row["rate"] = rate
		doc.append("taxes", row)


def _create_trade_document(
	target_doctype,
	parsed_data,
	match_result,
	file_url,
	party_field,
	party_match_key,
	date_field_candidates,
	number_field_candidates,
):
	doc = frappe.new_doc(target_doctype)
	matches = match_result.get("matches", {})

	doc.company = matches.get("company") or parsed_data.get("company_matched") or _default_company()
	party = matches.get(party_match_key)
	if party and doc.meta.has_field(party_field):
		doc.set(party_field, party)

	for fieldname in date_field_candidates:
		if not doc.meta.has_field(fieldname):
			continue
		date_value = parsed_data.get("document_date") or parsed_data.get("invoice_date") or parsed_data.get("posting_date")
		if date_value:
			doc.set(fieldname, date_value)
			break

	if doc.meta.has_field("due_date") and parsed_data.get("due_date"):
		doc.due_date = parsed_data.get("due_date")
	if doc.meta.has_field("currency") and parsed_data.get("currency"):
		doc.currency = parsed_data.get("currency")
	if (
		doc.meta.has_field("taxes_and_charges")
		and matches.get("taxes_and_charges")
		and not _has_meaningful_tax_rows(parsed_data)
		and _template_matches_document_tax(target_doctype, matches.get("taxes_and_charges"), parsed_data)
	):
		doc.taxes_and_charges = matches.get("taxes_and_charges")
	if doc.meta.has_field("payment_terms_template") and matches.get("payment_terms_template"):
		doc.payment_terms_template = matches.get("payment_terms_template")
	if doc.meta.has_field("ignore_pricing_rule"):
		doc.ignore_pricing_rule = 1

	for fieldname in number_field_candidates:
		if not doc.meta.has_field(fieldname):
			continue
		doc_number = parsed_data.get("document_number") or parsed_data.get("invoice_number")
		if doc_number:
			doc.set(fieldname, str(doc_number))
			break

	if target_doctype in CUSTOMER_SIDE_DOCTYPES:
		if target_doctype in {"Sales Order", "Quotation"} and doc.meta.has_field("order_type"):
			doc.order_type = "Sales"
		if target_doctype == "Quotation" and doc.meta.has_field("quotation_to"):
			doc.quotation_to = "Customer" if getattr(doc, "customer", None) else "Lead"
		if doc.meta.has_field("selling_price_list") and not doc.get("selling_price_list"):
			default_selling_price_list = frappe.db.get_single_value("Selling Settings", "selling_price_list")
			if not default_selling_price_list:
				default_selling_price_list = frappe.db.get_value("Price List", {"enabled": 1, "selling": 1}, "name")
			if default_selling_price_list:
				doc.selling_price_list = default_selling_price_list
				price_list_currency = frappe.db.get_value("Price List", default_selling_price_list, "currency")
				if doc.meta.has_field("price_list_currency") and price_list_currency:
					doc.price_list_currency = price_list_currency
				if doc.meta.has_field("plc_conversion_rate") and not doc.get("plc_conversion_rate"):
					doc.plc_conversion_rate = 1

	if target_doctype in SUPPLIER_SIDE_DOCTYPES and doc.meta.has_field("set_warehouse"):
		doc.set_warehouse = _default_warehouse(doc.company)

	if doc.meta.has_field("conversion_rate") and not doc.get("conversion_rate"):
		doc.conversion_rate = 1
	if doc.meta.has_field("posting_time") and not doc.get("posting_time"):
		doc.posting_time = frappe.utils.nowtime()

	_append_trade_items(doc, parsed_data)
	_append_trade_taxes(doc, parsed_data)

	if parsed_data.get("notes") and doc.meta.has_field("remarks"):
		doc.remarks = str(parsed_data.get("notes")).strip()

	_append_ai_note(doc, f"Draft created automatically via AI Bulk Upload ({target_doctype}).")

	try:
		if hasattr(doc, "set_missing_values"):
			doc.set_missing_values()
	except Exception:
		pass

	# ignore_mandatory=True: drafts are intentionally incomplete when items are
	# unmatched — the user must review before submitting. Matches the PI path.
	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	_attach_file_to_doc(target_doctype, doc.name, file_url)
	return doc.name


def _create_draft_payment_entry(parsed_data, match_result, file_url):
	doc = frappe.new_doc("Payment Entry")
	matches = match_result.get("matches", {})
	company = matches.get("company") or parsed_data.get("company_matched") or _default_company()
	doc.company = company

	payment_type = str(parsed_data.get("payment_type") or "").strip().title()
	if payment_type not in {"Pay", "Receive"}:
		party_type_hint = str(matches.get("party_type") or parsed_data.get("party_type") or "").strip().title()
		payment_type = "Pay" if party_type_hint == "Supplier" else "Receive"
	doc.payment_type = payment_type

	party_type = str(matches.get("party_type") or parsed_data.get("party_type") or "").strip().title()
	if party_type not in {"Supplier", "Customer"}:
		party_type = "Supplier" if payment_type == "Pay" else "Customer"
	party = matches.get("party")

	if party:
		doc.party_type = party_type
		doc.party = party

	from erpnext.accounts.party import get_party_account
	from erpnext.accounts.doctype.journal_entry.journal_entry import get_default_bank_cash_account

	party_account = None
	if party:
		try:
			party_account = get_party_account(party_type, party, company)
		except Exception:
			party_account = None

	bank = get_default_bank_cash_account(company, account_type="Bank", fetch_balance=False) or {}
	cash = get_default_bank_cash_account(company, account_type="Cash", fetch_balance=False) or {}
	company_money_account = bank.get("account") or cash.get("account")

	paid_from = matches.get("paid_from")
	paid_to = matches.get("paid_to")
	if payment_type == "Pay":
		paid_from = paid_from or company_money_account
		paid_to = paid_to or party_account
	else:
		paid_from = paid_from or party_account
		paid_to = paid_to or company_money_account

	if not paid_from or not paid_to:
		raise Exception("Could not resolve paid_from / paid_to accounts for Payment Entry.")

	doc.paid_from = paid_from
	doc.paid_to = paid_to
	doc.paid_from_account_currency = _get_account_currency(paid_from) or "INR"
	doc.paid_to_account_currency = _get_account_currency(paid_to) or "INR"

	paid_amount = _to_float(parsed_data.get("paid_amount"), 0.0)
	received_amount = _to_float(parsed_data.get("received_amount"), 0.0)
	base_amount = max(paid_amount, received_amount, 0.0)
	if base_amount <= 0:
		raise Exception("Payment amount could not be extracted from document.")

	if payment_type == "Pay":
		doc.paid_amount = paid_amount or base_amount
		doc.received_amount = received_amount or base_amount
	else:
		doc.paid_amount = paid_amount or base_amount
		doc.received_amount = received_amount or base_amount

	if doc.meta.has_field("source_exchange_rate") and not doc.get("source_exchange_rate"):
		doc.source_exchange_rate = 1
	if doc.meta.has_field("target_exchange_rate") and not doc.get("target_exchange_rate"):
		doc.target_exchange_rate = 1
	if doc.meta.has_field("base_paid_amount"):
		doc.base_paid_amount = doc.paid_amount
	if doc.meta.has_field("base_received_amount"):
		doc.base_received_amount = doc.received_amount

	doc.posting_date = parsed_data.get("posting_date") or frappe.utils.nowdate()
	if parsed_data.get("reference_no"):
		doc.reference_no = str(parsed_data.get("reference_no"))
	if parsed_data.get("reference_date"):
		doc.reference_date = parsed_data.get("reference_date")
	if matches.get("mode_of_payment"):
		doc.mode_of_payment = matches.get("mode_of_payment")
	if parsed_data.get("notes") and doc.meta.has_field("remarks"):
		doc.remarks = str(parsed_data.get("notes")).strip()

	_append_ai_note(doc, "Draft created automatically via AI Bulk Upload (Payment Entry).")

	try:
		doc.set_missing_values()
	except Exception:
		pass

	doc.insert(ignore_permissions=True, ignore_mandatory=True)
	_attach_file_to_doc("Payment Entry", doc.name, file_url)
	return doc.name


def _create_draft_document_for_target(target_doctype, parsed_data, match_result, file_url):
	if target_doctype == "Purchase Invoice":
		return _create_draft_purchase_invoice(parsed_data, match_result, file_url)
	if target_doctype == "Purchase Receipt":
		return _create_trade_document(
			"Purchase Receipt",
			parsed_data,
			match_result,
			file_url,
			party_field="supplier",
			party_match_key="supplier",
			date_field_candidates=("posting_date",),
			number_field_candidates=("bill_no",),
		)
	if target_doctype == "Supplier Quotation":
		return _create_trade_document(
			"Supplier Quotation",
			parsed_data,
			match_result,
			file_url,
			party_field="supplier",
			party_match_key="supplier",
			date_field_candidates=("transaction_date",),
			number_field_candidates=("supplier_quotation_no",),
		)
	if target_doctype == "Sales Order":
		return _create_trade_document(
			"Sales Order",
			parsed_data,
			match_result,
			file_url,
			party_field="customer",
			party_match_key="customer",
			date_field_candidates=("transaction_date",),
			number_field_candidates=("po_no",),
		)
	if target_doctype == "Quotation":
		return _create_trade_document(
			"Quotation",
			parsed_data,
			match_result,
			file_url,
			party_field="customer",
			party_match_key="customer",
			date_field_candidates=("transaction_date",),
			number_field_candidates=("po_no",),
		)
	if target_doctype == "Delivery Note":
		return _create_trade_document(
			"Delivery Note",
			parsed_data,
			match_result,
			file_url,
			party_field="customer",
			party_match_key="customer",
			date_field_candidates=("posting_date",),
			number_field_candidates=(),
		)
	if target_doctype == "Payment Entry":
		return _create_draft_payment_entry(parsed_data, match_result, file_url)
	raise Exception(f"Auto-creation not implemented for doctype: {target_doctype}")


def _create_draft_purchase_invoice(parsed_data, match_result, file_url):
	"""
	Creates a Draft Purchase Invoice from extracted data.
	Uses match_result["matches"] for matched ERPNext IDs.
	Sets item_code when matched and always attempts to set expense account defaults.
	"""
	pi = frappe.new_doc("Purchase Invoice")
	matches = match_result.get("matches", {})
	pi_meta = frappe.get_meta("Purchase Invoice")

	# Header fields from matches
	if matches.get("company"):
		pi.company = matches["company"]
	if matches.get("supplier"):
		pi.supplier = matches["supplier"]
	requested_tax_template = matches.get("taxes_and_charges")
	if matches.get("payment_terms_template"):
		pi.payment_terms_template = matches["payment_terms_template"]
	if pi_meta.has_field("ignore_pricing_rule"):
		pi.ignore_pricing_rule = 1

	# Header fields from parsed data
	if parsed_data.get("invoice_number"):
		pi.bill_no = str(parsed_data["invoice_number"])
	if parsed_data.get("invoice_date"):
		pi.bill_date = parsed_data["invoice_date"]
	if parsed_data.get("due_date"):
		pi.due_date = parsed_data["due_date"]
	if parsed_data.get("currency"):
		pi.currency = parsed_data["currency"]
	if parsed_data.get("notes"):
		pi.remarks = str(parsed_data["notes"]).strip()

	ai_bulk_note = "Draft created automatically via AI Bulk Upload."
	pi.remarks = f"{pi.remarks}\n\n{ai_bulk_note}" if pi.remarks else ai_bulk_note

	# Ensure company is set so account/cost center defaults can be resolved.
	if not pi.company:
		pi.company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
			"Global Defaults", "default_company"
		)

	def _first_non_group(doctype, filters, fieldname="name"):
		"""Return first non-group record for fallback defaults."""
		f = dict(filters or {})
		f["is_group"] = 0
		return frappe.db.get_value(doctype, f, fieldname)

	def _get_company_default_expense_account(company):
		if not company:
			return None
		try:
			company_meta = frappe.get_meta("Company")
			if company_meta.has_field("default_expense_account"):
				acc = frappe.db.get_value("Company", company, "default_expense_account")
				if acc:
					return acc
		except Exception:
			pass

		return _first_non_group(
			"Account",
			{"company": company, "root_type": "Expense", "disabled": 0},
		)

	def _get_company_default_cost_center(company):
		if not company:
			return None
		try:
			doc = frappe.get_cached_doc("Company", company)
			for attr in ("cost_center", "default_cost_center"):
				val = getattr(doc, attr, None)
				if val:
					return val
		except Exception:
			pass

		return _first_non_group("Cost Center", {"company": company})

	def _get_item_defaults(item_code, company):
		"""Fetch item-level expense account / cost center defaults for company."""
		if not item_code or not company:
			return (None, None)

		expense_account = None
		cost_center = None

		try:
			row = frappe.db.get_value(
				"Item Default",
				{"parent": item_code, "company": company},
				["expense_account", "buying_cost_center"],
				as_dict=True,
			)
			if row:
				expense_account = row.get("expense_account")
				cost_center = row.get("buying_cost_center")
		except Exception:
			pass

		# Some setups store expense account directly on Item
		if not expense_account:
			try:
				item_meta = frappe.get_meta("Item")
				if item_meta.has_field("expense_account"):
					expense_account = frappe.db.get_value("Item", item_code, "expense_account")
			except Exception:
				frappe.log_error(
					title="AI Bulk Scan — Progress Save Error",
					message=traceback.format_exc()
				)

		return (expense_account, cost_center)

	default_expense_account = _get_company_default_expense_account(pi.company)
	default_cost_center = _get_company_default_cost_center(pi.company)
	pi_item_meta = frappe.get_meta("Purchase Invoice Item")
	pi_tax_meta = frappe.get_meta("Purchase Taxes and Charges")

	def _field_max_len(meta, fieldname, default=None):
		df = meta.get_field(fieldname) if meta else None
		if not df:
			return default
		length = getattr(df, "length", None)
		if not length:
			return default
		try:
			return int(length)
		except Exception:
			return default

	def _truncate(value, max_len):
		if value is None:
			return None
		text = str(value).strip()
		if not text:
			return None
		if max_len and len(text) > max_len:
			return text[: max_len - 1] + "…"
		return text

	# Decide if we should auto-apply a tax template.
	# Explicit normalized tax rows always win over template defaults.
	taxes = parsed_data.get("taxes", []) or []
	has_non_zero_tax_amount = _has_meaningful_tax_rows(parsed_data)
	if (
		requested_tax_template
		and not has_non_zero_tax_amount
		and _template_matches_document_tax("Purchase Invoice", requested_tax_template, parsed_data)
	):
		pi.taxes_and_charges = requested_tax_template

	# Items
	items = parsed_data.get("items", [])
	dominant_item_expense_account = None
	dominant_item_cost_center = None
	matched_item_codes = [i.get("item_code_matched") for i in items if i.get("item_code_matched")]
	if matched_item_codes:
		dominant_item_code = max(set(matched_item_codes), key=matched_item_codes.count)
		dominant_item_expense_account, dominant_item_cost_center = _get_item_defaults(dominant_item_code, pi.company)

	for item in items:
		desc = (item.get("description_extracted") or item.get("description") or "Item").strip()
		history_defaults = resolve_purchase_history_defaults(
			pi.company,
			matches.get("supplier"),
			item_code=item.get("item_code_matched"),
			description=desc,
			hsn_sac_code=item.get("hsn_sac_code"),
		)
		item_code = item.get("item_code_matched") or history_defaults.get("item_code")
		item_expense_account, item_cost_center = _get_item_defaults(item_code, pi.company)
		item_name_val = desc
		item_master_uom = None
		if item_code:
			_master = frappe.db.get_value("Item", item_code, ["item_name", "stock_uom"], as_dict=True) or {}
			item_name_val = _master.get("item_name") or desc
			item_master_uom = _master.get("stock_uom")
		qty, rate, amount = _normalize_item_math(item)

		row_payload = {
			"item_name": _truncate(item_name_val, _field_max_len(pi_item_meta, "item_name", 140)) or "Item",
			"description": _truncate(desc, _field_max_len(pi_item_meta, "description", None)) or desc[:500],
			"qty": qty,
			"rate": rate,
			"amount": amount,
			"uom": item_master_uom or item.get("uom", "Nos"),
		}
		if pi_item_meta.has_field("price_list_rate"):
			row_payload["price_list_rate"] = rate

		# Use matched Item Code for better defaults/accuracy.
		if item_code:
			row_payload["item_code"] = item_code

		# Critical for ERPNext validation in many setups.
		expense_account = (
			item_expense_account
			or history_defaults.get("expense_account")
			or _find_expense_account_by_description(pi.company, desc)
			or dominant_item_expense_account
			or default_expense_account
		)
		if expense_account:
			row_payload["expense_account"] = expense_account

		cost_center = item_cost_center or history_defaults.get("cost_center") or dominant_item_cost_center or default_cost_center
		if cost_center:
			row_payload["cost_center"] = cost_center

		pi.append("items", row_payload)

	# Taxes — add only when matched and tax amount is non-zero.
	for tax in taxes:
		account_head = tax.get("account_head_matched")
		if not account_head:
			continue

		tax_amount = _to_float(tax.get("tax_amount"), 0.0)
		if abs(tax_amount) < 0.000001:
			# Ignore placeholder/no-amount rows (e.g. "-", "0", blank)
			continue

		row = {
			"charge_type": tax.get("charge_type", "On Net Total"),
			"account_head": account_head,
			"description": _truncate(
				tax.get("tax_type_extracted", "Tax"),
				_field_max_len(pi_tax_meta, "description", 140),
			) or "Tax",
			"tax_amount": tax_amount,
		}

		rate = _to_float(tax.get("rate"), 0.0)
		if rate > 0:
			row["rate"] = rate

		pi.append("taxes", row)

	# Insert as Draft.
	# ignore_mandatory=True: drafts are intentionally incomplete when items are unmatched —
	# the user must review them before submitting. Avoids confusing "expense_account mandatory"
	# errors when an item wasn't found in ERPNext.
	pi.insert(ignore_permissions=True, ignore_mandatory=True)

	# Attach the source file to the PI
	src_file_name = file_url.split("/")[-1]
	try:
		frappe.get_doc({
			"doctype": "File",
			"file_name": src_file_name,
			"file_url": file_url,
			"attached_to_doctype": "Purchase Invoice",
			"attached_to_name": pi.name,
			"is_private": 1
		}).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"AI Bulk Scan — PI File Attach Failed ({pi.name})",
			message=traceback.format_exc()
		)

	return pi.name


_ZIP_MAX_ENTRIES = 500
_ZIP_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per extracted file


def _extract_zip_file(zip_file_url):
	"""Extracts a ZIP file and returns list of new file urls."""
	file_doc = frappe.get_all("File", filters={"file_url": zip_file_url}, limit=1)
	if not file_doc:
		raise Exception("ZIP file not found.")

	file_name, file_content = frappe.utils.file_manager.get_file(file_doc[0].name)

	extracted_urls = []

	with zipfile.ZipFile(BytesIO(file_content)) as z:
		all_entries = z.namelist()
		if len(all_entries) > _ZIP_MAX_ENTRIES:
			raise Exception(
				f"ZIP contains {len(all_entries)} entries which exceeds the limit of {_ZIP_MAX_ENTRIES}."
			)

		for filename in all_entries:
			# Ignore macOS hidden files and directories
			if filename.startswith('__MACOSX/') or filename.startswith('.') or z.getinfo(filename).is_dir():
				continue

			ext = filename.split(".")[-1].lower() if "." in filename else ""
			if ext in ["pdf", "jpg", "jpeg", "png", "webp"]:
				file_info = z.getinfo(filename)
				if file_info.file_size > _ZIP_MAX_FILE_BYTES:
					frappe.log_error(
						title="AI Bulk Scan — ZIP Entry Too Large",
						message=f"Skipping '{filename}': {file_info.file_size} bytes exceeds limit of {_ZIP_MAX_FILE_BYTES} bytes."
					)
					continue

				content = z.read(filename)
				clean_name = filename.split("/")[-1]

				# Save as Frappe File (Private)
				saved_file = save_file(clean_name, content, get_queue_doctype(), None, is_private=1)
				extracted_urls.append(saved_file.file_url)

	return extracted_urls


@frappe.whitelist()
def cancel_queue_entry(queue_name):
	"""
	Cancel a queue entry that is in Queued or Processing state.
	Sets status to Cancelled so a running worker skips or the job is never picked up.
	"""
	frappe.only_for("System Manager")
	queue_doctype = get_queue_doctype()
	doc = frappe.get_doc(queue_doctype, queue_name)
	if doc.status in ("Done", "Cancelled", "Failed"):
		frappe.throw(f"Cannot cancel a queue entry with status '{doc.status}'.")
	doc.db_set("status", "Cancelled")
	frappe.db.commit()
	return {"status": "Cancelled", "name": queue_name}


@frappe.whitelist()
def delete_queue_entry(queue_name):
	"""
	Delete a queue entry. Only allowed when status is not Processing.
	"""
	frappe.only_for("System Manager")
	queue_doctype = get_queue_doctype()
	doc = frappe.get_doc(queue_doctype, queue_name)
	if doc.status == "Processing":
		frappe.throw("Cannot delete a queue entry that is currently Processing. Cancel it first.")
	frappe.delete_doc(queue_doctype, queue_name, ignore_permissions=True, force=True)
	frappe.db.commit()
	return {"deleted": True, "name": queue_name}
