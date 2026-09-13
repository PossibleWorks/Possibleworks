/* Copyright (c) 2026, Possibleworks and contributors */
/* For license information, please see license.txt */

/**
 * Pending Employee Fields panel.
 *
 * Different sites make different Employee fields mandatory (Custom Fields, plus the
 * Property Setters HR Settings.emp_created_by flips on naming_series/employee_number).
 * Any such field with no counterpart on this form is rendered here as a REAL control
 * built from the live Employee docfield -- so a Link gets a genuine link search and a
 * Select gets its own option list, rather than a plain text box.
 *
 * Two rules are load-bearing, both from frappe/public/js/frappe/form/controls/base_control.js:
 *
 *   Pass `doc`, never `frm`/`doctype`/`docname`. get_status() (base_control.js:48)
 *   branches on `(!this.doctype && !this.docname)` -- absent returns "Write", which is
 *   what we want. Present would make it resolve EMPLOYEE fieldnames against the
 *   ONBOARDING APPLICANT's locals entry. ControlLink.parse_filters (link.js:837) also
 *   dereferences this.doc.parenttype, so an undefined doc throws for any field with
 *   link_filters.
 *
 *   The server strips depends_on / mandatory_depends_on / fetch_from and forces
 *   hidden:0, read_only:0 -- see pending_fields._serialise_docfield. A falsy depends_on
 *   would render a blocking control hidden: an invisible deadlock.
 */

const METHOD_PATH = "possibleworks.onboarding.api";

// Frappe's Phone control only shows a country once the value already carries an ISD
// prefix, so an empty field starts blank and HR has to pick the country every time.
// Preselect it instead. The site's own default country wins; India is the fallback.
const FALLBACK_PHONE_COUNTRY = "India";
const PHONE_FIELDS = ["cell_number", "emergency_phone_number"];

frappe.ui.form.on("Onboarding Applicant", {
	onload: function (frm) {
		frm._pw_refresh_pending = frappe.utils.debounce(() => {
			// The panel is rebuilt from the SAVED child table, so anything typed into it
			// since the last save would be wiped by the re-render. Carry it across.
			if (frm.pw_pending_group) {
				frm._pw_pending_carry = Object.assign(
					{},
					frm._pw_pending_carry,
					frm.pw_pending_group.get_values(true) || {}
				);
			}
			render_pending_fields(frm);
		}, 400);
	},

	refresh: function (frm) {
		toggle_employee_number(frm);
		render_pending_fields(frm);
		add_relink_button(frm);
		add_retry_setup_button(frm);
		add_resync_button(frm);
		add_invite_button(frm);
		render_document_checklist(frm);
		render_invite_status(frm);
		preset_phone_country(frm);
	},

	document_template: function (frm) {
		// The snapshot is rebuilt server-side on save; refresh the checklist so the
		// change is visible immediately rather than after a round trip.
		if (frm.doc.document_template) {
			frappe.show_alert({
				message: __("Save to apply the {0} template.", [frm.doc.document_template]),
				indicator: "blue",
			});
		}
	},

	before_save: function (frm) {
		sync_pending_fields_into_table(frm);
	},

	after_save: function (frm) {
		// The child table is now the source of truth again. Holding the carry any longer
		// would let a stale value keep overriding what was actually saved.
		frm._pw_pending_carry = null;
	},

	same_as_current_address: function (frm) {
		if (frm.doc.same_as_current_address) {
			frm.set_value("permanent_address", frm.doc.current_address);
			frm.set_value("permanent_accommodation_type", frm.doc.current_accommodation_type);
		}
	},
});

/**
 * `employee_number` is only meaningful when HR Settings names Employees by Employee
 * Number -- in the other two modes the Employee field is hidden and reqd:0 upstream,
 * so showing it here would invite HR to fill in something that is never used.
 */
function toggle_employee_number(frm) {
	frappe.db.get_single_value("HR Settings", "emp_created_by").then((mode) => {
		const uses_number = mode === "Employee Number";
		frm.set_df_property("employee_number", "hidden", uses_number ? 0 : 1);
		frm.set_df_property(
			"employee_number",
			"description",
			uses_number
				? __("This becomes the Employee ID. Required before submitting.")
				: __("Not used: HR Settings names Employees by {0}.", [__(mode || "Naming Series")])
		);
		frm.refresh_field("employee_number");
	});
}

