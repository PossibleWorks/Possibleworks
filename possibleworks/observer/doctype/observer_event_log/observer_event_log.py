# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder.functions import Now
from frappe.query_builder.custom import Interval


class ObserverEventLog(Document):
	"""
	Immutable log row for a single observer event.

	One row is created per event at the moment it is queued in Redis
	(or immediately dropped). The batch processor updates the row via
	db_set() — no full save, no validators re-running.

	Status flow:
	    Queued  → Sending → Sent      (happy path)
	    Queued  → Sending → Failed    (webhook rejected / timed out)
	    Queued  → Dropped             (payload could not be built — e.g. missing tenant_id)
	    Immediate delivery:
	    Sending → Sent / Failed       (no Queued step — fires directly)

	The `name` (hash) of this row is embedded inside the Redis payload
	under the key `_log_id`. The batch processor reads it to call db_set()
	without any SELECT query.
	"""

	@staticmethod
	def clear_old_logs(days: int = 30) -> None:
		"""
		Delete log rows older than `days`.

		Called by the daily retention scheduler when auto-expire is enabled
		in Possibleworks Settings. The `days` argument comes from the
		`log_retention_days` field on that settings doctype.

		Example hook in hooks.py scheduler_events:
		    "daily": ["possibleworks.observer.doctype.observer_event_log.observer_event_log.clear_old_logs"]
		"""
		table = frappe.qb.DocType("Observer Event Log")
		frappe.db.delete(
			table,
			filters=(table.creation < (Now() - Interval(days=days)))
		)
		frappe.logger().info(
			f"ObserverEventLog: Cleared logs older than {days} days"
		)
