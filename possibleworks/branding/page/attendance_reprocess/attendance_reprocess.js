/* Attendance Reprocess – Possibleworks
   Admin-only trigger for the attendance sync service's manual reprocess API,
   plus a companion action to kick the always-on cron server's plain sync.
   Attendance Sync Admin only (see attendance_reprocess.json "roles").

   Layout: one "Client" section (which companies' employees are in scope --
   several companies can share one attendance sync client, e.g. GRIET/GRCP/GLEC
   all syncing under the same "attendance_griet" client), then one section per
   action with its own button, so each button's own inputs are unambiguous.
*/

frappe.pages["attendance-reprocess"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: "Attendance Reprocess",
		single_column: true,
	});

	new AttendanceReprocess(page);
};

class AttendanceReprocess {
	constructor(page) {
		this.page = page;
		this.company_checks = {};
		this.load_configured_companies();
	}

	load_configured_companies() {
		frappe.call({
			method: "possibleworks.branding.page.attendance_reprocess.attendance_reprocess.get_configured_companies",
			callback: (r) => {
				this.companies = r.message || [];
				this.make_form();
			},
		});
	}

	make_form() {
		this.inject_styles();
		this.$wrapper = $('<div class="attendance-reprocess-form"></div>').appendTo(this.page.body);

		if (!this.companies.length) {
			$(
				`<div class="text-muted" style="padding: var(--padding-md);">
					${__("No companies are configured yet. Add at least one row in")}
					<b>${__("Attendance Sync Settings")}</b> ${__("first.")}
				</div>`
			).appendTo(this.$wrapper);
			return;
		}

		this.build_client_section();
		this.build_sync_section();
		this.build_reprocess_section();
	}

	inject_styles() {
		if (document.getElementById("attendance-reprocess-styles")) return;
		$(
			`<style id="attendance-reprocess-styles">
				.attendance-reprocess-form .form-section { margin-bottom: var(--margin-md); }
				.attendance-reprocess-form .section-body-custom {
					padding: 0 var(--padding-md) var(--padding-md);
					max-width: var(--page-max-width);
					margin: auto;
				}
				.attendance-reprocess-form .section-description {
					color: var(--text-light);
					margin-bottom: var(--margin-sm);
				}
				.attendance-reprocess-form .company-checklist .frappe-control { margin-bottom: 2px; }
			</style>`
		).appendTo("head");
	}

	make_section(title) {
		const $section = $('<div class="form-section"></div>').appendTo(this.$wrapper);
		$(`<div class="section-head">${frappe.utils.escape_html(title)}</div>`).appendTo($section);
		return $('<div class="section-body-custom"></div>').appendTo($section);
	}

	// ==================== CLIENT (shared) ====================

	build_client_section() {
		const $body = this.make_section(__("Client"));
		$(
			`<div class="section-description">${__(
				"Select which companies' employees are in scope. Companies that share the same attendance sync client (e.g. several colleges under one client) can be selected together."
			)}</div>`
		).appendTo($body);

		const $checklist = $('<div class="company-checklist"></div>').appendTo($body);

		this.companies.forEach(({ company }) => {
			const control = frappe.ui.form.make_control({
				df: {
					fieldtype: "Check",
					fieldname: frappe.scrub(company),
					label: company,
					onchange: () => this.on_companies_change(),
				},
				parent: $checklist,
				render_input: true,
			});
			control.refresh();
			this.company_checks[company] = control;
		});
	}

	get_selected_companies() {
		return Object.keys(this.company_checks).filter((company) => this.company_checks[company].get_value());
	}

	on_companies_change() {
		// The employee list is scoped to the selected companies, so a stale
		// selection from a previous set of companies would silently be wrong.
		if (this.employees_field) this.employees_field.set_value([]);
	}

	// ==================== TRIGGER REGULAR SYNC ====================

	build_sync_section() {
		const $body = this.make_section(__("Trigger Regular Sync"));
		$(
			`<div class="section-description">${__(
				"Runs the always-on server's normal incremental sync immediately for the selected companies' client. No date range or employee selection needed."
			)}</div>`
		).appendTo($body);

		$(`<button class="btn btn-sm btn-default">${__("Trigger Regular Sync")}</button>`)
			.appendTo($body)
			.on("click", () => this.trigger_sync());
	}

	trigger_sync() {
		const companies = this.get_selected_companies();
		if (!companies.length) return frappe.msgprint(__("Select at least one Company in the Client section"));

		frappe.confirm(__("Trigger a regular sync now for the selected companies' client?"), () => {
			frappe.dom.freeze(__("Triggering sync..."));
			frappe.call({
				method: "possibleworks.branding.page.attendance_reprocess.attendance_reprocess.trigger_attendance_sync",
				args: { companies: JSON.stringify(companies) },
				callback: (r) => {
					frappe.dom.unfreeze();
					frappe.msgprint({
						title: __("Sync Triggered"),
						message: (r.message && r.message.message) || __("Sync started."),
						indicator: "green",
					});
				},
				error: () => frappe.dom.unfreeze(),
			});
		});
	}

	// ==================== REPROCESS ATTENDANCE ====================

