import frappe
from hrms.hr.doctype.shift_type.shift_type import ShiftType


def _is_negative_attendance_employee(employee: str) -> bool:
    return bool(frappe.db.get_value("Employee", employee, "custom_negative_attendance"))


class PossibleWorksShiftType(ShiftType):
    def should_mark_attendance(self, employee: str, attendance_date: str) -> bool:
        if _is_negative_attendance_employee(employee):
            frappe.logger().info(
                f"[PossibleWorks] Skipping checkin-based attendance marking for negative attendance employee {employee} on {attendance_date}"
            )
            return False
        return super().should_mark_attendance(employee, attendance_date)

    def mark_absent_for_dates_with_no_attendance(self, employee: str):
        if _is_negative_attendance_employee(employee):
            frappe.logger().info(
                f"[PossibleWorks] Skipping absent marking (no checkins) for negative attendance employee {employee}"
            )
            return
        super().mark_absent_for_dates_with_no_attendance(employee)

    def mark_absent_for_half_day_dates(self, employee: str):
        if _is_negative_attendance_employee(employee):
            frappe.logger().info(
                f"[PossibleWorks] Skipping half-day absent conversion for negative attendance employee {employee}"
            )
            return
        super().mark_absent_for_half_day_dates(employee)
