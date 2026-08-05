import frappe
from frappe import _
from frappe.model.document import Document

from hrms.hr.utils import validate_active_employee, validate_duplicate_exemption_for_payroll_period


class Form16(Document):
	def validate(self):
		validate_active_employee(self.employee)
		validate_duplicate_exemption_for_payroll_period(
			self.doctype, self.name, self.payroll_period, self.employee
		)
		self.validate_documents_are_private()

	def validate_documents_are_private(self):
		for row in self.documents:
			if not row.attachment:
				continue

			is_private = frappe.db.get_value("File", {"file_url": row.attachment}, "is_private")
			if is_private is not None and not is_private:
				frappe.throw(
					_("Row #{0}: document must be uploaded as a private file since it contains sensitive salary and tax information.").format(
						row.idx
					)
				)
