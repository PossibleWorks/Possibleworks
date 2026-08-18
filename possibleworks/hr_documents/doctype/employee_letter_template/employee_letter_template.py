# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import escape_html

from possibleworks.hr_documents.letters.utils import (
	CONTEXT_KEYS,
	get_employee_placeholder_fields,
)


# Which engine renders these letters. wkhtmltopdf, because it is the one already
# installed in every container of the deployed stack; Chromium is not in the image and
# was only ever present in the web container as a runtime download, missing entirely
# from the queue workers that render emailed letters.
#
# It is an old Qt-WebKit build with no flexbox and no viewport units, so the letterhead
# below sticks to block layout and absolute lengths -- a flex column with
# `min-height:100vh` silently collapses there.
PDF_GENERATOR = "wkhtmltopdf"

# The logo is hosted rather than a site file: a print worker fetches it unauthenticated,
# so /files/ and /private/files/ URLs come back empty.
DEFAULT_LOGO = "https://migrated-pw-images-dev.s3.ap-south-1.amazonaws.com/PWLogoForEmailV3.png"

BRAND_RULE = "#2E5CB8"

# Serif, and not for taste alone. wkhtmltopdf's Qt-WebKit drops the space before an
# inline <strong> for certain kerning pairs in sans faces -- "services at<b>Acme</b>",
# "capacity of<b>Associate</b>" -- while the text layer keeps it, so it survives
# copy-paste and only shows up in the printed letter. Verified against Helvetica Neue,
# Arial, Liberation Sans and DejaVu Sans (all affected) and Georgia, Liberation Serif,
# DejaVu Serif and Times (all clean). &nbsp; does not reliably fix it.
#
# Nunito was tried and cannot work here: wkhtmltopdf only uses host-installed fonts, a
# Google Fonts @import renders the letter blank, and an embedded base64 @font-face is
# ignored. It renders correctly under Chrome, so this becomes possible the day
# Chromium is baked into the image (`bench setup-chrome` at build time).
#
# Ordered so a Linux container without Georgia still lands on a verified face.
LETTER_FONT = "Georgia,'Liberation Serif','DejaVu Serif','Times New Roman',Times,serif"

# Everything above the letter title: brand rule, logo, and the issue date.
# `company_logo` is read off the Employee doc so a site can override per company
# without touching this file.
LETTER_HEAD = (
	'{% set company_name = c.company or "PossibleWorks.ai" %}\n'
	f'{{% set company_logo = doc.get("company_logo") or "{DEFAULT_LOGO}" %}}\n'
	f'<div style="font-family:{LETTER_FONT};font-size:12pt;'
	'line-height:1.7;color:#1a1a1a;padding:20px;">\n'
	f'<div style="border-bottom:2px solid {BRAND_RULE};padding-bottom:10px;'
	'margin-bottom:20px;">\n'
	'<img src="{{ company_logo }}" alt="{{ company_name }}" '
	'style="height:42px;width:auto;border:0;display:block;" />\n'
	"</div>\n"
	'<p style="text-align:right;margin-bottom:24px;">Date: {{ issue_date }}</p>\n'
)

LETTER_FOOT = "</div>"


class EmployeeLetterTemplate(Document):
	def validate(self):
		if not (self.body or "").strip():
			frappe.throw(_("Letter body is required."))
		if not self.icon:
			self.icon = "file-text"

	def on_update(self):
		"""Keep a matching Print Format in sync so the letter works in the
		native print view (preview / print / PDF / email), exactly like the
		built-in defaults."""
		self.sync_print_format()

	def on_trash(self):
		if self.is_default:
			frappe.throw(
				_("Default letter templates cannot be deleted. Disable them instead.")
			)
		if frappe.db.exists("Print Format", self.name):
			frappe.delete_doc("Print Format", self.name, ignore_permissions=True, force=True)

	# ------------------------------------------------------------------ #

	def build_print_format_html(self):
		"""Wrap the user-authored body with the standard letter chrome (date,
		title, subtitle) and expose placeholders as bare Jinja variables so the
		body can use `{{ employee_name }}` instead of `{{ c.employee_name }}`.

		Every value-bearing Employee field is exposed dynamically, then the
		computed/formatted helpers are set afterwards so they win on name
		collisions (e.g. formatted date_of_joining)."""
		raw_lines = "\n".join(
			f'{{%- set {f["fieldname"]} = doc.get("{f["fieldname"]}") or "" -%}}'
			for f in get_employee_placeholder_fields()
		)
		ctx_lines = "\n".join(f"{{%- set {k} = c.{k} -%}}" for k in CONTEXT_KEYS)
		set_lines = f"{raw_lines}\n{ctx_lines}"

		title = escape_html(self.letter_title or "")
		subtitle_block = ""
		if self.subtitle:
			subtitle_block = (
				'<p style="text-align:center;font-weight:bold;margin-bottom:16px;">'
				f"{escape_html(self.subtitle)}</p>"
			)

		body = self.body or ""

		# Title spacing differs when there is no subtitle: the two-line letters
		# (TO WHOMSOEVER IT MAY CONCERN) keep a tight gap, a bare title needs more.
		title_margin = 12 if self.subtitle else 28

		return (
			"{% set c = get_letter_context(doc) %}\n"
			f"{set_lines}\n"
			f"{LETTER_HEAD}"
			'<h3 style="text-align:center;text-decoration:underline;letter-spacing:1px;'
			f'margin-bottom:{title_margin}px;">{title}</h3>\n'
			f"{subtitle_block}\n"
			# Justified, as a formal letter is set. `text-align` inherits to the body's
			# paragraphs, and the last line of each block stays flush-left, so the
			# short signature lines are unaffected. No `hyphens:auto` -- that engine's
			# Qt-WebKit ignores it, and asking for it would only imply it works.
			f'<div style="margin-top:16px;text-align:justify;">{body}</div>\n'
			f"{LETTER_FOOT}"
		)

	def sync_print_format(self):
		html = self.build_print_format_html()
		exists = frappe.db.exists("Print Format", self.name)

		if exists:
			pf = frappe.get_doc("Print Format", self.name)
		else:
			pf = frappe.new_doc("Print Format")
			pf.name = self.name

		pf.doc_type = "Employee"
		pf.module = "HR Documents"
		pf.print_format_type = "Jinja"
		pf.custom_format = 1
		pf.standard = "No"
		# Set here rather than on the Print Format record: `sync_print_format` rebuilds
		# that record from scratch on every template save, so a value edited there is
		# silently reverted the next time HR touches the letter.
		pf.pdf_generator = PDF_GENERATOR
		pf.disabled = 0 if self.enabled else 1
		pf.html = html
		pf.flags.ignore_permissions = True

		if exists:
			pf.save()
		else:
			pf.insert(ignore_permissions=True)
