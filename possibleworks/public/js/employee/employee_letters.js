// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// Renders the letter cards on the Employee form. Letters are driven by the
// "Employee Letter Template" doctype, so HR Admins can add their own letters in
// addition to the seeded defaults (Relieving Letter, Experience Letter,
// Service Certificate, Visa Letter).
//
// Each template's `placement` decides which of the two mount points it renders
// into: the "Letters" tab, or the collapsible section beside the relieving
// fields on the Employee Exit tab.

frappe.provide("possibleworks.employee_letters");

const LETTER_API = "possibleworks.hr_documents.letters.api";
const TEMPLATE_DOCTYPE = "Employee Letter Template";
const ALLOWED_ROLES = ["System Manager", "HR Manager"];
const STYLE_ID = "pw-employee-letters-css";

// One entry per mount point. `placement` matches the values in
// hr_documents/letters/utils.py; only the Letters tab authors new templates, so
// the exit section stays a plain list of the two offboarding letters.
const MOUNTS = [
	{ fieldname: "custom_letters_html", placement: "Letters", can_create: true },
	{ fieldname: "custom_employee_letters_html", placement: "Employee Exit", can_create: false },
];

const esc = (s) => frappe.utils.escape_html(s == null ? "" : String(s));

frappe.ui.form.on("Employee", {
	refresh(frm) {
		possibleworks.employee_letters.render(frm);
	},
});

possibleworks.employee_letters.render = function (frm) {
	// A mount is missing until its patch has run, so skip rather than assume both.
	const mounts = MOUNTS.map((m) => ({ ...m, field: frm.get_field(m.fieldname) })).filter(
		(m) => m.field
	);
	if (!mounts.length) return;

	const has_access = ALLOWED_ROLES.some((r) => frappe.user.has_role(r));
	if (!has_access) {
		mounts.forEach((m) => m.field.$wrapper.empty());
		return;
	}

	if (frm.is_new()) {
		const note = `<p class="text-muted small">${__("Save the employee to generate letters.")}</p>`;
		mounts.forEach((m) => m.field.$wrapper.html(note));
		return;
	}

	// One call for every mount: the templates are filtered client-side by placement.
	frappe.call({
		method: `${LETTER_API}.list_letter_templates`,
		args: { employee: frm.doc.name },
		callback(r) {
			const templates = r.message || [];
			mounts.forEach((m) =>
				possibleworks.employee_letters.render_cards(
					frm,
					m,
					templates.filter((t) => t.placement === m.placement)
				)
			);
		},
	});
};

possibleworks.employee_letters.render_cards = function (frm, mount, templates) {
	const field = mount.field;
	const cards = templates
		.map((t) => {
			const disabled = !t.available;
			const note = disabled
				? `<div class="pw-letter-note">${frappe.utils.icon("info", "xs")} ${__("Set a Relieving Date to enable")}</div>`
				: "";
			return `
			<div class="pw-letter-card ${disabled ? "is-disabled" : ""}" data-letter="${esc(t.name)}">
				<div class="pw-letter-head">
					<span class="pw-letter-icon">${frappe.utils.icon(t.icon || "file-text", "md")}</span>
					<div class="pw-letter-meta">
						<div class="pw-letter-title">${esc(t.template_name)}</div>
						<div class="pw-letter-desc">${esc(t.description || "")}</div>
					</div>
					<button class="btn btn-xs pw-letter-edit" data-letter="${esc(t.name)}" title="${__("Edit template")}">
						${frappe.utils.icon("edit", "xs")}
					</button>
				</div>
				<div class="pw-letter-actions">
					<button class="btn btn-xs btn-default" data-action="print" ${disabled ? "disabled" : ""}>
						${frappe.utils.icon("printer", "xs")} ${__("Preview / Print")}
					</button>
					<button class="btn btn-xs btn-default" data-action="email" ${disabled ? "disabled" : ""}>
						${frappe.utils.icon("mail", "xs")} ${__("Email")}
					</button>
				</div>
				${note}
			</div>`;
		})
		.join("");

	let empty = "";
	if (!templates.length) {
		empty = mount.can_create
			? __("No letter templates yet. Create one to get started.")
			: __("No letters configured for this section.");
		empty = `<p class="text-muted small">${empty}</p>`;
	}

	const toolbar = mount.can_create
		? `<div class="pw-letters-toolbar">
				<button class="btn btn-xs btn-default pw-new-letter">
					${frappe.utils.icon("add", "xs")} ${__("New Letter Template")}
				</button>
			</div>`
		: "";

	possibleworks.employee_letters.inject_styles();
	field.$wrapper.html(`
		<div class="pw-letters">
			${toolbar}
			<div class="pw-letters-grid">${cards}</div>
			${empty}
		</div>
	`);

	field.$wrapper.find(".pw-new-letter").on("click", () =>
		possibleworks.employee_letters.create_template(frm)
	);

	field.$wrapper.find(".pw-letter-edit").on("click", function (e) {
		e.stopPropagation();
		frappe.set_route("Form", TEMPLATE_DOCTYPE, $(this).data("letter"));
	});

	field.$wrapper.find(".pw-letter-card").each(function () {
		const letter = $(this).data("letter");
		$(this)
			.find(".pw-letter-actions button[data-action]")
			.on("click", function () {
				const action = $(this).data("action");
				if (action === "print") possibleworks.employee_letters.print_view(frm, letter);
				else if (action === "email") possibleworks.employee_letters.email(frm, letter);
			});
	});
};

