# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""One branded HTML shell for every outgoing Possibleworks email.

Callers pass plain text and a link, never markup. That is the whole point: the shell
owns the palette, the logo and the footer, so the next email does not become a fourth
hand-pasted copy of the same table markup.

Kept deliberately small -- heading, body paragraphs, one call to action, trailing notes,
sign-off. Anything richer (a data table, a bullet list) should be added here when a
second caller actually needs it, not guessed at now.
"""

import frappe
from frappe import _

TEMPLATE = "possibleworks/templates/emails/pw_notification.html"

# Hosted rather than a site asset on purpose: mail clients will not resolve a relative
# path, and an authenticated /files/ URL is invisible to the recipient's inbox.
BRAND_LOGO_URL = "https://migrated-pw-images-dev.s3.ap-south-1.amazonaws.com/PWLogoForEmailV3.png"
PRIVACY_URL = "https://possibleworks.com/privacy-policy"


def render_branded_email(
	*,
	heading: str,
	paragraphs: list[str] | None = None,
	cta: dict | None = None,
	notes: list[str] | None = None,
	signoff: list[str] | None = None,
	footer_note: str | None = None,
	logo_url: str = BRAND_LOGO_URL,
	privacy_url: str = PRIVACY_URL,
) -> str:
	"""Return the HTML body for `frappe.sendmail(message=...)`.

	Every text argument is plain text and is escaped by the template, so a value that
	came from a form -- an applicant's own name, say -- cannot inject markup.

	`cta` is `{"label", "url"}` plus an optional `"fallback"` line, which makes the shell
	also print the raw URL. Worth passing: corporate clients strip styled buttons, and a
	recipient with no working button and no visible link is stuck.
	"""
	return frappe.render_template(
		TEMPLATE,
		{
			"logo_url": logo_url,
			"privacy_url": privacy_url,
			# Passed in rather than translated in the template: Jinja's `_` is not a
			# documented global here, and one place for translatable strings is enough.
			"privacy_label": _("Privacy Statement"),
			"heading": heading,
			"paragraphs": paragraphs or [],
			"cta": cta or {},
			"notes": notes or [],
			"signoff": signoff or [],
			"footer_note": footer_note or "",
		},
		is_path=True,
	)
