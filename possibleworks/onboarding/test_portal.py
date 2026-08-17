# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Applicant portal: field rules, invite links, and access scoping.

The access assertions here are the point of the whole feature -- an applicant is an
outsider with an account on your system, so "they can only ever reach their own record"
has to be enforced by tests rather than by reading the code and hoping.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, format_datetime, now_datetime, today

from possibleworks.observer.observer import WorkflowEventObserver
from possibleworks.onboarding import portal
from possibleworks.onboarding.constants import (
	APPLICANT_SUBMITTED,
	AWAITING_APPLICANT,
	DOCTYPE,
	DOCUMENT_TEMPLATE_DOCTYPE,
	PORTAL_ROLE,
	READY_TO_ONBOARD,
)
from possibleworks.onboarding.validators import normalise_phone, verhoeff_check_digit
from possibleworks.tests.site_fixtures import sample_value_for
from possibleworks.utils.branded_email import BRAND_LOGO_URL, render_branded_email
from possibleworks.www import onboarding as onboarding_page


def _aadhaar(prefix: str) -> str:
	"""Minted rather than hardcoded, so these stay checksum-valid by construction."""
	return prefix + str(verhoeff_check_digit(prefix))


# A pair, because "did the value change?" needs two values that both pass validation.
VALID_AADHAAR = _aadhaar("23412341234")
OTHER_AADHAAR = _aadhaar("29876543210")

# No IGNORE_TEST_RECORD_DEPENDENCIES here: that is only honoured for tests inside a
# doctype folder. A module-level test has no `cls.doctype`, so IntegrationTestCase
# builds no test-record graph at all -- which is exactly what we want on a populated
# site. These tests use the site's own masters.

FIELD_ROWS = (
	{"fieldname": "first_name", "is_editable": 0, "is_required": 0},
	{"fieldname": "gender", "is_editable": 1, "is_required": 1},
	{"fieldname": "blood_group", "is_editable": 1, "is_required": 0},
)


