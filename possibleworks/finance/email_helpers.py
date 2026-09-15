"""
Jinja-callable helpers for Notification (Email Alert) templates.

Registered via hooks.py's `jinja.methods`, so these become available as plain
function calls inside any Notification's subject/message field -- the same
mechanism this app already uses for Employee letter templates
(possibleworks.hr_documents.letters.utils).
"""

import frappe
from frappe.integrations.utils import make_get_request
from frappe.workflow.doctype.workflow_action.workflow_action import get_workflow_action_url

from possibleworks.observer.settings_helper import SettingsHelper


def get_approve_link(doc, action, user):
	"""Return a real, signed one-click action URL for `action` on `doc`.

	Clicking it takes `user` through Frappe's own confirm-then-apply workflow
	action flow (frappe.workflow.doctype.workflow_action) -- a real,
	permission-checked transition, not a bypass. Callable directly from a
	Notification template as:

		{{ get_approve_link(doc, "Approve", "someone@example.com") }}
	"""
	return get_workflow_action_url(action, doc, user)


def get_app_deep_link(doc):
	"""Return a link into the PossibleWorks app (not Frappe) for `doc`,
	landing the viewer on the exact chat card raised for it -- requires login,
	and the real Approve/Reject buttons on that card are what perform the
	action, not this link itself.

	Asks pw-server-v3 to resolve it (via /api/frappe-user/erp-app-link) rather
	than building it here: the PW message this document's tile was built from,
	and the app's own base URL, both only exist on that side. Reuses the same
	per-environment URL already configured for the observer's own webhook
	(sites/common_site_config.json's possibleworks_webhook_url_<env>), since
	it is already proven to point at the right backend for this site.

	Falls back to the app's bare base URL (login screen) if anything about
	the lookup fails or the card cannot be found yet -- never breaks the
	surrounding email over this.

	Callable from a Notification template as:

		{{ get_app_deep_link(doc) }}
	"""
	try:
		base_url = SettingsHelper.get_webhook_url()
		response = make_get_request(
			f"{base_url}/frappe-user/erp-app-link",
			params={
				"tenant_id": frappe.get_cached_value("Company", doc.company, "custom_tenant_id"),
				"doctype": doc.doctype,
				"docname": doc.name,
			},
		)
		return response.get("data", {}).get("appUrl") or ""
	except Exception:
		frappe.log_error(
			title="get_app_deep_link failed",
			message=frappe.get_traceback(),
		)
		return ""
