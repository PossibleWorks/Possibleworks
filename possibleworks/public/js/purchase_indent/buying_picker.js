// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

// Shared by the Purchase Order / Supplier Quotation / Request for Quotation form scripts.
// Lives in `app_include_js` rather than being copied into each of them so the picker's
// filters are defined once -- three drifting copies of the same eligibility rules is
// exactly how one form ends up offering indents the other two consider closed.

frappe.provide("possibleworks.purchase_indent");

possibleworks.purchase_indent.CONTROLLER =
	"possibleworks.finance.doctype.purchase_indent.purchase_indent";

/**
 * Swaps a buying form's "Get Items From > Material Request" picker for the same picker
 * reading approved Purchase Indents.
 *
 * The buying flow is MR -> Purchase Indent -> PO / RFQ / SQ. erpnext lets each of these
 * three forms pull Material Request rows in directly, which bypasses the indent and
 * lets one request be ordered against twice. The capability is replaced rather than
 * removed, so a buyer can still build the document from scratch -- from an approved
 * indent instead of a raw request.
 *
 * Removal, not prevention: `doctype_js` is appended AFTER a doctype's own script
 * (frappe/desk/form/meta.py), so erpnext's refresh has already added the button here.
 *
 * @param {object} frm     the buying form
 * @param {string} mapper  name of the mapper on the Purchase Indent controller
 */
possibleworks.purchase_indent.replace_material_request_picker = function (frm, mapper) {
	if (frm.doc.docstatus !== 0) return;

	frm.remove_custom_button(__("Material Request"), __("Get Items From"));

	frm.add_custom_button(
		__("Purchase Indent"),
		() =>
			erpnext.utils.map_current_doc({
				method: `${possibleworks.purchase_indent.CONTROLLER}.${mapper}`,
				source_doctype: "Purchase Indent",
				target: frm,
				setters: {
					schedule_date: undefined,
				},
				get_query_filters: {
					docstatus: 1,
					status: ["not in", ["Stopped", "Cancelled"]],
					per_ordered: ["<", 100],
					company: frm.doc.company,
				},
				allow_child_item_selection: true,
				child_fieldname: "items",
				child_columns: ["item_code", "item_name", "qty", "ordered_qty"],
			}),
		__("Get Items From")
	);
};
