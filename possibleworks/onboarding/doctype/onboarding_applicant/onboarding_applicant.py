# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Pre-employee onboarding record.

ATOMICITY HAZARD -- read before changing `on_submit`.

`Employee` is listed in `possibleworks/observer/constants.py:IMMEDIATE_SEND_DOCTYPES`,
and `hooks.py` routes `doc_events["*"]["after_insert"]` to the Observer, whose
immediate-send branch calls `frappe.db.commit()` (observer/observer.py:145).

So `employee.insert()` below COMMITS the in-flight transaction. The usual Frappe
guarantee -- "a throw in on_submit rolls back the docstatus=1 write" -- does not hold
here. Consequences, all handled below:

  * Every gate lives in `before_submit`, which runs before both the docstatus write
    and the insert.
  * Everything that can fail on a business rule -- the Job Applicant, the Job Offer,
    the User -- is created BEFORE `employee.insert()`, where a rollback still undoes it.
  * `Employee.onboarding_applicant` is set BEFORE insert, so an orphan stays findable.
  * `on_submit` opens with a chain lookup and resumes rather than creating a second
    Employee.
  * What genuinely cannot run before the Employee exists -- the role profile and the
    onboarding checklist -- runs in `complete_post_employee_setup`, which swallows its
    failures rather than rolling this record back behind a committed Employee. Both
    steps are idempotent.
  * `relink_employee` and `retry_onboarding_setup` in api.py repair the partial states,
    since a docstatus=1 record cannot be re-submitted.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields, table_fields
from frappe.model.document import Document
from frappe.utils import cint, flt, formatdate, get_link_to_form, getdate, today

from possibleworks.onboarding import boarding, pending_fields, provisioning, validators
from possibleworks.onboarding.constants import (
	APPLICANT_EDITABLE_STATUSES,
	APPLICANT_SELF_MANAGED_FIELDS,
	APPLICANT_SHOWABLE_CHILD_TABLES,
	APPLICANT_SUBMITTED,
	APPLICANT_SYSTEM_CHILD_TABLES,
	APPLICANT_WRITABLE_CHILD_TABLES,
	APPLICANT_WRITABLE_FIELDS,
	AWAITING_APPLICANT,
	CANCELLED,
	DOCUMENT_TEMPLATE_DOCTYPE,
	DOCUMENT_TYPE_DOCTYPE,
	HR_ROLES,
	INTEGRATION_ROLE,
	ONBOARDED,
	PORTAL_ROLE,
	READY_TO_ONBOARD,
	STATUS_TRANSITIONS,
)
from possibleworks.onboarding.doctype.onboarding_document_template.onboarding_document_template import (
	get_matching_template,
)
from possibleworks.onboarding.employee_fields import (
	EMPLOYEE_DOCTYPE,
	build_employee,
	coerce_value,
	uses_employee_number_naming,
)

MAX_AMENDMENT_CHAIN = 50


