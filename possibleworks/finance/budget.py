import json

import frappe
from frappe import _
from frappe.utils import flt, get_last_day, getdate

from erpnext.accounts.doctype.budget.budget import (
	get_accumulated_monthly_budget,
	get_actions,
	get_actual_expense,
	revise_budget,
)
from erpnext.accounts.utils import get_fiscal_year


# =============================================================================
# INTERNAL CORE LOGIC
# =============================================================================
# Mirrors erpnext.accounts.doctype.budget.budget.validate_expense_against_budget
# / validate_budget_records / compare_expense_with_budget, but NEVER throws or
# msgprints - it only reports numbers, so it can be safely called ahead of a
# real submit (a dry run). Scoped to Cost Center budgets only (this app's
# doctypes always carry cost_center, not project).

def _find_cost_center_budgets(company, account, cost_center, posting_date):
	"""Submitted Budget rows covering this account+cost_center+date, Cost Center dimension only."""
	return frappe.db.sql(
		"""
		SELECT
			b.name,
			b.budget_amount,
			b.from_fiscal_year,
			b.to_fiscal_year,
			b.budget_start_date,
			b.budget_end_date,
			IFNULL(b.applicable_on_booking_actual_expenses, 0) AS for_actual_expenses,
			b.action_if_annual_budget_exceeded,
			b.action_if_accumulated_monthly_budget_exceeded
		FROM `tabBudget` b
		WHERE b.company = %s
			AND b.docstatus = 1
			AND %s BETWEEN b.budget_start_date AND b.budget_end_date
			AND b.account = %s
			AND b.cost_center = %s
			AND b.budget_against = 'Cost Center'
		""",
		(company, posting_date, account, cost_center),
		as_dict=True,
	)


def _check_one_line(company, account, cost_center, posting_date, proposed_amount):
	proposed_amount = flt(proposed_amount)
	posting_date = getdate(posting_date)

	root_type = frappe.get_cached_value("Account", account, "root_type")
	if root_type != "Expense":
		# ERPNext's own budget engine only ever applies to Expense accounts -
		# nothing to check for any other account type.
		return {
			"account": account,
			"cost_center": cost_center,
			"budget_found": False,
			"would_block": False,
		}

	budgets = _find_cost_center_budgets(company, account, cost_center, posting_date)
	if not budgets:
		return {
			"account": account,
			"cost_center": cost_center,
			"budget_found": False,
			"would_block": False,
		}

	# If several overlapping Budget rows exist, report the tightest (most
	# restrictive) one - the one that blocks first is the one that matters.
	best = None

	for budget in budgets:
		if not flt(budget.budget_amount):
			continue

		params = frappe._dict(
			{
				"company": company,
				"account": account,
				"cost_center": cost_center,
				"posting_date": posting_date,
				"budget_against_field": "cost_center",
				"is_tree": False,
				"from_fiscal_year": budget.from_fiscal_year,
				"to_fiscal_year": budget.to_fiscal_year,
				"budget_start_date": budget.budget_start_date,
				"budget_end_date": budget.budget_end_date,
				"for_actual_expenses": budget.for_actual_expenses,
				"doctype": None,
			}
		)

		yearly_action, monthly_action = get_actions(params, budget)

		annual_actual = flt(get_actual_expense(params))
		annual_total = annual_actual + proposed_amount
		would_exceed_annual = annual_total > flt(budget.budget_amount)

		monthly_amount = None
		monthly_actual = None
		would_exceed_monthly = False
		if monthly_action in ("Stop", "Warn"):
			monthly_amount = flt(get_accumulated_monthly_budget(budget.name, posting_date))
			params["month_end_date"] = get_last_day(posting_date)
			monthly_actual = flt(get_actual_expense(params))
			would_exceed_monthly = (monthly_actual + proposed_amount) > monthly_amount

		effective_annual_action = yearly_action
		effective_monthly_action = monthly_action

		# Same standing bypass ERPNext itself honours: a user holding the
		# company's exception_budget_approver_role gets Stop downgraded to Warn.
		exception_role = frappe.get_cached_value(
			"Company", company, "exception_budget_approver_role"
		)
		if exception_role and exception_role in frappe.get_roles(frappe.session.user):
			if effective_annual_action == "Stop":
				effective_annual_action = "Warn"
			if effective_monthly_action == "Stop":
				effective_monthly_action = "Warn"

		would_block = (would_exceed_annual and effective_annual_action == "Stop") or (
			would_exceed_monthly and effective_monthly_action == "Stop"
		)

		line_result = {
			"account": account,
			"cost_center": cost_center,
			"budget_found": True,
			"budget_name": budget.name,
			"budget_amount": flt(budget.budget_amount),
			"accumulated_monthly_budget": monthly_amount,
			"actual_spent": annual_actual,
			"proposed_amount": proposed_amount,
			"would_exceed_annual": would_exceed_annual,
			"would_exceed_monthly": would_exceed_monthly,
			"annual_action": effective_annual_action or "Ignore",
			"monthly_action": effective_monthly_action or "Ignore",
			"would_block": would_block,
		}

		if best is None or (line_result["would_block"] and not best["would_block"]):
			best = line_result

	return best or {
		"account": account,
		"cost_center": cost_center,
		"budget_found": False,
		"would_block": False,
	}


# =============================================================================
# WHITELISTED API
# =============================================================================

@frappe.whitelist()
def check_budget(company, posting_date, lines):
	"""
	Dry-run budget check for one or more proposed accounting lines - never
	throws, only reports. Call this BEFORE attempting a real submit.

	lines: JSON-encoded string, a list of {"account", "cost_center", "amount"}
	Returns: {"would_block_submit": bool, "lines": [ ...per-line results... ]}
	"""
	if isinstance(lines, str):
		lines = json.loads(lines)

	results = []
	for line in lines:
		results.append(
			_check_one_line(
				company=company,
				account=line.get("account"),
				cost_center=line.get("cost_center"),
				posting_date=posting_date,
				proposed_amount=line.get("amount"),
			)
		)

	return {
		"would_block_submit": any(r.get("would_block") for r in results),
		"lines": results,
	}


@frappe.whitelist()
def approve_budget_increase(budget_name, new_amount):
	"""
	Raises a submitted Budget's amount using ERPNext's own legitimate
	revise-budget mechanism (cancel + copy to a new draft revision), then
	sets the approved amount and submits the revision.
	"""
	new_amount = flt(new_amount)

	new_budget_name = revise_budget(budget_name)
	new_budget = frappe.get_doc("Budget", new_budget_name)
	new_budget.budget_amount = new_amount
	new_budget.save(ignore_permissions=True)
	new_budget.submit()

	return {
		"previous_budget": budget_name,
		"name": new_budget.name,
		"budget_amount": new_budget.budget_amount,
	}
