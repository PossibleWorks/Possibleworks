# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class AIDocumentQueue(Document):
	"""AI Document Queue doctype model."""
	pass


# Backward-compatible alias.
APInvoiceQueue = AIDocumentQueue
