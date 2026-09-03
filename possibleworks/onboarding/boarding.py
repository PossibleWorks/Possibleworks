# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""The stock HRMS boarding chain, built from a submitted Onboarding Applicant.

Stock Frappe runs Job Applicant -> Job Offer -> Employee Onboarding -> Employee. We
create the Employee directly from our own intake record, so this module rebuilds the
chain **behind** the Employee: the Job Applicant and Job Offer exist only because
`Employee Onboarding` declares both as reqd links, and the checklist is the thing HR
actually wants.

That inversion is what makes `EmployeeOnboarding.employee` trustworthy here. In stock
flow the Employee does not exist when the checklist is created, so `set_employee()`
finds nothing and the field sits empty until somebody re-saves. We create the Employee
first AND write the field explicitly, so it identifies the employee from the moment the
record exists -- which is what makes it a safe key to look the checklist up by.
"""

import frappe
from frappe import _
from frappe.utils import today

from possibleworks.boarding_activities import copy_template_activities
from possibleworks.onboarding.constants import (
	BOARDING_DOCTYPE,
	BOARDING_TEMPLATE_DOCTYPE,
	DEFAULT_BOARDING_ACTIVITIES,
	DEFAULT_BOARDING_TEMPLATE_TITLE,
	JOB_APPLICANT_ACCEPTED,
	JOB_APPLICANT_DOCTYPE,
	JOB_OFFER_ACCEPTED,
	JOB_OFFER_DOCTYPE,
)


def validate_designation_present(applicant) -> None:
	"""`before_submit` gate.

	`designation` is optional on our intake record but reqd on Job Offer, so without it
	the chain cannot be built. Gated at submit rather than marked reqd on the doctype
	for the same reason as Work Email: HR seeds a draft before every detail is settled.
	"""
	if not applicant.designation:
		frappe.throw(
			_(
				"Designation is required before submitting -- the Job Offer created for this employee cannot be saved without it."
			),
			title=_("Designation Required"),
		)


def validate_holiday_list_available(applicant) -> None:
	"""`before_submit` gate: a Holiday List must resolve for this employee.

	Checked here, early, because the failure would otherwise surface much later and
	somewhere confusing. The checklist is created as a draft, so nothing reads a holiday
	list during our submit; it is read when HR submits the checklist to generate tasks,
	and at that point `EmployeeBoardingController.get_holiday_list()` takes its
	`if self.employee:` branch and calls `get_holiday_list_for_employee()`. HRMS
	replaces that resolver (`employee_holiday_list` hook) with one that reads ONLY
	submitted `Holiday List Assignment` records -- never `Employee.holiday_list`, never
	`Company.default_holiday_list`.

	A brand-new employee has no assignment of their own, so what matters is whether
	their COMPANY has one effective by the joining date.
	"""
	from hrms.utils.holiday_list import get_assigned_holiday_list

	if get_assigned_holiday_list(applicant.company, applicant.date_of_joining):
		return

	frappe.throw(
		_(
			"No Holiday List is assigned to {0} as of {1}, so the onboarding checklist would not be able to schedule any task. Create a Holiday List Assignment for the company first."
		).format(frappe.bold(applicant.company), frappe.bold(frappe.utils.formatdate(applicant.date_of_joining))),
		title=_("Holiday List Missing"),
	)


def create_job_applicant(applicant) -> str:
	"""Always a fresh Job Applicant -- never reuse one for the same address.

	`Job Applicant.autoname` is the email address, so reuse would put two Employees on
	one `job_applicant`. Two things break then: `EmployeeOnboarding.set_employee()`
	resolves by exactly that field and could hand back the older Employee, and the
	one-per-applicant duplicate guards on Job Offer and Employee Onboarding would refuse
	a rehire outright. `append_number_if_name_exists` suffixes a repeat address instead.
	"""
	doc = frappe.new_doc(JOB_APPLICANT_DOCTYPE)
	doc.applicant_name = applicant.applicant_name
	doc.email_id = applicant.personal_email
	doc.status = JOB_APPLICANT_ACCEPTED
	doc.designation = applicant.designation
	# Custom field from the v1_5 patch. Job Applicant ships with nothing the Observer
	# can resolve a company from, and it is in IMMEDIATE_SEND_DOCTYPES, so leaving this
	# blank means every event for the record is silently dropped as unresolvable.
	if frappe.get_meta(JOB_APPLICANT_DOCTYPE).has_field("company"):
		doc.company = applicant.company
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	return doc.name


def create_job_offer(applicant, job_applicant: str) -> str:
	"""Submitted, Accepted Job Offer for the checklist to point at.

	`offer_date` is today rather than the joining date: this record is minted now, and
	backdating it would misrepresent when the offer was made.

	`validate_vacancies` is neutralised **on this instance only**. Vacancy control is a
	recruitment gate -- it asks "may we hire another of these?" -- and this person has
	already joined, so a full Staffing Plan must not be able to block their onboarding.
	Shadowing the bound method keeps the blast radius to one document: no global setting
	is touched, and every other Job Offer validation, including the duplicate-offer
	guard and any site customisation, still runs.
	"""
	doc = frappe.new_doc(JOB_OFFER_DOCTYPE)
	doc.job_applicant = job_applicant
	doc.applicant_name = applicant.applicant_name
	doc.offer_date = today()
	doc.designation = applicant.designation
	doc.company = applicant.company
	doc.status = JOB_OFFER_ACCEPTED

	doc.validate_vacancies = lambda: None

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)
	doc.submit()

	return doc.name


def ensure_default_boarding_template() -> str:
	"""Get-or-create the shared default template, matched by `title`.

	Looked up by title because `autoname` is a series, so the title is neither the
	record name nor unique -- `frappe.db.exists` on a name would never match.

	Only ever created, never updated. Once a site has this template it owns it: an
	admin who removes an activity or assigns an owner should not have that undone the
	next time somebody is onboarded.
	"""
	existing = frappe.db.get_value(
		BOARDING_TEMPLATE_DOCTYPE, {"title": DEFAULT_BOARDING_TEMPLATE_TITLE}, "name"
	)
	if existing:
		return existing

	template = frappe.new_doc(BOARDING_TEMPLATE_DOCTYPE)
	template.title = DEFAULT_BOARDING_TEMPLATE_TITLE

	for activity in DEFAULT_BOARDING_ACTIVITIES:
		template.append("activities", dict(activity))

	template.flags.ignore_permissions = True
	template.insert(ignore_permissions=True)

	return template.name


def ensure_employee_onboarding(applicant, employee: str) -> str:
	"""Create the checklist as a DRAFT, or return the one already there.

	Draft on purpose: `on_submit` is what creates the Project, the Tasks and the
	assignments, and who owns what differs per hire. HR reviews the activities, sets
	owners, then submits.

	Idempotent by `employee`, which is safe precisely because we write that field
	ourselves rather than leaving it to `set_employee()`.
	"""
	existing = frappe.db.get_value(BOARDING_DOCTYPE, {"employee": employee, "docstatus": ("!=", 2)}, "name")
	if existing:
		return existing

	job_applicant = frappe.db.get_value("Employee", employee, "job_applicant")
	job_offer = frappe.db.get_value(
		JOB_OFFER_DOCTYPE, {"job_applicant": job_applicant, "docstatus": 1}, "name"
	)
	if not (job_applicant and job_offer):
		frappe.throw(
			_("Employee {0} has no Job Applicant and Job Offer, which {1} requires.").format(
				frappe.bold(employee), _(BOARDING_DOCTYPE)
			)
		)

	doc = frappe.new_doc(BOARDING_DOCTYPE)
	doc.job_applicant = job_applicant
	doc.job_offer = job_offer
	# Written explicitly rather than left to `set_employee()`, which resolves by
	# `job_applicant` and only fills a blank field. This is the key the checklist is
	# looked up by, so it must be the Employee we actually created.
	doc.employee = employee
	doc.employee_name = applicant.applicant_name
	doc.company = applicant.company
	doc.department = applicant.department
	doc.designation = applicant.designation
	doc.date_of_joining = applicant.date_of_joining
	# The anchor every task date is counted from -- not the joining date by default,
	# hence set explicitly.
	doc.boarding_begins_on = applicant.date_of_joining
	doc.holiday_list = applicant.holiday_list

	template = ensure_default_boarding_template()
	doc.employee_onboarding_template = template
	copy_template_activities(template, BOARDING_TEMPLATE_DOCTYPE, doc)

	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True)

	return doc.name


def employee_onboarding_missing(employee: str) -> bool:
	"""True when this Employee has no live onboarding checklist."""
	return not frappe.db.exists(
		BOARDING_DOCTYPE, {"employee": employee, "docstatus": ("!=", 2)}
	)