/**
 * Preselect the country on empty Phone fields, so typing 9876543210 yields
 * +91-9876543210 without HR opening the picker first.
 *
 * Only ever touches a field that is still empty -- a number already entered (or one
 * carrying a different country) is left exactly as it is.
 */
function preset_phone_country(frm) {
	const country = (frappe.boot && frappe.boot.sysdefaults && frappe.boot.sysdefaults.country) || FALLBACK_PHONE_COUNTRY;
	PHONE_FIELDS.forEach((fieldname) => apply_phone_country(frm, fieldname, country, 0));
}

function apply_phone_country(frm, fieldname, country, attempt) {
	const control = frm.fields_dict[fieldname];
	if (!control || frm.doc[fieldname]) return;

	const picker = control.country_code_picker;
	const ready = picker && typeof picker.on_change === "function" && control.country_codes;

	if (!ready || !control.country_codes[country]) {
		// ControlPhone.make_input is async -- it awaits the country list before the
		// picker exists -- so retry briefly rather than give up on first paint.
		if (attempt < 20) {
			setTimeout(() => apply_phone_country(frm, fieldname, country, attempt + 1), 150);
		}
		return;
	}

	// on_change() ends by focusing its own input; restore focus so opening the form
	// does not yank the cursor into the phone field.
	const previously_focused = document.activeElement;
	picker.on_change(country);
	if (previously_focused && previously_focused.focus) previously_focused.focus();
}

function render_pending_fields(frm) {
	const wrapper = frm.get_field("pending_fields_html").$wrapper;
	wrapper.empty();
	frm.pw_pending_group = null;
	frm.pw_pending_dfs = [];

	if (frm.is_new()) {
		wrapper.html(
			`<div class="text-muted">${__("Save this record to see which Employee fields are still required.")}</div>`
		);
		return;
	}

	// `refresh` fires more than once (load, after save, after reload_doc), so several
	// requests can be in flight at once. Without a token, each callback appends its own
	// panel -- the emptying above already happened -- and the panel renders twice.
	const token = (frm._pw_pending_token = (frm._pw_pending_token || 0) + 1);

	frappe.call({
		method: `${METHOD_PATH}.get_pending_fields`,
		args: { applicant: frm.doc.name },
		callback: function (r) {
			if (!r.message) return;
			// A stale response must never paint over a newer one.
			if (token !== frm._pw_pending_token) return;
			draw_panel(frm, wrapper, r.message);
		},
	});
}

