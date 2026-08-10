# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.utils.csvutils import build_csv_response
from frappe.utils.xlsxutils import build_xlsx_response

from possibleworks.hr_documents.page.payroll_compliance_export.constants import PF_COLUMNS
from possibleworks.hr_documents.page.payroll_compliance_export.pf_export import (
	build_pf_rows,
	get_period_months,
)

# Same role set as the Page's own `roles` list (Page JSON) — kept here too as
# defense-in-depth against a direct API call bypassing the Desk page.
ALLOWED_ROLES = ["System Manager", "HR Manager", "HR User"]


def _parse_mapping(mapping):
	"""`mapping` arrives from the client as a JSON string; accept a dict too
	so this is safe to call directly (e.g. from bench console) without
	re-serializing."""
	if isinstance(mapping, str):
		return json.loads(mapping) if mapping else {}
	return mapping or {}


@frappe.whitelist()
def get_periods(payroll_period):
	"""Return the selected Payroll Period's company plus its pre-resolved
	list of payroll-cycle months, so the page's Month dropdown never has to
	guess at date math itself."""
	frappe.only_for(ALLOWED_ROLES)
	doc = frappe.get_doc("Payroll Period", payroll_period)
	return {"company": doc.company, "months": get_period_months(payroll_period)}


@frappe.whitelist()
def preview_pf_export(payroll_period, start_date, end_date, mapping):
	"""Return the resolved PF export rows for on-page review before download."""
	frappe.only_for(ALLOWED_ROLES)
	company = frappe.get_doc("Payroll Period", payroll_period).company
	rows = build_pf_rows(company, start_date, end_date, _parse_mapping(mapping))
	return {"columns": PF_COLUMNS, "rows": rows}


@frappe.whitelist()
def download_pf_export(payroll_period, start_date, end_date, mapping, file_format="Excel"):
	"""Stream the PF export as a CSV or Excel download."""
	frappe.only_for(ALLOWED_ROLES)
	company = frappe.get_doc("Payroll Period", payroll_period).company
	rows = build_pf_rows(company, start_date, end_date, _parse_mapping(mapping))
	if not rows:
		frappe.throw(
			_("No submitted Salary Slips found for {0} between {1} and {2}").format(
				company, start_date, end_date
			)
		)

	table = [PF_COLUMNS] + [[row.get(column, "") for column in PF_COLUMNS] for row in rows]
	filename = f"PF Export {company} {start_date} to {end_date}"
	if file_format == "CSV":
		build_csv_response(table, filename)
	else:
		build_xlsx_response(table, filename)
