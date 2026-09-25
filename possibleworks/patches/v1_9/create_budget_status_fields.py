# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Add a budget-lifecycle status to Purchase Indent, and the reverse link that
lets a Purchase Invoice trace back to the indent it ultimately came from.

`budget_status` tracks a SEPARATE lifecycle from the existing `status` field
(which tracks order-progress: Pending/Partially Ordered/Ordered/...). It moves:

  Exceeds Budget -> Committed -> Approved -> Completed

- "Exceeds Budget" / "Committed": set by the frontend at Indent submit time,
  after running the same check_budget dry-run already used for Payment Entry
  and Journal Entry -- and again by resolveErpWorkflowAction-adjacent backend
  code once a pending budget-increase request is approved (see
  resolveBudgetIncreaseRequest in pw-server-v3).
- "Approved": set by update_from_purchase_order (purchase_indent_status.py) the
  moment a Purchase Order referencing this indent is submitted -- piggybacks
  on the hook that already recomputes ordered_qty/status from the same event.
- "Completed": set by the new hook in this patch's sibling module,
  purchase_indent_budget_status.py, on Purchase Invoice submit.

Purchase Invoice Item never got the `purchase_indent`/`purchase_indent_item`
link fields that Purchase Order Item, RFQ Item and Supplier Quotation Item
already carry (patches/v1_6) -- added here so the Invoice-submit hook has
something to trace back through. Not folded into v1_6 itself: patches are
historical records, never edited after the fact.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Purchase Indent": [
				{
					"fieldname": "budget_status",
					"label": "Budget Status",
					"fieldtype": "Select",
					"options": "\nExceeds Budget\nCommitted\nApproved\nCompleted",
					"insert_after": "status",
					"read_only": 1,
					"allow_on_submit": 1,
					"print_hide": 1,
					"in_standard_filter": 1,
				},
			],
			"Purchase Invoice Item": [
				{
					"fieldname": "purchase_indent",
					"label": "Purchase Indent",
					"fieldtype": "Link",
					"options": "Purchase Indent",
					"insert_after": "purchase_order",
					"read_only": 1,
					"print_hide": 1,
					"search_index": 1,
				},
				{
					"fieldname": "purchase_indent_item",
					"label": "Purchase Indent Item",
					"fieldtype": "Data",
					"insert_after": "purchase_indent",
					"read_only": 1,
					"hidden": 1,
					"print_hide": 1,
					"search_index": 1,
				},
			],
		},
		update=True,
	)
