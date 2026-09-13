// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// See public/js/purchase_indent/buying_picker.js for why the Material Request picker is
// swapped out here rather than left alongside.

frappe.ui.form.on("Request for Quotation", {
	refresh(frm) {
		possibleworks.purchase_indent.replace_material_request_picker(frm, "make_request_for_quotation");
	},
});
