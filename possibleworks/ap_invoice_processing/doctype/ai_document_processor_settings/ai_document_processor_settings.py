# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

import os

import frappe
from frappe.model.document import Document
from frappe.utils.password import get_decrypted_password

from possibleworks.ap_invoice_processing.constants import (
	ROLLOUT_DOCTYPES,
	SETTINGS_DOCTYPE,
)


class AIDocumentProcessorSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF
		from possibleworks.ap_invoice_processing.doctype.ai_document_processor_supported_doctype.ai_document_processor_supported_doctype import (
			AIDocumentProcessorSupportedDocType,
		)

		allowed_file_types: DF.SmallText
		enable_ai_invoice_processing: DF.Check
		max_file_size_mb: DF.Int
		openai_model: DF.Data | None
		supported_doctypes: DF.Table[AIDocumentProcessorSupportedDocType]
	# end: auto-generated types

	def validate(self):
		self.ensure_supported_doctypes_scope()
		self.validate_max_file_size()

	def ensure_supported_doctypes_scope(self):
		"""
		Keep supported_doctypes constrained to the current rollout set.

		UI rules prevent adding/removing rows, but we also enforce it server-side
		to avoid drift via API/imports.
		"""
		existing_enabled: dict[str, int] = {}
		for row in list(self.supported_doctypes or []):
			dt = (row.document_type or "").strip()
			if not dt:
				continue
			existing_enabled[dt] = 1 if row.enabled else 0

		self.set("supported_doctypes", [])
		for dt in ROLLOUT_DOCTYPES:
			self.append(
				"supported_doctypes",
				{"document_type": dt, "enabled": existing_enabled.get(dt, 1)},
			)

	def validate_max_file_size(self):
		if self.max_file_size_mb and self.max_file_size_mb <= 0:
			frappe.throw("Maximum File Size must be greater than 0 MB")

	@staticmethod
	def is_enabled():
		"""Check if AI Document Processing is enabled."""

		value = frappe.db.get_single_value(SETTINGS_DOCTYPE, "enable_ai_invoice_processing")
		if value is None:
			return True
		try:
			return bool(int(value))
		except Exception:
			return bool(value)

	@staticmethod
	def is_doctype_supported(doctype_name):
		"""Check if a given DocType is in the supported list and enabled."""
		if doctype_name not in ROLLOUT_DOCTYPES:
			return False

		settings = frappe.get_single(SETTINGS_DOCTYPE)
		rows = list(settings.supported_doctypes or [])
		if not rows:
			return True

		for row in rows:
			if row.document_type == doctype_name and row.enabled:
				return True
		return False

	@staticmethod
	def get_allowed_extensions():
		"""Return list of allowed file extensions (lowercase)."""
		settings = frappe.get_single(SETTINGS_DOCTYPE)
		if not settings.allowed_file_types:
			return ["pdf", "jpg", "png", "webp"]
		return [
			ext.strip().lower()
			for ext in settings.allowed_file_types.split("\n")
			if ext.strip()
		]

	@staticmethod
	def get_max_file_size_bytes():
		"""Return max file size in bytes."""
		mb = frappe.db.get_single_value(SETTINGS_DOCTYPE, "max_file_size_mb") or 10
		return int(mb) * 1024 * 1024

	@staticmethod
	def get_openai_config():
		"""
		Return OpenAI configuration with resilient API key lookup.

		API key is sourced from frappe.conf / env (preferred) with a fallback
		to the encrypted password store.
		"""

		settings = frappe.get_single(SETTINGS_DOCTYPE)

		api_key = (
			(frappe.conf or {}).get("openai_api_key")
			or os.environ.get("OPENAI_API_KEY")
		)

		if not api_key:
			api_key = AIDocumentProcessorSettings._get_api_key_for_doctype(SETTINGS_DOCTYPE)

		if not api_key:
			api_key = AIDocumentProcessorSettings._get_single_api_key_value(SETTINGS_DOCTYPE)

		return {
			"api_key": api_key,
			"model": settings.openai_model or "gpt-4.1-mini",
		}

	@staticmethod
	def _get_api_key_for_doctype(doctype_name):
		if not doctype_name or not frappe.db.exists("DocType", doctype_name):
			return None
		try:
			return get_decrypted_password(
				doctype_name,
				doctype_name,
				"openai_api_key",
				raise_exception=False,
			)
		except Exception:
			return None

	@staticmethod
	def _get_single_api_key_value(doctype_name):
		if not doctype_name or not frappe.db.exists("DocType", doctype_name):
			return None
		try:
			value = frappe.db.get_single_value(doctype_name, "openai_api_key")
		except Exception:
			return None
		if not value:
			return None
		if isinstance(value, str) and value and set(value) == {"*"}:
			return None
		return value


# Backward-compatible alias so any direct imports of APProcessorSettings still work.
APProcessorSettings = AIDocumentProcessorSettings
