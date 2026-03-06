# Frappe/ERPNext system doctypes that should be ignored by observer
# These are internal system events that create massive noise

IGNORED_DOCTYPES = {
    "Version",
    "Comment",
    "Workflow Action",
    "Communication",
    "Activity Log",
    "Error Log",
    "Document Share",
    "Event",
    "Task",
    "ToDo",
    "Notification",
    "Notification Count",
    "System Settings",
    "Notification Log",
    "Email Account",
    "Email Queue",
    "User",
    "Role",
    "Permission",
    "Custom Field",
    "Customize Form",
    "DocType",
    "DocField",
    "DocPerm",
}

# Events to observe
OBSERVED_EVENTS = [
    "after_insert",
    "on_update",
    "on_submit",
    "on_cancel",
    "before_insert",
    "before_update",
]

# Redis configuration
REDIS_PREFIX = "possibleworks:event"
REDIS_QUEUE_KEY = f"{REDIS_PREFIX}:queue"
REDIS_PROCESSING_KEY = f"{REDIS_PREFIX}:processing"

# Batch processing
BATCH_SIZE = 100
BATCH_TIMEOUT_SECONDS = 10