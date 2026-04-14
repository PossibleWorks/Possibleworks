# Frappe/ERPNext system doctypes that should be ignored by observer
# These are internal system events that create massive noise

# Doctypes that are always observed regardless of active workflow
ALWAYS_OBSERVED_DOCTYPES = [
    "Leave Application",
    "Attendance Request",
    "Compensatory Leave Request",
    "Employee",
]

# Procurement doctypes — always observed, always delivered immediately.
# Add any new procurement doctype here; no other file needs to change.
IMMEDIATE_SEND_DOCTYPES = [
    "Material Request",
    "Request for Quotation",
    "Supplier Quotation",
    "Purchase Order",
    "Purchase Receipt",
    "Landed Cost Voucher",
    "Purchase Invoice",
    "Payment Request",
    "Payment Entry",
]

# Doctypes where on_update fires on ANY field change (not just state transitions).
# All other observed doctypes require workflow_state / status / docstatus to change.
SKIP_STATE_CHANGED_CHECK_DOCTYPES = [
    "Employee",
]


# Redis configuration
REDIS_PREFIX = "possibleworks:event"
REDIS_QUEUE_KEY = f"{REDIS_PREFIX}:queue"
REDIS_PROCESSING_KEY = f"{REDIS_PREFIX}:processing"

# Batch processing
BATCH_SIZE = 5
BATCH_TIMEOUT_SECONDS = 10