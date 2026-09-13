# Copyright (c) 2026, Possibleworks and contributors
# For license information, please see license.txt

"""Split the letter templates across the two mount points and add the Visa Letter.

`placement` is new, so every existing template needs a value before the form can
render it. The two offboarding letters stay in the Employee Exit section; anything
else -- seeded or HR-authored -- belongs on the Letters tab.
"""

import frappe

from possibleworks.hr_documents.letters.utils import PLACEMENT_EXIT, PLACEMENT_LETTERS

# Only these two are about leaving; everything else falls through to the default.
EXIT_LETTERS = ("Relieving Letter", "Experience Letter")

SIGNATURE = (
	"<br>\n"
	'<p style="margin-bottom:48px;">For <strong>{{ company }}</strong>,</p>\n'
	'<p style="margin:0;">Authorised Signatory</p>\n'
	'<p style="margin:0;color:#555;">Human Resources</p>'
)

VISA_LETTER = {
	"template_name": "Visa Letter",
	"letter_title": "VISA SUPPORT LETTER",
	"subtitle": "TO WHOMSOEVER IT MAY CONCERN",
	"description": "Supports a visa application with proof of employment.",
	"icon": "web",
	"placement": PLACEMENT_LETTERS,
	"requires_relieving_date": 0,
	"body": (
		"<p>This is to certify that {{ salutation }} <strong>{{ employee_name }}</strong> "
		"(Employee ID: {{ employee_id }}) {% if is_relieved %}was employed with "
		"<strong>{{ company }}</strong> as <strong>{{ designation }}</strong>"
		"{% if department %} in the {{ department }} department{% endif %} from "
		"<strong>{{ date_of_joining }}</strong> to <strong>{{ relieving_date }}</strong>"
		"{% else %}is employed with <strong>{{ company }}</strong> as "
		"<strong>{{ designation }}</strong>{% if department %} in the {{ department }} "
		"department{% endif %}, and has been with the organisation since "
		"<strong>{{ date_of_joining }}</strong>{% endif %}, completing "
		"<strong>{{ tenure_text }}</strong> of service.</p>\n"
		"{% if passport_number %}<p>Passport Number: <strong>{{ passport_number }}</strong>"
		"</p>\n{% endif %}"
		"<p>This letter is issued at the employee's request in support of their visa "
		"application.{% if not is_relieved %} We confirm that {{ salutation }} "
		"{{ employee_name }} continues in our employment, that the leave applied for has "
		"been approved, and that they are expected to resume their duties with us on "
		"completion of the travel.{% endif %}</p>\n"
		"<p>Should you require any further information, please contact our Human Resources "
		"department.</p>\n" + SIGNATURE
	),
}


def execute():
	backfill_placement()
	seed_visa_letter()


def backfill_placement():
	for name in frappe.get_all("Employee Letter Template", pluck="name"):
		placement = PLACEMENT_EXIT if name in EXIT_LETTERS else PLACEMENT_LETTERS
		frappe.db.set_value("Employee Letter Template", name, "placement", placement)


def seed_visa_letter():
	"""Insert the Visa Letter, whose on_update hook generates its Print Format."""
	if frappe.db.exists("Employee Letter Template", VISA_LETTER["template_name"]):
		return

	doc = frappe.new_doc("Employee Letter Template")
	doc.update(VISA_LETTER)
	doc.is_default = 1
	doc.enabled = 1
	doc.insert(ignore_permissions=True)
