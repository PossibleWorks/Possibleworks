# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Routes a Service-purpose document onto its own naming series.

`material_request_type` travels down the buying chain under the same fieldname at every
stage -- Material Request's own field, mirrored as a same-named Custom Field on Purchase
Indent, Purchase Order and Purchase Receipt -- specifically so `frappe.model.mapper`'s
default same-fieldname copying carries it across every mapper (direct or via an indent)
with no field_map changes anywhere. This hook is the one place that reads it.
"""

SERVICE_NAMING_SERIES = {
	"Material Request": "MAT-SR-.YYYY.-",
	"Purchase Indent": "SER-IND-.YYYY.-",
	"Purchase Order": "WO-.YYYY.-",
	"Purchase Receipt": "SRN-.YYYY.-",
}


def set_service_naming_series(doc, method=None):
	"""Only a Service purpose is redirected -- every other purpose (including the
	default "Purchase") keeps the doctype's existing naming series untouched.
	"""
	if doc.get("material_request_type") == "Service":
		doc.naming_series = SERVICE_NAMING_SERIES[doc.doctype]
