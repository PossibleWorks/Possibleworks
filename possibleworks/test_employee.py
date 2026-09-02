# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for possibleworks.employee's manager-reassignment flow: when an Employee's status
changes to Left/Inactive/Suspended while they still have Active direct reports, this lets
those reports be atomically reassigned to another Active manager instead of just hard-
blocking the save (see block_status_change_with_active_reports for the hard block itself,
which this feature sits in front of and does not modify).

NOTE ON ISOLATION: creating an `Employee` fires the Observer, and `Employee` is in
`IMMEDIATE_SEND_DOCTYPES`, whose branch calls `frappe.db.commit()`
(possibleworks/observer/observer.py:145). Left alone, that would commit test data into the
site being tested. Every test that creates or saves an Employee therefore neutralises
`WorkflowEventObserver.should_process`, exactly as
onboarding/doctype/onboarding_applicant/test_onboarding_applicant.py does.

NOTE ON FIXTURES: this site makes `reports_to` mandatory via a Property Setter, and
possibleworks.employee.sync_leave_approver_and_reports_to (an Employee before_save hook)
rejects a Reports To without a linked user account. Rather than fabricate a fully valid
Employee (and a User account) from scratch, every test employee is cloned from a real,
already-valid Active employee -- same reasoning as that test file's `cls.manager`.

NOTE ON ISOLATION BETWEEN TESTS: IntegrationTestCase only rolls the database back once,
at class teardown (frappe/tests/classes/integration_test_case.py) -- not between
individual test methods, which also don't run in definition order. So every test builds
its own fresh, uniquely-named manager (via `_new_manager`) rather than sharing one across
tests: a shared record's set of "active direct reports" would otherwise depend on what
other tests happened to run first. `new_manager_name` is the one exception -- it's only
ever a reassignment *target*, never mutated or asserted to have an exact set of reports,
so other tests reassigning into it is harmless.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, today

from possibleworks.employee import change_status_with_reassignment, get_active_direct_reports
from possibleworks.observer.observer import WorkflowEventObserver


