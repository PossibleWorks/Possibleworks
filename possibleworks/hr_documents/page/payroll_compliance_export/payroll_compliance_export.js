/* Payroll Compliance Export – Possibleworks
   PF (Provident Fund) export: pick a Company, then a Payroll Period scoped to
   that Company, then a Month; map each statutory field to the Salary
   Component that supplies it; preview, then download as CSV/Excel.

   Mapping is intentionally NOT persisted — every visit starts blank and the
   value shown is exactly whatever component the user just picked, read
   straight off that employee's Salary Slip ("NA" if that component isn't on
   a given slip, or the field wasn't mapped). Nothing here is calculated.

   Keep PF_FIELDS in sync with the Python-side field list in constants.py —
   this is the only place the field/description/mapped metadata is
   duplicated, and it only changes if the outsourced template itself changes.
*/

frappe.provide("possibleworks.hr_documents");

const PF_FIELDS = [
	{ fieldname: "UAN Number", description: __("Employee's Universal Account Number (PF identity number)."), mapped: false },
	{ fieldname: "Employee Name", description: __("Employee's name as on the payslip."), mapped: false },
	{ fieldname: "Gross Salary", description: __("Total gross wages for the selected period."), mapped: false },
	{ fieldname: "EPF Wages", description: __("Wage base the employee's PF contribution is calculated on."), mapped: true },
	{ fieldname: "EPS Wages", description: __("Wage base for the Employee Pension Scheme (EPS) contribution."), mapped: true },
	{ fieldname: "EDLI Wages", description: __("Wage base for the Employees Deposit Linked Insurance (EDLI) contribution."), mapped: true },
	{ fieldname: "EPF", description: __("Employee's own PF contribution amount for the period."), mapped: true },
	{ fieldname: "EPS", description: __("Employer's Pension Scheme contribution amount for the period."), mapped: true },
	{ fieldname: "ERPF", description: __("Employer's residual PF contribution (employer PF share other than EPS)."), mapped: true },
	{ fieldname: "LOP Days", description: __("Loss-of-pay / non-contributing days in the period."), mapped: false },
	{ fieldname: "Refund", description: __("Refund of a PF advance adjusted in this period, if any."), mapped: true },
];

const METHOD_PATH = "possibleworks.hr_documents.page.payroll_compliance_export.payroll_compliance_export";

frappe.pages["payroll-compliance-export"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Payroll Compliance Export"),
		single_column: true,
	});

	possibleworks.hr_documents.pf_export_tool = new possibleworks.hr_documents.PayrollComplianceExport(page);
};

