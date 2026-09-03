# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Create the fallback owner role for exit checklist activities.

Created in code, per site, via migrate -- not left to a manual step and not shipped as
a fixture (fixtures sync into every installed site for every app, so anything put
there is global by accident as much as by design).

It has to exist before the first resignation is approved. Every row of the default
separation template carries this role as its floor, because a separation is submitted
unattended by the scheduler: an activity with no `user` and no `role` produces a Task
assigned to nobody, an empty `Task._assign`, and therefore no PossibleWorks tile --
with the checklist still looking perfectly healthy in Frappe.

No DocPerm is granted here. Holders work the Tasks the checklist creates, and Task
permissions are the site's own business.
"""

import frappe

from possibleworks.offboarding.separation import ensure_exit_coordinator_role


def execute():
	name = ensure_exit_coordinator_role()
	frappe.logger().info(f"possibleworks: Role {name} is present")
