# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Quotation link validity is now set per supplier row, not per RFQ.

Removes the RFQ-level `pw_quotation_link_validity_days` Custom Field. Fixtures
only add or update fields, so dropping its entry from custom_field.json would
leave it on every existing site; this patch deletes it.

Before deleting, each RFQ's value is copied onto its supplier rows that have no
validity of their own, so RFQs saved before the move keep the days they were set
up with. Deleting a Custom Field does not drop its table column, so the old
values also stay in the database.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

RFQ_FIELD = "Request for Quotation-pw_quotation_link_validity_days"
FIELDNAME = "pw_quotation_link_validity_days"


def execute():
	# Fixtures sync after patches, so the row field may not exist on this site yet.
	create_custom_fields(
		{
			"Request for Quotation Supplier": [
				{
					"fieldname": FIELDNAME,
					"label": "Link Valid For (Days)",
					"fieldtype": "Int",
					"insert_after": "email_id",
					"default": "7",
					"non_negative": 1,
					"in_list_view": 1,
					"description": "Days this supplier's quotation link stays valid after the RFQ is sent.",
				}
			]
		},
		update=True,
	)

	if frappe.db.has_column("Request for Quotation", FIELDNAME):
		rfqs = frappe.db.sql(
			f"""
			select name, `{FIELDNAME}` as days
			from `tabRequest for Quotation`
			where coalesce(`{FIELDNAME}`, 0) > 0
			""",
			as_dict=True,
		)
		for rfq in rfqs:
			frappe.db.sql(
				f"""
				update `tabRequest for Quotation Supplier`
				set `{FIELDNAME}` = %s
				where parenttype = 'Request for Quotation'
					and parent = %s
					and coalesce(`{FIELDNAME}`, 0) = 0
				""",
				(rfq.days, rfq.name),
			)

	if frappe.db.exists("Custom Field", RFQ_FIELD):
		frappe.delete_doc("Custom Field", RFQ_FIELD, ignore_permissions=True)