possibleworks.hr_documents.PayrollComplianceExport = class PayrollComplianceExport {
	constructor(page) {
		this.page = page;
		this.mapping_controls = {};
		this.periods_by_value = {};

		this.setup_filters();
		this.setup_field_table();
		this.setup_actions();
	}

	setup_filters() {
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.on_company_change(),
		});

		this.period_field = this.page.add_field({
			fieldname: "payroll_period",
			label: __("Payroll Period"),
			fieldtype: "Link",
			options: "Payroll Period",
			reqd: 1,
			get_query: () => ({
				filters: { company: this.company_field.get_value() },
			}),
			change: () => this.on_period_change(),
		});

		this.month_field = this.page.add_field({
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			reqd: 1,
		});

		this.format_field = this.page.add_field({
			fieldname: "file_format",
			label: __("Format"),
			fieldtype: "Select",
			options: ["Excel", "CSV"],
			default: "Excel",
		});
	}

	on_company_change() {
		// Payroll Period options are scoped to the selected Company (via
		// period_field's get_query above); once Company changes, any
		// previously chosen Payroll Period may no longer be valid, so clear
		// it and let on_period_change() cascade the Month/preview reset.
		this.period_field.set_input("");
		this.on_period_change();
	}

	on_period_change() {
		const payroll_period = this.period_field.get_value();

		this.periods_by_value = {};
		this.month_field.df.options = [];
		this.month_field.set_input("");
		this.month_field.refresh();
		this.hide_preview();

		if (!payroll_period) return;

		frappe.call({
			method: `${METHOD_PATH}.get_periods`,
			args: { payroll_period },
			freeze: true,
			callback: (r) => {
				const months = (r.message && r.message.months) || [];
				if (!months.length) {
					frappe.msgprint(__("This Payroll Period has no resolvable payroll cycles."));
					return;
				}
				months.forEach((m, idx) => {
					this.periods_by_value[String(idx)] = m;
				});
				this.month_field.df.options = months.map((m, idx) => ({ value: String(idx), label: m.label }));
				this.month_field.refresh();
				// default to the most recent cycle in this Payroll Period
				this.month_field.set_input(String(months.length - 1));
			},
		});
	}

	setup_field_table() {
		this.page.body.append(`
			<div class="pf-export-tool" style="margin-top: 15px;">
				<table class="table table-bordered">
					<thead>
						<tr>
							<th style="width: 15%">${__("PF Template Field")}</th>
							<th style="width: 45%">${__("Description")}</th>
							<th style="width: 40%">${__("Maps To")}</th>
						</tr>
					</thead>
					<tbody class="pf-field-rows"></tbody>
				</table>
				<div class="pf-preview-section" style="display: none; margin-top: 20px;">
					<h5>${__("Preview")}</h5>
					<div class="pf-preview-table-wrapper" style="overflow-x: auto;"></div>
				</div>
			</div>
		`);

		const $rows = this.page.body.find(".pf-field-rows");

		PF_FIELDS.forEach((field) => {
			const $tr = $(`
				<tr>
					<td><strong>${frappe.utils.escape_html(field.fieldname)}</strong></td>
					<td class="text-muted">${frappe.utils.escape_html(field.description)}</td>
					<td class="pf-maps-to"></td>
				</tr>
			`).appendTo($rows);

			if (!field.mapped) {
				$tr.find(".pf-maps-to").html(
					`<span class="text-muted">${__("Auto-filled")}</span>`
				);
				return;
			}

			// Mirrors frappe.ui.Page's own add_field internals (make_control +
			// refresh + make_input fallback), just targeting our table cell
			// instead of the page's filter toolbar.
			const control = frappe.ui.form.make_control({
				df: {
					fieldname: field.fieldname,
					fieldtype: "Link",
					options: "Salary Component",
					placeholder: __("Select a Salary Component"),
				},
				parent: $tr.find(".pf-maps-to"),
				only_input: true,
			});
			control.refresh();
			if (!control.$input) control.make_input();

			this.mapping_controls[field.fieldname] = control;
		});
	}

	setup_actions() {
		this.page.set_secondary_action(__("Preview"), () => this.run("preview"));
		this.page.set_primary_action(__("Download"), () => this.run("download"));
	}

	get_mapping() {
		const mapping = {};
		Object.keys(this.mapping_controls).forEach((fieldname) => {
			mapping[fieldname] = this.mapping_controls[fieldname].get_value() || "";
		});
		return mapping;
	}

	validate_selection() {
		const company = this.company_field.get_value();
		const payroll_period = this.period_field.get_value();
		const month_key = this.month_field.get_value();
		if (!company || !payroll_period || !month_key || !this.periods_by_value[month_key]) {
			frappe.msgprint(__("Please select a Company, Payroll Period and Month first."));
			return null;
		}
		return { payroll_period, period: this.periods_by_value[month_key] };
	}

	run(action) {
		const selection = this.validate_selection();
		if (!selection) return;

		const args = {
			payroll_period: selection.payroll_period,
			start_date: selection.period.start_date,
			end_date: selection.period.end_date,
			mapping: JSON.stringify(this.get_mapping()),
		};

		if (action === "preview") {
			frappe.call({
				method: `${METHOD_PATH}.preview_pf_export`,
				args,
				freeze: true,
				callback: (r) => this.render_preview(r.message),
			});
			return;
		}

		args.file_format = this.format_field.get_value();
		open_url_post(frappe.request.url, {
			cmd: `${METHOD_PATH}.download_pf_export`,
			...args,
		});
	}

	render_preview(result) {
		const $section = this.page.body.find(".pf-preview-section");
		const $wrapper = this.page.body.find(".pf-preview-table-wrapper");
		const columns = (result && result.columns) || [];
		const rows = (result && result.rows) || [];

		if (!rows.length) {
			$wrapper.html(`<p class="text-muted">${__("No submitted Salary Slips found for this period.")}</p>`);
			$section.show();
			return;
		}

		let html = '<table class="table table-bordered table-sm"><thead><tr>';
		columns.forEach((column) => {
			html += `<th>${frappe.utils.escape_html(column)}</th>`;
		});
		html += "</tr></thead><tbody>";
		rows.forEach((row) => {
			html += "<tr>";
			columns.forEach((column) => {
				const value = row[column];
				html += `<td>${value === undefined || value === null ? "" : frappe.utils.escape_html(String(value))}</td>`;
			});
			html += "</tr>";
		});
		html += "</tbody></table>";

		$wrapper.html(html);
		$section.show();
	}

	hide_preview() {
		this.page.body.find(".pf-preview-section").hide();
	}
};
