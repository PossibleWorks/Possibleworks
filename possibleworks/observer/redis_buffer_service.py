"""
Redis Buffer Service
Manages event buffering in Redis to prevent API overload.
"""

import frappe
import json
from typing import Optional, List, Dict
from datetime import datetime, timezone
from .constants import REDIS_QUEUE_KEY, REDIS_PREFIX
from .settings_helper import SettingsHelper


class RedisBufferService:
    """
    Service to buffer events in Redis.
    
    Instead of sending events immediately to external API,
    we queue them in Redis to:
    1. Prevent API overload
    2. Batch process events
    3. Handle spikes gracefully
    4. Allow deduplication
    
    Redis structure:
    possibleworks:event:queue = [event1, event2, event3, ...]
    """

    @staticmethod
    def push_event(event_payload: Dict) -> bool:
        """
        Push an event to Redis queue.
        
        Args:
            event_payload: The JSON payload to queue
            
        Returns:
            True if successful, False otherwise
        """
        try:

            # Get Redis connection
            redis_client = frappe.cache()
            if not redis_client:
                frappe.logger().error("RedisBufferService: Redis connection not available")
                return False

            # Convert payload to JSON
            event_json = json.dumps(event_payload, default=str)

            # Push to queue (RPUSH = right push, adds to end of list)
            result = redis_client.lpush(REDIS_QUEUE_KEY, event_json)

            frappe.logger().debug(
                f"RedisBufferService: Pushed event to queue. Queue size: {result}"
            )

            return True

        except Exception as e:
            frappe.logger().error(
                f"RedisBufferService: Error pushing event to Redis: {str(e)}"
            )
            return False

    @staticmethod
    def get_queue_size() -> int:
        """
        Get current queue size.
        
        Returns:
            Number of events in queue
        """
        try:
            redis_client = frappe.cache()
            if not redis_client:
                return 0

            size = redis_client.llen(REDIS_QUEUE_KEY)
            return size or 0

        except Exception as e:
            frappe.logger().warning(
                f"RedisBufferService: Error getting queue size: {str(e)}"
            )
            return 0

    @staticmethod
    def pop_batch(batch_size: int) -> List[Dict]:
        """
        Pop a batch of events from queue for processing.
        
        Uses RPOP to get events from the right side (FIFO order).
        
        Args:
            batch_size: Number of events to pop
            
        Returns:
            List of event payloads
        """
        try:
            redis_client = frappe.cache()
            if not redis_client:
                return []

            events = []
            
            # Pop events one by one
            for _ in range(batch_size):
                event_json = redis_client.rpop(REDIS_QUEUE_KEY)
                
                if not event_json:
                    break

                try:
                    event = json.loads(event_json)
                    events.append(event)
                except json.JSONDecodeError:
                    frappe.logger().warning(
                        f"RedisBufferService: Invalid JSON in queue: {event_json}"
                    )
                    continue

            frappe.logger().debug(
                f"RedisBufferService: Popped {len(events)} events from queue"
            )

            return events

        except Exception as e:
            frappe.logger().error(
                f"RedisBufferService: Error popping batch: {str(e)}"
            )
            return []

    @staticmethod
    def clear_queue() -> bool:
        """
        Clear all events from the queue.
        
        Useful for resetting during testing or errors.
        
        Returns:
            True if successful
        """
        try:
            redis_client = frappe.cache()
            if not redis_client:
                return False

            redis_client.delete(REDIS_QUEUE_KEY)
            frappe.logger().info("RedisBufferService: Queue cleared")
            return True

        except Exception as e:
            frappe.logger().error(
                f"RedisBufferService: Error clearing queue: {str(e)}"
            )
            return False

    @staticmethod
    def get_queue_stats() -> Dict:
        """
        Get queue statistics for monitoring.
        
        Returns:
            {
                "queue_size": 150,
                "queue_key": "possibleworks:event:queue"
            }
        """
        try:
            redis_client = frappe.cache()
            if not redis_client:
                return {"error": "Redis not available"}

            size = redis_client.llen(REDIS_QUEUE_KEY) or 0

            return {
                "queue_size": size,
                "queue_key": REDIS_QUEUE_KEY,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

        except Exception as e:
            frappe.logger().warning(
                f"RedisBufferService: Error getting queue stats: {str(e)}"
            )
            return {"error": str(e)}

    @staticmethod
    def dequeue_by_filter(filter_func) -> List[Dict]:
        """
        Pop events that match a filter function.
        
        Useful for removing duplicates or specific events.
        
        Args:
            filter_func: Function that returns True if event should be removed
            
        Returns:
            List of removed events
        """
        try:
            redis_client = frappe.cache()
            if not redis_client:
                return []

            # Get all events (this is read-only)
            queue_size = redis_client.llen(REDIS_QUEUE_KEY) or 0
            all_events = []
            
            for _ in range(queue_size):
                event_json = redis_client.rpop(REDIS_QUEUE_KEY)
                if event_json:
                    all_events.append(event_json)

            # Filter and re-push
            removed = []
            for event_json in all_events:
                try:
                    event = json.loads(event_json)
                    if filter_func(event):
                        removed.append(event)
                    else:
                        # Re-push events we want to keep
                        redis_client.lpush(REDIS_QUEUE_KEY, event_json)
                except json.JSONDecodeError:
                    redis_client.lpush(REDIS_QUEUE_KEY, event_json)

            frappe.logger().debug(
                f"RedisBufferService: Filtered queue, removed {len(removed)} events"
            )

            return removed

        except Exception as e:
            frappe.logger().error(
                f"RedisBufferService: Error filtering queue: {str(e)}"
            )
            return []