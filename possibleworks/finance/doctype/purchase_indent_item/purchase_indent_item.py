# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class PurchaseIndentItem(Document):
	"""One line of a Purchase Indent.

	No logic of its own on purpose: quantity, amount and UOM conversion are all
	derived in the parent's `validate`, so a row can never be validated outside the
	context of the indent it belongs to.
	"""
