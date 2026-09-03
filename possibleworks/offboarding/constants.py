# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Shared constants for the Offboarding module.

Kept free of `frappe` imports so it can be imported from patches and tests without
pulling in a request context -- same rule as `onboarding/constants.py`.
"""

SEPARATION_DOCTYPE = "Employee Separation"
SEPARATION_TEMPLATE_DOCTYPE = "Employee Separation Template"

# --------------------------------------------------------------------------- #
# The notice window
# --------------------------------------------------------------------------- #
# `boarding_begins_on` is DAY ZERO for every task date. `get_task_dates`
# (hrms/controllers/employee_boarding_controller.py:114) counts FORWARD from it:
#
#     start_date = add_days(self.boarding_begins_on, activity.begin_on)
#
# Onboarding anchors day zero to the joining date, which is right -- the person
# arrives, then work happens. An exit is the mirror image: anchoring to the last
# working day would schedule every task AFTER the person has gone (asset return two
# days after they hand back the laptop, access revoked two days after they leave).
#
# So day zero is a fixed window BEFORE the last working day, and the activities below
# are written as offsets toward it. That holds regardless of whether the notice period
# is 30, 60 or 90 days -- which anchoring to the approval date would not.
NOTICE_WINDOW_DAYS = 7

# --------------------------------------------------------------------------- #
# Ownership
# --------------------------------------------------------------------------- #
# Unlike the onboarding checklist -- which HR opens, assigns and submits by hand --
# a separation is submitted unattended by the pw-cron-jobs-v2 scheduler. Nobody is in
# the loop to set owners first.
#
# That matters because tiles are created per entry in `Task._assign`, and Frappe only
# fills `_assign` from an activity's `user` or `role`
# (employee_boarding_controller.py:76-93). An activity with neither produces a Task
# assigned to nobody, an empty `_assign`, and therefore NO tile -- silently, with the
# checklist looking perfectly healthy in Frappe.
#
# So every template row carries this role as its floor. The role is created in code
# (patches/v1_6) rather than per site, so a fresh site is never one manual step away
# from a checklist that quietly notifies no one.
EXIT_COORDINATOR_ROLE = "Employee Exit Coordinator"

# Activities that belong to the leaver's own reporting manager rather than to HR,
# matched by `activity_name` when the record is built.
#
# Matched by name because the template is site-owned once created (see
# `ensure_default_separation_template`), so there is no stable row id to key on. A
# renamed activity simply fails to match and keeps the role -- it degrades to "the
# exit coordinators get it", never to "nobody gets it".
MANAGER_OWNED_ACTIVITIES = frozenset({
	"Knowledge transfer and handover documentation",
})

# --------------------------------------------------------------------------- #
# Default template
# --------------------------------------------------------------------------- #
# Matched by `title`, NOT by name: `Employee Separation Template.autoname` is
# `HR-EMP-STP-.#####`, so the title is neither the record name nor unique. Same
# reasoning as DEFAULT_BOARDING_TEMPLATE_TITLE.
DEFAULT_SEPARATION_TEMPLATE_TITLE = "Default Employee Separation"

# `begin_on` is days after day zero, and day zero is NOTICE_WINDOW_DAYS before the
# last working day. So with the default window of 7:
#
#     begin_on 0  -> a week before the last working day
#     begin_on 7  -> the last working day itself
#     begin_on 8+ -> after the person has left, which is correct for settlement
#
# Every row sets `begin_on` explicitly. A blank one makes `get_task_dates` return
# [None, None] -- the Task is still created and still assigned, but with no expected
# start or end, so it never surfaces in any date-driven view.
DEFAULT_SEPARATION_ACTIVITIES = (
	{
		"activity_name": "Exit interview",
		"description": "Capture the reason for leaving and any feedback worth acting on.",
		"begin_on": 0,
		"duration": 2,
	},
	{
		"activity_name": "Knowledge transfer and handover documentation",
		"description": "Hand over live work, credentials to shared systems, and anything only this person knows.",
		"begin_on": 0,
		"duration": 5,
	},
	{
		"activity_name": "Asset return - laptop, ID card and access cards",
		"description": "Collect company property and record its condition.",
		"begin_on": 5,
		"duration": 2,
	},
	{
		"activity_name": "Revoke system and application access",
		"description": "Disable accounts and remove access to every tool the role carried.",
		"begin_on": 7,
		"duration": 0,
	},
	{
		"activity_name": "Full and final settlement clearance",
		"description": "Clear dues, settle leave encashment, and issue the relieving paperwork.",
		"begin_on": 7,
		"duration": 10,
	},
)

# --------------------------------------------------------------------------- #
# Custom fields (added by patches/v1_6)
# --------------------------------------------------------------------------- #
# The last working day the approving manager set in PossibleWorks.
#
# Stored rather than derived, for two reasons. `boarding_begins_on` is clamped to today
# for a short or already-elapsed notice period, so `boarding_begins_on + 7` is not
# reliably the last working day. And the exit tile has to tell the assignee when the
# person actually leaves -- inferring it from a task's start date would be guessing.
LAST_WORKING_DAY_FIELD = "last_working_day"

# Provenance marker.
#
# The scheduler in pw-cron-jobs-v2 submits drafts whose `boarding_begins_on` has
# arrived. Without a marker its only filter would be `docstatus = 0`, which would also
# sweep up a draft HR created by hand and is still halfway through editing -- and
# submitting a separation is irreversible in practice: it mints a Project and a Task
# per row, and cancelling deletes both.
SOURCE_FIELD = "created_from_possibleworks"
