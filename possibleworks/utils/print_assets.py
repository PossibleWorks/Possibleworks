import base64
import mimetypes

import frappe


@frappe.whitelist()
def get_file_as_data_uri(file_url: str | None) -> str | None:
	"""Inline a File's bytes as a data: URI, so print formats don't depend on the
	browser being able to fetch /files/<name> as a second, cross-origin request.

	pw-react-client-v3 rasterizes print-format HTML with html2canvas to build a
	client-side PDF - that needs CORS-readable pixel data, not just a displayable
	<img>, and Frappe's static file route (served by StaticDataMiddleware, ahead
	of the normal request pipeline) never sends Access-Control-Allow-Origin. A
	data URI sidesteps the network fetch entirely instead of depending on
	infra-level CORS config that would need to be replicated per environment.
	"""
	if not file_url:
		return None

	file_doc = frappe.get_last_doc("File", {"file_url": file_url})
	content = file_doc.get_content()
	if isinstance(content, str):
		content = content.encode("utf-8")

	mime_type = mimetypes.guess_type(file_doc.file_name or file_url)[0] or "application/octet-stream"
	encoded = base64.b64encode(content).decode("ascii")
	return f"data:{mime_type};base64,{encoded}"
