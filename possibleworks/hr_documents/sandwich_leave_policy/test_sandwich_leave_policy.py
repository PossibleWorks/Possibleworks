# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for the Sandwich Leave Policy.

Detection, adjacency, idempotency, cancellation and notification logic all
live in the Server Script body (server_script_reference.txt), not in
importable app code -- deliberately, so HR can inspect/edit the policy from
Desk without a deployment. This suite therefore tests that logic by loading
the actual script text and running it through frappe.utils.safe_exec.safe_exec
-- the same sandbox a real Server Script executes in -- rather than testing a
separate Python reimplementation that could silently drift from what's
actually pasted into Desk.

helpers.py (the three mechanical primitives the script calls into) and
payroll.py (the Salary Slip payment_days hook) remain ordinary importable
Python and are tested directly.

Uses a dedicated, code-computed Fri/Sat/Sun/Mon block far in the future (not
tied to `today()`) so the dates never collide with real site data and the
suite stays deterministic regardless of when it's run. A purpose-built
Holiday List and Leave Type keep the fixtures self-contained -- the Leave
Type is Leave Without Pay (is_lwp=1) specifically so applying leave never
needs a Leave Allocation/balance to exist first, which is irrelevant to
what's under test here.

NOTE ON ISOLATION: IntegrationTestCase only rolls the database back once, at
class teardown -- not between individual test methods (see test_employee.py
for the fuller explanation this suite borrows). Every test that creates a
Leave Application/Attendance/Sandwich Leave Log therefore uses its own
employee (cloned fresh per test) so one test's writes can never be seen by
another.
"""

import datetime
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, cstr, getdate
from frappe.utils.safe_exec import safe_exec
from hrms.hr.doctype.attendance.attendance import Attendance

from possibleworks.hr_documents.sandwich_leave_policy import helpers, payroll
from possibleworks.observer.observer import WorkflowEventObserver


def _next_weekday(start, weekday):
	"""Next date on/after `start` falling on `weekday` (Monday=0 .. Sunday=6)."""
	days_ahead = (weekday - start.weekday()) % 7
	return start + datetime.timedelta(days=days_ahead or 7)


# A Friday far in the future (2027, well past any real data on this bench),
# so FRIDAY/SATURDAY/SUNDAY/MONDAY never collide with anything real.
FRIDAY = _next_weekday(datetime.date(2027, 1, 1), 4)
SATURDAY = add_days(FRIDAY, 1)
SUNDAY = add_days(FRIDAY, 2)
MONDAY = add_days(FRIDAY, 3)

_SCRIPT_PATH = os.path.join(os.path.dirname(__file__), "server_script_reference.txt")
_SCRIPT_START = '---- paste below this line into the Server Script\'s "Script" field ----'
_SCRIPT_END = "---- paste above this line ----"


def _load_script_body():
	"""The exact text meant to be pasted into the Server Script -- read fresh
	from the reference file every call, never cached in this suite, so an
	edit to the real script is what the tests exercise, not a stale copy."""
	with open(_SCRIPT_PATH) as handle:
		content = handle.read()
	start = content.index(_SCRIPT_START) + len(_SCRIPT_START)
	end = content.index(_SCRIPT_END)
	return content[start:end].strip("\n")


class SandwichLeavePolicyTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

		candidates = frappe.get_all(
			"Employee",
			filters={"status": "Active", "user_id": ("is", "set")},
			pluck="name",
			order_by="creation",
			limit_page_length=1,
		)
		if not candidates:
			raise unittest.SkipTest("Need at least one Active employee with a user account on this site")
		cls.anchor = frappe.get_doc("Employee", candidates[0])

		# from_date is deliberately years in the past, not just before FRIDAY:
		# hrms.hr.utils.get_holidays_for_employee resolves the applicable Holiday
		# List Assignment as of *today*, not as of the dates being queried (it
		# calls get_holiday_list_for_employee without an `as_on`, which then
		# defaults to today) -- so both the Holiday List's own validity and the
		# Assignment's from_date must already cover today, or every Leave
		# Application submission in this suite fails to resolve a holiday list
		# at all, regardless of how far in the future FRIDAY/MONDAY are.
		cls.holiday_list = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": f"Test Sandwich Holiday List {frappe.generate_hash(length=6)}",
				"from_date": "2020-01-01",
				"to_date": add_days(MONDAY, 30),
				"holidays": [
					{"holiday_date": SATURDAY, "description": "Test Saturday"},
					{"holiday_date": SUNDAY, "description": "Test Sunday"},
				],
			}
		).insert()

		cls.leave_type = frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": f"Test Sandwich Leave Type {frappe.generate_hash(length=6)}",
				"is_lwp": 1,  # sidesteps needing a Leave Allocation/balance for this suite
			}
		).insert()

		cls.other_leave_type = frappe.get_doc(
			{
				"doctype": "Leave Type",
				"leave_type_name": f"Test Ineligible Leave Type {frappe.generate_hash(length=6)}",
				"is_lwp": 1,
			}
		).insert()

		cls._original_enable_flag = frappe.db.get_single_value(
			"Policy Configuration", "enable_sandwich_leave_policy"
		)
		cls._original_eligible_rows = frappe.get_all(
			"Sandwich Eligible Leave Type",
			filters={"parenttype": "Policy Configuration"},
			fields=["leave_type"],
		)
		policy = frappe.get_single("Policy Configuration")
		policy.enable_sandwich_leave_policy = 1
		policy.set("sandwich_eligible_leave_types", [{"leave_type": cls.leave_type.name}])
		policy.save()

		def _restore_policy_config():
			policy = frappe.get_single("Policy Configuration")
			policy.enable_sandwich_leave_policy = cls._original_enable_flag
			policy.set(
				"sandwich_eligible_leave_types",
				[{"leave_type": row.leave_type} for row in cls._original_eligible_rows],
			)
			policy.save()

		cls.addClassCleanup(_restore_policy_config)

	def setUp(self):
		frappe.set_user("Administrator")
		# Employee creation fires the Observer, whose IMMEDIATE_SEND_DOCTYPES branch
		# commits -- neutralise it exactly as test_employee.py does, so test data
		# never gets committed to the site being tested.
		self._observer = patch.object(WorkflowEventObserver, "should_process", return_value=False)
		self._observer.start()
		self.addCleanup(self._observer.stop)
		self.addCleanup(lambda: frappe.set_user("Administrator"))

		# The script commits after every successfully-applied block (so a
		# failure on one employee doesn't roll back everyone already done) and
		# rolls back on a failed one. Both are real SQL COMMIT/ROLLBACK -- fine
		# in production, but IntegrationTestCase holds this whole class in ONE
		# uncommitted transaction, rolled back only once at teardown (see
		# integration_test_case.py). A real commit here would permanently write
		# test data to this site; a real rollback would wipe out setUpClass's
		# own fixtures (Holiday List, Leave Type, Policy Configuration) for
		# every test that runs after this one. Neutralise both, the same way
		# the Observer's commit is neutralised above -- what's under test is
		# the script's own control flow (does it call commit on success, does
		# it call rollback and keep going on failure), not the SQL engine's
		# commit/rollback semantics, which are Frappe's own primitive.
		self._db_commit = patch.object(frappe.db, "commit", lambda *a, **k: None)
		self._db_rollback = patch.object(frappe.db, "rollback", lambda *a, **k: None)
		self._db_commit.start()
		self._db_rollback.start()
		self.addCleanup(self._db_commit.stop)
		self.addCleanup(self._db_rollback.stop)

	def _new_employee(self):
		clone = frappe.copy_doc(self.anchor)
		suffix = frappe.generate_hash(length=8)
		clone.employee_number = f"TESTSW{suffix}"
		clone.first_name = f"TestSW{suffix}"
		clone.employee_name = f"TestSW{suffix}"
		clone.personal_email = f"test.sw.{suffix}@example.com"
		clone.company_email = None
		clone.user_id = None
		clone.status = "Active"
		clone.relieving_date = None
		clone.leave_approver = None
		clone.lft = None
		clone.rgt = None
		clone.old_parent = None
		clone.attendance_device_id = None
		clone.custom_probation_start_date = FRIDAY
		clone.custom_probation_end_date = add_days(FRIDAY, 90)
		clone.insert()

		# This HRMS version resolves holidays exclusively through a submitted
		# Holiday List Assignment (hrms.utils.holiday_list.get_assigned_holiday_list)
		# -- Employee.holiday_list is not consulted at all -- so the test Holiday
		# List has to be attached this way, not via the Employee field.
		frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Employee",
				"assigned_to": clone.name,
				"holiday_list": self.holiday_list.name,
				"from_date": "2020-01-01",  # must cover today too -- see the Holiday List comment above
			}
		).insert().submit()
		return clone

	def _leave_application(self, employee, from_date, to_date, leave_type=None, half_day_session=None):
		doc = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": employee.name,
				"leave_type": (leave_type or self.leave_type).name,
				"from_date": from_date,
				"to_date": to_date,
				"status": "Approved",
			}
		)
		if half_day_session:
			doc.half_day = 1
			doc.half_day_date = from_date
			doc.custom_half_day_session = half_day_session
		doc.insert()
		doc.submit()
		return doc

	def _run_policy(self, from_date, to_date, dry_run, user="Administrator"):
		"""Execute the real Server Script text -- the exact string meant to be
		pasted into Desk -- through the same safe_exec sandbox a live Server
		Script runs in, simulating the API-type call it's configured as."""
		script = _load_script_body()

		previous_user = frappe.session.user
		previous_form_dict = getattr(frappe.local, "form_dict", None)
		previous_response = getattr(frappe.local, "response", None)

		frappe.set_user(user)
		frappe.local.form_dict = frappe._dict(from_date=cstr(from_date), to_date=cstr(to_date), dry_run=dry_run)
		# get_safe_globals() only exposes frappe.response when `if frappe.response:`
		# is truthy, and binds the SAME object -- seed it non-empty like a real
		# request does, so the script's writes land somewhere this test can read.
		frappe.local.response = frappe._dict({"docs": []})

		try:
			safe_exec(script)
			return frappe.local.response.get("message")
		finally:
			frappe.local.form_dict = previous_form_dict
			frappe.local.response = previous_response
			frappe.set_user(previous_user)

	def _block_for(self, result, employee_name):
		"""Scope an orchestrator result to one employee's block. Other tests in
		this class share the same FRIDAY/MONDAY dates and IntegrationTestCase
		only rolls back once at class teardown (see test_employee.py for the
		fuller rationale), so a run started after other tests have already
		created real sandwiches on those dates legitimately finds their blocks
		too -- asserting on the raw totals would be testing DB leftovers, not
		this test's own behaviour."""
		matches = [b for b in result["blocks"] if b["employee"] == employee_name]
		self.assertEqual(len(matches), 1, f"expected exactly one block for {employee_name}")
		return matches[0]

	# ---- helpers.py (mechanical primitives, called by the script) ----------

	def test_helper_get_holiday_dates_returns_the_configured_holidays(self):
		employee = self._new_employee()

		dates = helpers.get_holiday_dates(employee.name, FRIDAY, MONDAY)

		self.assertEqual(sorted(dates), sorted([cstr(SATURDAY), cstr(SUNDAY)]))

	def test_helper_cancel_leave_application_cancels_and_is_idempotent(self):
		employee = self._new_employee()
		leave_application = self._leave_application(employee, FRIDAY, FRIDAY)

		helpers.cancel_leave_application(leave_application.name)
		self.assertEqual(frappe.db.get_value("Leave Application", leave_application.name, "docstatus"), 2)

		helpers.cancel_leave_application(leave_application.name)  # no-op, must not raise
		self.assertEqual(frappe.db.get_value("Leave Application", leave_application.name, "docstatus"), 2)

	def test_helper_force_mark_absent_overwrites_whatever_attendance_already_exists(self):
		"""Matches the script's actual calling order: cancel the covering leave
		first, then force_mark_absent -- see the next test for what happens if
		you don't."""
		employee = self._new_employee()
		leave_application = self._leave_application(employee, FRIDAY, FRIDAY)  # auto-creates "On Leave" Attendance
		self.assertEqual(
			frappe.db.get_value("Attendance", {"employee": employee.name, "attendance_date": FRIDAY}, "status"),
			"On Leave",
		)

		helpers.cancel_leave_application(leave_application.name)
		helpers.force_mark_absent(employee.name, FRIDAY, employee.company)

		attendance = frappe.get_all(
			"Attendance",
			filters={"employee": employee.name, "attendance_date": FRIDAY, "docstatus": 1},
			fields=["status", "custom_sandwich_policy_applied"],
		)
		self.assertEqual(len(attendance), 1)
		self.assertEqual(attendance[0].status, "Absent")
		self.assertEqual(attendance[0].custom_sandwich_policy_applied, 1)

	def test_helper_force_mark_absent_is_overridden_by_an_active_leave_application(self):
		"""Documents a real sharp edge (see the docstring on force_mark_absent):
		hrms's own Attendance.validate() overwrites status back to "On Leave"
		whenever an Approved+submitted Leave Application still covers the date,
		regardless of what force_mark_absent sets -- silently, no error. This is
		exactly why the script always cancels the covering leave application
		before calling force_mark_absent. Asserted here so a future edit to
		that ordering fails loudly instead of silently mis-marking attendance."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)  # left active, NOT cancelled

		helpers.force_mark_absent(employee.name, FRIDAY, employee.company)

		status = frappe.db.get_value(
			"Attendance", {"employee": employee.name, "attendance_date": FRIDAY, "docstatus": 1}, "status"
		)
		self.assertEqual(status, "On Leave")

	# ---- the actual Server Script, run end to end via safe_exec ------------

	def test_full_day_leave_on_both_sides_is_a_sandwich(self):
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		self._leave_application(employee, MONDAY, MONDAY)

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		block = self._block_for(result, employee.name)
		self.assertEqual(block["holiday_dates"], [cstr(SATURDAY), cstr(SUNDAY)])
		self.assertEqual(block["day_before"], cstr(FRIDAY))
		self.assertEqual(block["day_after"], cstr(MONDAY))

	def test_adjacent_half_days_are_a_sandwich(self):
		"""Friday Second Half (afternoon, touching the weekend) + Monday First Half
		(morning, touching the weekend) -- the halves that DO touch the block."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY, half_day_session="Second Half")
		self._leave_application(employee, MONDAY, MONDAY, half_day_session="First Half")

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		self._block_for(result, employee.name)  # raises if not found

	def test_non_adjacent_half_days_are_not_a_sandwich(self):
		"""Friday First Half (morning, worked in the afternoon) + Monday Second Half
		(afternoon, worked in the morning) -- a worked buffer sits between the leave
		and the weekend on both sides, so this must NOT be flagged."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY, half_day_session="First Half")
		self._leave_application(employee, MONDAY, MONDAY, half_day_session="Second Half")

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		self.assertFalse(any(b["employee"] == employee.name for b in result["blocks"]))

	def test_leave_on_only_one_side_is_not_a_sandwich(self):
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		# no Monday leave

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		self.assertFalse(any(b["employee"] == employee.name for b in result["blocks"]))

	def test_ineligible_leave_type_is_not_a_sandwich(self):
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY, leave_type=self.other_leave_type)
		self._leave_application(employee, MONDAY, MONDAY, leave_type=self.other_leave_type)

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		self.assertFalse(any(b["employee"] == employee.name for b in result["blocks"]))

	def test_dry_run_makes_no_writes(self):
		employee = self._new_employee()
		before = self._leave_application(employee, FRIDAY, FRIDAY)
		after = self._leave_application(employee, MONDAY, MONDAY)

		result = self._run_policy(FRIDAY, MONDAY, dry_run=1)

		self.assertTrue(result["dry_run"])
		self._block_for(result, employee.name)
		# Nothing written by the dry run itself: leave applications untouched, no
		# Sandwich Leave Log rows, and the Friday/Monday Attendance rows are the
		# ones core HRMS creates natively on Leave Application submission (status
		# "On Leave") -- present regardless of our code, and unchanged by it.
		self.assertEqual(frappe.db.get_value("Leave Application", before.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Leave Application", after.name, "docstatus"), 1)
		for date in (FRIDAY, MONDAY):
			self.assertEqual(
				frappe.db.get_value("Attendance", {"employee": employee.name, "attendance_date": date}, "status"),
				"On Leave",
			)
		for date in (SATURDAY, SUNDAY):
			self.assertEqual(
				frappe.db.count("Attendance", {"employee": employee.name, "attendance_date": date}), 0
			)
		self.assertEqual(frappe.db.count("Sandwich Leave Log", {"employee": employee.name}), 0)

	def test_live_run_cancels_leave_marks_attendance_and_logs(self):
		employee = self._new_employee()
		before = self._leave_application(employee, FRIDAY, FRIDAY)
		after = self._leave_application(employee, MONDAY, MONDAY)

		result = self._run_policy(FRIDAY, MONDAY, dry_run=0)

		self.assertFalse(result["dry_run"])
		self._block_for(result, employee.name)

		self.assertEqual(frappe.db.get_value("Leave Application", before.name, "docstatus"), 2)
		self.assertEqual(frappe.db.get_value("Leave Application", after.name, "docstatus"), 2)

		for date in (FRIDAY, SATURDAY, SUNDAY, MONDAY):
			status = frappe.db.get_value(
				"Attendance",
				{"employee": employee.name, "attendance_date": date, "docstatus": 1},
				"status",
			)
			self.assertEqual(status, "Absent", f"expected Absent on {date}")

		log_rows = frappe.get_all(
			"Sandwich Leave Log",
			filters={"employee": employee.name},
			fields=["date", "is_holiday_date"],
		)
		self.assertEqual(len(log_rows), 4)
		holiday_rows = {getdate(r.date) for r in log_rows if r.is_holiday_date}
		self.assertEqual(holiday_rows, {SATURDAY, SUNDAY})

	def test_a_failed_block_is_reported_and_does_not_abort_the_run(self):
		"""One employee's block failing partway through (simulated here inside
		Attendance.validate, i.e. after cancel_leave_application has already
		run and force_mark_absent is mid-write) must not crash the whole
		script, and must not be silently counted as applied -- it shows up in
		failed_blocks with its error, while a second, unrelated employee's
		block in the SAME run still succeeds normally. This is what the
		per-block try/except + commit is for: one bad employee out of many
		doesn't sink the whole run.

		Deliberately does NOT patch force_mark_absent (or any other whitelisted
		function the script reaches via frappe.call) directly: frappe.call's
		dispatch checks the resolved method by IDENTITY against the registry
		@frappe.whitelist() populated at decoration time -- swapping in a
		Mock/replacement function fails that identity check before the
		simulated failure logic ever runs, with a confusing unrelated error
		about the mock missing a __name__. Patching Attendance.validate -- an
		ordinary document lifecycle method, never whitelist-checked -- avoids
		that pitfall entirely, and only fires for the sandwich-created Absent
		record (guarded on custom_sandwich_policy_applied), not the ordinary
		"On Leave" Attendance the leave application submissions above create."""
		failing_employee = self._new_employee()
		self._leave_application(failing_employee, FRIDAY, FRIDAY)
		self._leave_application(failing_employee, MONDAY, MONDAY)

		ok_employee = self._new_employee()
		self._leave_application(ok_employee, FRIDAY, FRIDAY)
		self._leave_application(ok_employee, MONDAY, MONDAY)

		real_validate = Attendance.validate

		def _maybe_fail_validate(self):
			if self.employee == failing_employee.name and self.get("custom_sandwich_policy_applied"):
				raise RuntimeError("simulated failure")
			return real_validate(self)

		with patch.object(Attendance, "validate", _maybe_fail_validate):
			result = self._run_policy(FRIDAY, MONDAY, dry_run=0)

		self.assertFalse(any(b["employee"] == failing_employee.name for b in result["blocks"]))
		failed = [f for f in result["failed_blocks"] if f["employee"] == failing_employee.name]
		self.assertEqual(len(failed), 1)
		self.assertIn("simulated failure", failed[0]["error"])

		self._block_for(result, ok_employee.name)  # unaffected by the other employee's failure

	def test_a_clean_rerun_finds_nothing_left_to_do(self):
		"""Once a block is fully applied, its bridging leave applications are
		cancelled -- so a plain re-run over the same range no longer even sees
		this employee as a candidate (candidate lookup only considers Approved,
		docstatus=1 leave). That's a fine outcome (nothing left to double-charge),
		but it means this scenario alone doesn't exercise the Sandwich Leave Log
		skip path -- see test_partially_processed_block_is_skipped_on_rerun for
		the scenario that actually does."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		self._leave_application(employee, MONDAY, MONDAY)

		first = self._run_policy(FRIDAY, MONDAY, dry_run=0)
		self._block_for(first, employee.name)

		second = self._run_policy(FRIDAY, MONDAY, dry_run=0)

		self.assertFalse(any(b["employee"] == employee.name for b in second["blocks"]))
		self.assertFalse(any(s["employee"] == employee.name for s in second["skipped_already_processed"]))
		# Still exactly 4 log rows -- nothing extra got logged on the second call.
		self.assertEqual(frappe.db.count("Sandwich Leave Log", {"employee": employee.name}), 4)

	def test_partially_processed_block_is_skipped_on_rerun(self):
		"""The realistic case the Sandwich Leave Log's per-date check guards
		against: a prior run got interrupted after logging some dates of a
		block but before cancelling its leave applications (a crash mid-block,
		or two overlapping script runs racing) -- so the leave applications are
		still Approved/submitted, making this employee a candidate again, but
		the block must not be re-applied and re-logged a second time."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		self._leave_application(employee, MONDAY, MONDAY)

		frappe.get_doc(
			{
				"doctype": "Sandwich Leave Log",
				"employee": employee.name,
				"date": SATURDAY,
				"is_holiday_date": 1,
				"block_id": "test-partial-run",
			}
		).insert()

		result = self._run_policy(FRIDAY, MONDAY, dry_run=0)

		self.assertFalse(any(b["employee"] == employee.name for b in result["blocks"]))
		skipped_for_employee = [s for s in result["skipped_already_processed"] if s["employee"] == employee.name]
		self.assertEqual(len(skipped_for_employee), 1)
		# Leave applications are untouched -- the whole block was skipped, not
		# partially re-applied.
		self.assertEqual(
			frappe.db.get_value(
				"Leave Application",
				{"employee": employee.name, "from_date": FRIDAY, "to_date": FRIDAY},
				"docstatus",
			),
			1,
		)
		# Still just the one pre-seeded log row -- no new rows added for this block.
		self.assertEqual(frappe.db.count("Sandwich Leave Log", {"employee": employee.name}), 1)

	def test_disabled_policy_is_a_no_op(self):
		frappe.db.set_value("Policy Configuration", "Policy Configuration", "enable_sandwich_leave_policy", 0)
		self.addCleanup(
			lambda: frappe.db.set_value(
				"Policy Configuration", "Policy Configuration", "enable_sandwich_leave_policy", 1
			)
		)
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		self._leave_application(employee, MONDAY, MONDAY)

		result = self._run_policy(FRIDAY, MONDAY, dry_run=0)

		self.assertFalse(result["enabled"])
		self.assertEqual(frappe.db.count("Sandwich Leave Log", {"employee": employee.name}), 0)

	def test_permission_check_blocks_unprivileged_user(self):
		self.assertRaises(
			frappe.PermissionError,
			self._run_policy,
			FRIDAY, MONDAY, 1,
			user="Guest",
		)

	def test_permission_check_blocks_system_manager_role_holder(self):
		# The literal Administrator account only, not merely a System Manager or
		# HR Manager role -- a role check would let this user through, which is
		# exactly the gap this check exists to close.
		user = self.anchor.user_id
		if not user or user == "Administrator":
			raise unittest.SkipTest("Anchor employee has no non-Administrator user account")

		self.assertRaises(
			frappe.PermissionError,
			self._run_policy,
			FRIDAY, MONDAY, 1,
			user=user,
		)

	# ---- payroll.apply_sandwich_payment_days_adjustment --------------------

	def test_payroll_hook_reduces_payment_days_by_holiday_count_only(self):
		"""Deliberately a lightweight fake doc, not a real Salary Slip: this
		site's local Salary Structures are stale/empty (verified separately
		against the real hosted site), so exercising the whole Salary Slip
		calculation chain isn't reliable here. What's under test is the hook's
		own arithmetic and its use of calculate_net_pay() as the recompute
		step -- both fully exercised without a real structure."""
		employee = self._new_employee()
		self._leave_application(employee, FRIDAY, FRIDAY)
		self._leave_application(employee, MONDAY, MONDAY)
		self._run_policy(FRIDAY, MONDAY, dry_run=0)

		doc = SimpleNamespace(
			employee=employee.name,
			start_date=FRIDAY,
			end_date=MONDAY,
			payment_days=28,
			calculate_net_pay=lambda: None,
		)
		recompute_calls = []
		doc.calculate_net_pay = lambda: recompute_calls.append(True)

		payroll.apply_sandwich_payment_days_adjustment(doc)

		self.assertEqual(doc.payment_days, 26)  # 28 - 2 holiday-only days (Sat, Sun)
		self.assertEqual(doc.custom_sandwich_leave_days, 2)
		self.assertEqual(len(recompute_calls), 1)

	def test_payroll_hook_is_a_no_op_without_log_rows(self):
		employee = self._new_employee()
		doc = SimpleNamespace(
			employee=employee.name,
			start_date=FRIDAY,
			end_date=MONDAY,
			payment_days=28,
			calculate_net_pay=lambda: (_ for _ in ()).throw(AssertionError("should not recompute")),
		)

		payroll.apply_sandwich_payment_days_adjustment(doc)

		self.assertEqual(doc.payment_days, 28)
		self.assertFalse(hasattr(doc, "custom_sandwich_leave_days"))

	def test_payroll_hook_is_a_no_op_when_policy_disabled(self):
		frappe.db.set_value("Policy Configuration", "Policy Configuration", "enable_sandwich_leave_policy", 0)
		self.addCleanup(
			lambda: frappe.db.set_value(
				"Policy Configuration", "Policy Configuration", "enable_sandwich_leave_policy", 1
			)
		)
		employee = self._new_employee()
		doc = SimpleNamespace(
			employee=employee.name,
			start_date=FRIDAY,
			end_date=MONDAY,
			payment_days=28,
			calculate_net_pay=lambda: (_ for _ in ()).throw(AssertionError("should not recompute")),
		)

		payroll.apply_sandwich_payment_days_adjustment(doc)

		self.assertEqual(doc.payment_days, 28)
		self.assertFalse(hasattr(doc, "custom_sandwich_leave_days"))