class TestOnboardingPortal(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")
		cls.gender = frappe.db.get_value("Gender", {}, "name")

	def setUp(self):
		frappe.set_user("Administrator")
		self._observer = patch.object(WorkflowEventObserver, "should_process", return_value=False)
		self._observer.start()
		self.addCleanup(self._observer.stop)
		self.template = self.make_template()

	def tearDown(self):
		frappe.set_user("Administrator")

	# ------------------------------------------------------------------ #
	# Factories
	# ------------------------------------------------------------------ #

	def make_template(self, field_rows=FIELD_ROWS, doc_rows=None):
		doc = frappe.get_doc(
			{
				"doctype": DOCUMENT_TEMPLATE_DOCTYPE,
				"template_name": f"Portal Tpl {frappe.generate_hash(length=8)}",
				"enabled": 1,
			}
		)
		for row in doc_rows if doc_rows is not None else [
			{"document_type": "PAN Card", "is_required": 1, "enabled": 1}
		]:
			doc.append("documents", dict(row))
		for row in field_rows:
			doc.append("applicant_fields", dict(row))
		doc.insert()
		return doc

	def make_applicant(self, **overrides):
		values = {
			"doctype": DOCTYPE,
			"company": self.company,
			"date_of_joining": add_days(today(), 10),
			"personal_email": f"portal{frappe.generate_hash(length=8)}@example.com",
			"first_name": "Priya",
			"last_name": "Nair",
			"document_template": self.template.name,
		}
		values.update(overrides)
		doc = frappe.get_doc(values)
		doc.insert()
		return doc

	def invited(self, **overrides):
		doc = self.make_applicant(**overrides)
		portal.invite_applicant(doc.name)
		doc.reload()
		return doc

	# ------------------------------------------------------------------ #
	# Field rules and snapshot
	# ------------------------------------------------------------------ #

	def test_field_rules_are_snapshotted_from_the_template(self):
		doc = self.make_applicant()
		self.assertEqual(
			{r.fieldname for r in doc.applicant_fields},
			{r["fieldname"] for r in FIELD_ROWS},
		)

	def test_editing_the_template_does_not_change_an_existing_record(self):
		doc = self.make_applicant()
		before = {r.fieldname for r in doc.applicant_fields}

		self.template.append("applicant_fields", {"fieldname": "pan_number", "is_editable": 1})
		self.template.save()

		doc.reload()
		doc.save()
		self.assertEqual({r.fieldname for r in doc.applicant_fields}, before)

	def test_template_rejects_a_field_the_applicant_may_not_write(self):
		"""Offering an HR-only field would build a form that can be filled but never
		saved, because the mass-assignment allowlist rejects it."""
		self.assertRaises(
			frappe.ValidationError,
			self.make_template,
			field_rows=[{"fieldname": "date_of_joining", "is_editable": 1}],
		)

	def test_required_but_not_editable_is_rejected(self):
		self.assertRaises(
			frappe.ValidationError,
			self.make_template,
			field_rows=[{"fieldname": "gender", "is_editable": 0, "is_required": 1}],
		)

	def test_field_label_is_restamped_from_live_meta(self):
		template = self.make_template(field_rows=[{"fieldname": "gender", "is_editable": 1}])
		self.assertEqual(template.applicant_fields[0].label, "Gender")

	# ------------------------------------------------------------------ #
	# Invite
	# ------------------------------------------------------------------ #

	def test_invite_creates_a_website_user_with_no_desk_access(self):
		doc = self.invited()

		user = frappe.get_doc("User", doc.applicant_user)
		self.assertEqual(user.user_type, "Website User")
		self.assertIn(PORTAL_ROLE, [r.role for r in user.roles])
		self.assertFalse(
			frappe.db.get_value("Role", PORTAL_ROLE, "desk_access"),
			"the portal role must never grant Desk access",
		)

	def test_invite_expiry_never_outlives_the_joining_date(self):
		doc = self.invited(date_of_joining=add_days(today(), 3))
		self.assertLessEqual(
			frappe.utils.get_datetime(doc.invite_expires_on),
			frappe.utils.get_datetime(frappe.utils.getdate(doc.date_of_joining)),
		)

	def test_reissuing_an_invite_retires_the_previous_link(self):
		doc = self.invited()
		first = portal.build_invite_url(doc.name, doc.invite_expires_on)

		doc.db_set("invite_expires_on", add_days(now_datetime(), 5), update_modified=False)
		doc.reload()
		second = portal.build_invite_url(doc.name, doc.invite_expires_on)

		self.assertNotEqual(first, second)

	def test_invite_refused_once_the_applicant_has_handed_over(self):
		doc = self.invited()
		doc.db_set("status", APPLICANT_SUBMITTED, update_modified=False)
		doc.reload()

		self.assertRaises(frappe.ValidationError, portal.invite_applicant, doc.name)

	def test_invite_refuses_to_reuse_a_system_user_account(self):
		"""Never quietly turn a staff account into an applicant login."""
		doc = self.make_applicant(personal_email="Administrator")
		with self.assertRaises(frappe.ValidationError):
			portal.invite_applicant(doc.name)

	def test_submitting_kills_the_invite(self):
		doc = self.invited(date_of_joining=today())
		doc.append("documents", {"document_type": "PAN Card", "attachment": make_attachment()})
		doc.gender = self.gender
		doc.date_of_birth = "1995-04-12"
		doc.cell_number = "+91-9876543210"
		doc.salary_mode = "Cash"
		doc.employee_number = f"PT-{frappe.generate_hash(length=8).upper()}"
		doc.reports_to = frappe.db.get_value(
			"Employee", {"status": "Active", "user_id": ("is", "set")}, "name"
		)
		doc.status = READY_TO_ONBOARD
		doc.save()
		capture_site_mandatory_fields(doc)
		doc.submit()

		doc.reload()
		self.assertIsNone(doc.invite_expires_on)

	# ------------------------------------------------------------------ #
	# Invite email body
	# ------------------------------------------------------------------ #

	def test_invite_email_carries_the_signed_link_and_a_readable_expiry(self):
		doc = self.invited()
		link = portal.build_invite_url(doc.name, doc.invite_expires_on)

		body = portal.build_invite_email(doc, link)

		self.assertIn(f'href="{link}"', body, "the call to action must point at the signed link")
		self.assertIn("Open my onboarding form", body)
		self.assertIn(doc.first_name, body)
		self.assertIn(format_datetime(doc.invite_expires_on), body)
		self.assertNotIn(
			str(doc.invite_expires_on), body, "the raw database timestamp should never be shown"
		)

	def test_invite_email_is_branded_and_survives_frappes_paragraph_wrapper(self):
		"""sendmail drops `message` into <p>{{ content }}</p>, so no document tags."""
		doc = self.invited()

		body = portal.build_invite_email(doc, portal.build_invite_url(doc.name, doc.invite_expires_on))

		self.assertIn(BRAND_LOGO_URL, body)
		self.assertIn("#eaf1fe", body, "the branded card background is the theme's tell")
		for tag in ("<html", "<body", "<head", "<!DOCTYPE"):
			self.assertNotIn(tag, body, f"{tag} cannot be nested inside a paragraph")

	def test_branded_shell_escapes_text_it_is_given(self):
		"""Frappe's Jinja runs with autoescape off and applicants supply their own names."""
		body = render_branded_email(heading="Dear <script>alert(1)</script>,")

		self.assertNotIn("<script>", body)
		self.assertIn("&lt;script&gt;", body)

	# ------------------------------------------------------------------ #
	# Access scoping -- the part that matters
	# ------------------------------------------------------------------ #

	def test_applicant_sees_only_their_own_record(self):
		mine = self.invited()
		theirs = self.invited()

		frappe.set_user(mine.applicant_user)
		try:
			visible = frappe.get_list(DOCTYPE, pluck="name")
			self.assertEqual(visible, [mine.name])
			self.assertNotIn(theirs.name, visible)
		finally:
			frappe.set_user("Administrator")

	def test_applicant_cannot_read_another_record_directly(self):
		mine = self.invited()
		theirs = self.invited()

		frappe.set_user(mine.applicant_user)
		try:
			frappe.get_doc(DOCTYPE, mine.name).check_permission("read")
			self.assertRaises(
				frappe.PermissionError,
				frappe.get_doc(DOCTYPE, theirs.name).check_permission,
				"read",
			)
		finally:
			frappe.set_user("Administrator")

	def test_applicant_may_change_only_editable_fields(self):
		doc = self.invited()

		frappe.set_user(doc.applicant_user)
		try:
			mine = frappe.get_doc(DOCTYPE, doc.name)
			mine.blood_group = "O+"
			mine.save()
			self.assertEqual(mine.blood_group, "O+")

			mine.first_name = "Hacked"  # in the template, but not editable
			self.assertRaises(frappe.PermissionError, mine.save)
		finally:
			frappe.set_user("Administrator")

	def test_applicant_cannot_change_hr_fields(self):
		doc = self.invited()

		frappe.set_user(doc.applicant_user)
		try:
			for fieldname, value in (
				("date_of_joining", add_days(today(), 60)),
				("employee_number", "SNEAKY-1"),
				("document_template", None),
			):
				mine = frappe.get_doc(DOCTYPE, doc.name)
				mine.set(fieldname, value)
				self.assertRaises(frappe.PermissionError, mine.save)
		finally:
			frappe.set_user("Administrator")

	def test_applicant_cannot_submit_the_document(self):
		doc = self.invited()

		frappe.set_user(doc.applicant_user)
		try:
			mine = frappe.get_doc(DOCTYPE, doc.name)
			self.assertRaises(frappe.PermissionError, mine.submit)
		finally:
			frappe.set_user("Administrator")

	# ------------------------------------------------------------------ #
	# Portal actions
	# ------------------------------------------------------------------ #

	def test_portal_save_writes_editable_and_ignores_the_rest(self):
		doc = self.invited()

		frappe.set_user(doc.applicant_user)
		try:
			result = portal.portal_save(
				values={"blood_group": "B+", "first_name": "Hacked", "date_of_joining": today()}
			)
			self.assertIn("Gender", result["missing"])
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.blood_group, "B+")
		# Silently ignored rather than throwing: a non-editable field is simply not
		# part of this applicant's form.
		self.assertEqual(doc.first_name, "Priya")

	def test_portal_submit_requires_the_declaration(self):
		doc = self.invited()
		frappe.set_user(doc.applicant_user)
		try:
			self.assertRaises(frappe.ValidationError, portal.portal_submit, {}, False)
		finally:
			frappe.set_user("Administrator")

	def test_portal_submit_lists_everything_outstanding(self):
		doc = self.invited()
		frappe.set_user(doc.applicant_user)
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				portal.portal_submit(values={}, declaration_accepted=True)
			message = str(ctx.exception)
			self.assertIn("Gender", message)
			self.assertIn("PAN Card", message)
		finally:
			frappe.set_user("Administrator")

	def test_portal_submit_keeps_docstatus_zero_and_locks_the_applicant_out(self):
		doc = self.invited()
		doc.append("documents", {"document_type": "PAN Card", "attachment": make_attachment()})
		doc.save()

		frappe.set_user(doc.applicant_user)
		try:
			result = portal.portal_submit(
				values={"gender": self.gender}, declaration_accepted=True
			)
			self.assertEqual(result["docstatus"], 0)
			self.assertEqual(result["status"], APPLICANT_SUBMITTED)

			# Handed over -- no further applicant edits.
			self.assertRaises(frappe.PermissionError, portal.portal_save, {"blood_group": "A+"})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertTrue(doc.applicant_declaration)
		self.assertTrue(doc.declaration_accepted_on)

	def test_portal_attach_rejects_a_document_outside_the_checklist(self):
		doc = self.invited()
		frappe.set_user(doc.applicant_user)
		try:
			self.assertRaises(
				frappe.PermissionError,
				portal.portal_attach,
				"Signed Offer Letter",
				make_attachment(),
			)
		finally:
			frappe.set_user("Administrator")

	def test_portal_actions_refuse_without_a_record(self):
		"""A portal account with no record of its own gets nothing."""
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"stray{frappe.generate_hash(length=8)}@example.com",
				"first_name": "Stray",
				"user_type": "Website User",
				"send_welcome_email": 0,
				"roles": [{"role": PORTAL_ROLE}],
			}
		).insert(ignore_permissions=True)

		frappe.set_user(user.name)
		try:
			self.assertRaises(frappe.PermissionError, portal.portal_save, {"blood_group": "A+"})
		finally:
			frappe.set_user("Administrator")

	# ------------------------------------------------------------------ #
	# Personal Email is identity, not data
	# ------------------------------------------------------------------ #

	def test_personal_email_cannot_be_made_editable_by_a_template(self):
		"""Display-only, and corrected rather than rejected -- showing an applicant the
		address on file is useful, letting them change it is not."""
		template = self.make_template(
			field_rows=[{"fieldname": "personal_email", "is_editable": 1, "is_required": 1}]
		)
		row = template.applicant_fields[0]
		self.assertFalse(row.is_editable)
		self.assertFalse(row.is_required)

	def test_applicant_cannot_change_their_personal_email(self):
		"""`applicant_user` is written once, at invite, and nothing re-syncs it -- so an
		edit here would silently detach the record from the login it is keyed on."""
		doc = self.invited(
			document_template=self.make_template(
				field_rows=[{"fieldname": "personal_email", "is_editable": 1}]
			).name
		)
		original = doc.personal_email

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({"personal_email": "someone.else@example.com"})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.personal_email, original)
		self.assertEqual(doc.applicant_user, original)

	# ------------------------------------------------------------------ #
	# Lock Once Provided
	# ------------------------------------------------------------------ #

	def locking_template(self, fieldname="aadhar_number"):
		return self.make_template(
			field_rows=[{"fieldname": fieldname, "is_editable": 1, "lock_when_filled": 1}]
		)

	def test_lock_when_filled_still_accepts_the_first_value(self):
		"""The regression this design is most likely to cause: resolving editability
		against the incoming doc would see the value just typed and reject the very save
		that supplied it."""
		doc = self.invited(document_template=self.locking_template().name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({"aadhar_number": VALID_AADHAAR})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.aadhar_number, VALID_AADHAAR)

	def test_lock_when_filled_protects_a_value_hr_prefilled(self):
		doc = self.invited(
			document_template=self.locking_template().name, aadhar_number=VALID_AADHAAR
		)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({"aadhar_number": OTHER_AADHAAR})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.aadhar_number, VALID_AADHAAR)

	def test_lock_when_filled_closes_once_the_applicant_supplies_it(self):
		doc = self.invited(document_template=self.locking_template().name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({"aadhar_number": VALID_AADHAAR})
			portal.portal_save({"aadhar_number": OTHER_AADHAAR})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.aadhar_number, VALID_AADHAAR)

	def test_plain_editable_field_stays_overwritable(self):
		"""Without lock_when_filled the old behaviour is unchanged."""
		doc = self.invited(
			document_template=self.make_template(
				field_rows=[{"fieldname": "aadhar_number", "is_editable": 1}]
			).name,
			aadhar_number=VALID_AADHAAR,
		)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({"aadhar_number": OTHER_AADHAAR})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.aadhar_number, OTHER_AADHAAR)

	# ------------------------------------------------------------------ #
	# Repeating tables
	# ------------------------------------------------------------------ #

	def table_template(self, fieldname="education"):
		return self.make_template(field_rows=[{"fieldname": fieldname, "is_editable": 1}])

	def test_lock_when_filled_is_rejected_on_a_table(self):
		"""It compares one stored value; a list of rows has none."""
		self.assertRaises(
			frappe.ValidationError,
			self.make_template,
			field_rows=[{"fieldname": "education", "is_editable": 1, "lock_when_filled": 1}],
		)

	def test_applicant_can_fill_a_repeating_table(self):
		doc = self.invited(document_template=self.table_template().name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save(
				{},
				tables={
					"education": [
						{"school_univ": "Anna University", "qualification": "B.E.", "year_of_passing": 2016},
						{"school_univ": "IIM Bangalore", "qualification": "MBA", "year_of_passing": 2020},
					]
				},
			)
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual([r.qualification for r in doc.education], ["B.E.", "MBA"])

	def test_repeating_table_rows_are_replaced_not_merged(self):
		"""The page posts the whole list it is showing, so a deleted row has to vanish."""
		doc = self.invited(document_template=self.table_template().name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({}, tables={"education": [{"qualification": "B.E."}, {"qualification": "MBA"}]})
			portal.portal_save({}, tables={"education": [{"qualification": "MBA"}]})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual([r.qualification for r in doc.education], ["MBA"])

	def test_blank_repeating_rows_are_dropped(self):
		"""Clicking Add and then Save should not fail on the child table's own rules."""
		doc = self.invited(document_template=self.table_template().name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({}, tables={"education": [{"qualification": "B.E."}, {}, {"school_univ": ""}]})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.education), 1)

	def test_an_untouched_added_row_is_dropped(self):
		"""What the page actually posts for a row nobody filled in.

		Every rendered column is sent, and an unticked checkbox sends 0 -- so the row is
		present-but-blank rather than absent. Judging emptiness on presence would save a
		nameless job here.
		"""
		doc = self.invited(document_template=self.table_template("external_work_history").name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save(
				{},
				tables={
					"external_work_history": [
						{
							"company_name": "",
							"designation": "",
							"from_date": "",
							"to_date": "",
							"is_current_employer": 0,
							"reason_for_leaving": "",
						}
					]
				},
			)
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.external_work_history), 0)

	def test_current_employer_cannot_also_have_a_leaving_date(self):
		"""The contradiction used to be resolved by silently discarding the To Date --
		which also skipped the inverted-date check, so a backwards pair saved cleanly as
		long as the box was ticked."""
		doc = self.invited(document_template=self.table_template("external_work_history").name)

		frappe.set_user(doc.applicant_user)
		try:
			with self.assertRaises(frappe.ValidationError) as ctx:
				portal.portal_save(
					{},
					tables={
						"external_work_history": [
							{
								"company_name": "Garden",
								"from_date": "2022-08-03",
								"to_date": "2022-08-01",
								"is_current_employer": 1,
							}
						]
					},
				)
			self.assertIn("current employer", str(ctx.exception).lower())
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.external_work_history), 0)

	def test_current_employer_without_a_leaving_date_counts_to_today(self):
		doc = self.invited(document_template=self.table_template("external_work_history").name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save(
				{},
				tables={
					"external_work_history": [
						{
							"company_name": "Garden",
							"from_date": add_days(today(), -365),
							"is_current_employer": 1,
						}
					]
				},
			)
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertAlmostEqual(doc.total_experience_years, 1.0, places=1)

	def test_table_not_offered_by_the_template_is_ignored(self):
		doc = self.invited()  # FIELD_ROWS offers no tables

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save({}, tables={"education": [{"qualification": "B.E."}]})
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.education), 0)

	def test_only_the_rendered_columns_are_read_from_a_row(self):
		"""`total_experience` is derived by the child controller; accepting it from the
		applicant would let them contradict the computed value."""
		doc = self.invited(document_template=self.table_template("external_work_history").name)

		frappe.set_user(doc.applicant_user)
		try:
			portal.portal_save(
				{},
				tables={
					"external_work_history": [
						{
							"company_name": "Acme",
							"from_date": add_days(today(), -400),
							"to_date": add_days(today(), -40),
							"total_experience": "99 years",
						}
					]
				},
			)
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(doc.external_work_history[0].company_name, "Acme")
		self.assertNotIn("99", doc.external_work_history[0].total_experience or "")

	# ------------------------------------------------------------------ #
	# Duplicate uploads
	# ------------------------------------------------------------------ #

	def multi_upload_applicant(self):
		return self.invited(
			document_template=self.make_template(
				doc_rows=[
					{"document_type": "Educational Certificate", "is_required": 0, "allow_multiple": 1, "enabled": 1}
				]
			).name
		)

	def test_two_files_with_the_same_name_are_rejected(self):
		"""Different content, same name.

		Frappe stores the second as `degree<hash>.png` rather than letting it collide,
		so both save happily and HR is left with two rows whose only difference is six
		random characters. The check compares the name the APPLICANT used, which is the
		one they can actually act on.
		"""
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			upload(doc, "Educational Certificate", "degree.png")
			with self.assertRaises(frappe.ValidationError) as ctx:
				upload(doc, "Educational Certificate", "degree.png")
			self.assertIn("degree.png", str(ctx.exception))
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.documents), 1)

	def test_the_name_is_compared_case_insensitively(self):
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			upload(doc, "Educational Certificate", "Degree.PNG")
			self.assertRaises(
				frappe.ValidationError, upload, doc, "Educational Certificate", "degree.png"
			)
		finally:
			frappe.set_user("Administrator")

	def test_the_same_file_uploaded_twice_is_rejected(self):
		"""Identical content dedupes on content_hash, so save_file hands back the File
		already attached -- and two rows would end up sharing one url, where removing
		either orphans the other."""
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			upload(doc, "Educational Certificate", "degree.png", b"identical-bytes")
			self.assertRaises(
				frappe.ValidationError,
				upload,
				doc,
				"Educational Certificate",
				"degree-copy.png",
				b"identical-bytes",
			)
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.documents), 1)

	def test_rejecting_a_duplicate_never_deletes_the_file_already_in_use(self):
		"""Content-hash dedup can hand back the EXISTING File, and cleaning that up
		would destroy the attachment of the row already holding it."""
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			url = attach_to(doc, "degree.png", b"identical-bytes")
			portal.portal_attach("Educational Certificate", url, "degree.png")
			self.assertRaises(
				frappe.ValidationError,
				portal.portal_attach,
				"Educational Certificate",
				url,
				"degree.png",
			)
		finally:
			frappe.set_user("Administrator")

		self.assertTrue(frappe.db.exists("File", {"file_url": url}))
		doc.reload()
		self.assertEqual(doc.documents[0].attachment, url)

	def test_distinct_filenames_are_accepted_for_a_multi_document(self):
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			state = upload(doc, "Educational Certificate", "tenth.png")
			upload(doc, "Educational Certificate", "twelfth.png")
		finally:
			frappe.set_user("Administrator")

		# The response has to carry enough to redraw the card, or the page is back to
		# reloading itself and losing whatever was typed.
		self.assertEqual(state["file"]["file_name"], "tenth.png")
		self.assertIn("progress", state)
		self.assertTrue(state["file"]["row_name"])

		doc.reload()
		self.assertEqual(len(doc.documents), 2)
		self.assertEqual(
			[r.original_file_name for r in doc.documents], ["tenth.png", "twelfth.png"]
		)

	def test_one_file_cannot_stand_in_for_two_requirements(self):
		"""The corruption worth blocking, and the reason the url check is record-wide.

		`validate_required_documents` counts rows, so one PDF attached as both Aadhaar
		Card and PAN Card makes the record read as complete when only one document was
		ever supplied.
		"""
		doc = self.invited(
			document_template=self.make_template(
				doc_rows=[
					{"document_type": "Aadhaar Card", "is_required": 1, "enabled": 1},
					{"document_type": "PAN Card", "is_required": 1, "enabled": 1},
				]
			).name
		)
		frappe.set_user(doc.applicant_user)
		try:
			url = attach_to(doc, "both-ids.png", b"one-scan-of-two-cards")
			portal.portal_attach("Aadhaar Card", url, "both-ids.png")
			with self.assertRaises(frappe.ValidationError) as ctx:
				portal.portal_attach("PAN Card", url, "both-ids.png")
			self.assertIn("Aadhaar Card", str(ctx.exception))
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.documents), 1)

	def test_the_same_name_under_a_different_document_type_is_fine(self):
		doc = self.invited(
			document_template=self.make_template(
				doc_rows=[
					{"document_type": "PAN Card", "is_required": 0, "enabled": 1},
					{"document_type": "Address Proof", "is_required": 0, "enabled": 1},
				]
			).name
		)
		frappe.set_user(doc.applicant_user)
		try:
			upload(doc, "PAN Card", "scan.png")
			upload(doc, "Address Proof", "scan.png")
		finally:
			frappe.set_user("Administrator")

		doc.reload()
		self.assertEqual(len(doc.documents), 2)

	def test_removing_a_document_reports_the_new_state(self):
		"""The page patches itself from this instead of reloading, which is what used to
		throw away everything typed since the last save."""
		doc = self.multi_upload_applicant()
		frappe.set_user(doc.applicant_user)
		try:
			state = upload(doc, "Educational Certificate", "tenth.png")
			removed = portal.portal_remove_document(state["file"]["row_name"])
		finally:
			frappe.set_user("Administrator")

		self.assertEqual(removed["document_type"], "Educational Certificate")
		self.assertIn("progress", removed)

		doc.reload()
		self.assertEqual(len(doc.documents), 0)


