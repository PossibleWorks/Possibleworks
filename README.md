# Possibleworks – White-Label Architecture

White-label branding app for Frappe/ERPNext. Replaces user-visible "Frappe" with "Possibleworks" **without modifying any core Frappe, ERPNext, or HRMS files**. Survives `bench update` and upstream pulls.

---

## Core Principle: Zero Core Modifications

**No changes made in:**
- `apps/frappe/`
- `apps/erpnext/`
- `apps/hrms/`

**All changes are in:** `apps/possibleworks/`

---

## Folder Structure

```
apps/possibleworks/
├── possibleworks/
│   ├── __init__.py
│   ├── hooks.py                    # App config, template order, CSS/JS inclusion
│   ├── modules.txt                 # "Branding" module
│   ├── patches.txt                 # Empty
│   │
│   ├── branding/                   # Module (required by Frappe)
│   │   └── __init__.py
│   │
│   ├── setup/
│   │   ├── __init__.py
│   │   └── after_install.py        # Sets app_name, footer_powered on install
│   │
│   ├── public/
│   │   ├── css/
│   │   │   └── whitelabel.css      # Desk CSS overrides (cacheable)
│   │   ├── js/
│   │   │   └── whitelabel.js       # Desk UI patches
│   │   └── images/
│   │       ├── possibleworks-logo.svg
│   │       └── possibleworks-logo-big.svg
│   │
│   ├── templates/
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
├── pyproject.toml
└── README.md
```

---

## How It Works

### 1. Template Resolution

In `hooks.py`:

```python
template_apps = ["possibleworks", "erpnext", "hrms", "frappe"]
```

Frappe checks apps in this order. If `possibleworks/templates/base.html` exists, it is used instead of frappe’s.

### 2. Hooks

| Hook | Purpose |
|------|---------|
| `app_name`, `app_title`, `app_logo_url` | Boot context, app switcher |
| `template_apps` | Ensures our templates override core |
| `app_include_css` | Loads `whitelabel.css` on Desk (cacheable) |
| `app_include_js` | Loads `whitelabel.js` last on Desk |
| `after_install` | Sets System/Website Settings defaults |

### 3. After Install

On `bench install-app possibleworks`:

- **System Settings** → App Name = "Possibleworks"
- **Website Settings** → App Name, Footer Powered = "Powered by Possibleworks"

### 4. Desk Assets (`whitelabel.css` + `whitelabel.js`)

**CSS** hides About/Support menu items, help-links, and leftover dividers via a cacheable stylesheet.

**JS** runs one-time prototype patches at `DOMContentLoaded`, plus a single debounced `MutationObserver` for reactive DOM text replacement. No `setInterval` or stacked `setTimeout` calls.

| Patch | Effect |
|-------|--------|
| Boot filtering | Keeps only "System Health" & "Keyboard Shortcuts" in help menu |
| Theme switcher | "Frappe Light" → "Light" |
| Help sidebar | Filters out "About" and "Support" items |
| Menu filtering | Removes "About" and "Frappe Support" from `create_menu` |
| `set_title` | Removes "Frappe Framework" from page titles |
| Router | Runs DOM replacement on every route change |
| MutationObserver | Replaces text when new DOM nodes appear (debounced, cleaned up on unload) |

### 5. Template Overrides

| File | Change |
|------|--------|
| `base.html` | `generator` meta = "possibleworks" |
| `footer_powered.html` | "Powered by Possibleworks" |
| `standard.html` | Possibleworks logo, no frappeframework.com |
| `email_footer.html` | Replaces ERPNext "Sent via" with Possibleworks footer |
| `printview.html` | `generator` meta = "possibleworks" |

---

## Install

```bash
bench --site YOUR_SITE install-app possibleworks
bench build
bench --site YOUR_SITE clear-cache
```

Hard refresh browser (Cmd+Shift+R).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Still see "Frappe" | Clear cache; confirm `/assets/possibleworks/js/whitelabel.js` returns 200 |
| "frappe.ready is not a function" | desk.bundle.js likely 404 – run `bench build` and clear cache |
| Styles/CSS 404 | Run `bench build` and `bench --site <site> clear-cache` |
| Emails show "Frappe" | Ensure `template_apps` has possibleworks first; clear cache |
| Page title "Frappe" | Set App Name in System Settings and Website Settings |
