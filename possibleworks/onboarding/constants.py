# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Shared constants for the Onboarding module.

Kept free of `frappe` imports so it can be imported from patches, tests and
`hooks.py` callbacks without pulling in a request context.
"""

DOCTYPE = "Onboarding Applicant"
DOCUMENT_TYPE_DOCTYPE = "Onboarding Document Type"
DOCUMENT_TEMPLATE_DOCTYPE = "Onboarding Document Template"

DEFAULT_TEMPLATE_NAME = "Default Onboarding Documents"

# --------------------------------------------------------------------------- #
# Statuses
# --------------------------------------------------------------------------- #
# docstatus stays 0 (Draft) across every one of these except Onboarded/Cancelled,
# which are set at submit/cancel time. `status` is deliberately a plain Select
# managed in code rather than a Frappe Workflow, so no per-site workflow config is
# required and it can never fight with docstatus.

AWAITING_APPLICANT = "Awaiting Applicant"
APPLICANT_SUBMITTED = "Applicant Submitted"
HR_REVIEW = "HR Review"
READY_TO_ONBOARD = "Ready to Onboard"
ONBOARDED = "Onboarded"
CANCELLED = "Cancelled"

STATUSES = (
	AWAITING_APPLICANT,
	APPLICANT_SUBMITTED,
	HR_REVIEW,
	READY_TO_ONBOARD,
	ONBOARDED,
	CANCELLED,
)

# Statuses in which the external onboarding app may still write applicant fields.
APPLICANT_EDITABLE_STATUSES = frozenset({AWAITING_APPLICANT})

# Statuses the integration role is allowed to see at all (enforced by the
# permission_query_conditions / has_permission hooks). Everything submitted or
# archived is invisible to a leaked integration key.
APPLICANT_VISIBLE_STATUSES = frozenset({
	AWAITING_APPLICANT,
	APPLICANT_SUBMITTED,
	HR_REVIEW,
	READY_TO_ONBOARD,
})

# Allowed status transitions, enforced in validate(). Onboarded/Cancelled are set
# only by on_submit/on_cancel via db_set, so they are terminal here.
STATUS_TRANSITIONS = {
	AWAITING_APPLICANT: {APPLICANT_SUBMITTED, HR_REVIEW, READY_TO_ONBOARD},
	APPLICANT_SUBMITTED: {AWAITING_APPLICANT, HR_REVIEW, READY_TO_ONBOARD},
	HR_REVIEW: {AWAITING_APPLICANT, READY_TO_ONBOARD},
	READY_TO_ONBOARD: {AWAITING_APPLICANT, HR_REVIEW, ONBOARDED},
	ONBOARDED: {CANCELLED},
	CANCELLED: set(),
}

# --------------------------------------------------------------------------- #
# Roles
# --------------------------------------------------------------------------- #

HR_ROLES = ("System Manager", "HR Manager", "HR User")
HR_REVERSAL_ROLES = ("System Manager", "HR Manager")

# Auto-created by DocType.on_update -> make_module_and_roles from the permissions
# block; no fixture or patch needed.
INTEGRATION_ROLE = "Onboarding Integration"

# The applicant's portal identity. Deliberately granted NO DocPerm anywhere: portal
# endpoints prove ownership themselves and then act with ignore_permissions, so this
# role by itself permits nothing. Its `desk_access` is forced to 0 by the v1_4 patch --
# roles auto-created from a permissions block default to 1.
PORTAL_ROLE = "Onboarding Applicant Portal"

# --------------------------------------------------------------------------- #
# Mass-assignment allowlist
# --------------------------------------------------------------------------- #
# `read_only: 1` is a client-side hint with NO server-side enforcement in _save,
# so /api/resource PUT, /api/v2/document PATCH and run_doc_method can all set any
# field. This allowlist is enforced in validate(), which every one of those paths
# goes through.

APPLICANT_WRITABLE_FIELDS = frozenset({
	# Identity
	"salutation",
	"first_name",
	"middle_name",
	"last_name",
	"gender",
	"date_of_birth",
	"marital_status",
	"blood_group",
	"image",
	# Contact
	"personal_email",
	"cell_number",
	# Statutory identifiers
	"aadhar_number",
	"pan_number",
	"passport_number",
	# Address
	"current_address",
	"current_accommodation_type",
	"same_as_current_address",
	"permanent_address",
	"permanent_accommodation_type",
	# Emergency contact
	"person_to_be_contacted",
	"relation",
	"emergency_phone_number",
	# Bank
	"salary_mode",
	"bank_name",
	"bank_ac_no",
	"bank_account_holder_name",
	"ifsc_code",
	"micr_code",
	"iban",
	"provident_fund_account",
})

# Set by before_validate() or by our own API, never accepted from the caller, but
# they do legitimately change during an applicant write so the diff must allow them.
APPLICANT_SELF_MANAGED_FIELDS = frozenset({
	"applicant_name",
	"applicant_declaration",
	"declaration_accepted_on",
	"applicant_submitted_on",
	"total_experience_years",
})

APPLICANT_WRITABLE_CHILD_TABLES = frozenset({
	"education",
	"external_work_history",
	"documents",
	"pending_employee_fields",
})

# Derived by the controller, never supplied by any caller. `required_documents` is a
# snapshot of the selected template, and `document_template` -- the only thing that can
# change it -- is already HR-only, so a derived change here is safe to exempt from the
# mass-assignment diff. Without this, an applicant's very first save would be rejected
# for "changing" a table the system just populated.
APPLICANT_SYSTEM_CHILD_TABLES = frozenset({"required_documents", "applicant_fields"})

# Everything else -- date_of_joining, company, employee, employee_number,
# company_email, department, designation, branch, employment_type, grade,
# reports_to, holiday_list, default_shift, status, hr_remarks, document_template,
# amended_from -- is HR-only.

# --------------------------------------------------------------------------- #
# Document type seed data
# --------------------------------------------------------------------------- #
# Inserted only if absent. The seeding patch NEVER overwrites is_required /
# allow_multiple on an existing row -- once installed, the site owns its own
# required-document policy. That is the configurability requirement.

DEFAULT_DOCUMENT_TYPES = (
	{
		"document_type_name": "Aadhaar Card",
		"is_required": 1,
		"allow_multiple": 0,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "PAN Card",
		"is_required": 1,
		"allow_multiple": 0,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Cancelled Cheque",
		"is_required": 1,
		"allow_multiple": 0,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Passport Size Photograph",
		"is_required": 1,
		"allow_multiple": 0,
		"allowed_extensions": "jpg,jpeg,png",
	},
	{
		"document_type_name": "Educational Certificate",
		"is_required": 0,
		"allow_multiple": 1,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Relieving Letter",
		"is_required": 0,
		"allow_multiple": 1,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Experience Letter",
		"is_required": 0,
		"allow_multiple": 1,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Recent Payslips",
		"is_required": 0,
		"allow_multiple": 1,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Address Proof",
		"is_required": 0,
		"allow_multiple": 0,
		"allowed_extensions": "pdf,jpg,jpeg,png",
	},
	{
		"document_type_name": "Signed Offer Letter",
		"is_required": 0,
		"allow_multiple": 0,
		"allowed_extensions": "pdf",
	},
	{
		"document_type_name": "Other",
		"is_required": 0,
		"allow_multiple": 1,
		"allowed_extensions": "",
	},
)
