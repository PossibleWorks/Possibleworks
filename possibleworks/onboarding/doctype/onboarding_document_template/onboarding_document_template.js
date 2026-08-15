/* Copyright (c) 2026, Possibleworks and contributors */
/* For license information, please see license.txt */

/**
 * Grey out disabled rows.
 *
 * A disabled row still renders its Required / Allow Multiple ticks in the collapsed
 * grid -- per-row `depends_on` only takes effect in the expanded row editor -- so a
 * suspended requirement reads as an active one. Dimming the row makes "this is not
 * being asked for" visible at a glance, which is where the confusion actually happens.
 */

frappe.ui.form.on("Onboarding Document Template", {
	refresh: function (frm) {
		mark_disabled_rows(frm);
		load_field_options(frm);
	},

	// `{fieldname}_add` is a PARENT-form event, not a child one. A row added before the
	// option list arrives would otherwise build its control with an empty list.
	applicant_fields_add: function (frm) {
		setTimeout(() => apply_field_options(frm), 0);
	},
});

/**
 * Populate the Field picker from live meta rather than a hardcoded Select, so it
 * cannot offer a field the applicant is not allowed to write -- which would produce a
 * form they can fill but never submit.
 *
 * Setting `df.options` is NOT enough on its own. ControlAutocomplete only reads it in
 * `set_options()`, which runs once inside `make_input()` -- so any control built before
 * this async call returns keeps an empty suggestion list even though its docfield looks
 * populated. Hence: cache the options, and push them into controls explicitly.
 */
function load_field_options(frm) {
	if (frm._pw_field_options) {
		apply_field_options(frm);
		return;
	}

	frappe.call({
		method: "possibleworks.onboarding.api.list_applicant_field_options",
		callback: function (r) {
			if (!r.message) return;
			frm._pw_field_options = r.message;
			apply_field_options(frm);
		},
	});
}

function apply_field_options(frm) {
	const options = frm._pw_field_options;
	const grid = frm.fields_dict.applicant_fields && frm.fields_dict.applicant_fields.grid;
	if (!options || !grid) return;

	// New rows read this when their control is built.
	grid.update_docfield_property("fieldname", "options", options);

	// A control built before the options arrived keeps an empty list, so refresh any
	// that already exist. Select re-reads via set_options(); Autocomplete needs set_data().
	(grid.grid_rows || []).forEach((row) => {
		const control = row.columns && row.columns.fieldname && row.columns.fieldname.field;
		if (!control) return;
		control.df.options = options;
		if (typeof control.set_data === "function") control.set_data(options);
		else if (typeof control.set_options === "function") control.set_options();
	});
}

frappe.ui.form.on("Onboarding Document Template Item", {
	enabled: function (frm) {
		mark_disabled_rows(frm);
	},
	documents_add: function (frm) {
		mark_disabled_rows(frm);
	},
});

frappe.ui.form.on("Onboarding Template Field", {
	// form_render fires when a grid row's editor is opened -- the moment its controls
	// are actually built, so this is where a late-arriving option list must land.
	form_render: function (frm) {
		apply_field_options(frm);
	},
});

function mark_disabled_rows(frm) {
	const grid = frm.fields_dict.documents && frm.fields_dict.documents.grid;
	if (!grid) return;

	// Let the grid finish painting before restyling it.
	setTimeout(() => {
		(grid.grid_rows || []).forEach((row) => {
			if (!row.doc || !row.row) return;
			const off = !row.doc.enabled;
			row.row.toggleClass("pw-row-disabled", off);
			row.row.css({ opacity: off ? 0.45 : "" });
			row.row.attr(
				"title",
				off ? __("Disabled — this document is not requested or enforced.") : null
			);
		});
	}, 0);
}
