# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Repurposes the Budget doctype's "Material Request" dimension into Purchase
Indent's own dedicated dimension.

Material Request can never actually be budget-checked in this product: its item
table (ERPConstants/MaterialRequest.ts) carries no expense_account / cost_center
fields at all, so there's nothing for erpnext's own budget engine (or ours) to
match a Budget row against. "Applicable on Material Request" has therefore always
been a dead checkbox here - meanwhile Purchase Indent, this app's real first
commitment step, has never had a dedicated slot of its own and has always fallen
through to the generic "Applicable on booking actual expenses" pair instead (see
possibleworks.finance.budget._check_one_line / get_actions in erpnext core).

Relabels (Property Setters only - the underlying fieldnames, and so the database
columns, are untouched):
	applicable_on_material_request                      -> "Applicable on Purchase Indent"
	action_if_annual_budget_exceeded_on_mr               -> "...on Purchase Indent"
	action_if_accumulated_monthly_budget_exceeded_on_mr  -> "...on Purchase Indent"

possibleworks.finance.budget._check_one_line is what actually makes the relabeled
field mean something for Purchase Indent (it presents Indent to erpnext's own
get_actions() as "Material Request" for this one lookup) - this patch only
changes what the checkbox is labelled and which existing Budgets have it set.

Also updates applicable_on_cumulative_expense's own hint text. That field is
unrelated to the three above (it drives the SEPARATE combined check added in
get_indent_outstanding_amount / _check_one_line's cumulative branch, not the
per-doctype dimension this patch relabels). Material Request is excluded from
that combined total entirely (same reason as everywhere else in this patch -
it can never be budget-checked in this product), so the hint now reads
"Purchase Order + Purchase Indent + Actual Expense".

Backfill: every Budget currently relying on the generic pair to (ambiguously)
govern Purchase Indent keeps working with no manual reconfiguration - its
existing Stop/Warn settings are copied into the newly-dedicated fields. Only
touches budgets that don't already have the MR/Indent dimension configured, so
it can never overwrite something already set.
"""

import frappe


def execute():
	_relabel_fields()
	_backfill_existing_budgets()
	# frappe.db.set_value() on an existing Property Setter (the "already exists,
	# update it" branch in _relabel_fields) writes the row directly and never
	# fires Property Setter's own on_update, which is what normally clears the
	# cached DocType meta - without this, frappe.get_meta("Budget") keeps
	# serving the pre-relabel label/description text until something else
	# happens to clear the site cache.
	frappe.clear_cache(doctype="Budget")


def _relabel_fields():
	relabels = [
		("applicable_on_material_request", "label", "Applicable on Purchase Indent"),
		(
			"action_if_annual_budget_exceeded_on_mr",
			"label",
			"Action if Annual Budget Exceeded on Purchase Indent",
		),
		(
			"action_if_accumulated_monthly_budget_exceeded_on_mr",
			"label",
			"Action if Accumulated Monthly Budget Exceeded on Purchase Indent",
		),
		(
			"applicable_on_cumulative_expense",
			"description",
			"(Purchase Order + Purchase Indent + Actual Expense)",
		),
	]
	for fieldname, prop, value in relabels:
		if frappe.db.exists(
			"Property Setter", {"doc_type": "Budget", "field_name": fieldname, "property": prop}
		):
			frappe.db.set_value(
				"Property Setter",
				{"doc_type": "Budget", "field_name": fieldname, "property": prop},
				"value",
				value,
			)
		else:
			frappe.make_property_setter(
				{
					"doctype": "Budget",
					"fieldname": fieldname,
					"property": prop,
					"value": value,
					"property_type": "Small Text" if prop == "description" else "Data",
				}
			)


def _backfill_existing_budgets():
	budgets = frappe.get_all(
		"Budget",
		filters={
			"applicable_on_booking_actual_expenses": 1,
			"applicable_on_material_request": 0,
		},
		fields=[
			"name",
			"action_if_annual_budget_exceeded",
			"action_if_accumulated_monthly_budget_exceeded",
		],
	)
	for b in budgets:
		frappe.db.set_value(
			"Budget",
			b.name,
			{
				"applicable_on_material_request": 1,
				"action_if_annual_budget_exceeded_on_mr": b.action_if_annual_budget_exceeded,
				"action_if_accumulated_monthly_budget_exceeded_on_mr": (
					b.action_if_accumulated_monthly_budget_exceeded
				),
			},
			update_modified=False,
		)
