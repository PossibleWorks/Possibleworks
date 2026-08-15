# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""The applicant's own onboarding page.

Scoped by identity, not by URL: there is no record id in the route. The page resolves
the single record whose `applicant_user` is the logged-in user, so an applicant cannot
reach anyone else's form by editing an address.

Which fields appear, and whether they can be edited, comes from the record's
`applicant_fields` snapshot -- never the template -- so a template edited mid-application
cannot change the form under someone who is halfway through filling it in.
"""

import frappe
from frappe import _

from possibleworks.onboarding.constants import APPLICANT_EDITABLE_STATUSES, DOCTYPE

no_cache = 1


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False

	if frappe.session.user == "Guest":
		frappe.throw(_("Please use the link from your onboarding email."), frappe.PermissionError)

	doc = get_applicant_for_session()
	if not doc:
		context.template = None
		context.error = _(
			"No onboarding form is assigned to this account. Please contact your HR contact."
		)
		return context

	context.doc = doc
	context.editable = doc.docstatus == 0 and doc.status in APPLICANT_EDITABLE_STATUSES
	context.fields = build_field_view(doc)
	context.documents = build_document_view(doc)
	context.missing = missing_required(doc)
	context.company = doc.company
	context.greeting = greeting_for(doc)
	context.progress = progress_for(doc)
	return context


def greeting_for(doc) -> str:
	"""A name, or nothing.

	`applicant_name` falls back to the email address when HR has not filled in a name,
	and greeting somebody with their own email address reads as broken. Better to drop
	the name than to show that.
	"""
	name = (doc.first_name or "").strip()
	if name:
		return name

	full = (doc.applicant_name or "").strip()
	return "" if (not full or "@" in full) else full


def progress_for(doc) -> dict:
	"""How much of the form is done -- the applicant's first question on opening it."""
	required, done = 0, 0

	for row in doc.applicant_fields:
		if row.is_required and row.is_editable:
			required += 1
			if doc.get(row.fieldname):
				done += 1

	present = {r.document_type for r in doc.documents if r.attachment}
	for row in doc.required_documents:
		if row.enabled and row.is_required:
			required += 1
			if row.document_type in present:
				done += 1

	return {
		"required": required,
		"done": done,
		"percent": round(done * 100 / required) if required else 100,
		"complete": required and done >= required,
	}


def get_applicant_for_session():
	"""The one record this user owns, if any."""
	name = frappe.db.get_value(
		DOCTYPE, {"applicant_user": frappe.session.user, "docstatus": 0}, "name"
	)
	# ignore_permissions is safe here precisely because the lookup was by session user:
	# the portal role grants no DocPerm, so this is the only way in and it is scoped.
	return frappe.get_doc(DOCTYPE, name) if name else None


def build_field_view(doc) -> list[dict]:
	"""Render instructions for each field the template asked for.

	A field absent from the snapshot is not rendered at all -- hidden, not merely
	read-only -- so the applicant never sees data the template did not choose to show.
	"""
	meta = frappe.get_meta(DOCTYPE)
	view = []

	for row in doc.applicant_fields:
		df = meta.get_field(row.fieldname)
		if not df:
			# Field removed from the form since the snapshot was taken.
			continue

		view.append(
			{
				"fieldname": row.fieldname,
				"label": row.label or df.label,
				# A Link is rendered as a dropdown, not a text box: the applicant has no
				# way to know the exact stored name, and a near-miss becomes a
				# LinkValidationError only after they hit save.
				"fieldtype": "Select" if df.fieldtype == "Link" else df.fieldtype,
				"options": df.options,
				"options_list": link_options(df) if df.fieldtype == "Link" else (
					[o for o in (df.options or "").split("\n")] if df.fieldtype == "Select" else []
				),
				"value": doc.get(row.fieldname),
				"required": bool(row.is_required),
				"editable": bool(row.is_editable),
				"help_text": row.help_text,
				"description": df.description,
			}
		)
	return view


def link_options(df) -> list[str]:
	"""Choices for a Link field, as a plain list.

	Capped deliberately: the applicant-writable Links are small masters (Gender,
	Salutation). If a template ever offers a Link with thousands of rows, a dropdown is
	the wrong control anyway, and the cap keeps the page from becoming unusable rather
	than silently rendering megabytes of options.
	"""
	if not df.options:
		return []

	try:
		names = frappe.get_all(df.options, pluck="name", limit=200, order_by="name asc")
	except frappe.PermissionError:
		# Portal users hold no DocPerm on masters; read them as the system for this
		# read-only lookup rather than granting the role standing access.
		names = frappe.get_all(
			df.options, pluck="name", limit=200, order_by="name asc", ignore_permissions=True
		)

	return ["", *names]


def build_document_view(doc) -> list[dict]:
	uploaded = {}
	for row in doc.documents:
		uploaded.setdefault(row.document_type, []).append(
			{
				"row_name": row.name,
				"file_url": row.attachment,
				"file_name": frappe.db.get_value("File", {"file_url": row.attachment}, "file_name"),
			}
		)

	view = []
	for row in doc.required_documents:
		if not row.enabled:
			continue
		view.append(
			{
				"document_type": row.document_type,
				"required": bool(row.is_required),
				"allow_multiple": bool(row.allow_multiple),
				"allowed_extensions": row.allowed_extensions
				or frappe.db.get_value(
					"Onboarding Document Type", row.document_type, "allowed_extensions"
				),
				"instructions": row.instructions,
				"files": uploaded.get(row.document_type, []),
			}
		)
	return view


def missing_required(doc) -> list[str]:
	"""What still stands between the applicant and handing the form back."""
	missing = []

	for row in doc.applicant_fields:
		if row.is_required and row.is_editable and not doc.get(row.fieldname):
			missing.append(row.label or row.fieldname)

	present = {row.document_type for row in doc.documents if row.attachment}
	for row in doc.required_documents:
		if row.enabled and row.is_required and row.document_type not in present:
			missing.append(row.document_type)

	return missing
