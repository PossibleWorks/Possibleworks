"""Shared constants for AI document processing."""

SETTINGS_DOCTYPE = "AI Document Processor Settings"
QUEUE_DOCTYPE = "AI Document Queue"
EXTRACTION_LOG_DOCTYPE = "AI Document Extraction Log"

ROLLOUT_DOCTYPES = (
	"Purchase Invoice",
	"Purchase Receipt",
	"Supplier Quotation",
	"Payment Entry",
	"Sales Order",
	"Quotation",
	"Delivery Note",
)

SUPPLIER_SIDE_DOCTYPES = (
	"Purchase Invoice",
	"Purchase Receipt",
	"Supplier Quotation",
)

CUSTOMER_SIDE_DOCTYPES = (
	"Sales Order",
	"Quotation",
	"Delivery Note",
)

# Payment Entry is bi-directional (Pay = supplier side, Receive = customer side).
# Listed separately so party-type logic can branch on it explicitly.
PAYMENT_ENTRY_DOCTYPE = "Payment Entry"


def get_settings_doctype() -> str:
	return SETTINGS_DOCTYPE


def get_queue_doctype() -> str:
	return QUEUE_DOCTYPE


def get_extraction_log_doctype() -> str:
	return EXTRACTION_LOG_DOCTYPE
