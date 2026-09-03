// Copyright (c) 2026, Possibleworks and contributors
// For license information, please see license.txt

const CONTROLLER = "possibleworks.finance.doctype.purchase_indent.purchase_indent";
const MAKE_PURCHASE_INDENT = `${CONTROLLER}.make_purchase_indent`;

frappe.ui.form.on("Purchase Indent", {
	refresh(frm) {
		// Pulling items in is only meaningful while the indent is still editable;
		// raising documents from it only once it is approved.
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(
				__("Material Request"),
				() => get_items_from_material_request(frm),
				__("Get Items From")
			);
		} else if (frm.doc.docstatus === 1) {
			add_create_buttons(frm);
		}
	},

	set_warehouse(frm) {
		cascade_to_items(frm, "set_warehouse", "warehouse");
	},

	schedule_date(frm) {
		cascade_to_items(frm, "schedule_date", "schedule_date");
	},
});

/**
 * Opens the multi-select picker for Material Requests. Ticking one or more requests
 * appends every item row from each of them onto this indent; `allow_child_item_selection`
 * additionally lets the user expand a request and tick individual rows.
 *
 * The list is filtered to submitted, non-stopped requests for this indent's company,
 * with no restriction on Purpose -- an indent may be raised against any request type.
 */
function get_items_from_material_request(frm) {
	erpnext.utils.map_current_doc({
		method: MAKE_PURCHASE_INDENT,
		source_doctype: "Material Request",
		target: frm,
		setters: {
			schedule_date: undefined,
		},
		get_query_method: `${CONTROLLER}.get_indentable_material_requests`,
		get_query_filters: {
			company: frm.doc.company,
		},
		allow_child_item_selection: true,
		child_fieldname: "items",
		child_columns: ["item_code", "item_name", "qty", "uom"],
	});
}

/**
 * The MR -> PI -> PO / RFQ / SQ leg. Mirrors the three buttons Material Request offers,
 * and is shown until the indent is fully ordered so a partially-covered indent can
 * still raise the balance.
 */
function add_create_buttons(frm) {
	if (["Stopped", "Cancelled"].includes(frm.doc.status)) return;

	if (flt(frm.doc.per_ordered) < 100) {
		frm.add_custom_button(
			__("Purchase Order"),
			() => prompt_for_supplier_then_create(frm),
			__("Create")
		);
	}

	frm.add_custom_button(
		__("Request for Quotation"),
		() =>
			frappe.model.open_mapped_doc({
				method: `${CONTROLLER}.make_request_for_quotation`,
				frm: frm,
				run_link_triggers: true,
			}),
		__("Create")
	);

	frm.add_custom_button(
		__("Supplier Quotation"),
		() =>
			frappe.model.open_mapped_doc({
				method: `${CONTROLLER}.make_supplier_quotation`,
				frm: frm,
			}),
		__("Create")
	);

	frm.page.set_inner_btn_group_as_primary(__("Create"));
}

/**
 * Naming a supplier is optional. When one is given the resulting Purchase Order keeps
 * only the lines whose item defaults to them, which is how a single indent spanning
 * several suppliers gets split into one order each.
 */
function prompt_for_supplier_then_create(frm) {
	frappe.prompt(
		{
			label: __("For Default Supplier (Optional)"),
			fieldname: "default_supplier",
			fieldtype: "Link",
			options: "Supplier",
			description: __(
				"Leave blank to order every outstanding line. Choose a supplier to order only the items that default to them."
			),
			get_query: () => ({
				query: `${CONTROLLER}.get_default_supplier_query`,
				filters: { doc: frm.doc.name },
			}),
		},
		(values) => {
			frappe.model.open_mapped_doc({
				method: `${CONTROLLER}.make_purchase_order`,
				frm: frm,
				args: { default_supplier: values.default_supplier },
				run_link_triggers: true,
			});
		},
		__("Enter Supplier"),
		__("Create")
	);
}

/**
 * Pushes a header value down onto rows that have none of their own. Rows pulled from
 * a Material Request keep whatever they arrived with -- only blanks are filled, which
 * matches what the server does in `set_defaults_on_items`.
 */
function cascade_to_items(frm, parent_fieldname, child_fieldname) {
	const value = frm.doc[parent_fieldname];
	if (!value) return;

	let changed = false;
	(frm.doc.items || []).forEach((row) => {
		if (!row[child_fieldname]) {
			row[child_fieldname] = value;
			changed = true;
		}
	});

	if (changed) frm.refresh_field("items");
}
