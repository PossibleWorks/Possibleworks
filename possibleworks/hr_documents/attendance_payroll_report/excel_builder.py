# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Builds the attendance-exception workbook for attendance_payroll_report.py.

Kept separate from the data-fetching/email side so the sheet layout can be
tested (row dicts in, xlsx bytes out) without touching the database.

The Date cell is the ONLY cell that gets a fill color, and only when the row
is `is_holiday` -- Status is plain text (no red/orange), so a holiday is never
masked or duplicated by a status color. That is the one signal payroll needs:
a colored date means "this was a holiday on this employee's calendar, do not
treat it as an unapproved Absent/Half Day."
"""

import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

HEADER_FILL = PatternFill(start_color="F5F7FB", end_color="F5F7FB", fill_type="solid")
HOLIDAY_FILL = PatternFill(start_color="CFE2FF", end_color="CFE2FF", fill_type="solid")

# (row dict key, column header). Order here is the order columns are written.
COLUMNS = [
	("employee", "Employee ID"),
	("employee_name", "Employee Name"),
	("department", "Department"),
	("date", "Date"),
	("day_name", "Day"),
	("status", "Status"),
	("working_hours", "Working Hours"),
	("holiday", "Holiday"),
	("pending_request", "Pending Request"),
	("shift", "Shift"),
	("checkins", "Check-in / Check-out"),
	("offshift_checkins", "Off-shift Punches"),
]

DATE_COLUMN_INDEX = [key for key, _ in COLUMNS].index("date") + 1


def build_attendance_exception_workbook(rows):
	"""rows: list of dicts, one per Absent/Half-Day day per employee, keyed by
	COLUMNS' fieldnames, plus an `is_holiday` bool that drives the Date cell fill.

	Returns xlsx bytes.
	"""
	wb = Workbook()
	ws = wb.active
	ws.title = "Attendance Exceptions"

	ws.append([label for _, label in COLUMNS])
	for cell in ws[1]:
		cell.font = Font(bold=True)
		cell.fill = HEADER_FILL

	for row in rows:
		ws.append([row.get(key, "") for key, _ in COLUMNS])
		if row.get("is_holiday"):
			ws.cell(row=ws.max_row, column=DATE_COLUMN_INDEX).fill = HOLIDAY_FILL

	for idx, (_key, label) in enumerate(COLUMNS, start=1):
		width = max(12, min(40, len(label) + 6))
		ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

	ws.freeze_panes = "A2"

	buffer = io.BytesIO()
	wb.save(buffer)
	return buffer.getvalue()
