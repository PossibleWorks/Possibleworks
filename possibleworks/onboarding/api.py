# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Whitelisted endpoints for the external onboarding application.

Auth: the external app holds a service user's API key and secret and sends
`Authorization: token <api_key>:<api_secret>`. That resolves to a real `User`
(frappe/auth.py:validate_api_key_secret), so every DocPerm, User Permission and
`has_permission` hook applies exactly as it does in the Desk.

Route version: call these on **v1** -- `/api/method/possibleworks.onboarding.api.<fn>`.
`@rate_limit` builds its cache key from `frappe.form_dict.cmd` (rate_limiter.py:153),
which v1 sets and v2 does not; on v2 every endpoint would share a single bucket and a
chatty poll would starve document uploads.

Do NOT point the external app at `/api/resource/...` PUT or `/api/v2/document/...`
PATCH. Both do `doc.update(request_body); doc.save()` -- unbounded mass assignment.
The allowlist in `OnboardingApplicant.enforce_applicant_field_allowlist` defends them
regardless, but the app should not be told they exist.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import now_datetime

from possibleworks.onboarding import pending_fields
from possibleworks.onboarding.constants import (
	APPLICANT_EDITABLE_STATUSES,
	APPLICANT_READONLY_FIELDS,
	APPLICANT_SHOWABLE_CHILD_TABLES,
	APPLICANT_SUBMITTED,
	APPLICANT_TEMPLATE_FIELDS,
	APPLICANT_WRITABLE_FIELDS,
	DOCTYPE,
	DOCUMENT_TEMPLATE_DOCTYPE,
	DOCUMENT_TYPE_DOCTYPE,
	HR_REVERSAL_ROLES,
	ONBOARDED,
)
from possibleworks.onboarding.employee_fields import EMPLOYEE_DOCTYPE

# Applicant-supplied child rows. Keys not listed are ignored rather than rejected, so
# a newer external app sending an extra field cannot break an older Frappe.
EDUCATION_KEYS = (
	"school_univ",
	"qualification",
	"level",
	"year_of_passing",
	"start_year",
	"class_per",
	"maj_opt_subj",
	"certificate",
	"is_highest_qualification",
)

WORK_HISTORY_KEYS = (
	"company_name",
	"designation",
	"from_date",
	"to_date",
	"is_current_employer",
	"salary",
	"address",
	"contact",
	"notice_period_days",
	"reason_for_leaving",
	"relieving_letter",
)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _get_editable(name: str):
	"""Load a record the applicant is still allowed to write to."""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")

	if doc.docstatus != 0:
		frappe.throw(
			_("Onboarding record {0} is no longer a draft.").format(name),
			frappe.PermissionError,
		)

	if doc.status not in APPLICANT_EDITABLE_STATUSES:
		frappe.throw(
			_("This onboarding record is not open for edits (status: {0}).").format(_(doc.status)),
			frappe.PermissionError,
		)

	return doc


def _replace_table(doc, fieldname: str, rows, allowed_keys) -> None:
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	if not isinstance(rows, list):
		frappe.throw(_("{0} must be a list of rows.").format(fieldname))

	doc.set(fieldname, [])
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("{0} must be a list of objects.").format(fieldname))
		doc.append(fieldname, {key: row.get(key) for key in allowed_keys if key in row})


def _missing_applicant_fields(doc) -> list[str]:
	"""Fields the applicant still has to fill in before they can hand over.

	Advisory on save; enforced on `submit_applicant_section`.
	"""
	required = ["first_name", "last_name", "personal_email", "cell_number", "date_of_birth", "gender"]
	if doc.salary_mode == "Bank":
		required += ["bank_name", "bank_ac_no", "ifsc_code"]

	return [
		_(doc.meta.get_label(fieldname))
		for fieldname in required
		if not doc.get(fieldname) and doc.meta.has_field(fieldname)
	]


