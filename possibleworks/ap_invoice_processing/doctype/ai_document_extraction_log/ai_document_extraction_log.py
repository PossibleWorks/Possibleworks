# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIDocumentExtractionLog(Document):
	"""AI Document Extraction Log doctype model."""
	pass


# Backward-compatible alias.
APInvoiceExtractionLog = AIDocumentExtractionLog
