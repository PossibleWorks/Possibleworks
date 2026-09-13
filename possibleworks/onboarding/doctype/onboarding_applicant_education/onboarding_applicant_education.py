# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OnboardingApplicantEducation(Document):
	"""Mirrors `Employee Education` for its first six fields so rows copy straight
	across on Employee creation; the extras (start_year, certificate,
	is_highest_qualification) stay on the onboarding record as the intake record."""

	pass
