# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class PWAISettings(Document):
	"""Single DocType for AI-related configuration (OpenAI key, model, toggles)."""

	def validate(self):
		# Only validate if this looks like a new plaintext key being entered
		# (encrypted passwords come back as None or masked when accessed via .openai_api_key)
		# Use get_password to check if a key is actually set; don't validate the encrypted blob
		key = self.openai_api_key
		if key and not key.startswith("sk-"):
			# Could be an encrypted/masked value — only warn if it looks like plaintext
			if not set(key) <= {"*"}:
				frappe.msgprint(
					frappe._("Tip: OpenAI API keys typically start with 'sk-'. Please verify your key is correct."),
					indicator="orange",
					alert=True,
				)
