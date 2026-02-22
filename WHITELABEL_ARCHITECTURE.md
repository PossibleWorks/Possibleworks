# Technical Architecture: Non-Invasive Whitelabeling

This document outlines the implementation strategy for the **Possibleworks Whitelabeling**. The architecture adheres to a **Zero Core Modification** principle, ensuring that branding remains intact and upgrade-safe without altering the internal Frappe or ERPNext codebases.

## 1. Extension Strategy
All customizations are encapsulated within the `possibleworks` app. The integration relies on Frappe's native extensibility features.

## 2. Resource Injection and Prioritization (`hooks.py`)
The `hooks.py` file serves as the configuration entry point for custom resource loading:
- **`app_include_js`**: Injects `whitelabel.js` into the global Desk context. This script executes after internal desk bundles, allowing for post-render DOM manipulation.
- **`template_apps`**: An ordered list that defines template resolution priority. By placing `possibleworks` before `frappe` in this list, the Jinja2 engine prioritizes shadowing files in our app's `templates` or `www` directories (e.g., our custom `login.html`).
- **`after_install`**: Automates database-level branding via `frappe.db.set_value` for `Website Settings` and `System Settings`.

## 3. Client-Side Lifecycle Management (`whitelabel.js`)
Since the Frappe Desk is a Single Page Application (SPA), server-side overrides are insufficient for dynamic UI components.

### A. Prototype Monkey-Patching
To alter the behavior of existing UI components (like the Help menu or Theme Switcher), we use prototype patching. This intercepts class methods before they are called by the framework.
```javascript
// Example: Intercepting the Help Menu generation
if (frappe.ui.SidebarHeader) {
    const _get = frappe.ui.SidebarHeader.prototype.get_help_siblings;
    frappe.ui.SidebarHeader.prototype.get_help_siblings = function() {
        const items = _get.apply(this, arguments);
        // Memory-level filtering of items defined in hooks.py
        return (items || []).filter(item => 
            ["System Health", "Keyboard Shortcuts"].includes(item.label)
        );
    };
}
```

### B. Reactive DOM Observation
A `MutationObserver` instance monitors the `document.body` for asynchronous DOM insertions (e.g., dialogs, dynamic sidebars). 
- **Mechanism:** Any node matching "Support" or "About" selectors that is injected post-load is immediately targeted with `display: none !important` or removed to prevent a Flash of Unbranded Content (FOBC).

## 4. Summary of Mechanisms

| Mechanism | Purpose | Implementation |
| :--- | :--- | :--- |
| **Shadowing** | Overwriting global HTML templates | `template_apps` in `hooks.py` |
| **Asset Injection** | Global JS/CSS execution | `app_include_js` / `app_include_css` |
| **Interception** | Modifying internal UI class behavior | Prototype Monkey-Patching |
| **Persistence** | Real-time DOM sanitization | `MutationObserver` + `setInterval` |
| **Automation** | Initial environment setup | `after_install` python hook |

## 5. Maintenance and Upgrades
Because the `frappe` and `erpnext` directories remain untouched, system updates (`bench update`) can be performed safely. The whitelabeling layer remains decoupled from the core framework logic.

---
*Possibleworks Engineering Team*
