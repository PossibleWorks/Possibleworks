"""Set default branding (app_name, footer_powered) on install."""
import frappe


def set_default_branding():
	"""Set System Settings and Website Settings for Possibleworks branding."""

	# ── System Settings ──────────────────────────────────────────────
	try:
		ss = frappe.get_single("System Settings")
		if not ss.app_name or ss.app_name == "Frappe":
			ss.app_name = "Possibleworks"
		ss.hide_footer_in_auto_email_reports = 1
		ss.flags.ignore_mandatory = True
		ss.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			title="Possibleworks: System Settings branding failed",
			message=str(e),
		)

	# ── Website Settings ─────────────────────────────────────────────
	try:
		ws = frappe.get_single("Website Settings")
		if not ws.app_name or ws.app_name == "Frappe":
			ws.app_name = "Possibleworks"

		if not ws.title_prefix or ws.title_prefix == "Frappe":
			ws.title_prefix = "Possibleworks"

		if not ws.footer_powered or "Frappe" in ws.footer_powered:
			ws.footer_powered = "Powered by Possibleworks"

		if not ws.favicon or "frappe-favicon.svg" in ws.favicon:
			ws.favicon = "/assets/possibleworks/images/possibleworks-logo.svg"

		if not ws.brand_logo or "frappe-framework-logo.svg" in ws.brand_logo:
			ws.brand_logo = "/assets/possibleworks/images/possibleworks-logo-big.svg"

		if not ws.app_logo or "frappe-framework-logo.svg" in ws.app_logo:
			ws.app_logo = "/assets/possibleworks/images/possibleworks-logo-big.svg"

		if not ws.splash_image or "frappe-framework-logo.svg" in ws.splash_image:
			ws.splash_image = "/assets/possibleworks/images/possibleworks-logo-big.svg"

		ws.flags.ignore_mandatory = True
		ws.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			title="Possibleworks: Website Settings branding failed",
			message=str(e),
		)
