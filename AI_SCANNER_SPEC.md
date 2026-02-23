# AI Document Scanner — Technical Specification

> **Scope**: `apps/possibleworks/possibleworks/`  
> **App**: `possibleworks` (custom Frappe/ERPNext app)  
> **Frappe Version**: 16.x · **ERPNext Version**: 16.x  
> **Author**: AI-assisted implementation (Feb 2026)  
> **Status**: Deployed, Production-ready

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [High-Level Architecture](#2-high-level-architecture)
3. [Directory Structure](#3-directory-structure)
4. [Layer-by-Layer Breakdown](#4-layer-by-layer-breakdown)
   - 4.1 [Configuration Layer — PW AI Settings + PW AI Doctype Config](#41-configuration-layer)
   - 4.2 [Boot Layer — `config.py` + `boot_session` hook](#42-boot-layer)
   - 4.3 [Client Layer — `ai_scanner.js`](#43-client-layer)
   - 4.4 [API Layer — `scan_document.py`](#44-api-layer)
   - 4.5 [Mapper Layer — `document_mappers.py`](#45-mapper-layer)
   - 4.6 [Master Data Resolution](#46-master-data-resolution)
   - 4.7 [File Processing — PyMuPDF PDF-to-image](#47-file-processing)
   - 4.8 [OpenAI Integration — GPT-4o Vision](#48-openai-integration)
5. [Complete Data Flow](#5-complete-data-flow)
6. [Per-DocType Mapper Reference](#6-per-doctype-mapper-reference)
7. [Payment Entry — Special Architecture](#7-payment-entry--special-architecture)
8. [Master Data Resolution Algorithm](#8-master-data-resolution-algorithm)
9. [Frappe Hooks Used](#9-frappe-hooks-used)
10. [Security Design](#10-security-design)
11. [Error Handling Matrix](#11-error-handling-matrix)
12. [How to Add a New DocType](#12-how-to-add-a-new-doctype)
13. [Known Constraints and Gotchas](#13-known-constraints-and-gotchas)
14. [Glossary](#14-glossary)

---

## 1. Problem Statement

ERPNext's accounting DocTypes (Purchase Invoice, Sales Invoice, Payment Entry, etc.) require significant manual data entry when processing documents received from suppliers or customers. A team member must:
- Read a paper invoice or PDF
- Manually type supplier name, invoice number, line items, HSN codes, tax amounts, totals
- Look up the correct supplier / customer / item in ERPNext
- Manually enter payment references, cheque numbers, etc.

This is error-prone, slow, and creates duplicate master data entries when operators create new Suppliers or Items instead of matching existing ones.

**Solution**: An AI-powered document scanner that:
1. Accepts a PDF/image upload on any supported form
2. Sends it to OpenAI GPT-4o (vision model) with a doctype-specific extraction schema
3. Runs multi-tier matching against existing ERPNext master data (Supplier, Customer, Item)
4. Pre-fills the form fields — operator only needs to review and save

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ CONFIGURATION (Admin UI)                                        │
│  PW AI Settings (Single DocType)                                │
│  ├── OpenAI API Key (encrypted Password field)                  │
│  ├── Model selection (gpt-4o / gpt-4o-mini)                     │
│  ├── Auto Create Master Data toggle                             │
│  └── doctype_config (child table: PW AI Doctype Config)         │
│       ├── Purchase Invoice  [enabled] [custom prompt]           │
│       ├── Sales Invoice     [enabled] [custom prompt]           │
│       ├── Payment Entry     [enabled] [custom prompt]           │
│       └── ... (8 total)                                         │
└───────────────────────┬─────────────────────────────────────────┘
                        │ boot_session hook
                        │ embeds config into frappe.boot.pw_ai_doctypes
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ BROWSER (Frappe Desk)                                           │
│  ai_scanner.js (loaded as app_include_js)                       │
│  ├── Reads frappe.boot.pw_ai_doctypes synchronously             │
│  ├── Registers frappe.ui.form.on(doctype, { refresh })          │
│  │   for every enabled doctype                                  │
│  ├── On refresh: injects "🤖 Scan with AI" under Tools menu      │
│  ├── On click: shows Attach file dialog                         │
│  └── On upload: calls scan_document API → populates form        │
└───────────────────────┬─────────────────────────────────────────┘
                        │ frappe.call(scan_document)
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│ SERVER (Python / Frappe)                                        │
│  api/v1/scan_document.py                                        │
│  ├── Validates file extension (.pdf / .jpg / .jpeg / .png)      │
│  ├── Reads PW AI Settings (API key, model, toggles)             │
│  ├── Looks up MAPPER_REGISTRY[doctype] → mapper instance        │
│  ├── _read_file() → list of base64 image dicts                  │
│  │   ├── PDF: each page → PyMuPDF → JPEG bytes → base64         │
│  │   └── Image: file bytes → base64 (single element list)       │
│  ├── mapper.build_prompt() → doctype-specific extraction schema │
│  ├── _call_openai() → GPT-4o Vision API → raw JSON string       │
│  └── mapper.resolve_and_return() → master-data-matched dict     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Directory Structure

```
apps/possibleworks/possibleworks/
│
├── hooks.py                          ← app_include_js + boot_session hooks
│
├── ai/
│   ├── __init__.py
│   ├── config.py                     ← add_to_boot() + get_enabled_doctypes()
│   ├── document_mappers.py           ← MAPPER_REGISTRY + 8 mapper classes
│   │
│   └── doctype/
│       ├── pw_ai_settings/
│       │   ├── pw_ai_settings.json   ← Single DocType definition
│       │   └── pw_ai_settings.py     ← Validation (soft API key warning)
│       │
│       └── pw_ai_doctype_config/     ← Child Table DocType
│           ├── pw_ai_doctype_config.json
│           └── pw_ai_doctype_config.py
│
├── api/
│   └── v1/
│       ├── __init__.py
│       └── scan_document.py          ← @frappe.whitelist() entry point
│
├── patches/
│   ├── __init__.py
│   └── seed_ai_doctype_config.py     ← Migration seeder (idempotent)
│
└── public/
    └── js/
        └── ai_scanner.js             ← Single generic client script
```

> **Deleted during refactor**: `public/js/purchase_invoice.js` (was hardcoded for Purchase Invoice only)

---

## 4. Layer-by-Layer Breakdown

### 4.1 Configuration Layer

#### `PW AI Settings` (Single DocType)
**File**: `ai/doctype/pw_ai_settings/pw_ai_settings.json`

A Frappe **Single DocType** — meaning there is exactly one instance in the system (no list view). Accessed at `/app/pw-ai-settings`.

| Field | Type | Purpose |
|-------|------|---------|
| `openai_api_key` | Password | API key stored encrypted in DB (`tabSingles`) |
| `model` | Select | `gpt-4o` (default) or `gpt-4o-mini` |
| `auto_create_master_data` | Check | If OFF (default): unmatched suppliers/customers/items left blank, flagged for manual selection. If ON: creates missing records. |
| `doctype_config` | Table → PW AI Doctype Config | Child rows, one per supported DocType |
| `extraction_prompt_hint` | Small Text | Optional global suffix appended to ALL prompts |

**Why a Single DocType?**  
One API key per organization. No need for a list. Matches the pattern of other ERPNext settings pages (`HR Settings`, `Stock Settings`, etc.).

#### `PW AI Doctype Config` (Child Table DocType)
**File**: `ai/doctype/pw_ai_doctype_config/pw_ai_doctype_config.json`

| Field | Type | Purpose |
|-------|------|---------|
| `doctype_name` | Link → DocType | Which ERPNext DocType this row configures |
| `is_enabled` | Check | Show/hide button — toggled without code changes |
| `button_label` | Data | Custom label. Blank = "Scan with AI" |
| `extraction_prompt` | Code (Text) | Full custom prompt overriding the mapper default |

**Pre-seeded rows** (via `patches/seed_ai_doctype_config.py`):

```
Purchase Invoice, Sales Invoice, Purchase Order, Sales Order,
Payment Entry, Quotation, Delivery Note, Purchase Receipt
```

**Seeder is idempotent**: checks existing rows before inserting. Safe to run multiple times.

---

### 4.2 Boot Layer

**File**: `ai/config.py`  
**Hook**: `boot_session = "possibleworks.ai.config.add_to_boot"` in `hooks.py`

```python
def add_to_boot(bootinfo):
    bootinfo.pw_ai_doctypes = _get_enabled()
```

#### Why `boot_session` and not an async API call?

This is the **critical architectural decision** that makes buttons appear correctly.

**Problem with async approach** (what we tried first):
```js
// WRONG — timing issue
frappe.ready(() => {
    frappe.call("get_enabled_doctypes").then(r => {
        frappe.ui.form.on("Purchase Invoice", { refresh: ... });
        // ❌ Too late — Purchase Invoice form already mounted if user navigated directly
    });
});
```

**Why it fails**: When a user navigates to `/app/purchase-invoice/new`, Frappe mounts the form immediately on page load. Any `frappe.ui.form.on` called AFTER the form's `refresh` event has already fired will not trigger for that page load.

**Solution with `boot_session`**:
```python
# hooks.py
boot_session = "possibleworks.ai.config.add_to_boot"
```

Frappe calls all `boot_session` hooks during login/session initialization and embeds the returned data into `frappe.boot` — the JavaScript global that is populated **before any JavaScript runs** on the Desk.

```js
// CORRECT — synchronous, runs before any form opens
var ENABLED = frappe.boot.pw_ai_doctypes || {};
Object.keys(ENABLED).forEach(function(doctype) {
    frappe.ui.form.on(doctype, { refresh: function(frm) { ... } });
});
// ✅ All hooks registered synchronously before any user navigation
```

**Implication**: When an admin changes the PW AI Settings config, users need to **re-login** (or clear session cache) to pick up the updated enabled-doctype list. This is intentional and matches how ERPNext handles all boot-time configuration.

---

### 4.3 Client Layer

**File**: `public/js/ai_scanner.js`  
**Load mechanism**: `app_include_js` in `hooks.py` → bundled by `bench build` → served as `/assets/possibleworks/js/ai_scanner.js`

#### Execution Timeline
```
Frappe Desk boot
  → frappe.boot.pw_ai_doctypes available (injected by boot_session)
  → ai_scanner.js executes (IIFE — Immediately Invoked Function Expression)
  → All frappe.ui.form.on hooks registered synchronously
  → User navigates to any form
  → form.refresh fires → _injectButton() called
  → "🤖 Scan with AI" appears under Tools menu (docstatus === 0 only)
  → User clicks → _openScanDialog()
  → User uploads file → dialog.primary_action()
  → _processScan(frm, file_url)
  → frappe.call(scan_document) → server
  → callback: _populateForm(frm, data)
```

#### Key Functions

**`_injectButton(frm, cfg)`**
```js
frm.add_custom_button("🤖 Scan with AI", handler, "Tools");
frm.change_custom_button_type("🤖 Scan with AI", "Tools", "primary");
```
- Adds button under the "Tools" dropdown (⋯ menu in mobile view)
- Only shows when `frm.doc.docstatus === 0` (Draft/New documents)
- `cfg.button_label` overrides the default label

**`_populateForm(frm, data)`**  
Handles all 8 DocTypes generically:

```js
// Supplier resolution result
if (data._supplier) { frm.set_value("supplier", data._supplier.supplier); }

// Customer resolution result  
if (data._customer) { frm.set_value("customer", data._customer.customer); }

// Payment Entry special: generic party
if (data._party) {
    frm.set_value("party_type", data._party_type);
    frm.set_value("party", party_name);
    // + payment_type, paid_amount, reference_no, etc.
}

// Common header fields (mapped generically)
["bill_no", "bill_date", "due_date", "posting_date", ...].forEach(f => {
    if (data[f] && frm.fields_dict[f]) frm.set_value(f, data[f]);
});

// Items & Taxes (Applied with a deferred timeout)
// See "Known Constraints and Gotchas" below for why this timing is necessary.
setTimeout(function() {
    // apply rates and taxes here
}, 1200);
```

The `frm.fields_dict[f]` check ensures fields silently skip if they don't exist on the current DocType — **no DocType-specific branching in JS**.

---

### 4.4 API Layer

**File**: `api/v1/scan_document.py`  
**Endpoint**: `possibleworks.api.v1.scan_document.scan_document`

```python
@frappe.whitelist()
def scan_document(file_url: str, doctype: str) -> dict:
```

**Responsibilities (in order)**:
1. `_validate_file_url(file_url)` — extension whitelist: `.pdf`, `.jpg`, `.jpeg`, `.png`
2. `_get_ai_settings()` — fetches Single DocType, validates API key present, returns settings object
3. `get_mapper(doctype)` — registry lookup → raises `frappe.throw` if unsupported doctype
4. `_read_file(file_url)` — returns `list[dict]` of `{mime_type, base64_data}` (see §4.7)
5. `mapper.build_prompt(custom_prompt, global_hint)` — assembles extraction prompt
6. `_call_openai(settings, image_list, prompt)` — GPT-4o Vision call (see §4.8)
7. `mapper.resolve_and_return(raw_data, settings)` — master data resolution → final dict
8. `return resolved` — JSON-serialized back to client

---

### 4.5 Mapper Layer

**File**: `ai/document_mappers.py`

#### Registry Pattern

```python
MAPPER_REGISTRY: dict[str, type[DocumentMapper]] = {
    "Purchase Invoice": PurchaseInvoiceMapper,
    "Sales Invoice":    SalesInvoiceMapper,
    "Purchase Order":   PurchaseOrderMapper,
    "Sales Order":      SalesOrderMapper,
    "Payment Entry":    PaymentEntryMapper,
    "Quotation":        QuotationMapper,
    "Delivery Note":    DeliveryNoteMapper,
    "Purchase Receipt": PurchaseReceiptMapper,
}
```

**Why a registry and not `if/else`?**  
- O(1) lookup vs O(N) chain
- Adding a new DocType = add one class + one dict entry, **zero changes elsewhere**
- Type-safe: `MAPPER_REGISTRY[doctype]` fails loudly with a clear error if the doctype is unknown
- Easy to test each mapper class in isolation

#### Base Class

```python
class DocumentMapper:
    party_label = "Party"           # used in user messages

    def get_default_prompt(self) -> str:
        raise NotImplementedError   # each subclass defines its own schema

    def build_prompt(self, custom_prompt=None, global_hint=None) -> str:
        # Use custom_prompt from settings if provided, else get_default_prompt()
        # Append global_hint suffix if set in PW AI Settings
        ...

    def resolve_and_return(self, raw: dict, settings) -> dict:
        raise NotImplementedError   # each subclass resolves its parties + items
```

#### Shared Prompt Schema Blocks

To avoid repetition, shared JSON schema snippets are defined as module-level constants:

```python
_ITEMS_SCHEMA = """  "items": [ { "item_name": ..., "qty": ..., "rate": ..., "hsn_code": ... } ]"""
_TAXES_SCHEMA = """  "taxes": [ { "tax_type": "CGST", "rate": 9.0, "amount": 9.00 } ]"""
_TOTALS_SCHEMA = """  "total": ..., "total_taxes": ..., "grand_total": ..."""
_BASE_RULES = """RULES:\n- Dates: YYYY-MM-DD\n- Amounts: numbers not strings\n..."""
```

Each mapper's `get_default_prompt()` composes these blocks with its party-specific fields.

---

### 4.6 Master Data Resolution

All resolution helpers live in `document_mappers.py` and are **shared across all mapper classes**.

#### `_resolve_supplier(supplier_name, gstin, auto_create=False) → dict`

```
Tier 1: GSTIN (15-char GSTIN → query Supplier.tax_id)
         ↓ not found
Tier 2: Exact name (case-insensitive LIKE against supplier_name)
         ↓ not found
Tier 3: Keyword fuzzy match
         - Extract keywords from supplier_name (strip stop words like "Pvt", "Ltd", "M/s")
         - LIKE query per keyword
         - If 1 match: use it
         - If multiple: _pick_best_match() → semantic keyword overlap score
         - Present up to 5 candidates back to client in "candidates" array
         ↓ not found
Tier 4: Auto-create (only if settings.auto_create_master_data is ON)
         - If OFF: return {supplier: None, supplier_name: "...", match_type: "not_found"}
```

**Return shape** (always consistent, regardless of tier):
```json
{
  "supplier": "SUP-0001",            // null if not found
  "supplier_name": "ACME Corp",
  "match_type": "gstin|exact|fuzzy|fuzzy_multiple|created|not_found",
  "candidates": [{"name": "...", "supplier_name": "..."}],  // if multiple fuzzy
  "message": "Human-readable result description"
}
```

#### `_resolve_customer(customer_name, gstin, auto_create) → dict`

Follows the identical 4-tier pattern (GSTIN → exact → fuzzy → auto-create) as Supplier matching. Handles graceful selection of best-match existing customers or generating new ones silently into the database.

#### `_resolve_item_row(item: dict, settings) → dict`

Mutates the item dict, adding `_resolved` sub-dict:

```
Tier 1: Exact name match (item_name LIKE)
Tier 2: HSN code match (gst_hsn_code =)
         - If 1 HSN match: use it
         - If multiple HSN matches: also check name similarity → pick best
Tier 3: Keyword fuzzy (per word in item_name, LIKE query)
         _pick_best_match() by keyword overlap score
Tier 4: Auto-create Item (if settings.auto_create_master_data) or flag as not_found
```

#### `_pick_best_match(target, candidates, name_field)`

```python
def _pick_best_match(target, candidates, name_field):
    target_kw = set(w.lower() for w in _extract_keywords(target))
    # Score each candidate by keyword intersection count
    # Return highest score candidate
```

#### `_extract_keywords(name) → list[str]`

Strips typical business stop words before keyword matching:
```python
_STOP_WORDS = {
    "pvt", "ltd", "limited", "private", "inc", "llc", "llp",
    "enterprise", "enterprises", "trading", "traders", "industries",
    "company", "corporation", "solutions", "services", "technologies",
    "m/s", "ms", "mr", "mrs", "dr", "shri", "smt",
    "and", "the", "of", "for", "in", "on", "at", "to", "by"
}
```

**Example**: `"Acme Trading Pvt Ltd"` → keywords: `["Acme", "Trading"]`

---

### 4.7 File Processing

**Function**: `_read_file(file_url)` in `scan_document.py`

```python
def _read_file(file_url: str) -> list[dict]:
    file_doc = frappe.get_doc("File", {"file_url": file_url})
    file_path = file_doc.get_full_path()   # resolves to bench site files dir
    ext = file_url.rsplit(".", 1)[-1].lower()
    ...
```

#### Image files (JPG / PNG)
```python
return [{"mime_type": "image/jpeg", "base64_data": base64.b64encode(content).decode()}]
```
Returns a **single-element** list (consistent API with PDFs).

#### PDF files
```python
import fitz  # PyMuPDF

doc = fitz.open(file_path)
for i in range(min(len(doc), 5)):           # max 5 pages — cost control
    page = doc.load_page(i)
    matrix = fitz.Matrix(2.0, 2.0)          # 2× zoom = ~144 DPI (good OCR quality)
    pix = page.get_pixmap(matrix=matrix)
    img_data = pix.tobytes("jpeg", jpg_quality=85)
    images.append({"mime_type": "image/jpeg", "base64_data": base64.b64encode(img_data).decode()})
```

**Why 5-page limit?**  
GPT-4o charges per image token. A 5-page invoice = 5 × ~1000 tokens for images = ~5000 tokens input cost. Pages beyond 5 are rare on standard invoices and would push costs up significantly.

**Why 2× zoom (144 DPI)?**  
- 1× (72 DPI) is too blurry for small invoice text
- 3× (216 DPI) produces unnecessarily large files (more tokens, slower)
- 2× is the sweet spot: readable text, reasonable file size

---

### 4.8 OpenAI Integration

**Function**: `_call_openai(settings, image_list, prompt)` in `scan_document.py`

```python
content = [{"type": "text", "text": prompt}]
for img in image_list:
    content.append({
        "type": "image_url",
        "image_url": {
            "url": f"data:{img['mime_type']};base64,{img['base64_data']}",
            "detail": "high"   # ← full 2048×2048 token budget per image tile
        }
    })
response = client.chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": content}],
    max_tokens=4096,
    temperature=0.1   # ← low entropy = consistent structured output
)
```

**`detail: "high"`**: GPT-4o will resize the image into 512px tiles and process each tile. This gives maximum text accuracy for dense invoice content at a higher token cost vs `"low"`.

**`temperature: 0.1`**: Near-zero randomness. We want deterministic extraction, not creative interpretation.

**Response parsing**:
```python
raw_text = response.choices[0].message.content.strip()
raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)   # strip ```json fences
raw_text = re.sub(r"\s*```$", "", raw_text)             # strip closing ```
return json.loads(raw_text)
```
GPT-4o sometimes wraps output in markdown fences even when instructed not to — this handles both cases.

**Error handling**:
```python
except openai.AuthenticationError → frappe.throw("Invalid API key")
except openai.RateLimitError     → frappe.throw("Rate limit — retry later")
except openai.APIError           → frappe.throw(str(e))
except json.JSONDecodeError      → frappe.log_error(...) + frappe.throw("Parse error")
```

---

## 5. Complete Data Flow

```
[1] User opens /app/purchase-invoice/new
      │
[2] frappe.boot.pw_ai_doctypes already in memory (from login)
    ai_scanner.js already registered form hooks (synchronously at boot)
      │
[3] form.refresh event fires → _injectButton(frm, cfg)
    → frm.add_custom_button("🤖 Scan with AI", ..., "Tools")
      │
[4] User clicks → _openScanDialog(frm, cfg)
    → frappe.ui.Dialog with Attach field
      │
[5] User picks invoice.pdf → file uploaded as Frappe attachment
    → private/files/invoice.pdf created on disk
    → file_url = "/private/files/invoice.pdf"
      │
[6] dialog.primary_action → _processScan(frm, "/private/files/invoice.pdf")
    → frappe.call("possibleworks.api.v1.scan_document.scan_document", {
          file_url: "/private/files/invoice.pdf",
          doctype: "Purchase Invoice"
      })
      │
[7] SERVER: scan_document()
    ├─ _validate_file_url → ext = "pdf" ✅
    ├─ _get_ai_settings() → PW AI Settings loaded
    ├─ get_mapper("Purchase Invoice") → PurchaseInvoiceMapper()
    ├─ _read_file("/private/files/invoice.pdf")
    │   ├─ file_doc.get_full_path() → /path/to/bench/sites/hrms-pw.local/private/files/invoice.pdf
    │   ├─ fitz.open(path) → 3 pages
    │   └─ returns [{mime_type: jpeg, base64_data: ...}, ...] (3 items)
    ├─ mapper.build_prompt(custom_prompt=None, global_hint=settings.hint)
    │   └─ PurchaseInvoiceMapper.get_default_prompt() + global_hint
    ├─ _call_openai(settings, [3 images], prompt)
    │   ├─ content = [text_prompt, image1, image2, image3]
    │   ├─ openai.chat.completions.create(model=gpt-4o, ...)
    │   └─ returns raw JSON: {supplier_name: "ACME", bill_no: "INV-123", items: [...], ...}
    └─ mapper.resolve_and_return(raw_data, settings)
        ├─ _resolve_supplier("ACME Corp", "29ABCDE1234F1Z5")
        │   ├─ GSTIN match → found "SUP-0012" ✅
        │   └─ returns {supplier: "SUP-0012", match_type: "gstin", ...}
        ├─ _resolve_item_row({item_name: "Steel Bolt M8", hsn_code: "73181500"})
        │   ├─ Exact name → not found
        │   ├─ HSN 73181500 → found "ITM-0047 (SS Bolt M8)" ✅
        │   └─ returns item with _resolved: {item_code: "ITM-0047", match_type: "hsn"}
        └─ returns full resolved dict
      │
[8] CLIENT: _populateForm(frm, data)
    ├─ frm.set_value("supplier", "SUP-0012")
    ├─ frm.set_value("bill_no", "INV-123")
    ├─ frm.set_value("bill_date", "2026-02-15")
    ├─ frm.add_child("items") × N → set item_code, qty, rate, uom per row
    ├─ frm.add_child("taxes") × N → set charge_type, description, rate
    ├─ frm.refresh_fields() + frm.dirty()
    └─ frappe.msgprint("AI Scan Complete", messages, warnings)
```

---

## 6. Per-DocType Mapper Reference

| DocType | Class | Party | Party Field | Items | Key Extracted Fields |
|---------|-------|-------|-------------|-------|---------------------|
| Purchase Invoice | `PurchaseInvoiceMapper` | Supplier | `supplier` | ✅ | `bill_no`, `bill_date`, `due_date`, `taxes`, `grand_total` |
| Sales Invoice | `SalesInvoiceMapper` | Customer | `customer` | ✅ | `posting_date`, `due_date`, `payment_terms`, `taxes` |
| Purchase Order | `PurchaseOrderMapper` | Supplier | `supplier` | ✅ | `transaction_date`, `schedule_date`, `payment_terms` |
| Sales Order | `SalesOrderMapper` | Customer | `customer` | ✅ | `transaction_date`, `delivery_date`, `po_no`, `po_date` |
| Payment Entry | `PaymentEntryMapper` | Supplier **or** Customer | `party_type` + `party` | ❌ | `payment_type`, `paid_amount`, `received_amount`, `reference_no`, `reference_date`, `mode_of_payment`, `remarks` |
| Quotation | `QuotationMapper` | Customer/Lead | `customer` | ✅ | `valid_till`, `quotation_to` |
| Delivery Note | `DeliveryNoteMapper` | Customer | `customer` | ✅ | `lr_no`, `lr_date`, `transporter_name`, `vehicle_no` |
| Purchase Receipt | `PurchaseReceiptMapper` | Supplier | `supplier` | ✅ | `supplier_delivery_note`, `lr_no`, `lr_date` |

---

## 7. Payment Entry — Special Architecture

Payment Entry is structurally different from all other 7 DocTypes and required a dedicated design decision.

### Structural Differences

| Feature | Invoice DocTypes | Payment Entry |
|---------|-----------------|---------------|
| Party field | `supplier` or `customer` (fixed) | `party_type` + `party` (generic dynamic link) |
| Items table | ✅ | ❌ |
| Tax breakdown table | ✅ (for some) | Minimal |
| Document type uploaded | Supplier invoice / customer PO | Remittance slip / payment advice / bank receipt / cheque image |
| Primary data | Line items + totals | Single amount + reference number |

### PaymentEntryMapper Logic

```python
class PaymentEntryMapper(DocumentMapper):
    def get_default_prompt(self) -> str:
        # Instructs AI to:
        # - Determine payment_type: "Pay" (paying a supplier) vs "Receive" (receiving from customer)
        # - Note: if it's a REMITTANCE SLIP → "Pay" + "Supplier"
        # - If it's a RECEIPT / PAYMENT ACKNOWLEDGEMENT → "Receive" + "Customer"
        # - Extract: reference_no (cheque/UTR/NEFT ID), reference_date, mode_of_payment
        # - Extract: bank_name, bank_account_no (for context)
        # - paid_amount AND received_amount (may differ due to deductions)

    def resolve_and_return(self, raw, settings):
        party_type = raw.get("party_type", "Supplier")
        if party_type == "Supplier":
            resolved = _resolve_supplier(raw["party_name"], raw.get("party_gstin"), ...)
            raw["_party"] = resolved
            raw["_party_type"] = "Supplier"
        else:
            resolved = _resolve_customer(raw["party_name"], raw.get("party_gstin"))
            raw["_party"] = resolved
            raw["_party_type"] = "Customer"
        return raw
```

### Client-Side Payment Entry Handling

```js
if (data._party) {
    frm.set_value("party_type", data._party_type);      // "Supplier" or "Customer"
    frm.set_value("party", partyName);                   // resolved ERPNext name
    frm.set_value("payment_type", data.payment_type);    // "Pay" or "Receive"
    frm.set_value("paid_amount", data.paid_amount);
    frm.set_value("received_amount", data.received_amount);
    frm.set_value("reference_no", data.reference_no);   // cheque/UTR number
    frm.set_value("reference_date", data.reference_date);
    frm.set_value("mode_of_payment", data.mode_of_payment); // NEFT/RTGS/UPI/Cheque
    frm.set_value("remarks", data.remarks);
}
```

---

## 8. Master Data Resolution Algorithm

### Stop Word Removal

Before fuzzy matching, company suffixes and common words are stripped:

```
"Reliance Industries Ltd" → keywords: ["Reliance", "Industries"]
"M/s ABC Traders Pvt Ltd" → keywords: ["ABC", "Traders"]
```

This prevents false positives where "Industries Ltd" matches unrelated companies sharing common suffixes.

### Keyword Overlap Score

```
target:    "Tata Steel Limited" → {"tata", "steel"}
candidate: "Tata Steel ERPNext" → {"tata", "steel", "erpnext"}
overlap:   {"tata", "steel"} → score = 2

candidate: "Tata Iron"        → {"tata", "iron"}
overlap:   {"tata"} → score = 1

Winner: "Tata Steel ERPNext" (score 2)
```

### Candidates Array

When multiple fuzzy matches exist (score > 0 but no clear winner):
- The **best** match is still set on the form (for convenience)
- The full `candidates` array is returned in the response
- The client JS shows a warning message listing alternates
- User can change the field manually if the auto-selection is wrong

### Auto-Create Behavior

| Setting | Master Data | Result |
|---------|---------|------|
| `auto_create_master_data = 0` (default) | Supplier/Customer/Item | `supplier/customer/item_code = null`, display name populated, yellow warning banner in dialog |
| `auto_create_master_data = 1` | Supplier/Customer/Item | Creates new record silently with `ignore_permissions + ignore_mandatory` and immediately links it |

**Why does this default to OFF?**  
Creating a new Supplier, Customer, or Item from partial AI-extracted data can pollute master data with duplicate or thinly-populated records. The safe default is to let operators manually select or confirm the creation unless explicitly enabled by an Administrator.

---

## 9. Frappe Hooks Used

```python
# hooks.py

# 1. Load ai_scanner.js on every Frappe Desk page
app_include_js = [
    "/assets/possibleworks/js/whitelabel.js",
    "/assets/possibleworks/js/ai_scanner.js",
]

# 2. Inject AI config into frappe.boot at login time
boot_session = "possibleworks.ai.config.add_to_boot"

# 3. Migration patch registration
# patches.txt:
#   possibleworks.patches.seed_ai_doctype_config
```

### Hook Execution Order

```
bench start
  → User accesses /app (login)
    → Frappe calls all boot_session hooks → bootinfo.pw_ai_doctypes populated
    → Frappe serializes bootinfo → JSON → <script>frappe.boot = {...}</script>
    → Browser loads Desk
    → app_include_js scripts execute
      → ai_scanner.js reads frappe.boot.pw_ai_doctypes
      → registers frappe.ui.form.on for each enabled doctype
    → User navigates to any enabled form
    → refresh event → button injected
```

---

## 10. Security Design

| Concern | Implementation |
|---------|---------------|
| API key storage | Frappe `Password` field type → stored AES-encrypted in `tabSingles` |
| API key retrieval | `settings.get_password("openai_api_key")` → decrypted on demand, never logged |
| API key validation | Soft warning (not hard throw) to avoid breaking saves when encrypted value is checked |
| Access control | `PW AI Settings` uses `permissions: [{ role: "System Manager" }]` |
| Endpoint protection | `@frappe.whitelist()` requires valid Frappe session (cookie-based auth) |
| File access | `frappe.get_doc("File", {"file_url": file_url})` → only files the current user can access |
| Prompt injection | Prompts are constructed server-side; user can only provide `file_url` and `doctype` |
| Auto-create safety | Defaults to OFF to prevent polluting master data |
| No core modifications | Zero changes to `frappe`, `erpnext`, or `hrms` app code |

---

## 11. Error Handling Matrix

| Error | Where caught | User message |
|-------|-------------|--------------|
| Unsupported file extension | `_validate_file_url()` | "Unsupported file type. Allowed: PDF, JPG, PNG" |
| API key not configured | `_get_ai_settings()` | "OpenAI API key not configured. Go to PW AI Settings." |
| Unsupported DocType | `get_mapper()` | "AI scanning not configured for DocType: X" |
| PyMuPDF not installed | `_read_file()` | "PyMuPDF is required. Run: bench pip install PyMuPDF" |
| OpenAI auth failure | `_call_openai()` | "Invalid OpenAI API key." |
| OpenAI rate limit | `_call_openai()` | "Rate limited. Please wait and retry." |
| OpenAI API error | `_call_openai()` | str(exception) |
| JSON parse error | `_call_openai()` | "AI returned invalid data. Try again with a clearer image." + `frappe.log_error` |
| Supplier not found | `_resolve_supplier()` | Warning in summary dialog; field left blank |
| Item not found | `_resolve_item_row()` | Warning in summary dialog; `item_name` set, `item_code` blank |

---

## 12. How to Add a New DocType

Adding a new DocType (e.g., "Expense Claim") requires exactly **3 steps**:

### Step 1: Create a Mapper Class

In `ai/document_mappers.py`:

```python
class ExpenseClaimMapper(DocumentMapper):
    party_label = "Employee"

    def get_default_prompt(self) -> str:
        return f"""You are extracting data from an Expense Claim document or receipt.

{_BASE_RULES}

Extract into this exact JSON:
{{
  "employee_name": "Employee name",
  "claim_date": "YYYY-MM-DD",
  "total_claimed_amount": 0.00,
  "expenses": [
    {{
      "expense_date": "YYYY-MM-DD",
      "expense_type": "Travel or Accommodation or Meals etc.",
      "description": "Description",
      "amount": 0.00
    }}
  ]
}}"""

    def resolve_and_return(self, raw: dict, settings) -> dict:
        # Set employee, map expense rows, etc.
        return raw
```

### Step 2: Add to Registry

```python
MAPPER_REGISTRY: dict[str, type[DocumentMapper]] = {
    ...existing entries...,
    "Expense Claim": ExpenseClaimMapper,   # ← add this line
}
```

### Step 3: Add Row in PW AI Settings

Navigate to PW AI Settings → DocType Configuration → Add row:
- DocType: `Expense Claim`
- Enabled: ✅
- Button Label: (or leave blank)

On next user login, the button will appear on Expense Claim new forms automatically.

> **No JS changes. No hooks changes. No migrations.** Just Python + UI.

---

## 13. Known Constraints and Gotchas

### Config refresh requires re-login
The `frappe.boot.pw_ai_doctypes` dict is embedded at login time. If an admin enables/disables a doctype in PW AI Settings, users must re-login (or use "Clear Cache" from the Frappe Settings menu) to see the updated button visibility.

**Workaround**: Admin can immediately see changes by opening an Incognito window and logging in fresh.

### PDF page limit (5 pages max)
The scanner processes the first 5 pages. Long documents (catalogues, multi-order PDFs) will only extract data from pages 1–5. This is a deliberate cost-control decision.

**Mitigation**: Users should upload individual invoice pages rather than batches.

### GPT-4o has no schema enforcement
The model is instructed to return JSON but can hallucinate or mis-format in edge cases. The JSON parse error handler catches these and logs them to `frappe.log_error` for debugging.

**Best practice**: Check "Error Log" in Frappe if scan returns an unexpected error.

### Auto-create with `ignore_mandatory = True`
When auto-creating Supplier/Item, mandatory fields (like `default_currency` for Supplier) are bypassed. The created record may require manual completion in its own form before being fully usable in transactions.

### `frm.change_custom_button_type` may fail silently
This styles the button as "primary" (blue). If Frappe changes its internal button type API, this call is wrapped in try/catch and fails silently — button still appears but unstyled. The `/* non-critical */` comment marks this intent.

### Async Trigger Overwrites (Rates & Taxes)
**The Problem**: Setting `item_code` triggers ERPNext to async-fetch the Item Price. Setting `supplier` triggers ERPNext to async-fetch the Default Tax Template. Both server calls return ~800ms later and OVERWRITE the AI's extracted `rate` and `taxes` values.
**The Fix**: `ai_scanner.js` sets the basic item linkage, explicitly blanks `frm.doc.taxes_and_charges`, and sets a `setTimeout(..., 1200)`. 1.2 seconds later, the script wakes up, clears whatever default template ERPNext generated, and forcefully re-applies whatever rates and taxes the AI actually OCR'd off the document.

### `bench pip install` vs `pip install`
Always use `bench pip install PyMuPDF openai` (not plain `pip install`) to install into the correct virtualenv associated with the Frappe bench. Plain `pip install` will install into the system Python and the bench server won't find it.

---

## 14. Glossary

| Term | Definition |
|------|-----------|
| **Single DocType** | A Frappe DocType with only one instance (no list). Stored in `tabSingles` rather than a named table. |
| **Child Table DocType** | A DocType with `istable: 1`. Rows appear inline inside a parent DocType's form. Stored in its own table with `parent`, `parenttype`, `parentfield` columns. |
| **boot_session hook** | A Frappe hook called during session initialization. The function receives a `bootinfo` object and can add arbitrary data to it, which is then serialized into `frappe.boot` in the client browser. |
| **app_include_js** | A Frappe hook that includes a JS file on every Frappe Desk page. Bundled by `bench build`. |
| **MAPPER_REGISTRY** | A Python dict `{doctype_name: MapperClass}` used for O(1) doctype-to-class lookup. |
| **DocumentMapper** | Base class defining the interface (`get_default_prompt`, `build_prompt`, `resolve_and_return`) that all per-doctype mapper classes implement. |
| **GPT-4o Vision** | OpenAI's multimodal model that accepts both text and image inputs. Used here with `detail: "high"` for maximum text accuracy on invoice images. |
| **PyMuPDF (fitz)** | Python library for PDF processing. Used to rasterize PDF pages into JPEG images before sending to OpenAI (the Vision API does not accept raw PDF bytes). |
| **Multi-tier matching** | The fallback sequence for supplier/customer/item resolution: GSTIN → exact name → fuzzy keyword → auto-create or flag. |
| **GSTIN** | Goods and Services Tax Identification Number (India). 15-character alphanumeric. The most reliable identifier for matching Indian suppliers/customers. |
| **HSN code** | Harmonized System Nomenclature code. 4–8 digit code used in India to classify goods for GST. Used as a secondary tier for item matching. |
| **`frappe.throw`** | Raises an exception in Python that Frappe converts to an HTTP 417 error response with a user-friendly message shown in a red dialog on the client. |
| **`@frappe.whitelist()`** | Decorator that marks a Python function as callable from the browser via `frappe.call()`. Without it, the function is inaccessible from the client. |
| **`ignore_permissions`** | A Frappe flag that bypasses permission checks for a single document operation. Used in auto-create to avoid role-based access issues. |
| **`ignore_mandatory`** | A Frappe flag that bypasses mandatory field validation. Used carefully in auto-create to allow minimal record creation. |
