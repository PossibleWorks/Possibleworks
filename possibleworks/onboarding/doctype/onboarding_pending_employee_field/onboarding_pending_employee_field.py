# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class OnboardingPendingEmployeeField(Document):
	"""One captured value for a site-specific mandatory `Employee` field.

	`value` is `Long Text` rather than `Data` on purpose: `Data` is varchar(140) and
	`BaseDocument._validate_length` raises `CharacterLengthExceededError` past that,
	so a site whose mandatory Employee field is a Text Editor or Code field would
	hard-fail on save. Nothing queries by value, so the lost index costs nothing.

	`label` / `fieldtype` / `options` are denormalised copies of the live docfield,
	re-stamped on every parent `validate()`. They give the record an audit trail of
	what was actually asked for at capture time, and render without a meta lookup.
	"""

	pass