function draw_panel(frm, wrapper, data) {
	// Clear at RENDER time, not request time -- this is the only point at which we
	// know we are the response that gets to draw.
	wrapper.empty();
	frm.pw_pending_group = null;
	frm.pw_pending_dfs = [];

	const has_work = data.native.length || data.blocking.length || data.manual.length;
	// Always visible on a saved record. This section holds BOTH the outstanding fields
	// and the "you are done" confirmation below, so hiding it when the buckets drain
	// removed the last visible control from the tab -- and Frappe drops a tab with no
	// visible section out of the tab bar entirely (Tab.refresh, tab.js:57). HR watched
	// the Pending Employee Fields tab vanish as they filled in the final field, which
	// reads as lost data, and the success message was never reachable.
	frm.toggle_display("pending_fields_section", true);

	// The Captured Values grid is machine-managed and only ever holds rows for fields
	// with no counterpart on this form. Showing it empty invites HR to type into it.
	const captured_rows = (frm.doc.pending_employee_fields || []).length;
	const needs_capture = data.blocking.length || data.conditional.length;
	frm.toggle_display("pending_employee_fields", Boolean(captured_rows || needs_capture));

	if (!has_work && !data.conditional.length) {
		wrapper.html(
			`<div class="alert alert-success">${__("All mandatory Employee fields are satisfied. This record is ready to submit on or after the Date of Joining.")}</div>`
		);
		return;
	}

	const $panel = $('<div class="pw-pending-fields">').appendTo(wrapper);

	if (frm.doc.pending_fields_stale) {
		$panel.append(
			`<div class="alert alert-warning">${__("The mandatory Employee fields on this site changed after this record was filled in. Please review below.")}</div>`
		);
	}

	// Fields that already exist on this form -- point at them rather than drawing a
	// second control for the same value, which would let HR set two different ones.
	if (data.native.length) {
		const links = data.native
			.map((df) => {
				const label = frappe.utils.escape_html(df.label);
				const target = frappe.utils.escape_html(df.source_fieldname);
				const tab = frappe.utils.escape_html(df.tab_label || "");
				return `<li style="margin-bottom:4px">
						<a href="#" data-pw-goto="${target}"><b>${label}</b></a>
						${tab ? `<span class="text-muted small"> &mdash; ${__("on the {0} tab", [tab])}</span>` : ""}
					</li>`;
			})
			.join("");
		$panel.append(
			`<div class="alert alert-warning">
				<b>${__("Still required, and collected on this form:")}</b>
				<div class="small text-muted" style="margin:4px 0 8px">
					${__("Click a field to jump straight to it. Do not enter these below.")}
				</div>
				<ul style="margin-bottom:0">${links}</ul>
			</div>`
		);
		$panel.on("click", "[data-pw-goto]", function (e) {
			e.preventDefault();
			frm.scroll_to_field($(this).attr("data-pw-goto"));
		});
	}

	if (data.manual.length) {
		const items = data.manual
			.map((df) => `<li>${frappe.utils.escape_html(df.label || df.fieldname)}</li>`)
			.join("");
		$panel.append(
			`<div class="alert alert-danger"><b>${__("Required, but cannot be captured here:")}</b><ul>${items}</ul>
			 <div class="small">${__("These must be cleared on the Employee DocType, or the Employee created manually.")}</div></div>`
		);
	}

	const renderable = (data.blocking || []).concat(data.conditional || []);
	if (!renderable.length) return;

	if (frm.doc.docstatus !== 0) {
		// Submitted: the child-table grid below is already the read-only audit record.
		// Rendering editable-looking controls whose values get discarded is worse.
		return;
	}

	$panel.append(`<div class="pw-pending-intro text-muted small">${__("These Employee fields are mandatory on this site but are not part of the standard onboarding form. Values entered here are applied when the Employee is created.")}</div>`);
	const $mount = $('<div class="pw-pending-controls">').appendTo($panel);

	frm.pw_pending_dfs = renderable;
	frm.pw_pending_group = new frappe.ui.FieldGroup({
		body: $mount.get(0),
		// The in-memory Employee preview. Keeps link_filters and any surviving
		// conditional expressions resolving against real Employee keys, and gives
		// set_model_value somewhere to write as the user types.
		doc: data.employee_preview,
		is_dialog: true,
		fields: [{ fieldtype: "Section Break" }].concat(renderable),
	});
	frm.pw_pending_group.make();
	// Anything typed since the last save outranks the saved child table.
	frm.pw_pending_group.set_values(
		Object.assign({}, data.captured || {}, frm._pw_pending_carry || {})
	);

	// Attached AFTER set_values so seeding the panel cannot itself mark the form dirty.
	//
	// These controls write into `employee_preview`, not `frm.doc`, so Frappe never sees
	// the form change and the toolbar keeps offering Submit instead of Save -- leaving
	// HR to submit a record whose pending values were never persisted. Marking the form
	// dirty is what makes `before_save` (and therefore sync_pending_fields_into_table)
	// reachable at all.
	renderable.forEach((df) => {
		df.onchange = () => frm.dirty();
	});

	// Filling a native field can satisfy or introduce a pending field, so re-resolve
	// when one changes.
	(data.mapped_fieldnames || []).forEach((fieldname) => {
		if (frm.fields_dict[fieldname]) {
			frm.fields_dict[fieldname].df.onchange = () => frm._pw_refresh_pending();
		}
	});
}

/**
 * Copy the panel's values into the child table so they are part of the save payload.
 *
 * Ordering is safe: form.js runs script_manager.trigger("validate") then
 * trigger("before_save") and only then posts frm.doc.
 *
 * `get_values(true)` ignores missing-value errors on purpose -- HR must be able to
 * park a half-filled record. The server is the real gate, in before_submit, which
 * also covers the external API caller.
 */
