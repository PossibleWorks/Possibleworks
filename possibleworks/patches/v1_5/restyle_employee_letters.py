# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Regenerate every letter's Print Format after a letterhead or engine change.

`Employee Letter Template.sync_print_format()` is the only thing that writes these
Print Format records, and it only runs on save -- so a change to `LETTER_HEAD` or
`PDF_GENERATOR` reaches nothing until each template is re-saved. That is exactly the
trap that made hand-editing the Print Format look like it worked: the next save of the
template silently reverts it.

Re-saving is safe and repeatable. `sync_print_format` is a full rebuild, not a merge,
so running this twice produces the same record. It never touches `body`, which is the
site's own content.
"""

import frappe


def execute():
	names = frappe.get_all("Employee Letter Template", pluck="name")
	if not names:
		return

	for name in names:
		doc = frappe.get_doc("Employee Letter Template", name)
		doc.flags.ignore_permissions = True
		# on_update -> sync_print_format() does the work.
		doc.save(ignore_permissions=True)

	frappe.logger().info(f"possibleworks: regenerated {len(names)} letter Print Formats")
