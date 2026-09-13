# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Seed a starter set of onboarding document types.

Idempotent, and deliberately non-destructive: an existing row is left completely
untouched. Once a site has installed this, HR owns its own `is_required` /
`allow_multiple` policy and a later migrate must never reset it.
"""

import frappe

from possibleworks.onboarding.constants import DEFAULT_DOCUMENT_TYPES, DOCUMENT_TYPE_DOCTYPE


def execute():
	if not frappe.db.exists("DocType", DOCUMENT_TYPE_DOCTYPE):
		return

	for row in DEFAULT_DOCUMENT_TYPES:
		if frappe.db.exists(DOCUMENT_TYPE_DOCTYPE, row["document_type_name"]):
			# The site owns this row now -- never clobber its configuration.
			continue

		# Vocabulary fields only. `is_required` / `allow_multiple` in the constant are
		# seed POLICY, consumed by v1_3 to build the default template -- they are no
		# longer fields on this doctype.
		doc = frappe.new_doc(DOCUMENT_TYPE_DOCTYPE)
		doc.document_type_name = row["document_type_name"]
		doc.allowed_extensions = row.get("allowed_extensions")
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