/**
 * Add the shared card CSS to the page once.
 *
 * Both mount points render the same classes, and each re-renders on every form
 * refresh -- injecting the stylesheet with the markup would duplicate it.
 */
possibleworks.employee_letters.inject_styles = function () {
	if (document.getElementById(STYLE_ID)) return;
	$("head").append(`<style id="${STYLE_ID}">${possibleworks.employee_letters.styles()}</style>`);
};

possibleworks.employee_letters.styles = function () {
	return `
		.pw-letters { margin: 4px 0; }
		.pw-letters-toolbar { display: flex; justify-content: flex-end; margin-bottom: 10px; }
		.pw-letters-grid {
			display: grid;
			grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
			gap: 12px;
		}
		.pw-letter-card {
			border: 1px solid var(--border-color);
			border-radius: var(--border-radius-lg, 8px);
			background: var(--card-bg, var(--fg-color));
			padding: 14px 16px;
			display: flex;
			flex-direction: column;
			gap: 12px;
			transition: box-shadow .15s ease, border-color .15s ease;
		}
		.pw-letter-card:hover { box-shadow: var(--shadow-sm); border-color: var(--gray-300); }
		.pw-letter-card.is-disabled { opacity: .6; }
		.pw-letter-head { display: flex; align-items: flex-start; gap: 10px; }
		.pw-letter-icon {
			flex: 0 0 auto;
			width: 32px; height: 32px;
			display: inline-flex; align-items: center; justify-content: center;
			border-radius: var(--border-radius, 6px);
			background: var(--bg-blue, var(--control-bg));
			color: var(--text-on-blue, var(--text-color));
		}
		.pw-letter-icon > svg { width: 16px; height: 16px; }
		.pw-letter-meta { flex: 1 1 auto; min-width: 0; }
		.pw-letter-title { font-weight: 600; color: var(--heading-color, var(--text-color)); }
		.pw-letter-desc { font-size: var(--text-sm, 12px); color: var(--text-muted); line-height: 1.4; margin-top: 2px; }
		.pw-letter-edit { flex: 0 0 auto; opacity: 0; transition: opacity .15s ease; padding: 2px 6px; }
		.pw-letter-card:hover .pw-letter-edit { opacity: .7; }
		.pw-letter-edit:hover { opacity: 1; }
		.pw-letter-actions { display: flex; gap: 6px; flex-wrap: wrap; margin-top: auto; }
		.pw-letter-actions .btn { display: inline-flex; align-items: center; gap: 4px; }
		.pw-letter-note {
			display: flex; align-items: center; gap: 5px;
			font-size: var(--text-xs, 11px); color: var(--text-muted);
		}
		.pw-letter-note > svg { width: 12px; height: 12px; }
	`;
};

