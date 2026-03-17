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

frappe.ui.form.on("Purchase Invoice", {
	refresh(frm) {
		// Only show on Draft non-submitted invoices
		if (frm.doc.docstatus !== 0 || frm.doc.supplier) return;

		_pw_get_ai_access("Purchase Invoice").then((access) => {
			if (!access || !access.allowed) return;
			if (frm.page && frm.page.inner_toolbar && frm.page.inner_toolbar.find(".pw-ai-smart-scan").length) {
				return;
			}
			frm.add_custom_button(__("🤖 AI Smart Scan"), () => _open_extraction_dialog(frm))
				.addClass("btn-primary ap-extract-btn pw-ai-smart-scan");
		});
	},
	after_save(frm) {
		// Attach the AI-extracted file once the document has a real name in the database
		if (frm.custom_pending_attachment) {
			frm.attachments.attachment_uploaded({
				file_url: frm.custom_pending_attachment,
				name: frm.custom_pending_attachment.split("/").pop()
			});
			frm.custom_pending_attachment = null;
		}

		// Wire up audit log: capture what the user actually saved vs what AI extracted.
		if (frm.__pw_ai_log_id) {
			const log_id = frm.__pw_ai_log_id;
			const submitted_values = {
				supplier: frm.doc.supplier,
				bill_no: frm.doc.bill_no,
				bill_date: frm.doc.bill_date,
				grand_total: frm.doc.grand_total,
				currency: frm.doc.currency,
				taxes_and_charges: frm.doc.taxes_and_charges,
				items: (frm.doc.items || []).map(r => ({
					item_code: r.item_code,
					item_name: r.item_name,
					qty: r.qty,
					rate: r.rate,
					amount: r.amount
				}))
			};
			frappe.call({
				method: "possibleworks.ap_invoice_processing.api.log_user_submission",
				args: { log_id, final_submitted_values: submitted_values },
				// Fire-and-forget — don't block the save flow on this.
			});
		}
	}
});

frappe.ui.form.on("Purchase Invoice Item", {
	item_code(frm, cdt, cdn) {
		// Update unmatched-item badge when the user manually clears or sets item_code.
		const row = locals[cdt] && locals[cdt][cdn];
		if (!row) return;
		row.__pw_ai_unmatched_item = row.item_code ? 0 : 1;
		_pw_apply_unmatched_item_styles(frm);
	}
});

