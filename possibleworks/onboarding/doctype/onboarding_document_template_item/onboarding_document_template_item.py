# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OnboardingDocumentTemplateItem(Document):
	"""One document requirement.

	Used as the child table of BOTH `Onboarding Document Template` (the reusable
	policy) and `Onboarding Applicant.required_documents` (the snapshot taken when a
	template is selected). One definition, so the snapshot can never drift in shape
	from the template it came from.
	"""

	pass
