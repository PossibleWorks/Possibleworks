# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Mapping from `Onboarding Applicant` to `Employee`.

Deliberately hand-rolled rather than using `frappe.model.mapper.get_mapped_doc`.

`map_fields` (frappe/model/mapper.py:184-211) iterates the TARGET doctype's fields
and blind-copies any same-named source value, honouring only source-side `no_copy`.
Employee has ~110 fields, so an intake doctype designed to grow is exactly the wrong
shape for implicit mapping. Two concrete collisions today:

    Onboarding Applicant.status = "Ready to Onboard"
        -> Employee.status  (reqd Select: Active/Inactive/Suspended/Left)
        -> ValidationError

    Onboarding Applicant.naming_series = "HR-ONB-.YYYY.-"
        -> Employee.naming_series
        -> Employee silently named HR-ONB-2026-00001 instead of HR-EMP-0001

An explicit allowlist cannot regress that way; a `field_no_map` blocklist breaks the
day someone adds a colliding field. Hand-rolling also lets us set the reverse link
before `insert()`, which matters because the Observer commits mid-transaction (see
`onboarding_applicant.py`).
"""

import frappe
from frappe.utils import cint, flt, sbool, to_timedelta

EMPLOYEE_DOCTYPE = "Employee"

# Onboarding Applicant fieldname -> Employee fieldname.
#
# Deliberately absent:
#   status           - set explicitly to "Active"; see module docstring
#   employee_number  - conditional on HR Settings.emp_created_by, see apply_employee_number
#   applicant_name   - Employee.employee_name is read_only and recomputed by
#                      Employee.set_employee_name()
#   same_as_current_address, bank_account_holder_name, hr_remarks,
#   applicant_* - no Employee counterpart
EMPLOYEE_FIELD_MAP = {
	# Identity
	"salutation": "salutation",
	"first_name": "first_name",
	"middle_name": "middle_name",
	"last_name": "last_name",
	"gender": "gender",
	"date_of_birth": "date_of_birth",
	"marital_status": "marital_status",
	"blood_group": "blood_group",
	"image": "image",
	# Contact
	"personal_email": "personal_email",
	"cell_number": "cell_number",
	# Statutory identifiers (India regional / our own custom fields)
	"pan_number": "pan_number",
	"aadhar_number": "aadhar_number",
	"passport_number": "passport_number",
	# Address
	"current_address": "current_address",
	"current_accommodation_type": "current_accommodation_type",
	"permanent_address": "permanent_address",
	"permanent_accommodation_type": "permanent_accommodation_type",
	# Emergency contact
	"person_to_be_contacted": "person_to_be_contacted",
	"relation": "relation",
	"emergency_phone_number": "emergency_phone_number",
	# Bank / payroll
	"salary_mode": "salary_mode",
	"bank_name": "bank_name",
	"bank_ac_no": "bank_ac_no",
	"ifsc_code": "ifsc_code",
	"micr_code": "micr_code",
	"iban": "iban",
	"provident_fund_account": "provident_fund_account",
	# Organisation
	"company": "company",
	"date_of_joining": "date_of_joining",
	"company_email": "company_email",
	"department": "department",
	"designation": "designation",
	"branch": "branch",
	"employment_type": "employment_type",
	"grade": "grade",
	"reports_to": "reports_to",
	"holiday_list": "holiday_list",
	"default_shift": "default_shift",
	"total_experience_years": "custom_years_of_experience",
}

# Targets that only exist on some sites, so a missing one is expected rather than a
# bug. India regional setup creates the first four; HRMS creates the next three;
# possibleworks' own fixtures create custom_years_of_experience; our v1_2 patch
# creates aadhar_number.
OPTIONAL_EMPLOYEE_TARGETS = frozenset({
	"pan_number",
	"ifsc_code",
	"micr_code",
	"provident_fund_account",
	"aadhar_number",
	"employment_type",
	"grade",
	"default_shift",
	"custom_years_of_experience",
})

# Shared columns copied verbatim into the Employee child tables. Our child doctypes
# are a strict superset -- the intake-only extras (certificate, from_date, to_date,
# relieving_letter, ...) stay on the onboarding record as the audit trail.
EDUCATION_FIELDS = (
	"school_univ",
	"qualification",
	"level",
	"year_of_passing",
	"class_per",
	"maj_opt_subj",
)

WORK_HISTORY_FIELDS = (
	"company_name",
	"designation",
	"salary",
	"address",
	"contact",
	"total_experience",
)


def coerce_value(value, fieldtype: str):
	"""Convert a stored string back into the Python type the Employee field expects.

	`pending_employee_fields.value` is a Long Text, so everything round-trips as a
	string regardless of the Employee field's real type.

	Date/Datetime/Currency/Float are delegated to `hrms.hr.utils.get_formatted_value`
	so we stay bug-compatible with Employee Promotion/Transfer, which is the existing
	consumer of this exact pattern. It handles user number formats (e.g. "1.234,56"
	under #.###,##) but covers nothing else -- no Int, Check, Time, Percent or
	Duration -- hence the rest here.
	"""
	if value is None or value == "":
		return None

	if fieldtype in ("Date", "Datetime", "Currency", "Float"):
		from hrms.hr.utils import get_formatted_value

		return get_formatted_value(value, fieldtype)

	if fieldtype in ("Check", "Int", "Long Int"):
		return cint(sbool(value))

	if fieldtype in ("Percent", "Duration", "Rating"):
		return flt(value)

	if fieldtype == "Time":
		return to_timedelta(value)

	# Data / Link / Select / Small Text / Text / Text Editor / Attach / ...
	return value


def uses_employee_number_naming() -> bool:
	"""True when HR Settings names Employees by Employee Number.

	`EmployeeMaster.autoname` (hrms/overrides/employee_master.py) does
	`self.name = self.employee_number` in that mode, so the value HR entered becomes
	the Employee's ID. Under "Naming Series" or "Full Name" the field is hidden and
	reqd:0 upstream, and writing it would just be noise.
	"""
	return frappe.db.get_single_value("HR Settings", "emp_created_by") == "Employee Number"


def apply_employee_number(applicant, employee) -> None:
	"""Carry HR's Employee ID across, but only when it is actually used for naming."""
	if uses_employee_number_naming():
		employee.employee_number = applicant.employee_number


def build_employee(applicant):
	"""Return an unsaved, in-memory `Employee` built from `applicant`.

	Used for BOTH the pre-submit dry run and the real creation, so the fields we
	check for completeness can never drift from the fields we actually write.

	Side-effect free: `frappe.new_doc` deep-copies a request-local template and only
	reads from the DB. `autoname` is not called (that happens in `insert()`), so no
	naming series is consumed and `EmployeeMaster.autoname` never fires. NestedSet is
	inert until `on_update`.
	"""
	employee = frappe.new_doc(EMPLOYEE_DOCTYPE)
	meta = frappe.get_meta(EMPLOYEE_DOCTYPE)

	for source_field, target_field in EMPLOYEE_FIELD_MAP.items():
		if not meta.has_field(target_field):
			# Site without India regional setup / HRMS custom fields.
			continue
		value = applicant.get(source_field)
		if value not in (None, ""):
			employee.set(target_field, value)

	# Never mapped -- an onboarding status must not leak into Employee.status.
	employee.status = "Active"

	apply_employee_number(applicant, employee)

	for row in applicant.get("education") or []:
		employee.append("education", {f: row.get(f) for f in EDUCATION_FIELDS})

	for row in applicant.get("external_work_history") or []:
		employee.append("external_work_history", {f: row.get(f) for f in WORK_HISTORY_FIELDS})

	# Site-specific mandatory Employee fields captured via the pending-fields panel.
	for row in applicant.get("pending_employee_fields") or []:
		if not row.fieldname or not meta.has_field(row.fieldname):
			continue
		value = coerce_value(row.value, row.fieldtype or meta.get_field(row.fieldname).fieldtype)
		if value not in (None, ""):
			employee.set(row.fieldname, value)

	return employee
