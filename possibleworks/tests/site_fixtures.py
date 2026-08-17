# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Test fixtures that adapt to whatever the SITE has been customised to require.

A suite that hardcodes which Employee fields are mandatory does not test our code -- it
tests somebody's Customize Form session, and turns red the day HR adds a field. This
happened on `hw-hris`: two `reqd` probation-date Custom Fields on Employee took out both
the Form 16 suite and every onboarding submit test at once, none of which had changed.

So: read the live meta, mint a value from each docfield's own type, and let the suites
assert on their own behaviour instead.
"""

import frappe
from frappe.utils import today

# Filled by the framework, by a controller, or by the factory being helped -- supplying
# them here would either be overwritten or would mask the thing under test.
ALWAYS_SUPPLIED = frozenset(
	{
		"naming_series",
		"status",
		"employee_name",
		"first_name",
		"date_of_joining",
		"date_of_birth",
		"gender",
		"company",
	}
)


def sample_value_for(df) -> str | None:
	"""A plausible value for `df`, chosen by fieldtype.

	`df` may be a real DocField or the plain dict `pending_fields` serialises, so this
	only ever reads keys both provide. Returns None for anything that cannot be filled
	generically -- Table, Attach and friends -- which the caller should skip rather than
	guess at.
	"""
	get = df.get if isinstance(df, dict) else lambda key, default=None: getattr(df, key, default)

	fieldtype = get("fieldtype")
	options = (get("options") or "").strip()

	if fieldtype in ("Date", "Datetime"):
		return today()
	if fieldtype in ("Int", "Float", "Currency", "Percent", "Check"):
		return "1"
	if fieldtype == "Select":
		choices = [choice for choice in options.split("\n") if choice.strip()]
		return choices[0] if choices else None
	if fieldtype == "Link":
		return frappe.db.get_value(options, {}, "name") if options else None
	if fieldtype in ("Data", "Small Text", "Text", "Long Text", "Text Editor"):
		return "Test"
	return None


def site_mandatory_values(doctype: str, exclude=()) -> dict:
	"""Values for every field this site marks mandatory on `doctype`.

	Skips `ALWAYS_SUPPLIED` plus anything the caller names, and anything with a default
	(the framework will fill it) or a `mandatory_depends_on` (conditional, and guessing
	the condition is worse than leaving it).
	"""
	skip = ALWAYS_SUPPLIED | set(exclude)
	values = {}

	for df in frappe.get_meta(doctype).get("fields", {"reqd": 1}):
		if df.fieldname in skip or df.default or df.mandatory_depends_on:
			continue
		value = sample_value_for(df)
		if value is not None:
			values[df.fieldname] = value

	return values
