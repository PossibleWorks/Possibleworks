import frappe
from hrms.hr.doctype.shift_type.shift_type import ShiftType

CUSTOM_ATTENDANCE_TYPES = {"Positive Attendance", "Negative Attendance"}


def _is_custom_attendance_employee(employee: str) -> bool:
    attendance_type = frappe.db.get_value("Employee", employee, "custom_attendance_type")
    return attendance_type in CUSTOM_ATTENDANCE_TYPES


class PossibleWorksShiftType(ShiftType):
    def should_mark_attendance(self, employee: str, attendance_date: str) -> bool:
        if _is_custom_attendance_employee(employee):
            return False
        return super().should_mark_attendance(employee, attendance_date)

    def mark_absent_for_dates_with_no_attendance(self, employee: str):
        if _is_custom_attendance_employee(employee):
            return
        super().mark_absent_for_dates_with_no_attendance(employee)

    def mark_absent_for_half_day_dates(self, employee: str):
        if _is_custom_attendance_employee(employee):
            return
        super().mark_absent_for_half_day_dates(employee)
