# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import math

from possibleworks.hr_documents.page.payroll_compliance_export.common import (
	get_salary_slips_with_components,
)
from possibleworks.hr_documents.page.payroll_compliance_export.constants import NA


def build_esci_rows(company, start_date, end_date, mapping):
	"""Build ESCI export rows for the given period. `mapping` is
	{"Total Monthly Wages": salary_component_name} (component_name may be ""
	/ missing, meaning "not mapped").

	IP Number/IP Name/No of Days come straight off Employee/Salary Slip.
	Total Monthly Wages is read off the mapped Salary Component's `amount` on
	that employee's Salary Slip, "NA" if unmapped or not present on this
	particular slip — same no-calculation approach as the PF export.

	Reason Code and Last Working Day are always left blank: no
	employee-status-based exit logic is implemented for this export.
	"""
	slips = get_salary_slips_with_components(company, start_date, end_date)

	rows = []
	for slip in slips:
		wages_component = mapping.get("Total Monthly Wages")
		rows.append({
			"IP Number": slip.esic_number or NA,
			"IP Name": slip.employee_name,
			# ESCI template's own instructions require a whole number of days,
			# rounding fractional payment_days UP rather than to the nearest.
			"No of Days": math.ceil(slip.payment_days),
			"Total Monthly Wages": (
				slip.components.get(wages_component, NA) if wages_component else NA
			),
			"Reason Code": "",
			"Last Working Day": "",
		})

	return rows
