# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add "Document Received" tracking to Purchase Receipt.

Accounts wants to record, at the moment goods physically arrive, whether the
supplier sent a Delivery Challan, a tax Invoice, or both -- mirroring
standard Indian trade/GST practice (a DC accompanies goods without a tax
invoice; only the invoice carries GST input credit and should drive booking
the purchase liability). ERPNext already keeps this distinction financially
correct on its own: submitting a Purchase Receipt posts to "Stock Received
But Not Billed", never to Payables, regardless of this field -- the field
below is purely descriptive/tracking, not something the accounting entries
depend on.

Purchase Receipt already has `supplier_delivery_note` (the DC's own
reference number) -- this patch adds the two fields that were missing: the
document-type dropdown itself, and a place to record the supplier's invoice
number/date when one arrives with the shipment (today that pair only exists
later, on Purchase Invoice).

All three are `allow_on_submit` -- a receipt is normally submitted right
after the goods are checked in, which is exactly the moment "DC only" is
still true. The whole point of this field is to let accounts come back to
that SAME submitted receipt once the real invoice arrives later and update
it then, rather than being frozen at whatever was known at goods-receipt
time.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

TARGET_DOCTYPE = "Purchase Receipt"


def execute():
	custom_fields = {
		TARGET_DOCTYPE: [
			{
				"fieldname": "document_received",
				"label": "Document Received",
				"fieldtype": "Select",
				"options": "\nInvoice\nDC\nBoth",
				"insert_after": "supplier_delivery_note",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "supplier_invoice_no",
				"label": "Supplier Invoice No",
				"fieldtype": "Data",
				"insert_after": "document_received",
				"depends_on": "eval:in_list(['Invoice', 'Both'], doc.document_received)",
				"allow_on_submit": 1,
			},
			{
				"fieldname": "supplier_invoice_date",
				"label": "Supplier Invoice Date",
				"fieldtype": "Date",
				"insert_after": "supplier_invoice_no",
				"depends_on": "eval:in_list(['Invoice', 'Both'], doc.document_received)",
				"allow_on_submit": 1,
			},
		]
	}

	create_custom_fields(custom_fields, update=True)
