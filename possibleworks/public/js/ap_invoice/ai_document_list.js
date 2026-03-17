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

const AI_ROLLOUT_DOCTYPES = [
	"Purchase Invoice",
	"Purchase Receipt",
	"Supplier Quotation",
	"Payment Entry",
	"Sales Order",
	"Quotation",
	"Delivery Note"
];

function _build_common_listview_settings(targetDoctype) {
	return {
		onload: function(listview) {
			_pw_get_ai_access(targetDoctype).then((access) => {
				if (!access || !access.allowed) return;

				// Each button has its own independent guard so a missing queue
				// button is added even when the bulk-scan button already exists.
				if (!listview.page.inner_toolbar.find(".pw-ai-bulk-scan").length) {
					listview.page.add_inner_button(
						__("🤖 AI Bulk Scan"),
						() => _open_bulk_process_dialog(listview, targetDoctype)
					).addClass("ap-extract-btn pw-ai-bulk-scan");
				}

				if (!listview.page.inner_toolbar.find(".pw-ai-queue").length) {
					listview.page.add_inner_button(
						__("🤖 AI Processing Queue"),
						() => frappe.set_route("List", "AI Document Queue")
					).addClass("pw-ai-queue");
				}
			});
		}
	};
}

function _build_purchase_invoice_settings() {
	return {
		..._build_common_listview_settings("Purchase Invoice"),
		add_fields: ["remarks", "status", "docstatus"],
		has_indicator_for_draft: true,
		get_indicator: function(doc) {
			if (doc.docstatus === 0) {
				if (_is_ai_bulk_draft(doc)) {
					return [__("AI Draft Created"), "blue", "docstatus,=,0"];
				}
				return [__("Draft"), "red", "docstatus,=,0"];
			}
			if (doc.docstatus === 1) {
				return [__("Submitted"), "blue", "docstatus,=,1"];
			}
			if (doc.docstatus === 2) {
				return [__("Cancelled"), "red", "docstatus,=,2"];
			}
		},
		formatters: {
			status: function(value, df, doc) {
				if (doc.docstatus === 0 && _is_ai_bulk_draft(doc)) {
					return _status_pill(__("AI Draft Created"), "blue", "Draft");
				}
				const label = value || (doc.docstatus === 0 ? __("Draft") : "");
				if (!label) return value;
				return _status_pill(__(label), frappe.utils.guess_colour(label), label);
			}
		}
	};
}

AI_ROLLOUT_DOCTYPES.forEach((doctypeName) => {
	if (doctypeName === "Purchase Invoice") {
		frappe.listview_settings[doctypeName] = _build_purchase_invoice_settings();
		return;
	}
	frappe.listview_settings[doctypeName] = _build_common_listview_settings(doctypeName);
});

function _is_ai_bulk_draft(doc) {
	const remarks = (doc.remarks || "").toLowerCase();
	return doc.docstatus === 0 && remarks.includes("draft created automatically via ai bulk upload");
}

function _status_pill(label, color, filter_value) {
	const safe_label = frappe.utils.escape_html(label || "");
	const safe_filter = frappe.utils.escape_html(filter_value || label || "");
	return `<span class="indicator-pill ${color} ellipsis" data-filter="status,=,${safe_filter}">
		<span class="ellipsis">${safe_label}</span>
	</span>`;
}

function _open_bulk_process_dialog(listview, targetDoctype) {
	let uploaded_files = [];
	let _submitted = false;
	let _debounce_timer = null;

	new frappe.ui.FileUploader({
		allow_multiple: true,
		on_success: function(file_doc) {
			const doc = file_doc.message || file_doc;
			if (doc && doc.file_url) {
				uploaded_files.push(doc.file_url);
			}
			// Proper debounce: each successful upload resets the timer so
			// _submit_to_queue fires exactly once — 1.2 s after the LAST
			// on_success, by which time all concurrent uploads have landed.
			clearTimeout(_debounce_timer);
			_debounce_timer = setTimeout(function() {
				if (_submitted || !uploaded_files.length) return;
				_submitted = true;
				_submit_to_queue(uploaded_files, targetDoctype);
			}, 1200);
		}
	});
}

function _submit_to_queue(file_urls, targetDoctype) {
	if (!file_urls || file_urls.length === 0) {
		frappe.msgprint({
			title: __("No Files"),
			indicator: "orange",
			message: __("No files were uploaded. Please try again.")
		});
		return;
	}

	frappe.show_alert({
		message: __("Queuing {0} file(s) for AI processing ({1})...", [file_urls.length, targetDoctype]),
		indicator: "blue"
	});

	const is_single_zip = file_urls.length === 1 && file_urls[0].toLowerCase().endsWith(".zip");
	let args = {
		target_doctype: targetDoctype
	};

	if (is_single_zip) {
		args.zip_file_url = file_urls[0];
	} else {
		args.file_urls = JSON.stringify(file_urls);
	}

	frappe.call({
		method: "possibleworks.ap_invoice_processing.bulk_processor.enqueue_bulk_processing",
		args: args,
		callback: function(r) {
			if (r.message && r.message.batch_id) {
				frappe.msgprint({
					title: __("Batch Queued ✅"),
					indicator: "green",
					message: __(
						"Successfully queued {0} document(s) for AI processing.<br/>" +
						"You will receive a notification when the batch is complete.<br/><br/>" +
						"<b>Batch ID:</b> {1}",
						[r.message.count, r.message.batch_id]
					)
				});
			} else {
				frappe.msgprint({
					title: __("Queue Failed"),
					indicator: "red",
					message: __("Failed to queue the files. Please check the error log.")
				});
			}
		}
	});
}
