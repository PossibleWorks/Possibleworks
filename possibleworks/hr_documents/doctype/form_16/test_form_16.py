# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils.file_manager import save_file

import erpnext
from erpnext.setup.doctype.employee.employee import InactiveEmployeeStatusError
from erpnext.setup.doctype.employee.test_employee import make_employee

from hrms.hr.utils import DuplicateDeclarationError
from possibleworks.hr_documents.api import download_form16_document, list_form16, list_form16_documents

PAYROLL_PERIOD_NAME = "_Test Form16 Period"
PAYROLL_PERIOD_START = "2022-01-01"
PAYROLL_PERIOD_END = "2022-12-31"

EMPLOYEE_1 = "employee@form16.test"
EMPLOYEE_2 = "employee1@form16.test"


def create_payroll_period(name=PAYROLL_PERIOD_NAME, start_date=PAYROLL_PERIOD_START, end_date=PAYROLL_PERIOD_END):
	if frappe.db.exists("Payroll Period", name):
		return frappe.get_doc("Payroll Period", name)

	return frappe.get_doc(
		{
			"doctype": "Payroll Period",
			"name": name,
			"company": erpnext.get_default_company(),
			"start_date": start_date,
			"end_date": end_date,
		}
	).insert()


def make_attachment(is_private=1, content=None, filename="form16.pdf"):
	if content is None:
		# vary content so repeated calls don't just return the same deduped File
		content = f"%PDF-1.4 dummy form16 content {frappe.generate_hash(length=8)}".encode()
	file_doc = save_file(filename, content, None, None, is_private=is_private)
	return file_doc.file_url


class TestForm16(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Form 16")

		self.employee_1 = make_employee(EMPLOYEE_1, company=erpnext.get_default_company())
		self.employee_2 = make_employee(EMPLOYEE_2, company=erpnext.get_default_company())
		self.payroll_period = create_payroll_period().name

	def tearDown(self):
		frappe.set_user("Administrator")

	def make_form16(self, employee=None, payroll_period=None, documents=None):
		doc = frappe.get_doc(
			{
				"doctype": "Form 16",
				"employee": employee or self.employee_1,
				"payroll_period": payroll_period or self.payroll_period,
			}
		)
		if documents is None:
			documents = [{"document_type": "Form 16", "attachment": make_attachment()}]
		for row in documents:
			doc.append("documents", row)
		return doc

	def test_create_and_submit(self):
		form16 = self.make_form16()
		form16.insert()
		form16.submit()

		self.assertEqual(form16.docstatus, 1)
		self.assertEqual(
			form16.employee_name, frappe.db.get_value("Employee", self.employee_1, "employee_name")
		)

	def test_at_least_one_document_required(self):
		form16 = frappe.get_doc(
			{
				"doctype": "Form 16",
				"employee": self.employee_1,
				"payroll_period": self.payroll_period,
			}
		)
		self.assertRaises(frappe.MandatoryError, form16.insert)

	def test_multiple_documents_in_one_record(self):
		form16 = self.make_form16(
			documents=[
				{"document_type": "Part A", "attachment": make_attachment()},
				{"document_type": "Part B", "attachment": make_attachment()},
			]
		)
		form16.insert()
		form16.submit()

		self.assertEqual(len(form16.documents), 2)

		duplicate = self.make_form16()
		self.assertRaises(DuplicateDeclarationError, duplicate.insert)

	def test_duplicate_entry_for_payroll_period(self):
		first = self.make_form16()
		first.insert()
		first.submit()

		duplicate = self.make_form16()
		self.assertRaises(DuplicateDeclarationError, duplicate.insert)

		# a different employee in the same payroll period is not a duplicate
		other_employee_doc = self.make_form16(employee=self.employee_2)
		other_employee_doc.insert()
		other_employee_doc.submit()
		self.assertEqual(other_employee_doc.docstatus, 1)

	def test_inactive_employee_blocked(self):
		frappe.db.set_value("Employee", self.employee_1, "status", "Inactive")
		form16 = self.make_form16()
		self.assertRaises(InactiveEmployeeStatusError, form16.insert)

	def test_public_document_rejected(self):
		form16 = self.make_form16(
			documents=[{"document_type": "Form 16", "attachment": make_attachment(is_private=0)}]
		)
		self.assertRaises(frappe.ValidationError, form16.insert)

	def test_cancel_and_amend(self):
		form16 = self.make_form16()
		form16.insert()
		form16.submit()

		form16.cancel()
		self.assertEqual(form16.docstatus, 2)

		amended = frappe.copy_doc(form16)
		amended.docstatus = 0
		amended.amended_from = form16.name
		amended.documents = []
		amended.append("documents", {"document_type": "Form 16 (Revised)", "attachment": make_attachment()})
		amended.insert()
		amended.submit()

		self.assertEqual(amended.docstatus, 1)
		self.assertEqual(amended.amended_from, form16.name)

	def test_employee_self_service_scoping(self):
		own = self.make_form16(employee=self.employee_1)
		own.insert()
		own.submit()

		others = self.make_form16(employee=self.employee_2)
		others.insert()
		others.submit()

		frappe.set_user(EMPLOYEE_1)
		try:
			visible = frappe.get_list("Form 16", pluck="name")
			self.assertIn(own.name, visible)
			self.assertNotIn(others.name, visible)

			frappe.get_doc("Form 16", own.name).check_permission("read")
			self.assertRaises(
				frappe.PermissionError, frappe.get_doc("Form 16", others.name).check_permission, "read"
			)
		finally:
			frappe.set_user("Administrator")

	def test_list_and_download_form16_documents_api(self):
		own = self.make_form16(
			employee=self.employee_1,
			documents=[
				{"document_type": "Part A", "attachment": make_attachment()},
				{"document_type": "Part B", "attachment": make_attachment()},
			],
		)
		own.insert()
		own.submit()

		others = self.make_form16(employee=self.employee_2)
		others.insert()
		others.submit()

		frappe.set_user(EMPLOYEE_1)
		try:
			names = [r.name for r in list_form16()]
			self.assertIn(own.name, names)
			self.assertNotIn(others.name, names)

			own_documents = list_form16_documents(own.name)
			self.assertEqual(len(own_documents), 2)

			for row in own_documents:
				download_form16_document(own.name, row["row_name"])
				self.assertEqual(frappe.local.response.filename, "form16.pdf")
				self.assertTrue(frappe.local.response.filecontent)

			self.assertRaises(frappe.PermissionError, list_form16_documents, others.name)

			other_row_name = others.documents[0].name
			self.assertRaises(frappe.PermissionError, download_form16_document, others.name, other_row_name)
		finally:
			frappe.set_user("Administrator")
