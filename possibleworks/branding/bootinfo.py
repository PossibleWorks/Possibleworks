# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Boot-time branding overrides for the desk."""

# What each installed app is called in the UI, keyed by app name.
#
# Two surfaces read a title, and neither can be reached through hooks:
#
#   * the sidebar subtitle under "Home" is bootinfo.app_data[].app_title, which boot.py
#     takes from the app's own `add_to_apps_screen` hook -- and that lookup is scoped to
#     the owning app (`get_hooks(..., app_name=...)`), so no other app can override it;
#
#   * the app launcher tiles are `Desktop Icon` rows, whose label is seeded once from the
#     `app_title` hook by create_desktop_icons_from_installed_apps() and never updated
#     afterwards. Renaming those rows in the database is the obvious move and the wrong
#     one: create_desktop_icons_from_workspace() finds an app's tile by
#     {"label": app_title}, so a renamed row would stop matching and every hrms workspace
#     added later would land on the launcher loose instead of grouped under its app.
#
# Rewriting the assembled bootinfo covers both without touching either mechanism.
#
# This does not reach the /apps screen, which builds its own list from
# frappe.apps.get_apps() -- sessions.py assembles that *after* extend_bootinfo runs.
APP_LABELS = {
	"erpnext": "noERP",
}


def override_app_titles(bootinfo):
	"""extend_bootinfo hook: relabel third-party apps across the desk."""
	for app in bootinfo.get("app_data") or []:
		label = APP_LABELS.get(app.get("app_name"))
		if label:
			app["app_title"] = label

	# Only the app-level tiles; the rest are workspaces and carry their own names.
	for icon in bootinfo.get("desktop_icons") or []:
		if icon.get("icon_type") != "App":
			continue
		label = APP_LABELS.get(icon.get("app"))
		if label:
			icon["label"] = label