class OnboardingApplicant(Document):
	# ------------------------------------------------------------------ #
	# Lifecycle
	# ------------------------------------------------------------------ #

	def validate(self):
		self.reset_state_on_amend()
		self.set_applicant_name()
		self.normalise_identifiers()
		self.apply_same_as_current_address()
		self.validate_identifiers()
		self.validate_dates()
		self.validate_work_history()
		self.apply_document_template()
		self.validate_documents()
		self.validate_status_transition()
		self.refresh_pending_field_rows()
		self.enforce_applicant_field_allowlist()

	def before_submit(self):
		self.validate_joining_date_reached()
		self.validate_ready_for_onboarding()
		self.validate_no_employee_already_created()
		self.validate_bank_details()
		self.validate_document_template_selected()
		self.validate_required_documents()
		self.validate_required_applicant_fields()
		# Gates for the records built at submit: the login, the Job Offer, and the
		# checklist's ability to schedule a task. All three are submit-time rather than
		# `reqd` on the doctype -- HR seeds a draft before a work email exists.
		provisioning.validate_work_email_available(self)
		boarding.validate_designation_present(self)
		boarding.validate_holiday_list_available(self)
		self.validate_employee_number_for_naming()
		self.validate_employee_mandatory_fields()

	def on_submit(self):
		# Idempotent resume. The Observer commits inside Employee.after_insert, so a
		# crash between insert and db_set can leave an orphan; recover it instead of
		# minting a duplicate.
		existing = self.get_employee_from_chain()
		if existing:
			self.db_set(
				{"employee": existing, "status": ONBOARDED}, update_modified=False
			)
			self.complete_post_employee_setup(existing)
			return

		# --- Everything below, up to and including the insert, is one transaction. ---
		# Ordered so that whatever can fail on a business rule fails while a rollback
		# still means something. The Job Offer's duplicate guard, the work-email
		# collision and the User's own validation all live in here.
		job_applicant = boarding.create_job_applicant(self)
		boarding.create_job_offer(self, job_applicant)
		user = provisioning.create_employee_user(self)

		employee = self.build_employee_record()
		# Both set before insert. `user_id` is what makes `Employee.on_update` append
		# the Employee role and create the User Permissions; `job_applicant` is what
		# ties the Employee into the boarding chain.
		employee.user_id = user
		if frappe.get_meta(EMPLOYEE_DOCTYPE).has_field("job_applicant"):
			employee.job_applicant = job_applicant

		employee.insert(ignore_permissions=True)
		# ==================== the Observer commits here ====================

		self.db_set(
			# Killing the invite here is deliberate: once onboarded there is nothing
			# left for the applicant to fill in, so the link should stop working.
			{"employee": employee.name, "status": ONBOARDED, "invite_expires_on": None},
			update_modified=False,
		)

		self.complete_post_employee_setup(employee.name)

		frappe.msgprint(
			_("Employee {0} created.").format(get_link_to_form(EMPLOYEE_DOCTYPE, employee.name)),
			alert=True,
			indicator="green",
		)

	def complete_post_employee_setup(self, employee: str) -> None:
		"""The steps that necessarily run after the Observer's commit.

		Non-fatal, deliberately. The Employee is committed by the time we get here, so
		the person IS onboarded; throwing would roll this record back to draft while
		the Employee stands, leaving the record asserting something false. A missing
		role profile or checklist is recoverable -- an applicant that disagrees with
		its own Employee is not.

		Both steps are idempotent, so `retry_onboarding_setup` (api.py) converges.
		"""
		self._run_post_step(provisioning.assign_standard_role_profile, employee, _("role profile"))
		self._run_post_step(boarding.ensure_employee_onboarding, employee, _("onboarding checklist"))

	def _run_post_step(self, step, employee: str, label: str) -> None:
		try:
			step(self, employee)
		except Exception:
			frappe.log_error(
				f"Onboarding post-setup failed ({label}): {self.name}",
				frappe.get_traceback(),
			)
			frappe.msgprint(
				_(
					"Employee was created, but the {0} could not be set up. Use Retry Onboarding Setup on this record once the cause is fixed."
				).format(label),
				title=_("Follow-up Needed"),
				indicator="orange",
			)

	def on_cancel(self):
		# check_if_doc_is_linked only raises for linked docs that are themselves
		# submitted, and Employee is not submittable -- so cancel is never blocked by
		# the reverse link. Declared for intent.
		self.ignore_linked_doctypes = (EMPLOYEE_DOCTYPE,)
		self.db_set("status", CANCELLED, update_modified=False)

		if self.employee and frappe.db.exists(EMPLOYEE_DOCTYPE, self.employee):
			frappe.msgprint(
				_(
					"Employee {0} was NOT deleted or deactivated. Handle it manually if that was intended."
				).format(get_link_to_form(EMPLOYEE_DOCTYPE, self.employee)),
				title=_("Employee Retained"),
				indicator="orange",
			)

	# ------------------------------------------------------------------ #
	# validate() helpers
	# ------------------------------------------------------------------ #

	def reset_state_on_amend(self) -> None:
		"""`no_copy` does NOT survive an amend.

		`frappe.copy_doc` defaults to `ignore_no_copy=True` (document.py:2095) and the
		Desk amend path sets `is_no_copy = !from_amend` (create_new.js:288), so an
		amended draft arrives carrying `employee` and `status` from the cancelled
		original. Reset them explicitly rather than trusting the flag.
		"""
		if not (self.is_new() and self.amended_from):
			return

		self.employee = None
		self.status = AWAITING_APPLICANT
		self.applicant_submitted_on = None
		self.declaration_accepted_on = None
		self.applicant_declaration = 0

	def set_applicant_name(self) -> None:
		parts = [self.first_name, self.middle_name, self.last_name]
		full_name = " ".join(part.strip() for part in parts if part and part.strip())
		# The record exists before the applicant has typed anything, but title_field
		# still needs something to render.
		self.applicant_name = full_name or self.personal_email or _("New Applicant")

	def normalise_identifiers(self) -> None:
		if self.aadhar_number:
			self.aadhar_number = validators.normalise_digits(self.aadhar_number)
		if self.pan_number:
			self.pan_number = validators.normalise_pan(self.pan_number)
		for fieldname in ("ifsc_code", "micr_code"):
			if self.get(fieldname):
				self.set(fieldname, validators.normalise_code(self.get(fieldname)))

		# Every Phone field, from live meta rather than a hardcoded pair, so a site that
		# adds one as a Custom Field gets the same treatment. Done here rather than in the
		# portal because the Desk and the integration API write these too, and only one of
		# the three formats survives a round trip through the Desk control.
		for df in self.meta.get_phone_fields():
			value = self.get(df.fieldname)
			if value:
				self.set(df.fieldname, validators.normalise_phone(value))

	def apply_same_as_current_address(self) -> None:
		if self.same_as_current_address:
			self.permanent_address = self.current_address
			self.permanent_accommodation_type = self.current_accommodation_type

	def validate_identifiers(self) -> None:
		# PAN is deliberately not format-checked; see validators.normalise_pan.
		if self.aadhar_number:
			validators.validate_aadhaar(self.aadhar_number, throw=True)
		if self.ifsc_code:
			validators.validate_ifsc(self.ifsc_code, throw=True)

	def validate_dates(self) -> None:
		if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
			frappe.throw(_("Date of Birth cannot be in the future."))

		if self.date_of_birth and self.date_of_joining:
			if getdate(self.date_of_birth) >= getdate(self.date_of_joining):
				frappe.throw(_("Date of Joining must be after Date of Birth."))

	def validate_work_history(self) -> None:
		total = 0.0
		for row in self.external_work_history:
			if row.from_date and getdate(row.from_date) > getdate(today()):
				frappe.throw(_("Row #{0}: From Date cannot be in the future.").format(row.idx))

			if row.is_current_employer and row.to_date:
				# This used to silently do `row.to_date = None`, which threw away a date
				# the applicant had typed AND skipped the ordering check below -- so an
				# inverted pair sailed through as long as the box was ticked. Say so
				# instead: the two answers contradict each other and only they know which
				# one is true.
				frappe.throw(
					_("Row #{0}: {1} is marked as your current employer, so it cannot have a To Date. Clear one or the other.").format(
						row.idx, frappe.bold(row.company_name or _("this entry"))
					),
					title=_("Still Working There?"),
				)

			if not row.is_current_employer and row.from_date and row.to_date:
				if getdate(row.to_date) < getdate(row.from_date):
					frappe.throw(
						_("Row #{0}: To Date cannot be before From Date.").format(row.idx)
					)

			row.set_total_experience()
			if row.from_date:
				end = today() if row.is_current_employer else row.to_date
				if end:
					total += (getdate(end) - getdate(row.from_date)).days / 365.25

		self.total_experience_years = flt(total, 2)

	def validate_bank_details(self) -> None:
		"""`depends_on` is evaluated client-side only -- there is no server handler in
		v16 -- so an API caller can set salary_mode='Bank' with no account details.

		A completeness rule, so it belongs at the submit gate, not in validate():
		running it on every save would block HR from parking a half-filled record.
		`salary_mode` starts empty and stays empty until somebody chooses, so an
		untouched record never trips this.
		"""
		if self.salary_mode != "Bank":
			return

		missing = [
			self.meta.get_label(fieldname)
			for fieldname in ("bank_name", "bank_ac_no", "ifsc_code")
			if not self.get(fieldname)
		]
		if missing:
			frappe.throw(
				_("Salary Mode is Bank, so these are required: {0}").format(
					", ".join(frappe.bold(label) for label in missing)
				)
			)

	# ------------------------------------------------------------------ #
	# Document template
	# ------------------------------------------------------------------ #

	def apply_document_template(self) -> None:
		"""Suggest a template, and snapshot it onto this record.

		The snapshot is the whole point: `validate_documents` and
		`validate_required_documents` read `required_documents`, never the template, so
		editing a template can never change the requirements of a record already in
		flight. HR pulls changes in deliberately via `resync_document_template`.
		"""
		if self.docstatus != 0:
			return

		if not self.document_template:
			# Only ever fills a blank -- a manual choice is never overwritten.
			suggested = get_matching_template(self)
			if not suggested:
				return
			self.document_template = suggested

		previous = self.get_doc_before_save()
		template_changed = bool(previous) and previous.document_template != self.document_template

		if template_changed or not self.required_documents:
			self.sync_required_documents()

	def sync_required_documents(self) -> None:
		"""Replace both snapshots with the template's current rows.

		Documents and applicant fields are copied together -- they are two halves of one
		form definition, and letting them drift apart would mean an applicant asked for
		documents under one policy and fields under another.
		"""
		self.set("required_documents", [])
		self.set("applicant_fields", [])
		if not self.document_template:
			return

		template = frappe.get_cached_doc(DOCUMENT_TEMPLATE_DOCTYPE, self.document_template)

		for row in template.documents:
			self.append(
				"required_documents",
				{
					"document_type": row.document_type,
					"is_required": row.is_required,
					"allow_multiple": row.allow_multiple,
					"enabled": row.enabled,
					"allowed_extensions": row.allowed_extensions,
					"instructions": row.instructions,
				},
			)

		for row in template.applicant_fields:
			self.append(
				"applicant_fields",
				{
					"fieldname": row.fieldname,
					"label": row.label,
					"is_required": row.is_required,
					"is_editable": row.is_editable,
					"lock_when_filled": row.lock_when_filled,
					"help_text": row.help_text,
				},
			)

	def field_is_editable(self, row, source=None) -> bool:
		"""Whether the applicant may change `row` right now.

		`is_editable` alone is static template policy and cannot know whether HR
		prefilled THIS record. `lock_when_filled` resolves that per record: a value HR
		already supplied is protected, a blank one can still be filled in.

		`source` is the document the current value is read from, and callers that are
		vetting a save MUST pass the pre-save doc. Reading `self` there would see the
		value the applicant just typed and lock the field against the very save that
		supplied it -- their first Aadhaar entry would be rejected as an edit of
		something they had never been allowed to set.
		"""
		if not row.is_editable:
			return False
		if row.lock_when_filled and (source or self).get(row.fieldname):
			return False
		return True

	def get_applicant_field_rules(self) -> dict:
		"""Effective per-field rules for the portal, keyed by fieldname.

		Read from the snapshot, never the template, so a template edit cannot change
		what an applicant already has open.
		"""
		return {
			row.fieldname: frappe._dict(
				fieldname=row.fieldname,
				label=row.label,
				is_required=row.is_required,
				is_editable=self.field_is_editable(row),
				declared_editable=bool(row.is_editable),
				lock_when_filled=bool(row.lock_when_filled),
				help_text=row.help_text,
			)
			for row in self.applicant_fields
			if row.fieldname
		}

	def get_document_rules(self) -> dict:
		"""Effective rules per document type, from the snapshot.

		`allowed_extensions` falls back to the Document Type when the template row does
		not override it, so the vocabulary keeps a sensible default.
		"""
		rules = {}
		for row in self.required_documents:
			if not row.enabled:
				continue
			extensions = row.allowed_extensions or frappe.db.get_value(
				DOCUMENT_TYPE_DOCTYPE, row.document_type, "allowed_extensions"
			)
			rules[row.document_type] = frappe._dict(
				document_type=row.document_type,
				is_required=row.is_required,
				allow_multiple=row.allow_multiple,
				allowed_extensions=extensions or "",
				instructions=row.instructions,
			)
		return rules

	def validate_documents(self) -> None:
		"""Row-level document rules, enforced on every save so bad data never lands.
		Completeness (`is_required`) is checked separately, at submit."""
		if not self.documents:
			return

		configured = self.get_document_rules()
		rows_by_type: dict[str, list[int]] = {}

		for row in self.documents:
			if not row.attachment:
				frappe.throw(_("Row #{0}: Attachment is required.").format(row.idx))

			# Always enforced, regardless of template: these carry Aadhaar, PAN and bank
			# details.
			self.validate_attachment_is_private(row)

			# A document outside the template is allowed. The template defines what is
			# REQUIRED, not an exhaustive whitelist of what may be attached -- and
			# erroring here would strand any record whose document type was later
			# disabled, with no way to save it again.
			config = configured.get(row.document_type)
			if not config:
				continue

			self.validate_attachment_extension(row, config)
			rows_by_type.setdefault(row.document_type, []).append(row.idx)

		for document_type, indexes in rows_by_type.items():
			if len(indexes) > 1 and not configured[document_type].allow_multiple:
				frappe.throw(
					_("Only one {0} document is allowed, but rows {1} all use it.").format(
						frappe.bold(document_type), ", ".join(str(i) for i in indexes)
					)
				)

	def validate_attachment_is_private(self, row) -> None:
		"""Mirrors Form16.validate_documents_are_private -- these carry Aadhaar, PAN
		and bank details."""
		is_private = frappe.db.get_value("File", {"file_url": row.attachment}, "is_private")
		if is_private is not None and not is_private:
			frappe.throw(
				_(
					"Row #{0}: document must be uploaded as a private file since it contains sensitive personal information."
				).format(row.idx)
			)

	def validate_attachment_extension(self, row, config) -> None:
		allowed = [
			part.strip().lstrip(".").lower()
			for part in (config.allowed_extensions or "").split(",")
			if part.strip()
		]
		if not allowed:
			return

		extension = row.attachment.rsplit(".", 1)[-1].lower() if "." in row.attachment else ""
		if extension not in allowed:
			frappe.throw(
				_("Row #{0}: {1} accepts only {2} files.").format(
					row.idx, frappe.bold(row.document_type), ", ".join(allowed)
				)
			)

	def validate_status_transition(self) -> None:
		if self.is_new() or not self.has_value_changed("status"):
			return

		before = self.get_doc_before_save()
		if not before or not before.status:
			return

		allowed = STATUS_TRANSITIONS.get(before.status, set())
		if self.status not in allowed:
			frappe.throw(
				_("Cannot change status from {0} to {1}.").format(
					frappe.bold(_(before.status)), frappe.bold(_(self.status))
				),
				title=_("Invalid Status Change"),
			)

	def refresh_pending_field_rows(self) -> None:
		"""Re-stamp label/fieldtype/options from live meta, coerce values to machine
		format, and flag rows whose Employee field is no longer mandatory.

		Stale rows are KEPT and still applied: a human deliberately entered the value,
		and "no longer mandatory" does not mean "no longer wanted". Purging would be
		destructive and unauditable.
		"""
		if self.docstatus != 0:
			return

		meta = frappe.get_meta(EMPLOYEE_DOCTYPE)
		mandatory = {df.fieldname for df in meta.get("fields", {"reqd": 1})}

		# Drop blank rows. The table is machine-managed (the panel writes it on save),
		# so an empty row is never meaningful -- and leaving one would fail the child
		# table's own `fieldname` mandatory check with a confusing error.
		kept = [row for row in self.pending_employee_fields if (row.fieldname or "").strip()]
		if len(kept) != len(self.pending_employee_fields):
			self.set("pending_employee_fields", kept)
			for idx, row in enumerate(self.pending_employee_fields, start=1):
				row.idx = idx

		for row in self.pending_employee_fields:
			df = meta.get_field(row.fieldname)
			if not df:
				# Field removed from this site entirely.
				row.is_stale = 1
				continue

			row.label = df.label
			row.fieldtype = df.fieldtype
			row.options = df.options
			row.is_stale = 0 if row.fieldname in mandatory else 1

			# Canonicalise on write so the Desk path and the API path store the same
			# thing, and build_employee's second coercion is idempotent.
			coerced = coerce_value(row.value, df.fieldtype)
			if coerced is not None and df.fieldtype in ("Date", "Datetime", "Currency", "Float"):
				row.value = str(coerced)

		signature = pending_fields.signature()
		if self.pending_fields_signature and self.pending_fields_signature != signature:
			self.pending_fields_stale = 1
		else:
			self.pending_fields_stale = 0
		self.pending_fields_signature = signature

	def enforce_applicant_field_allowlist(self) -> None:
		"""Explicit allowlist for the integration role.

		Lives here rather than only in api.py because `read_only: 1` has NO server-side
		enforcement in `_save`, and /api/resource PUT, /api/v2/document PATCH and
		run_doc_method all do a blanket `doc.update(request_body)`. validate() is the
		one choke point every write path goes through.
		"""
		if frappe.flags.in_install or frappe.flags.in_patch or frappe.flags.in_migrate:
			return
		if frappe.session.user == "Administrator":
			return

		roles = set(frappe.get_roles())
		if roles & set(HR_ROLES):
			return

		# The portal applicant is held to a STRICTER rule than the integration user:
		# only the fields their own template snapshot marked editable. They now hold
		# DocPerm write (upload_file requires it), so /api/resource PUT is reachable --
		# this is what stops it being useful.
		if PORTAL_ROLE in roles:
			self.enforce_portal_field_allowlist()
			return

		if INTEGRATION_ROLE not in roles:
			return

		if self.is_new():
			frappe.throw(
				_("Onboarding records can only be created by HR."), frappe.PermissionError
			)

		before = self.get_doc_before_save()
		if not before:
			frappe.throw(
				_("Cannot verify the prior state of this record."), frappe.PermissionError
			)

		if self.status != before.status:
			permitted = (
				self.flags.applicant_status_transition
				and before.status == AWAITING_APPLICANT
				and self.status == APPLICANT_SUBMITTED
			)
			if not permitted:
				frappe.throw(
					_("The onboarding app cannot change the status of this record."),
					frappe.PermissionError,
				)

		if before.status not in APPLICANT_EDITABLE_STATUSES:
			frappe.throw(
				_("This onboarding record is no longer open for applicant edits (status: {0}).").format(
					_(before.status)
				),
				frappe.PermissionError,
			)

		writable = APPLICANT_WRITABLE_FIELDS | APPLICANT_SELF_MANAGED_FIELDS
		if self.status != before.status:
			# Same reasoning as the portal path: the transition was approved above, so
			# the generic diff must not reject it a second time.
			writable = writable | {"status"}
		changed = []

		for df in self.meta.fields:
			if df.fieldtype in no_value_fields and df.fieldtype not in table_fields:
				continue

			if df.fieldtype in table_fields:
				if df.fieldname in APPLICANT_SYSTEM_CHILD_TABLES:
					continue
				if df.fieldname not in APPLICANT_WRITABLE_CHILD_TABLES:
					if _table_signature(self, df.fieldname) != _table_signature(
						before, df.fieldname
					):
						changed.append(df.fieldname)
				continue

			if df.fieldname in writable:
				continue

			if (self.get(df.fieldname) or None) != (before.get(df.fieldname) or None):
				changed.append(df.fieldname)

		# default_fields are absent from meta.fields but ARE settable via doc.update().
		for fieldname in ("owner", "creation", "docstatus", "parent", "parenttype", "idx"):
			if self.get(fieldname) != before.get(fieldname):
				changed.append(fieldname)

		if changed:
			frappe.throw(
				_("The onboarding app is not allowed to change: {0}").format(
					", ".join(
						frappe.bold(self.meta.get_label(name) or name)
						for name in sorted(set(changed))
					)
				),
				frappe.PermissionError,
				title=_("Field Not Editable"),
			)

	def enforce_portal_field_allowlist(self) -> None:
		"""An applicant may change only what their own snapshot marked editable."""
		if self.is_new():
			frappe.throw(_("Onboarding records are created by HR."), frappe.PermissionError)

		before = self.get_doc_before_save()
		if not before:
			frappe.throw(_("Cannot verify the prior state of this record."), frappe.PermissionError)

		if before.applicant_user != frappe.session.user:
			frappe.throw(_("This is not your onboarding record."), frappe.PermissionError)

		if before.docstatus != 0 or before.status not in APPLICANT_EDITABLE_STATUSES:
			frappe.throw(
				_("This onboarding form is no longer open for changes."), frappe.PermissionError
			)

		if self.status != before.status:
			permitted = (
				self.flags.applicant_status_transition
				and before.status == AWAITING_APPLICANT
				and self.status == APPLICANT_SUBMITTED
			)
			if not permitted:
				frappe.throw(_("You cannot change the status of this record."), frappe.PermissionError)

		# Read the snapshot off the PRE-SAVE doc. `applicant_fields` is exempt from the
		# diff below (the controller rewrites it), so judging the write against the
		# snapshot carried in on the request would let a caller ship their own
		# permissions alongside the data they wanted them for.
		#
		# Editability is resolved against `before` too -- see field_is_editable.
		editable = {
			row.fieldname
			for row in before.applicant_fields
			if row.fieldname and self.field_is_editable(row, source=before)
		}
		# A snapshot can only ever open a field the module already considers the
		# applicant's to write. Belt and braces against a hand-edited row.
		editable &= APPLICANT_WRITABLE_FIELDS | APPLICANT_SHOWABLE_CHILD_TABLES

		writable = editable | APPLICANT_SELF_MANAGED_FIELDS
		if self.status != before.status:
			# Already vetted above; without this the generic diff below would flag the
			# very transition it just approved.
			writable = writable | {"status"}
		changed = []

		for df in self.meta.fields:
			if df.fieldtype in table_fields:
				# `documents` is theirs to add to; the repeating tables are theirs only
				# where the snapshot said so; everything else is HR's or derived.
				allowed_tables = {"documents"} | APPLICANT_SYSTEM_CHILD_TABLES | (
					editable & APPLICANT_SHOWABLE_CHILD_TABLES
				)
				if df.fieldname not in allowed_tables:
					if _table_signature(self, df.fieldname) != _table_signature(before, df.fieldname):
						changed.append(df.fieldname)
				continue
			if df.fieldtype in no_value_fields or df.fieldname in writable:
				continue
			if (self.get(df.fieldname) or None) != (before.get(df.fieldname) or None):
				changed.append(df.fieldname)

		if changed:
			frappe.throw(
				_("You are not allowed to change: {0}").format(
					", ".join(
						frappe.bold(self.meta.get_label(name) or name)
						for name in sorted(set(changed))
					)
				),
				frappe.PermissionError,
				title=_("Field Not Editable"),
			)

	# ------------------------------------------------------------------ #
	# before_submit() gates
	# ------------------------------------------------------------------ #

	def validate_joining_date_reached(self) -> None:
		if getdate(today()) < getdate(self.date_of_joining):
			frappe.throw(
				_("This onboarding cannot be submitted before the Date of Joining ({0}). Today is {1}.").format(
					frappe.bold(formatdate(self.date_of_joining)),
					frappe.bold(formatdate(today())),
				),
				title=_("Too Early to Onboard"),
			)

	def validate_ready_for_onboarding(self) -> None:
		if self.status != READY_TO_ONBOARD:
			frappe.throw(
				_("Status must be {0} before submitting. It is currently {1}.").format(
					frappe.bold(_(READY_TO_ONBOARD)), frappe.bold(_(self.status))
				)
			)

	def validate_no_employee_already_created(self) -> None:
		existing = self.get_employee_from_chain()
		if existing:
			frappe.throw(
				_(
					"Employee {0} has already been created from this onboarding record. Edit that Employee directly, or delete it before re-submitting."
				).format(get_link_to_form(EMPLOYEE_DOCTYPE, existing)),
				title=_("Employee Already Created"),
			)

	def validate_document_template_selected(self) -> None:
		"""A template is required to submit, but not to create a draft -- HR seeds a
		shell record before knowing enough about the hire to choose one."""
		if not self.document_template:
			frappe.throw(
				_("Select a Document Template before submitting, so the required documents are defined."),
				title=_("Document Template Required"),
			)

	def validate_required_documents(self) -> None:
		"""Completeness, against this record's snapshot rather than any global list."""
		present = {row.document_type for row in self.documents if row.attachment}
		missing = [
			row.document_type
			for row in self.required_documents
			if row.enabled and row.is_required and row.document_type not in present
		]
		if missing:
			frappe.throw(
				_("The following required documents have not been uploaded:")
				+ "<ul><li>"
				+ "</li><li>".join(frappe.utils.escape_html(name) for name in missing)
				+ "</li></ul>",
				title=_("Documents Missing"),
			)

	def validate_required_applicant_fields(self) -> None:
		"""Every field the template marked Required must actually hold a value.

		This is not a second copy of the applicant's own completeness check -- it covers
		the path that check never runs on. `portal_submit` only fires when the applicant
		hands the form back; HR can set the status straight to Ready to Onboard and
		submit without them ever opening it. A template Required flag would then mean
		nothing, and the omission would surface later as an unexplained missing Employee
		field, or not at all.

		A Required field is always at least nominally editable -- the template refuses
		Required-without-Editable -- and `lock_when_filled` only closes a field that
		already HAS a value, so nothing reported here is ever unfixable: HR fills it in,
		or sends the record back to the applicant.
		"""
		missing = [
			row.label or row.fieldname
			for row in self.applicant_fields
			if row.is_required and row.fieldname and not self.get(row.fieldname)
		]
		if not missing:
			return

		frappe.throw(
			_("The onboarding template asks for these, and they are still empty:")
			+ "<ul><li>"
			+ "</li><li>".join(frappe.utils.escape_html(name) for name in missing)
			+ "</li></ul>",
			title=_("Details Missing"),
		)

	def validate_employee_number_for_naming(self) -> None:
		"""HR supplies the Employee ID, but it is only used when HR Settings names
		Employees by Employee Number.

		Pre-checked here because `set_new_name` runs at document.py:442 -- BEFORE
		`_validate()` at :448 -- so a blank or duplicate value otherwise fails inside
		naming with an opaque error that never mentions employee_number.
		"""
		if not uses_employee_number_naming():
			return

		if not self.employee_number:
			frappe.throw(
				_(
					"HR Settings names Employees by Employee Number, so Employee Number is required before submitting."
				),
				title=_("Employee Number Required"),
			)

		if frappe.db.exists(EMPLOYEE_DOCTYPE, self.employee_number):
			frappe.throw(
				_("Employee {0} already exists.").format(frappe.bold(self.employee_number)),
				title=_("Duplicate Employee Number"),
			)

	def validate_employee_mandatory_fields(self) -> None:
		"""Block the submit listing EVERY missing mandatory Employee field at once.

		The mandatory set is read from live meta on this site, so Custom Fields and
		Property Setters are picked up without any hardcoding.
		"""
		pending = pending_fields.get_pending_employee_fields(self)
		if not pending:
			return

		frappe.throw(
			_("The Employee record cannot be created yet. These mandatory fields are still empty:")
			+ pending_fields.format_missing_message(pending),
			title=_("Mandatory Information Missing"),
		)

	# ------------------------------------------------------------------ #
	# Employee creation
	# ------------------------------------------------------------------ #

	def build_employee_record(self):
		employee = build_employee(self)

		# Written BEFORE insert so it survives the Observer's mid-transaction commit
		# and an orphaned Employee stays discoverable. See the module docstring.
		if frappe.get_meta(EMPLOYEE_DOCTYPE).has_field("onboarding_applicant"):
			employee.onboarding_applicant = self.name

		return employee

	def get_amendment_chain(self) -> list[str]:
		chain = [self.name]
		current = self.amended_from
		guard = 0
		while current and guard < MAX_AMENDMENT_CHAIN:
			chain.append(current)
			current = frappe.db.get_value("Onboarding Applicant", current, "amended_from")
			guard += 1
		return [name for name in chain if name]

	def get_employee_from_chain(self) -> str | None:
		"""Find an Employee created from this record or any earlier version of it.

		Uses the reverse link rather than `self.employee`, because that field can be
		NULL even when an Employee exists -- see the atomicity hazard in the module
		docstring.
		"""
		if not frappe.get_meta(EMPLOYEE_DOCTYPE).has_field("onboarding_applicant"):
			return self.employee or None

		chain = self.get_amendment_chain()
		if not chain:
			return self.employee or None

		return frappe.db.get_value(
			EMPLOYEE_DOCTYPE, {"onboarding_applicant": ("in", chain)}, "name"
		)

	# ------------------------------------------------------------------ #
	# Helpers
	# ------------------------------------------------------------------ #

	@frappe.whitelist()
	def invite_applicant_now(self) -> dict:
		"""Issue the applicant's portal invite (Desk button)."""
		from possibleworks.onboarding.portal import invite_applicant

		return invite_applicant(self.name)

	@frappe.whitelist()
	def resync_document_template(self) -> dict:
		"""Deliberately pull the template's current rows into this record.

		Separate from `validate` on purpose: the snapshot exists so template edits do
		NOT leak into records already in flight, so re-syncing has to be something HR
		asks for rather than something that happens quietly on save.
		"""
		self.check_permission("write")

		if self.docstatus != 0:
			frappe.throw(
				_("Only a draft can be re-synced from its template."), frappe.PermissionError
			)
		if not self.document_template:
			frappe.throw(_("Select a Document Template first."))

		before = {row.document_type for row in self.required_documents if row.enabled}
		self.sync_required_documents()
		after = {row.document_type for row in self.required_documents if row.enabled}
		self.save()

		return {
			"name": self.name,
			"added": sorted(after - before),
			"removed": sorted(before - after),
		}


