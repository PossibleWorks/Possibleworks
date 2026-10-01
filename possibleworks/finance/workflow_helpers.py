import frappe
from frappe.model.workflow import get_workflow_safe_globals


@frappe.whitelist()
def get_starting_transitions(doctype, doc=None):
	"""Workflow transitions available from a doctype's starting state, for the
	current user's own roles — usable for a brand-new (unsaved) document, unlike
	frappe.model.workflow.get_transitions which always returns [] until the doc
	is saved (`if doc.is_new(): return []`).

	Mirrors get_transitions' own role check (`transition.allowed in frappe.get_roles()`)
	but is scoped to whichever state has no incoming transition, since that's the
	state a brand-new document starts in.

	`doc` (optional): JSON string of the in-progress form's current field values.
	Used to evaluate each transition's `condition`, the same way
	frappe.model.workflow.get_transitions does for a saved doc (e.g. scoping
	Payment Entry's "Send For Approval" to whichever of Receive/Pay the user
	picked) — without it, a transition with a condition can't be evaluated yet,
	so every role-matching transition would show regardless of its condition.
	"""
	workflow_name = frappe.db.get_value(
		"Workflow", {"document_type": doctype, "is_active": 1}, "name"
	)
	if not workflow_name:
		return []

	workflow = frappe.get_cached_doc("Workflow", workflow_name)
	if not workflow.transitions:
		return []

	next_states = {t.next_state for t in workflow.transitions}
	start_state = next(
		(s.state for s in workflow.states if s.state not in next_states),
		workflow.states[0].state if workflow.states else None,
	)
	if not start_state:
		return []

	roles = frappe.get_roles()
	doc_status_by_state = {s.state: s.doc_status for s in workflow.states}
	doc_values = frappe._dict(frappe.parse_json(doc) or {})
	available = [
		t for t in workflow.transitions
		if t.state == start_state and t.allowed in roles
		and (
			not t.condition
			or frappe.safe_eval(t.condition, get_workflow_safe_globals(), {"doc": doc_values})
		)
	]

	# If the user can jump straight to a submitted state (e.g. an approver
	# creating their own request), that's the only button worth showing —
	# offering an intermediate "send it to myself" step alongside it is just
	# noise. Only fall back to the intermediate transitions when none of the
	# user's available transitions reach a submitted state directly.
	direct_submit = [t for t in available if doc_status_by_state.get(t.next_state) == "1"]
	return [t.action for t in (direct_submit or available)]
