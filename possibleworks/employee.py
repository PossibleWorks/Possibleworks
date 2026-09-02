import frappe
from frappe import _
from frappe.utils import get_link_to_form, today


def _manager_reassignment_enabled():
    # Same Policy Configuration gate as sync_leave_approver_and_reports_to. Defaults to
    # enabled, but stays a per-site switch: disabling it turns off both the hard block
    # below and the reassignment dialog, returning Inactive/Suspended status changes to
    # plain erpnext behaviour (only "Left" carries any such validation out of the box).
    policy = frappe.get_single("Policy Configuration")
    return bool(policy.get("enable_manager_status_reassignment"))


def block_status_change_with_active_reports(doc, method):
    if not _manager_reassignment_enabled():
        return

    if doc.status not in ("Inactive", "Suspended"):
        return

    active_reports = frappe.get_all(
        "Employee",
        filters={"reports_to": doc.name, "status": "Active"},
        fields=["name", "employee_name"],
    )
    if not active_reports:
        return

    link_to_employees = [
        get_link_to_form("Employee", employee.name, label=employee.employee_name)
        for employee in active_reports
    ]
    message = _("The following employees are currently still reporting to {0}:").format(
        frappe.bold(doc.employee_name)
    )
    message += "<br><br><ul><li>" + "</li><li>".join(link_to_employees) + "</li></ul><br>"
    message += _("Please make sure the employees above report to another Active employee.")
    frappe.throw(message, title=_("Cannot Change Employee Status"))


STATUSES_REQUIRING_MANAGER_REASSIGNMENT = ("Left", "Inactive", "Suspended")


def _get_active_direct_reports(employee):
    return frappe.get_all(
        "Employee",
        filters={"reports_to": employee, "status": "Active"},
        fields=["name", "employee_name"],
    )


@frappe.whitelist()
def get_active_direct_reports(employee):
    """Active employees directly reporting to `employee`, for the status-change reassignment dialog.

    Returns an empty list when the feature is disabled, rather than throwing: the client script
    treats an empty list as "nothing to reassign" and lets the save proceed normally. With the
    feature disabled, block_status_change_with_active_reports is also a no-op, so that save goes
    through untouched -- a disabled site sees plain erpnext behaviour for Inactive/Suspended.
    """
    frappe.has_permission("Employee", "write", doc=employee, throw=True)
    if not _manager_reassignment_enabled():
        return []
    return _get_active_direct_reports(employee)


@frappe.whitelist()
def change_status_with_reassignment(employee, new_status, new_manager, relieving_date=None):
    """Reassign `employee`'s active direct reports to `new_manager`, then apply the status change.

    Both steps run in this one whitelisted call, so a failure at either stage rolls back the
    other -- frappe's own request handler rolls back the whole DB transaction when a
    whitelisted call raises (frappe/app.py), so Employee is never left in a state where the
    status changed but reports were not reassigned, or vice versa, for any real API caller.
    """
    frappe.has_permission("Employee", "write", doc=employee, throw=True)

    if not _manager_reassignment_enabled():
        frappe.throw(_("Manager status reassignment is not enabled for this site."))

    if new_status not in STATUSES_REQUIRING_MANAGER_REASSIGNMENT:
        frappe.throw(_("{0} is not a status that requires reassigning reports.").format(new_status))

    if not new_manager:
        frappe.throw(_("Please select a manager to reassign the reports to."))

    if new_manager == employee:
        frappe.throw(_("Cannot reassign reports to the employee whose status is changing."))

    new_manager_status, new_manager_user_id = frappe.db.get_value(
        "Employee", new_manager, ["status", "user_id"]
    ) or (None, None)

    if new_manager_status is None:
        frappe.throw(_("Employee {0} does not exist.").format(new_manager))
    if new_manager_status != "Active":
        frappe.throw(
            _("{0} is not an Active employee and cannot be assigned as a manager.").format(new_manager)
        )
    if not new_manager_user_id:
        frappe.throw(
            _("{0} does not have a user account assigned and cannot be a Leave Approver.").format(
                new_manager
            )
        )

    # Re-fetch live rather than trusting a list the client saw earlier -- someone else may have
    # added a new direct report between the dialog opening and this call.
    active_reports = _get_active_direct_reports(employee)

    # `new_manager` being one of these reports (promoting a peer to take over the team)
    # can't be handled by just excluding them from the loop below: they'd keep reporting
    # to `employee`, which is itself still an active direct report as far as
    # block_status_change_with_active_reports is concerned -- so the status save a few
    # lines down would immediately hit that same hard block this feature exists to avoid.
    # Moving them to report to someone else first is a separate action for HR to take.
    if new_manager in [report.name for report in active_reports]:
        frappe.throw(
            _("{0} currently reports to {1} and can't be the new manager for their team.").format(
                new_manager, employee
            )
        )

    for report in active_reports:
        report_doc = frappe.get_doc("Employee", report.name)
        report_doc.reports_to = new_manager
        report_doc.leave_approver = new_manager_user_id
        report_doc.save()

    employee_doc = frappe.get_doc("Employee", employee)
    employee_doc.status = new_status
    if new_status == "Left" and relieving_date:
        employee_doc.relieving_date = relieving_date
    employee_doc.save()

    return {
        "reassigned": [report.name for report in active_reports],
        "new_status": new_status,
    }


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
