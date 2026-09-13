# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Shared helpers for the Employee letter templates (Relieving Letter,
Experience Letter, Service Certificate).

`get_letter_context` and `get_employee_tenure_text` are exposed to the Jinja
print-format environment via the `jinja` hook, so the print formats stay thin
and all computation lives here.
"""

import frappe
from frappe import _
from frappe.utils import formatdate, getdate, today
from dateutil.relativedelta import relativedelta

import keyword

# Roles allowed to preview / download / email employee letters.
ALLOWED_ROLES = ("System Manager", "HR Manager")

# Computed / formatted helper placeholders provided on top of the raw Employee
# fields. These mirror the keys returned by get_letter_context() and, where a
# name collides with a raw field (e.g. date_of_joining), the formatted helper
# wins.
CONTEXT_KEYS = (
	"employee_id",
	"employee_name",
	"salutation",
	"designation",
	"department",
	"company",
	"gender",
	"date_of_joining",
	"relieving_date",
	"end_date",
	"issue_date",
	"is_relieved",
	"tenure_text",
)

# Which engine renders these letters. wkhtmltopdf, because it is the one already
# installed in every container of the deployed stack; Chromium is not in the image and
# was only ever present in the web container as a runtime download, missing entirely
# from the queue workers that render emailed letters.
#
# It is an old Qt-WebKit build with no flexbox and no viewport units, so the letterhead
# sticks to block layout and absolute lengths -- a flex column with `min-height:100vh`
# silently collapses there.
#
# Shared so the generated Print Formats and the download/email path cannot drift apart
# and render the same letter through two different engines.
PDF_GENERATOR = "wkhtmltopdf"

# Where a letter shows up on the Employee form. Each value is one mount point
# rendered by public/js/employee/employee_letters.js -- "Letters" is the general
# tab, "Employee Exit" keeps the offboarding letters beside the relieving fields.
# These must match the `placement` field's Select options.
PLACEMENT_LETTERS = "Letters"
PLACEMENT_EXIT = "Employee Exit"

# Friendly descriptions for the computed helpers (used in the placeholder UI).
CONTEXT_LABELS = {
	"employee_id": "Employee ID",
	"employee_name": "Employee Name",
	"salutation": "Salutation (Mr./Ms.)",
	"designation": "Designation",
	"department": "Department (cleaned)",
	"company": "Company",
	"gender": "Gender",
	"date_of_joining": "Date of Joining (formatted)",
	"relieving_date": "Relieving Date (formatted)",
	"end_date": "End Date (relieving date, or today)",
	"issue_date": "Issue Date (today, formatted)",
	"is_relieved": "Is Relieved (true/false)",
	"tenure_text": "Tenure (e.g. 3 years, 2 months)",
}

# Employee fieldtypes that carry a printable value and can be used as bare
# `{{ fieldname }}` placeholders.
_VALUE_FIELDTYPES = frozenset(
	{
		"Data",
		"Select",
		"Small Text",
		"Text",
		"Long Text",
		"Text Editor",
		"Read Only",
		"Link",
		"Dynamic Link",
		"Date",
		"Datetime",
		"Time",
		"Int",
		"Float",
		"Currency",
		"Percent",
		"Check",
		"Phone",
		"Duration",
		"Rating",
		"Color",
	}
)

# Names we must not shadow in the Jinja context.
_RESERVED_NAMES = frozenset({"c", "doc", "true", "false", "none"} | set(keyword.kwlist))

_DATE_FORMAT = "d MMMM yyyy"  # e.g. 14 August 2026


def get_employee_placeholder_fields():
	"""Value-bearing Employee fields usable as bare `{{ fieldname }}` placeholders.

	Derived live from the Employee doctype meta, so custom fields are supported
	automatically."""
	meta = frappe.get_meta("Employee")
	fields = []
	for df in meta.fields:
		fn = df.fieldname
		if not fn or df.fieldtype not in _VALUE_FIELDTYPES:
			continue
		if not fn.isidentifier() or fn in _RESERVED_NAMES:
			continue
		fields.append({"fieldname": fn, "label": df.label or fn, "fieldtype": df.fieldtype})
	return fields


def ensure_letter_access():
	"""Guard: only the configured HR roles may generate letters."""
	if not set(ALLOWED_ROLES) & set(frappe.get_roles()):
		frappe.throw(
			_("You are not permitted to generate employee letters."),
			frappe.PermissionError,
		)


def resolve_letter(employee, letter):
	"""Validate inputs and return (print_format_name, employee_doc).

	`letter` is the Employee Letter Template name (which is also its Print
	Format name)."""
	letter = (letter or "").strip()
	if not letter or not frappe.db.exists("Employee Letter Template", letter):
		frappe.throw(_("Unknown letter template: {0}").format(letter))

	tmpl = frappe.get_cached_doc("Employee Letter Template", letter)
	if not tmpl.enabled:
		frappe.throw(_("The letter template {0} is disabled.").format(letter))

	doc = frappe.get_doc("Employee", employee)
	doc.check_permission("read")

	if tmpl.requires_relieving_date and not doc.relieving_date:
		frappe.throw(
			_("A {0} requires a Relieving Date on {1}.").format(letter, doc.employee_name)
		)

	if not doc.date_of_joining:
		frappe.throw(
			_("{0} has no Date of Joining, which is required for this letter.").format(
				doc.employee_name
			)
		)

	# Template name == Print Format name.
	return letter, doc


def get_letter_head(company):
	"""Company's default Letter Head, falling back to the site default."""
	letter_head = None
	if company:
		letter_head = frappe.db.get_value("Company", company, "default_letter_head")
	if not letter_head:
		letter_head = frappe.db.get_value("Letter Head", {"is_default": 1}, "name")
	return letter_head