function sync_pending_fields_into_table(frm) {
	if (!frm.pw_pending_group || frm.doc.docstatus !== 0) return;

	const values = frm.pw_pending_group.get_values(true) || {};
	frm.clear_table("pending_employee_fields");

	frm.pw_pending_dfs.forEach((df) => {
		const value = values[df.fieldname];
		if (value === undefined || value === null || value === "") return;

		frm.add_child("pending_employee_fields", {
			fieldname: df.fieldname,
			label: df.label,
			fieldtype: df.fieldtype,
			options: df.options,
			value: String(value),
		});
	});

	frm.refresh_field("pending_employee_fields");
}

/**
 * Recovery for the partial-submit state described in onboarding_applicant.py: the
 * Employee exists but the link is empty. The record is already docstatus=1, so it
 * cannot be re-submitted and this is the only route back.
 */
function add_relink_button(frm) {
	if (frm.doc.docstatus !== 1 || frm.doc.employee) return;

	frm.add_custom_button(__("Relink Employee"), function () {
		frappe.call({
			method: `${METHOD_PATH}.relink_employee`,
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Looking for an Employee created from this record..."),
			callback: function (r) {
				if (r.message && r.message.relinked) {
					frappe.show_alert({
						message: __("Linked to Employee {0}", [r.message.employee]),
						indicator: "green",
					});
					frm.reload_doc();
				}
			},
		});
	}).addClass("btn-warning");
}

/**
 * Finish the steps that run after the Employee is committed.
 *
 * `complete_post_employee_setup` swallows its failures on purpose -- the Employee is
 * already committed by then, so throwing would roll this record back to draft behind a
 * live Employee. That trade is only honest if there is a way to finish the job
 * afterwards, and this is it.
 *
 * Shown ONLY when something is actually missing, which the server reports in
 * `__onload.pending_setup`. A recovery button that sits there on every healthy record
 * invites HR to press it and to wonder what went wrong -- so its presence is the
 * signal, and the label says what it will fix.
 */
function add_retry_setup_button(frm) {
	if (frm.doc.docstatus !== 1 || !frm.doc.employee) return;

	const missing = (frm.doc.__onload || {}).pending_setup || [];
	if (!missing.length) return;

	frm.dashboard.add_comment(
		__("Employee created, but the {0} is still missing.", [missing.join(__(" and "))]),
		"orange",
		true
	);

	frm.add_custom_button(__("Set Up {0}", [missing.join(__(" and "))]), function () {
		frappe.call({
			method: `${METHOD_PATH}.retry_onboarding_setup`,
			args: { name: frm.doc.name },
			freeze: true,
			freeze_message: __("Finishing onboarding setup..."),
			callback: function (r) {
				if (!r.message) return;

				const done = [];
				const missing = [];
				(r.message.role_profile_assigned ? done : missing).push(__("role profile"));
				(r.message.employee_onboarding ? done : missing).push(__("onboarding checklist"));

				if (missing.length) {
					frappe.msgprint({
						title: __("Still Incomplete"),
						indicator: "orange",
						message: __("Could not set up: {0}. Check the Error Log for why.", [
							missing.join(", "),
						]),
					});
					return;
				}

				frappe.show_alert({
					message: __("Onboarding setup complete: {0}", [done.join(", ")]),
					indicator: "green",
				});
				frm.reload_doc();
			},
		});
	}).addClass("btn-warning");
}

/**
 * Pull template edits into this record on purpose.
 *
 * The snapshot exists precisely so template changes do NOT leak into records already
 * in flight, so re-syncing has to be an explicit act -- and it reports what changed
 * rather than silently swapping the requirements underneath HR.
 */
function add_resync_button(frm) {
	if (frm.doc.docstatus !== 0 || !frm.doc.document_template || frm.is_new()) return;

	frm.add_custom_button(__("Re-sync from Template"), function () {
		frappe.confirm(
			__("Replace this record's requirements with the current {0} template?", [
				frm.doc.document_template,
			]),
			function () {
				frm.call("resync_document_template").then((r) => {
					const res = r.message || {};
					const added = (res.added || []).length;
					const removed = (res.removed || []).length;
					frappe.show_alert({
						message:
							added || removed
								? __("{0} added, {1} removed", [added, removed])
								: __("Already up to date"),
						indicator: added || removed ? "green" : "blue",
					});
					frm.reload_doc();
				});
			}
		);
	}, __("Actions"));
}

