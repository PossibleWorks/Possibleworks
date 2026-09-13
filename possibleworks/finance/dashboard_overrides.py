# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Extensions to other apps' Connections tabs, registered via `override_doctype_dashboards`.

Frappe hands the existing dashboard data in and takes the modified data back
(`frappe/model/meta.py:758`), so these EXTEND erpnext's own dashboards rather than
replace them -- an erpnext upgrade that adds a link keeps it.
"""

from frappe import _


def _add_to_transactions(data, label, doctype):
	"""Append a doctype to a named group, creating the group if it is not there yet."""
	for group in data.setdefault("transactions", []):
		if group.get("label") == label:
			if doctype not in group["items"]:
				group["items"].append(doctype)
			return
	data["transactions"].append({"label": label, "items": [doctype]})


def material_request(data=None):
	"""Show the Purchase Indents raised from a Material Request.

	Without this a requester or the approving Principal opens their request and sees no
	sign of what stores did with it -- erpnext's own dashboard predates this doctype and
	cannot know about it.

	No `internal_links` entry is needed: the dashboard's default `fieldname` for Material
	Request is already `material_request`, and `Purchase Indent Item.material_request` is
	named the same, so Frappe resolves the child link on its own -- exactly how the
	existing Stock Entry link works.
	"""
	data = data or {}
	_add_to_transactions(data, _("Reference"), "Purchase Indent")
	return data
