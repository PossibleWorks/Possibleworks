"""
Batch Processor
Processes batches of events from Redis and sends to external API.

This is meant to run periodically (e.g., every 10 seconds via Frappe task).
"""

import frappe
import json
import requests
import time
from typing import List, Dict, Tuple
from datetime import datetime, timezone
from .redis_buffer_service import RedisBufferService
from .settings_helper import SettingsHelper


class BatchProcessor:
	"""
	Processor that:
	1. Reads events from Redis
	2. Batches them
	3. Sends to external webhook
	4. Removes processed events

	This prevents individual API calls for each event and instead
	sends them in batches.

	Example: 1000 events → 10 API calls (100 events each)
	"""

	@staticmethod
	def process_batch() -> Dict:
		"""
		Main batch processing function.

		Returns:
			{
				"status": "success" | "error",
				"events_processed": 150,
				"message": "..."
			}
		"""
		try:
			url = SettingsHelper.get_webhook_url()
			webhook_url = f"{url}/frappe-user/workflow-events"
			if not webhook_url:
				frappe.logger().warning(
					"BatchProcessor: Webhook URL not configured in settings"
				)
				return {
					"status": "error",
					"events_processed": 0,
					"message": "Webhook URL not configured"
				}

			batch_size = SettingsHelper.get_batch_size()
			events = RedisBufferService.pop_batch(batch_size)

			if not events:
				return {
					"status": "success",
					"events_processed": 0,
					"message": "No events in queue"
				}

			frappe.logger().info(
				f"BatchProcessor: Processing batch of {len(events)} events"
			)

			# Extract log IDs and strip _log_id from events before sending to webhook
			log_ids = [event.pop("_log_id", None) for event in events]

			# Generate a batch ID to group these events in the log
			batch_id = frappe.generate_hash(length=10)

			# Mark all log rows as Sending
			from frappe.utils import now_datetime
			now = now_datetime()
			for log_id in log_ids:
				if log_id:
					frappe.db.set_value("Observer Event Log", log_id, {
						"status": "Sending",
						"batch_id": batch_id,
						"last_attempted_at": now,
					}, update_modified=False)
			if any(log_ids):
				frappe.db.commit()

			success, sent_count, response_code, response_body, error_message = BatchProcessor._send_to_webhook(
				webhook_url=webhook_url,
				events=events
			)

			if success:
				sent_at = now_datetime()
				for log_id in log_ids:
					if log_id:
						frappe.db.set_value("Observer Event Log", log_id, {
							"status": "Sent",
							"sent_at": sent_at,
							"response_code": response_code,
							"response_body": (response_body or "")[:2000],
						}, update_modified=False)
				if any(log_ids):
					frappe.db.commit()

				return {
					"status": "success",
					"events_processed": sent_count,
					"timestamp": datetime.now(timezone.utc).isoformat()
				}
			else:
				# Re-embed _log_id before re-queuing so the next attempt can still find the row
				for event, log_id in zip(events, log_ids):
					if log_id:
						event["_log_id"] = log_id
					RedisBufferService.push_event(event)

				# Update rows to Failed and increment retry_count
				for log_id in log_ids:
					if log_id:
						current_retry = frappe.db.get_value("Observer Event Log", log_id, "retry_count") or 0
						frappe.db.set_value("Observer Event Log", log_id, {
							"status": "Failed",
							"retry_count": current_retry + 1,
							"last_attempted_at": now_datetime(),
							"error_message": error_message or "Failed to send to webhook",
						}, update_modified=False)
				if any(log_ids):
					frappe.db.commit()

				return {
					"status": "error",
					"events_processed": 0,
					"message": "Failed to send to webhook, events re-queued"
				}

		except Exception as e:
			frappe.logger().error(
				f"BatchProcessor: Error in process_batch: {str(e)}"
			)
			return {
				"status": "error",
				"message": str(e)
			}

	@staticmethod
	def _send_to_webhook(webhook_url: str, events: List[Dict]) -> Tuple[bool, int, "int | None", "str | None", "str | None"]:
		"""
		Send events to external webhook.

		Args:
			webhook_url: External API endpoint
			events: List of event payloads (must already have _log_id stripped)

		Returns:
			(success, sent_count, response_code, response_body, error_message)
		"""
		try:
			payload = {
				"timestamp": datetime.now(timezone.utc).isoformat(),
				"batch_count": len(events),
				"events": events
			}

			headers = {
				"Content-Type": "application/json",
				"User-Agent": "Frappe-Possibleworks/1.0"
			}

			time.sleep(2)

			response = requests.post(
				webhook_url,
				json=payload,
				headers=headers,
				timeout=30
			)
			frappe.logger().info(f"BatchProcessor: Response: {response.text}")

			if response.status_code in [200, 201, 202]:
				frappe.logger().info(
					f"BatchProcessor: Sent {len(events)} events to {webhook_url}. "
					f"Status: {response.status_code}"
				)
				return True, len(events), response.status_code, response.text, None
			else:
				frappe.logger().error(
					f"BatchProcessor: Webhook returned {response.status_code}. "
					f"Response: {response.text}"
				)
				return False, 0, response.status_code, response.text, f"Webhook returned {response.status_code}"

		except requests.exceptions.Timeout:
			frappe.logger().error("BatchProcessor: Webhook request timed out")
			return False, 0, None, None, "Request timed out after 30s"

		except requests.exceptions.ConnectionError:
			frappe.logger().error("BatchProcessor: Failed to connect to webhook")
			return False, 0, None, None, "Connection error — could not reach webhook"

		except Exception as e:
			frappe.logger().error(
				f"BatchProcessor: Error sending to webhook: {str(e)}"
			)
			return False, 0, None, None, str(e)

	@staticmethod
	def get_status() -> Dict:
		"""
		Get current batch processor status.

		Returns:
			Status information
		"""
		try:
			queue_stats = RedisBufferService.get_queue_stats()
			webhook_url = SettingsHelper.get_webhook_url()

			return {
				"webhook_configured": bool(webhook_url),
				"queue_stats": queue_stats,
				"timestamp": datetime.now(timezone.utc).isoformat()
			}

		except Exception as e:
			return {"error": str(e)}


