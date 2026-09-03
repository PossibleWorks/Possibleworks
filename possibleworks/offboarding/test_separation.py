# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for the exit checklist created when a resignation is approved.

NOTE ON ISOLATION: `Employee Separation` is in `IMMEDIATE_SEND_DOCTYPES`, and that
branch calls `frappe.db.commit()` (possibleworks/observer/observer.py:152). Left alone
it would commit test data into the site being tested, so every test here neutralises
`WorkflowEventObserver.should_process` -- which also keeps the suite honest about the
fact that this commit exists.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, today

from possibleworks.observer.constants import IMMEDIATE_SEND_DOCTYPES
from possibleworks.observer.observer import WorkflowEventObserver
from possibleworks.offboarding import separation
from possibleworks.offboarding.api import create_employee_separation
from possibleworks.offboarding.constants import (
	DEFAULT_SEPARATION_ACTIVITIES,
	DEFAULT_SEPARATION_TEMPLATE_TITLE,
	EXIT_COORDINATOR_ROLE,
	LAST_WORKING_DAY_FIELD,
	MANAGER_OWNED_ACTIVITIES,
	NOTICE_WINDOW_DAYS,
	SEPARATION_DOCTYPE,
	SEPARATION_TEMPLATE_DOCTYPE,
	SOURCE_FIELD,
)


