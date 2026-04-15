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
		# TEST LOG: To verify if this new code version is being executed
		frappe.logger().warning("🔥 CUSTOM COMP OFF VALIDATION CODE RUNNING - NEW VERSION DEPLOYED")
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

		# Custom validation: explicitly call HRMS validations we need,
		# skip validate_attendance() and use working hours check instead
		from hrms.hr.utils import validate_active_employee, validate_dates, validate_overlap
		from frappe.utils import getdate

		validate_active_employee(self.employee)
		validate_dates(self, self.work_from_date, self.work_end_date)

		# Half-day validation
		if self.half_day:
			if not self.half_day_date:
				frappe.throw(_("Half Day Date is mandatory"))
			if not getdate(self.work_from_date) <= getdate(self.half_day_date) <= getdate(self.work_end_date):
				frappe.throw(_("Half Day Date should be in between Work From Date and Work End Date"))

		validate_overlap(self, self.work_from_date, self.work_end_date)
		self.validate_holidays()

		# SKIP HRMS's validate_attendance() — use custom working hours validation instead
		if self._submitted_attendance_exists():
			self._validate_working_hours(policy)

		# Leave type validation
		if not self.leave_type:
			frappe.throw(_("Leave Type is mandatory"))

	# --- override points ------------------------------------------------------
	# The methods below override HRMS methods solely to customize error messages.
	# Used only when custom validation is disabled.

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
		Check if attendance records exist using the same filters as HRMS.
		This determines whether to run working hours validation.
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
