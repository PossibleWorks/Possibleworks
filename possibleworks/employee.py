import frappe
from frappe.utils import today


def sync_leave_approver_and_reports_to(doc, method):
    # Gate: only run if Policy Configuration flag is enabled
    policy = frappe.get_single("Policy Configuration")
    if not policy.get("enable_leave_approver_and_reports_to_sync"):
        return

    # Employee is NOT yet written to DB here (before_save), so frappe.db.get_value
    # returns the true old values from the last committed state
    old_values = frappe.db.get_value(
        "Employee",
        doc.name,
        ["leave_approver", "reports_to"],
        as_dict=True,
    ) or {}

    old_leave_approver = old_values.get("leave_approver")
    old_reports_to = old_values.get("reports_to")

    leave_approver_changed = old_leave_approver != doc.leave_approver
    reports_to_changed = old_reports_to != doc.reports_to

    if not leave_approver_changed and not reports_to_changed:
        return

    # Build emp_id → user_id map for all active employees with a user account
    all_employees = frappe.get_all(
        "Employee",
        filters={"status": "Active", "user_id": ["!=", ""]},
        fields=["name", "user_id"],
    )
    emp_id_to_user_id = {e.name: e.user_id for e in all_employees if e.user_id}

    # Sync: leave_approver changed → derive and set reports_to on doc (persists in this save)
    if leave_approver_changed and doc.leave_approver:
        approver_emp_id = next(
            (emp_id for emp_id, user_id in emp_id_to_user_id.items() if user_id == doc.leave_approver),
            None,
        )
        if not approver_emp_id:
            frappe.throw(
                f"Leave Approver <b>{doc.leave_approver}</b> has no associated active Employee record."
            )
        if approver_emp_id == doc.name:
            frappe.throw("An employee cannot be their own leave approver.")

        doc.reports_to = approver_emp_id

    # Sync: reports_to changed → derive and set leave_approver on doc (persists in this save)
    if reports_to_changed and doc.reports_to:
        if doc.reports_to not in emp_id_to_user_id:
            frappe.throw(
                f"Reports To employee <b>{doc.reports_to}</b> does not have a user account assigned."
            )
        if doc.reports_to == doc.name:
            frappe.throw("An employee cannot report to themselves.")

        doc.leave_approver = emp_id_to_user_id[doc.reports_to]

    if not doc.leave_approver:
        return

    # Find the active Leave Period for today and this employee's company
    active_period = frappe.db.get_value(
        "Leave Period",
        {
            "company": doc.company,
            "from_date": ["<=", today()],
            "to_date": [">=", today()],
            "is_active": 1,
        },
        ["name", "from_date", "to_date"],
        as_dict=True,
    )

    if not active_period:
        return

    # Fetch draft Leave Applications within the active leave period
    draft_leave_apps = frappe.get_all(
        "Leave Application",
        filters={
            "employee": doc.name,
            "docstatus": 0,
            "from_date": [">=", active_period.from_date],
            "to_date": ["<=", active_period.to_date],
        },
        fields=["name"],
    )

    approver_full_name = frappe.db.get_value("User", doc.leave_approver, "full_name")

    for row in draft_leave_apps:
        leave_doc = frappe.get_doc("Leave Application", row.name)
        leave_doc.leave_approver = doc.leave_approver
        leave_doc.leave_approver_name = approver_full_name
        # Full validation runs — any error propagates up and rolls back the entire transaction
        # including the Employee save in progress
        leave_doc.save()
