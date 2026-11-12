# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Turns the leave / attendance request overlap block on for sites that have already
saved Policy Configuration.

A Single's field defaults only apply while it has no saved rows at all
(Document.load_from_db), so on those sites the new default-1 Check would read 0: off,
shown unticked, and written as 0 by the next save of the page.

Sites with no rows are skipped on purpose. They already get every default, and writing
one row here would make every other default-1 flag on the page read 0.

Runs before fixtures sync adds the field; set_single_value writes tabSingles directly
without consulting the meta, so that order is fine.
"""

import frappe

DOCTYPE = "Policy Configuration"
FIELDNAME = "enable_leave_attendance_overlap_block"


def execute():
	saved = frappe.db.get_singles_dict(DOCTYPE)
	if saved and FIELDNAME not in saved:
		frappe.db.set_single_value(DOCTYPE, FIELDNAME, 1, update_modified=False)
