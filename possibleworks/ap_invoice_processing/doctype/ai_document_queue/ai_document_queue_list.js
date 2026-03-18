/* Copyright (c) 2026, Possibleworks and contributors */

frappe.listview_settings["AI Document Queue"] = {
	add_fields: ["target_doctype", "file_count", "triggered_by", "total_invoices_created"],
	get_indicator: function (doc) {
		const status_colors = {
			"Queued": "blue",
			"Processing": "orange",
			"Done": "green",
			"Partially Done": "yellow",
			"Failed": "red",
			"Cancelled": "grey"
		};
		return [__(doc.status), status_colors[doc.status] || "grey", "status,=," + doc.status];
	},
	formatters: {
		target_doctype(value) {
			return value ? `<span class="ellipsis" title="${frappe.utils.escape_html(value)}">${frappe.utils.escape_html(value)}</span>` : "";
		}
	},
	onload: function(listview) {
		listview.page.add_action_item(__("Cancel Selected"), function() {
			const selected = listview.get_checked_items();
			if (!selected.length) { frappe.msgprint(__("Select at least one entry.")); return; }
			const cancellable = selected.filter(d => d.status === "Queued" || d.status === "Processing");
			if (!cancellable.length) { frappe.msgprint(__("No Queued or Processing entries selected.")); return; }
			frappe.confirm(
				__("Cancel {0} queue entry/entries?", [cancellable.length]),
				function() {
					const calls = cancellable.map(d =>
						frappe.call({
							method: "possibleworks.ap_invoice_processing.bulk_processor.cancel_queue_entry",
							args: { queue_name: d.name }
						})
					);
					Promise.all(calls).then(() => {
						frappe.show_alert({ message: __("{0} entry/entries cancelled.", [cancellable.length]), indicator: "orange" });
						listview.refresh();
					});
				}
			);
		});

		listview.page.add_action_item(__("Delete Selected"), function() {
			const selected = listview.get_checked_items();
			if (!selected.length) { frappe.msgprint(__("Select at least one entry.")); return; }
			const deletable = selected.filter(d => d.status !== "Processing");
			if (!deletable.length) { frappe.msgprint(__("Cannot delete Processing entries — cancel them first.")); return; }
			frappe.confirm(
				__("Permanently delete {0} queue entry/entries?", [deletable.length]),
				function() {
					const calls = deletable.map(d =>
						frappe.call({
							method: "possibleworks.ap_invoice_processing.bulk_processor.delete_queue_entry",
							args: { queue_name: d.name }
						})
					);
					Promise.all(calls).then(() => {
						frappe.show_alert({ message: __("{0} entry/entries deleted.", [deletable.length]), indicator: "green" });
						listview.refresh();
					});
				}
			);
		});
	}
};