	build_reprocess_section() {
		const $body = this.make_section(__("Reprocess Attendance"));
		$(
			`<div class="section-description">${__(
				"Reprocess biometric attendance for specific employees over a date range. Does not touch the ongoing sync cursor."
			)}</div>`
		).appendTo($body);

		this.employees_field = frappe.ui.form.make_control({
			df: {
				fieldtype: "MultiSelectPills",
				fieldname: "employees",
				label: __("Employees"),
				placeholder: __("Search employees by name..."),
				reqd: 1,
				get_data: (txt) => this.get_employee_options(txt),
			},
			parent: $body,
			render_input: true,
		});
		this.employees_field.refresh();

		$(
			`<div style="margin: -8px 0 var(--margin-sm);">
				<a class="btn btn-xs btn-link select-all-employees" style="padding-left: 0;">${__("Select All")}</a>
				<a class="btn btn-xs btn-link clear-all-employees">${__("Clear All")}</a>
			</div>`
		)
			.insertAfter(this.employees_field.wrapper)
			.on("click", "a.select-all-employees", (e) => {
				e.preventDefault();
				this.select_all_employees();
			})
			.on("click", "a.clear-all-employees", (e) => {
				e.preventDefault();
				this.employees_field.set_value([]);
			});

		this.from_date_field = frappe.ui.form.make_control({
			df: { fieldtype: "Date", fieldname: "from_date", label: __("From Date"), reqd: 1 },
			parent: $body,
			render_input: true,
		});
		this.from_date_field.refresh();

		this.to_date_field = frappe.ui.form.make_control({
			df: { fieldtype: "Date", fieldname: "to_date", label: __("To Date"), reqd: 1 },
			parent: $body,
			render_input: true,
		});
		this.to_date_field.refresh();

		$(`<button class="btn btn-sm btn-primary" style="margin-top: var(--margin-sm);">${__(
			"Reprocess Attendance"
		)}</button>`)
			.appendTo($body)
			.on("click", () => this.trigger_reprocess());
	}

	employee_filters(txt) {
		const filters = {
			company: ["in", this.get_selected_companies()],
			status: "Active",
			attendance_device_id: ["is", "set"],
		};
		if (txt) filters.employee_name = ["like", `%${txt}%`];
		return filters;
	}

	get_employee_options(txt) {
		if (!this.get_selected_companies().length) return Promise.resolve([]);

		return frappe.db
			.get_list("Employee", {
				filters: this.employee_filters(txt),
				fields: ["name", "employee_name"],
				limit: 20,
			})
			.then((records) =>
				records.map((r) => ({
					value: r.name,
					label: `${r.employee_name} (${r.name})`,
					description: r.name,
				}))
			);
	}

	select_all_employees() {
		if (!this.get_selected_companies().length) {
			return frappe.msgprint(__("Select at least one Company in the Client section"));
		}

		frappe.dom.freeze(__("Loading employees..."));
		frappe.db
			.get_list("Employee", {
				filters: this.employee_filters(),
				fields: ["name", "employee_name"],
				limit: 1000,
			})
			.then((records) => {
				frappe.dom.unfreeze();
				const options = records.map((r) => ({
					value: r.name,
					label: `${r.employee_name} (${r.name})`,
					description: r.name,
				}));
				// Populate the pill labels before setting values, otherwise a
				// pill for an employee never fetched via the search dropdown
				// falls back to showing its raw Employee ID instead of name.
				this.employees_field.set_data(options);
				this.employees_field.set_value(options.map((o) => o.value));
			})
			.catch(() => frappe.dom.unfreeze());
	}

	trigger_reprocess() {
		const companies = this.get_selected_companies();
		const employees = this.employees_field.get_value();
		const from_date = this.from_date_field.get_value();
		const to_date = this.to_date_field.get_value();

		if (!companies.length) return frappe.msgprint(__("Select at least one Company in the Client section"));
		if (!employees || !employees.length) return frappe.msgprint(__("Select at least one employee"));
		if (!from_date || !to_date) return frappe.msgprint(__("From Date and To Date are required"));
		if (from_date > to_date) return frappe.msgprint(__("From Date must be before To Date"));

		frappe.confirm(
			__("Reprocess attendance for {0} employee(s) between {1} and {2}?", [
				employees.length,
				from_date,
				to_date,
			]),
			() => this.call_reprocess(companies, employees, from_date, to_date)
		);
	}

	call_reprocess(companies, employees, from_date, to_date) {
		frappe.dom.freeze(__("Triggering reprocess..."));
		frappe.call({
			method:
				"possibleworks.branding.page.attendance_reprocess.attendance_reprocess.trigger_attendance_reprocess",
			args: {
				companies: JSON.stringify(companies),
				employees: JSON.stringify(employees),
				from_date,
				to_date,
			},
			callback: (r) => {
				frappe.dom.unfreeze();
				frappe.msgprint({
					title: __("Reprocess Triggered"),
					message: (r.message && r.message.message) || __("Reprocess started."),
					indicator: "green",
				});
			},
			error: () => frappe.dom.unfreeze(),
		});
	}
}
