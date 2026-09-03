"""Derive Purchase Indent / Purchase Indent Item from ERPNext's Material Request pair.

Generated rather than hand-written so the clone is field-for-field faithful. Re-run from
an activated bench env after an ERPNext upgrade to diff what upstream changed:

    python apps/possibleworks/possibleworks/finance/doctype/purchase_indent/\\
        _generate_from_material_request.py

Leading underscore so Frappe's doctype loader ignores it -- it imports only
`{doctype}/{doctype}.py`.
"""

import copy
import importlib.util
import json
import os


def _erpnext_path():
    """Locate the installed erpnext package rather than assuming a bench layout.

    Resolved through the import system so this works on any bench, in a container, and
    wherever apps live outside the conventional `apps/<app>` directory.
    """
    spec = importlib.util.find_spec("erpnext")
    if not spec or not spec.origin:
        raise SystemExit("erpnext is not importable -- activate the bench env first.")
    return os.path.dirname(spec.origin)


ERPNEXT = _erpnext_path()

# This file lives in the purchase_indent folder, so its parent is the doctype root that
# both generated doctypes are written under.
OUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixed, not `now()`: a stable creation/modified stamp keeps a re-run from churning the
# generated JSON when nothing upstream actually changed.
STAMP = "2026-09-03 10:00:00.000000"


def load(p):
    with open(p) as f:
        return json.load(f)


def fld(fields, name):
    for f in fields:
        if f["fieldname"] == name:
            return f
    raise KeyError(name)


def insert_after(doc, anchor, new_fields):
    """Insert into both `fields` and `field_order`, keeping them in lockstep."""
    fields, order = doc["fields"], doc["field_order"]
    fi = next(i for i, f in enumerate(fields) if f["fieldname"] == anchor) + 1
    oi = order.index(anchor) + 1
    for offset, nf in enumerate(new_fields):
        fields.insert(fi + offset, nf)
        order.insert(oi + offset, nf["fieldname"])


def drop(doc, name):
    doc["fields"] = [f for f in doc["fields"] if f["fieldname"] != name]
    doc["field_order"] = [f for f in doc["field_order"] if f != name]


def rebrand(doc, name, module="Finance"):
    doc.update({
        "name": name, "module": module, "creation": STAMP, "modified": STAMP,
        "owner": "Administrator", "modified_by": "Administrator", "idx": 0,
        "actions": [], "links": [],
    })


# ---------------------------------------------------------------- parent
pi = copy.deepcopy(load(f"{ERPNEXT}/stock/doctype/material_request/material_request.json"))
rebrand(pi, "Purchase Indent")

# The one field the requirement removes. Everything below is fallout from it.
drop(pi, "material_request_type")

f = fld(pi["fields"], "title")
f["default"] = "Purchase Indent"                       # was "{material_request_type}"

# Both were shown only for purposes a purchase indent can never have. Kept in the
# schema (so the clone stays field-complete) but hidden, since their display
# condition referenced the removed field and would otherwise be always-true.
for name in ("customer", "set_from_warehouse"):
    f = fld(pi["fields"], name)
    f.pop("depends_on", None)
    f["hidden"] = 1

fld(pi["fields"], "naming_series")["options"] = "PUR-IND-.YYYY.-"
fld(pi["fields"], "items")["options"] = "Purchase Indent Item"
fld(pi["fields"], "amended_from")["options"] = "Purchase Indent"

# Standard equivalents of the two Material Request custom fields on this bench.
insert_after(pi, "company", [
    {"fieldname": "department", "fieldtype": "Link", "label": "Department", "options": "Department"},
    {"fieldname": "purpose_note", "fieldtype": "Small Text", "label": "Purpose"},
])

# ---------------------------------------------------------------- child
pii = copy.deepcopy(load(f"{ERPNEXT}/stock/doctype/material_request_item/material_request_item.json"))
rebrand(pii, "Purchase Indent Item")

# Condition was `in_list(["Manufacture", "Purchase"], parent.material_request_type)`.
# A purchase indent is implicitly Purchase, so the branch is always taken.
fld(pii["fields"], "manufacture_details").pop("depends_on", None)

# Was Material-Transfer-only -- unreachable here.
f = fld(pii["fields"], "from_warehouse")
f.pop("depends_on", None)
f["hidden"] = 1

# Standard equivalents of the three Material Request Item custom fields.
insert_after(pii, "description", [
    {"fieldname": "mis", "fieldtype": "Link", "label": "MIS", "options": "MIS Master"},
    {"fieldname": "area_of_application", "fieldtype": "Data", "label": "Area of Application"},
    {"fieldname": "additional_details", "fieldtype": "Small Text", "label": "Additional Details"},
])

# Back-links to the row this was pulled from, grouped with the other source-doc links.
insert_after(pii, "sales_order_item", [
    {"fieldname": "material_request", "fieldtype": "Link", "label": "Material Request",
     "options": "Material Request", "read_only": 1, "search_index": 1},
    {"fieldname": "material_request_item", "fieldtype": "Data", "label": "Material Request Item",
     "read_only": 1, "hidden": 1},
])

# Material Request relies on erpnext's client-side get_item_details to fill these,
# which needs a supplier/price-list context a plain indent has no reason to carry.
# Declarative fetches do the same job with no JS and also cover hand-typed rows.
# item_name/description are fetch_if_empty so a user's edit is not overwritten on save.
for name, source, if_empty in (
    ("item_name", "item_code.item_name", 1),
    ("description", "item_code.description", 1),
    ("stock_uom", "item_code.stock_uom", 0),
    ("item_group", "item_code.item_group", 0),
    ("brand", "item_code.brand", 0),
):
    f = fld(pii["fields"], name)
    f["fetch_from"] = source
    if if_empty:
        f["fetch_if_empty"] = 1

# ---------------------------------------------------------------- write
for folder, doc in (("purchase_indent", pi), ("purchase_indent_item", pii)):
    d = os.path.join(OUT, folder)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "__init__.py"), "a").close()
    with open(os.path.join(d, f"{folder}.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")

print(f"Purchase Indent      : {len(pi['fields'])} fields (MR had 40)")
print(f"Purchase Indent Item : {len(pii['fields'])} fields (MRI had 61)")
for doc, lbl in ((pi, "PI"), (pii, "PII")):
    assert len(doc["fields"]) == len(doc["field_order"]), f"{lbl} fields/field_order out of sync"
    assert {f["fieldname"] for f in doc["fields"]} == set(doc["field_order"]), f"{lbl} name mismatch"
    leftover = [f["fieldname"] for f in doc["fields"]
                if any(isinstance(v, str) and "material_request_type" in v for v in f.values())]
    assert not leftover, f"{lbl} still references material_request_type: {leftover}"
print("field_order in sync; no lingering material_request_type references")
