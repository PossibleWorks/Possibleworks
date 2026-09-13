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

from possibleworks.onboarding.constants import (
	APPLICANT_CHILD_TABLE_COLUMNS,
	APPLICANT_EDITABLE_STATUSES,
	APPLICANT_SHOWABLE_CHILD_TABLES,
	DOCTYPE,
)

no_cache = 1

DEFAULT_COUNTRY = "India"
DEFAULT_ISD = "+91"


def get_context(context):
	context.no_cache = 1
	context.show_sidebar = False
	# base.html renders this onto <body>. The stylesheet paints the page ground through
	# it, so setting it here rather than leaving it to the script means no white first
	# paint. The script still adds it, harmlessly, for the dialog rules.
	context.body_class = "ob-page"

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
	context.country_codes = phone_countries()
	context.fields = build_field_view(doc, context.country_codes)
	context.tables = build_table_view(doc)
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


# --------------------------------------------------------------------------- #
# Completeness
# --------------------------------------------------------------------------- #


def outstanding_items(doc) -> list[str]:
	"""Everything the applicant can still act on, as labels.

	Editability is resolved per record (see `OnboardingApplicant.field_is_editable`),
	so a field HR prefilled and locked is not reported as outstanding -- it is done.
	A field they were never allowed to edit is also excluded: they cannot fix it, and
	listing it would only leave them stuck. HR is blocked on those instead, by
	`validate_required_applicant_fields` at submit.
	"""
	items = []

	for row in doc.applicant_fields:
		if not (row.fieldname and row.is_required):
			continue
		if not doc.field_is_editable(row):
			continue
		if not doc.get(row.fieldname):
			items.append(row.label or row.fieldname)

	present = {r.document_type for r in doc.documents if r.attachment}
	for row in doc.required_documents:
		if row.enabled and row.is_required and row.document_type not in present:
			items.append(row.document_type)

	return items


def required_count(doc) -> int:
	"""How many things the applicant was asked for in total."""
	total = sum(
		1
		for row in doc.applicant_fields
		if row.fieldname and row.is_required and doc.field_is_editable(row)
	)
	total += sum(1 for row in doc.required_documents if row.enabled and row.is_required)
	return total


def progress_for(doc) -> dict:
	"""How much of the form is done -- the applicant's first question on opening it."""
	required = required_count(doc)
	done = required - len(outstanding_items(doc))

	return {
		"required": required,
		"done": done,
		"percent": round(done * 100 / required) if required else 100,
		"complete": required and done >= required,
	}


def missing_required(doc) -> list[str]:
	"""What still stands between the applicant and handing the form back."""
	return outstanding_items(doc)


def get_applicant_for_session():
	"""The one record this user owns, if any."""
	name = frappe.db.get_value(
		DOCTYPE, {"applicant_user": frappe.session.user, "docstatus": 0}, "name"
	)
	# ignore_permissions is safe here precisely because the lookup was by session user:
	# the portal role grants no DocPerm, so this is the only way in and it is scoped.
	return frappe.get_doc(DOCTYPE, name) if name else None


# --------------------------------------------------------------------------- #
# Field rendering
# --------------------------------------------------------------------------- #


def build_field_view(doc, countries=None) -> list[dict]:
	"""Render instructions for each scalar field the template asked for.

	A field absent from the snapshot is not rendered at all -- hidden, not merely
	read-only -- so the applicant never sees data the template did not choose to show.
	Repeating tables are handled separately by `build_table_view`.
	"""
	meta = frappe.get_meta(DOCTYPE)
	countries = countries if countries is not None else phone_countries()
	isds = [c["isd"] for c in countries]
	default_isd = default_isd_for(doc, countries)
	view = []

	for row in doc.applicant_fields:
		if row.fieldname in APPLICANT_SHOWABLE_CHILD_TABLES:
			continue

		df = meta.get_field(row.fieldname)
		if not df:
			# Field removed from the form since the snapshot was taken.
			continue

		editable = doc.field_is_editable(row)
		value = doc.get(row.fieldname)

		entry = {
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
			"value": value,
			"required": bool(row.is_required),
			"editable": editable,
			"locked_reason": locked_reason(row, editable),
			"help_text": row.help_text,
			"description": df.description,
		}

		if df.fieldtype == "Phone":
			# Split server-side: the isd list lives here, and doing the longest-prefix
			# match in two places is how "+1" starts swallowing "+91".
			isd, number = split_phone(value, isds)
			entry["value_isd"] = isd or default_isd
			entry["value_number"] = number

		view.append(entry)

	return view


