// Possibleworks AI Document Scanner — Generic Client Script
// Runs synchronously at page load, reads frappe.boot.pw_ai_doctypes (set by boot_session hook),
// and registers frappe.ui.form.on handlers for every enabled DocType before any form renders.
// No per-doctype JavaScript needed — all field resolution lives in the backend mapper.

(function () {
    "use strict";

    // frappe.boot.pw_ai_doctypes is populated by the boot_session hook (config.add_to_boot)
    // It's available synchronously right here — no async call needed.
    var ENABLED = frappe.boot && frappe.boot.pw_ai_doctypes || {};

    // Register form hooks for every enabled doctype immediately
    Object.keys(ENABLED).forEach(function (doctype) {
        frappe.ui.form.on(doctype, {
            refresh: function (frm) {
                _injectButton(frm, ENABLED[doctype]);
            },
        });
    });

    // ── Inject the AI scan button onto the form ──────────────────────
    function _injectButton(frm, cfg) {
        // Only show on new/draft documents (docstatus 0)
        if (frm.doc.docstatus !== 0) return;

        var label = cfg.button_label || "Scan with AI";

        frm.add_custom_button(
            __("🤖 {0}", [label]),
            function () { _openScanDialog(frm, cfg); },
            __("Tools")
        );

        // Highlight the button
        try {
            frm.change_custom_button_type(__("🤖 {0}", [label]), __("Tools"), "primary");
        } catch (_) { /* non-critical */ }
    }

    // ── Upload dialog ──────────────────────────────────────────────
    function _openScanDialog(frm, cfg) {
        var dialog = new frappe.ui.Dialog({
            title: __("Upload Document to Scan"),
            fields: [
                {
                    fieldname: "doc_file",
                    fieldtype: "Attach",
                    label: __("Document File (PDF, JPG, PNG)"),
                    reqd: 1,
                },
                {
                    fieldname: "info",
                    fieldtype: "HTML",
                    options: [
                        "<div style='padding:10px 14px;background:var(--subtle-fg);",
                        "border-radius:var(--border-radius-md);margin-top:6px;",
                        "font-size:var(--text-sm);color:var(--text-muted);line-height:1.7'>",
                        "<b>How it works:</b><br>",
                        "1. Upload the document (PDF or image)<br>",
                        "2. AI extracts all relevant fields<br>",
                        "3. Review the pre-filled form and save",
                        "</div>",
                    ].join(""),
                },
            ],
            primary_action_label: __("Scan"),
            primary_action: function (values) {
                dialog.hide();
                _processScan(frm, values.doc_file);
            },
            size: "small",
        });
        dialog.show();
    }

    // ── Call server API ─────────────────────────────────────────────
    function _processScan(frm, file_url) {
        frappe.show_progress(
            __("Scanning..."),
            0, 100,
            __("AI is reading your document. This may take 15–30 seconds...")
        );

        frappe.call({
            method: "possibleworks.api.v1.scan_document.scan_document",
            args: { file_url: file_url, doctype: frm.doctype },
            freeze: true,
            freeze_message: __("🤖 AI is reading your document..."),
            callback: function (r) {
                frappe.hide_progress();
                if (!r.message) {
                    frappe.msgprint({
                        title: __("Scan Failed"),
                        message: __("No data returned. Please try again."),
                        indicator: "red",
                    });
                    return;
                }
                _populateForm(frm, r.message);
            },
            error: function () {
                frappe.hide_progress();
            },
        });
    }

    // ── Populate form fields from AI result ─────────────────────────
    function _populateForm(frm, data) {
        var messages = [];
        var warnings = [];

        // Supplier (Purchase Invoice, Purchase Order, Purchase Receipt)
        if (data._supplier) {
            var s = data._supplier;
            if (s.supplier) {
                if (frm.fields_dict["supplier"]) frm.set_value("supplier", s.supplier);
                messages.push("✅ Supplier: " + s.supplier_name + " (" + s.match_type + ")");
            } else if (s.supplier_name) {
                warnings.push("⚠️ Supplier \"" + s.supplier_name + "\" not found — select manually.");
            }
            if (s.candidates && s.candidates.length > 1) {
                warnings.push("ℹ️ Multiple possible suppliers: " + s.candidates.map(function (c) { return c.supplier_name; }).join(", "));
            }
        }

        // Customer (Sales Invoice, Sales Order, Quotation, Delivery Note)
        if (data._customer) {
            var c = data._customer;
            var customerField = frm.fields_dict["customer"] ? "customer" : null;
            if (c.customer && customerField) {
                frm.set_value(customerField, c.customer);
                messages.push("✅ Customer: " + c.customer_name + " (" + c.match_type + ")");
            } else if (c.customer_name) {
                warnings.push("⚠️ Customer \"" + c.customer_name + "\" not found — select manually.");
            }
        }

        // Payment Entry (generic party)
        if (data._party) {
            var p = data._party;
            var partyName = data._party_type === "Supplier" ? p.supplier : p.customer;
            if (partyName) {
                frm.set_value("party_type", data._party_type);
                frm.set_value("party", partyName);
                messages.push("✅ Party: " + data._party_name + " (" + p.match_type + ")");
            } else {
                warnings.push("⚠️ Party \"" + data._party_name + "\" not found — select manually.");
            }
            // Payment Entry specific
            [["payment_type", data.payment_type],
            ["paid_amount", data.paid_amount],
            ["received_amount", data.received_amount],
            ["reference_no", data.reference_no],
            ["reference_date", data.reference_date],
            ["posting_date", data.posting_date],
            ["remarks", data.remarks],
            ["mode_of_payment", data.mode_of_payment],
            ].forEach(function (pair) {
                if (pair[1] != null && frm.fields_dict[pair[0]]) frm.set_value(pair[0], pair[1]);
            });
        }

        // Common header fields
        var headerFields = [
            "bill_no", "bill_date", "due_date", "posting_date",
            "transaction_date", "schedule_date", "delivery_date",
            "valid_till", "po_no", "po_date",
            "lr_no", "lr_date", "transporter_name", "vehicle_no",
            "supplier_delivery_note",
        ];
        headerFields.forEach(function (f) {
            if (data[f] != null && frm.fields_dict[f]) frm.set_value(f, data[f]);
        });

        // Items table
        if (data.items && data.items.length > 0 && frm.fields_dict["items"]) {
            frm.clear_table("items");
            var matched = 0;
            data.items.forEach(function (item) {
                var r = item._resolved || {};
                var row = frm.add_child("items");
                if (r.item_code) {
                    frappe.model.set_value(row.doctype, row.name, "item_code", r.item_code);
                    matched++;
                } else {
                    frappe.model.set_value(row.doctype, row.name, "item_name", item.item_name || "");
                    if (item.item_name) warnings.push("⚠️ Item \"" + item.item_name + "\" not found — link manually.");
                }
                if (item.qty != null) frappe.model.set_value(row.doctype, row.name, "qty", item.qty);
                if (item.rate != null) frappe.model.set_value(row.doctype, row.name, "rate", item.rate);
                if (r.uom || item.uom) frappe.model.set_value(row.doctype, row.name, "uom", r.uom || item.uom);
                if (item.description) frappe.model.set_value(row.doctype, row.name, "description", item.description);
            });
            if (matched) messages.push("📦 Items: " + matched + "/" + data.items.length + " matched");
            frm.refresh_field("items");

            // Fix for Issue: ERPNext's item_code and party triggers fetch price lists
            // and default tax templates asynchronously, overwriting our extracted data.
            // Workaround: Re-apply extracted rates and taxes after a short delay.
            setTimeout(function () {
                var has_changes = false;

                // 1. Re-apply item rates
                if (data.items && frm.fields_dict["items"]) {
                    var grid = frm.fields_dict["items"].grid;
                    var grid_data = grid.get_data();
                    data.items.forEach(function (item, idx) {
                        if (item.rate != null && grid_data[idx] && grid_data[idx].rate !== item.rate) {
                            frappe.model.set_value(grid_data[idx].doctype, grid_data[idx].name, "rate", item.rate);
                            has_changes = true;
                        }
                    });
                }

                // 2. Clear default tax template and apply AI taxes
                if (frm.fields_dict["taxes"] && data.taxes !== undefined) {
                    // Clear the template field if it exists, so ERPNext stops trying to calculate it
                    if (frm.fields_dict["taxes_and_charges"]) {
                        frm.set_value("taxes_and_charges", "");
                    }

                    // Clear whatever default table ERPNext generated asynchronously
                    frm.clear_table("taxes");

                    // Only populate if AI actually returned taxes
                    if (data.taxes && data.taxes.length > 0) {
                        data.taxes.forEach(function (tax) {
                            var row = frm.add_child("taxes");
                            frappe.model.set_value(row.doctype, row.name, "charge_type", "On Net Total");
                            frappe.model.set_value(row.doctype, row.name, "description", (tax.tax_type || "Tax").toUpperCase());
                            if (tax.rate) frappe.model.set_value(row.doctype, row.name, "rate", tax.rate);
                            if (tax.amount) frappe.model.set_value(row.doctype, row.name, "tax_amount", tax.amount);
                            _setTaxAccount(row, tax.tax_type, frm.doc.company);
                        });
                        messages.push("💰 Taxes: " + data.taxes.length + " explicit rows applied");
                    }
                    frm.refresh_field("taxes");
                    has_changes = true;
                }

                if (has_changes) {
                    console.log("ai_scanner.js: Applied deferred rate and tax fixups");
                }
            }, 1200);

            frm.refresh_fields();
            frm.dirty();

            // Summary dialog
            var html = "<div style='line-height:1.8'>";
            if (messages.length) html += "<b>Extracted:</b><br>" + messages.join("<br>") + "<br><br>";
            if (warnings.length) html += "<b style='color:var(--orange-500)'>Needs attention:</b><br>" + warnings.join("<br>") + "<br><br>";
            if (data.grand_total) html += "<b>Grand Total on Document:</b> " + (data.currency || "INR") + " " + data.grand_total;
            html += "</div>";

            frappe.msgprint({
                title: __("AI Scan Complete"),
                message: html,
                indicator: warnings.length ? "orange" : "green",
                wide: true,
            });
        }

        // ── Best-effort tax account lookup ──────────────────────────────
        function _setTaxAccount(row, tax_type, company) {
            if (!company || !tax_type) return;
            frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: "Account",
                    filters: { company: company, account_type: "Tax", account_name: ["like", "%" + tax_type + "%"], is_group: 0 },
                    fields: ["name"],
                    limit_page_length: 1,
                },
                callback: function (r) {
                    if (r.message && r.message.length) {
                        frappe.model.set_value(row.doctype, row.name, "account_head", r.message[0].name);
                    }
                },
            });
        }
    }
})();
