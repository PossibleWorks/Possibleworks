# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from possibleworks.onboarding.validators import normalise_extension_list


class OnboardingDocumentType(Document):
	"""Vocabulary only: what a document IS.

	Deliberately carries no policy. Whether a document is required, and whether more
	than one file is allowed, varies by the kind of hire and therefore lives on
	`Onboarding Document Template` rows instead. Keeping identity here means "Aadhaar
	Card" is the same thing across every template, uploaded rows have a stable Link
	target, and reporting groups cleanly.
	"""

	def validate(self):
		self.normalise_extensions()

	def normalise_extensions(self) -> None:
		"""Clean lower-case comma list, with every entry checked against the mimetypes
		table so nonsense like "j" cannot be saved."""
		self.allowed_extensions = normalise_extension_list(self.allowed_extensions, throw=True)

	def on_trash(self):
		"""A document type still in use must not vanish -- the referencing rows would
		keep a dangling Link and the required-documents check would silently stop
		enforcing it."""
		for doctype, label in (
			("Onboarding Applicant Document", _("Onboarding Applicant")),
			("Onboarding Document Template Item", _("Onboarding Document Template")),
		):
			if frappe.db.exists(doctype, {"document_type": self.name}):
				frappe.throw(
					_("{0} is in use on at least one {1}. Disable it instead of deleting it.").format(
						frappe.bold(self.name), label
					),
					title=_("Cannot Delete"),
				)

	def get_extensions(self) -> list[str]:
		if not self.allowed_extensions:
			return []
		return [part for part in self.allowed_extensions.split(",") if part]
