# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for the Attendance Reprocess page's trigger_attendance_reprocess whitelisted
method and Attendance Sync Settings' company -> client resolution.

The external attendance sync service is mocked (requests.post) -- these tests verify
company/client resolution and the attendance_device_id guard, never a real network call.

NOTE ON ISOLATION: IntegrationTestCase only rolls the database back once, at class
teardown, not between individual test methods (see
possibleworks/test_employee.py for the same caveat). Attendance Sync Settings is a
Single, so every test that mutates it restores the original `clients` rows itself in a
`finally` block instead of relying on that rollback, so test order never matters.

NOTE ON FIXTURES: no new Employee records are created here -- this site's Employee
validations (mandatory reports_to, linked user account; see test_employee.py) make
fabricating one non-trivial for no real benefit. Both the "has a device id" and
"missing a device id" cases are found among existing Employees instead; a test using
either case is skipped if this site has none, rather than fabricated.
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from possibleworks.branding.doctype.attendance_sync_settings.attendance_sync_settings import (
	AttendanceSyncSettings,
)
from possibleworks.branding.page.attendance_reprocess.attendance_reprocess import (
	_resolve_client_for_companies,
	get_configured_companies,
	trigger_attendance_reprocess,
	trigger_attendance_sync,
)


class TestAttendanceReprocess(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")

	def _with_clients_mapping(self, rows, fn):
		"""Temporarily replace any existing mapping for each row's company with
		`rows` (a list of dicts with company/client_name/reprocess_base_url/
		sync_base_url), run `fn`, then restore Attendance Sync Settings to its
		prior state -- regardless of whether `fn` raises."""
		settings = frappe.get_single("Attendance Sync Settings")
		original_rows = [dict(row.as_dict()) for row in (settings.clients or [])]
		companies = {r["company"] for r in rows}

		settings.set(
			"clients",
			[r for r in original_rows if r.get("company") not in companies] + rows,
		)
		settings.save(ignore_permissions=True)
		try:
			return fn()
		finally:
			settings = frappe.get_single("Attendance Sync Settings")
			settings.set("clients", original_rows)
			settings.save(ignore_permissions=True)

	def _with_client_mapping(self, company, client_name, reprocess_base_url, fn, sync_base_url=None):
		"""Single-company convenience wrapper around `_with_clients_mapping`."""
		return self._with_clients_mapping(
			[
				{
					"company": company,
					"client_name": client_name,
					"reprocess_base_url": reprocess_base_url,
					"sync_base_url": sync_base_url,
				}
			],
			fn,
		)

	def test_get_client_for_company_returns_mapping(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("Need at least one Company on this site")
		company = company[0]

		def _check():
			result = AttendanceSyncSettings.get_client_for_company(company)
			self.assertEqual(result["client_name"], "Test Client")
			self.assertEqual(result["reprocess_base_url"], "https://example.erwrds.com")
			self.assertEqual(result["sync_base_url"], "https://cron.example.erwrds.com")

		self._with_client_mapping(
			company,
			"Test Client",
			"https://example.erwrds.com/",
			_check,
			sync_base_url="https://cron.example.erwrds.com/",
		)

	def test_get_client_for_company_sync_base_url_is_none_when_unset(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("Need at least one Company on this site")
		company = company[0]

		def _check():
			result = AttendanceSyncSettings.get_client_for_company(company)
			self.assertIsNone(result["sync_base_url"])

		self._with_client_mapping(company, "Test Client", "https://example.erwrds.com", _check)

	def test_get_client_for_company_returns_none_when_unmapped(self):
		company = frappe.get_all(
			"Company", filters={"name": ["not in", self._mapped_companies()]}, limit=1, pluck="name"
		)
		if not company:
			raise unittest.SkipTest("Every Company on this site already has a client mapping")

		result = AttendanceSyncSettings.get_client_for_company(company[0])
		self.assertIsNone(result)

	def _mapped_companies(self):
		settings = frappe.get_single("Attendance Sync Settings")
		return [row.company for row in (settings.clients or [])] or [""]

	def test_trigger_blocks_employees_missing_device_id(self):
		employee = frappe.get_all(
			"Employee",
			filters={"attendance_device_id": ("in", ["", None])},
			fields=["name", "company"],
			limit=1,
		)
		if not employee:
			raise unittest.SkipTest("Need an Employee with no Attendance Device ID on this site")
		employee = employee[0]

		def _check():
			with self.assertRaises(frappe.ValidationError):
				trigger_attendance_reprocess(
					companies=[employee.company],
					employees=[employee.name],
					from_date="2026-06-01",
					to_date="2026-06-02",
				)

		self._with_client_mapping(employee.company, "Test Client", "https://example.erwrds.com", _check)

	def test_trigger_posts_resolved_device_ids(self):
		employee = frappe.get_all(
			"Employee",
			filters={"attendance_device_id": ("is", "set")},
			fields=["name", "company", "attendance_device_id"],
			limit=1,
		)
		if not employee:
			raise unittest.SkipTest("Need an Employee with an Attendance Device ID set on this site")
		employee = employee[0]

		def _check():
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_response.json.return_value = {"status": "started"}

			with patch("possibleworks.branding.page.attendance_reprocess.attendance_reprocess.requests.post", return_value=mock_response) as mock_post:
				result = trigger_attendance_reprocess(
					companies=[employee.company],
					employees=[employee.name],
					from_date="2026-06-01",
					to_date="2026-06-02",
				)

			self.assertEqual(result, {"status": "started"})
			_, kwargs = mock_post.call_args
			self.assertEqual(kwargs["json"]["clientName"], "Test Client")
			self.assertEqual(kwargs["json"]["userIds"], [employee.attendance_device_id])

		self._with_client_mapping(employee.company, "Test Client", "https://example.erwrds.com", _check)

	def test_trigger_sync_blocks_when_sync_base_url_unset(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("Need at least one Company on this site")
		company = company[0]

		def _check():
			with self.assertRaises(frappe.ValidationError):
				trigger_attendance_sync(companies=[company])

		# No sync_base_url passed -- this is the "not configured yet" case.
		self._with_client_mapping(company, "Test Client", "https://example.erwrds.com", _check)

	def test_trigger_sync_posts_client_name(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("Need at least one Company on this site")
		company = company[0]

		def _check():
			mock_response = MagicMock()
			mock_response.status_code = 200
			mock_response.json.return_value = {"status": "started"}

			with patch(
				"possibleworks.branding.page.attendance_reprocess.attendance_reprocess.requests.post",
				return_value=mock_response,
			) as mock_post:
				result = trigger_attendance_sync(companies=[company])

			self.assertEqual(result, {"status": "started"})
			args, kwargs = mock_post.call_args
			self.assertEqual(args[0], "https://cron.example.erwrds.com/api/sync-client")
			self.assertEqual(kwargs["json"], {"clientName": "Test Client"})

		self._with_client_mapping(
			company,
			"Test Client",
			"https://example.erwrds.com",
			_check,
			sync_base_url="https://cron.example.erwrds.com",
		)

	def test_get_configured_companies_lists_mapped_rows(self):
		company = frappe.get_all("Company", limit=1, pluck="name")
		if not company:
			raise unittest.SkipTest("Need at least one Company on this site")
		company = company[0]

		def _check():
			result = get_configured_companies()
			self.assertIn({"company": company, "client_name": "Test Client"}, result)

		self._with_client_mapping(company, "Test Client", "https://example.erwrds.com", _check)

	def test_resolve_client_allows_companies_sharing_one_client(self):
		"""Several companies syncing under the same attendance sync client
		(e.g. GRIET/GRCP/GLEC all under "attendance_griet") must resolve
		together without error -- this is the whole point of letting the page
		select more than one company at once."""
		companies = frappe.get_all("Company", limit=2, pluck="name")
		if len(companies) < 2:
			raise unittest.SkipTest("Need at least two Companies on this site")

		def _check():
			result = _resolve_client_for_companies(companies)
			self.assertEqual(result["client_name"], "Shared Client")

		self._with_clients_mapping(
			[
				{
					"company": c,
					"client_name": "Shared Client",
					"reprocess_base_url": "https://example.erwrds.com",
					"sync_base_url": None,
				}
				for c in companies
			],
			_check,
		)

	def test_resolve_client_rejects_companies_from_different_clients(self):
		companies = frappe.get_all("Company", limit=2, pluck="name")
		if len(companies) < 2:
			raise unittest.SkipTest("Need at least two Companies on this site")

		def _check():
			with self.assertRaises(frappe.ValidationError):
				_resolve_client_for_companies(companies)

		self._with_clients_mapping(
			[
				{
					"company": companies[0],
					"client_name": "Client A",
					"reprocess_base_url": "https://a.erwrds.com",
					"sync_base_url": None,
				},
				{
					"company": companies[1],
					"client_name": "Client B",
					"reprocess_base_url": "https://b.erwrds.com",
					"sync_base_url": None,
				},
			],
			_check,
		)
