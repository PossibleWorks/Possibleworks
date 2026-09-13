"""Manual QA helper: what did submitting an Onboarding Applicant actually create?

Not imported by anything and not part of the feature -- delete it once the flow has
been signed off.

    bench --site hw-hris execute possibleworks.onboarding.verify_live.check \
        --kwargs '{"name": "HR-ONB-2026-000XX"}'
"""

import frappe


def check(name):
	doc = frappe.get_doc("Onboarding Applicant", name)
	line = "-" * 62

	def row(label, value, good):
		print(f"  {'OK ' if good else 'XX '} {label:<26} {value}")

	print(f"\n{line}\n  Onboarding Applicant {name}\n{line}")
	row("docstatus", doc.docstatus, doc.docstatus == 1)
	row("status", doc.status, doc.status == "Onboarded")
	row("employee", doc.employee, bool(doc.employee))
	row("invite killed", doc.invite_expires_on or "yes", not doc.invite_expires_on)

	if not doc.employee:
		print("\n  No Employee -- nothing downstream to check.\n")
		return

	emp = frappe.get_doc("Employee", doc.employee)
	print(f"{line}\n  Employee {emp.name}\n{line}")
	row("employee_name", emp.employee_name, bool(emp.employee_name))
	row("status", emp.status, emp.status == "Active")
	row("company_email", emp.company_email, emp.company_email == doc.company_email)
	row("user_id (the login)", emp.user_id, emp.user_id == doc.company_email)
	row("job_applicant", emp.job_applicant, bool(emp.job_applicant))
	row("onboarding_applicant", emp.onboarding_applicant, emp.onboarding_applicant == name)

	print(f"{line}\n  Login\n{line}")
	if emp.user_id:
		user = frappe.get_doc("User", emp.user_id)
		roles = sorted(r.role for r in user.roles)
		profiles = [p.role_profile for p in user.get("role_profiles") or []]
		row("enabled", user.enabled, bool(user.enabled))
		row("user_type", user.user_type, user.user_type == "System User")
		row("welcome email sent", "no" if not user.send_welcome_email else "YES", not user.send_welcome_email)
		row("role profile", profiles or "-- none --", "Standard Employee Role Profile" in profiles)
		row("Employee role survived", "Employee" in roles, "Employee" in roles)
		print(f"      roles: {', '.join(roles)}")

	print(f"{line}\n  Recruitment chain\n{line}")
	ja = emp.job_applicant
	if ja:
		st = frappe.db.get_value("Job Applicant", ja, "status")
		row("Job Applicant", f"{ja} [{st}]", st == "Accepted")
		offer = frappe.db.get_value(
			"Job Offer", {"job_applicant": ja}, ["name", "status", "docstatus"], as_dict=True
		)
		if offer:
			row("Job Offer", f"{offer.name} [{offer.status}]", offer.status == "Accepted")
			row("Job Offer submitted", offer.docstatus == 1, offer.docstatus == 1)
		else:
			row("Job Offer", "-- MISSING --", False)

	print(f"{line}\n  Employee Onboarding checklist\n{line}")
	ob = frappe.db.get_value(
		"Employee Onboarding",
		{"employee": emp.name, "docstatus": ("!=", 2)},
		["name", "docstatus", "boarding_begins_on", "date_of_joining", "employee_onboarding_template"],
		as_dict=True,
	)
	if not ob:
		row("checklist", "-- MISSING -- run Retry Onboarding Setup", False)
	else:
		row("name", ob.name, True)
		row("draft", ob.docstatus == 0, ob.docstatus == 0)
		row("template", ob.employee_onboarding_template, bool(ob.employee_onboarding_template))
		row(
			"boarding_begins_on",
			f"{ob.boarding_begins_on}  (DOJ {doc.date_of_joining})",
			str(ob.boarding_begins_on) == str(doc.date_of_joining),
		)
		acts = frappe.get_all(
			"Employee Boarding Activity",
			filters={"parent": ob.name, "parenttype": "Employee Onboarding"},
			fields=["activity_name", "user", "role", "begin_on", "duration"],
			order_by="idx",
		)
		row("activities copied", len(acts), len(acts) == 5)
		for a in acts:
			owner = a.user or a.role or "unassigned"
			flag = "OK " if owner == "unassigned" and a.begin_on is not None else "XX "
			print(f"      {flag} {a.activity_name:<44} begin_on={a.begin_on} dur={a.duration} [{owner}]")

	print(f"{line}\n  Template is shared, not duplicated\n{line}")
	n = frappe.db.count("Employee Onboarding Template", {"title": "Default Employee Onboarding"})
	row("copies of default template", n, n == 1)
	print()
