# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Applicant portal access.

An applicant is not an Employee and has no account, so they get a **Website User**
holding a single role whose `desk_access` is 0. That role is the whole access story:
it grants no DocPerm at all, so even if another endpoint were reached it would permit
nothing. Every portal call instead proves ownership itself --
`doc.applicant_user == frappe.session.user` -- and only then acts with
`ignore_permissions`. Least privilege, and one place to audit.

Login is passwordless. Someone who uses this once for a week should not have to invent
and store a password. The link is HMAC-signed with the site secret using Frappe's own
`frappe.utils.verified_command`, so nothing bespoke is doing the cryptography, and the
signature covers the expiry so it cannot be extended by editing the URL.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import add_days, format_datetime, get_url, getdate, now_datetime, get_datetime
from frappe.utils.verified_command import get_signed_params, verify_request

from possibleworks.utils.branded_email import render_branded_email
from possibleworks.onboarding.constants import (
	APPLICANT_CHILD_TABLE_COLUMNS,
	APPLICANT_EDITABLE_STATUSES,
	APPLICANT_SHOWABLE_CHILD_TABLES,
	APPLICANT_SUBMITTED,
	DOCTYPE,
	HR_ROLES,
	PORTAL_ROLE,
)

# How long an invite stays usable when the Date of Joining is far off.
DEFAULT_INVITE_DAYS = 14

ACCEPT_METHOD = "possibleworks.onboarding.portal.accept_invite"


def build_invite_url(applicant_name: str, expires_on) -> str:
	"""Signed, expiring URL. The expiry is inside the signature deliberately."""
	params = get_signed_params({"applicant": applicant_name, "expires": str(expires_on)})
	return get_url(f"/api/method/{ACCEPT_METHOD}?{params}")


def resolve_expiry(doc) -> object:
	"""Expire at the Date of Joining, or in two weeks, whichever comes first.

	Past the joining date the record is HR's to submit, so a live applicant link has no
	remaining purpose.
	"""
	default = get_datetime(add_days(now_datetime(), DEFAULT_INVITE_DAYS))
	if not doc.date_of_joining:
		return default
	joining = get_datetime(getdate(doc.date_of_joining))
	return min(default, joining) if joining > now_datetime() else default


def ensure_portal_user(doc) -> str:
	"""Create (or reuse) the applicant's Website User.

	Website User + a role with no desk access means this account can never reach /app,
	regardless of what else is configured later.
	"""
	if doc.applicant_user and frappe.db.exists("User", doc.applicant_user):
		return doc.applicant_user

	if not doc.personal_email:
		frappe.throw(
			_("Set a Personal Email before inviting the applicant."),
			title=_("Email Required"),
		)

	existing = frappe.db.exists("User", doc.personal_email)
	if existing:
		user = frappe.get_doc("User", existing)
		if user.user_type != "Website User":
			# Never quietly repurpose a real staff account as an applicant login.
			frappe.throw(
				_("{0} already belongs to a system user. Use a different personal email for this applicant.").format(
					frappe.bold(doc.personal_email)
				),
				title=_("Email Already In Use"),
			)
	else:
		user = frappe.new_doc("User")
		user.email = doc.personal_email
		user.first_name = doc.first_name or doc.applicant_name or doc.personal_email
		user.last_name = doc.last_name
		user.user_type = "Website User"
		user.send_welcome_email = 0
		user.flags.ignore_permissions = True
		user.insert(ignore_permissions=True)

	if not any(role.role == PORTAL_ROLE for role in user.get("roles") or []):
		user.append("roles", {"role": PORTAL_ROLE})
		user.flags.ignore_permissions = True
		user.save(ignore_permissions=True)

	return user.name


@frappe.whitelist(methods=["POST"])
def invite_applicant(name: str) -> dict:
	"""Issue (or re-issue) an applicant's portal invite. HR action."""
	frappe.only_for(HR_ROLES, message=True)

	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("write")

	if doc.docstatus != 0:
		frappe.throw(_("Only a draft can be sent to an applicant."), frappe.PermissionError)

	if doc.status not in APPLICANT_EDITABLE_STATUSES:
		frappe.throw(
			_("This record is not open for applicant edits (status: {0}). Set it back to Awaiting Applicant first.").format(
				_(doc.status)
			)
		)

	user = ensure_portal_user(doc)
	expires_on = resolve_expiry(doc)

	doc.db_set(
		{
			"applicant_user": user,
			"invite_sent_on": now_datetime(),
			"invite_expires_on": expires_on,
		},
		update_modified=False,
	)

	link = build_invite_url(doc.name, expires_on)
	emailed = send_invite_email(doc, link)

	return {"name": doc.name, "user": user, "link": link, "emailed": emailed, "expires_on": expires_on}


