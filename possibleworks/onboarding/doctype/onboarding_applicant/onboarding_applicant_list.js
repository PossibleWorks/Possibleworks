/* Copyright (c) 2026, Possibleworks and contributors */
/* For license information, please see license.txt */

frappe.listview_settings["Onboarding Applicant"] = {
	add_fields: ["status", "date_of_joining", "employee", "docstatus"],

	get_indicator: function (doc) {
		const colours = {
			"Awaiting Applicant": "orange",
			"Applicant Submitted": "blue",
			"HR Review": "purple",
			"Ready to Onboard": "yellow",
			Onboarded: "green",
			Cancelled: "red",
		};

		// Surface the date-of-joining gate in the list, so HR can see at a glance
		// which records are actually submittable today.
		if (doc.status === "Ready to Onboard" && doc.date_of_joining) {
			if (frappe.datetime.get_diff(doc.date_of_joining, frappe.datetime.get_today()) > 0) {
				return [__("Joins {0}", [frappe.datetime.str_to_user(doc.date_of_joining)]), "gray", "status,=,Ready to Onboard"];
			}
			return [__("Ready to Onboard"), "yellow", "status,=,Ready to Onboard"];
		}

		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
