// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// Renders a dynamic placeholder helper (derived from the Employee doctype)
// into the "Placeholders" HTML field. Clicking a placeholder appends it to
// the letter body.

frappe.ui.form.on("Employee Letter Template", {
	refresh(frm) {
		frappe.call({
			method:
				"possibleworks.hr_documents.letters.api.get_letter_placeholders",
			callback(r) {
				render_placeholder_help(frm, r.message || { helpers: [], fields: [] });
			},
		});
	},
});

function render_placeholder_help(frm, placeholders) {
	const field = frm.get_field("placeholders_help");
	if (!field) return;

	const esc = frappe.utils.escape_html;
	const chip = (name, label) =>
		`<code class="pw-ph" role="button" title="${esc(label || name)}"
			style="cursor:pointer;margin:2px 4px 2px 0;display:inline-block;">{{ ${esc(name)} }}</code>`;

	const helpers = (placeholders.helpers || []).map((h) => chip(h.name, h.label)).join("");
	const fields = (placeholders.fields || []).map((f) => chip(f.name, f.label)).join("");

	field.$wrapper.html(`
		<div style="padding:10px 12px;background:var(--control-bg);border-radius:6px;font-size:12px;line-height:1.9;">
			<div><b>${__("Computed placeholders")}</b> ${__("(formatted / derived)")}:</div>
			<div style="margin-bottom:8px;">${helpers}</div>
			<div><b>${__("Employee fields")}</b> ${__("(click to insert into the body)")}:</div>
			<div style="max-height:120px;overflow:auto;">${fields}</div>
			<div class="text-muted" style="margin-top:8px;">
				${__("Conditionals are supported, e.g.")} <code>{% if is_relieved %}…{% endif %}</code>
			</div>
		</div>
	`);

	field.$wrapper.find(".pw-ph").on("click", function () {
		const token = $(this).text().trim();
		const cur = frm.doc.body || "";
		frm.set_value("body", `${cur} ${token}`);
	});
}