function _open_extraction_dialog(frm) {
	// Provide a clean dialog instance without binding to the unsaved form doc
	// This prevents the 'Attach' field from trying to link the file to "new-purchase-invoice..."
	const d = new frappe.ui.Dialog({
		title: __("Extract Invoice Data via AI"),
		fields: [
			{
				fieldname: "invoice_file",
				fieldtype: "Attach",
				label: __("Upload Invoice (PDF/Image)"),
				reqd: 1,
				options: {
					restrictions: { bypass_document_check: true }
				}
			}
		],
		primary_action_label: __("Extract Data"),
		primary_action: (values) => {
			d.disable_primary_action();
			d.set_message(__("🔍 Scanning and extracting data with AI... This may take 15-30 seconds."));

			frappe.call({
				method: "possibleworks.ap_invoice_processing.api.process_single_invoice",
				args: {
					file_url: values.invoice_file,
					target_doctype: "Purchase Invoice"
				},
				callback: function(r) {
					d.hide();
					if (r.message && r.message.status === "success") {
						_show_extraction_result(frm, values.invoice_file, r.message);
					} else {
						let msg = r.message ? r.message.message : "Unknown error occurred";
						frappe.msgprint({
							title: __("Extraction Failed"),
							indicator: "red",
							message: frappe.utils.escape_html(msg)
						});
					}
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

function _show_extraction_result(frm, file_url, result) {
	const parsed = result.parsed_data;
	const match = result.match_result;
	const log_id = result.log_id;

	let html = `<div class="ap-extraction-popup" style="font-size: 14px;">`;

	// ── Status Banner ──
	if (!parsed.is_valid_document) {
		html += `<div style="background: linear-gradient(135deg, #ff4444, #cc0000); color: white; padding: 14px 16px; border-radius: 8px; margin-bottom: 12px; display: flex; align-items: center; gap: 10px;">
			<span style="font-size: 20px;">⚠️</span>
			<div>
				<strong>Invalid Document</strong>
				<div style="font-size: 0.9em; opacity: 0.9;">This document does not appear to be a valid Purchase Invoice.</div>
			</div>
		</div>`;
	}

	// ── Warnings ──
	if (match.warnings && match.warnings.length > 0) {
		html += `<div style="background: #fff3cd; border: 1px solid #ffc107; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px;">`;
		match.warnings.forEach(w => {
			html += `<div style="color: #856404; margin-bottom: 4px; display: flex; align-items: flex-start; gap: 8px;">
				<span style="flex-shrink: 0;">⚠️</span>
				<span>${w}</span>
			</div>`;
		});
		html += `</div>`;
	}

	// ── Success Messages ──
	if (match.messages && match.messages.length > 0) {
		html += `<div style="background: #d4edda; border: 1px solid #28a745; padding: 12px 16px; border-radius: 8px; margin-bottom: 12px;">`;
		match.messages.forEach(m => {
			html += `<div style="color: #155724; margin-bottom: 4px; display: flex; align-items: flex-start; gap: 8px;">
				<span style="flex-shrink: 0;">✅</span>
				<span>${m}</span>
			</div>`;
		});
		html += `</div>`;
	}

	// ── Header Fields ──
	html += `<div style="margin-top: 16px; font-weight: 700; font-size: 15px; border-bottom: 2px solid var(--border-color); padding-bottom: 6px; margin-bottom: 12px; color: var(--heading-color);">📋 Extracted Header Fields</div>`;

	const _esc = (v) => frappe.utils.escape_html(String(v ?? ""));

	const make_row = (label, extracted_val, matched_val = undefined) => {
		if (extracted_val === null || extracted_val === undefined || extracted_val === '') {
			extracted_val = `<span style="color: var(--text-muted); font-style: italic;">Not found</span>`;
		} else {
			extracted_val = _esc(extracted_val);
		}

		let match_badge = '';
		if (matched_val !== undefined) {
			if (matched_val) {
				match_badge = `<div style="margin-top: 4px;"><span style="background: #d4edda; color: #155724; padding: 2px 8px; border-radius: 4px; font-size: 0.85em;">✅ ERPNext: <strong>${_esc(matched_val)}</strong></span></div>`;
			} else {
				match_badge = `<div style="margin-top: 4px;"><span style="background: #fff3cd; color: #856404; padding: 2px 8px; border-radius: 4px; font-size: 0.85em;">⚠️ No match in ERPNext</span></div>`;
			}
		}

		return `
		<div style="padding: 8px 0; border-bottom: 1px solid var(--border-color); display: flex;">
			<div style="font-weight: 600; width: 35%; color: var(--text-muted); font-size: 0.95em;">${label}</div>
			<div style="width: 65%;">
				<div style="font-weight: 500;">${extracted_val}</div>
				${match_badge}
			</div>
		</div>`;
	};

	// Use validated match values for the badge — these are what Fill Invoice will actually use.
	const _fill_supplier    = (match.matches && match.matches.supplier) || parsed.supplier_id_matched;
	const _fill_tax_tmpl    = match.matches && match.matches.taxes_and_charges;
	const _fill_pay_terms   = match.matches && match.matches.payment_terms_template;

	html += make_row("Supplier", parsed.supplier_name_extracted, _fill_supplier);
	html += make_row("GSTIN", parsed.supplier_gstin_extracted);
	html += make_row("Invoice Number", parsed.invoice_number);
	html += make_row("Invoice Date", parsed.invoice_date);
	html += make_row("Due Date", parsed.due_date);
	html += make_row("Grand Total", parsed.grand_total);
	html += make_row("Currency", parsed.currency);
	// Show tax template and payment terms exactly as they will be filled.
	if (_fill_tax_tmpl || parsed.taxes_and_charges_template) {
		html += make_row("Tax Template", parsed.taxes_and_charges_template, _fill_tax_tmpl || null);
	}
	if (_fill_pay_terms || parsed.payment_terms_template) {
		html += make_row("Payment Terms", parsed.payment_terms_template, _fill_pay_terms || null);
	}

	// ── Line Items ──
	if (parsed.items && parsed.items.length > 0) {
		html += `<div style="margin-top: 20px; font-weight: 700; font-size: 15px; border-bottom: 2px solid var(--border-color); padding-bottom: 6px; margin-bottom: 12px; color: var(--heading-color);">🛒 Line Items (${parsed.items.length})</div>`;
		html += `<table style="width: 100%; border-collapse: collapse;">
		<thead><tr style="border-bottom: 2px solid var(--border-color); text-align: left; background: var(--bg-light-gray);">
			<th style="padding: 8px 10px;">Description / ERPNext Match</th>
			<th style="padding: 8px 10px; text-align: right;">Qty</th>
			<th style="padding: 8px 10px; text-align: right;">Rate</th>
			<th style="padding: 8px 10px; text-align: right;">Amount</th>
		</tr></thead><tbody>`;

		parsed.items.forEach(item => {
			// Normalize here so preview values are identical to what Fill Invoice will use.
			const normalized = _normalize_item_math(item);

			const desc = item.description_extracted
				? _esc(item.description_extracted)
				: '<i>No description</i>';
			let match_badge = '';
			if (item.item_code_matched) {
				match_badge = `<div style="margin-top: 3px;"><span style="background: #d4edda; color: #155724; padding: 1px 6px; border-radius: 3px; font-size: 0.82em;">✅ ${_esc(item.item_code_matched)}</span></div>`;
			} else {
				match_badge = `<div style="margin-top: 3px;"><span style="background: #e2e3e5; color: #383d41; padding: 1px 6px; border-radius: 3px; font-size: 0.82em;">📝 Will add as raw text</span></div>`;
			}

			html += `<tr style="border-bottom: 1px solid var(--border-color);">
				<td style="padding: 8px 10px; vertical-align: top;">
					<div style="font-weight: 500;">${desc}</div>
					${match_badge}
					${item.hsn_sac_code ? `<div style="font-size: 0.8em; color: var(--text-muted); margin-top: 2px;">HSN/SAC: ${_esc(item.hsn_sac_code)}</div>` : ''}
				</td>
				<td style="padding: 8px 10px; vertical-align: top; text-align: right;">${normalized.qty}</td>
				<td style="padding: 8px 10px; vertical-align: top; text-align: right;">${frappe.format(normalized.rate, {fieldtype: 'Currency'})}</td>
				<td style="padding: 8px 10px; vertical-align: top; text-align: right; font-weight: 600;">${frappe.format(normalized.amount, {fieldtype: 'Currency'})}</td>
			</tr>`;
		});
		html += `</tbody></table>`;
	}

	// ── Taxes ──
	if (parsed.taxes && parsed.taxes.length > 0) {
		html += `<div style="margin-top: 20px; font-weight: 700; font-size: 15px; border-bottom: 2px solid var(--border-color); padding-bottom: 6px; margin-bottom: 12px; color: var(--heading-color);">💰 Taxes</div>`;
		html += `<table style="width: 100%; border-collapse: collapse;">
		<thead><tr style="border-bottom: 2px solid var(--border-color); text-align: left; background: var(--bg-light-gray);">
			<th style="padding: 8px 10px;">Tax Type</th>
			<th style="padding: 8px 10px;">Account Head</th>
			<th style="padding: 8px 10px; text-align: right;">Rate</th>
			<th style="padding: 8px 10px; text-align: right;">Amount</th>
		</tr></thead><tbody>`;

		parsed.taxes.forEach(tax => {
			const label = _esc(tax.tax_type_extracted || 'Tax');
			const acct = tax.account_head_matched
				? `<span style="color: #155724;">✅ ${_esc(tax.account_head_matched)}</span>`
				: `<span style="color: #856404;">⚠️ Not matched</span>`;
			const rate = tax.rate ? `${_esc(String(tax.rate))}%` : '-';

			html += `<tr style="border-bottom: 1px solid var(--border-color);">
				<td style="padding: 8px 10px;">${label}</td>
				<td style="padding: 8px 10px;">${acct}</td>
				<td style="padding: 8px 10px; text-align: right;">${rate}</td>
				<td style="padding: 8px 10px; text-align: right; font-weight: 600;">${frappe.format(tax.tax_amount || 0, {fieldtype: 'Currency'})}</td>
			</tr>`;
		});
		html += `</tbody></table>`;
	}

	// ── Notes ──
	if (parsed.notes) {
		html += `<div style="margin-top: 16px; background: var(--bg-light-gray); padding: 10px 14px; border-radius: 6px; font-size: 0.9em; color: var(--text-muted);">
			<strong>📝 Notes from Document:</strong> ${_esc(parsed.notes)}
		</div>`;
	}

	// ── Duplicate Check ──
	if (match.is_duplicate) {
		const isDraft = match.duplicate_status === "Draft";
		const note = frappe.utils.escape_html(
			match.note || (isDraft
				? `Draft invoice '${match.duplicate_invoice_id}' exists but is not yet submitted.`
				: `Submitted invoice '${match.duplicate_invoice_id}' already exists.`)
		);
		if (isDraft) {
			// Informational note only — no confirmation required for drafts
			html += `
			<div style="background: #fff3cd; border: 1px solid #ffc107; padding: 15px 18px; border-radius: 8px; margin-top: 20px;">
				<h4 style="margin: 0 0 8px 0; display: flex; align-items: center; gap: 8px; color: #856404;">⚠️ Possible Duplicate (Draft)</h4>
				<p style="margin: 0; color: #856404;">${note} You can still proceed.</p>
			</div>`;
		} else {
			// Hard confirmation required for submitted duplicates
			html += `
			<div style="background: linear-gradient(135deg, #ff6b6b, #ee5a24); color: white; padding: 15px 18px; border-radius: 8px; margin-top: 20px;">
				<h4 style="margin: 0 0 8px 0; display: flex; align-items: center; gap: 8px;">🚨 Duplicate Detected</h4>
				<p style="margin: 0 0 10px 0; opacity: 0.95;">${note}</p>
				<label style="display: flex; align-items: center; gap: 8px; cursor: pointer; font-weight: 600;">
					<input type="checkbox" id="ap-confirm-not-duplicate" style="width: 18px; height: 18px;" />
					I confirm this is a new, separate invoice
				</label>
			</div>`;
		}
	}

	html += `</div>`;

	const summary_dialog = new frappe.ui.Dialog({
		title: __("🤖 AI Extraction Review"),
		size: "large",
		fields: [
			{
				fieldname: "html_summary",
				fieldtype: "HTML",
				options: html
			}
		],
		primary_action_label: __("✅ Fill Invoice"),
		primary_action: () => {
			if (match.is_duplicate && match.duplicate_status !== "Draft") {
				const check = $("#ap-confirm-not-duplicate").is(":checked");
				if (!check) {
					frappe.msgprint(__("You must confirm this is not a duplicate before filling the invoice."));
					return;
				}
			}

			summary_dialog.hide();
			_fill_invoice_form(frm, parsed, match, file_url, log_id);
		}
	});

	summary_dialog.show();
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
	// Rate is the source of truth (printed on the document).
	// Amount is always recomputed as qty * rate.
	// Only derive rate from amount when rate is genuinely missing/zero.
	let qty = _to_number(item.quantity, 1);
	if (Math.abs(qty) < 0.000001) qty = 1;

	let rate = _to_number(item.rate, 0);
	let amount = _to_number(item.amount, 0);

	// If rate is missing, derive from amount.
	if (Math.abs(rate) < 0.000001 && Math.abs(amount) >= 0.000001) {
		rate = amount / qty;
	}

	// Rate is source of truth: recompute amount for consistency.
	if (Math.abs(rate) >= 0.000001) {
		amount = qty * rate;
	}

	return { qty, rate, amount };
}

function _has_tax_evidence(parsed) {
	const taxes = Array.isArray(parsed.taxes) ? parsed.taxes : [];
	const has_non_zero_tax = taxes.some(tax => Math.abs(_to_number(tax.tax_amount, 0)) >= 0.000001);
	const subtotal = _to_number(parsed.subtotal, 0);
	const grand_total = _to_number(parsed.grand_total, 0);
	const has_total_delta = subtotal > 0 && (grand_total - subtotal) > 0.01;
	return has_non_zero_tax || has_total_delta;
}


function _pw_apply_unmatched_item_styles(frm) {
	const grid = frm.fields_dict.items && frm.fields_dict.items.grid;
	if (!grid || !Array.isArray(grid.grid_rows)) return;

	grid.grid_rows.forEach((grid_row) => {
		const doc = grid_row && grid_row.doc;
		if (!doc || !grid_row.wrapper) return;

		const is_unmatched = !!doc.__pw_ai_unmatched_item;
		grid_row.wrapper.toggleClass("pw-ai-unmatched-row", is_unmatched);

		// Add / remove the ⚠ "Select Item" badge in the row-index cell.
		const $idx = grid_row.wrapper.find(".row-index");
		$idx.find(".pw-select-item-badge").remove();
		if (is_unmatched) {
			$idx.append(
				'<span class="pw-select-item-badge" title="Item not found in ERPNext — please select an item code">⚠</span>'
			);
		}
	});
}

function _fill_invoice_form(frm, parsed, match, file_url, log_id) {
	// Store log_id on the form so after_save can call log_user_submission.
	frm.__pw_ai_log_id = log_id;
	frappe.dom.freeze(__("Populating invoice data..."));

	// 1. Set supplier (use match result which has validated ID)
	const supplier_id = match.matches.supplier || parsed.supplier_id_matched;
	if (supplier_id) {
		frm.set_value("supplier", supplier_id);
	}

	// 2. Header fields
	if (parsed.invoice_number) frm.set_value("bill_no", parsed.invoice_number);
	if (parsed.invoice_date) frm.set_value("bill_date", parsed.invoice_date);
	if (parsed.due_date) frm.set_value("due_date", parsed.due_date);
	if (parsed.currency) frm.set_value("currency", parsed.currency);
	if (frm.fields_dict.ignore_pricing_rule) {
		frm.set_value("ignore_pricing_rule", 1);
	}

	// Tax template and payment terms are set inside _fill_items_and_taxes (after the
	// supplier-defaults AJAX settles) so our values override whatever ERPNext sets.
	const should_set_tax_template = !!match.matches.taxes_and_charges && _has_tax_evidence(parsed);

	// 3. Add file to attachments (safe guard against unsaved docs)
	if (!frm.is_new()) {
		try {
			frm.attachments.attachment_uploaded({
				file_url: file_url,
				name: file_url.split("/").pop()
			});
		} catch (e) {
			console.warn("Failed to attach file immediately", e);
		}
	} else {
		frm.custom_pending_attachment = file_url;
		frappe.show_alert({
			message: __("Invoice data populated! The file will be attached when you save."),
			indicator: "green"
		}, 6);
	}

	// Wait 500ms for ERPNext supplier-defaults AJAX to settle before filling
	// taxes/payment-terms/items. Setting these inside the callback ensures our
	// values win over whatever the supplier-defaults AJAX writes.
	const _fill_items_and_taxes = () => {
		// 4. Tax template (set here so it overrides supplier-defaults AJAX result)
		if (should_set_tax_template) {
			frm.set_value("taxes_and_charges", match.matches.taxes_and_charges);
		}

		// 5. Payment terms
		if (match.matches.payment_terms_template) {
			frm.set_value("payment_terms_template", match.matches.payment_terms_template);
		}

		// 6. Line Items
		frm.clear_table("items");
		let missing_codes = false;
		const math_enforcement = [];
		if (parsed.items && parsed.items.length > 0) {
			parsed.items.forEach(item => {
				let row = frm.add_child("items");

				let desc = (item.description_extracted || item.description || "").trim();
				if (!desc) desc = "Item";
				const normalized = _normalize_item_math(item);

				// Set all non-item_code fields directly (no AJAX triggered).
				row.item_name   = desc;
				row.description = desc;
				row.qty    = normalized.qty;
				row.rate   = normalized.rate;
				row.amount = normalized.amount;
				if (item.uom && !item.item_code_matched) row.uom = item.uom;

				// Use direct assignment for item_code — NOT frappe.model.set_value.
				// frappe.model.set_value triggers ERPNext's get_item_details AJAX which
				// overwrites rate/qty/amount with item master defaults (e.g. 45000).
				// UOM is fetched separately via a batch call after all rows are added.
				row.__pw_ai_unmatched_item = item.item_code_matched ? 0 : 1;

				if (item.item_code_matched) {
					row.item_code = item.item_code_matched;
				} else {
					missing_codes = true;
				}

				math_enforcement.push({
					doctype: row.doctype,
					name: row.name,
					desc,
					qty: normalized.qty,
					rate: normalized.rate,
					amount: normalized.amount,
					uom: item.uom || "Nos",
					item_code_matched: item.item_code_matched || null
				});
			});
		}

		// 7. Taxes
		let unmatched_tax_rows = 0;
		if (!should_set_tax_template && parsed.taxes && parsed.taxes.length > 0) {
			frm.clear_table("taxes");
			parsed.taxes.forEach(tax => {
				const tax_amt = _to_number(tax.tax_amount, 0);
				if (Math.abs(tax_amt) < 0.000001) return; // skip zero rows
				if (!tax.account_head_matched) {
					unmatched_tax_rows++;
					return; // can't add row without account head — ERPNext will reject
				}
				let row = frm.add_child("taxes");
				row.charge_type = tax.charge_type || "On Net Total";
				row.account_head = tax.account_head_matched;
				row.description = tax.tax_type_extracted || "Tax";
				if (tax.rate) row.rate = tax.rate;
				row.tax_amount = tax_amt;
			});
		}

		frm.refresh_fields();
		_pw_apply_unmatched_item_styles(frm);

		// Batch-fetch stock_uom for all matched items in one API call.
		// This avoids calling frappe.model.set_value(item_code) which would
		// trigger get_item_details AJAX and overwrite the AI-extracted rate.
		const _matched_for_uom = math_enforcement.filter(l => l.item_code_matched);
		if (_matched_for_uom.length > 0) {
			const _item_codes = _matched_for_uom.map(l => l.item_code_matched);
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: "Item",
					filters: [["name", "in", _item_codes]],
					fields: ["name", "stock_uom"],
					limit: _item_codes.length + 5
				},
				callback: function(r) {
					const _uom_map = {};
					(r.message || []).forEach(itm => { _uom_map[itm.name] = itm.stock_uom; });
					_matched_for_uom.forEach(line => {
						const _row = locals[line.doctype] && locals[line.doctype][line.name];
						if (_row && _uom_map[line.item_code_matched]) {
							_row.uom = _uom_map[line.item_code_matched];
						}
					});
					frm.refresh_field("items");
				}
			});
		}

		// Final safety-net restore: yield to the browser event loop then
		// re-assert the AI values. Ensures nothing queued synchronously
		// (form triggers, calc callbacks) has overwritten our values.
		requestAnimationFrame(() => {
			requestAnimationFrame(() => {
				math_enforcement.forEach(line => {
					const r = locals[line.doctype] && locals[line.doctype][line.name];
					if (!r) return;
					r.item_name   = line.desc;
					r.description = line.desc;
					r.qty         = line.qty;
					r.rate        = line.rate;
					r.amount      = line.amount;
					if (line.uom && !line.item_code_matched) r.uom = line.uom;
				});
				frm.refresh_field("items");
				_pw_apply_unmatched_item_styles(frm);
			});
		});

		frappe.dom.unfreeze();

		if (missing_codes) {
			frappe.msgprint({
				title: __("Unmatched Items"),
				indicator: "orange",
				message: __("Some items could not be matched automatically. Rows highlighted in red need an ERPNext Item selected before saving.")
			});
		}
		if (unmatched_tax_rows > 0) {
			frappe.msgprint({
				title: __("Tax Accounts Not Found"),
				indicator: "orange",
				message: __(`${unmatched_tax_rows} tax row(s) from the document could not be filled because no matching GL account was found in ERPNext. Please add the tax rows manually in the Taxes & Charges table.`)
			});
		}
	};

	// If a supplier was set, give ERPNext's supplier-defaults AJAX 500ms to
	// complete before we fill taxes/payment-terms/items. Items are filled with
	// direct assignment (no frappe.model.set_value) so supplier AJAX cannot
	// overwrite rates. The 500ms delay only ensures taxes & payment-terms we
	// set in _fill_items_and_taxes land after the supplier defaults, not before.
	if (supplier_id) {
		setTimeout(_fill_items_and_taxes, 500);
	} else {
		_fill_items_and_taxes();
	}
}
