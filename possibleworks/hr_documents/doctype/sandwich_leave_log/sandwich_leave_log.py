# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class SandwichLeaveLog(Document):
	def validate(self):
		# Defense in depth: the orchestrator already checks for an existing log
		# row before creating one, but this guards against ever double-logging
		# the same employee+date even if that pre-check is ever bypassed.
		duplicate = frappe.db.exists(
			"Sandwich Leave Log",
			{
				"employee": self.employee,
				"date": self.date,
				"name": ["!=", self.name],
			},
		)
		if duplicate:
			frappe.throw(
				_("{0} is already logged as a Sandwich Leave Policy date for employee {1} ({2}).").format(
					self.date, self.employee, duplicate
				)
			)
