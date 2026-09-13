# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""A reusable document policy.

Separates vocabulary from policy: `Onboarding Document Type` says WHAT a document is
(name, description, file types); a template says WHICH documents a given kind of hire
must supply. That is what lets an intern and a full-time hire need different things.

An `Onboarding Applicant` never validates against a template directly -- selecting one
copies its rows onto the applicant (see `OnboardingApplicant.sync_required_documents`),
so editing a template can never change the requirements of someone already in flight.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from possibleworks.onboarding.constants import (
	APPLICANT_READONLY_FIELDS,
	APPLICANT_SHOWABLE_CHILD_TABLES,
	APPLICANT_TEMPLATE_FIELDS,
	DOCTYPE,
)
from possibleworks.onboarding.validators import (
	InvalidFileExtensionError,
	normalise_extension_list,
)

# Template field -> the Onboarding Applicant field it is matched against.
#
# The `applies_to_` prefix is load-bearing, not cosmetic: a Link field named plainly
# `company` is silently auto-filled from the session's default company by
# `set_user_and_static_default_values` (frappe/model/create_new.py:50, via
# `defaults.get(df.fieldname)`). That would scope every new template to one company
# behind the user's back and quietly break "leave blank to match anything".
MATCH_FIELDS = {
	"applies_to_company": "company",
	"applies_to_employment_type": "employment_type",
	"applies_to_designation": "designation",
}


