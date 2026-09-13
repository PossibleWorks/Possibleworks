# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.tests import IntegrationTestCase

from possibleworks.onboarding.constants import DEFAULT_DOCUMENT_TYPES, DOCUMENT_TYPE_DOCTYPE
from possibleworks.patches.v1_2.seed_onboarding_document_types import execute as seed

# `applies_to_company` would otherwise drag Company -> Fiscal Year -> ... into the
# test-record graph and collide with the site's real data.
IGNORE_TEST_RECORD_DEPENDENCIES = ["Company"]


class TestOnboardingDocumentType(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def test_seed_created_the_starter_set(self):
		for row in DEFAULT_DOCUMENT_TYPES:
			self.assertTrue(
				frappe.db.exists(DOCUMENT_TYPE_DOCTYPE, row["document_type_name"]),
				f"{row['document_type_name']} should have been seeded",
			)

	def test_seed_is_idempotent(self):
		before = frappe.db.count(DOCUMENT_TYPE_DOCTYPE)
		seed()
		seed()
		self.assertEqual(frappe.db.count(DOCUMENT_TYPE_DOCTYPE), before)

	def test_seed_never_clobbers_site_configuration(self):
		"""Once installed, the site owns this row -- a later migrate must not reset it."""
		name = DEFAULT_DOCUMENT_TYPES[0]["document_type_name"]
		frappe.db.set_value(DOCUMENT_TYPE_DOCTYPE, name, "allowed_extensions", "pdf")
		seed()
		self.assertEqual(
			frappe.db.get_value(DOCUMENT_TYPE_DOCTYPE, name, "allowed_extensions"), "pdf"
		)

	def test_master_carries_no_policy(self):
		"""Policy lives on templates. A stray `is_required` here would be read by
		nobody and would quietly mislead whoever set it."""
		meta = frappe.get_meta(DOCUMENT_TYPE_DOCTYPE)
		for retired in ("is_required", "allow_multiple", "applies_to_company", "display_order"):
			self.assertFalse(
				meta.has_field(retired), f"{retired} should have moved to the template"
			)

	def test_extensions_are_normalised(self):
		doc = frappe.get_doc(
			{
				"doctype": DOCUMENT_TYPE_DOCTYPE,
				"document_type_name": f"Test Type {frappe.generate_hash(length=6)}",
				"allowed_extensions": " .PDF , jpg,\n JPG ,,png ",
			}
		).insert()

		# Lower-cased, dots stripped, de-duplicated, blanks dropped.
		self.assertEqual(doc.allowed_extensions, "pdf,jpg,png")

	def test_document_type_in_use_cannot_be_deleted(self):
		"""Deleting it would leave dangling Links and silently stop the
		required-documents check from enforcing it."""
		doc_type = frappe.get_doc(
			{
				"doctype": DOCUMENT_TYPE_DOCTYPE,
				"document_type_name": f"In Use {frappe.generate_hash(length=6)}",
			}
		).insert()

		applicant = frappe.get_doc(
			{
				"doctype": "Onboarding Applicant",
				"company": frappe.db.get_value("Company", {}, "name"),
				"date_of_joining": frappe.utils.today(),
				"personal_email": f"inuse{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Doc",
			}
		)
		applicant.append(
			"documents",
			{
				"document_type": doc_type.name,
				"attachment": frappe.utils.file_manager.save_file(
					"proof.png",
					b"\x89PNG\r\n\x1a\n" + frappe.generate_hash(length=8).encode(),
					None,
					None,
					is_private=1,
				).file_url,
			},
		)
		applicant.insert()

		self.assertRaises(frappe.ValidationError, doc_type.delete)
