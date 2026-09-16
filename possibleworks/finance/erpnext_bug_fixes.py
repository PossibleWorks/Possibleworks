"""
Runtime patches for confirmed bugs in pinned, read-only core apps (frappe/
erpnext/hrms) -- see each function's docstring for the upstream bug and why
it can only be fixed here rather than at the source.

Imported once from hooks.py so it applies at app boot, before any report can
run.
"""

import frappe.desk.reportview as reportview

_original_build_match_conditions = reportview.build_match_conditions


def _build_match_conditions_with_leading_space(doctype, user=None, as_condition=True):
	"""Fix for a real ERPNext core bug (confirmed on erpnext 16.4.1) that
	breaks Trial Balance, Balance Sheet, Profit and Loss, and Cash Flow for
	any user who has a "Company" User Permission (auto-created whenever an
	Employee record is linked to both a user and a company).

	`erpnext.accounts.report.financial_statements.get_accounting_entries`
	does:

		match_conditions = build_match_conditions(doctype)
		if match_conditions:
			query += "and" + match_conditions

	`build_match_conditions` (this function, unpatched) returns the bare
	condition with no leading space -- e.g. `coalesce(...)='' OR ... IN (...)`.
	Concatenated with the literal `"and"` above, with no spaces on either
	side, "and" + "coalesce(...)" fuses into the single invalid SQL token
	`andcoalesce`, and MySQL/MariaDB rejects the query outright.

	The correctly-spaced sibling function two lines above in the same core
	file, `get_match_cond`, already does `" and " + cond` -- financial_
	statements.py should have called that instead, but didn't, and core is
	read-only here. Patching `build_match_conditions` to always carry its
	own leading space is a safe, minimal fix: SQL is whitespace-insensitive,
	so every other core caller that already prefixes its own connector
	keyword is unaffected (a stray extra space is never a syntax error),
	while this one buggy caller is fixed for free.
	"""
	result = _original_build_match_conditions(doctype, user=user, as_condition=as_condition)
	if as_condition and result and not result.startswith(" "):
		return " " + result
	return result


reportview.build_match_conditions = _build_match_conditions_with_leading_space
