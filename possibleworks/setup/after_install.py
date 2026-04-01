"""Set default branding (app_name, footer_powered) on install."""
import frappe


def seed_ai_settings():
	"""
	Keep AI Document Processor Settings.supported_doctypes aligned with the
	current rollout.

	Runs after every bench migrate so fresh sites get the default rows and
	existing sites are pruned when the rollout scope changes.
	"""
	from possibleworks.ap_invoice_processing.constants import ROLLOUT_DOCTYPES, SETTINGS_DOCTYPE

	try:
		if not frappe.db.exists("DocType", SETTINGS_DOCTYPE):
			return

		settings = frappe.get_single(SETTINGS_DOCTYPE)
		existing_enabled = {}
		for row in list(settings.supported_doctypes or []):
			dt = (row.document_type or "").strip()
			if not dt:
				continue
			existing_enabled[dt] = 1 if row.enabled else 0

		desired_rows = [
			{"document_type": dt, "enabled": existing_enabled.get(dt, 1)}
			for dt in ROLLOUT_DOCTYPES
		]
		current_rows = [
			{
				"document_type": (row.document_type or "").strip(),
				"enabled": 1 if row.enabled else 0,
			}
			for row in list(settings.supported_doctypes or [])
			if (row.document_type or "").strip()
		]

		if current_rows == desired_rows:
			return

		settings.set("supported_doctypes", [])
		for row in desired_rows:
			settings.append("supported_doctypes", row)

		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
		frappe.db.commit()
	except Exception as e:
		frappe.log_error(
			title="Possibleworks: AI Settings seed failed",
			message=str(e),
		)


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

		if not ws.banner_image or "frappe-framework-logo.svg" in ws.banner_image:
			ws.banner_image = "/assets/possibleworks/images/possibleworks-logo-big.svg"

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