# ============================================================================
# Frappe task functions
# ============================================================================

def process_event_batch():
	"""
	Frappe task function to process batch.

	Add to hooks.py:
	scheduler_events = {
		"all": [
			"possibleworks.observer.batch_processor.process_event_batch"
		]
	}
	"""
	result = BatchProcessor.process_batch()
	frappe.logger().info(f"Batch processing result: {result}")
	return result


def send_single_event(payload: dict):
	"""
	Frappe background job for immediate event delivery (IMMEDIATE_SEND_DOCTYPES only).

	Creates the Observer Event Log row at the START of this job (not in the
	observer hook) so INSERT and subsequent UPDATEs live in the same worker
	transaction — eliminates the snapshot-isolation race (error 1020) that
	occurred when the observer inserted the row mid-request and this job updated
	it before the main transaction committed.
	"""
	import json
	from frappe.utils import now_datetime

	# Strip internal metadata fields before sending to webhook
	log_id = payload.pop("_log_id", None)      # backward-compat: old jobs may carry this
	log_meta = payload.pop("_log_meta", None)  # new format: metadata for log row creation

	# Create the log row here, in this worker's own transaction.
	if log_meta and not log_id:
		try:
			log = frappe.get_doc({
				"doctype": "Observer Event Log",
				"status": "Sending",
				"payload": json.dumps(payload, default=str),
				"queued_at": now_datetime(),
				**log_meta,
			})
			log.insert(ignore_permissions=True)
			log_id = log.name
			frappe.db.commit()
		except Exception as exc:
			frappe.logger().error(f"send_single_event: Failed to create log row: {exc}")

	try:
		url = SettingsHelper.get_webhook_url()
		webhook_url = f"{url}/frappe-user/workflow-events"

		body = {"events": [payload]}

		headers = {
			"Content-Type": "application/json",
			"User-Agent": "Frappe-Possibleworks/1.0",
		}

		response = requests.post(webhook_url, json=body, headers=headers, timeout=30)
		frappe.logger().info(f"send_single_event: Response: {response.text}")

		if response.status_code in [200, 201, 202]:
			if log_id:
				frappe.db.set_value("Observer Event Log", log_id, {
					"status": "Sent",
					"sent_at": now_datetime(),
					"response_code": response.status_code,
					"response_body": response.text[:2000],
				}, update_modified=False)
				frappe.db.commit()
		else:
			frappe.logger().error(
				f"send_single_event: Webhook returned {response.status_code} "
				f"doctype={payload.get('document', {}).get('doctype')} "
				f"name={payload.get('document', {}).get('name')}"
			)
			if log_id:
				frappe.db.set_value("Observer Event Log", log_id, {
					"status": "Failed",
					"response_code": response.status_code,
					"error_message": f"Webhook returned {response.status_code}: {response.text[:500]}",
				}, update_modified=False)
				frappe.db.commit()

	except requests.exceptions.Timeout:
		frappe.logger().error(
			f"send_single_event: Request timed out "
			f"doctype={payload.get('document', {}).get('doctype')} "
			f"name={payload.get('document', {}).get('name')}"
		)
		if log_id:
			frappe.db.set_value("Observer Event Log", log_id, {
				"status": "Failed",
				"error_message": "Request timed out after 30s",
			}, update_modified=False)
			frappe.db.commit()
	except requests.exceptions.ConnectionError:
		frappe.logger().error(
			f"send_single_event: Failed to connect to webhook "
			f"doctype={payload.get('document', {}).get('doctype')} "
			f"name={payload.get('document', {}).get('name')}"
		)
		if log_id:
			frappe.db.set_value("Observer Event Log", log_id, {
				"status": "Failed",
				"error_message": "Connection error — could not reach webhook",
			}, update_modified=False)
			frappe.db.commit()
	except Exception as e:
		frappe.logger().error(f"send_single_event: Unexpected error: {str(e)}")
		if log_id:
			frappe.db.set_value("Observer Event Log", log_id, {
				"status": "Failed",
				"error_message": f"Unexpected error: {str(e)[:500]}",
			}, update_modified=False)
			frappe.db.commit()
