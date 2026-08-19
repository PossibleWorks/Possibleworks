# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Seed the three default Employee Letter Templates (Relieving Letter,
Experience Letter, Service Certificate). Each template generates its Print
Format via the doctype's on_update hook."""

import frappe

from possibleworks.hr_documents.letters.utils import PLACEMENT_EXIT, PLACEMENT_LETTERS

SIGNATURE = (
	"<br>\n"
	'<p style="margin-bottom:48px;">For <strong>{{ company }}</strong>,</p>\n'
	'<p style="margin:0;">Authorised Signatory</p>\n'
	'<p style="margin:0;color:#555;">Human Resources</p>'
)

DEFAULTS = [
	{
		"template_name": "Relieving Letter",
		"placement": PLACEMENT_EXIT,
		"letter_title": "RELIEVING LETTER",
		"subtitle": "",
		"description": "Issued when an employee leaves the organisation.",
		"icon": "file-text",
		"requires_relieving_date": 1,
		"body": (
			"<p>Employee ID: <strong>{{ employee_id }}</strong></p>\n"
			"<p>Dear {{ salutation }} {{ employee_name }},</p>\n"
			"<p>This is to confirm that you have been relieved from your services at "
			"<strong>{{ company }}</strong> in the capacity of <strong>{{ designation }}</strong>"
			"{% if department %} in the {{ department }} department{% endif %}, effective "
			"<strong>{{ relieving_date }}</strong>.</p>\n"
			"<p>You joined our organisation on {{ date_of_joining }} and served with us for a "
			"period of {{ tenure_text }}. During your tenure, your conduct and performance were "
			"found to be satisfactory.</p>\n"
			"<p>We confirm that, as on the date of relieving, there are no dues or obligations "
			"pending against you with the company. We thank you for your valuable contribution "
			"and wish you the very best in all your future endeavours.</p>\n" + SIGNATURE
		),
	},
	{
		"template_name": "Experience Letter",
		"placement": PLACEMENT_EXIT,
		"letter_title": "EXPERIENCE LETTER",
		"subtitle": "TO WHOMSOEVER IT MAY CONCERN",
		"description": "Certifies role, tenure and conduct.",
		"icon": "star",
		"requires_relieving_date": 0,
		"body": (
			"<p>This is to certify that {{ salutation }} <strong>{{ employee_name }}</strong> "
			"(Employee ID: {{ employee_id }}) {% if is_relieved %}was employed{% else %}has been "
			"employed{% endif %} with <strong>{{ company }}</strong> as "
			"<strong>{{ designation }}</strong>{% if department %} in the {{ department }} "
			"department{% endif %} from <strong>{{ date_of_joining }}</strong> "
			"{% if is_relieved %}to <strong>{{ relieving_date }}</strong>{% else %}till date"
			"{% endif %}.</p>\n"
			"<p>During {% if is_relieved %}the{% else %}this{% endif %} period of {{ tenure_text }}, "
			"{{ salutation }} {{ employee_name }} was found to be sincere, hardworking and diligent "
			"in the discharge of duties. Their overall conduct and performance during the tenure "
			"were good.</p>\n"
			"<p>We wish {{ salutation }} {{ employee_name }} continued success in all future "
			"endeavours.</p>\n" + SIGNATURE
		),
	},
	{
		"template_name": "Service Certificate",
		"placement": PLACEMENT_LETTERS,
		"letter_title": "SERVICE CERTIFICATE",
		"subtitle": "TO WHOMSOEVER IT MAY CONCERN",
		"description": "Confirms continuous duration of service.",
		"icon": "clock",
		"requires_relieving_date": 0,
		"body": (
			"<p>This is to certify that {{ salutation }} <strong>{{ employee_name }}</strong> "
			"(Employee ID: {{ employee_id }}) {% if is_relieved %}was in the continuous service of"
			"{% else %}has been in the continuous service of{% endif %} <strong>{{ company }}</strong> "
			"from <strong>{{ date_of_joining }}</strong> {% if is_relieved %}to "
			"<strong>{{ relieving_date }}</strong>{% else %}to date{% endif %}, holding the position "
			"of <strong>{{ designation }}</strong>{% if department %} in the {{ department }} "
			"department{% endif %}.</p>\n"
			"<p>As per our records, {{ salutation }} {{ employee_name }} has completed "
			"<strong>{{ tenure_text }}</strong> of continuous service with the organisation.</p>\n"
			"<p>This certificate is issued upon request for the purpose of record and reference.</p>\n"
			+ SIGNATURE
		),
	},
]


def execute():
	for d in DEFAULTS:
		if frappe.db.exists("Employee Letter Template", d["template_name"]):
			continue
		doc = frappe.new_doc("Employee Letter Template")
		doc.update(d)
		doc.is_default = 1
		doc.enabled = 1
		doc.insert(ignore_permissions=True)
