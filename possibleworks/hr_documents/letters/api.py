# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Whitelisted endpoints powering the Employee Letters section:
list templates, download (PDF) and email (PDF attachment)."""

import frappe
from frappe import _
from frappe.utils import validate_email_address

from possibleworks.hr_documents.letters.utils import (
	CONTEXT_KEYS,
	CONTEXT_LABELS,
	ensure_letter_access,
	get_employee_placeholder_fields,
	get_letter_head,
	resolve_letter,
)

# wkhtmltopdf is not available in this environment; use the Chrome generator
# (same mechanism Frappe uses for Salary Slip and other standard letters).
PDF_GENERATOR = "chrome"


def _pdf_filename(label, doc):
	safe_label = (label or "Letter").replace(" ", "_")
	safe_name = (doc.employee_name or doc.name).replace(" ", "_")
	return f"{safe_label}_{safe_name}.pdf"


def _render_pdf(employee, print_format, company):
	return frappe.get_print(
		"Employee",
		employee,
		print_format=print_format,
		as_pdf=True,
		letterhead=get_letter_head(company),
		pdf_generator=PDF_GENERATOR,
	)


@frappe.whitelist()
def get_letter_placeholders():
	"""Placeholders usable in a letter body, derived live from the Employee
	doctype. `helpers` are computed/formatted values; `fields` are raw Employee
	fields (including custom fields)."""
	ensure_letter_access()
	return {
		"helpers": [{"name": k, "label": _(CONTEXT_LABELS.get(k, k))} for k in CONTEXT_KEYS],
		"fields": [
			{"name": f["fieldname"], "label": f["label"]}
			for f in get_employee_placeholder_fields()
		],
	}


@frappe.whitelist()
def list_letter_templates(employee=None):
	"""Return the enabled letter templates, with availability for this employee."""
	ensure_letter_access()

	relieved = bool(frappe.db.get_value("Employee", employee, "relieving_date")) if employee else False

	templates = frappe.get_all(
		"Employee Letter Template",
		filters={"enabled": 1},
		fields=[
			"name",
			"template_name",
			"description",
			"icon",
			"requires_relieving_date",
			"is_default",
		],
		order_by="is_default desc, template_name asc",
	)
	for t in templates:
		t["icon"] = t.get("icon") or "file-text"
		t["available"] = (not t["requires_relieving_date"]) or relieved
	return templates


@frappe.whitelist()
def download_letter(employee, letter):
	"""Stream the letter as a PDF download. `letter` is the template name."""
	ensure_letter_access()
	print_format, doc = resolve_letter(employee, letter)

	pdf = _render_pdf(employee, print_format, doc.company)

	frappe.local.response.filename = _pdf_filename(letter, doc)
	frappe.local.response.filecontent = pdf
	frappe.local.response.type = "pdf"


@frappe.whitelist()
def email_letter(employee, letter, recipient, subject=None, message=None):
	"""Email the letter as a PDF attachment. `letter` is the template name."""
	ensure_letter_access()
	print_format, doc = resolve_letter(employee, letter)

	# Accept one or more comma- (or semicolon-) separated recipients.
	recipients = [
		email.strip()
		for email in (recipient or "").replace(";", ",").split(",")
		if email.strip()
	]
	if not recipients:
		frappe.throw(_("At least one recipient email address is required."))

	for email in recipients:
		validate_email_address(email, throw=True)

	subject = (subject or "").strip() or _("{0} - {1}").format(letter, doc.employee_name)
	message = message or _(
		"Dear {0},<br><br>Please find your {1} attached.<br><br>Regards,<br>HR Team"
	).format(doc.employee_name, letter)

	attachment = {
		"fname": _pdf_filename(letter, doc),
		"fcontent": _render_pdf(employee, print_format, doc.company),
	}

	frappe.sendmail(
		recipients=recipients,
		subject=subject,
		message=message,
		attachments=[attachment],
		reference_doctype="Employee",
		reference_name=employee,
	)

	return {"sent_to": ", ".join(recipients), "letter": letter}
