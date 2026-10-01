# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json

import frappe
import requests

from possibleworks.branding.doctype.attendance_sync_settings.attendance_sync_settings import (
	AttendanceSyncSettings,
)

SYNC_SERVICE_TIMEOUT_SECONDS = 30


def _post_to_sync_service(url, payload, action_label):
	"""POST to the attendance sync service and surface a clear frappe.throw on
	any failure, instead of letting a raw requests exception or a silent
	non-2xx response reach the caller."""
	try:
		response = requests.post(url, json=payload, timeout=SYNC_SERVICE_TIMEOUT_SECONDS)
	except requests.exceptions.Timeout:
		frappe.logger().error(f"{action_label}: request to {url} timed out")
		frappe.throw("The attendance sync service did not respond in time. Try again shortly.")
	except requests.exceptions.ConnectionError:
		frappe.logger().error(f"{action_label}: could not connect to {url}")
		frappe.throw("Could not reach the attendance sync service. Check the configured Base URL.")

	if response.status_code >= 400:
		frappe.logger().error(f"{action_label}: {url} returned {response.status_code}: {response.text}")
		frappe.throw(f"Attendance sync service returned an error: {response.text}")

	return response.json()


def _as_list(value):
	if isinstance(value, str):
		value = json.loads(value)
	return value or []


@frappe.whitelist()
def get_configured_companies():
	"""Companies with a row in Attendance Sync Settings, for the Client
	checklist on the Attendance Reprocess page."""
	settings = frappe.get_single("Attendance Sync Settings")
	return [{"company": row.company, "client_name": row.client_name} for row in (settings.clients or []) if row.company]


def _resolve_client_for_companies(companies):
	"""All `companies` must resolve to the exact same client -- the sync
	service only accepts one clientName per call, so a mix of companies from
	different clients (e.g. a Nyas-GVS company alongside a Nyas-GRIET one) is
	rejected here rather than silently picking one and dropping the rest."""
	companies = _as_list(companies)
	if not companies:
		frappe.throw("Select at least one Company")

	clients = []
	for company in companies:
		client = AttendanceSyncSettings.get_client_for_company(company)
		if not client:
			frappe.throw(
				f"No attendance sync client is configured for company {company}. "
				"Add a row for it in Attendance Sync Settings first."
			)
		clients.append(client)

	distinct_names = {c["client_name"] for c in clients}
	if len(distinct_names) > 1:
		frappe.throw(
			"The selected companies map to different attendance sync clients "
			f"({', '.join(sorted(distinct_names))}). Select companies that all belong "
			"to the same client."
		)

	return clients[0]


@frappe.whitelist()
def trigger_attendance_sync(companies):
	"""Trigger a plain incremental sync (no date range, no userIds) against the
	always-on cron server for the client shared by `companies`."""
	client = _resolve_client_for_companies(companies)
	if not client["sync_base_url"]:
		frappe.throw(
			f"No Sync Server Base URL is configured for client {client['client_name']}. "
			"Add one in Attendance Sync Settings first."
		)

	url = f"{client['sync_base_url']}/api/sync-client"
	result = _post_to_sync_service(url, {"clientName": client["client_name"]}, "Attendance Sync")

	frappe.logger().info(f"Attendance Sync: triggered for client={client['client_name']}")

	return result


@frappe.whitelist()
def trigger_attendance_reprocess(companies, employees, from_date, to_date):
	"""Resolve the selected employees' biometric device IDs and trigger a
	reprocess on the attendance sync service instance shared by `companies`.

	`employees`/`companies` are JSON-encoded lists (as sent by the page) or
	already-decoded lists.
	"""
	employees = _as_list(employees)

	if not employees:
		frappe.throw("Select at least one employee")
	if not from_date or not to_date:
		frappe.throw("From Date and To Date are required")
	if from_date > to_date:
		frappe.throw("From Date must be before To Date")

	client = _resolve_client_for_companies(companies)

	employee_rows = frappe.get_all(
		"Employee",
		filters={"name": ["in", employees]},
		fields=["name", "employee_name", "attendance_device_id"],
	)

	missing = [row.employee_name or row.name for row in employee_rows if not row.attendance_device_id]
	if missing:
		frappe.throw(
			"These employees have no Attendance Device ID set, so their biometric "
			f"records can't be reprocessed: {', '.join(missing)}. "
			"Set it on the Employee record first, or remove them from the selection."
		)

	user_ids = [row.attendance_device_id for row in employee_rows]

	url = f"{client['reprocess_base_url']}/api/reprocess-attendance-client"
	payload = {
		"clientName": client["client_name"],
		"fromDate": from_date,
		"toDate": to_date,
		"userIds": user_ids,
	}

	result = _post_to_sync_service(url, payload, "Attendance Reprocess")

	frappe.logger().info(
		f"Attendance Reprocess: triggered for client={client['client_name']} "
		f"employees={len(user_ids)} from={from_date} to={to_date}"
	)

	return result
