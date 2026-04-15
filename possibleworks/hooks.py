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
app_include_css = [
    "/assets/possibleworks/css/whitelabel.css",
    "/assets/possibleworks/css/ap_invoice.css"
]
app_include_js = ["/assets/possibleworks/js/whitelabel.js"]

doctype_js = {
	"Purchase Invoice": "public/js/ap_invoice/purchase_invoice_ai_form.js",
	"Purchase Receipt": "public/js/ap_invoice/ai_document_form.js",
	"Supplier Quotation": "public/js/ap_invoice/ai_document_form.js",
	"Payment Entry": "public/js/ap_invoice/ai_document_form.js",
	"Sales Order": "public/js/ap_invoice/ai_document_form.js",
	"Quotation": "public/js/ap_invoice/ai_document_form.js",
	"Delivery Note": "public/js/ap_invoice/ai_document_form.js",
	"AI Document Queue": "ap_invoice_processing/doctype/ai_document_queue/ai_document_queue.js",
	"AI Document Processor Settings": "ap_invoice_processing/doctype/ai_document_processor_settings/ai_document_processor_settings.js",
}

doctype_list_js = {
	"Purchase Invoice": "public/js/ap_invoice/ai_document_list.js",
	"Purchase Receipt": "public/js/ap_invoice/ai_document_list.js",
	"Supplier Quotation": "public/js/ap_invoice/ai_document_list.js",
	"Payment Entry": "public/js/ap_invoice/ai_document_list.js",
	"Sales Order": "public/js/ap_invoice/ai_document_list.js",
	"Quotation": "public/js/ap_invoice/ai_document_list.js",
	"Delivery Note": "public/js/ap_invoice/ai_document_list.js",
	"AI Document Queue": "ap_invoice_processing/doctype/ai_document_queue/ai_document_queue_list.js",
}

after_install = "possibleworks.setup.after_install.set_default_branding"
after_migrate = "possibleworks.setup.after_install.seed_ai_settings"

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

override_doctype_class = {
	"AI Document Processor Settings": "possibleworks.ap_invoice_processing.doctype.ai_document_processor_settings.ai_document_processor_settings.AIDocumentProcessorSettings",
	"AI Document Processor Supported DocType": "possibleworks.ap_invoice_processing.doctype.ai_document_processor_supported_doctype.ai_document_processor_supported_doctype.AIDocumentProcessorSupportedDocType",
	"AI Document Extraction Log": "possibleworks.ap_invoice_processing.doctype.ai_document_extraction_log.ai_document_extraction_log.AIDocumentExtractionLog",
	"AI Document Queue": "possibleworks.ap_invoice_processing.doctype.ai_document_queue.ai_document_queue.AIDocumentQueue",
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
        "filters": [["name", "in", [
            "Leave Supporting Documents",
            "AI Document Processor Settings",
            "AI Document Processor Supported DocType",
            "AI Document Extraction Log",
            "AI Document Queue",
			"Policy Configuration",
            "Possibleworks Settings"
        ]]],
    },
]
