// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// "Send Attendance Report" button on Payroll Period. Same server entry point
// (send_attendance_exception_report) as the daily scheduler dispatch -- this
// dialog just lets the from/to dates be overridden for a manual resend.
frappe.ui.form.on("Payroll Period", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(
			__("Send Attendance Report"),
			() => possibleworks.payroll_period_attendance_report.show_dialog(frm),
			__("Actions")
		);
	},
});

frappe.provide("possibleworks.payroll_period_attendance_report");

possibleworks.payroll_period_attendance_report.show_dialog = function (frm) {
	frappe.call({
		method: "possibleworks.utils.payroll_period.get_period_boundaries",
		args: {
			date: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
			company: frm.doc.company,
		},
		callback(r) {
			const defaults = r.message || {};
			possibleworks.payroll_period_attendance_report.open_dialog(frm, defaults.start, defaults.end);
		},
	});
};

possibleworks.payroll_period_attendance_report.open_dialog = function (frm, default_from, default_to) {
	const dialog = new frappe.ui.Dialog({
		title: __("Send Attendance Exception Report"),
		fields: [
			{
				fieldname: "from_date",
				fieldtype: "Date",
				label: __("From Date"),
				default: default_from,
				reqd: 1,
			},
			{
				fieldname: "to_date",
				fieldtype: "Date",
				label: __("To Date"),
				default: default_to,
				reqd: 1,
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			dialog.hide();
			frappe.call({
				method:
					"possibleworks.hr_documents.attendance_payroll_report.attendance_payroll_report.send_attendance_exception_report",
				args: {
					payroll_period: frm.doc.name,
					from_date: values.from_date,
					to_date: values.to_date,
				},
				freeze: true,
				freeze_message: __("Building attendance report..."),
				callback(r) {
					if (r.message && r.message.sent) {
						frappe.show_alert({
							message: __("Attendance report sent ({0} record(s)).", [r.message.rows]),
							indicator: "green",
						});
					}
				},
			});
		},
	});
	dialog.show();
};