class TestEmployeeStatusReassignment(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		candidates = frappe.get_all(
			"Employee",
			filters={"status": "Active", "user_id": ("is", "set")},
			pluck="name",
			order_by="creation",
			limit_page_length=5,
		)
		if len(candidates) < 2:
			raise unittest.SkipTest(
				"Need at least two Active employees with a user account on this site"
			)

		cls.anchor = frappe.get_doc("Employee", candidates[0])
		cls.new_manager_name = candidates[1]
		cls.new_manager_user_id = frappe.db.get_value("Employee", cls.new_manager_name, "user_id")

		# The feature is gated by Policy Configuration.enable_manager_status_reassignment --
		# force it on regardless of this site's current value, so this suite doesn't depend
		# on it. test_disabled_flag_* below is the one test that flips it off deliberately.
		cls._original_flag_value = frappe.db.get_single_value(
			"Policy Configuration", "enable_manager_status_reassignment"
		)
		frappe.db.set_value(
			"Policy Configuration", "Policy Configuration", "enable_manager_status_reassignment", 1
		)
		cls.addClassCleanup(
			lambda: frappe.db.set_value(
				"Policy Configuration",
				"Policy Configuration",
				"enable_manager_status_reassignment",
				cls._original_flag_value,
			)
		)

	def setUp(self):
		frappe.set_user("Administrator")
		self._observer = patch.object(WorkflowEventObserver, "should_process", return_value=False)
		self._observer.start()
		self.addCleanup(self._observer.stop)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

	def _new_employee(self, **overrides):
		"""A fresh, in-memory-valid Employee cloned from a real Active employee, so every
		site-specific mandatory field is already satisfied. Always a brand-new, uniquely
		named record, so nothing pre-existing in the DB can already reference it."""
		clone = frappe.copy_doc(self.anchor)
		suffix = frappe.generate_hash(length=8)
		# This site names Employee by `employee_number` (HR Settings.emp_created_by --
		# see hrms.overrides.employee_master.EmployeeMaster.autoname), so a clone that
		# keeps the anchor's employee_number would autoname to the anchor's own name.
		clone.employee_number = f"TEST{suffix}"
		clone.first_name = f"Test{suffix}"
		clone.employee_name = f"Test{suffix}"
		clone.personal_email = f"test.{suffix}@example.com"
		clone.company_email = None
		clone.user_id = None
		clone.status = "Active"
		clone.relieving_date = None
		clone.reports_to = self.anchor.name
		# For a brand-new insert, sync_leave_approver_and_reports_to sees "old" leave_approver
		# as None (the doc doesn't exist in the DB yet) -- so a copied, non-empty
		# leave_approver would look "changed" and win over the `reports_to` set above,
		# re-deriving reports_to from it and silently clobbering the override.
		clone.leave_approver = None
		# Employee is a NestedSet keyed on `reports_to` (nsm_parent_field). A copied lft/rgt/
		# old_parent makes frappe.utils.nestedset.update_nsm think this is an *existing* node
		# whose parent moved, and it tries to relocate a tree position that was never really
		# this doc's -- clearing them makes it treat this insert as the new leaf it actually is.
		clone.lft = None
		clone.rgt = None
		clone.old_parent = None
		# Unique on this site; copying the anchor's value collides on the second clone.
		clone.attendance_device_id = None
		# custom_probation_* is `reqd` unconditionally on this site (added after the
		# real records this suite clones from were created, so they still carry blank
		# values) -- any new insert must supply it regardless of employment_type.
		clone.custom_probation_start_date = today()
		clone.custom_probation_end_date = add_days(today(), 90)
		for field, value in overrides.items():
			clone.set(field, value)
		clone.insert()
		return clone

	def _new_manager(self, **overrides):
		"""A fresh Active employee with a (reused, real) user account, so other fresh
		test employees can validly set `reports_to` to it. Inserted without a user_id
		first -- erpnext's validate_duplicate_user_id would otherwise reject a user
		already assigned to `self.anchor` -- then attached via db_set, which bypasses
		that validation the same way `test_promoting_a_direct_report_...` does."""
		manager = self._new_employee(**overrides)
		manager.db_set("user_id", self.anchor.user_id)
		return manager

	def _change_status(self, manager, **kwargs):
		"""Wraps change_status_with_reassignment for a `_new_manager()` fixture.

		`manager.user_id` was only ever needed transiently (see `_new_manager`) so other
		fresh employees could validly report to it -- change_status_with_reassignment
		saves `manager`'s own record next, and a leftover user_id there would collide
		with the real employee `self.anchor.user_id` actually belongs to, under
		erpnext's validate_duplicate_user_id. Detached here rather than in
		`_new_manager` because reports created between the two calls still need it.
		"""
		manager.db_set("user_id", None)
		return change_status_with_reassignment(employee=manager.name, **kwargs)

	def test_reassigns_active_direct_reports_and_changes_status(self):
		manager = self._new_manager()
		report = self._new_employee(reports_to=manager.name)

		result = self._change_status(
			manager,
			new_status="Inactive",
			new_manager=self.new_manager_name,
		)

		self.assertEqual(result["reassigned"], [report.name])
		self.assertEqual(result["new_status"], "Inactive")
		self.assertEqual(
			frappe.db.get_value("Employee", report.name, "reports_to"), self.new_manager_name
		)
		self.assertEqual(
			frappe.db.get_value("Employee", report.name, "leave_approver"),
			self.new_manager_user_id,
		)
		self.assertEqual(frappe.db.get_value("Employee", manager.name, "status"), "Inactive")

	def test_reassigns_for_left_with_relieving_date(self):
		manager = self._new_manager()
		report = self._new_employee(reports_to=manager.name)
		relieving_date = today()

		self._change_status(
			manager,
			new_status="Left",
			new_manager=self.new_manager_name,
			relieving_date=relieving_date,
		)

		self.assertEqual(
			frappe.db.get_value("Employee", report.name, "reports_to"), self.new_manager_name
		)
		self.assertEqual(frappe.db.get_value("Employee", manager.name, "status"), "Left")
		self.assertEqual(
			str(frappe.db.get_value("Employee", manager.name, "relieving_date")), relieving_date
		)

	def test_left_without_relieving_date_is_still_rejected(self):
		"""relieving_date isn't re-validated by this feature -- erpnext's own
		Employee.validate_status still enforces it, since the manager's own status
		change goes through a normal `.save()`."""
		manager = self._new_manager()

		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Left",
			new_manager=self.new_manager_name,
		)

	def test_rejects_new_manager_who_currently_reports_to_the_departing_employee(self):
		"""Promoting a direct report to manage their own former peers can't be handled by
		just excluding them from the reassignment loop: they would keep reporting to
		`employee`, which is itself still an active direct report as far as
		block_status_change_with_active_reports is concerned -- so the status save
		would immediately hit that same hard block this feature exists to avoid.
		Moving that peer to report elsewhere first is a separate action for HR to take."""
		manager = self._new_manager()
		peer = self._new_employee(reports_to=manager.name)
		# Otherwise valid as a manager (Active, has a user account) -- isolates the
		# assertion to the "currently reports to the departing employee" rule itself,
		# rather than incidentally failing the unrelated user-account check.
		peer.db_set("user_id", self.new_manager_user_id)

		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Suspended",
			new_manager=peer.name,
		)

	def test_no_active_direct_reports_is_a_no_op_reassignment(self):
		manager = self._new_manager()

		result = self._change_status(
			manager,
			new_status="Suspended",
			new_manager=self.new_manager_name,
		)

		self.assertEqual(result["reassigned"], [])
		self.assertEqual(frappe.db.get_value("Employee", manager.name, "status"), "Suspended")

	def test_rejects_invalid_status(self):
		manager = self._new_manager()
		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Active",
			new_manager=self.new_manager_name,
		)

	def test_rejects_blank_new_manager(self):
		manager = self._new_manager()
		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Inactive",
			new_manager=None,
		)

	def test_rejects_self_as_new_manager(self):
		manager = self._new_manager()
		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Inactive",
			new_manager=manager.name,
		)

	def test_rejects_inactive_new_manager(self):
		manager = self._new_manager()
		inactive_candidate = self._new_employee(status="Inactive")

		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Inactive",
			new_manager=inactive_candidate.name,
		)

	def test_rejects_new_manager_without_user_account(self):
		manager = self._new_manager()
		no_user_candidate = self._new_employee()

		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Inactive",
			new_manager=no_user_candidate.name,
		)

	def test_rolls_back_reassignment_when_status_save_fails(self):
		"""Reassignment happens before the manager's own status save. If that second save
		fails, nothing should end up partially persisted -- but change_status_with_reassignment
		doesn't roll back its own writes; that's frappe's request handler rolling back the
		whole DB transaction when a whitelisted call raises (frappe/app.py), which only
		happens at a real HTTP request boundary. A raw function call in this test has no
		such boundary, so a savepoint here stands in for it."""
		manager = self._new_manager()
		report = self._new_employee(reports_to=manager.name)

		savepoint = "test_status_reassignment_rollback"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(frappe.ValidationError):
				self._change_status(
					manager,
					new_status="Left",
					new_manager=self.new_manager_name,
					# No relieving_date -- erpnext's core validate_status rejects this for
					# Left, after the reassignment loop above would already have run.
				)
		finally:
			frappe.db.rollback(save_point=savepoint)

		self.assertEqual(frappe.db.get_value("Employee", report.name, "reports_to"), manager.name)
		self.assertEqual(frappe.db.get_value("Employee", manager.name, "status"), "Active")

	def test_permission_check_blocks_unprivileged_user(self):
		manager = self._new_manager()
		frappe.set_user("Guest")
		self.assertRaises(
			frappe.PermissionError,
			change_status_with_reassignment,
			employee=manager.name,
			new_status="Inactive",
			new_manager=self.new_manager_name,
		)

	def test_get_active_direct_reports_lists_only_active_reports(self):
		manager = self._new_manager()
		active_report = self._new_employee(reports_to=manager.name)
		self._new_employee(reports_to=manager.name, status="Inactive")

		names = [row.name for row in get_active_direct_reports(manager.name)]

		self.assertEqual(names, [active_report.name])

	def test_get_active_direct_reports_permission_check_blocks_unprivileged_user(self):
		manager = self._new_manager()
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, get_active_direct_reports, manager.name)

	def test_disabled_flag_blocks_the_feature_entirely(self):
		"""With Policy Configuration.enable_manager_status_reassignment off, a site sees no
		trace of this feature: get_active_direct_reports returns nothing (so the client
		script's dialog never opens) and change_status_with_reassignment refuses outright."""
		frappe.db.set_value(
			"Policy Configuration", "Policy Configuration", "enable_manager_status_reassignment", 0
		)
		self.addCleanup(
			lambda: frappe.db.set_value(
				"Policy Configuration", "Policy Configuration", "enable_manager_status_reassignment", 1
			)
		)

		manager = self._new_manager()
		self._new_employee(reports_to=manager.name)

		self.assertEqual(get_active_direct_reports(manager.name), [])
		self.assertRaises(
			frappe.ValidationError,
			self._change_status,
			manager,
			new_status="Inactive",
			new_manager=self.new_manager_name,
		)
