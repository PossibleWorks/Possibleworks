import frappe
from frappe import _
from frappe.utils import getdate


# =============================================================================
# HOLIDAY UTILITIES
# Reusable across server scripts, scheduled jobs, and other utils
# =============================================================================

def build_holiday_cache():
    """
    Fetch ALL holidays from DB into a dict keyed by holiday list name.
    Call once per script execution and pass the result around.

    Returns:
        {
            "Holiday List Name": {date(2026, 1, 1), date(2026, 1, 26), ...},
            ...
        }
    """
    holiday_map = {}
    rows = frappe.db.sql(
        """
        SELECT parent AS holiday_list, holiday_date
        FROM `tabHoliday`
        """,
        as_dict=True,
    )
    for r in rows:
        holiday_map.setdefault(r.holiday_list, set()).add(getdate(r.holiday_date))
    return holiday_map


@frappe.whitelist()
def get_holiday_list_for_employee(employee, date):
    """
    Resolve the correct holiday list for an employee on a given date.
    Checks Holiday List Assignments first, falls back to employee's default.

    Returns:
        holiday_list name (str) or None
    """
    date = getdate(date)
    # Check assignments (time-bound overrides)
    assignment = frappe.db.get_value(
        "Holiday List Assignment",
        {
            "assigned_to": employee,
            "from_date":   ["<=", date],
            "docstatus":   1,
        },
        "holiday_list",
        order_by="from_date desc",  # Most recent assignment wins
    )
    if assignment:
        return assignment

    # Fallback to employee master
    return frappe.db.get_value("Employee", employee, "holiday_list")



def get_holidays_for_employee(employee, from_date, to_date, holiday_cache=None):
    """
    Get all holiday dates for an employee within a date range.
    Respects Holiday List Assignments and holiday list validity dates.

    Throws:
        If no holiday list validity covers the requested date range.
    """
    from_date = getdate(from_date)
    to_date   = getdate(to_date)

    if holiday_cache is None:
        holiday_cache = build_holiday_cache()

    # Get all applicable assignments in range
    assignments = frappe.db.sql(
        """
        SELECT holiday_list, from_date
        FROM `tabHoliday List Assignment`
        WHERE assigned_to = %s
          AND docstatus = 1
          AND from_date <= %s
        ORDER BY from_date DESC
        """,
        (employee, to_date),
        as_dict=True,
    )

    if not assignments:
        default_list = frappe.db.get_value("Employee", employee, "holiday_list")
        if default_list:
            assignments = [{"holiday_list": default_list, "from_date": from_date}]

    if not assignments:
        frappe.throw(
            _("No Holiday List assigned for Employee {0}.").format(frappe.bold(employee)),
            title=_("Holiday List Required"),
        )

    all_holidays = set()
    coverage_found = False   

    for assignment in assignments:
        holiday_list_name = assignment["holiday_list"]
        assignment_from   = getdate(assignment["from_date"])

        validity = frappe.db.get_value(
            "Holiday List",
            holiday_list_name,
            ["from_date", "to_date"],
            as_dict=True,
        )
        if not validity:
            continue

        list_from = getdate(validity.from_date)
        list_to   = getdate(validity.to_date)

        # Effective range = intersection of assignment, list validity, and query range
        effective_from = max(assignment_from, list_from, from_date)
        effective_to   = min(list_to, to_date)

        # ⭐ Detect coverage
        if effective_from <= effective_to:
            coverage_found = True

            if holiday_list_name in holiday_cache:
                for hdate in holiday_cache[holiday_list_name]:
                    if effective_from <= hdate <= effective_to:
                        all_holidays.add(hdate)


    if not coverage_found:
        frappe.throw(
            _("No Holiday List validity covers Employee {0} for the period {1} to {2}. "
              "Please extend the Holiday List or create a Holiday List Assignment.")
            .format(frappe.bold(employee), frappe.bold(from_date), frappe.bold(to_date)),
            title=_("Holiday Policy Missing"),
        )

    return all_holidays


@frappe.whitelist()
def is_holiday(employee, date, holiday_cache=None):
    """
    Check whether a specific date is a holiday for an employee.
    Safe to call from server scripts via frappe.call().
    Throws if no Holiday List is assigned to the employee (default or assignment).

    Args:
        employee     : Employee ID
        date         : date string or date object
        holiday_cache: Optional pre-built cache (not passable via frappe.call,
                       but usable when called directly from Python)

    Returns:
        True if holiday, False otherwise
    """
    date = getdate(date)
    holiday_list = get_holiday_list_for_employee(employee, date)
    if not holiday_list:
        frappe.throw(
            _("No Holiday List assigned for Employee {0} on {1}. Set a default Holiday List on the Employee or create a Holiday List Assignment.").format(
                frappe.bold(employee), date
            ),
            title=_("Holiday List Required"),
        )
    holidays = get_holidays_for_employee(employee, date, date, holiday_cache=holiday_cache)
    return date in holidays


def is_working_day(employee, date, holiday_cache=None):
    """
    Inverse of is_holiday. Returns True if the date is a working day.
    """
    return not is_holiday(employee, date, holiday_cache=holiday_cache)


def count_working_days(employee, from_date, to_date, holiday_cache=None):
    """
    Count working days (non-holidays) for an employee between two dates inclusive.

    Args:
        employee     : Employee ID
        from_date    : date object or string
        to_date      : date object or string
        holiday_cache: Optional pre-built cache

    Returns:
        int — number of working days
    """
    from_date = getdate(from_date)
    to_date   = getdate(to_date)

    holidays = get_holidays_for_employee(employee, from_date, to_date, holiday_cache=holiday_cache)

    count = 0
    temp  = from_date
    while temp <= to_date:
        if temp not in holidays:
            count += 1
        temp = frappe.utils.add_days(temp, 1)
    return count


def count_working_days_in_period(employee, from_date, to_date, period_start, period_end, holiday_cache=None):
    """
    Count working days for an employee between from_date and to_date
    BUT only counting days that also fall within period_start → period_end.

    Useful for payroll period-aware leave validation.

    Args:
        employee     : Employee ID
        from_date    : Leave application start date
        to_date      : Leave application end date
        period_start : Payroll period start
        period_end   : Payroll period end
        holiday_cache: Optional pre-built cache

    Returns:
        int/float — number of working days within the period
    """
    from_date    = getdate(from_date)
    to_date      = getdate(to_date)
    period_start = getdate(period_start)
    period_end   = getdate(period_end)

    # Narrow the range to only what overlaps the period
    effective_from = max(from_date, period_start)
    effective_to   = min(to_date, period_end)

    if effective_from > effective_to:
        return 0

    holidays = get_holidays_for_employee(
        employee, effective_from, effective_to, holiday_cache=holiday_cache
    )

    count = 0
    temp  = effective_from
    while temp <= effective_to:
        if temp not in holidays:
            count += 1
        temp = frappe.utils.add_days(temp, 1)
    return count