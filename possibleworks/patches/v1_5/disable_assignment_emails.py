# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Stop Frappe emailing task-assignment notifications.

Assignments now reach the assignee as a PossibleWorks chat tile, so Frappe's own
email is a duplicate -- including the "your assignment has been removed" one that
fires when our Submit marks the onboarding tasks Completed.

`Notification Settings.enable_email_assignment` is the supported lever: it gates
the email for both directions (NotificationLog.after_insert consults it for every
log of type "Assignment") and leaves the in-desk notification bell working.

Both halves are needed. `create_notification_settings` copies the field default
when a user is created, and `is_email_notifications_enabled_for_type` treats a
missing value as enabled -- so the default covers future users and the bulk update
covers the rows already there.
"""

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

DOCTYPE = "Notification Settings"
FIELDNAME = "enable_email_assignment"


def execute():
	make_property_setter(
		DOCTYPE,
		FIELDNAME,
		"default",
		"0",
		"Text",
		validate_fields_for_doctype=False,
	)

	frappe.db.set_value(
		DOCTYPE,
		{FIELDNAME: 1},
		FIELDNAME,
		0,
		update_modified=False,
	)

	frappe.clear_cache(doctype=DOCTYPE)
