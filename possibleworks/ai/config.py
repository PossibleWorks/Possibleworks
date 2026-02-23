# Copyright (c) 2026, Possibleworks
# For license information, please see license.txt

"""
Config API – returns the list of AI-enabled doctypes to the client JS at boot time.
"""

import frappe


def add_to_boot(bootinfo):
	"""Called via boot_session hook — embeds AI config into frappe.boot.
	
	This makes the config available synchronously via frappe.boot.pw_ai_doctypes,
	which the client JS reads immediately without needing an async server call.
	"""
	try:
		bootinfo.pw_ai_doctypes = _get_enabled()
	except Exception:
		bootinfo.pw_ai_doctypes = {}


@frappe.whitelist()
def get_enabled_doctypes() -> dict:
	"""Whitelisted API — returns enabled doctypes. Used as fallback."""
	return _get_enabled()


def _get_enabled() -> dict:
	"""Core logic: reads PW AI Settings and returns enabled doctype config dict."""
	try:
		settings = frappe.get_single("PW AI Settings")
		api_key = settings.get_password("openai_api_key", raise_exception=False)
		if not api_key:
			return {}
	except Exception:
		return {}

	result = {}
	for row in settings.get("doctype_config") or []:
		if row.is_enabled and row.doctype_name:
			result[row.doctype_name] = {
				"button_label": row.button_label or "",
				"custom_prompt": row.extraction_prompt or "",
			}
	return result
