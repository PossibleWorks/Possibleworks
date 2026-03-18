/* Copyright (c) 2026, Possibleworks and contributors */

function _pw_get_ai_access(targetDoctype) {
	window.__pw_ai_access_cache = window.__pw_ai_access_cache || {};
	if (window.__pw_ai_access_cache[targetDoctype]) {
		return Promise.resolve(window.__pw_ai_access_cache[targetDoctype]);
	}

	return new Promise((resolve) => {
		frappe.call({
			method: "possibleworks.ap_invoice_processing.api.get_ai_access",
			args: { target_doctype: targetDoctype },
			callback: (r) => {
				const msg = (r && r.message) || {};
				window.__pw_ai_access_cache[targetDoctype] = msg;
				resolve(msg);
			},
			error: () => resolve({ allowed: false, enabled: false, doctype_enabled: false })
		});
	});
}

const AI_FORM_DOCTYPES = [
	"Purchase Receipt",
	"Supplier Quotation",
	"Payment Entry",
	"Sales Order",
	"Quotation",
	"Delivery Note"
];

AI_FORM_DOCTYPES.forEach((doctypeName) => {
	frappe.ui.form.on(doctypeName, {
		refresh(frm) {
			if (frm.doc.docstatus !== 0) return;
			_pw_get_ai_access(frm.doctype).then((access) => {
				if (!access || !access.allowed) return;
				if (frm.page && frm.page.inner_toolbar && frm.page.inner_toolbar.find(".pw-ai-smart-scan").length) {
					return;
				}
				frm.add_custom_button(__("🤖 AI Smart Scan"), () => _open_ai_doc_dialog(frm))
					.addClass("btn-primary ap-extract-btn pw-ai-smart-scan");
			});
		}
	});
});

function _open_ai_doc_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Extract Document Data via AI"),
		fields: [
			{
				fieldname: "source_file",
				fieldtype: "Attach",
				label: __("Upload Document (PDF/Image)"),
				reqd: 1,
				options: {
					restrictions: { bypass_document_check: true }
				}
			}
		],
		primary_action_label: __("Extract Data"),
		primary_action: (values) => {
			d.disable_primary_action();
			d.set_message(__("🔍 Scanning and extracting data with AI..."));
			frappe.call({
				method: "possibleworks.ap_invoice_processing.api.process_single_invoice",
				args: {
					file_url: values.source_file,
					target_doctype: frm.doctype
				},
				callback: function(r) {
					d.hide();
					if (!(r.message && r.message.status === "success")) {
						const msg = r.message ? r.message.message : __("Unknown extraction error");
						frappe.msgprint({
							title: __("Extraction Failed"),
							indicator: "red",
							message: msg
						});
						return;
					}
					_apply_extraction_to_form(frm, r.message, values.source_file);
				},
				error: function() {
					d.hide();
					frappe.msgprint({
						title: __("API Error"),
						indicator: "red",
						message: __("Failed to reach the extraction service.")
					});
				}
			});
		}
	});
	d.show();
}

function _to_number(value, fallback = 0) {
	if (value === null || value === undefined) return fallback;
	if (typeof value === "number") return Number.isFinite(value) ? value : fallback;
	const text = String(value).trim();
	if (!text || ["-", "--", "NA", "N/A", "None", "null"].includes(text)) return fallback;
	const num = Number(text.replace(/,/g, "").replace(/%/g, ""));
	return Number.isFinite(num) ? num : fallback;
}

function _normalize_item_math(item) {
	let qty = _to_number(item.quantity, 1);
	if (Math.abs(qty) < 0.000001) qty = 1;

	let rate = _to_number(item.rate, 0);
	let amount = _to_number(item.amount, 0);

	if (Math.abs(amount) < 0.000001 && Math.abs(rate) >= 0.000001) {
		amount = qty * rate;
	}
	if (Math.abs(amount) >= 0.000001 && Math.abs(qty) >= 0.000001) {
		if (Math.abs(rate) < 0.000001) {
			rate = amount / qty;
		} else {
			const computed = qty * rate;
			const mismatch = Math.abs(computed - amount);
			const tolerance = Math.max(0.5, Math.abs(amount) * 0.01);
			if (mismatch > tolerance) {
				rate = amount / qty;
			}
		}
	}
	amount = Math.abs(rate) >= 0.000001 ? qty * rate : amount;
	return { qty, rate, amount };
}

function _set_if_exists(frm, fieldname, value) {
	if (!frm.fields_dict[fieldname]) return;
	if (value === undefined || value === null || value === "") return;
	frm.set_value(fieldname, value);
}

function _has_tax_evidence(parsed) {
	const taxes = Array.isArray(parsed.taxes) ? parsed.taxes : [];
	const has_non_zero_tax = taxes.some(tax => Math.abs(_to_number(tax.tax_amount, 0)) >= 0.000001);
	const subtotal = _to_number(parsed.subtotal, 0);
	const grand_total = _to_number(parsed.grand_total, 0);
	const has_total_delta = subtotal > 0 && (grand_total - subtotal) > 0.5;
	return has_non_zero_tax || has_total_delta;
}

