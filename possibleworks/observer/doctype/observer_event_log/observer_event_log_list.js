frappe.listview_settings["Observer Event Log"] = {
	get_indicator: function (doc) {
		const colour = {
			Queued: "grey",
			Sending: "blue",
			Sent: "green",
			Failed: "red",
			Dropped: "orange",
		};
		return [__(doc.status), colour[doc.status] || "grey", "status,=," + doc.status];
	},
	onload: function (list_view) {
		frappe.require("logtypes.bundle.js", () => {
			frappe.utils.logtypes.show_log_retention_message(list_view.doctype);
		});
	},
};
