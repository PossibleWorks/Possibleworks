import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.workflow import get_workflow_name, get_workflow_state_field
from frappe.utils.pdf import pdf_body_html as frappe_pdf_body_html

# Table MultiSelect on Print Settings (fixtures/custom_field.json). Its rows reuse core's
# "User Select Document Type" child table, which Frappe only ever reads through its parent.
DOCTYPES_FIELD = "custom_computer_generated_line_doctypes"
APPROVED_STATE = "Approved"


def pdf_body_html(template, args, **kwargs):
	html = frappe_pdf_body_html(template, args, **kwargs)
	doc = args.get("doc")
	if doc and show_computer_generated_line(doc):
		html += (
			'<div class="computer-generated-line" style="text-align: center; font-size: 9px; margin-top: 8px;">'
			+ _("This is a computer-generated document and does not require a signature.")
			+ "</div>"
		)
	return html


def show_computer_generated_line(doc: Document) -> bool:
	"""Submitted, selected in Print Settings, and Approved when the DocType has an active Workflow."""
	if doc.docstatus != 1:
		return False

	# `or []`: a site that has the hook but not yet the migrated field shows no line
	selected = frappe.get_cached_doc("Print Settings").get(DOCTYPES_FIELD) or []
	if doc.doctype not in {row.document_type for row in selected}:
		return False

	workflow = get_workflow_name(doc.doctype)
	if not workflow:
		return True
	return doc.get(get_workflow_state_field(workflow)) == APPROVED_STATE