class OnboardingDocumentTemplate(Document):
	def validate(self):
		self.validate_asks_for_something()
		self.validate_unique_document_types()

	def validate_asks_for_something(self) -> None:
		"""A template with neither documents nor fields asks the applicant for nothing.

		`documents` used to be mandatory, which made a fields-only template impossible
		once the Fields section was added. Either table alone is now valid; both empty
		is not.
		"""
		if not self.documents and not self.applicant_fields:
			frappe.throw(
				_("Add at least one document or one field, otherwise this template asks the applicant for nothing."),
				title=_("Nothing to Collect"),
			)
		self.validate_single_default()
		self.normalise_extensions()
		self.validate_applicant_fields()
		self.warn_about_disabled_required_rows()

	def validate_applicant_fields(self) -> None:
		"""Keep the form definition honest against the security model.

		Only fields in APPLICANT_TEMPLATE_FIELDS may be offered. Anything else -- say
		`date_of_joining` -- would be rejected by the mass-assignment allowlist when the
		applicant saved, leaving them with a form they can fill but never submit.
		"""
		meta = frappe.get_meta(DOCTYPE)
		seen: dict[str, int] = {}
		forced_readonly: list[str] = []

		for row in self.applicant_fields:
			if row.fieldname in seen:
				frappe.throw(
					_("Row #{0}: {1} is already listed in row #{2}.").format(
						row.idx, frappe.bold(row.fieldname), seen[row.fieldname]
					),
					title=_("Duplicate Field"),
				)
			seen[row.fieldname] = row.idx

			if row.fieldname not in APPLICANT_TEMPLATE_FIELDS:
				frappe.throw(
					_("Row #{0}: {1} cannot be shown to applicants. Only fields the applicant is allowed to fill in may be listed here.").format(
						row.idx, frappe.bold(row.fieldname)
					),
					title=_("Field Not Applicant-Writable"),
				)

			df = meta.get_field(row.fieldname)
			if not df:
				frappe.throw(
					_("Row #{0}: {1} is not a field on {2}.").format(
						row.idx, frappe.bold(row.fieldname), DOCTYPE
					)
				)

			# Re-stamp from live meta so the template always shows what the applicant
			# will actually see.
			row.label = df.label

			if row.fieldname in APPLICANT_READONLY_FIELDS:
				# Corrected rather than rejected: these are worth SHOWING, and the only
				# wrong part of the row is a flag HR had no reason to know was unsafe.
				if row.is_editable or row.is_required or row.lock_when_filled:
					forced_readonly.append(row.label or row.fieldname)
				row.is_editable = 0
				row.is_required = 0
				row.lock_when_filled = 0
				continue

			if row.is_required and not row.is_editable:
				frappe.throw(
					_("Row #{0} ({1}): a field cannot be Required if it is not Editable -- the applicant would have no way to fill it in.").format(
						row.idx, frappe.bold(row.label or row.fieldname)
					),
					title=_("Unsatisfiable Requirement"),
				)

			if row.lock_when_filled and row.fieldname in APPLICANT_SHOWABLE_CHILD_TABLES:
				frappe.throw(
					_("Row #{0} ({1}): Lock Once Provided applies to single values, not to a list of rows.").format(
						row.idx, frappe.bold(row.label or row.fieldname)
					),
					title=_("Not Applicable"),
				)

		if forced_readonly:
			frappe.msgprint(
				_("These fields are shown to the applicant but can never be edited by them, so their flags were cleared:")
				+ "<ul><li>"
				+ "</li><li>".join(frappe.utils.escape_html(name) for name in forced_readonly)
				+ "</li></ul>"
				+ _("Personal Email is the address the applicant's login is tied to. Change it here on the record instead."),
				title=_("Display Only"),
				indicator="blue",
			)

	def warn_about_disabled_required_rows(self) -> None:
		"""Flag rows ticked Required but left disabled.

		Legal and sometimes deliberate -- disabling is how you suspend a requirement
		without losing its configuration -- but the combination reads as a contradiction
		in the grid, so say so rather than let HR wonder why the document never appears
		on the checklist.
		"""
		suspended = [row.document_type for row in self.documents if row.is_required and not row.enabled]
		if not suspended:
			return

		frappe.msgprint(
			_("These documents are marked Required but are disabled, so they will NOT be asked for:")
			+ "<ul><li>"
			+ "</li><li>".join(frappe.utils.escape_html(name) for name in suspended)
			+ "</li></ul>"
			+ _("Tick Enabled to bring them back."),
			title=_("Requirements Suspended"),
			indicator="orange",
		)

	def validate_unique_document_types(self) -> None:
		seen: dict[str, int] = {}
		for row in self.documents:
			if row.document_type in seen:
				frappe.throw(
					_("Row #{0}: {1} is already listed in row #{2}.").format(
						row.idx, frappe.bold(row.document_type), seen[row.document_type]
					),
					title=_("Duplicate Document Type"),
				)
			seen[row.document_type] = row.idx

	def validate_single_default(self) -> None:
		"""Only one default, or the fallback would be ambiguous."""
		if not self.is_default:
			return

		existing = frappe.db.get_value(
			"Onboarding Document Template", {"is_default": 1, "name": ("!=", self.name)}, "name"
		)
		if existing:
			frappe.throw(
				_("{0} is already the default template. Clear its Default flag first.").format(
					frappe.bold(existing)
				),
				title=_("Default Already Set"),
			)

	def normalise_extensions(self) -> None:
		for row in self.documents:
			if not row.allowed_extensions:
				continue
			try:
				row.allowed_extensions = normalise_extension_list(
					row.allowed_extensions, throw=True
				)
			except InvalidFileExtensionError:
				# Re-raise with the row number, otherwise HR has to guess which of a
				# dozen rows carries the bad value.
				frappe.throw(
					_("Row #{0} ({1}): {2}").format(
						row.idx,
						row.document_type or _("no document type"),
						frappe.message_log[-1].get("message")
						if frappe.message_log
						else _("Invalid file extension."),
					),
					InvalidFileExtensionError,
					title=_("Invalid File Extension"),
				)

	def specificity_for(self, applicant) -> int | None:
		"""How well this template fits `applicant`.

		Returns the number of matched criteria, or None if the template is disqualified.
		A blank field on the template matches anything; a set field must match exactly,
		otherwise the template does not apply at all.
		"""
		score = 0
		for template_field, applicant_field in MATCH_FIELDS.items():
			wanted = self.get(template_field)
			if not wanted:
				continue
			if applicant.get(applicant_field) != wanted:
				return None
			score += 1
		return score


def get_matching_template(applicant) -> str | None:
	"""Best template for this applicant, or the default, or None.

	Most specific wins. Ties break on `is_default` then name, so the result is stable
	rather than dependent on row order.
	"""
	candidates = []
	for name in frappe.get_all(
		"Onboarding Document Template", filters={"enabled": 1}, pluck="name", order_by="name asc"
	):
		template = frappe.get_cached_doc("Onboarding Document Template", name)
		score = template.specificity_for(applicant)
		if score is not None:
			candidates.append((score, 1 if template.is_default else 0, name))

	if not candidates:
		return None

	# Highest score first, then default flag, then name for determinism.
	candidates.sort(key=lambda c: (-c[0], -c[1], c[2]))
	return candidates[0][2]
