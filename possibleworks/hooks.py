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
app_include_js = [
    "/assets/possibleworks/js/whitelabel.js",
    # Shared by the three buying form scripts below; see the file header for why it is
    # here rather than copied into each one.
    "/assets/possibleworks/js/purchase_indent/buying_picker.js",
]

doctype_js = {
	"Purchase Invoice": "public/js/ap_invoice/purchase_invoice_ai_form.js",
	"Purchase Receipt": "public/js/ap_invoice/ai_document_form.js",
	# Two scripts: the AP-invoice AI form, plus the Purchase Indent picker swap. A second
	# "Supplier Quotation" key would silently replace this one (duplicate dict key), so
	# they share it as a list.
	"Supplier Quotation": [
		"public/js/ap_invoice/ai_document_form.js",
		"public/js/purchase_indent/supplier_quotation_form.js",
	],
	"Payment Entry": "public/js/ap_invoice/ai_document_form.js",
	"Sales Order": "public/js/ap_invoice/ai_document_form.js",
	"Quotation": "public/js/ap_invoice/ai_document_form.js",
	"Delivery Note": "public/js/ap_invoice/ai_document_form.js",
	"AI Document Queue": "ap_invoice_processing/doctype/ai_document_queue/ai_document_queue.js",
	"AI Document Processor Settings": "ap_invoice_processing/doctype/ai_document_processor_settings/ai_document_processor_settings.js",
	"Employee": "public/js/employee/employee_letters.js",
	# Route the buying flow through Purchase Indent: MR -> PI -> PO / RFQ / SQ. Each of
	# these swaps erpnext's direct "Material Request" picker for a Purchase Indent one.
	# Stock Entry keeps its Material Request picker on purpose -- transfer and issue
	# requests are not buying documents and never pass through an indent.
	"Material Request": "public/js/purchase_indent/material_request_form.js",
	"Purchase Order": "public/js/purchase_indent/purchase_order_form.js",
	"Request for Quotation": "public/js/purchase_indent/request_for_quotation_form.js",
}

# Jinja methods exposed to print formats / templates (Employee letters)
jinja = {
	"methods": [
		"possibleworks.hr_documents.letters.utils.get_letter_context",
		"possibleworks.hr_documents.letters.utils.get_employee_tenure_text",
		"possibleworks.utils.print_assets.get_file_as_data_uri",
	],
}
# NOTE: do NOT list a doctype here when the .js already lives in that doctype's own
# folder in this app -- Frappe loads it automatically, and a second entry injects the
# file twice into the same `new Function` body (ScriptManager.setup). Any top-level
# `const` then raises "Identifier has already been declared" and the form renders blank.
# `doctype_js` is for attaching scripts to OTHER apps' doctypes.

doctype_list_js = {
	"Purchase Invoice": "public/js/ap_invoice/ai_document_list.js",
	"Purchase Receipt": "public/js/ap_invoice/ai_document_list.js",
	"Supplier Quotation": "public/js/ap_invoice/ai_document_list.js",
	"Payment Entry": "public/js/ap_invoice/ai_document_list.js",
	"Sales Order": "public/js/ap_invoice/ai_document_list.js",
	"Quotation": "public/js/ap_invoice/ai_document_list.js",
	"Delivery Note": "public/js/ap_invoice/ai_document_list.js",
	"AI Document Queue": "ap_invoice_processing/doctype/ai_document_queue/ai_document_queue_list.js",
	"Observer Event Log": "observer/doctype/observer_event_log/observer_event_log_list.js",
	# Onboarding Applicant is deliberately absent -- see the note under doctype_js.
	# `onboarding_applicant_list.js` sits in the doctype folder and is auto-loaded.
}

