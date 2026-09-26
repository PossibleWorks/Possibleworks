"""
Runs a doctype's submit-time validation early, at the moment ANY Workflow
transition hands a document to its next approval stage, instead of leaving
it to surface only when the document is finally Submitted.

Why this is needed: several submit-time checks live in a doctype's
before_submit (e.g. Journal Entry's debit/credit balance check in
apps/erpnext/erpnext/accounts/doctype/journal_entry/journal_entry.py) rather
than validate(), by core design -- a draft should be editable/incomplete
until it's actually submitted. That's fine without a workflow. But once a
Workflow sits in front of Submit (Draft -> Pending Approval -> Approved),
every transition between two docstatus-0 states (e.g. "Send For Approval",
"Escalate") is a plain save(), not a submit() -- see
frappe.model.workflow.apply_workflow. So before_submit silently never runs
until the FINAL transition that actually flips docstatus to 1, and whoever
clicks that (the approver) is the one who hits the error, not the creator
who raised a bad document in the first place.

Fix: hooked generically (doc_events["*"]["validate"] in hooks.py) rather
than per-doctype, so it covers every doctype that has an active Workflow --
today's (Journal Entry, Request for Quotation, Purchase Order, Purchase
Indent, Material Request) and any added later -- with no further hooks.py
changes needed. Whenever a save changes the workflow_state field while the
document is still unsubmitted, its before_submit is run early via
run_method(), which is a safe no-op for doctypes that don't define one.
"""

import frappe
from frappe.model.workflow import get_workflow, get_workflow_name, get_workflow_state_field


def validate_workflow_transition_early(doc, method=None):
    if doc.is_new() or doc.docstatus != 0:
        return

    workflow_name = get_workflow_name(doc.doctype)
    if not workflow_name:
        return

    state_field = get_workflow_state_field(workflow_name)
    new_state = doc.get(state_field)
    if not new_state:
        return

    old_state = frappe.db.get_value(doc.doctype, doc.name, state_field)
    if old_state == new_state:
        return

    workflow = get_workflow(doc.doctype)
    new_state_row = next((s for s in workflow.states if s.state == new_state), None)
    if new_state_row and new_state_row.doc_status == "1":
        # Transitioning straight into a submitted state -- that's a real
        # Submit, whose own before_submit already runs right after this
        # validate() call in the normal submit flow. Don't run it twice.
        return

    doc.run_method("before_submit")
