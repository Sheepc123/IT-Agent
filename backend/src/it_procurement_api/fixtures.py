"""Load synthetic fixture sets (docs/种子评测规格.md, section 3) into PostgreSQL.

    uv run python -m it_procurement_api.fixtures FX-A-BASE

Loading is repeatable: every row id is derived from its fixture key, and a load
first removes all rows of the demo tenant. All data here is synthetic.
"""

import json
import sys
import uuid
from collections import Counter
from datetime import datetime
from hashlib import sha256

from sqlalchemy import delete, select, text, update
from sqlalchemy.orm import Session

from .db import new_session
from .db.models import (
    AccessScope,
    Asset,
    Base,
    Principal,
    ProductModel,
    PurchaseRequest,
    QuoteLine,
    QuoteVersion,
    RoleGrant,
    Sku,
    SourceBatch,
    Supplier,
)
from .settings import REPO_ROOT

FIXTURES_DIR = REPO_ROOT / "database" / "fixtures"
NAMESPACE = uuid.UUID("6f0e1c1e-5a0b-4a52-9d0c-3b8a5c2e7f10")
IMPORTER = "SVC-IMPORT"
# Parsed vendor documents are real source material, not fixtures: a fixture reset keeps them.
NOT_FIXTURE_TABLES = frozenset({"document_version", "document_chunk"})


class FixtureError(ValueError):
    pass


def fixture_id(kind: str, key: str) -> uuid.UUID:
    """Stable id for a fixture row, e.g. fixture_id("asset", "AST-0001")."""
    return uuid.uuid5(NAMESPACE, f"{kind}:{key}")


TENANT_ID = fixture_id("tenant", "demo")


def read_sets() -> dict:
    return json.loads((FIXTURES_DIR / "sets.json").read_text(encoding="utf-8"))


def read_part(name: str) -> tuple[dict, str]:
    raw = (FIXTURES_DIR / f"{name}.json").read_bytes()
    return json.loads(raw), sha256(raw).hexdigest()


def _is_money(value) -> bool:
    return value is None or (type(value) is int and value >= 0)


def _aware(value: str) -> datetime:
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError(f"timestamp without time zone: {value}")
    return moment


def validate(parts: dict[str, dict], expect: dict | None = None) -> list[str]:
    """Quality checks before anything is written. Returns a list of problems."""
    problems: list[str] = []
    merged: dict[str, list] = {}
    for data in parts.values():
        for key, value in data.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)

    def keys(kind: str, field: str) -> set[str]:
        values = [row[field] for row in merged.get(kind, [])]
        for value in {v for v in values if values.count(v) > 1}:
            problems.append(f"duplicate {kind} {value}")
        return set(values)

    departments = set(merged.get("departments", []))
    principals = keys("principals", "key")
    models = keys("models", "model_code")
    skus = keys("skus", "sku_code")
    suppliers = keys("suppliers", "code")
    keys("assets", "asset_tag")
    keys("quotes", "quote_key")
    scopes = {"ORG"} | {f"DEPT:{d}" for d in departments}

    for p in merged.get("principals", []):
        if p["department"] is not None and p["department"] not in departments:
            problems.append(f"principal {p['key']}: unknown department {p['department']}")
        for role, scope in p["grants"]:
            if scope not in scopes:
                problems.append(f"principal {p['key']}: unknown scope {scope}")
    for s in merged.get("skus", []):
        if not s["sku_code"].startswith("SIM-"):
            problems.append(f"sku {s['sku_code']}: synthetic SKU codes must start with SIM-")
        if s["model_code"] not in models:
            problems.append(f"sku {s['sku_code']}: unknown model {s['model_code']}")
    for a in merged.get("assets", []):
        if a["sku"] not in skus:
            problems.append(f"asset {a['asset_tag']}: unknown sku {a['sku']}")
        if a["department"] not in departments:
            problems.append(f"asset {a['asset_tag']}: unknown department {a['department']}")
        if a["assigned_to"] is not None and a["assigned_to"] not in principals:
            problems.append(f"asset {a['asset_tag']}: unknown principal {a['assigned_to']}")
        if (a["status"] == "IN_USE") != (a["assigned_to"] is not None):
            problems.append(f"asset {a['asset_tag']}: IN_USE and assigned_to must go together")
    for q in merged.get("quotes", []):
        name = f"quote {q['quote_key']}"
        if q["supplier"] not in suppliers:
            problems.append(f"{name}: unknown supplier {q['supplier']}")
        if not _is_money(q["shipping_minor"]):
            problems.append(f"{name}: shipping_minor must be a non-negative integer or null")
        try:
            if not _aware(q["valid_from"]) < _aware(q["valid_until"]):
                problems.append(f"{name}: valid_from must be before valid_until")
            _aware(q["delivery_at"])
        except ValueError as exc:
            problems.append(f"{name}: {exc}")
        for line in q["lines"]:
            if line["sku"] not in skus:
                problems.append(f"{name}: unknown sku {line['sku']}")
            if not _is_money(line["unit_price_minor"]):
                problems.append(f"{name}: unit_price_minor must be a non-negative integer or null")
            if not _is_money(line["available_quantity"]):
                problems.append(f"{name}: available_quantity must be a non-negative integer or null")

    if expect and not problems:
        available = sum(1 for a in merged.get("assets", []) if a["status"] == "AVAILABLE")
        if available != expect["available_assets"]:
            problems.append(f"expected {expect['available_assets']} available assets, found {available}")
        quotes = {q["quote_key"]: q for q in merged.get("quotes", [])}
        for key, total in expect["quote_totals"].items():
            quote = quotes.get(key)
            if quote is None:
                problems.append(f"expected quote {key} is missing")
                continue
            actual = quote["lines"][0]["unit_price_minor"] * expect["quantity"] + quote["shipping_minor"]
            if actual != total:
                problems.append(f"quote {key}: total for {expect['quantity']} is {actual}, expected {total}")
    return problems


