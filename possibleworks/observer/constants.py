# Frappe/ERPNext system doctypes that should be ignored by observer
# These are internal system events that create massive noise


# Redis configuration
REDIS_PREFIX = "possibleworks:event"
REDIS_QUEUE_KEY = f"{REDIS_PREFIX}:queue"
REDIS_PROCESSING_KEY = f"{REDIS_PREFIX}:processing"

# Batch processing
BATCH_SIZE = 100
BATCH_TIMEOUT_SECONDS = 10