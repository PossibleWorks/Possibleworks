# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Runtime discovery of the Employee fields an onboarding record still has to supply.

Different sites configure different mandatory fields on `Employee`: site Custom
Fields with `reqd: 1`, and Property Setters that `HR Settings.emp_created_by` flips
on `naming_series` / `employee_number`. So the mandatory set is read from live meta
on every call and never hardcoded.

Resolution is a DRY RUN, not a fieldname diff: we build the Employee we would
actually create and ask it what is missing, using the framework's own
`_get_missing_mandatory_fields()`. That inherits three behaviours a static diff gets
wrong:

  * Defaults. `status` defaults to "Active"; `naming_series` resolves to its first
    Select option (frappe/model/create_new.py:117), so a Property-Setter-mandated
    `naming_series` is ALREADY satisfied -- a diff would wrongly demand it.
  * User defaults and user-permission defaults, applied by the same code path
    `insert()` will use.
  * `has_content()` semantics -- a `reqd` Check is never missing; a Text Editor
    holding "<p><br></p>" is.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields, table_fields

from possibleworks.onboarding.employee_fields import (
	EMPLOYEE_DOCTYPE,
	EMPLOYEE_FIELD_MAP,
	build_employee,
	uses_employee_number_naming,
)

# Fieldtypes we can render as a standalone control. `frappe.ui.form.make_control`
# returns undefined for anything without a matching ControlXxx class, and
# Layout.make_field silently skips it -- which would make a blocking field vanish
# from the panel while still blocking the submit. So anything not listed here is
# routed to the `manual` bucket instead of being rendered.
RENDERABLE_FIELDTYPES = frozenset({
	"Data",
	"Select",
	"Link",
	"Dynamic Link",
	"Date",
	"Datetime",
	"Time",
	"Int",
	"Float",
	"Currency",
	"Percent",
	"Check",
	"Small Text",
	"Text",
	"Long Text",
	"Text Editor",
	"Phone",
	"Attach",
	"Attach Image",
	"Duration",
	"Rating",
	"Color",
	"Password",
})

# Docfield properties forwarded to the client. Everything else is dropped -- see
# _serialise_docfield for why that matters.
_KEEP_PROPERTIES = (
	"fieldname",
	"label",
	"fieldtype",
	"options",
	"precision",
	"length",
	"non_negative",
	"link_filters",
	"description",
	"default",
	"placeholder",
	"ignore_user_permissions",
	"only_select",
	"sort_options",
)


def _serialise_docfield(df) -> dict:
	"""Return a client-safe copy of an Employee docfield.

	Three properties are deliberately stripped:

	  depends_on / mandatory_depends_on / read_only_depends_on
	      Evaluated against the Employee preview, not the onboarding form. A falsy
	      expression makes Layout render the control hidden while it still blocks the
	      submit -- an invisible deadlock. The framework strips these for the same
	      reason in bulk_operations.js:413.
	  fetch_from
	      Makes ControlInput.read_only_because_of_fetch_from() grey out the input.

	and hidden/read_only are forced to 0 because BaseControl.get_status returns
	"None"/"Read" for them, which would render an unfillable control for a field that
	still blocks the submit.
	"""
	out = {key: df.get(key) for key in _KEEP_PROPERTIES if df.get(key) not in (None, "")}
	out.update(
		parent=EMPLOYEE_DOCTYPE,
		reqd=1,
		hidden=0,
		read_only=0,
		is_virtual=0,
		permlevel=0,
	)
	# Pre-split so a non-Frappe client does not need to know the newline convention
	# ControlSelect.set_options relies on.
	if df.fieldtype == "Select" and df.get("options"):
		out["options_list"] = [o for o in str(df.options).split("\n")]
	return out


def _is_mandatory_conditionally(df, preview) -> bool:
	"""Evaluate `mandatory_depends_on`, which the server never does itself.

	It is handled purely client-side (frappe/public/js/frappe/form/save.js:200 and
	layout.js:734); `_validate_mandatory` filters on the static `reqd` flag alone. So
	an API caller gets zero enforcement, and we collect these fields -- but we never
	BLOCK on them, because blocking would make us stricter than `Employee.insert()`
	itself and would create records that can never be submitted.
	"""
	expression = df.get("mandatory_depends_on")
	if not expression:
		return False

	if not isinstance(expression, str):
		return bool(expression)

	if not expression.startswith("eval:"):
		return bool(preview.get(expression))

	try:
		return bool(frappe.safe_eval(expression[5:], None, {"doc": preview.as_dict()}))
	except Exception:
		# A broken expression must not block onboarding.
		return False


def _native_source(applicant, employee_fieldname: str) -> str | None:
	"""Return the Onboarding Applicant fieldname that feeds this Employee field.

	A mandatory Employee field that already has a counterpart on the onboarding form
	must NOT be rendered in the pending panel -- HR would see it twice and could set
	two different values. It still blocks the submit; the message just points at the
	real field instead.
	"""
	if employee_fieldname == "employee_number":
		return "employee_number" if uses_employee_number_naming() else None

	for source, target in EMPLOYEE_FIELD_MAP.items():
		if target == employee_fieldname:
			return source if applicant.meta.has_field(source) else None
	return None


def _tab_label(applicant, fieldname: str) -> str:
	"""Label of the tab a field sits on, so the panel can say where to go.

	Walks backwards through field_order to the nearest preceding Tab Break.
	"""
	tab = ""
	for df in applicant.meta.fields:
		if df.fieldtype == "Tab Break":
			tab = df.label or ""
		if df.fieldname == fieldname:
			return tab
	return ""


