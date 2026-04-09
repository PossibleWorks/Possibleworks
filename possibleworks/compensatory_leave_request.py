# Copyright (c) 2025, Possibleworks and Contributors
# License: MIT

import frappe
from frappe import _
from frappe.utils import flt
from hrms.hr.doctype.compensatory_leave_request.compensatory_leave_request import (
	CompensatoryLeaveRequest,
)


class PossibleWorksCompensatoryLeaveRequest(CompensatoryLeaveRequest):
	def validate(self):
		policy = frappe.get_single("Policy Configuration")

		if not policy.enable_custom_comp_off_validation:
			super().validate()
			return

		# Flag checked inside validate_attendance() below
		self._skip_attendance_validation = not self._submitted_attendance_exists()

		super().validate()

		# Only check working hours when attendance records were found
		# (if skipped, there's nothing to validate against)
		if not self._skip_attendance_validation:
			self._validate_working_hours(policy)

	# --- override point -------------------------------------------------------

	def validate_attendance(self):
		"""Skip HRMS attendance check when no submitted attendance exists."""
		if getattr(self, "_skip_attendance_validation", False):
			return
		super().validate_attendance()

	# --- helpers --------------------------------------------------------------

	def _submitted_attendance_exists(self):
		"""
		Use the same filters as HRMS validate_attendance so the flag is set
		only when HRMS would itself throw due to missing records.
		"""
		return bool(
			frappe.db.count(
				"Attendance",
				{
					"employee": self.employee,
					"attendance_date": ["between", [self.work_from_date, self.work_end_date]],
					"status": ["in", ["Present", "Work From Home", "Half Day"]],
					"docstatus": 1,
				},
			)
		)

	def _validate_working_hours(self, policy):
		"""
		Validate that every attendance record in the request range meets the
		minimum working hours configured in Policy Configuration.
		"""
		attendance_records = frappe.get_all(
			"Attendance",
			filters={
				"employee": self.employee,
				"attendance_date": ["between", [self.work_from_date, self.work_end_date]],
				"status": ["in", ["Present", "Work From Home", "Half Day"]],
				"docstatus": 1,
			},
			fields=["attendance_date", "working_hours"],
		)

		comp_off_type = _("Half Day") if self.half_day else _("Full Day")
		min_hours = flt(
			policy.minimum_hours_for_half_day_comp_off
			if self.half_day
			else policy.minimum_hours_for_full_day_comp_off
		)

		for record in attendance_records:
			working_hours = flt(record.working_hours)
			if working_hours < min_hours:
				frappe.throw(
					_(
						"{0} Compensatory Off requires a minimum of {1} working hour(s). "
						"Attendance on {2} shows {3} working hour(s)."
					).format(
						comp_off_type,
						frappe.bold(min_hours),
						frappe.bold(frappe.format(record.attendance_date, {"fieldtype": "Date"})),
						frappe.bold(working_hours),
					),
					title=_("Insufficient Working Hours"),
				)
