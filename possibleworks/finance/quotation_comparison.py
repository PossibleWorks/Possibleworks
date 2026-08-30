import frappe
from frappe.utils import add_days, flt, today

# =============================================================================
# RFQ -> Supplier Quotation AI recommendation.
#
# Read-only over RFQ/Supplier Quotation data (plus one dedup flag written back
# onto the RFQ). pw-server-v3's own daily cron calls these - Frappe never
# wakes itself up for this, it only answers when asked, same as the guest
# quotation portal.
# =============================================================================


@frappe.whitelist()
def get_due_tomorrow_rfqs():
	"""RFQs due tomorrow, submitted, not yet processed, with at least one
	submitted Supplier Quotation against them. Never throws - a bad candidate
	is simply skipped rather than failing the whole daily run."""
	tomorrow = add_days(today(), 1)

	candidates = frappe.get_all(
		"Request for Quotation",
		filters={
			"docstatus": 1,
			"schedule_date": tomorrow,
			"pw_recommendation_sent": ["!=", 1],
		},
		pluck="name",
	)

	qualifying = []
	for rfq in candidates:
		try:
			has_quotation = frappe.db.exists(
				"Supplier Quotation Item",
				{"request_for_quotation": rfq, "docstatus": 1},
			)
			if has_quotation:
				qualifying.append(rfq)
		except Exception:
			frappe.log_error(title=f"get_due_tomorrow_rfqs: failed checking {rfq}")

	return {"rfqs": qualifying}


def _vendor_history(supplier):
	scorecard_score = frappe.db.get_value("Supplier Scorecard", supplier, "supplier_score")

	purchase_invoice_count = frappe.db.count(
		"Purchase Invoice", {"supplier": supplier, "docstatus": 1}
	)
	purchase_invoice_total = flt(
		frappe.db.sql(
			"SELECT SUM(grand_total) FROM `tabPurchase Invoice` WHERE supplier=%s AND docstatus=1",
			(supplier,),
		)[0][0]
		or 0
	)

	flags = frappe.db.get_value(
		"Supplier", supplier, ["on_hold", "disabled", "warn_rfqs", "prevent_rfqs"], as_dict=True
	)

	return {
		"scorecard_score": scorecard_score,
		"purchase_invoice_count": purchase_invoice_count,
		"purchase_invoice_total": purchase_invoice_total,
		**(flags or {}),
	}


@frappe.whitelist()
def get_quotation_comparison_data(rfq):
	"""Everything needed to judge the submitted Supplier Quotations against
	one RFQ: per-item rate/qty/delivery, the free-text terms, and a
	vendor-history proxy per supplier."""
	rfq_doc = frappe.get_doc("Request for Quotation", rfq)

	sq_names = frappe.get_all(
		"Supplier Quotation Item",
		filters={"request_for_quotation": rfq, "docstatus": 1},
		pluck="parent",
		distinct=True,
	)

	quotations = []
	for sq_name in sq_names:
		sq = frappe.get_doc("Supplier Quotation", sq_name)
		quotations.append(
			{
				"supplier_quotation": sq.name,
				"supplier": sq.supplier,
				"grand_total": sq.grand_total,
				"currency": sq.currency,
				"terms": sq.terms,
				"items": [
					{
						"item_code": item.item_code,
						"qty": item.qty,
						"rate": item.rate,
						"amount": item.amount,
						"lead_time_days": item.lead_time_days,
						"expected_delivery_date": item.expected_delivery_date,
					}
					for item in sq.items
				],
				"vendor_history": _vendor_history(sq.supplier),
			}
		)

	return {
		"rfq": rfq_doc.name,
		"company": rfq_doc.company,
		"schedule_date": rfq_doc.schedule_date,
		"quotations": quotations,
	}


@frappe.whitelist()
def mark_recommendation_sent(rfq):
	frappe.db.set_value("Request for Quotation", rfq, "pw_recommendation_sent", 1)
