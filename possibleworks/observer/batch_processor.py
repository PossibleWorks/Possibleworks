"""
Batch Processor
Processes batches of events from Redis and sends to external API.

This is meant to run periodically (e.g., every 10 seconds via Frappe task).
"""

import frappe
import json
import requests
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
                "batches_sent": 2,
                "message": "..."
            }
        """
        try:
            # Check if webhook is configured
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

            # Get batch size from settings
            batch_size = SettingsHelper.get_batch_size()

            # Pop events from Redis
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

            # Send to external API
            success, sent_count = BatchProcessor._send_to_webhook(
                webhook_url=webhook_url,
                events=events
            )

            if success:
                return {
                    "status": "success",
                    "events_processed": sent_count,
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
            else:
                # Re-queue events if sending failed
                for event in events:
                    RedisBufferService.push_event(event)

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
    def _send_to_webhook(webhook_url: str, events: List[Dict]) -> Tuple[bool, int]:
        """
        Send events to external webhook.
        
        Args:
            webhook_url: External API endpoint
            events: List of event payloads
            
        Returns:
            (success: bool, sent_count: int)
        """
        try:
            # Prepare payload
            payload = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "batch_count": len(events),
                "events": events
            }

            headers = {
                "Content-Type": "application/json",
                "User-Agent": "Frappe-Possibleworks/1.0"
            }

            # Send POST request
            response = requests.post(
                webhook_url,
                json=payload,
                headers=headers,
                timeout=30
            )
            frappe.logger().info(f"BatchProcessor: Response: {response.text}")

            # Check response
            if response.status_code in [200, 201, 202]:
                frappe.logger().info(
                    f"BatchProcessor: Sent {len(events)} events to {webhook_url}. "
                    f"Status: {response.status_code}"
                )
                return True, len(events)
            else:
                frappe.logger().error(
                    f"BatchProcessor: Webhook returned {response.status_code}. "
                    f"Response: {response.text}"
                )
                return False, 0

        except requests.exceptions.Timeout:
            frappe.logger().error("BatchProcessor: Webhook request timed out")
            return False, 0

        except requests.exceptions.ConnectionError:
            frappe.logger().error("BatchProcessor: Failed to connect to webhook")
            return False, 0

        except Exception as e:
            frappe.logger().error(
                f"BatchProcessor: Error sending to webhook: {str(e)}"
            )
            return False, 0

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