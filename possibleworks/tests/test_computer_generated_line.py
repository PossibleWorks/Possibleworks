# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Tests for possibleworks.utils.computer_generated_line: the "computer generated"
line is added only for DocTypes selected in Print Settings, only once submitted,
and only in the Approved state when the DocType has an active Workflow.

Workflow lookups are patched so the tests don't depend on which Workflows the
test site happens to have. Print Settings is saved for real (rolled back with the
class), so its cached copy is cleared after each test to avoid leaking rows.
"""

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from possibleworks.utils.computer_generated_line import (
	DOCTYPES_FIELD,
	pdf_body_html,
	show_computer_generated_line,
)

LINE = "This is a computer-generated document and does not require a signature."
MODULE = "possibleworks.utils.computer_generated_line"


def _doc(doctype, docstatus, workflow_state=None):
	return frappe.get_doc({"doctype": doctype, "docstatus": docstatus, "workflow_state": workflow_state})


def _patch_workflow(name):
	return patch(f"{MODULE}.get_workflow_name", return_value=name)


class TestComputerGeneratedLine(IntegrationTestCase):
	def setUp(self):
		settings = frappe.get_single("Print Settings")
		settings.set(DOCTYPES_FIELD, [{"document_type": "Purchase Order"}, {"document_type": "Stock Entry"}])
		settings.save()
		self.addCleanup(frappe.clear_document_cache, "Print Settings", "Print Settings")

	def test_doctype_not_selected(self):
		with _patch_workflow(""):
			self.assertFalse(show_computer_generated_line(_doc("Payment Entry", 1)))

	def test_without_workflow_needs_submit(self):
		with _patch_workflow(""):
			self.assertTrue(show_computer_generated_line(_doc("Stock Entry", 1)))
			self.assertFalse(show_computer_generated_line(_doc("Stock Entry", 0)))
			self.assertFalse(show_computer_generated_line(_doc("Stock Entry", 2)))

	def test_with_workflow_needs_submit_and_approved(self):
		with (
			_patch_workflow("PO Approval"),
			patch(f"{MODULE}.get_workflow_state_field", return_value="workflow_state"),
		):
			self.assertTrue(show_computer_generated_line(_doc("Purchase Order", 1, "Approved")))
			self.assertFalse(show_computer_generated_line(_doc("Purchase Order", 0, "Rejected")))
			self.assertFalse(show_computer_generated_line(_doc("Purchase Order", 0, "Pending Approval L1")))
			# submitted before the Workflow was activated: no Approved state, so no line
			self.assertFalse(show_computer_generated_line(_doc("Purchase Order", 1, None)))

	def test_hook_appends_line_only_when_eligible(self):
		template = frappe.get_jenv().from_string("<p>{{ doc.doctype }}</p>")
		with _patch_workflow(""):
			submitted = pdf_body_html(template, {"doc": _doc("Stock Entry", 1)})
			draft = pdf_body_html(template, {"doc": _doc("Stock Entry", 0)})

		self.assertTrue(submitted.startswith("<p>Stock Entry</p>"))
		self.assertIn(LINE, submitted)
		self.assertEqual(draft, "<p>Stock Entry</p>")
