# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIDocumentProcessorSupportedDocType(Document):
	"""Row model for supported AI document doctypes in settings."""
	pass


# Backward-compatible alias.
APProcessorSupportedDocType = AIDocumentProcessorSupportedDocType