def locked_reason(row, editable: bool) -> str:
	"""Why this field is read-only, in the applicant's terms.

	A padlock with no explanation reads as a bug. These two cases look identical on the
	page but mean very different things, and only one of them is worth contacting HR
	about.
	"""
	if editable:
		return ""
	if row.is_editable and row.lock_when_filled:
		return _("Already on file. Contact HR if this needs to change.")
	return _("Provided by HR. Contact them if this is wrong.")


def build_table_view(doc) -> list[dict]:
	"""Repeating-row sections (Education, Work Experience) the template asked for.

	Columns come from APPLICANT_CHILD_TABLE_COLUMNS rather than the child doctype's
	full field list, so intake-only extras (certificate uploads, derived totals) are
	not silently exposed the day one is added upstream.
	"""
	meta = frappe.get_meta(DOCTYPE)
	view = []

	for row in doc.applicant_fields:
		if row.fieldname not in APPLICANT_SHOWABLE_CHILD_TABLES:
			continue

		df = meta.get_field(row.fieldname)
		if not df or not df.options:
			continue

		child_meta = frappe.get_meta(df.options)
		columns = []
		for fieldname in APPLICANT_CHILD_TABLE_COLUMNS.get(row.fieldname, ()):
			cdf = child_meta.get_field(fieldname)
			if not cdf:
				continue
			columns.append(
				{
					"fieldname": fieldname,
					"label": _(cdf.label),
					"fieldtype": "Select" if cdf.fieldtype == "Link" else cdf.fieldtype,
					"options_list": link_options(cdf) if cdf.fieldtype == "Link" else (
						[o for o in (cdf.options or "").split("\n")]
						if cdf.fieldtype == "Select"
						else []
					),
				}
			)

		if not columns:
			continue

		view.append(
			{
				"fieldname": row.fieldname,
				"label": row.label or df.label,
				"required": bool(row.is_required),
				"editable": doc.field_is_editable(row),
				"help_text": row.help_text,
				"columns": columns,
				"rows": [
					{column["fieldname"]: child.get(column["fieldname"]) for column in columns}
					for child in (doc.get(row.fieldname) or [])
				],
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


# --------------------------------------------------------------------------- #
# Phone numbers
# --------------------------------------------------------------------------- #


def phone_countries() -> list[dict]:
	"""Country dialling codes for the portal's Phone control.

	Sourced from the same `frappe/geo/country_info.json` the Desk control uses, read
	server-side. The Desk control reads it from `frappe.boot`, which a website page
	does not get -- and a plain text box here is what made `cell_number` impossible to
	fill: the server always parses the value with no default region, so a number
	without a dialling code fails `validate_phone_number_with_country_code`.
	"""
	from frappe.geo.country_info import get_all

	countries = []
	for name, info in (get_all() or {}).items():
		info = info or {}
		isd = info.get("isd")
		if not isd:
			continue
		countries.append({"name": name, "isd": isd, "flag": _flag(info.get("code"))})

	countries.sort(key=lambda c: c["name"])
	return countries


def _flag(iso_code) -> str:
	"""The flag emoji for an ISO 3166-1 alpha-2 code, or "" if there isn't one.

	Two regional indicator symbols, which every current platform renders as a flag. Doing
	it from the code means no image assets and nothing to keep in sync -- and an unknown
	code degrades to the dialling code alone rather than a broken glyph.
	"""
	code = (iso_code or "").strip().upper()
	if len(code) != 2 or not code.isalpha():
		return ""
	return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def default_isd_for(doc, countries) -> str:
	"""Pre-select the employer's country, so the common case is zero clicks."""
	country = None
	if doc.company:
		country = frappe.db.get_value("Company", doc.company, "country")

	by_name = {c["name"]: c["isd"] for c in countries}
	return by_name.get(country) or by_name.get(DEFAULT_COUNTRY) or DEFAULT_ISD


def split_phone(value: str, isds: list[str]) -> tuple[str, str]:
	"""Split a stored `+91 9876543210` into its dialling code and the rest.

	Longest match wins, or `+1` would claim every `+91` number.
	"""
	value = (value or "").strip()
	if not value:
		return "", ""

	for isd in sorted(isds, key=len, reverse=True):
		if value.startswith(isd):
			return isd, value[len(isd) :].strip(" -")

	return "", value


# --------------------------------------------------------------------------- #
# Documents
# --------------------------------------------------------------------------- #


def build_document_view(doc) -> list[dict]:
	uploaded = {}
	for row in doc.documents:
		uploaded.setdefault(row.document_type, []).append(
			{
				"row_name": row.name,
				"file_url": row.attachment,
				# The applicant's own name for it, falling back to the stored one for
				# rows that predate the column or came in from the Desk. Frappe suffixes
				# a stored name when it collides, so the two are not always the same.
				"file_name": row.original_file_name
				or frappe.db.get_value("File", {"file_url": row.attachment}, "file_name"),
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
