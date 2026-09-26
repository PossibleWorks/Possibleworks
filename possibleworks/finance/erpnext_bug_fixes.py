"""
Runtime patches for confirmed bugs in pinned, read-only core apps (frappe/
erpnext/hrms) -- see each function's docstring for the upstream bug and why
it can only be fixed here rather than at the source.

Imported once from hooks.py so it applies at app boot, before any report can
run.
"""

import frappe.desk.reportview as reportview

_original_build_match_conditions = reportview.build_match_conditions


def _build_match_conditions_wrapped(doctype, user=None, as_condition=True):
	"""Fix for two real ERPNext core bugs (confirmed on erpnext 16.4.1), both
	caused by callers naively splicing this function's raw return value into
	a larger WHERE clause instead of treating it as a self-contained unit.

	`build_match_conditions` returns a condition representing "the current
	user's permission restriction", meant to be AND-ed with the rest of a
	query -- e.g. for a user with a Company User Permission (auto-created
	whenever an Employee record is linked to both a user and a company), it
	returns something like:

		IFNULL(`tabGL Entry`.`company`,'')='' OR `tabGL Entry`.`company` IN ('Finance')

	Note the *top-level* `OR` and the lack of a leading space or wrapping
	parentheses -- the function assumes the caller adds both. Two core
	reports don't:

	1. `erpnext.accounts.report.financial_statements.get_accounting_entries`:

		match_conditions = build_match_conditions(doctype)
		if match_conditions:
			query += "and" + match_conditions

	   With no leading space, "and" + "IFNULL(...)" fuses into the single
	   invalid SQL token `andifnull`, and MySQL/MariaDB rejects the query
	   outright -- breaks Trial Balance, Balance Sheet, Profit and Loss, and
	   Cash Flow for any such user.

	2. `erpnext.accounts.report.general_ledger.general_ledger.get_conditions`:

		conditions.append(match_conditions)
		...
		return "and {}".format(" and ".join(conditions))

	   This *is* correctly spaced, but never wraps match_conditions in
	   parentheses. Since it contains a top-level OR, SQL's normal AND-binds-
	   tighter-than-OR precedence means that OR silently applies to the
	   *entire* preceding chain of conditions -- including this report's own
	   party/party_type/date-range filters -- rather than being confined to
	   just the permission check. For any such user, clicking "View Ledger"
	   for one supplier/customer instead shows the company's *entire*,
	   completely unfiltered General Ledger, with no error at all: the
	   permission condition's OR ends up satisfied by ordinary company-scoped
	   rows, which silently short-circuits every other filter ANDed before it.

	Both are fixed at once by making this function always return its
	condition already wrapped in parentheses with a leading space --
	`" (IFNULL(...)='' OR ... IN (...))"`. That makes it safe to concatenate
	after *any* connector keyword, spaced or not, and safe to AND together
	with anything else regardless of what operators it contains internally.
	SQL is whitespace- and parenthesis-insensitive for callers that already
	handle this correctly (frappe.desk.reportview.get_match_cond, the
	correctly-written sibling of this function, already does `" and (" + cond
	+ ")"` -- both buggy reports above should have called that instead, but
	didn't, and core is read-only here), so this is safe for every existing
	caller, not just the two confirmed-broken ones.
	"""
	result = _original_build_match_conditions(doctype, user=user, as_condition=as_condition)
	if as_condition and result:
		return f" ({result})"
	return result


reportview.build_match_conditions = _build_match_conditions_wrapped
