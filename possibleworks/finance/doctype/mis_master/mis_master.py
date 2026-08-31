# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class MISMaster(Document):
	"""The vehicle/laptop/desktop register a requisition's MIS field selects from.

	Deliberately minimal - just enough to identify one physical asset (entity +
	type + code) so it can be picked from a dropdown and tagged onto a request
	line. No fleet-management fields (insurance, depreciation, odometer) since
	nothing here asked for that.
	"""