// Open Frappe's native in-desk print view with this letter's format
// preselected — the same preview/print experience used for Salary Slips.
// The template name is also the Print Format name.
//
// `meta.default_print_format` alone does NOT do this, which is why the wrong letter
// used to come up. The print page is a singleton: `frappe.pages["print"]` builds one
// PrintView in `on_page_load` and every later visit reuses it (print.js:6), so its
// format selector still holds whatever was chosen last time. `set_default_print_format`
// then opens with an early return when that leftover value is a valid format for the
// doctype (print.js:829-835) — so it only ever honoured `default_print_format` on the
// very first print of a session, and silently ignored every one after.
//
// So set the selector itself and fire its change handler, which is exactly what
// picking the format by hand does.
// `frappe.route_options` is deliberately NOT used here. Router.set_route serialises
// every route option into the URL as JSON (router.js:370-374), so passing `frm` meant
// stuffing the whole Employee doc and its meta into the address bar — for a hint the
// print page then ignored anyway.
possibleworks.employee_letters.print_view = function (frm, letter) {
	frappe.set_route("print", "Employee", frm.doc.name).then(() => {
		possibleworks.employee_letters.select_print_format(letter);
	});
};

/**
 * Force the print view's format selector onto `letter`.
 *
 * Retried because the page renders asynchronously — `set_route` resolves once the route
 * has changed, not once PrintView has finished drawing its sidebar — and because
 * `set_default_print_format` runs during that draw and would otherwise overwrite us.
 */
possibleworks.employee_letters.select_print_format = function (letter, attempt = 0) {
	// The user may have routed somewhere else while we were waiting.
	if (frappe.get_route()[0] !== "print") return;

	const wrapper = document.querySelector(
		'.print-preview-sidebar [data-fieldname="print_format"]'
	);
	const control = wrapper && wrapper.fieldobj;

	if (!control) {
		// ~2s of retries. The sidebar is normally up within one or two frames; giving up
		// quietly just leaves the user on the format they last used.
		if (attempt < 20) {
			setTimeout(() => possibleworks.employee_letters.select_print_format(letter, attempt + 1), 100);
		}
		return;
	}

	if (control.$input.val() === letter) return;

	// `.trigger("change")` is what reaches df.change -> refresh_print_format(), which
	// re-renders the preview. Setting the value alone would show the right name above a
	// stale document.
	control.$input.val(letter).trigger("change");
};

possibleworks.employee_letters.email = function (frm, letter) {
	const default_recipient = frm.doc.company_email || frm.doc.personal_email || "";

	const d = new frappe.ui.Dialog({
		title: __("Email {0}", [letter]),
		fields: [
			{
				fieldname: "recipient",
				label: __("To"),
				fieldtype: "Data",
				reqd: 1,
				default: default_recipient,
				description: __("For multiple recipients, separate email addresses with commas."),
			},
			{
				fieldname: "subject",
				label: __("Subject"),
				fieldtype: "Data",
				default: __("{0} - {1}", [letter, frm.doc.employee_name]),
			},
			{
				fieldname: "message",
				label: __("Message"),
				fieldtype: "Text Editor",
				default: __(
					"Dear {0},<br><br>Please find your {1} attached.<br><br>Regards,<br>HR Team",
					[frm.doc.employee_name, letter]
				),
			},
			{
				fieldtype: "HTML",
				options: `<p class="text-muted small">${__("The letter will be attached as a PDF.")}</p>`,
			},
		],
		primary_action_label: __("Send"),
		primary_action(values) {
			frappe.call({
				method: `${LETTER_API}.email_letter`,
				args: {
					employee: frm.doc.name,
					letter,
					recipient: values.recipient,
					subject: values.subject,
					message: values.message,
				},
				freeze: true,
				freeze_message: __("Sending..."),
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("{0} sent to {1}", [r.message.letter, r.message.sent_to]),
							indicator: "green",
						});
						d.hide();
					}
				},
			});
		},
	});
	d.show();
};

