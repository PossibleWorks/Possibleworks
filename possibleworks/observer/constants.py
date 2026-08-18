# Frappe/ERPNext system doctypes that should be ignored by observer
# These are internal system events that create massive noise

# Doctypes that are always observed regardless of active workflow
ALWAYS_OBSERVED_DOCTYPES = [
    "Leave Application",
    "Attendance Request",
    "Compensatory Leave Request",
    "Employee",
]

# Always observed, always delivered immediately.
# Add any new doctype here; no other file needs to change.
#
# WARNING: the immediate branch calls frappe.db.commit() (observer.py). Anything listed
# here therefore COMMITS the in-flight transaction when it is created or updated, and a
# later failure in the same request can no longer be rolled back. Before adding a
# doctype, check whether it is created inside another document's on_submit -- if it is,
# that flow needs DEFER_IMMEDIATE_SEND_FLAG (below) or its atomicity is broken.
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
    "Expense Claim",
    "Leave Application",
    "Attendance Request",
    "Compensatory Leave Request",
    "Employee",
    # Hiring / onboarding chain. These send the same name/status pointer as everything
    # else and the receiver fetches the record itself -- deliberately, not for brevity.
    # `Onboarding Applicant` carries Aadhaar, PAN, passport and bank details, every
    # payload is persisted verbatim in Observer Event Log, and `auto_expire_logs`
    # defaults to off -- so a full-document event would park that data in this database
    # indefinitely, push it through Redis and hand all of it to the external app,
    # bypassing the row-level scoping that exists to stop exactly that.
    "Onboarding Applicant",
    "Job Applicant",
    "Job Offer",
    "Employee Onboarding",
]

# When this flag is set on frappe.flags, the immediate branch does NOT commit; the event
# is queued through the batch path instead, which leaves the transaction intact.
#
# Needed because `OnboardingApplicant.on_submit` creates a Job Applicant, a Job Offer, a
# User and an Employee in one transaction, deliberately ordered so that everything
# before the Employee insert can still be rolled back. Committing at each of those
# inserts would strand a Job Applicant and a submitted Job Offer behind an applicant
# that rolled back to draft -- and because a fresh Job Applicant is minted on every
# attempt, each retry would leave another orphan pair.
DEFER_IMMEDIATE_SEND_FLAG = "pw_defer_immediate_send"

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