class TestEmployeeSeparation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Run the field patch rather than depend on migration order -- same reasoning as
		# `ensure_standard_role_profile()` in the onboarding suite. Idempotent.
		from possibleworks.patches.v1_6 import add_employee_separation_source_field

		add_employee_separation_source_field.execute()

		# A real employee to separate. Needs a manager with a login so the
		# manager-owned-row assertion has something to resolve to.
		cls.employee = frappe.db.get_value(
			"Employee",
			{
				"status": "Active",
				"reports_to": ("is", "set"),
			},
			["name", "company", "reports_to"],
			as_dict=True,
		)

	def setUp(self):
		frappe.set_user("Administrator")
		if not self.employee:
			self.skipTest("no Active Employee with a Reports To on this site")

		# See the module docstring: keeps the separation insert from committing.
		observer = patch.object(
			WorkflowEventObserver, "should_process", return_value=False
		)
		observer.start()
		self.addCleanup(observer.stop)

		self.ensure_company_holiday_list()
		self.clear_separations()
		self.addCleanup(self.clear_separations)

	def clear_separations(self):
		"""Per-test isolation, done explicitly rather than left to the framework.

		`IntegrationTestCase` registers its rollback with `addClassCleanup`
		(frappe/tests/classes/integration_test_case.py:72), so it runs ONCE for the whole
		class -- a record written by one test is still there for the next. The onboarding
		suite gets away with it because every test mints a fresh applicant; this suite
		reuses one Employee, so without this the idempotency guard would turn every
		creation after the first into a no-op and the assertions would quietly invert.
		"""
		for name in frappe.get_all(
			SEPARATION_DOCTYPE, filters={"employee": self.employee.name}, pluck="name"
		):
			frappe.delete_doc(
				SEPARATION_DOCTYPE, name, force=True, ignore_permissions=True
			)

	def ensure_company_holiday_list(self):
		"""`validate_holiday_list_available` needs one to resolve, or nothing inserts."""
		from hrms.utils.holiday_list import get_holiday_list_for_employee

		if get_holiday_list_for_employee(self.employee.name, raise_exception=False):
			return

		holiday_list = frappe.db.get_value(
			"Holiday List",
			{"from_date": ("<=", today()), "to_date": (">=", today())},
			["name", "from_date"],
			as_dict=True,
		)
		if not holiday_list:
			self.skipTest("no Holiday List on this site covers today")

		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Company",
				"assigned_to": self.employee.company,
				"holiday_list": holiday_list.name,
				"from_date": holiday_list.from_date,
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

	# ------------------------------------------------------------------ #
	# Helpers
	# ------------------------------------------------------------------ #

	def last_working_day(self, days_from_now=30):
		return getdate(add_days(today(), days_from_now))

	def make_separation(self, days_from_now=30):
		return separation.ensure_employee_separation(
			employee=self.employee.name,
			notice_end_date=self.last_working_day(days_from_now),
		)

	# ------------------------------------------------------------------ #
	# The fallback role
	# ------------------------------------------------------------------ #

	def test_exit_coordinator_role_is_created_and_idempotent(self):
		first = separation.ensure_exit_coordinator_role()
		second = separation.ensure_exit_coordinator_role()

		self.assertEqual(first, EXIT_COORDINATOR_ROLE)
		self.assertEqual(second, EXIT_COORDINATOR_ROLE)
		self.assertTrue(frappe.db.exists("Role", EXIT_COORDINATOR_ROLE))
		# Desk access matters: holders work the Tasks the checklist creates.
		self.assertEqual(
			frappe.db.get_value("Role", EXIT_COORDINATOR_ROLE, "desk_access"), 1
		)

	# ------------------------------------------------------------------ #
	# The default template
	# ------------------------------------------------------------------ #

	def test_default_template_is_matched_by_title_not_name(self):
		name = separation.ensure_default_separation_template()

		# autoname is a series, so the title is neither the record name nor unique --
		# which is exactly why the lookup must not use `exists` on a name.
		self.assertNotEqual(name, DEFAULT_SEPARATION_TEMPLATE_TITLE)
		self.assertEqual(
			frappe.db.get_value(SEPARATION_TEMPLATE_DOCTYPE, name, "title"),
			DEFAULT_SEPARATION_TEMPLATE_TITLE,
		)
		self.assertEqual(name, separation.ensure_default_separation_template())

	def test_every_template_row_carries_the_fallback_role(self):
		"""Without this, submit creates Tasks assigned to nobody and sends no tiles."""
		template = frappe.get_doc(
			SEPARATION_TEMPLATE_DOCTYPE, separation.ensure_default_separation_template()
		)

		self.assertEqual(len(template.activities), len(DEFAULT_SEPARATION_ACTIVITIES))
		for row in template.activities:
			self.assertEqual(row.role, EXIT_COORDINATOR_ROLE, row.activity_name)
			# A blank begin_on makes get_task_dates return [None, None].
			self.assertIsNotNone(row.begin_on, row.activity_name)

	def test_existing_template_is_never_overwritten(self):
		"""Once a site has the template it owns it."""
		name = separation.ensure_default_separation_template()
		doc = frappe.get_doc(SEPARATION_TEMPLATE_DOCTYPE, name)
		doc.activities[0].activity_name = "Site edited this row"
		doc.activities[0].begin_on = 3
		doc.save()

		self.assertEqual(name, separation.ensure_default_separation_template())
		reloaded = frappe.get_doc(SEPARATION_TEMPLATE_DOCTYPE, name)
		self.assertEqual(reloaded.activities[0].activity_name, "Site edited this row")
		self.assertEqual(reloaded.activities[0].begin_on, 3)

	# ------------------------------------------------------------------ #
	# Day zero
	# ------------------------------------------------------------------ #

	def test_day_zero_is_the_notice_window_before_the_last_working_day(self):
		lwd = self.last_working_day(30)

		self.assertEqual(
			separation.resolve_boarding_begins_on(lwd),
			getdate(add_days(lwd, -NOTICE_WINDOW_DAYS)),
		)

	def test_day_zero_is_clamped_to_today_for_a_short_notice_period(self):
		"""Otherwise every task is created already overdue."""
		lwd = self.last_working_day(2)

		self.assertEqual(
			separation.resolve_boarding_begins_on(lwd), getdate(today())
		)

	def test_day_zero_is_clamped_for_a_last_working_day_in_the_past(self):
		self.assertEqual(
			separation.resolve_boarding_begins_on(self.last_working_day(-90)),
			getdate(today()),
		)

	# ------------------------------------------------------------------ #
	# The checklist
	# ------------------------------------------------------------------ #

	def test_separation_is_created_as_a_draft(self):
		result = self.make_separation()

		self.assertTrue(result["created"])
		doc = frappe.get_doc(SEPARATION_DOCTYPE, result["name"])
		# Draft on purpose: the scheduler submits it once day zero arrives, and that
		# submit is what mints the Project, the Tasks and therefore the tiles.
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.employee, self.employee.name)
		self.assertEqual(doc.company, self.employee.company)
		self.assertFalse(doc.project)

	def test_activities_are_actually_copied(self):
		"""The trap: setting the template link alone leaves `activities` empty.

		`employee_separation.js` fills the table client-side, so a Python insert or a
		REST POST that only sets the link produces a checklist that submits to a Project
		with no tasks -- silently.
		"""
		doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])

		self.assertTrue(doc.employee_separation_template)
		self.assertEqual(len(doc.activities), len(DEFAULT_SEPARATION_ACTIVITIES))
		self.assertEqual(
			[row.activity_name for row in doc.activities],
			[row["activity_name"] for row in DEFAULT_SEPARATION_ACTIVITIES],
		)

	def test_manager_owned_rows_are_pinned_to_the_manager_alone(self):
		manager_user = separation.get_reporting_manager_user(self.employee.name)
		if not manager_user:
			self.skipTest("this site's manager Employee has no user_id")

		doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])
		manager_rows = [
			row for row in doc.activities if row.activity_name in MANAGER_OWNED_ACTIVITIES
		]
		self.assertTrue(manager_rows, "expected at least one manager-owned activity")

		for row in manager_rows:
			self.assertEqual(row.user, manager_user)
			# Cleared, not left alongside `user`: Frappe unions the two, so leaving the
			# role would fan a handover task out to every exit coordinator as well.
			self.assertFalse(row.role, row.activity_name)

	def test_role_owned_rows_keep_the_role_and_name_no_user(self):
		doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])
		other_rows = [
			row
			for row in doc.activities
			if row.activity_name not in MANAGER_OWNED_ACTIVITIES
		]
		self.assertTrue(other_rows)

		for row in other_rows:
			self.assertEqual(row.role, EXIT_COORDINATOR_ROLE, row.activity_name)
			self.assertFalse(row.user, row.activity_name)

	def test_manager_owned_rows_keep_the_role_when_no_manager_resolves(self):
		"""Degrades to "the coordinators get it", never to "nobody gets it"."""
		with patch.object(separation, "get_reporting_manager_user", return_value=None):
			doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])

		for row in doc.activities:
			self.assertTrue(
				row.user or row.role,
				f"{row.activity_name} would produce a Task assigned to nobody",
			)

	def test_last_working_day_and_source_flag_are_stored(self):
		lwd = self.last_working_day(30)
		result = separation.ensure_employee_separation(
			employee=self.employee.name, notice_end_date=lwd
		)
		doc = frappe.get_doc(SEPARATION_DOCTYPE, result["name"])

		# The manager's exact date, not derivable from boarding_begins_on once clamped.
		self.assertEqual(getdate(doc.get(LAST_WORKING_DAY_FIELD)), lwd)
		self.assertEqual(doc.get(SOURCE_FIELD), 1)

	def test_resignation_letter_date_defaults_to_today(self):
		doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])
		self.assertEqual(getdate(doc.resignation_letter_date), getdate(today()))

	# ------------------------------------------------------------------ #
	# Idempotency
	# ------------------------------------------------------------------ #

	def test_second_call_returns_the_existing_record(self):
		"""`Employee Separation` ships with no duplicate guard of its own."""
		first = self.make_separation()
		second = self.make_separation()

		self.assertTrue(first["created"])
		self.assertFalse(second["created"])
		self.assertEqual(first["name"], second["name"])
		self.assertEqual(
			frappe.db.count(
				SEPARATION_DOCTYPE,
				{"employee": self.employee.name, "docstatus": ("!=", 2)},
			),
			1,
		)

	def test_missing_employee_is_refused(self):
		with self.assertRaises(frappe.DoesNotExistError):
			separation.ensure_employee_separation(
				employee="EMP-does-not-exist", notice_end_date=self.last_working_day()
			)

	def test_holiday_list_gate_blocks_creation(self):
		"""Raised at creation, where the approving manager sees it -- not at the
		unattended submit, where it would land in a scheduler log."""
		with patch(
			"hrms.utils.holiday_list.get_holiday_list_for_employee", return_value=None
		):
			with self.assertRaises(frappe.ValidationError):
				self.make_separation()

		self.assertTrue(separation.employee_separation_missing(self.employee.name))

	# ------------------------------------------------------------------ #
	# Agreed scope: Frappe's Employee record is not touched
	# ------------------------------------------------------------------ #

	def test_employee_status_and_relieving_date_are_left_alone(self):
		before = frappe.db.get_value(
			"Employee", self.employee.name, ["status", "relieving_date"], as_dict=True
		)
		self.make_separation()
		after = frappe.db.get_value(
			"Employee", self.employee.name, ["status", "relieving_date"], as_dict=True
		)

		self.assertEqual(before.status, after.status)
		self.assertEqual(before.relieving_date, after.relieving_date)

	# ------------------------------------------------------------------ #
	# The whitelisted endpoint
	# ------------------------------------------------------------------ #

	def test_endpoint_creates_and_reports_the_window(self):
		lwd = self.last_working_day(30)
		result = create_employee_separation(
			employee=self.employee.name, notice_end_date=str(lwd)
		)

		self.assertTrue(result["created"])
		self.assertEqual(result["employee"], self.employee.name)
		self.assertEqual(result["notice_window_days"], NOTICE_WINDOW_DAYS)
		self.assertEqual(
			getdate(result["boarding_begins_on"]),
			getdate(add_days(lwd, -NOTICE_WINDOW_DAYS)),
		)

	def test_endpoint_requires_a_notice_end_date(self):
		with self.assertRaises(frappe.MandatoryError):
			create_employee_separation(
				employee=self.employee.name, notice_end_date=""
			)

	def test_endpoint_requires_an_employee(self):
		with self.assertRaises(frappe.MandatoryError):
			create_employee_separation(
				employee="  ", notice_end_date=str(self.last_working_day())
			)

	def test_endpoint_rejects_an_unparseable_date(self):
		with self.assertRaises(frappe.ValidationError):
			create_employee_separation(
				employee=self.employee.name, notice_end_date="not-a-date"
			)

	# ------------------------------------------------------------------ #
	# Transport
	# ------------------------------------------------------------------ #

	def test_separation_is_observed(self):
		"""Without this the submit never reaches PossibleWorks and no tile is sent."""
		self.assertIn(SEPARATION_DOCTYPE, IMMEDIATE_SEND_DOCTYPES)

	def test_company_resolves_for_the_payload_builder(self):
		"""An unresolvable company means the payload is never built and the event is
		written off as Dropped -- silently."""
		from possibleworks.observer.payload_builder import PayloadBuilder

		doc = frappe.get_doc(SEPARATION_DOCTYPE, self.make_separation()["name"])
		self.assertEqual(PayloadBuilder._resolve_company(doc), self.employee.company)
