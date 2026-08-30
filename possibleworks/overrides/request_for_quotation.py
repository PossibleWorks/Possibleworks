import frappe
from erpnext.buying.doctype.request_for_quotation.request_for_quotation import RequestforQuotation
from frappe.utils import format_date

from possibleworks.finance.rfq_portal import build_guest_quotation_url, generate_and_store_token

# Same PossibleWorks email-card styling used by pw-server-v3's own
# notification emails (src/helpers/email/emailSender.ts's
# generateEmailTemplate()) - kept visually consistent across both systems.
PW_LOGO_URL = "https://migrated-pw-images-dev.s3.ap-south-1.amazonaws.com/PWLogoForEmailV3.png"
PW_BLUE = "#3d7bf5"
PW_CARD_BG = "#eaf1fe"
PW_TEXT_DARK = "#0f1e3d"
PW_TEXT_BODY = "#3d475c"


def _build_supplier_email_html(supplier_name, rfq_name, schedule_date, buyer_message, guest_link):
	due_date_text = format_date(schedule_date) if schedule_date else "-"
	body_text = buyer_message or "Please supply the specified items at the best possible rates."

	return f"""
<div style="max-width:600px; margin:0 auto;">
	<div style="padding:32px 24px; background-color:{PW_CARD_BG}; border-radius:12px; box-sizing:border-box;">
		<img src="{PW_LOGO_URL}" alt="PossibleWorks" style="height:40px; margin-bottom:24px; display:block;" />
		<h2 style="font-size:20px; font-weight:500; color:{PW_TEXT_DARK}; margin:0 0 12px 0;">Hey {supplier_name},</h2>
		<p style="color:{PW_TEXT_BODY}; font-size:14px; font-weight:400; margin:0 0 20px 0; line-height:1.5;">{body_text}</p>
		<div style="background-color:#ffffff; border-radius:12px; padding:16px 20px; margin-bottom:24px;">
			<div style="font-weight:600; color:{PW_TEXT_DARK}; font-size:14px;">{rfq_name}</div>
			<div style="color:{PW_TEXT_BODY}; font-size:13px; margin-top:4px;">Required by {due_date_text}</div>
		</div>
		<a href="{guest_link}" target="_blank" rel="noopener noreferrer" style="background-color:{PW_BLUE}; color:#ffffff !important; text-decoration:none; padding:12px 20px; border-radius:25px; font-weight:bold; font-size:14px; display:inline-block;">Submit your Quotation</a>
	</div>
</div>
"""


class CustomRequestForQuotation(RequestforQuotation):
	def send_to_supplier(self):
		"""
		Overrides ERPNext's native send_to_supplier(): replaces the login-gated
		supplier portal link with a tokenized, expiring "magic link" so the
		supplier can submit a quotation with no account/login at all, and
		replaces the plain-text email body with a PossibleWorks-styled card
		(same visual language as pw-server-v3's own notification emails).
		Mail sending/attachments are untouched - only the message content and
		the link itself change. Also skips update_supplier_contact (no
		Website User is provisioned - there's nothing for it to log into).
		"""
		validity_days = self.pw_quotation_link_validity_days or 7
		original_message = self.message_for_supplier

		try:
			for rfq_supplier in self.suppliers:
				if rfq_supplier.email_id is not None and rfq_supplier.send_email:
					self.validate_email_id(rfq_supplier)
					self.update_supplier_part_no(rfq_supplier.supplier)

					token = generate_and_store_token(rfq_supplier, validity_days)
					guest_link = build_guest_quotation_url(token, self.company)
					if not guest_link:
						frappe.msgprint(
							f"Skipped emailing {rfq_supplier.supplier}: this Company isn't"
							" connected to a PossibleWorks tenant yet (custom_tenant_id is"
							" not set), so no working link could be built.",
							title="Quotation link not sent",
							indicator="orange",
						)
						continue

					self.message_for_supplier = _build_supplier_email_html(
						rfq_supplier.supplier_name or rfq_supplier.supplier,
						self.name,
						self.schedule_date,
						original_message,
						guest_link,
					)
					self.supplier_rfq_mail(rfq_supplier, "", guest_link)
					rfq_supplier.email_sent = 1
					rfq_supplier.save()
		finally:
			self.message_for_supplier = original_message
