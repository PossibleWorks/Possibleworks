# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from possibleworks.hr_documents.page.payroll_compliance_export.common import (
	get_salary_slips_with_components,
)
from possibleworks.hr_documents.page.payroll_compliance_export.constants import NA, PF_MAPPED_FIELDS


def build_pf_rows(company, start_date, end_date, mapping):
	"""Build PF export rows for the given period. `mapping` is
	{field_name: salary_component_name} for each field in PF_MAPPED_FIELDS
	(component_name may be "" / missing, meaning "not mapped").

	Never calculates or assumes a value: EPF Wages/EPS Wages/EDLI Wages/EPF/
	EPS/ERPF/Refund are read straight off the mapped Salary Component's
	`amount` on that employee's Salary Slip, and are "NA" whenever the field
	isn't mapped or that component has no row on this particular slip.
	"""
	slips = get_salary_slips_with_components(company, start_date, end_date)

	rows = []
	for slip in slips:
		row = {
			"UAN Number": slip.uan_number or NA,
			"Employee Name": slip.employee_name,
			"Gross Salary": slip.gross_pay,
			# Days not paid for, for any reason (LWP leave, plain absence,
			# half-days, unmarked-attendance treated as absent, etc.) — all of
			# those already reduce payment_days below total_working_days, so
			# this stays correct without having to track each cause itself.
			"LOP Days": slip.total_working_days - slip.payment_days,
		}
		for field in PF_MAPPED_FIELDS:
			component = mapping.get(field)
			row[field] = slip.components.get(component, NA) if component else NA
		rows.append(row)

	return rows
