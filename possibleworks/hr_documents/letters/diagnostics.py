# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Is this environment able to turn a letter into a PDF?

Worth having as a command rather than a wiki note, because the failure is
environmental: the same code produces letters on one host and `OSError: No
wkhtmltopdf executable found` on another, and the only difference is a binary on
`PATH`. Frappe offers no setting for that path -- `frappe.utils.pdf.get_pdf` calls
`pdfkit.from_string(...)` with no `configuration=`, so pdfkit runs `which wkhtmltopdf`
against the PATH of whichever process is rendering. Which means the answer differs
between the web process and a background worker if their environments differ.

    bench --site <site> execute possibleworks.hr_documents.letters.diagnostics.check
"""

import os
import shutil
import subprocess

import frappe

from possibleworks.hr_documents.doctype.employee_letter_template.employee_letter_template import (
	PDF_GENERATOR,
)


def _wkhtmltopdf_report() -> list[str]:
	path = shutil.which("wkhtmltopdf")
	if not path:
		return [
			"  XX  wkhtmltopdf   NOT FOUND on PATH",
			"      pdfkit runs `which wkhtmltopdf`; install it anywhere on this",
			"      process's PATH, then restart bench so the new PATH is inherited.",
			f"      PATH = {os.environ.get('PATH', '')}",
		]

	lines = [f"  OK  wkhtmltopdf   {path}"]
	try:
		out = subprocess.run(
			[path, "--version"], capture_output=True, text=True, timeout=15
		).stdout.strip()
		lines.append(f"      version: {out}")
		if "with patched qt" not in out.lower():
			lines.append(
				"      WARNING: not a patched-Qt build -- headers, footers and page"
			)
			lines.append("      numbers will be missing or wrong.")
	except Exception as e:
		lines.append(f"      version check failed: {type(e).__name__}: {e}")
	return lines


def _chrome_report() -> list[str]:
	from frappe.utils.print_utils import find_or_download_chromium_executable

	configured = frappe.get_common_site_config().get("chromium_path")
	timeout = frappe.get_common_site_config().get("chromium_start_timeout", 3)

	lines = []
	try:
		resolved = find_or_download_chromium_executable()
	except Exception as e:
		return [f"  XX  chrome        could not resolve: {type(e).__name__}: {e}"]

	exists = os.path.exists(resolved)
	lines.append(f"  {'OK ' if exists else 'XX '} chrome        {resolved}")
	if not configured:
		lines.append(
			"      chromium_path is unset, so Frappe downloads ~150MB into the bench"
		)
		lines.append("      on first use -- inside the web request that triggered it.")
	if timeout <= 3:
		lines.append(
			f"      chromium_start_timeout is {timeout}s; raise it to ~30 or a cold"
		)
		lines.append("      start throws 'Chromium took too long to start.'")
	return lines


@frappe.whitelist()
def check() -> dict:
	"""Print a readable report and return the machine-readable version."""
	frappe.only_for(("System Manager", "HR Manager"), message=True)

	wk_path = shutil.which("wkhtmltopdf")
	formats = frappe.get_all(
		"Print Format", filters={"module": "HR Documents"}, fields=["name", "pdf_generator"]
	)

	print(f"\n  letters are configured to use: {PDF_GENERATOR}\n")
	for line in _wkhtmltopdf_report():
		print(line)
	print()
	for line in _chrome_report():
		print(line)

	print("\n  Print Formats in HR Documents:")
	for f in formats:
		flag = "OK " if f.pdf_generator == PDF_GENERATOR else "!! "
		note = "" if f.pdf_generator == PDF_GENERATOR else "  <- out of sync, re-run the restyle patch"
		print(f"    {flag} {f.name:<24} {f.pdf_generator}{note}")

	usable = bool(wk_path) if PDF_GENERATOR == "wkhtmltopdf" else True
	print(f"\n  can this host render letters? {'YES' if usable else 'NO'}\n")

	return {
		"configured_generator": PDF_GENERATOR,
		"wkhtmltopdf_path": wk_path,
		"usable": usable,
		"print_formats": formats,
	}