def reset_tenant(session: Session) -> None:
    """Remove every row of the demo tenant, including append-only events."""
    session.flush()
    session.expunge_all()  # objects loaded before the reset no longer exist
    session.execute(
        update(PurchaseRequest).where(PurchaseRequest.tenant_id == TENANT_ID).values(current_revision_id=None)
    )
    session.execute(text("ALTER TABLE business_event DISABLE TRIGGER business_event_no_update_delete"))
    for table in reversed(Base.metadata.sorted_tables):
        if table.name in NOT_FIXTURE_TABLES:
            continue
        session.execute(delete(table).where(table.c.tenant_id == TENANT_ID))
    session.execute(text("ALTER TABLE business_event ENABLE TRIGGER business_event_no_update_delete"))


def _insert(session: Session, data: dict, digest: str, captured_at: datetime, counts: Counter) -> None:
    base = dict(tenant_id=TENANT_ID, created_by=fixture_id("principal", IMPORTER))

    def add(*rows) -> None:
        session.add_all(rows)
        session.flush()
        counts.update(type(row).__tablename__ for row in rows)

    if "principals" in data:
        add(*[
            Principal(
                id=fixture_id("principal", p["key"]), kind=p["kind"], display_name=p["key"],
                department_id=fixture_id("department", p["department"]) if p["department"] else None, **base,
            )
            for p in data["principals"]
        ])
        add(
            AccessScope(id=fixture_id("scope", "ORG"), kind="ORG", **base),
            *[
                AccessScope(
                    id=fixture_id("scope", f"DEPT:{d}"), kind="DEPARTMENT",
                    department_id=fixture_id("department", d), **base,
                )
                for d in data["departments"]
            ],
        )
        add(*[
            RoleGrant(
                id=fixture_id("grant", f"{p['key']}:{role}:{scope}"),
                principal_id=fixture_id("principal", p["key"]), scope_id=fixture_id("scope", scope),
                role=role, valid_from=captured_at, **base,
            )
            for p in data["principals"]
            for role, scope in p["grants"]
        ])
        return

    batch_id = fixture_id("batch", data["source_name"])
    add(SourceBatch(
        id=batch_id, source_type="SYNTHETIC", source_name=data["source_name"], captured_at=captured_at,
        content_hash=digest, quality_status="PASSED", **base,
    ))
    add(*[
        ProductModel(
            id=fixture_id("model", m["model_code"]), manufacturer=m["manufacturer"], model_code=m["model_code"],
            category=m["category"], source_batch_id=batch_id, **base,
        )
        for m in data.get("models", [])
    ])
    add(*[
        Sku(
            id=fixture_id("sku", s["sku_code"]), model_id=fixture_id("model", s["model_code"]),
            sku_code=s["sku_code"], hardware_revision=s["hardware_revision"], specs=s["specs"],
            source_batch_id=batch_id, catalog_kind="SYNTHETIC", review_status="VERIFIED", **base,
        )
        for s in data.get("skus", [])
    ])
    for a in data.get("assets", []):
        sku = session.get(Sku, fixture_id("sku", a["sku"]))
        add(Asset(
            id=fixture_id("asset", a["asset_tag"]), sku_id=sku.id,
            scope_id=fixture_id("scope", f"DEPT:{a['department']}"), asset_tag=a["asset_tag"],
            serial_no=a["serial_no"], kind="DEVICE", lifecycle_status=a["status"], config_snapshot=sku.specs,
            location=a["location"],
            assigned_to=fixture_id("principal", a["assigned_to"]) if a["assigned_to"] else None, **base,
        ))
    add(*[
        Supplier(id=fixture_id("supplier", s["code"]), supplier_code=s["code"], name=s["name"], **base)
        for s in data.get("suppliers", [])
    ])
    for q in data.get("quotes", []):
        quote_id = fixture_id("quote", q["quote_key"])
        add(QuoteVersion(
            id=quote_id, supplier_id=fixture_id("supplier", q["supplier"]), scope_id=fixture_id("scope", "ORG"),
            quote_key=q["quote_key"], revision=1, currency=q["currency"], price_basis=q["price_basis"],
            shipping_minor=q["shipping_minor"], valid_from=_aware(q["valid_from"]),
            valid_until=_aware(q["valid_until"]), delivery_at=_aware(q["delivery_at"]),
            warranty_terms={"months": q["warranty_months"]}, source_batch_id=batch_id, content_hash=digest,
            channel_kind="SIMULATED_STORE", channel_name=q["channel_name"], market_data_kind="SYNTHETIC",
            captured_at=captured_at, **base,
        ))
        add(*[
            QuoteLine(
                id=fixture_id("quote_line", f"{q['quote_key']}:{line['sku']}"), quote_version_id=quote_id,
                sku_id=fixture_id("sku", line["sku"]), unit_price_minor=line["unit_price_minor"],
                tax_included=line["tax_included"], available_quantity=line["available_quantity"],
                stock_as_of=captured_at if line["available_quantity"] is not None else None,
                listing_key=f"{q['quote_key']}/{line['sku']}", **base,
            )
            for line in q["lines"]
        ])


