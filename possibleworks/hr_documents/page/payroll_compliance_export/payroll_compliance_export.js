/* Payroll Compliance Export – Possibleworks
   Pick a Company, then a Payroll Period scoped to that Company, then a
   Month; each of the PF and ESCI sections below maps its own statutory
   fields to the Salary Component that supplies them, previews, then
   downloads as CSV/Excel.

   Mapping is intentionally NOT persisted — every visit starts blank and the
   value shown is exactly whatever component the user just picked, read
   straight off that employee's Salary Slip ("NA" if that component isn't on
   a given slip, or the field wasn't mapped). Nothing here is calculated.

   Keep PF_FIELDS/ESCI_FIELDS in sync with the Python-side field lists in
   constants.py — this is the only place the field/description/mapped
   metadata is duplicated, and it only changes if the outsourced templates
   themselves change.
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

const ESCI_FIELDS = [
	{ fieldname: "IP Number", description: __("Employee's ESIC Insured Person (IP) number."), mapped: false },
	{ fieldname: "IP Name", description: __("Employee's name as on the payslip."), mapped: false },
	{ fieldname: "No of Days", description: __("Days wages were paid/payable in the period, rounded up to a whole number."), mapped: false },
	{ fieldname: "Total Monthly Wages", description: __("Total wages for ESI contribution purposes for the period."), mapped: true },
	{ fieldname: "Reason Code", description: __("Not used for this export — always left blank."), mapped: false },
	{ fieldname: "Last Working Day", description: __("Not used for this export — always left blank."), mapped: false },
];

const METHOD_PATH = "possibleworks.hr_documents.page.payroll_compliance_export.payroll_compliance_export";

// Same for both sections: nothing here is calculated, and an unmapped field is
// not an error — it just exports as NA. Say that up front so HR doesn't hunt
// for a validation message that never comes.
const MAPPING_HINT = __(
	"Pick the Salary Component each amount is read from."
);

// How many preview rows are on screen at once. The preview is a spot-check
// before downloading, not the export itself — a 1000-employee period would
// otherwise put 1000 rows in the DOM in a container that doesn't scroll, and
// the page becomes unusable. The full result is still fetched (the row total
// is worth knowing, and `build_*_rows` has to compute every row regardless),
// so paging is instant and never re-queries.
const PREVIEW_PAGE_SIZE = 20;

const EXPORTS = [
	{
		key: "pf",
		title: __("PF Export"),
		fields: PF_FIELDS,
		previewMethod: `${METHOD_PATH}.preview_pf_export`,
		downloadMethod: `${METHOD_PATH}.download_pf_export`,
	},
	{
		key: "esci",
		title: __("ESCI Export"),
		fields: ESCI_FIELDS,
		previewMethod: `${METHOD_PATH}.preview_esci_export`,
		downloadMethod: `${METHOD_PATH}.download_esci_export`,
	},
];

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
		this.mapping_controls = {}; // { pf: {fieldname: control}, esci: {...} }
		this.preview_state = {}; // { pf: {columns, rows, page}, esci: {...} }
		this.periods_by_value = {};

		this.inject_styles();
		this.setup_filters();
		EXPORTS.forEach((exportConfig, index) => {
			if (index > 0) this.add_section_divider();
			this.setup_export_section(exportConfig);
		});
	}

	inject_styles() {
		// Scoped to .pce-card only — deliberately NOT touching .page-container,
		// so Frappe's own breadcrumb/title and the filter toolbar above these
		// sections keep their standard Desk alignment.
		//
		// Every value below comes from an Espresso token (--border-color,
		// --text-*, --weight-*, --border-radius-*). No literal hex anywhere:
		// that is what keeps this page matching the rest of Desk and readable
		// under the dark theme, which hardcoded greys silently break.
		//
		// Layout is card = header / full-bleed table / action bar, each pair
		// separated by exactly one hairline. The table is full-bleed (its row
		// rules run to the card's own border) so there is a single frame around
		// the content rather than a bordered table floating inside a bordered
		// card. Bootstrap's .table/.table-bordered are deliberately not used —
		// their padding and border rules fight these at equal specificity.
		if ($("#payroll-compliance-export-styles").length) return;
		$(`<style id="payroll-compliance-export-styles">
			.pce-card {
				--pce-gutter: 16px;
				--pce-row-height: 44px;
				/* Full width on purpose. Capping this strands the card on the
				   left and leaves a dead region to the right of it, which is
				   worse than a description column with slack in it.
				   .page-body has NO horizontal padding — the page's content
				   gutter comes from .page-form / .page-head, which both use
				   --padding-md. Matching it here puts the card's border on the
				   same vertical axis as the filter inputs and the breadcrumb;
				   without it the card sits flush against the sidebar, 15px
				   left of everything else on the page. */
				margin: var(--margin-md) var(--padding-md) 0;
				background: var(--card-bg);
				border: 1px solid var(--border-color);
				border-radius: var(--border-radius-md);
			}
			/* PF and ESCI are independent exports with their own mapping and
			   their own Download — not two halves of one form. Whitespace alone
			   stopped reading as a boundary once the PF preview was open, so
			   there is an explicit rule between them. Two details make it read
			   as a page-level break rather than a third card border:
			     - full bleed (no horizontal margin), so it runs past both card
			       edges instead of lining up with them;
			     - 2px against the cards' 1px, so the weight differs too.
			   Crowd a 1px inset rule between the two cards instead and you just
			   get three parallel hairlines. */
			.pce-section-divider {
				margin: var(--margin-xl) 0;
				border: 0;
				border-top: 2px solid var(--dark-border-color);
			}
			.pce-section-divider + .pce-card { margin-top: 0; }
			.pce-card:last-child { margin-bottom: var(--margin-xl); }

			.pce-card__header {
				padding: 14px var(--pce-gutter);
				border-bottom: 1px solid var(--border-color);
			}
			.pce-card__title {
				margin: 0;
				font-size: var(--text-base);
				font-weight: var(--weight-semibold);
				color: var(--heading-color);
			}
			.pce-card__hint {
				margin: 4px 0 0;
				font-size: var(--text-sm);
				color: var(--text-muted);
			}

			.pce-table-scroll { overflow-x: auto; }
			.pce-table {
				width: 100%;
				min-width: 640px;
				margin: 0;
				table-layout: fixed;
				border-collapse: collapse;
			}
			.pce-table th,
			.pce-table td {
				height: var(--pce-row-height);
				padding: 0 var(--pce-gutter);
				border: 0;
				border-bottom: 1px solid var(--border-color);
				vertical-align: middle;
				text-align: left;
			}
			.pce-table thead th {
				height: 34px;
				font-size: var(--text-xs);
				font-weight: var(--weight-medium);
				color: var(--text-muted);
				text-transform: uppercase;
				letter-spacing: 0.04em;
			}
			.pce-table tbody tr:last-child > td { border-bottom: 0; }

			/* No vertical rules at all. The card is full width, so descriptions
			   end well short of the Maps To column; a rule there turns that
			   slack into a visible hole in the middle of the row. Without it
			   each row reads the way a settings row should — label on the left,
			   control anchored right — and the space between is just space. */

			.pce-table__field {
				width: 200px;
				font-size: var(--text-sm);
				font-weight: var(--weight-medium);
				color: var(--text-color);
			}
			.pce-table__desc {
				font-size: var(--text-sm);
				color: var(--text-muted);
			}
			/* Wide enough for a Salary Component name, narrow enough that the
			   input still reads as a control instead of a full-bleed grey slab. */
			.pce-table__maps { width: 296px; }
			.pce-table__auto {
				font-size: var(--text-sm);
				color: var(--text-muted);
			}

			/* A mounted frappe.ui.form control brings its own .form-group /
			   .frappe-control spacing, sized for a full form row (label + input
			   + description). Strip that back to fit a table row so mapped and
			   auto-filled rows land on the same baseline grid. */
			.pce-table__maps .frappe-control,
			.pce-table__maps .form-group { margin: 0; }
			.pce-table__maps .control-input-wrapper,
			.pce-table__maps .control-input { margin: 0; padding: 0; }
			.pce-table__maps .help-box,
			.pce-table__maps small.text-muted { display: none; }
			.pce-table__maps .awesomplete { display: block; }
			/* Controls default to --text-base; match the 13px the rest of the
			   row is set in so a chosen component doesn't outsize its own label. */
			.pce-table__maps input { width: 100%; font-size: var(--text-sm); }

			.pce-actions {
				display: flex;
				gap: 8px;
				padding: 12px var(--pce-gutter);
				border-top: 1px solid var(--border-color);
			}
			.pce-actions .btn { margin: 0; }

			.pce-preview {
				padding: var(--pce-gutter);
				border-top: 1px solid var(--border-color);
			}
			.pce-preview__title {
				margin: 0 0 10px;
				font-size: var(--text-xs);
				font-weight: var(--weight-medium);
				color: var(--text-muted);
				text-transform: uppercase;
				letter-spacing: 0.04em;
			}
			.pce-preview__count {
				text-transform: none;
				letter-spacing: 0;
				font-weight: var(--weight-regular);
			}
			.pce-preview__scroll {
				overflow-x: auto;
				border: 1px solid var(--border-color);
				border-radius: var(--border-radius-sm);
			}
			.pce-preview__empty {
				margin: 0;
				font-size: var(--text-sm);
				color: var(--text-muted);
			}
			/* Column rules are justified here and nowhere else: this really is
			   a grid of exported values, and the reader scans down columns. */
			.pce-preview-table {
				width: 100%;
				margin: 0;
				border-collapse: collapse;
				font-size: var(--text-sm);
			}
			.pce-preview-table th,
			.pce-preview-table td {
				padding: 8px 12px;
				border-bottom: 1px solid var(--border-color);
				border-right: 1px solid var(--border-color);
				text-align: left;
				white-space: nowrap;
			}
			.pce-preview-table th:last-child,
			.pce-preview-table td:last-child { border-right: 0; }
			.pce-preview-table tbody tr:last-child > td { border-bottom: 0; }
			.pce-preview-table thead th {
				background: var(--subtle-accent);
				font-weight: var(--weight-medium);
				color: var(--text-muted);
			}

			.pce-preview__pager {
				display: flex;
				align-items: center;
				justify-content: space-between;
				gap: 12px;
				margin-top: 10px;
			}
			.pce-preview__range {
				font-size: var(--text-sm);
				color: var(--text-muted);
			}
			.pce-preview__nav { display: flex; gap: 8px; }
			.pce-preview__nav .btn { margin: 0; }
		</style>`).appendTo("head");
	}

	setup_filters() {
		// No reqd: 1 on these. On a page filter bar it only paints the control
		// red the moment the page loads — flagging an error the user has not
		// had a chance to make yet — and it never blocks anything, because
		// Preview/Download go through validate_selection() which reports what
		// is actually missing.
		this.company_field = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			change: () => this.on_company_change(),
		});

		this.period_field = this.page.add_field({
			fieldname: "payroll_period",
			label: __("Payroll Period"),
			fieldtype: "Link",
			options: "Payroll Period",
			get_query: () => ({
				filters: { company: this.company_field.get_value() },
			}),
			change: () => this.on_period_change(),
		});

		this.month_field = this.page.add_field({
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
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
		this.hide_all_previews();

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

	/** A thematic break between two export sections — an <hr> rather than a
	 *  pseudo-element so it lives in the gap between the cards instead of
	 *  inside one of them, and reads as a break to a screen reader too. */
	add_section_divider() {
		$('<hr class="pce-section-divider">').appendTo(this.page.body);
	}

	setup_export_section(exportConfig) {
		this.mapping_controls[exportConfig.key] = {};

		const $section = $(`
			<div class="pce-card">
				<div class="pce-card__header">
					<h2 class="pce-card__title">${frappe.utils.escape_html(exportConfig.title)}</h2>
					<p class="pce-card__hint">${frappe.utils.escape_html(MAPPING_HINT)}</p>
				</div>
				<div class="pce-table-scroll">
					<table class="pce-table">
						<thead>
							<tr>
								<th class="pce-table__field">${__("Field")}</th>
								<th>${__("Description")}</th>
								<th class="pce-table__maps">${__("Maps To")}</th>
							</tr>
						</thead>
						<tbody class="pce-field-rows"></tbody>
					</table>
				</div>
				<div class="pce-actions">
					<button type="button" class="btn btn-default btn-sm pce-preview-btn">${__("Preview")}</button>
					<button type="button" class="btn btn-primary btn-sm pce-download-btn">${__("Download")}</button>
				</div>
				<div class="pce-preview" style="display: none;">
					<h3 class="pce-preview__title">${__("Preview")}</h3>
					<div class="pce-preview__body"></div>
					<div class="pce-preview__pager" style="display: none;">
						<span class="pce-preview__range"></span>
						<div class="pce-preview__nav">
							<button type="button" class="btn btn-default btn-xs pce-preview__prev">${__("Previous")}</button>
							<button type="button" class="btn btn-default btn-xs pce-preview__next">${__("Next")}</button>
						</div>
					</div>
				</div>
			</div>
		`).appendTo(this.page.body);

		const $rows = $section.find(".pce-field-rows");

		exportConfig.fields.forEach((field) => {
			const $tr = $(`
				<tr>
					<td class="pce-table__field">${frappe.utils.escape_html(field.fieldname)}</td>
					<td class="pce-table__desc">${frappe.utils.escape_html(field.description)}</td>
					<td class="pce-table__maps"></td>
				</tr>
			`).appendTo($rows);

			if (!field.mapped) {
				$tr.find(".pce-table__maps").html(
					`<span class="pce-table__auto">${__("Auto-filled")}</span>`
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
				parent: $tr.find(".pce-table__maps"),
				only_input: true,
			});
			control.refresh();
			if (!control.$input) control.make_input();

			this.mapping_controls[exportConfig.key][field.fieldname] = control;
		});

		$section.find(".pce-preview-btn").on("click", () => this.run(exportConfig, "preview", $section));
		$section.find(".pce-download-btn").on("click", () => this.run(exportConfig, "download", $section));
		$section.find(".pce-preview__prev").on("click", () => this.step_preview(exportConfig, -1));
		$section.find(".pce-preview__next").on("click", () => this.step_preview(exportConfig, 1));

		exportConfig.$section = $section;
	}

	get_mapping(exportKey) {
		const mapping = {};
		const controls = this.mapping_controls[exportKey] || {};
		Object.keys(controls).forEach((fieldname) => {
			mapping[fieldname] = controls[fieldname].get_value() || "";
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

	run(exportConfig, action, $section) {
		const selection = this.validate_selection();
		if (!selection) return;

		const args = {
			payroll_period: selection.payroll_period,
			start_date: selection.period.start_date,
			end_date: selection.period.end_date,
			mapping: JSON.stringify(this.get_mapping(exportConfig.key)),
		};

		if (action === "preview") {
			frappe.call({
				method: exportConfig.previewMethod,
				args,
				freeze: true,
				callback: (r) => this.set_preview(exportConfig, r.message),
			});
			return;
		}

		args.file_format = this.format_field.get_value();
		open_url_post(frappe.request.url, {
			cmd: exportConfig.downloadMethod,
			...args,
		});
	}

	/** Take a fresh preview result, reset to the first page and build the
	 *  table shell. Paging afterwards only swaps <tbody>, so the horizontal
	 *  scroll position of a wide export survives a page change. */
	set_preview(exportConfig, result) {
		const $section = exportConfig.$section;
		const $preview = $section.find(".pce-preview");
		const $title = $section.find(".pce-preview__title");
		const $body = $section.find(".pce-preview__body");
		const $pager = $section.find(".pce-preview__pager");

		const columns = (result && result.columns) || [];
		const rows = (result && result.rows) || [];
		this.preview_state[exportConfig.key] = { columns, rows, page: 0 };

		$title.html(__("Preview"));

		if (!rows.length) {
			$body.html(
				`<p class="pce-preview__empty">${__("No submitted Salary Slips found for this period.")}</p>`
			);
			$pager.hide();
			$preview.show();
			return;
		}

		// Total (not page) count belongs next to the heading — it's the first
		// thing you check before trusting the export. Both forms are separate
		// strings so translators get a real singular, not "1 rows".
		const total =
			rows.length === 1 ? __("1 row") : __("{0} rows", [format_number(rows.length, null, 0)]);
		$title.append(` <span class="pce-preview__count">· ${total}</span>`);

		const head = columns
			.map((column) => `<th>${frappe.utils.escape_html(column)}</th>`)
			.join("");
		$body.html(
			`<div class="pce-preview__scroll">
				<table class="pce-preview-table">
					<thead><tr>${head}</tr></thead>
					<tbody></tbody>
				</table>
			</div>`
		);

		this.render_preview_page(exportConfig);
		$preview.show();
	}

	/** Move the visible page by `delta`, clamped to the available range. */
	step_preview(exportConfig, delta) {
		const state = this.preview_state[exportConfig.key];
		if (!state || !state.rows.length) return;

		const last_page = Math.ceil(state.rows.length / PREVIEW_PAGE_SIZE) - 1;
		const next = Math.min(Math.max(state.page + delta, 0), last_page);
		if (next === state.page) return;

		state.page = next;
		this.render_preview_page(exportConfig);
	}

	render_preview_page(exportConfig) {
		const $section = exportConfig.$section;
		const state = this.preview_state[exportConfig.key];
		if (!state || !state.rows.length) return;

		const { columns, rows } = state;
		const last_page = Math.ceil(rows.length / PREVIEW_PAGE_SIZE) - 1;
		// Clamp defensively: a caller could have set `page` past the end.
		state.page = Math.min(Math.max(state.page, 0), last_page);

		const start = state.page * PREVIEW_PAGE_SIZE;
		const end = Math.min(start + PREVIEW_PAGE_SIZE, rows.length);

		const body = rows
			.slice(start, end)
			.map(
				(row) =>
					`<tr>${columns
						.map((column) => {
							const value = row[column];
							const text =
								value === undefined || value === null ? "" : String(value);
							return `<td>${frappe.utils.escape_html(text)}</td>`;
						})
						.join("")}</tr>`
			)
			.join("");
		$section.find(".pce-preview-table tbody").html(body);

		// Keep the pager's contents in step with what is on screen even when it
		// is hidden, so it can never come back showing a previous result's range.
		$section.find(".pce-preview__range").text(
			__("Showing {0}–{1} of {2}", [
				format_number(start + 1, null, 0),
				format_number(end, null, 0),
				format_number(rows.length, null, 0),
			])
		);
		$section.find(".pce-preview__prev").prop("disabled", state.page === 0);
		$section.find(".pce-preview__next").prop("disabled", state.page === last_page);

		// A single page needs no controls at all — a dead pager under six rows
		// is noise.
		$section.find(".pce-preview__pager").toggle(last_page > 0);
	}

	hide_all_previews() {
		EXPORTS.forEach((exportConfig) => {
			if (exportConfig.$section) exportConfig.$section.find(".pce-preview").hide();
			// Drop the rows too — they belong to the period that was just
			// replaced, so paging them after a filter change would page stale data.
			delete this.preview_state[exportConfig.key];
		});
	}
};
