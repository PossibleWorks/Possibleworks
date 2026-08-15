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

		return (
			"{% set c = get_letter_context(doc) %}\n"
			f"{set_lines}\n"
			"<div style=\"font-family:'Helvetica Neue',Arial,sans-serif;font-size:12pt;"
			'line-height:1.7;color:#1a1a1a;">\n'
			'<p style="text-align:right;margin-bottom:24px;">Date: {{ issue_date }}</p>\n'
			'<h3 style="text-align:center;text-decoration:underline;letter-spacing:1px;'
			f'margin-bottom:12px;">{title}</h3>\n'
			f"{subtitle_block}\n"
			f'<div style="margin-top:16px;">{body}</div>\n'
			"</div>"
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
		pf.pdf_generator = "chrome"
		pf.disabled = 0 if self.enabled else 1
		pf.html = html
		pf.flags.ignore_permissions = True

		if exists:
			pf.save()
		else:
			pf.insert(ignore_permissions=True)
