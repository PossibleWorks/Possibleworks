"""
Settings Helper
Provides access to Possibleworks Settings configuration.
"""

import frappe


class SettingsHelper:
    """
    Helper class to access Possibleworks Settings.
    """

    @staticmethod
    def get_client_environment() -> str:
        """
        Fetch environment from Possibleworks Settings.
        
        Returns:
            Environment name or "UNKNOWN"
        """
        try:
            env = frappe.db.get_single_value(
                "Possibleworks Settings",
                "environment"
            )

            if not env:
                return "UNKNOWN"

            return env

        except Exception as e:
            frappe.logger().warning(f"SettingsHelper: Error fetching environment: {str(e)}")
            return "UNKNOWN"

    @staticmethod
    def get_client_name() -> str:
        """
        Fetch client name from Possibleworks Settings.
        
        Returns:
            Client name or "UNKNOWN"
        """
        try:
            name = frappe.db.get_single_value(
                "Possibleworks Settings",
                "client_name"
            )

            if not name:
                return "UNKNOWN"

            return name

        except Exception as e:
            frappe.logger().warning(f"SettingsHelper: Error fetching client name: {str(e)}")
            return "UNKNOWN"

    @staticmethod
    def is_observer_enabled() -> bool:
        """
        Check if the observer system is enabled.
        
        Returns:
            True if enabled, False otherwise
        """
        try:
            enabled = frappe.db.get_single_value(
                "Possibleworks Settings",
                "enable_event_observer"
            )
            return bool(enabled)

        except Exception as e:
            frappe.logger().warning(f"SettingsHelper: Error checking observer enabled: {str(e)}")
            return False

    @staticmethod
    def get_webhook_url() -> str:
        """
        Get the external webhook URL for sending events.
        
        Returns:
            URL string or empty string
        """
        try:
            url = frappe.db.get_single_value(
                "Possibleworks Settings",
                "webhook_url"
            )
            return url or ""

        except Exception as e:
            frappe.logger().warning(f"SettingsHelper: Error fetching webhook URL: {str(e)}")
            return ""

    @staticmethod
    def get_batch_size() -> int:
        """
        Get the batch size for event processing.
        
        Returns:
            Batch size or default 100
        """
        try:
            size = frappe.db.get_single_value(
                "Possibleworks Settings",
                "batch_size"
            )
            return int(size) if size else 100

        except Exception:
            return 100

    @staticmethod
    def get_redis_enabled() -> bool:
        """
        Check if Redis buffering is enabled.
        
        Returns:
            True if Redis buffering is enabled
        """
        try:
            enabled = frappe.db.get_single_value(
                "Possibleworks Settings",
                "enable_redis_buffer"
            )
            return bool(enabled)

        except Exception:
            return True  # Default to True for safety