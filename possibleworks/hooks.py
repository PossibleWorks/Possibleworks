app_name = "possibleworks"
app_title = "Possibleworks"
app_publisher = "Possibleworks"
app_description = "White-label branding app for Frappe/ERPNext"
app_email = "contact@possibleworks.com"
app_license = "MIT"
app_logo_url = "/assets/possibleworks/images/possibleworks-logo.svg"


# Template resolution: our app first so overrides apply
template_apps = ["possibleworks", "erpnext", "hrms", "frappe"]

# Desk assets: CSS (cacheable) + JS patches
app_include_css = ["/assets/possibleworks/css/whitelabel.css"]
app_include_js = ["/assets/possibleworks/js/whitelabel.js"]

after_install = "possibleworks.setup.after_install.set_default_branding"

website_context = {
	"favicon": "/assets/possibleworks/images/possibleworks-logo.svg",
	"splash_image": "/assets/possibleworks/images/possibleworks-logo.svg"
}

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Leave Application": {
		"validate": "possibleworks.leave_application.validate_custom_attachments_required",
	},
    "*": {
        "after_insert": "possibleworks.observer.observer.handle_workflow_event",
        "on_update": "possibleworks.observer.observer.handle_workflow_event",
        "on_submit": "possibleworks.observer.observer.handle_workflow_event",
        "on_cancel": "possibleworks.observer.observer.handle_workflow_event",
        "on_trash": "possibleworks.observer.observer.handle_workflow_event",
        "on_discard": "possibleworks.observer.observer.handle_workflow_event",
        "after_delete": "possibleworks.observer.observer.handle_workflow_event",
    },
}


# ============================================================================
# Scheduler Events - Batch Processing
# ============================================================================

scheduler_events = {
    "cron": {
        "*/2 * * * *": [
            "possibleworks.observer.batch_processor.process_event_batch"
        ]
    }
}

# Fixtures: Custom Fields for these doctypes are synced via standard bench.
# From a site that has the custom fields:
#   bench --site <site> export-fixtures --app frappe_customizations
# Then commit fixtures/custom_field.json. Other sites get them via bench migrate.
# hooks.py

fixture_doctypes_with_custom_fields = ["Leave Type", "Leave Application", "Payroll Period"]

fixtures = [
    # Your existing custom fields
    {
        "doctype": "Custom Field",
        "filters": [["dt", "in", fixture_doctypes_with_custom_fields]],
    },
    # Add this — export the child DocType definition itself
    {
        "doctype": "DocType",
        "filters": [["name", "in", ["Leave Supporting Documents","Possibleworks Settings"]]],
    },
]