import frappe
from frappe.utils import flt, getdate


def _resolve_rate_row(category_doc, posting_date):
	for row in category_doc.rates:
		from_date = getdate(row.from_date) if row.from_date else None
		to_date = getdate(row.to_date) if row.to_date else None
		if (not from_date or posting_date >= from_date) and (not to_date or posting_date <= to_date):
			return row
	return None


def _resolve_account(category_doc, company):
	for row in category_doc.accounts:
		if row.company == company:
			return row.account
	return None


def _cumulative_amount_so_far(party_type, party, company, category_name, rate_row):
	if not rate_row.from_date or not rate_row.to_date:
		return 0

	total = frappe.db.sql(
		"""
		SELECT SUM(taxable_amount)
		FROM `tabTax Withholding Entry`
		WHERE party_type = %s
			AND party = %s
			AND company = %s
			AND tax_withholding_category = %s
			AND docstatus = 1
			AND taxable_date BETWEEN %s AND %s
		""",
		(party_type, party, company, category_name, rate_row.from_date, rate_row.to_date),
	)[0][0]

	return flt(total)


def _preview_for_category(company, party_type, party, posting_date, taxable_amount, category_name):
	category = frappe.get_cached_doc("Tax Withholding Category", category_name)

	rate_row = _resolve_rate_row(category, posting_date)
	if not rate_row:
		return {"applicable": False, "message": "No TDS rate configured for this date"}

	account = _resolve_account(category, company)
	if not account:
		return {"applicable": False, "message": "No TDS account configured for this company"}

	single_threshold = flt(rate_row.single_threshold)
	cumulative_threshold = flt(rate_row.cumulative_threshold)
	no_thresholds_set = not single_threshold and not cumulative_threshold

	cumulative_so_far = 0
	if not category.disable_cumulative_threshold and cumulative_threshold:
		cumulative_so_far = _cumulative_amount_so_far(
			party_type, party, company, category_name, rate_row
		)

	single_crossed = (
		not category.disable_transaction_threshold
		and single_threshold
		and taxable_amount >= single_threshold
	)
	cumulative_crossed = (
		not category.disable_cumulative_threshold
		and cumulative_threshold
		and (cumulative_so_far + taxable_amount) >= cumulative_threshold
	)
	threshold_crossed = no_thresholds_set or single_crossed or cumulative_crossed

	if not threshold_crossed:
		return {
			"applicable": True,
			"threshold_crossed": False,
			"category": category_name,
			"account": account,
			"rate": flt(rate_row.tax_withholding_rate),
			"tax_amount": 0,
			"message": "Threshold not yet crossed - TDS is configured but will not apply to this transaction based on current data.",
		}

	taxable_for_tax = taxable_amount
	if category.tax_on_excess_amount and cumulative_threshold:
		taxable_for_tax = max(0, (cumulative_so_far + taxable_amount) - cumulative_threshold)

	tax_amount = taxable_for_tax * flt(rate_row.tax_withholding_rate) / 100
	if category.round_off_tax_amount:
		tax_amount = round(tax_amount)

	return {
		"applicable": True,
		"threshold_crossed": True,
		"category": category_name,
		"account": account,
		"rate": flt(rate_row.tax_withholding_rate),
		"tax_amount": flt(tax_amount),
	}


# =============================================================================
# WHITELISTED API
# =============================================================================

@frappe.whitelist()
def preview_tds(company, party_type, party, posting_date, taxable_amount):
	"""
	Dry-run TDS preview for a party (e.g. Supplier) - never throws, only
	reports. Call this while the form is still being filled in, before any
	save. Returns {"applicable": False} if the party has no Tax Withholding
	Category/Group configured, or on any lookup failure - a broken preview
	should never block data entry.
	"""
	try:
		taxable_amount = flt(taxable_amount)
		posting_date = getdate(posting_date)

		category_name, group_name = frappe.get_cached_value(
			party_type, party, ["tax_withholding_category", "tax_withholding_group"]
		)

		if not category_name:
			if group_name:
				return {
					"applicable": False,
					"message": "This party uses a Tax Withholding Group - preview not supported, the amount will still be applied correctly on save.",
				}
			return {"applicable": False}

		if taxable_amount <= 0:
			return {"applicable": False}

		return _preview_for_category(
			company, party_type, party, posting_date, taxable_amount, category_name
		)
	except Exception:
		frappe.log_error(title="TDS preview failed")
		return {"applicable": False}


@frappe.whitelist()
def preview_tds_on_party_select(
	company, party_type, party, posting_date, taxable_amount, bill_date=None
):
	"""
	Combines due-date resolution (delegates to ERPNext's own get_due_date - not
	reimplemented here) with the TDS preview above, in one round trip. Used by
	the `supplier` field's trigger so picking a supplier shows the estimate
	immediately, regardless of whether item amounts were entered before or
	after the supplier was chosen.
	"""
	due_date = None
	try:
		from erpnext.accounts.party import get_due_date

		due_date = get_due_date(posting_date, party_type, party, company, bill_date)
	except Exception:
		frappe.log_error(title="get_due_date failed during TDS preview")

	return {
		"due_date": due_date,
		"tds": preview_tds(company, party_type, party, posting_date, taxable_amount),
	}
