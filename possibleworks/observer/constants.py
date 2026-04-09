# Frappe/ERPNext system doctypes that should be ignored by observer
# These are internal system events that create massive noise

# Doctypes that are always observed regardless of active workflow
ALWAYS_OBSERVED_DOCTYPES = [
    "Leave Application",
    "Attendance Request",
    "Compensatory Leave Request",
    "Employee",
]

SKIP_STATE_CHANGED_CHECK_DOCTYPES = [
    "Employee",
]


# Redis configuration
REDIS_PREFIX = "possibleworks:event"
REDIS_QUEUE_KEY = f"{REDIS_PREFIX}:queue"
REDIS_PROCESSING_KEY = f"{REDIS_PREFIX}:processing"

# Batch processing
BATCH_SIZE = 100
BATCH_TIMEOUT_SECONDS = 10