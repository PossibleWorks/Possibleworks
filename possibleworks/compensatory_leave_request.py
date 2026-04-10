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
			# Run standard HRMS validation but intercept specific error messages
			# and replace them with user-friendly custom messages.
			# This avoids editing HRMS source code directly.
			try:
				super().validate()
			except frappe.ValidationError as e:
				msg = str(e)
				# HRMS overlap error → custom message
				if "A Compensatory Leave Request exists between" in msg:
					frappe.message_log.pop()
					frappe.throw(
						_("Compensatory leave request already exists on {0}.").format(
							frappe.bold(frappe.format(self.work_from_date, {"fieldtype": "Date"}))
						)
					)
				raise
			return

		# Always skip HRMS status-based attendance check when custom validation is enabled
		self._skip_attendance_validation = True

		super().validate()

		# Only check working hours when attendance records were found
		if self._submitted_attendance_exists():
			self._validate_working_hours(policy)

	# --- override points ------------------------------------------------------
	# The methods below override HRMS methods solely to customize error messages.
	# The original HRMS logic is preserved by calling super() — only the thrown
	# message is replaced. frappe.message_log.pop() removes the original HRMS
	# message before raising ours so both don't appear together.

	def validate_attendance(self):
		if getattr(self, "_skip_attendance_validation", False):
			return
		try:
			super().validate_attendance()
		except frappe.ValidationError as e:
			msg = str(e)
			# HRMS: "You were only present for Half Day..." → custom message
			if "only present for Half Day" in msg:
				frappe.message_log.pop()
				frappe.throw(
					_("You cannot apply full day compensatory leave request because attendance is marked as Half Day.")
				)
			# HRMS: "You are not present all day(s)..." → custom message
			elif "not present all day" in msg:
				frappe.message_log.pop()
				frappe.throw(
					_("Compensatory leave request is not applicable for the selected date(s) due to no attendance record.")
				)
			raise

	def validate_holidays(self):
		try:
			super().validate_holidays()
		except frappe.ValidationError as e:
			msg = str(e)
			# HRMS: "is not a holiday" / "not valid holidays" → custom message
			if "not a holiday" in msg or "not valid holidays" in msg:
				from frappe.utils import date_diff
				frappe.message_log.pop()
				if date_diff(self.work_end_date, self.work_from_date):
					frappe.throw(
						_("You cannot apply for compensatory leave request as {0} to {1} are not holidays").format(
							frappe.bold(frappe.format(self.work_from_date, {"fieldtype": "Date"})),
							frappe.bold(frappe.format(self.work_end_date, {"fieldtype": "Date"})),
						)
					)
				else:
					frappe.throw(
						_("You cannot apply for compensatory leave request as {0} is not a holiday").format(
							frappe.bold(frappe.format(self.work_from_date, {"fieldtype": "Date"}))
						)
					)
			raise

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
