import frappe

def execute():
	"""
	Remove deprecated 'auto_create_supplier' and 'auto_create_item' fields
	from the tabSingles table for 'PW AI Settings'.
	These have been replaced by a unified 'auto_create_master_data' field.
	"""
	if not frappe.db.exists("DocType", "PW AI Settings"):
		return

	# Delete the old fields from the DB if they exist
	frappe.db.sql("""
		DELETE FROM `tabSingles`
		WHERE doctype = 'PW AI Settings'
		AND field IN ('auto_create_supplier', 'auto_create_item')
	""")

	# The new field 'auto_create_master_data' defaults to 0 (OFF),
	# which is the safe and intended default behavior, so no data
	# migration is needed from the old flags.
