# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Indian statutory identifier validators.

Nothing of this kind exists anywhere in frappe, erpnext or hrms -- `pan_number`,
`ifsc_code`, `micr_code` and `provident_fund_account` are all plain `Data` custom
fields created by `hrms/regional/india/setup.py` with no validation at all -- so
these are written here.

Modelled on `frappe.utils.validate_phone_number`: a compiled module-level pattern,
a `(value, throw=False) -> bool` signature, and a dedicated `frappe.ValidationError`
subclass so callers (and the external onboarding app) can branch on the error type.

PAN is intentionally NOT format-validated -- see `normalise_pan`.
"""

import mimetypes
import re

import frappe
from frappe import _


class InvalidAadhaarError(frappe.ValidationError):
	pass


class InvalidIFSCError(frappe.ValidationError):
	pass


class InvalidFileExtensionError(frappe.ValidationError):
	pass


# 12 digits; UIDAI reserves a leading 0 and 1, so the first digit is always 2-9.
AADHAAR_PATTERN = re.compile(r"^[2-9]\d{11}$")

# 4-letter bank code, a mandatory '0' in position 5, then a 6-char branch code.
IFSC_PATTERN = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")

_NON_ALNUM = re.compile(r"[^A-Za-z0-9]")
_NON_DIGIT = re.compile(r"\D")


def normalise_code(value: str | None) -> str:
	"""Upper-case and strip separators. What people paste is full of spaces/hyphens."""
	if not value:
		return ""
	return _NON_ALNUM.sub("", str(value)).upper()


def normalise_digits(value: str | None) -> str:
	"""Keep digits only -- for Aadhaar, which is commonly written as 1234 5678 9012."""
	if not value:
		return ""
	return _NON_DIGIT.sub("", str(value))


def normalise_pan(pan: str | None) -> str:
	"""Tidy a PAN without validating its format.

	Format validation is deliberately not performed: the 4th character encodes the
	holder type against a list that is documented practice rather than anything we
	can verify, and a wrong list silently rejects a genuine PAN with no override.
	Storage is normalised so lookups and comparisons stay consistent.
	"""
	return normalise_code(pan)


# --------------------------------------------------------------------------- #
# Verhoeff checksum (the dihedral-group D5 scheme UIDAI uses for Aadhaar)
# --------------------------------------------------------------------------- #

# D5 multiplication table.
_D = (
	(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
	(1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
	(2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
	(3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
	(4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
	(5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
	(6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
	(7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
	(8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
	(9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

# Permutation table, applied cyclically by digit position.
_P = (
	(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
	(1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
	(5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
	(8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
	(9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
	(4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
	(2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
	(7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

# Multiplicative inverse table for D5.
_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def verhoeff_checksum(number: str) -> int:
	"""Return the Verhoeff checksum of a digit string. 0 means the number is valid."""
	check = 0
	for position, digit in enumerate(reversed(number)):
		check = _D[check][_P[position % 8][int(digit)]]
	return check


def verhoeff_check_digit(payload: str) -> int:
	"""Return the digit that makes `payload` a valid Verhoeff number.

	Used by tests and fixtures to mint syntactically valid Aadhaar numbers rather
	than hardcoding real ones.
	"""
	check = 0
	for position, digit in enumerate(reversed(payload)):
		check = _D[check][_P[(position + 1) % 8][int(digit)]]
	return _INV[check]


# --------------------------------------------------------------------------- #
# Public validators
# --------------------------------------------------------------------------- #


def validate_aadhaar(aadhaar: str | None, throw: bool = False) -> bool:
	"""Return True if `aadhaar` is 12 digits and passes the UIDAI Verhoeff checksum."""
	value = normalise_digits(aadhaar)
	if not value:
		return False

	valid = bool(AADHAAR_PATTERN.match(value)) and verhoeff_checksum(value) == 0
	if not valid and throw:
		frappe.throw(
			_("{0} is not a valid Aadhaar number. It must be 12 digits and pass the UIDAI checksum.").format(
				frappe.bold(aadhaar)
			),
			InvalidAadhaarError,
			title=_("Invalid Aadhaar Number"),
		)
	return valid


def canonical_file_type(extension: str) -> str | None:
	"""Frappe's own name for this extension, or None if it isn't a real file type.

	Mirrors `File.set_file_type` (frappe/core/doctype/file/file.py:368) exactly --
	guess the MIME type, then map back to a canonical extension -- so a ".jpeg" here
	is compared as "JPG", the same way an uploaded file would be.
	"""
	mime_type = mimetypes.guess_type(f"x.{extension}")[0]
	if not mime_type:
		return None

	canonical = mimetypes.guess_extension(mime_type)
	return canonical.lstrip(".").upper() if canonical else extension.upper()


def normalise_extension_list(value: str | None, throw: bool = False) -> str:
	"""Tidy a comma-separated extension list and reject anything that isn't real.

	A plain format check is not enough here: "j" is perfectly valid as text but is not
	a file extension, so this resolves each entry through `mimetypes` -- the same table
	Frappe uses to classify uploads. That keeps the rule self-maintaining instead of
	depending on a hardcoded list that rots.

	Also refuses extensions the SITE blocks (`System Settings.allowed_file_extensions`,
	enforced in `File.validate_file_extension`), since requiring a file type that can
	never be uploaded is a rule nobody can satisfy.
	"""
	if not value:
		return ""

	parts = [
		part.strip().lstrip(".").lower() for part in str(value).replace("\n", ",").split(",")
	]
	extensions = list(dict.fromkeys(part for part in parts if part))
	if not extensions:
		return ""

	unknown = [ext for ext in extensions if not canonical_file_type(ext)]
	if unknown and throw:
		frappe.throw(
			_("{0} is not a recognised file extension. Use values like pdf, jpg, png or docx.").format(
				", ".join(frappe.bold(ext) for ext in unknown)
			),
			InvalidFileExtensionError,
			title=_("Invalid File Extension"),
		)

	site_allowed = frappe.get_system_settings("allowed_file_extensions")
	if site_allowed and throw:
		permitted = {line.strip().upper() for line in site_allowed.splitlines() if line.strip()}
		blocked = [
			ext
			for ext in extensions
			if canonical_file_type(ext) and canonical_file_type(ext) not in permitted
		]
		if blocked:
			frappe.throw(
				_("{0} cannot be uploaded on this site. System Settings allows only: {1}").format(
					", ".join(frappe.bold(ext) for ext in blocked), ", ".join(sorted(permitted))
				),
				InvalidFileExtensionError,
				title=_("Blocked By System Settings"),
			)

	return ",".join(extensions)


def validate_ifsc(ifsc: str | None, throw: bool = False) -> bool:
	"""Return True if `ifsc` is a structurally valid IFSC code.

	Worth validating even though PAN is not: a wrong IFSC means a salary payment
	that silently fails at the bank, and the format is RBI-mandated and stable.
	"""
	value = normalise_code(ifsc)
	if not value:
		return False

	valid = bool(IFSC_PATTERN.match(value))
	if not valid and throw:
		frappe.throw(
			_("{0} is not a valid IFSC code. Expected 11 characters, for example HDFC0001234.").format(
				frappe.bold(ifsc)
			),
			InvalidIFSCError,
			title=_("Invalid IFSC Code"),
		)
	return valid
