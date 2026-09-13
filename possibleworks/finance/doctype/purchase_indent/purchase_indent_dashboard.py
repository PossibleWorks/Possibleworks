# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe import _


def get_data():
	"""Fills the Connections tab the cloned schema brings with it.

	Backward links (Material Request, Project) live on this doctype's own items table,
	so they go under `internal_links` as [child fieldname, link fieldname] -- the shape
	Purchase Order uses to point back at its Material Requests.

	Forward links need nothing but the fieldname: `purchase_indent` sits on the target
	doctypes' child tables (added by patches/v1_6/create_purchase_indent_link_fields)
	and Frappe resolves child-table links on its own, which is exactly how Material
	Request lists its own Purchase Orders.
	"""
	return {
		"fieldname": "purchase_indent",
		"internal_links": {
			"Material Request": ["items", "material_request"],
			"Project": ["items", "project"],
		},
		"transactions": [
			{
				"label": _("Buying"),
				"items": ["Purchase Order", "Request for Quotation", "Supplier Quotation"],
			},
			{"label": _("Reference"), "items": ["Material Request", "Project"]},
		],
	}