class TestPhoneRendering(IntegrationTestCase):
	"""The portal renders Phone itself, so the pieces it depends on are tested here.

	Before this, a Phone field fell through to a plain text box, and the server then
	rejected the value for having no country code -- a field that could never be filled.
	"""

	def test_country_codes_are_available_to_the_page(self):
		countries = onboarding_page.phone_countries()
		by_name = {c["name"]: c["isd"] for c in countries}
		self.assertEqual(by_name["India"], "+91")
		self.assertEqual(by_name["United States"], "+1")

	def test_longest_dialling_code_wins(self):
		"""`+1` must not claim a `+91` number."""
		isds = ["+1", "+91", "+44"]
		self.assertEqual(
			onboarding_page.split_phone("+91 9876543210", isds), ("+91", "9876543210")
		)
		self.assertEqual(onboarding_page.split_phone("+1 4155550123", isds), ("+1", "4155550123"))

	def test_a_number_with_no_code_keeps_its_digits(self):
		self.assertEqual(onboarding_page.split_phone("9876543210", ["+91"]), ("", "9876543210"))

	def test_blank_stays_blank(self):
		self.assertEqual(onboarding_page.split_phone("", ["+91"]), ("", ""))
		self.assertEqual(onboarding_page.split_phone(None, ["+91"]), ("", ""))

	def test_stored_format_is_the_one_the_desk_can_read_back(self):
		"""A hyphen and exactly two parts.

		ControlPhone.set_formatted_input splits on "-" and re-prepends the dialling code
		when it does not get two parts -- so anything else comes back to HR as
		"+91-+91 9876543210".
		"""
		for raw in ("+91 9949596900", "+91-9949596900", "+91 99495 96900", "  +91 99495-96900  "):
			with self.subTest(raw=raw):
				stored = normalise_phone(raw)
				self.assertEqual(stored, "+91-9949596900")
				self.assertEqual(len(stored.split("-")), 2)

	def test_a_number_written_with_its_own_hyphen_is_still_two_parts(self):
		"""`98765-43210` would otherwise make three parts and trip the same branch."""
		self.assertEqual(normalise_phone("+91 98765-43210"), "+91-9876543210")

	def test_a_number_with_no_dialling_code_is_left_alone(self):
		"""Guessing a country is worse than the server's own "Country Code Required"."""
		self.assertEqual(normalise_phone("9949596900"), "9949596900")
		self.assertEqual(normalise_phone(""), "")
		self.assertEqual(normalise_phone(None), "")

	def test_longer_dialling_codes_survive(self):
		self.assertEqual(normalise_phone("+971 501234567"), "+971-501234567")
		self.assertEqual(normalise_phone("+1 415 555 0123"), "+1-4155550123")


