// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// When an Employee's status is changed to Left/Inactive/Suspended while they still have
// Active direct reports, the save is normally hard-blocked -- erpnext's own
// Employee.validate_status for Left, and possibleworks.employee.block_status_change_with_active_reports
// for Inactive/Suspended. Both stay untouched: they remain the safety net for anything that
// bypasses this script (bulk edit from the list view, API scripts, integrations).
//
// This intercepts the status change on the form itself, before Save is even clicked, and
// offers to reassign the affected reports to another Active manager in the same request as
// the status change -- see possibleworks.employee.change_status_with_reassignment.

frappe.provide("possibleworks.employee_status_reassignment");

const REASSIGNMENT_API = "possibleworks.employee";
const STATUSES_REQUIRING_REASSIGNMENT = ["Left", "Inactive", "Suspended"];

frappe.ui.form.on("Employee", {
	refresh(frm) {
		// Baseline to revert to if the reassignment dialog is dismissed without confirming.
		frm.__status_before_edit = frm.doc.status;
	},
	status(frm) {
		possibleworks.employee_status_reassignment.handle_status_change(frm);
	},
});

possibleworks.employee_status_reassignment.handle_status_change = function (frm) {
	const new_status = frm.doc.status;
	const previous_status = frm.__status_before_edit;

	if (frm.is_new() || !STATUSES_REQUIRING_REASSIGNMENT.includes(new_status)) {
		frm.__status_before_edit = new_status;
		return;
	}

	frappe.call({
		method: `${REASSIGNMENT_API}.get_active_direct_reports`,
		args: { employee: frm.doc.name },
		callback(r) {
			const active_reports = r.message || [];
			if (!active_reports.length) {
				frm.__status_before_edit = new_status;
				return;
			}
			possibleworks.employee_status_reassignment.show_dialog(
				frm,
				new_status,
				previous_status,
				active_reports
			);
		},
	});
};

possibleworks.employee_status_reassignment.show_dialog = function (
	frm,
	new_status,
	previous_status,
	active_reports
) {
	const esc = frappe.utils.escape_html;
	const list_html = active_reports
		.map((e) => `<li>${esc(e.employee_name || e.name)}</li>`)
		.join("");

	const fields = [
		{
			fieldtype: "HTML",
			options: `<p>${__("The following employees currently report to {0}:", [
				esc(frm.doc.employee_name),
			])}</p><ul>${list_html}</ul>`,
		},
		{
			fieldname: "new_manager",
			label: __("Reassign them to"),
			fieldtype: "Link",
			options: "Employee",
			reqd: 1,
			get_query: () => ({
				filters: {
					status: "Active",
					name: ["!=", frm.doc.name],
					user_id: ["is", "set"],
				},
			}),
			description: __("Only Active employees with a linked user account can be a manager."),
		},
	];

	if (new_status === "Left") {
		fields.push({
			fieldname: "relieving_date",
			label: __("Relieving Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frm.doc.relieving_date || frappe.datetime.get_today(),
		});
	}

	// Distinguishes a successful confirm (which hides the dialog itself) from every other
	// way a dialog can close (Escape, the X, clicking outside) -- only the latter reverts.
	let confirmed = false;

	const d = new frappe.ui.Dialog({
		title: __("Reassign Reports Before Changing Status"),
		fields,
		primary_action_label: __("Confirm & Reassign"),
		primary_action(values) {
			frappe.call({
				method: `${REASSIGNMENT_API}.change_status_with_reassignment`,
				args: {
					employee: frm.doc.name,
					new_status,
					new_manager: values.new_manager,
					relieving_date: values.relieving_date,
				},
				freeze: true,
				freeze_message: __("Reassigning..."),
				callback(r) {
					confirmed = true;
					d.hide();
					frappe.show_alert({
						message: __("Status updated and {0} report(s) reassigned.", [
							(r.message && r.message.reassigned.length) || 0,
						]),
						indicator: "green",
					});
					frm.reload_doc();
				},
			});
		},
		on_hide() {
			if (!confirmed && frm.doc.status === new_status) {
				frm.set_value("status", previous_status);
			}
		},
	});

	d.show();
};
