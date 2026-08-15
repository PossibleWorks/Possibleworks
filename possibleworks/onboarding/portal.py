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
from frappe.utils import add_days, get_url, getdate, now_datetime, get_datetime
from frappe.utils.verified_command import get_signed_params, verify_request

from possibleworks.onboarding.constants import (
	APPLICANT_EDITABLE_STATUSES,
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
			message=_(
				"<p>Hello {0},</p>"
				"<p>Please complete your onboarding details using the secure link below. "
				"It is personal to you, so do not forward it.</p>"
				"<p><a href='{1}'>Open my onboarding form</a></p>"
				"<p>The link stops working on {2}.</p>"
			).format(doc.first_name or doc.applicant_name, link, doc.invite_expires_on),
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


def _apply_values(doc, values) -> None:
	"""Write only what this record's own snapshot marked editable.

	The template decides what the applicant may touch, so a crafted request cannot set
	a field the form never offered -- even one that is otherwise applicant-writable.
	"""
	values = frappe.parse_json(values) if isinstance(values, str) else (values or {})
	rules = doc.get_applicant_field_rules()

	for fieldname, value in values.items():
		rule = rules.get(fieldname)
		if not rule or not rule.is_editable:
			continue
		doc.set(fieldname, value)


def _missing(doc) -> list[str]:
	from possibleworks.www.onboarding import missing_required

	return missing_required(doc)


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=60, seconds=60)
def portal_save(values: dict | str | None = None) -> dict:
	"""Save progress. Never validates completeness -- half-finished is expected."""
	doc = _get_own_applicant()
	_apply_values(doc, values)
	doc.save(ignore_permissions=True)

	return {"name": doc.name, "missing": _missing(doc)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=10, seconds=60)
def portal_submit(values: dict | str | None = None, declaration_accepted: bool = False) -> dict:
	"""Hand the form back to HR.

	docstatus stays 0 -- HR submits the Frappe document later, on or after the Date of
	Joining. What this does is lock the applicant out and record the declaration.
	"""
	doc = _get_own_applicant()

	if not declaration_accepted:
		frappe.throw(_("Please confirm your details before submitting."))

	_apply_values(doc, values)
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
def portal_attach(document_type: str, file_url: str) -> dict:
	"""Link a just-uploaded private file to one of this record's document rows."""
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
		["name", "is_private", "attached_to_doctype", "attached_to_name"],
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

	doc.append("documents", {"document_type": document_type, "attachment": file_url})
	doc.save(ignore_permissions=True)

	return {"name": doc.name, "missing": _missing(doc)}


@frappe.whitelist(methods=["POST"])
@rate_limit(limit=30, seconds=60)
def portal_remove_document(row_name: str) -> dict:
	doc = _get_own_applicant()

	row = next((r for r in doc.documents if r.name == row_name), None)
	if not row:
		frappe.throw(_("No such document on your record."), frappe.PermissionError)

	doc.remove(row)
	doc.save(ignore_permissions=True)

	return {"name": doc.name, "missing": _missing(doc)}


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