def get_employee_tenure_text(from_date, to_date=None):
	"""Human-readable tenure between two dates, e.g. "3 years, 2 months and 5 days"."""
	if not from_date:
		return ""

	start = getdate(from_date)
	end = getdate(to_date) if to_date else getdate(today())
	if end < start:
		end = start

	rd = relativedelta(end, start)
	parts = []
	if rd.years:
		parts.append(_("{0} year{1}").format(rd.years, "" if rd.years == 1 else "s"))
	if rd.months:
		parts.append(_("{0} month{1}").format(rd.months, "" if rd.months == 1 else "s"))
	if rd.days and not rd.years:  # omit trailing days once we are in whole years
		parts.append(_("{0} day{1}").format(rd.days, "" if rd.days == 1 else "s"))

	if not parts:
		return _("less than a day")
	if len(parts) == 1:
		return parts[0]
	return _("{0} and {1}").format(", ".join(parts[:-1]), parts[-1])


def _salutation(gender):
	return {"Male": _("Mr."), "Female": _("Ms.")}.get(gender, "")


def get_letter_context(doc):
	"""Return the computed values shared by every employee letter.

	Accepts either an Employee doc or its name so it can be called cleanly from
	Jinja: ``{% set c = get_letter_context(doc) %}``.
	"""
	if isinstance(doc, str):
		doc = frappe.get_doc("Employee", doc)

	end_date = doc.relieving_date or today()

	return frappe._dict(
		{
			"employee_id": doc.name,
			"employee_name": doc.employee_name,
			"salutation": _salutation(doc.get("gender")),
			"designation": doc.designation or "",
			"department": (doc.department or "").split(" - ")[0],
			"company": doc.company or "",
			"gender": doc.get("gender") or "",
			"date_of_joining": formatdate(doc.date_of_joining, _DATE_FORMAT) if doc.date_of_joining else "",
			"relieving_date": formatdate(doc.relieving_date, _DATE_FORMAT) if doc.relieving_date else "",
			"end_date": formatdate(end_date, _DATE_FORMAT) if end_date else "",
			"is_relieved": bool(doc.relieving_date),
			"tenure_text": get_employee_tenure_text(doc.date_of_joining, end_date),
			"issue_date": formatdate(today(), _DATE_FORMAT),
		}
	)