def _bulleted(items: list[str]) -> str:
	return "<ul><li>{0}</li></ul>".format(
		"</li><li>".join(frappe.utils.escape_html(item) for item in items)
	)


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #
# These allow POST as well as GET on purpose. Desk's `frappe.call` / `frappe.xcall`
# always issue a POST, and `is_valid_http_method` (frappe/handler.py) rejects a
# mismatch with a bare `throw_permission_error()` -- the user just sees "Not permitted"
# with no clue which call failed. GET stays allowed so the external app can use proper
# REST semantics for reads.


@frappe.whitelist(methods=["GET", "POST"])
@rate_limit(key="name", limit=120, seconds=60)
def get_applicant(name: str) -> dict:
	"""Applicant-visible projection of an onboarding record.

	Deliberately a projection, not `doc.as_dict()`: HR-only fields (`hr_remarks`,
	`employee`, `employee_number`, `hr_remarks`) are never serialised even
	though the integration role technically has DocPerm read on the doctype.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")

	payload = {fieldname: doc.get(fieldname) for fieldname in sorted(APPLICANT_WRITABLE_FIELDS)}
	payload.update(
		{
			"name": doc.name,
			"applicant_name": doc.applicant_name,
			"status": doc.status,
			"docstatus": doc.docstatus,
			"editable": doc.docstatus == 0 and doc.status in APPLICANT_EDITABLE_STATUSES,
			# Read-only context the applicant is allowed to see.
			"company": doc.company,
			"date_of_joining": doc.date_of_joining,
			"designation": doc.designation,
			"department": doc.department,
			"applicant_submitted_on": doc.applicant_submitted_on,
			"applicant_declaration": doc.applicant_declaration,
			"education": [
				dict(row.as_dict(no_default_fields=True), row_name=row.name) for row in doc.education
			],
			"work_history": [
				dict(row.as_dict(no_default_fields=True), row_name=row.name)
				for row in doc.external_work_history
			],
			"documents": [
				{
					"row_name": row.name,
					"document_type": row.document_type,
					"file_name": frappe.db.get_value(
						"File", {"file_url": row.attachment}, "file_name"
					),
					"remarks": row.remarks,
				}
				for row in doc.documents
				if row.attachment
			],
			"missing": _missing_applicant_fields(doc),
		}
	)
	return payload


@frappe.whitelist(methods=["GET", "POST"])
@rate_limit(key="name", limit=240, seconds=60)
def list_document_types(name: str) -> list[dict]:
	"""This applicant's document checklist.

	Served from the record's own snapshot of its template, so the external app shows
	exactly what this hire must provide -- not a site-wide list -- and stays in step
	with what `before_submit` will actually enforce.
	"""
	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")

	uploaded = {row.document_type for row in doc.documents if row.attachment}
	descriptions = dict(
		frappe.get_all(
			DOCUMENT_TYPE_DOCTYPE, fields=["name", "description"], as_list=True
		)
	)

	return [
		{
			"name": row.document_type,
			"document_type_name": row.document_type,
			"is_required": row.is_required,
			"allow_multiple": row.allow_multiple,
			"allowed_extensions": row.allowed_extensions
			or frappe.db.get_value(DOCUMENT_TYPE_DOCTYPE, row.document_type, "allowed_extensions"),
			"instructions": row.instructions,
			"description": descriptions.get(row.document_type),
			"uploaded": row.document_type in uploaded,
		}
		for row in doc.required_documents
		if row.enabled
	]


@frappe.whitelist(methods=["GET", "POST"])
def list_applicant_field_options() -> list[dict]:
	"""Fields a template may offer to applicants.

	Derived from live meta intersected with APPLICANT_TEMPLATE_FIELDS, so the picker
	can never offer something the mass-assignment allowlist would later reject, and it
	stays correct as the form changes.

	Includes the two repeating tables (Education, Work Experience) and the display-only
	fields, each labelled so HR can see what they are choosing.
	"""
	frappe.has_permission(DOCUMENT_TEMPLATE_DOCTYPE, "read", throw=True)

	meta = frappe.get_meta(DOCTYPE)
	options = []
	for fieldname in sorted(APPLICANT_TEMPLATE_FIELDS):
		df = meta.get_field(fieldname)
		if not df:
			continue

		if fieldname in APPLICANT_SHOWABLE_CHILD_TABLES:
			kind = _("list of rows")
		elif fieldname in APPLICANT_READONLY_FIELDS:
			kind = _("display only")
		else:
			kind = df.fieldtype

		options.append(
			{
				"value": fieldname,
				"label": df.label,
				"description": f"{df.label} ({kind})",
				"fieldtype": df.fieldtype,
				"is_table": fieldname in APPLICANT_SHOWABLE_CHILD_TABLES,
				"is_readonly": fieldname in APPLICANT_READONLY_FIELDS,
			}
		)
	return options


@frappe.whitelist(methods=["GET", "POST"])
def get_pending_fields(applicant: str | dict) -> dict:
	"""Employee fields this site makes mandatory that are still outstanding.

	`applicant` is either a docname, or the full document as a dict -- the Desk form
	passes `frm.doc`, which may be unsaved or dirty.

	Returns each field with its `fieldtype` and `options` so ANY client can render a
	matching control: a Link or Select comes back with the information needed to draw
	a real dropdown. Desk and the external app share this one contract, so they can
	never disagree about what is outstanding.
	"""
	# Gate on Employee: the response describes the Employee schema, so a caller with
	# no business creating Employees has no business seeing it.
	frappe.has_permission(EMPLOYEE_DOCTYPE, "create", throw=True)

	if isinstance(applicant, str) and applicant.strip().startswith("{"):
		applicant = frappe.parse_json(applicant)

	if isinstance(applicant, dict):
		# Untrusted input, but only ever used to build an in-memory Employee -- nothing
		# is written.
		doc = frappe.get_doc(applicant)
		doc.check_permission("create" if doc.is_new() else "write")
	else:
		doc = frappe.get_doc(DOCTYPE, applicant)
		doc.check_permission("read")

	return pending_fields.describe(doc)


# --------------------------------------------------------------------------- #
# Write
# --------------------------------------------------------------------------- #


@frappe.whitelist(methods=["POST"])
@rate_limit(key="name", limit=60, seconds=60)
def save_applicant(
	name: str,
	values: dict | str | None = None,
	education: list | str | None = None,
	work_history: list | str | None = None,
) -> dict:
	"""Save as draft.

	Writes only applicant-writable fields. `docstatus` stays 0 and `status` is
	unchanged -- this is the applicant pressing Save, not handing over.
	"""
	doc = _get_editable(name)

	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	if not isinstance(values, dict):
		frappe.throw(_("values must be an object."))

	rejected = sorted(set(values) - APPLICANT_WRITABLE_FIELDS)
	if rejected:
		frappe.throw(
			_("These fields cannot be set by the onboarding app: {0}").format(
				", ".join(frappe.bold(field) for field in rejected)
			),
			frappe.PermissionError,
		)

	for fieldname, value in values.items():
		doc.set(fieldname, value)

	if education is not None:
		_replace_table(doc, "education", education, EDUCATION_KEYS)
	if work_history is not None:
		_replace_table(doc, "external_work_history", work_history, WORK_HISTORY_KEYS)

	doc.save()

	return {
		"name": doc.name,
		"status": doc.status,
		"docstatus": doc.docstatus,
		# Advisory only -- saving a half-filled form is allowed on purpose.
		"missing": _missing_applicant_fields(doc),
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(key="name", limit=10, seconds=60)
def submit_applicant_section(name: str, declaration_accepted: bool = False) -> dict:
	"""The applicant's "I'm done".

	`docstatus` REMAINS 0 -- HR submits the Frappe document later, and only on or
	after the Date of Joining. What this actually does is validate fully and revoke
	the applicant's write access by moving `status` to Applicant Submitted. HR can
	send it back for correction by setting `status` to Awaiting Applicant again.
	"""
	doc = _get_editable(name)

	if not declaration_accepted:
		frappe.throw(_("Please accept the declaration before submitting."))

	missing = _missing_applicant_fields(doc)
	if missing:
		frappe.throw(
			_("Please complete the following before submitting:") + _bulleted(missing),
			title=_("Incomplete Application"),
		)

	doc.validate_required_documents()

	doc.status = APPLICANT_SUBMITTED
	doc.applicant_declaration = 1
	doc.declaration_accepted_on = now_datetime()
	doc.applicant_submitted_on = now_datetime()
	# Tells the allowlist that this one status transition is ours, not the caller's.
	doc.flags.applicant_status_transition = True
	doc.save()

	return {"name": doc.name, "status": doc.status, "docstatus": doc.docstatus}


@frappe.whitelist(methods=["POST"])
@rate_limit(key="name", limit=30, seconds=60)
def attach_document(
	name: str, document_type: str, file_url: str, remarks: str | None = None
) -> dict:
	"""Link an already-uploaded private file to the record.

	Two steps by design, because `upload_file` cannot address a child-table Attach
	field:

	    POST /api/method/upload_file          (multipart)
	        file=<binary> doctype=Onboarding Applicant docname=<name> is_private=1
	    POST /api/method/possibleworks.onboarding.api.attach_document
	        {"name": ..., "document_type": ..., "file_url": <from step 1>}

	Use `upload_file`, not `frappe.client.attach_file` -- the latter calls `doc.save()`
	internally when `docfield` is passed, running full validation on an incomplete draft.
	"""
	doc = _get_editable(name)

	file_doc = frappe.db.get_value(
		"File",
		{"file_url": file_url},
		["name", "is_private", "attached_to_doctype", "attached_to_name", "file_name"],
		as_dict=True,
	)
	if not file_doc:
		frappe.throw(_("No such file: {0}").format(file_url))

	# The file must belong to THIS record, or a caller could graft any readable file
	# onto any onboarding record.
	if (file_doc.attached_to_doctype, file_doc.attached_to_name) != (DOCTYPE, name):
		frappe.throw(
			_("File {0} is not attached to this onboarding record.").format(file_url),
			frappe.PermissionError,
		)

	if not file_doc.is_private:
		# Discard the leak rather than leaving a public copy of an Aadhaar card lying
		# around, then refuse.
		frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
		frappe.throw(
			_(
				"Documents must be uploaded as private files. The public upload has been discarded -- please re-upload with is_private=1."
			)
		)

	doc.append(
		"documents",
		{
			"document_type": document_type,
			"attachment": file_url,
			# Best available here: this caller uploads on the applicant's behalf and
			# never sees the original name. The portal, which does, sends its own.
			"original_file_name": file_doc.file_name,
			"remarks": remarks,
		},
	)
	# validate_documents re-checks privacy, allow_multiple and the extension list.
	doc.save()

	return {
		"name": doc.name,
		"row_name": doc.documents[-1].name,
		"file_name": file_doc.file_name,
	}


@frappe.whitelist(methods=["POST"])
@rate_limit(key="name", limit=30, seconds=60)
def remove_document(name: str, row_name: str) -> dict:
	"""Remove a document row. The underlying File is left in place deliberately --
	deleting it would break any other record that deduplicated onto the same content
	hash."""
	doc = _get_editable(name)

	row = next((r for r in doc.documents if r.name == row_name), None)
	if not row:
		frappe.throw(_("No such document row {0} on {1}").format(row_name, name))

	doc.remove(row)
	doc.save()

	return {"name": doc.name, "removed": row_name}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def relink_employee(name: str) -> dict:
	"""Repair a submitted record whose `employee` link is empty.

	This state is reachable because the Observer calls `frappe.db.commit()` inside
	`Employee.after_insert` (Employee is in IMMEDIATE_SEND_DOCTYPES), so a failure
	between the insert and the link write cannot be rolled back. The record is already
	docstatus=1 and therefore cannot be re-submitted, so this is the only way out.
	"""
	frappe.only_for(HR_REVERSAL_ROLES, message=True)

	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("submit")

	if doc.employee:
		return {"name": doc.name, "employee": doc.employee, "relinked": False}

	existing = doc.get_employee_from_chain()
	if not existing:
		frappe.throw(
			_("No Employee was created from this onboarding record, so there is nothing to relink."),
			title=_("Nothing to Relink"),
		)

	doc.db_set({"employee": existing, "status": ONBOARDED}, update_modified=False)

	return {"name": doc.name, "employee": existing, "relinked": True}