function _apply_trade_items(frm, parsed) {
	if (!frm.fields_dict.items) return;
	frm.clear_table("items");
	(parsed.items || []).forEach((item) => {
		const row = frm.add_child("items");
		if (item.item_code_matched && row.hasOwnProperty("item_code")) {
			row.item_code = item.item_code_matched;
		}
		const desc = item.description_extracted || item.description || "";
		if (row.hasOwnProperty("item_name")) row.item_name = desc;
		if (row.hasOwnProperty("description")) row.description = desc;
		const normalized = _normalize_item_math(item);
		if (row.hasOwnProperty("qty")) row.qty = normalized.qty;
		if (row.hasOwnProperty("rate")) row.rate = normalized.rate;
		if (row.hasOwnProperty("amount")) row.amount = normalized.amount;
		if (item.uom && row.hasOwnProperty("uom")) row.uom = item.uom;
	});
}

function _apply_trade_taxes(frm, parsed, match) {
	const should_set_tax_template = !!(match.matches && match.matches.taxes_and_charges) && _has_tax_evidence(parsed);
	if (should_set_tax_template && frm.fields_dict.taxes_and_charges) {
		frm.set_value("taxes_and_charges", match.matches.taxes_and_charges);
		return;
	}
	if (!frm.fields_dict.taxes) return;

	frm.clear_table("taxes");
	(parsed.taxes || []).forEach((tax) => {
		if (!tax.account_head_matched) return;
		if (Math.abs(_to_number(tax.tax_amount, 0)) < 0.000001) return;
		const row = frm.add_child("taxes");
		if (row.hasOwnProperty("charge_type")) row.charge_type = tax.charge_type || "On Net Total";
		if (row.hasOwnProperty("account_head")) row.account_head = tax.account_head_matched;
		if (row.hasOwnProperty("description")) row.description = tax.tax_type_extracted || "Tax";
		if (row.hasOwnProperty("rate") && tax.rate) row.rate = tax.rate;
		if (row.hasOwnProperty("tax_amount")) row.tax_amount = tax.tax_amount;
	});
}

function _apply_extraction_to_form(frm, result, file_url) {
	const parsed = result.parsed_data || {};
	const match = result.match_result || { matches: {}, warnings: [], messages: [] };
	const matches = match.matches || {};

	frappe.dom.freeze(__("Applying AI extraction..."));

	_set_if_exists(frm, "company", matches.company || parsed.company_matched);
	_set_if_exists(frm, "supplier", matches.supplier || parsed.supplier_id_matched);
	_set_if_exists(frm, "customer", matches.customer || parsed.customer_id_matched);
	_set_if_exists(frm, "party_type", matches.party_type || parsed.party_type);
	_set_if_exists(frm, "party", matches.party || parsed.party_id_matched);

	_set_if_exists(frm, "posting_date", parsed.posting_date || parsed.document_date || parsed.invoice_date);
	_set_if_exists(frm, "transaction_date", parsed.document_date || parsed.invoice_date || parsed.posting_date);
	_set_if_exists(frm, "bill_date", parsed.invoice_date || parsed.document_date);
	_set_if_exists(frm, "due_date", parsed.due_date);
	_set_if_exists(frm, "currency", parsed.currency);

	_set_if_exists(frm, "bill_no", parsed.invoice_number || parsed.document_number);
	_set_if_exists(frm, "supplier_quotation_no", parsed.document_number);
	_set_if_exists(frm, "reference_no", parsed.reference_no);
	_set_if_exists(frm, "reference_date", parsed.reference_date);

	_set_if_exists(frm, "payment_terms_template", matches.payment_terms_template || parsed.payment_terms_template);
	_set_if_exists(frm, "mode_of_payment", matches.mode_of_payment || parsed.mode_of_payment_matched);
	_set_if_exists(frm, "paid_from", matches.paid_from || parsed.paid_from_account_matched);
	_set_if_exists(frm, "paid_to", matches.paid_to || parsed.paid_to_account_matched);

	if (frm.fields_dict.payment_type) {
		const pt = parsed.payment_type || (matches.party_type === "Supplier" ? "Pay" : "Receive");
		frm.set_value("payment_type", pt);
	}

	const paidAmount = _to_number(parsed.paid_amount, 0);
	const recvAmount = _to_number(parsed.received_amount, 0);
	if (frm.fields_dict.paid_amount) {
		frm.set_value("paid_amount", paidAmount || recvAmount || 0);
	}
	if (frm.fields_dict.received_amount) {
		frm.set_value("received_amount", recvAmount || paidAmount || 0);
	}

	_apply_trade_items(frm, parsed);
	_apply_trade_taxes(frm, parsed, match);

	if (frm.fields_dict.remarks && parsed.notes) {
		const current = frm.doc.remarks || "";
		frm.set_value("remarks", `${current}\n\n${parsed.notes}`.trim());
	}

	frm.refresh_fields();
	frappe.dom.unfreeze();

	if (!frm.is_new()) {
		try {
			frm.attachments.attachment_uploaded({
				file_url: file_url,
				name: file_url.split("/").pop()
			});
		} catch (e) {
			// Non-blocking.
		}
	}

	const warnHtml = (match.warnings || []).map(w => `<li>${frappe.utils.escape_html(w)}</li>`).join("");
	const msgHtml = (match.messages || []).map(m => `<li>${frappe.utils.escape_html(m)}</li>`).join("");
	frappe.msgprint({
		title: __("AI Extraction Applied"),
		indicator: "green",
		message: `
			<div>
				${msgHtml ? `<p><b>Matched:</b></p><ul>${msgHtml}</ul>` : ""}
				${warnHtml ? `<p><b>Warnings:</b></p><ul>${warnHtml}</ul>` : ""}
			</div>
		`
	});
}
