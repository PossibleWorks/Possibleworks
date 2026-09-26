# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

SETTINGS_DOCTYPE = "Attendance Sync Settings"


class AttendanceSyncSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from possibleworks.branding.doctype.attendance_sync_client.attendance_sync_client import (
			AttendanceSyncClient,
		)

		clients: DF.Table[AttendanceSyncClient]
	# end: auto-generated types

	def validate(self):
		self.ensure_unique_companies()

	def ensure_unique_companies(self):
		"""A Company must resolve to exactly one client — otherwise a reprocess
		trigger has no way to pick which sync service instance to call."""
		seen = set()
		for row in self.clients or []:
			if not row.company:
				continue
			if row.company in seen:
				frappe.throw(
					f"Company {row.company} is mapped more than once in Attendance Sync "
					"Clients. Each Company must map to exactly one client."
				)
			seen.add(row.company)

	@staticmethod
	def get_client_for_company(company):
		"""Return {client_name, reprocess_base_url, sync_base_url} for a Company,
		or None if unmapped. sync_base_url may be None if that row hasn't set it."""
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		for row in settings.clients or []:
			if row.company == company:
				return {
					"client_name": row.client_name,
					"reprocess_base_url": row.reprocess_base_url.rstrip("/"),
					"sync_base_url": row.sync_base_url.rstrip("/") if row.sync_base_url else None,
				}
		return None
