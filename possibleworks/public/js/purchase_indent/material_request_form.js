// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// The buying flow is MR -> Purchase Indent -> PO / RFQ / SQ. erpnext offers all three
// buying documents straight off a Material Request, which lets a request be ordered
// against twice: once directly and again through an indent raised from the same rows.
//
// These buttons are removed rather than prevented: `doctype_js` is appended AFTER the
// doctype's own script (frappe/desk/form/meta.py), so by the time this runs erpnext's
// refresh has already added them.

const PURCHASE_INDENT_CONTROLLER =
	"possibleworks.finance.doctype.purchase_indent.purchase_indent";

const DIRECT_BUYING_BUTTONS = ["Purchase Order", "Request for Quotation", "Supplier Quotation"];

frappe.ui.form.on("Material Request", {
	refresh(frm) {
		DIRECT_BUYING_BUTTONS.forEach((label) =>
			frm.remove_custom_button(__(label), __("Create"))
		);

		// Stock Entry and Work Order are untouched -- neither is a buying document, and
		// a Material Transfer or Manufacture request never goes through an indent.
		if (frm.doc.docstatus !== 1 || frm.doc.status === "Stopped") return;

		frm.add_custom_button(
			__("Purchase Indent"),
			() =>
				frappe.model.open_mapped_doc({
					method: `${PURCHASE_INDENT_CONTROLLER}.make_purchase_indent`,
					frm: frm,
				}),
			__("Create")
		);

		frm.page.set_inner_btn_group_as_primary(__("Create"));
	},
});