@frappe.whitelist(methods=["GET", "POST"])
def get_invite_link(name: str) -> dict:
	"""Current invite link, for HR to copy when email is not configured."""
	frappe.only_for(HR_ROLES, message=True)

	doc = frappe.get_doc(DOCTYPE, name)
	doc.check_permission("read")

	if not doc.invite_expires_on:
		return {"link": None}

	return {"link": build_invite_url(doc.name, doc.invite_expires_on), "expires_on": doc.invite_expires_on}


def build_invite_email(doc, link: str) -> str:
	"""The invite body, separate from sending so it can be asserted on in a test.

	`invite_expires_on` is formatted rather than printed straight from the field: the raw
	value reads as `2026-08-18 00:00:00`, which is a database timestamp shown to someone
	who does not work here.
	"""
	return render_branded_email(
		heading=_("Dear {0},").format(doc.first_name or doc.applicant_name),
		paragraphs=[
			_(
				"Please complete your onboarding details using the secure link below. "
				"It is personal to you, so do not forward it."
			)
		],
		cta={
			"label": _("Open my onboarding form"),
			"url": link,
			"fallback": _("If the button does not open, copy this link into your browser:"),
		},
		notes=[_("The link stops working on {0}.").format(format_datetime(doc.invite_expires_on))],
		signoff=[_("Best regards,"), _("HR Team")],
		footer_note=_(
			"This email was sent from an unmonitored mailbox. You are receiving it because "
			"you are joining {0}."
		).format(doc.company),
	)


def send_invite_email(doc, link: str) -> bool:
	"""Email the link, reporting honestly whether it actually went out.

	A site with no outgoing Email Account is normal in development, so the caller shows
	the link to HR instead of silently doing nothing.
	"""
	if not frappe.db.exists("Email Account", {"enable_outgoing": 1}):
		return False

	try:
		frappe.sendmail(
			recipients=[doc.personal_email],
			subject=_("Complete your onboarding for {0}").format(doc.company),
			message=build_invite_email(doc, link),
			now=True,
		)
		return True
	except Exception:
		frappe.log_error("Onboarding invite email failed", frappe.get_traceback())
		return False


@frappe.whitelist(allow_guest=True, methods=["GET"])
@rate_limit(limit=20, seconds=60)
def accept_invite(applicant: str = None, expires: str = None, **kwargs):
	"""Consume a signed invite link and start the applicant's session.

	Unauthenticated by necessity, so every check is done here: signature, expiry,
	record state, and that the record still points at this user.
	"""
	# Renders its own "Invalid Link" page and returns False when the HMAC fails.
	if not verify_request():
		return

	if not applicant or not frappe.db.exists(DOCTYPE, applicant):
		return invalid_link()

	doc = frappe.get_doc(DOCTYPE, applicant)

	if not doc.applicant_user or not doc.invite_expires_on:
		return invalid_link()

	if get_datetime(expires) != get_datetime(doc.invite_expires_on):
		# A re-issued invite silently retires every earlier link.
		return invalid_link()

	if now_datetime() > get_datetime(doc.invite_expires_on):
		return expired_link()

	if doc.docstatus != 0 or doc.status not in APPLICANT_EDITABLE_STATUSES:
		return closed_link()

	frappe.local.login_manager.login_as(doc.applicant_user)
	frappe.local.response["type"] = "redirect"
	frappe.local.response["location"] = "/onboarding"


# --------------------------------------------------------------------------- #
# Portal actions
# --------------------------------------------------------------------------- #
# Every one of these resolves the record from the SESSION, never from an argument, so
# there is no id for an applicant to tamper with. The portal role holds no DocPerm, so
# these methods are the entire surface available to that identity.


def _get_own_applicant(editable_only: bool = True):
	from possibleworks.www.onboarding import get_applicant_for_session

	doc = get_applicant_for_session()
	if not doc:
		frappe.throw(_("No onboarding form is assigned to this account."), frappe.PermissionError)

	if editable_only and (doc.docstatus != 0 or doc.status not in APPLICANT_EDITABLE_STATUSES):
		frappe.throw(
			_("This onboarding form is no longer open for changes."), frappe.PermissionError
		)
	return doc


def _apply_values(doc, values, tables=None) -> None:
	"""Write only what this record's own snapshot marked editable.

	The template decides what the applicant may touch, so a crafted request cannot set
	a field the form never offered -- even one that is otherwise applicant-writable.
	`is_editable` on the rule is already resolved per record, so a `lock_when_filled`
	field that HR prefilled is silently skipped here as well.
	"""
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	rules = doc.get_applicant_field_rules()

	for fieldname, value in values.items():
		rule = rules.get(fieldname)
		if not rule or not rule.is_editable:
			continue
		if fieldname in APPLICANT_SHOWABLE_CHILD_TABLES:
			# Tables arrive under `tables`; a scalar write to one would replace the rows
			# with a string.
			continue
		doc.set(fieldname, value)

	_apply_tables(doc, tables, rules)


