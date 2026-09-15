"""
Jinja-callable helpers for Notification (Email Alert) templates.

Registered via hooks.py's `jinja.methods`, so these become available as plain
function calls inside any Notification's subject/message field -- the same
mechanism this app already uses for Employee letter templates
(possibleworks.hr_documents.letters.utils).
"""

import frappe


def get_app_deep_link(doc):
	"""Return a link into the PossibleWorks app's landing page for `doc`'s
	tenant -- same pattern as the guest RFQ portal link in
	possibleworks.finance.rfq_portal.build_guest_quotation_url, built entirely
	locally with no outbound call. Opens the app (login screen if the viewer
	isn't already signed in); it does not deep-link to a specific chat card --
	that was tried and reverted, "just opening the app" is what's wanted.

	Callable from a Notification template as:

		{{ get_app_deep_link(doc) }}
	"""
	origin = (frappe.db.get_single_value("Possibleworks Settings", "guest_quotation_portal_base_url") or "").rstrip("/")
	tenant_id = frappe.get_cached_value("Company", doc.company, "custom_tenant_id")

	if not origin:
		return ""
	if not tenant_id:
		return origin

	return f"{origin}/{tenant_id}"
