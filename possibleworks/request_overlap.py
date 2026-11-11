# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Stops an employee from holding a Leave Application and an Attendance Request on the
same date. HRMS lets both be raised, then fails whichever one the manager approves second
("Attendance already marked" / "No attendance records to create"), so the clash is
caught when the second one is raised instead.

Half days are blocked too: the PW app only raises full-day Attendance Requests, and
Attendance Request has no First/Second Half field to match a half-day leave against.

Hooked on before_validate so this message comes ahead of HRMS's own attendance checks
in validate(). Switched per site by Policy Configuration, on by default.
"""

import frappe
from frappe import _
from frappe.model.workflow import get_workflow_name
from frappe.utils import formatdate

# The Attendance Request workflow rejects without submitting: the request stays a draft
# (docstatus 0) in this state.
AR_REJECTED_STATE = "Rejected"


def validate_no_attendance_request_overlap(doc, method=None):
	"""Leave Application: no live Attendance Request on any of the leave's dates."""
	# Rejecting is a submit in HRMS; it must go through so the manager can clear a clash.
	if doc.status in ("Rejected", "Cancelled"):
		return

	# Live = submitted, or a draft the workflow hasn't rejected. Only while the workflow is
	# on: its state column outlives a disabled workflow.
	live = (
		{"docstatus": 1, "workflow_state": ["!=", AR_REJECTED_STATE]}
		if get_workflow_name("Attendance Request")
		else None
	)
	_throw_if_overlap(doc, "Attendance Request", live, _("An attendance regularization"), _("applying for leave"))


def validate_no_leave_overlap(doc, method=None):
	"""Attendance Request: no live Leave Application on any of the request's dates."""
	# Rejecting must go through so the manager can clear a clash.
	if get_workflow_name(doc.doctype) and doc.get("workflow_state") == AR_REJECTED_STATE:
		return

	# Live = a draft, or submitted and not rejected. Cancelled is docstatus 2.
	_throw_if_overlap(
		doc,
		"Leave Application",
		{"docstatus": 0, "status": ["!=", "Rejected"]},
		_("A leave application"),
		_("raising an attendance regularization"),
	)


def _overlap_block_enabled():
	# get_single, not db.get_single_value: on a site that never saved the page only
	# get_single applies the field's default of 1.
	policy = frappe.get_single("Policy Configuration")
	return bool(policy.get("enable_leave_attendance_overlap_block"))


def _throw_if_overlap(doc, other_doctype, live, existing, action):
	# before_validate still fires under ignore_validate (imports, scripts); respect it.
	if doc.flags.ignore_validate or not (doc.employee and doc.from_date and doc.to_date):
		return
	if not _overlap_block_enabled():
		return

	# get_all, not db.get_value: only get_all wraps "!=" in IFNULL, and requests raised
	# before a site's workflow existed have a NULL state.
	clashes = frappe.get_all(
		other_doctype,
		filters={
			"employee": doc.employee,
			"docstatus": ["<", 2],
			"from_date": ["<=", doc.to_date],
			"to_date": [">=", doc.from_date],
		},
		or_filters=live,
		fields=["name", "from_date", "to_date"],
		limit=1,
	)
	if not clashes:
		return

	# No document name: employees see this in the PW app, where the ID means nothing.
	from_date, to_date = (formatdate(d, "d MMM yyyy") for d in (clashes[0].from_date, clashes[0].to_date))
	dates = from_date if from_date == to_date else _("{0} to {1}").format(from_date, to_date)
	frappe.throw(
		_("{0} already exists for {1}. Withdraw it or get it rejected before {2}.").format(
			existing, dates, action
		),
		title=_("Overlapping Request"),
	)