/**
 * Issue or re-issue the applicant's portal invite. Deliberately an explicit action:
 * nothing is emailed to a candidate until HR decides the record is ready.
 */
function add_invite_button(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 0) return;

	const label = frm.doc.invite_sent_on ? __("Re-send Invite") : __("Invite Applicant");
	frm.add_custom_button(label, function () {
		frappe.confirm(
			frm.doc.invite_sent_on
				? __("Send a fresh link to {0}? Any earlier link stops working.", [frm.doc.personal_email])
				: __("Send the onboarding link to {0}?", [frm.doc.personal_email]),
			function () {
				frm.call("invite_applicant_now").then((r) => {
					const res = r.message || {};
					frappe.show_alert({
						message: res.emailed
							? __("Invite emailed to {0}", [frm.doc.personal_email])
							: __("Invite created — no outgoing email is configured, so copy the link below."),
						indicator: res.emailed ? "green" : "orange",
					});
					frm.reload_doc();
				});
			}
		);
	}, __("Actions"));
}

/**
 * Show the live invite link in the form.
 *
 * Without an outgoing Email Account nothing is sent, so HR needs a copyable link or the
 * feature is untestable and unusable on a site without SMTP.
 */
function render_invite_status(frm) {
	const field = frm.get_field("invite_link_html");
	if (!field) return;
	field.$wrapper.empty();

	if (frm.is_new() || !frm.doc.applicant_user) {
		field.$wrapper.html(
			`<div class="text-muted small">${__("Use Actions → Invite Applicant to give this person access to their own onboarding page.")}</div>`
		);
		return;
	}

	const expired =
		frm.doc.invite_expires_on &&
		frappe.datetime.now_datetime() > frm.doc.invite_expires_on;

	if (!frm.doc.invite_expires_on || expired) {
		field.$wrapper.html(
			`<div class="text-muted small">${__("No active link. Use Actions → Re-send Invite.")}</div>`
		);
		return;
	}

	frappe.call({
		method: `${METHOD_PATH.replace(".api", ".portal")}.get_invite_link`,
		args: { name: frm.doc.name },
		callback: function (r) {
			if (!r.message) return;
			const url = frappe.utils.escape_html(r.message.link);
			field.$wrapper.html(
				`<div class="small">
					<div class="text-muted" style="margin-bottom:4px">${__("Personal link — do not share")}</div>
					<input class="form-control input-sm" readonly value="${url}"
						onclick="this.select()" style="font-family:monospace;font-size:11px">
				</div>`
			);
		},
	});
}

function render_document_checklist(frm) {
	const field = frm.get_field("document_checklist_html");
	if (!field) return;

	if (frm.is_new() || !frm.doc.document_template) {
		field.$wrapper.html(
			frm.is_new()
				? ""
				: `<div class="text-muted small">${__("Select a Document Template to see which documents are required.")}</div>`
		);
		return;
	}

	// Same in-flight race as the pending-fields panel: only the newest response paints.
	const token = (frm._pw_checklist_token = (frm._pw_checklist_token || 0) + 1);

	frappe.call({
		method: `${METHOD_PATH}.list_document_types`,
		args: { name: frm.doc.name },
		callback: function (r) {
			if (!r.message) return;
			if (token !== frm._pw_checklist_token) return;

			const rows = r.message
				.filter((d) => d.is_required)
				.map((d) => {
					const ok = d.uploaded;
					return `<li class="${ok ? "text-success" : "text-danger"}">
							${ok ? "&#10003;" : "&#10007;"} ${frappe.utils.escape_html(d.document_type_name)}
						</li>`;
				})
				.join("");

			field.$wrapper.html(
				rows
					? `<div class="pw-doc-checklist"><b>${__("Required documents")}</b><ul>${rows}</ul></div>`
					: `<div class="text-muted small">${__("No documents are marked as required for onboarding.")}</div>`
			);
		},
	});
}
