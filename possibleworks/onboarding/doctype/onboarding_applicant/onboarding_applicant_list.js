/* Copyright (c) 2026, Possibleworks and contributors */
/* For license information, please see license.txt */

frappe.listview_settings["Onboarding Applicant"] = {
	add_fields: ["status", "employee", "docstatus"],

	get_indicator: function (doc) {
		const colours = {
			"Awaiting Applicant": "orange",
			"Applicant Submitted": "blue",
			"HR Review": "purple",
			"Ready to Onboard": "yellow",
			Onboarded: "green",
			Cancelled: "red",
		};

		return [__(doc.status), colours[doc.status] || "gray", "status,=," + doc.status];
	},
};
