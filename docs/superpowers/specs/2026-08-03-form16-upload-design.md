# Form 16 Upload/Download — Design

Date: 2026-08-03
App: `possibleworks`
Branch: `feat/form-16`

## Problem

HR/Payroll needs a way to record one Form 16 (income tax certificate) per employee per payroll period, uploaded by either HR or the employee themselves, and later downloaded by the employee (via Desk) or fetched by an external/headless client via API key. There is no existing Form 16 concept anywhere in `frappe`, `erpnext`, or `hrms` core — the closest analog is `Employee Tax Exemption Proof Submission`, which is a proof-of-investment doctype, not a fit for this. Editing `hrms`/`erpnext` source directly is not viable since those apps get overwritten on `bench update`.

## Goals

- One Form 16 document per employee per payroll period, uploaded by HR or the employee.
- Employee can view/download only their own record; HR roles can view/manage all.
- Correction/reissue is possible with a full audit trail (no silent overwrite of a legal tax document).
- External/headless clients (API key + secret) can list and download an employee's own Form 16(s), enforced by the same permission model as Desk.

## Non-goals (explicitly out of scope for this iteration)

- Bulk upload tooling for HR (e.g. matching many files to employees at once).
- Automatic email/notification on upload.
- Generating Form 16 content (this only stores/serves an already-produced document).

## Placement

- New module **`HR Documents`** in the `possibleworks` app (alongside existing modules `Branding`, `AP Invoice Processing`, `Observer`).
- New doctype **`Form 16`** under that module.
- `possibleworks/hooks.py` gets `hrms` added to `required_apps` (not currently declared), since the doctype links to the HRMS `Payroll Period` doctype and reuses `hrms.hr.utils` helpers.

## Data model

Doctype: `Form 16` (submittable: `is_submittable: 1`)

| Field | Type | Notes |
|---|---|---|
| `employee` | Link → Employee | required |
| `employee_name` | Data | `fetch_from: employee.employee_name`, read-only |
| `company` | Link → Company | `fetch_from: employee.company`, read-only |
| `payroll_period` | Link → Payroll Period | required |
| `attachment` | Attach | required; **must be uploaded as a private file** |
| `remarks` | Small Text | optional |
| `amended_from` | Link → Form 16 | standard amend-chain field, `no_copy`, `read_only`, `print_hide` |

Autoname suggestion: `HR-FORM16-.YYYY.-.#####` (mirrors `Employee Tax Exemption Declaration`'s `HR-TAX-DEC-.YYYY.-.#####`).

## Validation rules (in `Form16.validate()`)

1. `hrms.hr.utils.validate_active_employee(self.employee)` — blocks uploads against inactive/left employees. Reused directly, no reimplementation.
2. `hrms.hr.utils.validate_duplicate_exemption_for_payroll_period(self.doctype, self.name, self.payroll_period, self.employee)` — this helper is already generic over `doctype`, so it's reused as-is rather than reimplementing duplicate detection. Blocks a second non-cancelled Form 16 for the same employee + payroll period.

Re-issuing a corrected Form 16 = HR cancels the existing submitted doc → amends it → uploads the corrected file on the amended doc → submits. This gives a full audit trail (who uploaded which version, when), consistent with how `Employee Tax Exemption Declaration` already handles corrections in this codebase.

## Permissions

| Role | create | write | read | submit | cancel | amend | delete |
|---|---|---|---|---|---|---|---|
| System Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| HR Manager | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| HR User | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Employee | ✓ | ✓ (draft only) | ✓ | ✓ | ✗ | ✗ | ✗ |

Employee can upload and submit their own Form 16, but cannot cancel, amend, or delete one — corrections stay HR-gated to prevent tampering with an official tax document.

## Self-service scoping (no custom code needed)

`employee` is a Link field to `Employee`. This bench already auto-creates a `User Permission` (`allow: Employee, for_value: <employee>, user: <user_id>`) for every employee with `user_id` set and `create_user_permission` checked (`erpnext/setup/doctype/employee/employee.py:99`). Frappe's standard user-permission enforcement then automatically filters every list/read/report on any doctype with a Link to `Employee` — including this one — to the logged-in employee's own record. No `if_owner`, no custom `permission_query_conditions`, no `get_permission_query_conditions` hook required.

## Download

### Desk UI

Once the file is uploaded as private, opening the Form 16 record and clicking the attachment already works end-to-end: Frappe serves private files through the built-in `/private/files/...` route (`frappe/app.py:126` → `frappe.utils.response.download_private_file`), which resolves the `File` record and checks permission against its `attached_to_doctype`/`attached_to_name` — i.e., against `Form 16` read permission, which is already scoped correctly per the section above. No new code needed for this path.

### External/headless API access

Since the actual consumer here is a headless client authenticating via API key/secret (not a browser session), two whitelisted methods are added in `possibleworks/hr_documents/api.py`:

1. **`list_form16(employee=None)`**
   Returns submitted (`docstatus=1`) Form 16 records visible to the caller: `name`, `payroll_period`, `creation`. Built on `frappe.get_list`, which already applies User Permission filtering — an employee-scoped API key only ever sees its own records regardless of the `employee` argument; an HR-role API key sees everyone's.

2. **`download_form16(name)`**
   ```python
   @frappe.whitelist()
   def download_form16(name):
       doc = frappe.get_doc("Form 16", name)
       doc.check_permission("read")
       if not doc.attachment:
           frappe.throw(_("No Form 16 attached"))
       file_doc = frappe.get_doc(
           "File",
           {
               "file_url": doc.attachment,
               "attached_to_doctype": doc.doctype,
               "attached_to_name": doc.name,
           },
       )
       frappe.local.response.filename = file_doc.file_name
       frappe.local.response.filecontent = file_doc.get_content()
       frappe.local.response.type = "download"
   ```
   `doc.check_permission("read")` (`frappe/model/document.py:363`) is what actually enforces the scoping — not the `name` parameter — so a caller cannot download another employee's Form 16 by guessing/passing a different `name`, even with a valid API key. This mirrors the existing whitelisted-download convention already used elsewhere in this stack (e.g. `erpnext/regional/report/irs_1099/irs_1099.py`'s `response.filecontent`/`response.type = "download"` pattern), and uses `File.get_content()` from Frappe core (`frappe/core/doctype/file/file.py:591`) rather than reading disk paths manually.

   Frappe API key authentication resolves to a specific `frappe.session.user` exactly like a session cookie does, so the same permission/User Permission model applies uniformly to both Desk and headless access — no separate auth logic needed.

## Testing plan

- `test_form16.py`:
  - Create + submit a Form 16 for an employee/payroll period → succeeds.
  - Duplicate: attempt a second non-cancelled Form 16 for the same employee + payroll period → raises `DuplicateDeclarationError`.
  - Inactive employee → `validate_active_employee` blocks creation.
  - Cancel + amend flow → old doc cancelled, amended doc references `amended_from`, re-submit succeeds.
  - Permission test: an `Employee`-role user (linked via `User Permission`) can read/list only their own Form 16 via `frappe.get_list`; cannot read another employee's via `frappe.get_doc(...).check_permission("read")`.
  - `download_form16`: returns file content for own record; raises `frappe.PermissionError` for another employee's record even when called by an authenticated non-HR user.
  - `list_form16`: returns only submitted docs, scoped per caller's permissions.

## Open items for implementation plan

- Exact workspace/shortcut placement for `Form 16` under whatever workspace HR users currently land on in `possibleworks`/`hrms`.
- Whether `Payroll Period` needs a fixture/seed for local dev/testing, or whether it's assumed to already exist per site.
