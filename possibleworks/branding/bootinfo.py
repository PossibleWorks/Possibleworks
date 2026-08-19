# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Boot-time branding overrides for the desk sidebar."""

# The subtitle under "Home" in the sidebar is `bootinfo.app_data[].app_title`,
# which boot.py reads from each app's own `add_to_apps_screen` hook. That lookup is
# scoped to the owning app (`get_hooks(..., app_name=...)`), so no other app can
# override it through hooks -- rewriting the assembled bootinfo is the only place a
# white-label app gets a say.
#
# This covers the desk sidebar only. The /apps launcher builds its own list from
# frappe.apps.get_apps(), which sessions.py assembles *after* extend_bootinfo runs.
APP_TITLE_OVERRIDES = {
	"erpnext": "noERP",
}


def override_app_titles(bootinfo):
	"""extend_bootinfo hook: relabel third-party apps in the sidebar."""
	for app in bootinfo.get("app_data") or []:
		title = APP_TITLE_OVERRIDES.get(app.get("app_name"))
		if title:
			app["app_title"] = title
