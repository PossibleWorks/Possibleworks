import json

import frappe
from frappe import _
from frappe.utils import flt, get_last_day, getdate

from erpnext.accounts.doctype.budget.budget import (
	get_accumulated_monthly_budget,
	get_actions,
	get_actual_expense,
	get_ordered_amount,
	revise_budget,
)
from erpnext.accounts.utils import get_fiscal_year

_ACTION_RANK = {"Stop": 2, "Warn": 1}


def _pick_more_restrictive(exceed_a, action_a, exceed_b, action_b):
	"""Whichever of two independent budget-dimension verdicts is more restrictive wins -
	Stop beats Warn beats "did not exceed," the same principle _check_one_line already
	applies when several overlapping Budget rows disagree on a line."""
	rank_a = _ACTION_RANK.get(action_a, 0) if exceed_a else -1
	rank_b = _ACTION_RANK.get(action_b, 0) if exceed_b else -1
	return (exceed_b, action_b) if rank_b > rank_a else (exceed_a, action_a)


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
			b.distribution_frequency,
			IFNULL(b.applicable_on_booking_actual_expenses, 0) AS for_actual_expenses,
			IFNULL(b.applicable_on_material_request, 0) AS for_material_request,
			IFNULL(b.applicable_on_purchase_order, 0) AS for_purchase_order,
			IFNULL(b.applicable_on_cumulative_expense, 0) AS for_cumulative_expense,
			b.action_if_annual_budget_exceeded,
			b.action_if_accumulated_monthly_budget_exceeded,
			b.action_if_annual_budget_exceeded_on_mr,
			b.action_if_accumulated_monthly_budget_exceeded_on_mr,
			b.action_if_annual_budget_exceeded_on_po,
			b.action_if_accumulated_monthly_budget_exceeded_on_po,
			b.action_if_annual_exceeded_on_cumulative_expense,
			b.action_if_accumulated_monthly_exceeded_on_cumulative_expense
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


def get_indent_outstanding_amount(company, account, cost_center, from_date, to_date, exclude_name=None):
	"""Sum of (stock_qty - ordered_qty) * rate across submitted Purchase Indent rows for
	this account + cost_center + date range -- the budget commitment an approved indent
	still represents.

	erpnext's own cumulative-expense check (BudgetValidation.handle_cumulative_overlimit)
	only knows about Purchase Order + Material Request + Actual Expense -- it has no
	concept of Purchase Indent at all, since that's a doctype this app adds, not one
	erpnext ships. Folding this in here is what makes "cumulative" actually mean
	something for this app's real procurement flow, where Indent (not Material Request)
	is the first commitment step.

	`ordered_qty` is kept in step with submitted Purchase Orders by
	purchase_indent_status.update_ordered_qty, so a fully-converted row's outstanding
	amount is already zero here -- no separate "is this Completed" check needed, the
	same way Material Request's own requested_amount already subtracts ordered_qty
	rather than checking Purchase Order status.

	`exclude_name`: the indent currently being submitted, if any -- its own amount is
	the proposed_amount this total gets compared against, not part of what's already
	committed.
	"""
	exclude_condition = "AND parent.name != %(exclude_name)s" if exclude_name else ""
	return flt(
		frappe.db.sql(
			f"""
			SELECT IFNULL(SUM((child.stock_qty - child.ordered_qty) * child.rate), 0)
			FROM `tabPurchase Indent Item` child, `tabPurchase Indent` parent
			WHERE parent.name = child.parent
				AND child.expense_account = %(account)s
				AND child.cost_center = %(cost_center)s
				AND parent.company = %(company)s
				AND parent.docstatus = 1
				AND parent.status NOT IN ('Stopped', 'Cancelled')
				AND child.stock_qty > child.ordered_qty
				AND parent.transaction_date BETWEEN %(from_date)s AND %(to_date)s
				{exclude_condition}
			""",
			{
				"account": account,
				"cost_center": cost_center,
				"company": company,
				"from_date": from_date,
				"to_date": to_date,
				"exclude_name": exclude_name,
			},
		)[0][0]
	)


def _get_current_period(budget_name, posting_date):
	"""The single Budget Distribution row covering posting_date, if any.

	Used only by approve_budget_increase to find which period's own
	allocation to bump. The exceeded-budget check itself uses erpnext's own
	get_accumulated_monthly_budget - unused budget from earlier periods in
	the year rolls forward and is available in later periods.
	"""
	rows = frappe.db.sql(
		"""
		SELECT name, amount, start_date, end_date
		FROM `tabBudget Distribution`
		WHERE parent = %s
			AND %s BETWEEN start_date AND end_date
		LIMIT 1
		""",
		(budget_name, posting_date),
		as_dict=True,
	)
	return rows[0] if rows else None