# Row-level scoping for the external onboarding app's service user.
#
# DocPerm `read` is doctype-wide, so without these a leaked integration API key could
# enumerate every applicant's Aadhaar, PAN and bank details. Both are needed: the
# query conditions cover list/report views, has_permission covers single-document
# access (get_doc, read_doc, upload_file). HR roles are unaffected.
permission_query_conditions = {
	"Onboarding Applicant": "possibleworks.onboarding.doctype.onboarding_applicant.onboarding_applicant.get_permission_query_conditions",
}

has_permission = {
	"Onboarding Applicant": "possibleworks.onboarding.doctype.onboarding_applicant.onboarding_applicant.has_permission",
}

after_install = "possibleworks.setup.after_install.set_default_branding"

# Relabel third-party apps in the desk sidebar (see the module for why a hook
# override is not possible).
extend_bootinfo = ["possibleworks.branding.bootinfo.override_app_titles"]
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
		"on_cancel": "possibleworks.leave_application.reconstruct_attendance_on_leave_cancel",
	},
	"Employee": {
		"before_save": "possibleworks.employee.sync_leave_approver_and_reports_to",
	},
	# Keeps a Purchase Indent's ordered_qty / % Ordered / status current. erpnext drives
	# Material Request the same way, but through Purchase Order's own `status_updater`
	# list, which cannot be extended from another app without overriding the class.
	# `on_update_after_submit` is needed too: PO's "Update Items" edits qty in place.
	"Purchase Order": {
		"on_submit": "possibleworks.finance.purchase_indent_status.update_from_purchase_order",
		"on_cancel": "possibleworks.finance.purchase_indent_status.update_from_purchase_order",
		"on_update_after_submit": "possibleworks.finance.purchase_indent_status.update_from_purchase_order",
	},
    "*": {
        "after_insert": "possibleworks.observer.observer.handle_workflow_event",
        "on_update": "possibleworks.observer.observer.handle_workflow_event",
        "on_submit": "possibleworks.observer.observer.handle_workflow_event",
        "on_cancel": "possibleworks.observer.observer.handle_workflow_event",
        "on_trash": "possibleworks.observer.observer.handle_workflow_event",
        "on_discard": "possibleworks.observer.observer.handle_workflow_event",
        # "after_delete": "possibleworks.observer.observer.handle_workflow_event",
    },
}

override_doctype_class = {
	"Shift Type": "possibleworks.shift_type.PossibleWorksShiftType",
	"Request for Quotation": "possibleworks.overrides.request_for_quotation.CustomRequestForQuotation",
	"Compensatory Leave Request": "possibleworks.compensatory_leave_request.PossibleWorksCompensatoryLeaveRequest",
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
        "*/1 * * * *": [
            "possibleworks.observer.batch_processor.process_event_batch"
        ],
        "30 23 * * *": [
            "possibleworks.attendance_scheduler.mark_negative_attendance"
        ],
    },
    "daily": [
        "possibleworks.observer.doctype.observer_event_log.observer_event_log.run_log_cleanup"
    ],
}

# Fixtures: Custom Fields for these doctypes are synced via standard bench.
# From a site that has the custom fields:
#   bench --site <site> export-fixtures --app frappe_customizations
# Then commit fixtures/custom_field.json. Other sites get them via bench migrate.
# hooks.py

fixture_doctypes_with_custom_fields = [
	"Leave Type", "Leave Application", "Payroll Period","Employee","Shift Location",
	"Material Request", "Material Request Item", "Company", "Request for Quotation",
	"Request for Quotation Supplier",
]

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
            "Possibleworks Settings",
            "Shift Location Zone"
        ]]],
    },
    # Custom Print Formats + their Letter Head branding
    {
        "doctype": "Print Format",
        "filters": [["name", "in", [
            "GVS Material Requisition",
            "GVS Supplier Quotation",
            "GVS Purchase Order",
            "GVS Purchase Invoice",
        ]]],
    },
    {
        "doctype": "Letter Head",
        "filters": [["name", "in", ["Ganges Valley School"]]],
    },
]
