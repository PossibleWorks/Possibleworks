# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OnboardingTemplateField(Document):
	"""One field the applicant is asked for on their portal page.

	Used as the child table of BOTH `Onboarding Document Template` (the reusable
	policy) and `Onboarding Applicant.applicant_fields` (the snapshot taken when a
	template is selected) -- the same arrangement as the document rows, so the
	snapshot can never drift in shape from the template it came from.

	Absent from the list means the field is hidden from the applicant entirely; present
	but not editable means shown read-only.
	"""

	pass
