import frappe
from frappe.utils import getdate, add_days, get_last_day, get_first_day


# =============================================================================
# INTERNAL CORE LOGIC
# =============================================================================

def _get_payroll_config(target_date, company=None):
    """
    Fetch payroll config from Payroll Period covering target_date.
    Returns:
        payroll_type
        start_day
        end_day
    """
    filters = {
        "start_date": ["<=", target_date],
        "end_date": [">=", target_date],
        "docstatus": ["!=", 2],
    }

    if company:
        filters["company"] = company

    

    payroll_period = frappe.db.get_value(
        "Payroll Period",
        filters,
        [
            "custom_payroll_type",
            "custom_period_start_day",
            "custom_period_end_day",
        ],
        as_dict=True,
    )

    # print("===== PAYROLL CONFIG DEBUG =====")
    # print("Target date:", target_date)
    # print("Company:", company)
    # print("Payroll period row:", payroll_period)
    # print("================================")

    # Safe defaults
    if not payroll_period:
        return {
            "type": "Monthly",
            "start_day": 1,
            "end_day": 31,
        }

    return {
        "type": payroll_period.get("custom_payroll_type") or "Monthly",
        "start_day": int(payroll_period.get("custom_period_start_day") or 1),
        "end_day": int(payroll_period.get("custom_period_end_day") or 31),
    }


def _clamp_day_to_month(year, month, day):
    """
    Clamp configured day to actual month length
    Handles leap year automatically
    """
    first = getdate(f"{year}-{str(month).zfill(2)}-01")
    last_day = get_last_day(first).day
    return min(day, last_day)


def _build_period_date(year, month, configured_day):
    safe_day = _clamp_day_to_month(year, month, configured_day)
    return getdate(f"{year}-{str(month).zfill(2)}-{str(safe_day).zfill(2)}")


def _get_period_boundaries(target_date, company=None):
    """
    CORE payroll period computation logic
    Supports:
        - Monthly
        - Custom same-month window
        - Custom cross-month window
    """

    config = _get_payroll_config(target_date, company)

    payroll_type = config["type"]
    start_day = config["start_day"]
    end_day = config["end_day"]

    year = target_date.year
    month = target_date.month
    day = target_date.day

    # =========================================================
    # MONTHLY STRATEGY
    # =========================================================
    if payroll_type == "Monthly":
        return get_first_day(target_date), get_last_day(target_date)

    # Clamp start & end for this month
    effective_start = _clamp_day_to_month(year, month, start_day)
    effective_end = _clamp_day_to_month(year, month, end_day)

    # =========================================================
    # SAME MONTH WINDOW
    # =========================================================
    if start_day <= end_day:
        # `day` may fall outside this month's own window (e.g. day=20 with a
        # 6->10 window) -- resolve to the adjacent month's cycle instead of
        # silently repeating this month's window for an out-of-range date.
        if day < effective_start:
            if month == 1:
                ref_year, ref_month = year - 1, 12
            else:
                ref_year, ref_month = year, month - 1
        elif day > effective_end:
            if month == 12:
                ref_year, ref_month = year + 1, 1
            else:
                ref_year, ref_month = year, month + 1
        else:
            ref_year, ref_month = year, month

        period_start = _build_period_date(ref_year, ref_month, start_day)
        period_end = _build_period_date(ref_year, ref_month, end_day)
        return period_start, period_end

    # =========================================================
    # CROSS MONTH WINDOW
    # =========================================================

    # Subcase A → inside current cycle
    if day >= effective_start:

        period_start = _build_period_date(year, month, start_day)

        if month == 12:
            next_year, next_month = year + 1, 1
        else:
            next_year, next_month = year, month + 1

        period_end = _build_period_date(next_year, next_month, end_day)

    # Subcase B → previous cycle
    else:

        if month == 1:
            prev_year, prev_month = year - 1, 12
        else:
            prev_year, prev_month = year, month - 1

        period_start = _build_period_date(prev_year, prev_month, start_day)
        period_end = _build_period_date(year, month, end_day)

    return period_start, period_end


# =============================================================================
# PUBLIC SERVER SCRIPT API
# =============================================================================

@frappe.whitelist()
def get_period_boundaries(date, company=None):
    """
    Server Script safe API.
    """
    target_date = getdate(date)

    if not company:
        company = frappe.defaults.get_defaults().get("company")

    start, end = _get_period_boundaries(target_date, company=company)

    result = {
        "start": str(start),
        "end": str(end),
    }


    return result