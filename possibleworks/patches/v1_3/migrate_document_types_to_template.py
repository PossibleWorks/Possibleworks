# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Move document policy off the flat type master and into a template.

`Onboarding Document Type` used to carry both identity (what the document is) and
policy (whether it is required, whether several files are allowed). Policy varies by
the kind of hire, so it now lives on `Onboarding Document Template` rows.

This patch is one-way and drops columns, so it reads the existing flags FIRST and
writes them into a default template. Behaviour is preserved exactly: whatever was
required site-wide before is required by the default template after.
"""

import frappe

from possibleworks.onboarding.constants import (
	DEFAULT_DOCUMENT_TYPES,
	DEFAULT_TEMPLATE_NAME,
	DOCUMENT_TEMPLATE_DOCTYPE,
	DOCUMENT_TYPE_DOCTYPE,
)

# Columns being retired from Onboarding Document Type.
LEGACY_COLUMNS = ("is_required", "allow_multiple", "applies_to_company", "display_order")


def execute():
	if not frappe.db.exists("DocType", DOCUMENT_TYPE_DOCTYPE):
		return

	legacy = read_legacy_policy()
	build_default_template(legacy)
	attach_template_to_existing_drafts()
	drop_legacy_columns()


def read_legacy_policy() -> list[dict]:
	"""Read the old flags straight from the table.

	Deliberately raw SQL: by the time this runs the DocType JSON no longer declares
	these fields, so `frappe.get_all` would refuse to select them.
	"""
	columns = [c for c in LEGACY_COLUMNS if frappe.db.has_column(DOCUMENT_TYPE_DOCTYPE, c)]
	if not columns:
		return []

	select = ", ".join(["name", "enabled", "allowed_extensions", *columns])
	order = "display_order asc, name asc" if "display_order" in columns else "name asc"
	return frappe.db.sql(
		f"select {select} from `tabOnboarding Document Type` order by {order}", as_dict=True
	)


def build_default_template(legacy: list[dict]) -> None:
	if frappe.db.exists(DOCUMENT_TEMPLATE_DOCTYPE, DEFAULT_TEMPLATE_NAME):
		return

	if not legacy:
		# Fresh install: there are no legacy columns to migrate, so seed the starter
		# policy from the same constants the type seed uses.
		legacy = [
			frappe._dict(
				name=row["document_type_name"],
				enabled=1,
				allowed_extensions=row.get("allowed_extensions"),
				is_required=row.get("is_required", 0),
				allow_multiple=row.get("allow_multiple", 0),
			)
			for row in DEFAULT_DOCUMENT_TYPES
			if frappe.db.exists(DOCUMENT_TYPE_DOCTYPE, row["document_type_name"])
		]

	template = frappe.new_doc(DOCUMENT_TEMPLATE_DOCTYPE)
	template.template_name = DEFAULT_TEMPLATE_NAME
	template.enabled = 1
	template.is_default = 1
	template.description = (
		"Created automatically from the previous site-wide document settings. "
		"Copy it to build templates for specific kinds of hire."
	)

	for row in legacy:
		template.append(
			"documents",
			{
				"document_type": row.name,
				"is_required": row.get("is_required") or 0,
				"allow_multiple": row.get("allow_multiple") or 0,
				"enabled": row.get("enabled") or 0,
				"allowed_extensions": row.get("allowed_extensions"),
			},
		)

	if not template.documents:
		return

	template.insert(ignore_permissions=True)


def attach_template_to_existing_drafts() -> None:
	"""Point existing drafts at the default so they keep validating as before.

	Submitted records are left alone: their requirements were already met, and
	back-filling a snapshot onto a submitted document would be rewriting history.
	"""
	if not frappe.db.exists(DOCUMENT_TEMPLATE_DOCTYPE, DEFAULT_TEMPLATE_NAME):
		return

	drafts = frappe.get_all(
		"Onboarding Applicant",
		filters={"docstatus": 0, "document_template": ("in", ("", None))},
		pluck="name",
	)
	for name in drafts:
		doc = frappe.get_doc("Onboarding Applicant", name)
		doc.document_template = DEFAULT_TEMPLATE_NAME
		doc.sync_required_documents()
		doc.save(ignore_permissions=True)


def drop_legacy_columns() -> None:
	for column in LEGACY_COLUMNS:
		if frappe.db.has_column(DOCUMENT_TYPE_DOCTYPE, column):
			frappe.db.sql_ddl(f"alter table `tabOnboarding Document Type` drop column `{column}`")

	frappe.clear_cache(doctype=DOCUMENT_TYPE_DOCTYPE)
