# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Whitelisted endpoint for the PossibleWorks server.

Called once, when a manager approves a resignation in PossibleWorks, with the
tenant's admin API key and secret (`Authorization: token <api_key>:<api_secret>`).

Route version: call this on **v1** --
`/api/method/possibleworks.offboarding.api.create_employee_separation`. `@rate_limit`
builds its cache key from `frappe.form_dict.cmd` (rate_limiter.py:153), which v1 sets
and v2 does not.

Why a method and not a plain `POST /api/resource/Employee Separation`: the template
link alone does not populate `activities` -- that only happens client-side, in
`employee_separation.js` -- so a REST insert produces a checklist with no rows, which
submits to a Project with no tasks and sends no tiles. Everything that has to happen
in one place (template get-or-create, row copy, owner resolution, the holiday-list
gate, the duplicate guard `Employee Separation` does not ship with) lives in
`separation.py` next to the identical onboarding code, rather than being rebuilt in
TypeScript.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import getdate

from possibleworks.offboarding import separation
from possibleworks.offboarding.constants import (
	NOTICE_WINDOW_DAYS,
	SEPARATION_DOCTYPE,
)
from possibleworks.onboarding.constants import HR_ROLES


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def create_employee_separation(
	employee: str, notice_end_date: str, resignation_letter_date: str | None = None
) -> dict:
	"""Create the exit checklist for `employee` as a draft, or return the existing one.

	`notice_end_date` is the last working day the approving manager set in
	PossibleWorks. It is not stored on the record as-is -- it is the anchor
	`boarding_begins_on` is derived from, NOTICE_WINDOW_DAYS earlier.

	Safe to call twice: idempotent on the employee, so a retry after a network failure
	returns the record the first attempt created rather than minting a second one.
	"""
	frappe.only_for(HR_ROLES, message=True)

	if not frappe.db.exists("DocType", SEPARATION_DOCTYPE):
		frappe.throw(
			_("HRMS is not installed on this site, so {0} cannot be created.").format(
				_(SEPARATION_DOCTYPE)
			)
		)

	employee = (employee or "").strip()
	if not employee:
		frappe.throw(_("Employee is required."), frappe.MandatoryError)

	if not notice_end_date:
		frappe.throw(
			_("Notice period end date is required -- the exit checklist is scheduled backwards from it."),
			frappe.MandatoryError,
		)

	try:
		last_working_day = getdate(notice_end_date)
	except Exception:
		frappe.throw(
			_("{0} is not a valid date.").format(frappe.bold(notice_end_date)),
			frappe.ValidationError,
		)

	result = separation.ensure_employee_separation(
		employee=employee,
		notice_end_date=last_working_day,
		resignation_letter_date=resignation_letter_date,
	)

	return {
		**result,
		"employee": employee,
		"notice_end_date": str(last_working_day),
		"notice_window_days": NOTICE_WINDOW_DAYS,
	}