def _check_one_line(
	company, account, cost_center, posting_date, proposed_amount, doctype=None, exclude_name=None
):
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
				"doctype": doctype,
			}
		)


		action_lookup_params = params
		if doctype == "Purchase Indent":
			action_lookup_params = frappe._dict(params)
			action_lookup_params["doctype"] = "Material Request"

		yearly_action, monthly_action = get_actions(action_lookup_params, budget)

		annual_actual = flt(get_actual_expense(params))
		annual_total = annual_actual + proposed_amount
		would_exceed_annual = annual_total > flt(budget.budget_amount)

		monthly_amount = None
		monthly_actual = None
		would_exceed_monthly = False
		if monthly_action in ("Stop", "Warn"):
			monthly_amount = flt(get_accumulated_monthly_budget(budget.name, posting_date))
			accumulated_params = frappe._dict(params)
			accumulated_params["month_end_date"] = get_last_day(posting_date)
			monthly_actual = flt(get_actual_expense(accumulated_params))
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


		if budget.get("for_cumulative_expense"):
			cumulative_actual = (
				annual_actual
				+ flt(get_ordered_amount(params))
				+ get_indent_outstanding_amount(
					company,
					account,
					cost_center,
					budget.budget_start_date,
					budget.budget_end_date,
					exclude_name=exclude_name,
				)
			)
			cumulative_total = cumulative_actual + proposed_amount
			cum_would_exceed_annual = cumulative_total > flt(budget.budget_amount)
			cum_monthly_amount = (
				monthly_amount
				if monthly_amount is not None
				else flt(get_accumulated_monthly_budget(budget.name, posting_date))
			)
			cum_would_exceed_monthly = cumulative_total > cum_monthly_amount

			cum_annual_action = budget.action_if_annual_exceeded_on_cumulative_expense
			cum_monthly_action = budget.action_if_accumulated_monthly_exceeded_on_cumulative_expense
			if exception_role and exception_role in frappe.get_roles(frappe.session.user):
				if cum_annual_action == "Stop":
					cum_annual_action = "Warn"
				if cum_monthly_action == "Stop":
					cum_monthly_action = "Warn"

			would_exceed_annual, effective_annual_action = _pick_more_restrictive(
				would_exceed_annual, effective_annual_action, cum_would_exceed_annual, cum_annual_action
			)
			would_exceed_monthly, effective_monthly_action = _pick_more_restrictive(
				would_exceed_monthly, effective_monthly_action, cum_would_exceed_monthly, cum_monthly_action
			)
			if monthly_amount is None:
				monthly_amount = cum_monthly_amount

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
			# The period label the "monthly" (really: per-period) check is scoped
			# to - this app's Budgets aren't always distributed Monthly; a Budget
			# can equally be Quarterly / Half-Yearly / Yearly (erpnext's own
			# distribution_frequency options), and _get_current_period above
			# already resolves whichever single period covers posting_date
			# regardless of which of the four it is.
			"distribution_frequency": budget.distribution_frequency,
			# Echoed back so a later budget-increase approval bumps the SAME
			# period that was actually breached, not whatever period happens to
			# contain the approval date (which may be a different day).
			"posting_date": str(posting_date),
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
def check_budget(company, posting_date, lines, doctype=None, exclude_name=None):
	"""
	Dry-run budget check for one or more proposed accounting lines - never
	throws, only reports. Call this BEFORE attempting a real submit.

	lines: JSON-encoded string, a list of {"account", "cost_center", "amount"}
	doctype: the calling document's doctype - selects the matching
		per-trigger-point action pair the same way erpnext's own get_actions()
		does: "Purchase Order" -> `..._on_po`, "Purchase Indent" -> `..._on_mr`
		(the Budget form's "Applicable on Material Request" slot is relabelled
		"Applicable on Purchase Indent" and repurposed for this - see
		patches/v1_14 - because Material Request itself can never be
		budget-checked in this product; _check_one_line presents Indent to
		get_actions() as "Material Request" to reach that pair). Anything else
		(Purchase Invoice, Payment Entry, Journal Entry) falls back to the
		generic "booking actual expenses" action pair. Independently of this, a
		Budget with "Applicable on Cumulative Expense" checked is evaluated for
		every doctype (see _check_one_line) - that dimension combines Purchase
		Order + Purchase Indent (outstanding) + Actual Expense into one total
		(Material Request excluded there too, for the same reason).
	exclude_name: an already-submitted document re-checking itself (e.g. the
		real backend enforcement in enforce_cumulative_budget_on_submit) -
		excluded from its own doctype's "already committed" total so it isn't
		counted against itself alongside being the proposed_amount.
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
				doctype=doctype,
				exclude_name=exclude_name,
			)
		)

	return {
		"would_block_submit": any(r.get("would_block") for r in results),
		"lines": results,
	}


def enforce_cumulative_budget_on_submit(doc, method=None):
	"""`doc_events` entry point for on_submit, registered for both Purchase
	Indent and Purchase Order (see hooks.py) - any doctype whose child table
	carries `expense_account` / `cost_center` / `amount` per row can reuse this
	as-is.

	Runs AFTER docstatus has flipped to 1 (same point erpnext's own
	buying_controller.validate_budget() runs), so this document's own rows are
	already visible to get_indent_outstanding_amount's query if it's an Indent -
	exclude_name keeps it from being double-counted against itself alongside
	being the proposed_amount being checked.
	"""
	by_account_and_cost_center = {}
	for item in doc.get("items") or []:
		key = (item.get("expense_account"), item.get("cost_center"))
		if not all(key):
			continue
		by_account_and_cost_center[key] = by_account_and_cost_center.get(key, 0) + flt(item.get("amount"))

	for (account, cost_center), proposed_amount in by_account_and_cost_center.items():
		result = _check_one_line(
			company=doc.company,
			account=account,
			cost_center=cost_center,
			posting_date=doc.transaction_date,
			proposed_amount=proposed_amount,
			doctype=doc.doctype,
			exclude_name=doc.name,
		)
		if not result.get("budget_found"):
			continue

		exceeded_annual = result["would_exceed_annual"] and result["annual_action"] in ("Stop", "Warn")
		exceeded_monthly = result["would_exceed_monthly"] and result["monthly_action"] in ("Stop", "Warn")
		if not (exceeded_annual or exceeded_monthly):
			continue

		action = result["annual_action"] if exceeded_annual else result["monthly_action"]
		scope = _("Annual") if exceeded_annual else _("Accumulated")
		msg = _("{0} Budget for Account {1} against Cost Center {2} would be exceeded by {3}.").format(
			scope,
			frappe.bold(account),
			frappe.bold(cost_center),
			frappe.bold(frappe.utils.fmt_money(proposed_amount, currency=frappe.get_cached_value(
				"Company", doc.company, "default_currency"
			))),
		)
		if action == "Stop":
			frappe.throw(msg, title=_("Budget Exceeded"))
		else:
			frappe.msgprint(msg, title=_("Budget Exceeded"))


@frappe.whitelist()
def approve_budget_increase(budget_name, new_amount, posting_date=None):
	"""
	Raises a submitted Budget's amount using ERPNext's own legitimate
	revise-budget mechanism (cancel + copy to a new draft revision).

	`new_amount` is the APPROVED INCREASE (the delta the requester asked
	for), not a new total - it is added to the existing annual
	budget_amount, and added to just the single Budget Distribution period
	that covers `posting_date` (the date the original request was raised;
	falls back to today if not given). Every other period's own allocation
	is left exactly as it was. `distribute_equally` is turned off on the
	revision so the one-period bump can never be silently re-split evenly
	across the year by a later save.

	The period bump and total bump are both written via frappe.db.set_value
	directly, BEFORE the revision is reloaded and submitted - submitting a
	freshly-reloaded doc whose in-memory values already match what's in the
	DB means Budget.allocate_budget() sees nothing "changed" and leaves the
	distribution rows alone, instead of regenerating them from the new total.
	"""
	new_amount = flt(new_amount)
	reference_date = getdate(posting_date) if posting_date else getdate(frappe.utils.nowdate())

	new_budget_name = revise_budget(budget_name)

	old_total = flt(frappe.db.get_value("Budget", new_budget_name, "budget_amount"))
	new_total = old_total + new_amount

	period = _get_current_period(new_budget_name, reference_date)
	if period and period.get("name"):
		frappe.db.set_value(
			"Budget Distribution",
			period["name"],
			"amount",
			flt(period["amount"]) + new_amount,
			update_modified=False,
		)

	frappe.db.set_value(
		"Budget",
		new_budget_name,
		{
			"budget_amount": new_total,
			"distribute_equally": 0,
			"budget_distribution_total": new_total,
		},
		update_modified=False,
	)

	new_budget = frappe.get_doc("Budget", new_budget_name)
	new_budget.submit()

	return {
		"previous_budget": budget_name,
		"name": new_budget.name,
		"budget_amount": new_budget.budget_amount,
	}