def _apply_tables(doc, tables, rules) -> None:
	"""Replace the applicant's repeating rows wholesale.

	Replace rather than merge: the page posts the complete list it is showing, so a
	deleted row has to disappear, and there is no stable client-side row identity to
	merge against. Only the columns the portal actually renders are read -- anything
	else in the payload is ignored, not rejected, so a stale page cannot fail a save.
	"""
	tables = frappe.parse_json(tables) if isinstance(tables, str) else (tables or {})
	if not tables:
		return

	for fieldname, rows in tables.items():
		if fieldname not in APPLICANT_SHOWABLE_CHILD_TABLES:
			continue

		rule = rules.get(fieldname)
		if not rule or not rule.is_editable:
			continue

		columns = APPLICANT_CHILD_TABLE_COLUMNS.get(fieldname, ())
		doc.set(fieldname, [])

		for row in rows or []:
			if not isinstance(row, dict):
				continue

			values = {key: row.get(key) for key in columns if row.get(key) not in (None, "")}

			# Emptiness is judged on TRUTHINESS, not on presence. The page posts every
			# column it renders, and an untouched checkbox posts 0 -- so a row the
			# applicant added and never filled in still arrives as
			# `{"is_current_employer": 0}`, which is present-but-blank. Testing for
			# presence alone would save that as a nameless job.
			if not any(values.values()):
				# Dropping it is kinder than failing the save on the child table's own
				# mandatory fields.
				continue

			doc.append(fieldname, values)


def _missing(doc) -> list[str]:
	from possibleworks.www.onboarding import missing_required

	return missing_required(doc)


