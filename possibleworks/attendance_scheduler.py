import frappe
from frappe.utils import getdate, nowdate

from hrms.hr.doctype.attendance.attendance import mark_attendance
from possibleworks.utils.holiday_utils import build_holiday_cache, is_holiday


def mark_negative_attendance():
    """
    Daily scheduler (runs at 11:30 PM).
    Marks Present for all active negative attendance employees on today's date,
    skipping holidays and days that already have an attendance record (covers leaves too).
    """
    today = getdate(nowdate())
    logger = frappe.logger()
    logger.info(f"[NegativeAttendance] Starting attendance marking for {today}")

    employees = frappe.get_all(
        "Employee",
        filters={
            "status": "Active",
            "custom_negative_attendance": 1,
        },
        fields=["name", "company"],
    )

    if not employees:
        logger.info("[NegativeAttendance] No negative attendance employees found, exiting")
        return

    logger.info(f"[NegativeAttendance] Found {len(employees)} negative attendance employee(s)")

    holiday_cache = build_holiday_cache()
    marked = 0
    skipped_holiday = 0
    skipped_existing = 0

    for emp in employees:
        employee = emp.name

        # Skip holidays
        try:
            if is_holiday(employee, today, holiday_cache=holiday_cache):
                logger.info(f"[NegativeAttendance] Skipping {employee} — holiday on {today}")
                skipped_holiday += 1
                continue
        except frappe.ValidationError:
            logger.warning(
                f"[NegativeAttendance] No holiday list assigned for {employee}, skipping to avoid incorrect marking"
            )
            skipped_holiday += 1
            continue

        # Skip if attendance already exists (covers On Leave, manual entries, etc.)
        # Existing status  → Action
        # Present          → Skip (already marked)
        # Absent           → Skip (HR manually marked absent, respect it)
        # Half Day         → Skip (respect it)
        # On Leave         → Skip (leave covers the day)
        # Cancelled (doc=2)→ Treat as blank, mark Present again
        existing = frappe.db.exists(
            "Attendance",
            {
                "employee": employee,
                "attendance_date": today,
                "docstatus": ["!=", 2],
            },
        )
        if existing:
            logger.info(
                f"[NegativeAttendance] Skipping {employee} — attendance already exists for {today}"
            )
            skipped_existing += 1
            continue

        # Mark Present
        attendance_name = mark_attendance(employee, today, "Present")
        if attendance_name:
            logger.info(
                f"[NegativeAttendance] Marked Present for {employee} on {today} [{attendance_name}]"
            )
            marked += 1
        else:
            logger.warning(
                f"[NegativeAttendance] Failed to mark Present for {employee} on {today} — possible duplicate"
            )

    logger.info(
        f"[NegativeAttendance] Done for {today} — marked: {marked}, "
        f"skipped (holiday): {skipped_holiday}, skipped (existing): {skipped_existing}"
    )