def resolve(applicant, preview=None) -> dict:
	"""Bucket the Employee fields this applicant record cannot yet satisfy.

	  native      mandatory, missing, but collected by an existing field on this
	              form -- blocks the submit, pointing HR at that field
	  blocking    mandatory, missing, no counterpart here -- rendered as a control
	              in the pending panel; blocks the submit
	  conditional mandatory only under `mandatory_depends_on`; collected, never blocking
	  derived     read_only/virtual; Employee.validate() fills them before
	              _validate() runs, so blocking on them would be wrong
	  manual      real blockers that cannot be captured here (child tables,
	              permlevel-restricted, unrenderable fieldtype)
	"""
	if preview is None:
		preview = build_employee(applicant)
	meta = frappe.get_meta(EMPLOYEE_DOCTYPE)
	writable_permlevels = set(meta.get_permlevel_access("write"))

	buckets = {"native": [], "blocking": [], "conditional": [], "derived": [], "manual": []}
	seen = set()

	for fieldname, _msg in preview._get_missing_mandatory_fields():
		df = meta.get_field(fieldname)
		if not df:
			# parent/parenttype from the istable branch; not applicable to Employee.
			continue
		seen.add(fieldname)

		if df.fieldtype in table_fields:
			buckets["manual"].append(_serialise_docfield(df))
		elif df.read_only or df.get("is_virtual") or df.fieldtype == "Read Only":
			buckets["derived"].append(_serialise_docfield(df))
		elif (df.permlevel or 0) not in writable_permlevels:
			buckets["manual"].append(_serialise_docfield(df))
		elif df.fieldtype not in RENDERABLE_FIELDTYPES:
			buckets["manual"].append(_serialise_docfield(df))
		elif source := _native_source(applicant, fieldname):
			entry = _serialise_docfield(df)
			entry["source_fieldname"] = source
			entry["label"] = applicant.meta.get_label(source) or entry.get("label")
			entry["tab_label"] = _tab_label(applicant, source)
			buckets["native"].append(entry)
		else:
			# `hidden` deliberately does NOT disqualify: _validate_mandatory ignores
			# it, so a hidden+reqd field still fails the insert.
			buckets["blocking"].append(_serialise_docfield(df))

	# Conditionally-mandatory fields are not in the missing list (the server filters
	# on `reqd` alone), so they are collected separately.
	for df in meta.fields:
		if df.fieldname in seen or df.reqd or not df.get("mandatory_depends_on"):
			continue
		if df.fieldtype in no_value_fields or df.fieldtype not in RENDERABLE_FIELDTYPES:
			continue
		if df.read_only or df.get("is_virtual"):
			continue
		if preview.get(df.fieldname) not in (None, ""):
			continue
		if _is_mandatory_conditionally(df, preview):
			buckets["conditional"].append(_serialise_docfield(df))

	return buckets


def get_pending_employee_fields(applicant) -> list[dict]:
	"""Every field that must be supplied before an Employee can be created.

	This is the set `before_submit` refuses on. `derived` and `conditional` are
	excluded by design (see `resolve`).
	"""
	buckets = resolve(applicant)
	return buckets["native"] + buckets["blocking"] + buckets["manual"]


def describe(applicant) -> dict:
	"""Full payload for the Desk panel and the external app -- one shared contract,
	so the two can never disagree about what is still outstanding."""
	preview = build_employee(applicant)
	buckets = resolve(applicant, preview=preview)
	captured = {
		row.fieldname: row.value
		for row in (applicant.get("pending_employee_fields") or [])
		if row.fieldname
	}

	for key in ("blocking", "conditional", "manual"):
		for df in buckets[key]:
			df["value"] = captured.get(df["fieldname"])

	return {
		"ready": not (buckets["native"] or buckets["blocking"] or buckets["manual"]),
		"native": buckets["native"],
		"blocking": buckets["blocking"],
		"conditional": buckets["conditional"],
		"derived": buckets["derived"],
		"manual": buckets["manual"],
		"captured": captured,
		# Plain dict of every Employee fieldname, so link_filters expressions and any
		# surviving depends_on resolve against real keys client-side.
		"employee_preview": preview.as_dict(no_default_fields=True),
		# Applicant fields that feed Employee -- the client re-fetches when one changes.
		"mapped_fieldnames": sorted(
			source for source in EMPLOYEE_FIELD_MAP if applicant.meta.has_field(source)
		),
		"signature": signature(buckets),
	}


def signature(buckets: dict | None = None) -> str:
	"""Stable hash of the SITE's mandatory Employee fields.

	Deliberately hashes the site configuration, not the outstanding set: the
	outstanding set shrinks every time HR fills a field in, which is normal progress,
	not a configuration change. Hashing that would raise a "the mandatory fields
	changed" warning on almost every save.

	Advisory only -- `before_submit` always recomputes from live meta. Meta is the
	source of truth at submit time; this is a UX hint, never an input to the decision.
	`buckets` is accepted and ignored so existing call sites keep working.
	"""
	names = sorted(df.fieldname for df in frappe.get_meta(EMPLOYEE_DOCTYPE).get("fields", {"reqd": 1}))
	return frappe.utils.sha256_hash("|".join(names)) if names else ""


def format_missing_message(pending: list[dict]) -> str:
	"""Bulleted list of every missing field -- all at once, never one at a time."""
	items = []
	for df in pending:
		label = frappe.utils.escape_html(str(df.get("label") or df["fieldname"]))
		if df.get("source_fieldname"):
			items.append(f"<li>{label}</li>")
		else:
			items.append(f"<li>{label} <i>({_('in Pending Employee Fields')})</i></li>")
	return "<ul>{0}</ul>".format("".join(items))
