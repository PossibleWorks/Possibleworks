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
from frappe.utils import add_days, now_datetime, today

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
		doc.submit()

		doc.reload()
		self.assertIsNone(doc.invite_expires_on)

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


def make_attachment():
	import base64

	from frappe.utils.file_manager import save_file

	png = base64.b64decode(
		"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
	)
	return save_file(
		"proof.png", png + frappe.generate_hash(length=16).encode(), None, None, is_private=1
	).file_url
