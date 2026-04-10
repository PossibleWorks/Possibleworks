/* Copyright (c) 2026, Possibleworks and contributors */

frappe.ui.form.on("AI Document Queue", {
	refresh: function(frm) {
		_render_dashboard(frm);
		_add_action_buttons(frm);

		// If status is active, reload every 3 seconds to show live progress
		if (frm.doc.status === "Queued" || frm.doc.status === "Processing") {
			if (!frm._auto_reload) {
				frm._auto_reload = setInterval(() => {
					if (frm.doc.status === "Queued" || frm.doc.status === "Processing") {
						frm.reload_doc();
					} else {
						clearInterval(frm._auto_reload);
					}
				}, 3000);
			}
		} else {
			if (frm._auto_reload) {
				clearInterval(frm._auto_reload);
			}
		}
	}
});

function _add_action_buttons(frm) {
	const status = frm.doc.status;

	// Cancel button — visible when job is waiting or running
	if (status === "Queued" || status === "Processing") {
		frm.add_custom_button(__("Cancel Job"), function() {
			frappe.confirm(
				__("Cancel this queue entry? A running job cannot be interrupted mid-file but will stop before the next file."),
				function() {
					frappe.call({
						method: "possibleworks.ap_invoice_processing.bulk_processor.cancel_queue_entry",
						args: { queue_name: frm.doc.name },
						callback: function(r) {
							if (r.message && r.message.status === "Cancelled") {
								frappe.show_alert({ message: __("Queue entry cancelled."), indicator: "orange" });
								frm.reload_doc();
							}
						}
					});
				}
			);
		}, __("Actions")).addClass("btn-warning");
	}

	// Delete button — visible when job is not actively running
	if (status !== "Processing") {
		frm.add_custom_button(__("Delete Entry"), function() {
			frappe.confirm(
				__("Permanently delete this queue entry? This cannot be undone."),
				function() {
					frappe.call({
						method: "possibleworks.ap_invoice_processing.bulk_processor.delete_queue_entry",
						args: { queue_name: frm.doc.name },
						callback: function(r) {
							if (r.message && r.message.deleted) {
								frappe.show_alert({ message: __("Queue entry deleted."), indicator: "green" });
								frappe.set_route("List", "AI Document Queue");
							}
						}
					});
				}
			);
		}, __("Actions")).addClass("btn-danger");
	}
}

function _render_dashboard(frm) {
	let log_data = frm.doc.processing_log;
	let html = "";

	if (!log_data) {
		html = `<div class="text-muted text-center p-4">Waiting to start processing...</div>`;
	} else {
		try {
			let logs = JSON.parse(log_data);
			
			let rows = logs.map(log => {
				let status_color = "var(--text-color)";
				let status_icon = "⏳";
				
				if (log.status === "Processing") { status_color = "var(--orange-500)"; status_icon = "🔄"; }
				if (log.status === "Done") { status_color = "var(--green-500)"; status_icon = "✅"; }
				if (log.status === "AI Draft Created") { status_color = "var(--green-500)"; status_icon = "🤖"; }
				if (log.status === "Failed") { status_color = "var(--red-500)"; status_icon = "❌"; }
				if (log.status === "Flagged" || log.status === "Skipped") { status_color = "var(--yellow-500)"; status_icon = "⚠️"; }

				let message_html = "";
				if (log.error) {
					message_html = `<span class="text-danger">${log.error}</span>`;
				} else if (log.warnings && log.warnings.length > 0) {
					message_html = `<span class="text-warning">${log.warnings.join("<br>")}</span>`;
				} else if (log.status === "Done" || log.status === "AI Draft Created") {
					message_html = `<span class="text-success">Extracted and Matched successfully</span>`;
				}

				const createdDoctype = log.created_doctype || "Purchase Invoice";
				const createdName = log.created_document || log.purchase_invoice;
				const routePart = String(createdDoctype || "")
					.trim()
					.toLowerCase()
					.replace(/\s+/g, "-");
				let pi_link = createdName
					? `<a href="/app/${routePart}/${createdName}" class="btn btn-xs btn-default">View Draft</a>`
					: `<span class="text-muted">-</span>`;

				return `
					<tr>
						<td><span class="font-weight-bold">${log.file_name}</span></td>
						<td>
							<span style="color: ${status_color}; font-weight: 500;">
								${status_icon} ${log.status}
							</span>
						</td>
						<td style="font-size: 12px;">${message_html}</td>
						<td>${log.processing_time ? log.processing_time + "s" : "-"}</td>
						<td>${pi_link}</td>
					</tr>
				`;
			}).join("");

			html = `
				<div class="frappe-control ai-document-queue-dashboard">
					<div class="table-responsive" style="overflow-x: auto;">
						<table class="table table-bordered table-hover" style="width: 100%; min-width: 1180px; table-layout: auto;">
							<thead style="background-color: var(--highlight-color);">
								<tr>
									<th style="width: 24%; white-space: nowrap;">File Name</th>
									<th style="width: 12%; white-space: nowrap;">Status</th>
									<th style="width: 40%;">Details / Errors</th>
									<th style="width: 10%; white-space: nowrap;">Time</th>
									<th style="width: 14%; white-space: nowrap;">Document</th>
								</tr>
							</thead>
							<tbody>
								${rows}
							</tbody>
						</table>
					</div>
				</div>
			`;
		} catch (e) {
			html = `<div class="alert alert-danger">Error rendering dashboard. Invalid JSON in processing_log.</div>`;
		}
	}

	frm.set_df_property('dashboard_html', 'options', html);
}
