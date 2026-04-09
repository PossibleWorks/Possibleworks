"""
Settings Helper
Provides access to Possibleworks Settings configuration.

Webhook URL is per environment (each environment has its own URL).
This app expects webhook URLs to be configured ONLY in `sites/common_site_config.json`:
- possibleworks_webhook_url_dev
- possibleworks_webhook_url_uat
- possibleworks_webhook_url_prod
"""

import frappe
from .constants import BATCH_SIZE

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
    def _get_webhook_url_from_env_or_conf(environment: str) -> str:
        """
        Fetch webhook URL from `sites/common_site_config.json` and throw if missing.
        """
        env = (environment or "").strip().upper()
        if env not in {"DEV", "UAT", "PROD"}:
            frappe.throw(
                f"Possibleworks webhook not configured: unknown environment '{environment}'. "
                "Expected one of: DEV, UAT, PROD."
            )

        conf_key = f"possibleworks_webhook_url_{env.lower()}"
        common = frappe.get_common_site_config(cached=True) or {}
        url = common.get(conf_key) or ""
        if not isinstance(url, str) or not url.strip():
            frappe.throw(
                f"Missing `{conf_key}` in sites/common_site_config.json for environment {env}."
            )
        return url.strip()

    @staticmethod
    def get_webhook_url() -> str:
        """
        Get the external webhook URL for sending events.
        Reads per-environment URL ONLY from `sites/common_site_config.json` and throws if not set.
        
        Returns:
            URL string (never empty; throws if missing)
        """
        environment = SettingsHelper.get_client_environment()
        return SettingsHelper._get_webhook_url_from_env_or_conf(environment)

    @staticmethod
    def get_batch_size() -> int:
        """
        Get the batch size for event processing.
        
        Returns:
            Batch size or default 100
        """
        try:
            # size = frappe.db.get_single_value(
            #     "Possibleworks Settings",
            #     "batch_size"
            # )
            # return int(size) if size else 100
            return BATCH_SIZE

        except Exception:
            return 100