def _state(doc) -> dict:
	"""Everything the page needs to redraw itself without a reload.

	A reload was how uploads used to refresh the document list, and it threw away every
	field the applicant had typed but not yet saved.
	"""
	from possibleworks.www.onboarding import progress_for

	return {"name": doc.name, "missing": _missing(doc), "progress": progress_for(doc)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def portal_save(values: dict | str | None = None, tables: dict | str | None = None) -> dict:
	"""Save progress. Never validates completeness -- half-finished is expected."""
	doc = _get_own_applicant()
	_apply_values(doc, values, tables)
	doc.save(ignore_permissions=True)

	return _state(doc)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def portal_submit(
	values: dict | str | None = None,
	declaration_accepted: bool = False,
	tables: dict | str | None = None,
) -> dict:
	"""Hand the form back to HR.

	docstatus stays 0 -- HR submits the Frappe document later, on or after the Date of
	Joining. What this does is lock the applicant out and record the declaration.
	"""
	doc = _get_own_applicant()

	if not declaration_accepted:
		frappe.throw(_("Please confirm your details before submitting."))

	_apply_values(doc, values, tables)
	doc.save(ignore_permissions=True)

	outstanding = _missing(doc)
	if outstanding:
		frappe.throw(
			_("Please complete the following before submitting:")
			+ "<ul><li>"
			+ "</li><li>".join(frappe.utils.escape_html(item) for item in outstanding)
			+ "</li></ul>",
			title=_("Not Finished Yet"),
		)

	doc.status = APPLICANT_SUBMITTED
	doc.applicant_declaration = 1
	doc.declaration_accepted_on = now_datetime()
	doc.applicant_submitted_on = now_datetime()
	doc.flags.applicant_status_transition = True
	doc.save(ignore_permissions=True)

	return {"name": doc.name, "status": doc.status, "docstatus": doc.docstatus}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def portal_attach(document_type: str, file_url: str, file_name: str | None = None) -> dict:
	"""Link a just-uploaded private file to one of this record's document rows.

	`file_name` is what the applicant called the file, before Frappe possibly renamed it
	to avoid a collision on disk. It is used only for the duplicate check and for
	display, never for storage, so a caller that omits or fakes it can affect nothing
	beyond their own record's tidiness.
	"""
	doc = _get_own_applicant()

	allowed = {row.document_type for row in doc.required_documents if row.enabled}
	if document_type not in allowed:
		frappe.throw(
			_("{0} is not part of your document checklist.").format(document_type),
			frappe.PermissionError,
		)

	file_doc = frappe.db.get_value(
		"File",
		{"file_url": file_url},
		[
			"name",
			"file_name",
			"content_hash",
			"is_private",
			"attached_to_doctype",
			"attached_to_name",
		],
		as_dict=True,
	)
	if not file_doc or (file_doc.attached_to_doctype, file_doc.attached_to_name) != (
		DOCTYPE,
		doc.name,
	):
		frappe.throw(_("That file does not belong to your onboarding record."), frappe.PermissionError)

	if not file_doc.is_private:
		frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
		frappe.throw(_("Documents must be uploaded privately. Please try again."))

	display_name = (file_name or "").strip() or file_doc.file_name
	_reject_duplicate_upload(doc, document_type, file_doc, file_url, display_name)

	row = doc.append(
		"documents",
		{
			"document_type": document_type,
			"attachment": file_url,
			"original_file_name": display_name,
		},
	)
	doc.save(ignore_permissions=True)

	state = _state(doc)
	state["file"] = {
		"row_name": row.name,
		"file_url": file_url,
		"file_name": display_name,
		"document_type": document_type,
	}
	return state


def _reject_duplicate_upload(doc, document_type, file_doc, file_url, display_name) -> None:
	"""Stop one file standing in for two requirements, or two files sharing a name.

	Two halves, and they fail differently.

	**The same file twice** is the damaging one, and it is checked across the WHOLE
	record rather than within one document type. Frappe dedupes uploads on
	content_hash, so re-uploading identical bytes returns the File that is already
	attached -- and two child rows then point at one url. Nothing is corrupted on disk,
	but `validate_required_documents` counts rows, so one PDF silently satisfies both
	"Aadhaar Card" and "PAN Card" and the record reads as complete when only one
	document was ever supplied.

	**Two different files with one name** is the confusing one, and only makes sense
	within a document type. Frappe does not let them collide on disk:
	`generate_file_name` (core/doctype/file/utils.py:192) appends a random
	6-character suffix, so the second is stored as `certificate1a2b3c.pdf`. Comparing
	stored names would therefore never match -- which is why this reads
	`original_file_name` off the row instead. That column exists precisely because
	Frappe's rename destroys the only name worth comparing.

	Rejecting leaves the freshly uploaded File behind, since `upload_file` ran in an
	earlier request and has already committed. Clean it up -- but never when the url is
	one an existing row is using, or the cleanup would delete that row's attachment.
	"""
	sharing = next(
		(row for row in doc.documents if row.attachment == file_url), None
	)

	def reject(message):
		if not sharing:
			frappe.delete_doc("File", file_doc.name, ignore_permissions=True, force=True)
		frappe.throw(message, title=_("Already Uploaded"))

	if sharing:
		if sharing.document_type == document_type:
			reject(
				_("You have already uploaded that exact file for {0}.").format(
					frappe.bold(document_type)
				)
			)
		reject(
			_("That file is already attached as your {0}. Please upload the document for {1} separately.").format(
				frappe.bold(sharing.document_type), frappe.bold(document_type)
			)
		)

	siblings = [
		row
		for row in doc.documents
		if row.document_type == document_type and row.attachment and row.attachment != file_url
	]
	if not siblings:
		return

	wanted = (display_name or "").strip().casefold()
	for row in siblings:
		# Fall back to the stored name for rows written before this column existed, or
		# added from the Desk rather than the portal.
		known = (row.original_file_name or "").strip().casefold()
		if not known:
			known = (
				frappe.db.get_value("File", {"file_url": row.attachment}, "file_name") or ""
			).strip().casefold()

		if wanted and known == wanted:
			reject(
				_("You already have a file called {0} under {1}. Rename it so HR can tell them apart.").format(
					frappe.bold(display_name), frappe.bold(document_type)
				)
			)

	if not file_doc.content_hash:
		return

	duplicate = frappe.db.get_value(
		"File",
		{
			"file_url": ("in", [row.attachment for row in siblings]),
			"content_hash": file_doc.content_hash,
		},
		"file_url",
	)
	if duplicate:
		match = next((row for row in siblings if row.attachment == duplicate), None)
		reject(
			_("That is the same file you already uploaded as {0} under {1}.").format(
				frappe.bold((match and match.original_file_name) or display_name),
				frappe.bold(document_type),
			)
		)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def portal_remove_document(row_name: str) -> dict:
	doc = _get_own_applicant()

	row = next((r for r in doc.documents if r.name == row_name), None)
	if not row:
		frappe.throw(_("No such document on your record."), frappe.PermissionError)

	document_type = row.document_type
	doc.remove(row)
	doc.save(ignore_permissions=True)

	state = _state(doc)
	state["document_type"] = document_type
	return state


def invalid_link():
	frappe.respond_as_web_page(
		_("Invalid Link"),
		_("This onboarding link is not valid. Please ask your HR contact to send a new one."),
		indicator_color="red",
	)


def expired_link():
	frappe.respond_as_web_page(
		_("Link Expired"),
		_("This onboarding link has expired. Please ask your HR contact to send a new one."),
		indicator_color="orange",
	)


def closed_link():
	frappe.respond_as_web_page(
		_("Onboarding Closed"),
		_("This onboarding form is no longer open for changes. Please contact your HR contact."),
		indicator_color="blue",
	)
