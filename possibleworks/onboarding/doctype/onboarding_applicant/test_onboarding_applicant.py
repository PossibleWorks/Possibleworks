# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for the Onboarding Applicant lifecycle.

NOTE ON ISOLATION: creating an `Employee` fires the Observer, and `Employee` is in
`IMMEDIATE_SEND_DOCTYPES`, whose branch calls `frappe.db.commit()`
(possibleworks/observer/observer.py:145). Left alone, that would commit test data into
the site being tested. Every test that reaches Employee creation therefore neutralises
`WorkflowEventObserver.should_process` -- which also keeps the test suite honest about
the fact that this commit exists.
"""

import base64
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today
from frappe.utils.file_manager import save_file

from possibleworks.observer.observer import WorkflowEventObserver
from possibleworks.onboarding import pending_fields
from possibleworks.onboarding.api import (
	attach_document,
	get_applicant,
	get_pending_fields,
	save_applicant,
	submit_applicant_section,
)
from possibleworks.onboarding.constants import (
	APPLICANT_SUBMITTED,
	AWAITING_APPLICANT,
	CANCELLED,
	DOCTYPE,
	DOCUMENT_TEMPLATE_DOCTYPE,
	HR_REVIEW,
	ONBOARDED,
	READY_TO_ONBOARD,
)
from possibleworks.onboarding.employee_fields import (
	EMPLOYEE_FIELD_MAP,
	OPTIONAL_EMPLOYEE_TARGETS,
	build_employee,
)
from possibleworks.onboarding.validators import (
	InvalidAadhaarError,
	InvalidFileExtensionError,
	InvalidIFSCError,
	canonical_file_type,
	normalise_pan,
	validate_aadhaar,
	validate_ifsc,
	verhoeff_check_digit,
	verhoeff_checksum,
)
from possibleworks.tests.site_fixtures import sample_value_for

VALID_PAN = "ABCPD1234E"
VALID_IFSC = "HDFC0001234"
VALID_MOBILE = "+91-9876543210"

# These tests deliberately use the site's OWN masters (Company, Gender, Employee,
# Department, ...) rather than synthetic `_Test ...` records, because the whole point
# of the feature is to adapt to whatever a given site has configured. Left to itself,
# IntegrationTestCase would recursively create test records for every Link target --
# which on a populated site collides with real data (e.g. a `_Test Fiscal Year`
# overlapping the live one).
IGNORE_TEST_RECORD_DEPENDENCIES = [
	# Reached via the `documents` child table; left in, it drags Company -> Fiscal Year
	# -> Contact -> ... into the graph. Real rows are seeded by the v1_2 patch anyway.
	"Onboarding Document Type",
	"Onboarding Document Template",
	"Company",
	"Employee",
	"Department",
	"Designation",
	"Branch",
	"Gender",
	"Salutation",
	"Employment Type",
	"Employee Grade",
	"Holiday List",
	"Shift Type",
	"User",
	"Fiscal Year",
]


def valid_aadhaar(prefix: str = "23412341234") -> str:
	"""Mint a checksum-valid Aadhaar rather than hardcoding a real one."""
	return prefix + str(verhoeff_check_digit(prefix))


# A real 1x1 PNG. It must be genuinely valid: frappe v16 parses every uploaded PDF
# through `frappe.utils.pdf.pdf_contains_js` to block embedded JavaScript, so dummy
# ".pdf" bytes raise PdfStreamError. PNG skips that path entirely.
_PNG_1PX = base64.b64decode(
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def make_attachment(is_private=1, filename="aadhaar.png"):
	# Content is varied because save_file dedupes on content_hash + is_private, so
	# identical bytes would hand back the SAME File row across tests. Trailing bytes
	# after IEND are ignored by PNG readers, so the file stays valid.
	content = _PNG_1PX + frappe.generate_hash(length=16).encode()
	return save_file(filename, content, None, None, is_private=is_private).file_url


# Tests define their OWN template rather than leaning on the site's default, so they
# stay deterministic no matter how HR reconfigures document policy.
TEMPLATE_ROWS = (
	{"document_type": "Aadhaar Card", "is_required": 1, "allow_multiple": 0, "enabled": 1},
	{"document_type": "PAN Card", "is_required": 1, "allow_multiple": 0, "enabled": 1},
	{"document_type": "Educational Certificate", "is_required": 0, "allow_multiple": 1, "enabled": 1},
	{
		"document_type": "Signed Offer Letter",
		"is_required": 0,
		"allow_multiple": 0,
		"enabled": 1,
		"allowed_extensions": "pdf",
	},
	{"document_type": "Address Proof", "is_required": 0, "allow_multiple": 0, "enabled": 1},
)


def make_template(rows=None, **overrides):
	doc = frappe.get_doc(
		{
			"doctype": DOCUMENT_TEMPLATE_DOCTYPE,
			"template_name": f"Test Template {frappe.generate_hash(length=8)}",
			"enabled": 1,
			**overrides,
		}
	)
	for row in rows if rows is not None else TEMPLATE_ROWS:
		doc.append("documents", dict(row))
	doc.insert()
	return doc


class TestOnboardingApplicant(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		cls.gender = frappe.db.get_value("Gender", {}, "name")
		# This site makes `reports_to` mandatory via a Property Setter, so tests need a
		# real manager to point at -- and it must have a user account, because
		# possibleworks.employee.sync_leave_approver_and_reports_to (an Employee
		# before_save hook) rejects a Reports To without one.
		cls.manager = frappe.db.get_value(
			"Employee", {"status": "Active", "user_id": ("is", "set")}, "name"
		)

	def setUp(self):
		frappe.set_user("Administrator")
		# See the module docstring: keeps Employee creation from committing.
		self._observer = patch.object(WorkflowEventObserver, "should_process", return_value=False)
		self._observer.start()
		self.addCleanup(self._observer.stop)
		self.template = make_template()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ------------------------------------------------------------------ #
	# Factories
	# ------------------------------------------------------------------ #

	def make_applicant(self, **overrides):
		values = {
			"doctype": DOCTYPE,
			"company": self.company,
			"date_of_joining": today(),
			"personal_email": f"applicant{frappe.generate_hash(length=6)}@example.com",
			"first_name": "Asha",
			"last_name": "Rao",
			"document_template": self.template.name,
		}
		values.update(overrides)
		doc = frappe.get_doc(values)
		doc.insert()
		return doc

	def make_ready_applicant(self, **overrides):
		"""An applicant with every field this site needs, parked at Ready to Onboard."""
		values = {
			"gender": self.gender,
			"date_of_birth": "1995-04-12",
			"cell_number": VALID_MOBILE,
			"salary_mode": "Cash",
			"employee_number": f"TST-{frappe.generate_hash(length=8).upper()}",
			"reports_to": self.manager,
		}
		values.update(overrides)
		doc = self.make_applicant(**values)
		doc.status = READY_TO_ONBOARD
		doc.save()
		return doc

	# ------------------------------------------------------------------ #
	# Creation and status
	# ------------------------------------------------------------------ #

	def test_hr_can_create_shell_record_with_minimal_fields(self):
		"""`reqd` blocks draft saves, so HR must be able to seed a record before the
		applicant has filled in anything."""
		doc = self.make_applicant()
		self.assertTrue(doc.name.startswith("HR-ONB-"))
		self.assertEqual(doc.status, AWAITING_APPLICANT)
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.applicant_name, "Asha Rao")

	def test_applicant_name_falls_back_to_email(self):
		doc = self.make_applicant(first_name=None, last_name=None)
		self.assertEqual(doc.applicant_name, doc.personal_email)

	def test_invalid_status_transition_is_blocked(self):
		doc = self.make_applicant()
		doc.status = ONBOARDED
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_valid_status_transition_is_allowed(self):
		doc = self.make_applicant()
		doc.status = HR_REVIEW
		doc.save()
		self.assertEqual(doc.status, HR_REVIEW)

	# ------------------------------------------------------------------ #
	# Identifier validation
	# ------------------------------------------------------------------ #

	def test_verhoeff_round_trip(self):
		for i in range(50):
			payload = f"2{i:010d}"
			self.assertEqual(verhoeff_checksum(payload + str(verhoeff_check_digit(payload))), 0)

	def test_aadhaar_rejects_bad_checksum(self):
		good = valid_aadhaar()
		bad = good[:-1] + str((int(good[-1]) + 1) % 10)
		self.assertTrue(validate_aadhaar(good))
		self.assertFalse(validate_aadhaar(bad))
		self.assertRaises(InvalidAadhaarError, validate_aadhaar, bad, True)

	def test_aadhaar_rejects_bad_shape(self):
		for value in ("12341234123", "1234123412346", "034123412346"):
			self.assertFalse(validate_aadhaar(value))

	def test_aadhaar_is_normalised_on_save(self):
		aadhaar = valid_aadhaar()
		spaced = f"{aadhaar[:4]} {aadhaar[4:8]} {aadhaar[8:]}"
		doc = self.make_applicant(aadhar_number=spaced)
		self.assertEqual(doc.aadhar_number, aadhaar)

	def test_invalid_aadhaar_blocks_save(self):
		self.assertRaises(InvalidAadhaarError, self.make_applicant, aadhar_number="123456789012")

	def test_ifsc_validation(self):
		self.assertTrue(validate_ifsc(VALID_IFSC))
		self.assertFalse(validate_ifsc("HDFC1001234"))  # 5th char must be 0
		self.assertRaises(InvalidIFSCError, validate_ifsc, "HDF00001234", True)

	def test_pan_is_normalised_but_not_format_validated(self):
		"""Decision: PAN carries no format check -- the holder-type character list is
		documented practice we cannot verify, and a wrong list rejects genuine PANs."""
		doc = self.make_applicant(pan_number="  abcpd1234e  ")
		self.assertEqual(doc.pan_number, VALID_PAN)

		odd = self.make_applicant(pan_number="ZZZZZ9999Z")
		self.assertEqual(odd.pan_number, "ZZZZZ9999Z")
		self.assertEqual(normalise_pan("ab-cpd 1234e"), VALID_PAN)

	# ------------------------------------------------------------------ #
	# Framework-provided validation
	# ------------------------------------------------------------------ #

	def test_phone_requires_country_code(self):
		"""`fieldtype: "Phone"` is what delivers the "mobile with country code"
		requirement -- server-side, so it covers API callers too."""
		self.assertRaises(
			frappe.InvalidPhoneNumberError, self.make_applicant, cell_number="9876543210"
		)
		doc = self.make_applicant(cell_number=VALID_MOBILE)
		self.assertEqual(doc.cell_number, VALID_MOBILE)

	def test_email_validation_is_free(self):
		self.assertRaises(
			frappe.InvalidEmailAddressError, self.make_applicant, personal_email="not-an-email"
		)

	def test_date_of_birth_cannot_be_in_future(self):
		self.assertRaises(
			frappe.ValidationError, self.make_applicant, date_of_birth=add_days(today(), 1)
		)

	def test_date_of_joining_must_follow_date_of_birth(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_applicant,
			date_of_birth=today(),
			date_of_joining=today(),
		)

	# ------------------------------------------------------------------ #
	# Work history
	# ------------------------------------------------------------------ #

	def test_work_history_dates_and_total_experience(self):
		doc = self.make_applicant()
		doc.append(
			"external_work_history",
			{"company_name": "Acme", "from_date": "2020-01-01", "to_date": "2023-01-01"},
		)
		doc.save()
		self.assertAlmostEqual(doc.total_experience_years, 3.0, places=1)
		self.assertIn("year", doc.external_work_history[0].total_experience)

	def test_work_history_rejects_inverted_dates(self):
		doc = self.make_applicant()
		doc.append(
			"external_work_history",
			{"company_name": "Acme", "from_date": "2023-01-01", "to_date": "2020-01-01"},
		)
		self.assertRaises(frappe.ValidationError, doc.save)

	# ------------------------------------------------------------------ #
	# Documents
	# ------------------------------------------------------------------ #

	def test_public_attachment_is_rejected(self):
		doc = self.make_applicant()
		doc.append(
			"documents", {"document_type": "PAN Card", "attachment": make_attachment(is_private=0)}
		)
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_private_attachment_is_accepted(self):
		doc = self.make_applicant()
		doc.append("documents", {"document_type": "PAN Card", "attachment": make_attachment()})
		doc.save()
		self.assertEqual(len(doc.documents), 1)

	def test_allow_multiple_false_blocks_second_row(self):
		doc = self.make_applicant()
		for _ in range(2):
			doc.append("documents", {"document_type": "PAN Card", "attachment": make_attachment()})
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_allow_multiple_true_permits_many_rows(self):
		doc = self.make_applicant()
		for _ in range(3):
			doc.append(
				"documents",
				{"document_type": "Educational Certificate", "attachment": make_attachment()},
			)
		doc.save()
		self.assertEqual(len(doc.documents), 3)

	def test_document_outside_the_template_is_allowed(self):
		"""A template defines what is REQUIRED, not an exhaustive whitelist. Erroring
		here would strand any record whose document type was later disabled."""
		doc = self.make_applicant()
		doc.append(
			"documents", {"document_type": "Relieving Letter", "attachment": make_attachment()}
		)
		doc.save()
		self.assertEqual(len(doc.documents), 1)

	def test_disabled_template_row_is_not_required(self):
		template = make_template(
			rows=[{"document_type": "Aadhaar Card", "is_required": 1, "enabled": 0}]
		)
		doc = self.satisfy_pending_fields(
			self.make_ready_applicant(document_template=template.name)
		)
		# The only required row is disabled, so submission is not blocked on documents.
		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	def test_extension_allowlist_is_enforced(self):
		doc = self.make_applicant()
		doc.append(
			"documents",
			{
				"document_type": "Signed Offer Letter",  # pdf only
				"attachment": make_attachment(filename="offer.png"),
			},
		)
		self.assertRaises(frappe.ValidationError, doc.save)

	def test_required_documents_block_submit(self):
		doc = self.make_ready_applicant()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("required documents", str(ctx.exception).lower())

	# ------------------------------------------------------------------ #
	# Document template
	# ------------------------------------------------------------------ #

	def test_selecting_a_template_snapshots_its_rows(self):
		doc = self.make_applicant()
		self.assertEqual(len(doc.required_documents), len(TEMPLATE_ROWS))
		self.assertEqual(
			{r.document_type for r in doc.required_documents},
			{r["document_type"] for r in TEMPLATE_ROWS},
		)

	def test_editing_a_template_does_not_change_an_existing_record(self):
		"""The whole reason the snapshot exists: someone already collecting documents
		must not have the goalposts moved under them."""
		doc = self.make_applicant()
		before = {r.document_type for r in doc.required_documents if r.is_required}

		self.template.append(
			"documents", {"document_type": "Cancelled Cheque", "is_required": 1, "enabled": 1}
		)
		self.template.save()

		doc.reload()
		doc.save()
		after = {r.document_type for r in doc.required_documents if r.is_required}
		self.assertEqual(before, after)
		self.assertNotIn("Cancelled Cheque", after)

	def test_resync_pulls_template_changes_in(self):
		doc = self.make_applicant()
		self.template.append(
			"documents", {"document_type": "Cancelled Cheque", "is_required": 1, "enabled": 1}
		)
		self.template.save()

		result = doc.resync_document_template()

		self.assertIn("Cancelled Cheque", result["added"])
		doc.reload()
		self.assertIn(
			"Cancelled Cheque", {r.document_type for r in doc.required_documents}
		)

	def test_changing_the_template_replaces_the_snapshot(self):
		other = make_template(
			rows=[{"document_type": "Cancelled Cheque", "is_required": 1, "enabled": 1}]
		)
		doc = self.make_applicant()
		doc.document_template = other.name
		doc.save()

		self.assertEqual(
			{r.document_type for r in doc.required_documents}, {"Cancelled Cheque"}
		)

	def test_submit_blocked_without_a_template(self):
		doc = self.make_ready_applicant()
		doc.db_set("document_template", None, update_modified=False)
		doc.reload()

		# Auto-default would otherwise refill it on validate; suppress it so the gate
		# itself is what gets exercised.
		with patch(
			"possibleworks.onboarding.doctype.onboarding_applicant.onboarding_applicant.get_matching_template",
			return_value=None,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.submit()

		self.assertIn("Document Template", str(ctx.exception))

	def test_required_documents_come_from_the_snapshot(self):
		doc = self.make_ready_applicant()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()

		message = str(ctx.exception)
		self.assertIn("Aadhaar Card", message)
		self.assertIn("PAN Card", message)
		# Optional rows must never appear in the blocking list.
		self.assertNotIn("Address Proof", message)

	def test_auto_default_picks_the_most_specific_template(self):
		"""Two criteria must beat one.

		`precise` scores 2 so it wins outright, rather than tying with some other
		single-criterion template on the site and being decided by the name tie-break.
		"""
		employment_type = frappe.db.get_value("Employment Type", {}, "name")
		if not employment_type:
			self.skipTest("site has no Employment Type to match on")

		broad = make_template(
			rows=[{"document_type": "PAN Card", "enabled": 1}],
			applies_to_company=self.company,
		)
		precise = make_template(
			rows=[{"document_type": "Aadhaar Card", "enabled": 1}],
			applies_to_company=self.company,
			applies_to_employment_type=employment_type,
		)

		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"company": self.company,
				"employment_type": employment_type,
				"date_of_joining": today(),
				"personal_email": f"auto{frappe.generate_hash(length=6)}@example.com",
			}
		).insert()

		self.assertEqual(doc.document_template, precise.name)
		self.assertNotEqual(doc.document_template, broad.name)

	def test_template_matching_excludes_non_matching_criteria(self):
		"""A set field that does not match disqualifies the template entirely, rather
		than just scoring lower."""
		other_company = frappe.db.get_value(
			"Company", {"name": ("!=", self.company)}, "name"
		)
		if not other_company:
			self.skipTest("site has only one Company")

		mismatched = frappe.get_doc(
			DOCUMENT_TEMPLATE_DOCTYPE,
			make_template(
				rows=[{"document_type": "PAN Card", "enabled": 1}],
				applies_to_company=other_company,
			).name,
		)
		applicant = frappe._dict(
			company=self.company, employment_type=None, designation=None
		)
		self.assertIsNone(mismatched.specificity_for(applicant))

	def test_auto_default_never_overwrites_a_manual_choice(self):
		make_template(rows=[{"document_type": "Aadhaar Card", "enabled": 1}], applies_to_company=self.company)
		doc = self.make_applicant()  # explicitly uses self.template

		doc.designation = frappe.db.get_value("Designation", {}, "name")
		doc.save()

		self.assertEqual(doc.document_template, self.template.name)

	def test_template_rejects_nonsense_file_extension(self):
		"""A format check alone would pass "j" -- it has to be a REAL extension."""
		with self.assertRaises(InvalidFileExtensionError) as ctx:
			make_template(
				rows=[
					{
						"document_type": "Address Proof",
						"enabled": 1,
						"allowed_extensions": "j",
					}
				]
			)
		# The row number matters: HR should not have to guess which row is bad.
		self.assertIn("Row #1", str(ctx.exception))

	def test_template_accepts_real_file_extensions(self):
		template = make_template(
			rows=[
				{
					"document_type": "Address Proof",
					"enabled": 1,
					"allowed_extensions": " .PDF , jpeg,\n docx ,,",
				}
			]
		)
		self.assertEqual(template.documents[0].allowed_extensions, "pdf,jpeg,docx")

	def test_document_type_rejects_nonsense_file_extension(self):
		doc_type = frappe.get_doc("Onboarding Document Type", "Address Proof")
		doc_type.allowed_extensions = "zzz"
		self.assertRaises(InvalidFileExtensionError, doc_type.save)

	def test_canonical_file_type_matches_frappe(self):
		"""jpeg and jpg both resolve to Frappe's JPG, so a site allowlist holding one
		does not spuriously reject the other."""
		self.assertEqual(canonical_file_type("jpeg"), canonical_file_type("jpg"))
		self.assertEqual(canonical_file_type("pdf"), "PDF")
		self.assertIsNone(canonical_file_type("j"))

	def test_template_rejects_duplicate_document_types(self):
		self.assertRaises(
			frappe.ValidationError,
			make_template,
			rows=[
				{"document_type": "PAN Card", "enabled": 1},
				{"document_type": "PAN Card", "enabled": 1},
			],
		)

	def test_only_one_default_template(self):
		"""A second default would make the fallback ambiguous."""
		self.assertRaises(frappe.ValidationError, make_template, is_default=1)

	# ------------------------------------------------------------------ #
	# Submit gates
	# ------------------------------------------------------------------ #

	def satisfy_documents(self, doc):
		"""Upload whatever THIS record's template snapshot requires, and capture whatever
		this SITE requires on Employee. Together these are "everything except the thing
		the calling test is actually about"."""
		for row in doc.required_documents:
			if row.enabled and row.is_required:
				doc.append(
					"documents",
					{"document_type": row.document_type, "attachment": make_attachment()},
				)
		doc.save()
		return self.satisfy_pending_fields(doc)

	def satisfy_pending_fields(self, doc):
		"""Capture whatever THIS SITE makes mandatory on Employee.

		Without this a submit test asserts against the site's Employee configuration
		rather than against our own code, and passes or fails on what somebody added to
		Employee last week. `hw-hris` has two `reqd` probation-date Custom Fields; the
		next site will have something else entirely -- which is the whole reason the
		pending-fields resolver exists rather than a hardcoded list.

		Deliberately the `blocking` bucket only. `native` fields (gender, date of birth)
		have a counterpart on the onboarding form and must be filled THERE -- capturing
		them here would paper over the very gap the missing-field tests assert on.

		Values are minted from the docfield's own type, so this keeps working when the
		set changes.
		"""
		outstanding = pending_fields.resolve(doc)["blocking"]
		if not outstanding:
			return doc

		for df in outstanding:
			value = sample_value_for(df)
			if value is None:
				# Table, Attach and friends cannot be filled generically. The resolver
				# puts those in `manual` and the test that cares asserts on them directly.
				continue
			doc.append(
				"pending_employee_fields",
				{
					"fieldname": df["fieldname"],
					"label": df.get("label"),
					"fieldtype": df.get("fieldtype"),
					"options": df.get("options"),
					"value": value,
				},
			)
		doc.save()
		return doc

	def test_submit_blocked_before_date_of_joining(self):
		doc = self.satisfy_documents(self.make_ready_applicant(date_of_joining=add_days(today(), 5)))
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("Date of Joining", str(ctx.exception))
		doc.reload()
		self.assertEqual(doc.docstatus, 0)

	def test_submit_allowed_on_date_of_joining(self):
		"""The boundary: submission is permitted ON the date, not only after it."""
		doc = self.satisfy_documents(self.make_ready_applicant(date_of_joining=today()))
		doc.submit()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.status, ONBOARDED)

	def test_submit_allowed_after_date_of_joining(self):
		doc = self.make_ready_applicant()
		doc.db_set("date_of_joining", add_days(today(), -3), update_modified=False)
		doc.reload()
		self.satisfy_documents(doc)
		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	def test_submit_requires_ready_to_onboard(self):
		doc = self.satisfy_documents(self.make_applicant())
		doc.status = HR_REVIEW
		doc.save()
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn(READY_TO_ONBOARD, str(ctx.exception))

	def test_submit_lists_every_missing_mandatory_field_at_once(self):
		"""Requirement: block and list them ALL, not one at a time."""
		doc = self.satisfy_documents(
			self.make_ready_applicant(gender=None, date_of_birth=None)
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()

		message = str(ctx.exception)
		self.assertIn("Gender", message)
		self.assertIn("Date of Birth", message)

	def test_bank_details_required_at_submit_when_salary_mode_is_bank(self):
		"""`depends_on` is client-only, so this must be enforced in Python."""
		doc = self.satisfy_documents(self.make_ready_applicant(salary_mode="Bank"))
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("Bank", str(ctx.exception))

	def test_salary_mode_starts_empty(self):
		"""Defaulting to Bank silently claimed a payment method nobody chose -- and
		dragged the bank-detail requirements along with it."""
		self.assertFalse(frappe.new_doc(DOCTYPE).salary_mode)

	def test_required_applicant_field_blocks_submit_when_hr_never_filled_it(self):
		"""The gap a template's Required flag could not close on its own.

		`passport_number` here is Required but locked to the applicant, so HR was meant
		to supply it. The applicant's own submit cannot catch that -- they were never
		shown an editable control -- so the check has to live at HR's submit.
		"""
		template = make_template()
		template.append(
			"applicant_fields",
			{"fieldname": "passport_number", "is_editable": 1, "lock_when_filled": 1, "is_required": 1},
		)
		template.save()

		doc = self.satisfy_documents(
			self.make_ready_applicant(document_template=template.name)
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("Passport Number", str(ctx.exception))

		# A refused submit still leaves docstatus=1 on the in-memory doc; only the
		# transaction rolled back. Re-read before carrying on, or the next save fails on
		# a timestamp mismatch rather than on anything this test is about.
		doc.reload()

		doc.passport_number = "Z1234567"
		doc.save()
		doc.submit()
		self.assertEqual(doc.docstatus, 1)

	# ------------------------------------------------------------------ #
	# Employee ID / HR Settings.emp_created_by
	# ------------------------------------------------------------------ #

	def test_employee_number_required_when_naming_by_employee_number(self):
		doc = self.satisfy_documents(self.make_ready_applicant(employee_number=None))
		with patch(
			"possibleworks.onboarding.doctype.onboarding_applicant.onboarding_applicant.uses_employee_number_naming",
			return_value=True,
		):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.submit()
		self.assertIn("Employee Number", str(ctx.exception))

	def test_employee_number_used_as_employee_id_when_naming_by_number(self):
		doc = self.satisfy_documents(self.make_ready_applicant())
		with patch(
			"possibleworks.onboarding.employee_fields.uses_employee_number_naming",
			return_value=True,
		):
			employee = build_employee(doc)
		self.assertEqual(employee.employee_number, doc.employee_number)

	def test_employee_number_not_written_under_naming_series(self):
		"""Under Naming Series / Full Name the Employee field is hidden and reqd:0
		upstream, so writing it would be noise."""
		doc = self.make_ready_applicant()
		with patch(
			"possibleworks.onboarding.employee_fields.uses_employee_number_naming",
			return_value=False,
		):
			employee = build_employee(doc)
		self.assertFalsy = self.assertFalse(employee.employee_number)

	# ------------------------------------------------------------------ #
	# Employee creation
	# ------------------------------------------------------------------ #

	def test_employee_created_with_mapped_fields(self):
		doc = self.satisfy_documents(
			self.make_ready_applicant(
				current_address="12 Residency Road",
				person_to_be_contacted="Meera Rao",
				emergency_phone_number=VALID_MOBILE,
			)
		)
		doc.submit()

		self.assertTrue(doc.employee)
		employee = frappe.get_doc("Employee", doc.employee)
		self.assertEqual(employee.first_name, "Asha")
		self.assertEqual(employee.last_name, "Rao")
		self.assertEqual(employee.gender, self.gender)
		self.assertEqual(str(employee.date_of_birth), "1995-04-12")
		self.assertEqual(getdate(employee.date_of_joining), getdate(doc.date_of_joining))
		self.assertEqual(employee.company, self.company)
		self.assertEqual(employee.cell_number, VALID_MOBILE)
		self.assertEqual(employee.personal_email, doc.personal_email)
		self.assertEqual(employee.current_address, "12 Residency Road")
		self.assertEqual(employee.person_to_be_contacted, "Meera Rao")

	def test_employee_status_is_active_not_onboarding_status(self):
		"""Regression guard for the mapper hazard: a blind same-fieldname copy would
		put "Ready to Onboard" into Employee.status and break its Select."""
		doc = self.satisfy_documents(self.make_ready_applicant())
		self.assertEqual(doc.status, READY_TO_ONBOARD)
		doc.submit()

		employee = frappe.get_doc("Employee", doc.employee)
		self.assertEqual(employee.status, "Active")

	def test_child_tables_copied_to_employee(self):
		doc = self.make_ready_applicant()
		doc.append(
			"education",
			{"school_univ": "IIT Madras", "qualification": "B.Tech", "year_of_passing": 2016},
		)
		doc.append(
			"external_work_history",
			{"company_name": "Acme", "designation": "Engineer", "from_date": "2020-01-01", "to_date": "2023-01-01"},
		)
		self.satisfy_documents(doc)
		doc.submit()

		employee = frappe.get_doc("Employee", doc.employee)
		self.assertEqual(len(employee.education), 1)
		self.assertEqual(employee.education[0].school_univ, "IIT Madras")
		self.assertEqual(len(employee.external_work_history), 1)
		self.assertEqual(employee.external_work_history[0].company_name, "Acme")

	def test_reverse_link_is_set_on_employee(self):
		"""Set BEFORE insert, so an orphaned Employee stays discoverable even though
		the Observer commits inside after_insert."""
		doc = self.satisfy_documents(self.make_ready_applicant())
		doc.submit()

		self.assertEqual(
			frappe.db.get_value("Employee", doc.employee, "onboarding_applicant"), doc.name
		)

	def test_aadhaar_carried_to_employee(self):
		aadhaar = valid_aadhaar()
		doc = self.satisfy_documents(self.make_ready_applicant(aadhar_number=aadhaar))
		doc.submit()
		self.assertEqual(frappe.db.get_value("Employee", doc.employee, "aadhar_number"), aadhaar)

	def test_employee_field_map_targets_all_exist(self):
		"""Catches Employee schema drift in CI rather than at runtime."""
		meta = frappe.get_meta("Employee")
		missing = [
			target
			for target in EMPLOYEE_FIELD_MAP.values()
			if not meta.has_field(target) and target not in OPTIONAL_EMPLOYEE_TARGETS
		]
		self.assertEqual(missing, [], f"EMPLOYEE_FIELD_MAP points at missing fields: {missing}")

	def test_employee_field_map_never_maps_status_or_naming_series(self):
		"""The two fields whose blind copy silently corrupts the Employee."""
		self.assertNotIn("status", EMPLOYEE_FIELD_MAP.values())
		self.assertNotIn("naming_series", EMPLOYEE_FIELD_MAP.values())

	# ------------------------------------------------------------------ #
	# Cancel and amend
	# ------------------------------------------------------------------ #

	def test_cancel_keeps_employee_and_sets_cancelled_status(self):
		doc = self.satisfy_documents(self.make_ready_applicant())
		doc.submit()
		employee = doc.employee

		doc.cancel()
		doc.reload()
		self.assertEqual(doc.docstatus, 2)
		self.assertEqual(doc.status, CANCELLED)
		self.assertTrue(frappe.db.exists("Employee", employee))
		self.assertEqual(frappe.db.get_value("Employee", employee, "status"), "Active")

	def test_amend_resets_employee_and_status(self):
		"""`no_copy` does NOT survive amend -- frappe.copy_doc defaults to
		ignore_no_copy=True -- so the controller must reset these explicitly."""
		doc = self.satisfy_documents(self.make_ready_applicant())
		doc.submit()
		doc.cancel()

		amended = frappe.copy_doc(doc)
		amended.amended_from = doc.name
		# copy_doc keeps docstatus under frappe.in_test (document.py:2109); the Desk
		# amend clears it. Note its docstring: "No_copy fields also get copied."
		amended.docstatus = 0
		amended.insert()

		self.assertIsNone(amended.employee)
		self.assertEqual(amended.status, AWAITING_APPLICANT)

	def test_resubmit_after_amend_blocked_when_employee_exists(self):
		doc = self.satisfy_documents(self.make_ready_applicant())
		doc.submit()
		employee = doc.employee
		doc.cancel()

		amended = frappe.copy_doc(doc)
		amended.amended_from = doc.name
		amended.docstatus = 0
		amended.employee_number = f"TST-{frappe.generate_hash(length=8).upper()}"
		amended.insert()
		amended.status = READY_TO_ONBOARD
		amended.save()

		with self.assertRaises(frappe.ValidationError) as ctx:
			amended.submit()
		self.assertIn(employee, str(ctx.exception))

	# ------------------------------------------------------------------ #
	# Dynamic pending fields
	# ------------------------------------------------------------------ #

	def test_pending_fields_reflect_live_meta(self):
		doc = self.make_applicant()
		result = pending_fields.describe(doc)

		self.assertFalse(result["ready"])
		outstanding = {
			df["fieldname"] for df in result["native"] + result["blocking"] + result["manual"]
		}
		# gender is reqd on stock Employee and is collected on this form.
		self.assertIn("gender", outstanding)

	def test_pending_fields_ready_once_everything_supplied(self):
		"""Supplying the captured values is part of "everything" -- on a site with its
		own mandatory Employee fields, filling only the onboarding form is not enough,
		which is the entire reason the capture table exists."""
		doc = self.satisfy_pending_fields(self.make_ready_applicant())
		self.assertTrue(pending_fields.describe(doc)["ready"])

	def test_defaults_are_not_reported_as_missing(self):
		"""The dry run inherits Employee's defaults, so `status` and `naming_series`
		must never be demanded from HR. A static fieldname diff would get this wrong."""
		doc = self.make_applicant()
		outstanding = {df["fieldname"] for df in pending_fields.get_pending_employee_fields(doc)}
		self.assertNotIn("status", outstanding)
		self.assertNotIn("naming_series", outstanding)

	def test_natively_collected_fields_are_not_duplicated_in_the_panel(self):
		"""A mandatory Employee field with a counterpart on this form points at that
		field instead of rendering a second control for the same value."""
		doc = self.make_applicant()
		result = pending_fields.describe(doc)

		native = {df["fieldname"] for df in result["native"]}
		panel = {df["fieldname"] for df in result["blocking"]}
		self.assertIn("gender", native)
		self.assertEqual(native & panel, set())
		for df in result["native"]:
			self.assertTrue(df["source_fieldname"])

	def test_site_specific_mandatory_field_is_surfaced_with_its_control_metadata(self):
		"""The core requirement: a site makes some Employee field mandatory, and it
		turns up here with the fieldtype and options needed to render a real dropdown.

		`prefered_contact_email` is used because it is NOT in EMPLOYEE_FIELD_MAP -- a
		natively-collected field would land in the `native` bucket instead.
		"""
		doc = self.make_ready_applicant()
		with patch.object(frappe.get_meta("Employee").get_field("prefered_contact_email"), "reqd", 1):
			result = pending_fields.describe(doc)
			match = next(
				(df for df in result["blocking"] if df["fieldname"] == "prefered_contact_email"),
				None,
			)

		self.assertIsNotNone(match, "a newly-mandatory Employee field should appear")
		self.assertEqual(match["fieldtype"], "Select")
		self.assertIn("Company Email", match["options"])
		# Pre-split so a non-Frappe client need not know the newline convention.
		self.assertIn("Company Email", match["options_list"])

	def test_serialised_docfield_strips_hazardous_properties(self):
		"""A falsy depends_on would render a blocking control hidden -- an invisible
		deadlock the user could never clear. `encashment_date` carries
		`depends_on: eval:doc.leave_encashed=="Yes"`, which is false here."""
		doc = self.make_ready_applicant()
		self.assertTrue(frappe.get_meta("Employee").get_field("encashment_date").depends_on)

		with patch.object(frappe.get_meta("Employee").get_field("encashment_date"), "reqd", 1):
			result = pending_fields.describe(doc)
			match = next(
				(df for df in result["blocking"] if df["fieldname"] == "encashment_date"), None
			)

		self.assertIsNotNone(match)
		self.assertNotIn("depends_on", match)
		self.assertNotIn("fetch_from", match)
		self.assertEqual(match["hidden"], 0)
		self.assertEqual(match["read_only"], 0)

	def test_captured_pending_value_is_applied_to_employee(self):
		doc = self.make_ready_applicant()
		doc.append(
			"pending_employee_fields",
			{"fieldname": "blood_group", "fieldtype": "Select", "value": "O+"},
		)
		self.satisfy_documents(doc)
		doc.submit()

		self.assertEqual(frappe.db.get_value("Employee", doc.employee, "blood_group"), "O+")

	def test_pending_row_metadata_restamped_from_live_meta(self):
		doc = self.make_ready_applicant()
		doc.append(
			"pending_employee_fields",
			{"fieldname": "blood_group", "label": "stale label", "value": "O+"},
		)
		doc.save()

		row = doc.pending_employee_fields[0]
		self.assertEqual(row.fieldtype, "Select")
		self.assertEqual(row.label, "Blood Group")
		# Not currently mandatory on this site, so it is flagged but still applied.
		self.assertEqual(row.is_stale, 1)

	def test_pending_row_for_deleted_field_is_marked_stale_and_skipped(self):
		doc = self.make_ready_applicant()
		doc.append(
			"pending_employee_fields",
			{"fieldname": "field_that_no_longer_exists", "value": "x"},
		)
		self.satisfy_documents(doc)
		doc.submit()

		self.assertEqual(doc.pending_employee_fields[0].is_stale, 1)
		self.assertTrue(doc.employee)

	# ------------------------------------------------------------------ #
	# API
	# ------------------------------------------------------------------ #

	def test_get_applicant_projection_excludes_hr_fields(self):
		doc = self.make_applicant(employee_number="SECRET-1", hr_remarks="internal note")
		payload = get_applicant(doc.name)

		self.assertEqual(payload["name"], doc.name)
		self.assertTrue(payload["editable"])
		for hidden in ("hr_remarks", "employee", "employee_number", "document_template"):
			self.assertNotIn(hidden, payload)

	def test_save_applicant_writes_allowed_fields(self):
		doc = self.make_applicant()
		save_applicant(doc.name, values={"first_name": "Nadia", "blood_group": "B+"})

		doc.reload()
		self.assertEqual(doc.first_name, "Nadia")
		self.assertEqual(doc.blood_group, "B+")

	def test_save_applicant_rejects_hr_only_field(self):
		doc = self.make_applicant()
		for field in ("date_of_joining", "status", "employee", "employee_number"):
			with self.assertRaises(frappe.PermissionError):
				save_applicant(doc.name, values={field: today()})

	def test_save_applicant_returns_advisory_missing_list(self):
		doc = self.make_applicant()
		result = save_applicant(doc.name, values={"first_name": "Nadia"})

		self.assertEqual(result["docstatus"], 0)
		self.assertTrue(result["missing"], "an incomplete form should report what is missing")

	def test_save_applicant_replaces_child_tables(self):
		doc = self.make_applicant()
		save_applicant(
			doc.name,
			education=[{"school_univ": "NIT Trichy", "qualification": "B.E."}],
			work_history=[{"company_name": "Acme", "from_date": "2020-01-01", "to_date": "2022-01-01"}],
		)
		doc.reload()
		self.assertEqual(len(doc.education), 1)
		self.assertEqual(doc.education[0].school_univ, "NIT Trichy")
		self.assertEqual(len(doc.external_work_history), 1)

	def test_submit_applicant_section_requires_declaration(self):
		doc = self.make_applicant()
		with self.assertRaises(frappe.ValidationError):
			submit_applicant_section(doc.name, declaration_accepted=False)

	def test_submit_applicant_section_lists_all_missing(self):
		doc = self.make_applicant()
		with self.assertRaises(frappe.ValidationError) as ctx:
			submit_applicant_section(doc.name, declaration_accepted=True)

		message = str(ctx.exception)
		self.assertIn("Date of Birth", message)
		self.assertIn("Gender", message)

	def test_submit_applicant_section_keeps_docstatus_zero(self):
		"""The applicant's Submit is a handoff, not a docstatus change -- the Frappe
		record stays Draft until HR submits it."""
		doc = self.make_applicant(
			gender=self.gender,
			date_of_birth="1995-04-12",
			cell_number=VALID_MOBILE,
			salary_mode="Cash",
		)
		self.satisfy_documents(doc)

		result = submit_applicant_section(doc.name, declaration_accepted=True)

		self.assertEqual(result["docstatus"], 0)
		self.assertEqual(result["status"], APPLICANT_SUBMITTED)
		doc.reload()
		self.assertTrue(doc.applicant_declaration)
		self.assertTrue(doc.applicant_submitted_on)

	def test_applicant_locked_out_after_handing_over(self):
		doc = self.make_applicant(
			gender=self.gender,
			date_of_birth="1995-04-12",
			cell_number=VALID_MOBILE,
			salary_mode="Cash",
		)
		self.satisfy_documents(doc)
		submit_applicant_section(doc.name, declaration_accepted=True)

		with self.assertRaises(frappe.PermissionError):
			save_applicant(doc.name, values={"first_name": "Changed"})

	def test_attach_document_rejects_foreign_file(self):
		doc = self.make_applicant()
		other = self.make_applicant()
		file_url = save_file(
			"x.png", _PNG_1PX + b"other", DOCTYPE, other.name, is_private=1
		).file_url

		with self.assertRaises(frappe.PermissionError):
			attach_document(doc.name, "PAN Card", file_url)

	def test_attach_document_discards_and_rejects_public_file(self):
		doc = self.make_applicant()
		file_doc = save_file("pan.png", _PNG_1PX + b"public", DOCTYPE, doc.name, is_private=0)

		with self.assertRaises(frappe.ValidationError):
			attach_document(doc.name, "PAN Card", file_doc.file_url)

		self.assertFalse(frappe.db.exists("File", file_doc.name))

	def test_attach_document_links_private_file(self):
		doc = self.make_applicant()
		file_doc = save_file("pan.png", _PNG_1PX + b"private", DOCTYPE, doc.name, is_private=1)

		result = attach_document(doc.name, "PAN Card", file_doc.file_url, remarks="front")

		doc.reload()
		self.assertEqual(len(doc.documents), 1)
		self.assertEqual(doc.documents[0].document_type, "PAN Card")
		# save_file appends a hash when the filename is already taken on this site.
		self.assertTrue(result["file_name"].startswith("pan"))

	def test_get_pending_fields_api_returns_control_metadata(self):
		doc = self.make_applicant()
		result = get_pending_fields(doc.name)

		self.assertIn("blocking", result)
		self.assertIn("native", result)
		self.assertIn("employee_preview", result)
		for df in result["native"] + result["blocking"]:
			self.assertIn("fieldtype", df)

	def test_get_pending_fields_accepts_unsaved_doc_dict(self):
		"""The Desk form passes frm.doc, which may be unsaved or dirty."""
		payload = {
			"doctype": DOCTYPE,
			"company": self.company,
			"date_of_joining": today(),
			"personal_email": "unsaved@example.com",
			"first_name": "Unsaved",
		}
		result = get_pending_fields(payload)
		self.assertFalse(result["ready"])

	# ------------------------------------------------------------------ #
	# Decisions encoded as executable guards
	# ------------------------------------------------------------------ #

	def test_observer_skips_onboarding_applicant(self):
		"""Decision: pull-only. If someone later adds this doctype to
		ALWAYS_OBSERVED_DOCTYPES, this fails and forces the conversation."""
		self._observer.stop()
		try:
			self.assertFalse(WorkflowEventObserver.should_process(DOCTYPE))
		finally:
			self._observer.start()
