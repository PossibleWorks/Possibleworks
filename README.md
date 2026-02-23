# Possibleworks – White-Label & AI Scanner Specifics

This app provides two main capabilities: **White-Label Branding** for Frappe/ERPNext, and an **AI Document Scanner** leveraging GPT-4o Vision.

**Core Principle:** Zero modifications to core Frappe, ERPNext, or HRMS files. Survives `bench update` and upstream pulls.

---

## Folder & Code Structure

The repository is built strictly to override and augment core features purely through hooks, custom APIs, and client scripts.

```text
apps/possibleworks/
├── possibleworks/
│   ├── __init__.py
│   ├── hooks.py                    # App config, template order, CSS/JS injection, boot_session hooks
│   ├── modules.txt                 # "Branding" and "AI" modules
│   ├── patches.txt                 # Migration patches (AI seeder, cleanup)
│   │
│   ├── ai/                         # [NEW] AI Document Scanner Engine
│   │   ├── __init__.py
│   │   ├── config.py               # boot_session hook: injects AI config into frappe.boot
│   │   ├── document_mappers.py     # Core Logic: 8 DocType mappers + master data resolution helpers
│   │   └── doctype/
│   │       ├── pw_ai_settings/     # Single DocType: Stores OpenAI API Key and toggles
│   │       └── pw_ai_doctype_config/ # Child table: Per-DocType prompt overrides
│   │
│   ├── api/
│   │   └── v1/
│   │       ├── __init__.py
│   │       └── scan_document.py    # Whitelisted Frappe API: Receives files, calls GPT-4o, maps output
│   │
│   ├── branding/                   # Module (required by Frappe)
│   │   └── __init__.py
│   │
│   ├── patches/                    # Migration / Database Patches
│   │   ├── __init__.py
│   │   ├── seed_ai_doctype_config.py # Pre-populates the 8 supported DocTypes on install
│   │   └── remove_old_auto_create_fields.py # Cleans up deprecated settings
│   │
│   ├── setup/
│   │   ├── __init__.py
│   │   └── after_install.py        # Sets app_name, footer_powered on install
│   │
│   ├── public/
│   │   ├── css/
│   │   │   └── whitelabel.css      # Desk CSS overrides (hides Frappe branding)
│   │   ├── js/
│   │   │   ├── whitelabel.js       # Desk UI patches (DOM mutation observers)
│   │   │   └── ai_scanner.js       # [NEW] Generic client script injecting the "Scan with AI" button
│   │   └── images/
│   │       ├── possibleworks-logo.svg
│   │       └── possibleworks-logo-big.svg
│   │
│   ├── templates/                  # Template Overrides (resolves before Frappe core)
│   │   ├── base.html               # Website base
│   │   ├── includes/footer/
│   │   │   └── footer_powered.html
│   │   └── emails/
│   │       ├── standard.html       # Email wrapper
│   │       └── email_footer.html   # Email footer
│   │
│   └── www/
│       ├── login.html              # Login page override
│       └── printview.html          # Print view meta
│
├── .gitignore
├── pyproject.toml                  # Python dependencies (openai, PyMuPDF)
└── README.md
```

---

## Sensitive Files & Major Integration Points

### 1. `hooks.py` (The App Backbone)
Controls template override order (`template_apps`), injects JavaScript into the Desk (`app_include_js`), and executes backend methods at session start (`boot_session`). Modifying this requires `bench restart`.

### 2. `possibleworks/ai/doctype/pw_ai_settings/` (Security)
The **PW AI Settings** Single DocType contains the `openai_api_key` field. This is a `Password` type field, meaning Frappe AES-encrypts it in the `tabSingles` database table.
- **Access**: Only `System Manager` role can view/edit this DocType.
- **Retrieval**: `api/v1/scan_document.py` decrypts it dynamically.

### 3. `possibleworks/public/js/ai_scanner.js` (Client-Side Async Workarounds)
This generic script is loaded universally. It explicitly relies on `frappe.boot.pw_ai_doctypes` to register its form hooks synchronously *before* forms render.
- **Frappe Async Gotchas**: It contains a deliberate `setTimeout(..., 1200)` to combat ERPNext's async price-list and tax-template fetching, which overwrites values set by the AI.

### 4. `possibleworks/ai/document_mappers.py` (Core Logic)
Houses the `MAPPER_REGISTRY` and all standard GPT extraction schemas. This is the sole logic center for Master Data auto-creation (`_create_supplier`, `_create_customer`, `_create_item`) which inherently bypasses standard frappe permissions logic (`ignore_permissions=True`, `ignore_mandatory=True`) to silently create backbone master data records.

### 5. `possibleworks/public/js/whitelabel.js` (DOM Integrity)
Uses a `MutationObserver` on `document.body` to reactively replace "Frappe" occurrences as Frappe's Vue/JS routing dynamically rewrites the DOM. Modifications here heavily impact overall UI performance.

---

## Install / Build Commands

```bash
bench --site YOUR_SITE install-app possibleworks
bench build --app possibleworks
bench --site YOUR_SITE migrate
bench --site YOUR_SITE clear-cache
sudo systemctl restart supervisor  # or bench restart
```
**Note**: Adding new AI configurations requires a browser hard-refresh and re-login (re-triggers boot_session hook).
