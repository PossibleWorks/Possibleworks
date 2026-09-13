# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OnboardingApplicantDocument(Document):
	"""Mirrors the `Form 16 Document` pattern, with `document_type` promoted from a
	free-text Data field to a Link so each site configures its own required set."""

	pass
