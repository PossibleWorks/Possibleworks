# Copyright (c) 2026, Possibleworks
# For license information, please see license.txt

"""
Migration patch: seed the PW AI Doctype Config child table
with all 8 supported doctypes and enable them by default.

This runs AFTER DocType sync in bench migrate.
It is idempotent — skips rows that already exist.
"""

import frappe


DEFAULT_DOCTYPES = [
	"Purchase Invoice",
	"Sales Invoice",
	"Purchase Order",
	"Sales Order",
	"Payment Entry",
	"Quotation",
	"Delivery Note",
	"Purchase Receipt",
]


def execute():
	"""Seed PW AI Doctype Config rows in PW AI Settings."""
	# Guard: ensure the child table exists before trying to insert
	if not frappe.db.table_exists("PW AI Doctype Config"):
		print("[possibleworks] PW AI Doctype Config table not yet created — skipping seed.")
		return

	# Singles don't have their own tables — check if DocType metadata exists
	if not frappe.db.exists("DocType", "PW AI Settings"):
		print("[possibleworks] PW AI Settings DocType not found — skipping seed.")
		return

	# Get existing rows
	existing = set(
		frappe.db.get_all(
			"PW AI Doctype Config",
			filters={"parent": "PW AI Settings", "parenttype": "PW AI Settings"},
			pluck="doctype_name",
		)
	)

	added = 0
	for dt in DEFAULT_DOCTYPES:
		if dt not in existing:
			doc = frappe.get_doc({
				"doctype": "PW AI Doctype Config",
				"doctype_name": dt,
				"is_enabled": 1,
				"button_label": "",
				"extraction_prompt": "",
				"parent": "PW AI Settings",
				"parenttype": "PW AI Settings",
				"parentfield": "doctype_config",
			})
			doc.flags.ignore_permissions = True
			doc.db_insert()
			added += 1

	if added:
		frappe.db.commit()
		print(f"[possibleworks] Seeded {added} AI Doctype Config rows.")
	else:
		print("[possibleworks] AI Doctype Config already seeded — nothing to do.")