def load_fixture_set(session: Session, name: str) -> dict[str, int]:
    """Validate, reset the demo tenant, insert the set. Returns row counts per table.

    The caller owns the transaction (commit or roll back).
    """
    sets = read_sets()
    if name not in sets["sets"]:
        raise FixtureError(f"unknown fixture set {name}; known: {', '.join(sorted(sets['sets']))}")
    spec = sets["sets"][name]
    parts = {part: read_part(part) for part in spec["parts"]}
    problems = validate({part: data for part, (data, _) in parts.items()}, spec.get("expect"))
    if problems:
        raise FixtureError(f"{name} failed quality checks: " + "; ".join(problems))

    reset_tenant(session)
    counts: Counter = Counter()
    captured_at = _aware(sets["captured_at"])
    for data, digest in parts.values():
        _insert(session, data, digest, captured_at, counts)
    return dict(counts)


def loaded_counts(session: Session) -> dict[str, int]:
    """Row counts of the demo tenant per table, for checks."""
    return {
        table.name: session.execute(
            select(text("count(*)")).select_from(table).where(table.c.tenant_id == TENANT_ID)
        ).scalar_one()
        for table in Base.metadata.sorted_tables
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m it_procurement_api.fixtures <SET>, e.g. FX-A-BASE")
    with new_session() as session:
        counts = load_fixture_set(session, sys.argv[1])
        session.commit()
    print(f"loaded {sys.argv[1]} (synthetic):", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))


if __name__ == "__main__":
    main()
