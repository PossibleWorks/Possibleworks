# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import date_diff, flt, getdate, today


class OnboardingApplicantWorkHistory(Document):
	"""Mirrors `Employee External Work History` for the six fields that copy across.

	ERPNext models `total_experience` as free-text with no dates at all, which is
	unusable for an intake form -- so the dates are collected here and the text is
	derived from them. Row-level date sanity is validated on the parent, which is
	where a readable "Row #N" message can be raised.
	"""

	def set_total_experience(self) -> None:
		if not self.from_date:
			return

		end = today() if self.is_current_employer else self.to_date
		if not end:
			return

		years = flt(date_diff(getdate(end), getdate(self.from_date)) / 365.25, 1)
		if years < 0:
			return

		self.total_experience = f"{years} year" if years == 1 else f"{years} years"