// Dialog to create a new letter template. On save the doctype auto-generates
// a matching Print Format, so the new letter immediately supports
// Preview / Print and Email.
possibleworks.employee_letters.create_template = function (frm) {
	// Placeholders are resolved live from the Employee doctype so every field
	// (including custom fields) is available — nothing is hard-coded here.
	frappe.call({
		method: `${LETTER_API}.get_letter_placeholders`,
		callback(r) {
			possibleworks.employee_letters._open_create_dialog(frm, r.message || { helpers: [], fields: [] });
		},
	});
};

possibleworks.employee_letters._open_create_dialog = function (frm, placeholders) {
	const helpers = placeholders.helpers || [];
	const fields = placeholders.fields || [];

	const helper_chips = helpers
		.map((h) => `<code title="${esc(h.label)}">{{ ${h.name} }}</code>`)
		.join(" ");

	// "label (fieldname)" so users can find a field by its human label but
	// insert the correct token.
	const field_options = [""].concat(
		fields.map((f) => (f.label && f.label !== f.name ? `${f.label} (${f.name})` : f.name))
	);
	const token_from_option = (opt) => {
		const m = /\(([^()]+)\)\s*$/.exec(opt || "");
		return m ? m[1] : opt;
	};

	const help = `<div class="text-muted small" style="margin:2px 0 8px;line-height:1.9;">
		<b>${__("Computed placeholders")}:</b> ${helper_chips}
		<br>${__("You can also use any Employee field below, and conditionals like")}
		<code>{% if is_relieved %}…{% endif %}</code>
	</div>`;

	const d = new frappe.ui.Dialog({
		title: __("New Letter Template"),
		size: "large",
		fields: [
			{
				fieldname: "template_name",
				label: __("Template Name"),
				fieldtype: "Data",
				reqd: 1,
				description: __("Shown on the card; also used as the Print Format name."),
			},
			{ fieldname: "cb1", fieldtype: "Column Break" },
			{
				// Options come from MOUNTS so this never drifts from what the form renders.
				fieldname: "placement",
				label: __("Show Under"),
				fieldtype: "Select",
				options: MOUNTS.map((m) => m.placement).join("\n"),
				default: MOUNTS[0].placement,
				reqd: 1,
				description: __("Which part of the Employee form lists this letter."),
			},
			{
				fieldname: "requires_relieving_date",
				label: __("Requires Relieving Date"),
				fieldtype: "Check",
			},
			{ fieldname: "sb1", fieldtype: "Section Break" },
			{
				fieldname: "letter_title",
				label: __("Letter Title"),
				fieldtype: "Data",
				reqd: 1,
				description: __("Heading, e.g. RELIEVING LETTER"),
			},
			{
				fieldname: "subtitle",
				label: __("Subtitle"),
				fieldtype: "Data",
				description: __("Optional, e.g. TO WHOMSOEVER IT MAY CONCERN"),
			},
			{ fieldname: "description", label: __("Card Description"), fieldtype: "Data" },
			{ fieldtype: "HTML", options: help },
			{
				fieldname: "insert_placeholder",
				label: __("Insert Employee Field"),
				fieldtype: "Select",
				options: field_options,
				description: __("Pick a field to append its placeholder to the body."),
			},
			{ fieldname: "body", label: __("Body"), fieldtype: "Text Editor", reqd: 1 },
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			const doc = Object.assign({ doctype: TEMPLATE_DOCTYPE, enabled: 1 }, values);
			delete doc.insert_placeholder;
			frappe.call({
				method: "frappe.client.insert",
				args: { doc },
				freeze: true,
				freeze_message: __("Creating..."),
				callback(r) {
					if (r.message) {
						frappe.show_alert({
							message: __("Letter template “{0}” created", [r.message.name]),
							indicator: "green",
						});
						d.hide();
						possibleworks.employee_letters.render(frm);
					}
				},
			});
		},
	});

	// Append the chosen field's placeholder token to the body, then reset.
	d.fields_dict.insert_placeholder.$input.on("change", function () {
		const token = token_from_option(d.get_value("insert_placeholder"));
		if (token) {
			const cur = d.get_value("body") || "";
			d.set_value("body", `${cur} {{ ${token} }}`);
		}
		d.set_value("insert_placeholder", "");
	});

	d.show();
};
