# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe import _


@frappe.whitelist()
def list_form16(employee=None, payroll_period=None):
	"""Return submitted Form 16 records visible to the calling user.

	Scoping is handled by frappe.get_list's user-permission filtering: an
	employee-scoped caller (session or API key) only ever sees their own
	records regardless of the `employee` argument; an HR-role caller sees
	everyone's.
	"""
	filters = {"docstatus": 1}
	if employee:
		filters["employee"] = employee
	if payroll_period:
		filters["payroll_period"] = payroll_period

	return frappe.get_list(
		"Form 16",
		filters=filters,
		fields=["name", "employee", "employee_name", "payroll_period", "creation"],
		order_by="creation desc",
	)


@frappe.whitelist()
def list_form16_documents(name):
	"""List the documents attached to a single Form 16 record.

	`doc.check_permission("read")` enforces the same scoping as `list_form16` -
	an employee can only list documents on their own record.
	"""
	doc = frappe.get_doc("Form 16", name)
	doc.check_permission("read")

	return [
		{
			"row_name": row.name,
			"document_type": row.document_type,
			"file_name": frappe.db.get_value("File", {"file_url": row.attachment}, "file_name"),
		}
		for row in doc.documents
		if row.attachment
	]


@frappe.whitelist()
def download_form16_document(name, row_name):
	"""Stream a single document row's attachment back to an authenticated caller.

	`doc.check_permission("read")` enforces the scoping - not the `name`/`row_name`
	arguments - so a caller cannot download another employee's document by
	guessing/passing a different name, even with a valid API key.
	"""
	doc = frappe.get_doc("Form 16", name)
	doc.check_permission("read")

	if doc.docstatus != 1:
		frappe.throw(_("Form 16 {0} is not submitted yet").format(name))

	row = next((d for d in doc.documents if d.name == row_name), None)
	if not row or not row.attachment:
		frappe.throw(_("No such document {0} on Form 16 {1}").format(row_name, name))

	file_doc = frappe.get_doc("File", {"file_url": row.attachment})

	frappe.local.response.filename = file_doc.file_name
	frappe.local.response.filecontent = file_doc.get_content()
	frappe.local.response.type = "download"