class TestPhoneRoundTrip(IntegrationTestCase):
	"""Portal write -> storage -> portal read, in one piece.

	The bug this guards was invisible on the portal: the value the applicant typed came
	back correctly there, and only the Desk showed "+91-+91 ...".
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = frappe.db.get_value("Company", {}, "name")

	def test_a_portal_write_survives_a_desk_read(self):
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"company": self.company,
				"date_of_joining": add_days(today(), 10),
				"personal_email": f"phone{frappe.generate_hash(length=8)}@example.com",
				"first_name": "Ravi",
				# What the portal posts.
				"cell_number": "+91-9949596900",
			}
		)
		doc.insert()
		self.addCleanup(lambda: frappe.delete_doc(DOCTYPE, doc.name, force=True))

		doc.reload()
		self.assertEqual(doc.cell_number, "+91-9949596900")
		self.assertEqual(len(doc.cell_number.split("-")), 2)

	def test_a_space_separated_value_is_repaired_on_save(self):
		"""Covers the Desk and the integration API, not just the portal."""
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"company": self.company,
				"date_of_joining": add_days(today(), 10),
				"personal_email": f"phone{frappe.generate_hash(length=8)}@example.com",
				"first_name": "Ravi",
				"cell_number": "+91 9949596900",
			}
		)
		doc.insert()
		self.addCleanup(lambda: frappe.delete_doc(DOCTYPE, doc.name, force=True))

		self.assertEqual(doc.cell_number, "+91-9949596900")

	def test_the_portal_splits_the_stored_value_back_apart(self):
		countries = onboarding_page.phone_countries()
		isds = [c["isd"] for c in countries]
		self.assertEqual(
			onboarding_page.split_phone("+91-9949596900", isds), ("+91", "9949596900")
		)


def capture_site_mandatory_fields(doc):
	"""Fill whatever THIS site makes mandatory on Employee, so a submit test asserts on
	our code rather than on somebody's Employee customisation.

	`hw-hris` has two `reqd` probation-date Custom Fields; without this the test passes
	or fails depending on which site it runs against -- and, because meta is cached
	per process, on what ran before it.
	"""
	from possibleworks.onboarding import pending_fields

	blocking = pending_fields.resolve(doc)["blocking"]
	if not blocking:
		return doc

	for df in blocking:
		value = sample_value_for(df)
		if value is None:
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


def make_attachment():
	import base64

	from frappe.utils.file_manager import save_file

	png = base64.b64decode(
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
	)
	return save_file(
		"proof.png", png + frappe.generate_hash(length=16).encode(), None, None, is_private=1
	).file_url


PNG_HEADER = (
	b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
)


def attach_to(doc, file_name: str, body: bytes | None = None) -> str:
	"""A private File genuinely attached to `doc`, which portal_attach requires.

	`body` defaults to unique content, so a test only gets content-hash deduping when it
	asks for it by passing the same bytes twice.
	"""
	from frappe.utils.file_manager import save_file

	content = PNG_HEADER + (body if body is not None else frappe.generate_hash(length=24).encode())
	return save_file(file_name, content, DOCTYPE, doc.name, is_private=1).file_url


def upload(doc, document_type: str, file_name: str, body: bytes | None = None) -> dict:
	"""Upload the way the portal does -- including the applicant's own filename.

	Passing it matters: Frappe renames a colliding file on disk, so by the time
	portal_attach sees the File its `file_name` may already be `degree1a2b3c.png`. The
	page sends `file.name` for exactly this reason.
	"""
	return portal.portal_attach(document_type, attach_to(doc, file_name, body), file_name)