def _table_signature(doc, fieldname: str) -> list[tuple]:
	"""Order-insensitive fingerprint of a child table, for change detection."""
	rows = doc.get(fieldname) or []
	return sorted(
		tuple(sorted((k, str(v)) for k, v in row.as_dict(no_default_fields=True).items()))
		for row in rows
	)


# --------------------------------------------------------------------------- #
# Permission hooks (wired in hooks.py)
# --------------------------------------------------------------------------- #


def get_permission_query_conditions(user=None, doctype=None):
	"""Scope the integration role to open drafts.

	DocPerm `read` is doctype-wide, so without this a leaked API key could enumerate
	every applicant's Aadhaar, PAN and bank details. These conditions are applied by
	`frappe.get_list` and the v2 list engine alike.
	"""
	from possibleworks.onboarding.constants import APPLICANT_VISIBLE_STATUSES

	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	roles = set(frappe.get_roles(user))
	if roles & set(HR_ROLES):
		return ""

	if PORTAL_ROLE in roles:
		# An applicant sees exactly one record: their own.
		return f"(`tabOnboarding Applicant`.applicant_user = {frappe.db.escape(user)})"

	if INTEGRATION_ROLE not in roles:
		return ""

	statuses = ", ".join(frappe.db.escape(status) for status in sorted(APPLICANT_VISIBLE_STATUSES))
	return (
		"(`tabOnboarding Applicant`.docstatus = 0 "
		f"and `tabOnboarding Applicant`.status in ({statuses}))"
	)


def has_permission(doc, ptype=None, user=None, debug=False):
	"""Same rule as above for single-document access (get_doc, read_doc, upload_file)."""
	from possibleworks.onboarding.constants import APPLICANT_VISIBLE_STATUSES

	user = user or frappe.session.user
	if user == "Administrator":
		return True

	roles = set(frappe.get_roles(user))
	if roles & set(HR_ROLES):
		return True

	if PORTAL_ROLE in roles:
		return doc.applicant_user == user

	if INTEGRATION_ROLE not in roles:
		return True

	return cint(doc.docstatus) == 0 and doc.status in APPLICANT_VISIBLE_STATUSES
