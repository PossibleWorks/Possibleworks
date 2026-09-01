# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Field metadata for the PF (Provident Fund) compliance export template.

Column order matches the outsourced team's template exactly:
UAN Number#~#Employee Name#~#Gross Salary#~#EPF Wages#~#EPS Wages#~#EDLI Wages#~#
EPF#~#EPS#~#ERPF#~#LOP Days#~#Refund

Two kinds of fields:
- "fixed"     — value always comes straight from Employee/Salary Slip, no user
                mapping needed (see FIXED_FIELD_SOURCE for exactly where from).
- "component" — value comes from whatever Salary Component the user maps this
                field to on the export page, read off that employee's Salary
                Slip for the selected period. If the mapped component has no
                row on a given employee's slip (or the field was left
                unmapped), the value is "NA" for that employee — never
                calculated or assumed.
"""

NA = "NA"

PF_COLUMNS = [
	"UAN Number",
	"Employee Name",
	"Gross Salary",
	"EPF Wages",
	"EPS Wages",
	"EDLI Wages",
	"EPF",
	"EPS",
	"ERPF",
	"LOP Days",
	"Refund",
]

# fieldname -> human description, shown on the export page so HR understands
# what each template column means before mapping/downloading.
PF_FIELD_DESCRIPTIONS = {
	"UAN Number": "Employee's Universal Account Number (PF identity number).",
	"Employee Name": "Employee's name as on the payslip.",
	"Gross Salary": "Total gross wages for the selected period.",
	"EPF Wages": "Wage base the employee's PF contribution is calculated on.",
	"EPS Wages": "Wage base for the Employee Pension Scheme (EPS) contribution.",
	"EDLI Wages": "Wage base for the Employees Deposit Linked Insurance (EDLI) contribution.",
	"EPF": "Employee's own PF contribution amount for the period.",
	"EPS": "Employer's Pension Scheme contribution amount for the period.",
	"ERPF": "Employer's residual PF contribution (employer PF share other than EPS).",
	"LOP Days": "Loss-of-pay / non-contributing days in the period.",
	"Refund": "Refund of a PF advance adjusted in this period, if any.",
}

# Fields whose value is read directly off Employee/Salary Slip — never mapped
# by the user, never looked up in Salary Detail.
PF_FIXED_FIELDS = ["UAN Number", "Employee Name", "Gross Salary", "LOP Days"]

# Fields the user maps to a Salary Component on the export page.
PF_MAPPED_FIELDS = ["EPF Wages", "EPS Wages", "EDLI Wages", "EPF", "EPS", "ERPF", "Refund"]

# Safety cap on how many period rows get_period_months() will ever return,
# in case a Payroll Period document has a malformed/huge date range.
MAX_PERIOD_MONTHS = 60


# =============================================================================
# ESCI (ESIC monthly contribution) export
#
# Column order/labels match the real ESIC "Sample MC Excel Template" exactly:
# IP Number, IP Name, No of Days, Total Monthly Wages, Reason Code, Last
# Working Day. Per the user: Reason Code and Last Working Day are explicitly
# out of scope for this export — always left blank, no employee-status-based
# exit logic. Only "Total Monthly Wages" is mapped to a Salary Component; the
# rest are fixed, same split style as the PF export above.
# =============================================================================

ESCI_COLUMNS = [
	"IP Number",
	"IP Name",
	"No of Days",
	"Total Monthly Wages",
	"Reason Code",
	"Last Working Day",
]

ESCI_FIELD_DESCRIPTIONS = {
	"IP Number": "Employee's ESIC Insured Person (IP) number.",
	"IP Name": "Employee's name as on the payslip.",
	"No of Days": "Days wages were paid/payable in the period, rounded up to a whole number.",
	"Total Monthly Wages": "Total wages for ESI contribution purposes for the period.",
	"Reason Code": "Not used for this export — always left blank.",
	"Last Working Day": "Not used for this export — always left blank.",
}

ESCI_FIXED_FIELDS = ["IP Number", "IP Name", "No of Days", "Reason Code", "Last Working Day"]

ESCI_MAPPED_FIELDS = ["Total Monthly Wages"]
