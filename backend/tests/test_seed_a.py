"""Seed cases SEED-A01, A02, A03, A06 (docs/种子评测用例明细.md) through the API.

Synthetic data, fixed clock, rolled back after each test. The form path must
make zero model calls and must not create plans, reviews or orders.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from it_procurement_api import clock
from it_procurement_api.db.models import BusinessEvent, PlanVersion, PurchaseRequest, TaskRun
from it_procurement_api.fixtures import TENANT_ID, load_fixture_set, read_sets

TZ = timezone(timedelta(hours=8))
T0 = datetime(2026, 11, 12, 12, 0, tzinfo=TZ)
URL = "/api/v1/requests"


def as_user(key: str) -> dict:
    return {"X-Demo-Principal": key}


def form(**overrides) -> dict:
    body = {k: v for k, v in read_sets()["sets"]["FX-A-BASE"]["request"].items() if k != "requester"}
    body.update(overrides)
    return body


def analyse(client, at: datetime = T0, **overrides) -> dict:
    """U-REQ-01 creates and submits the form; U-BUY-01 runs the analysis and reads the result."""
    with clock.frozen(at):
        created = client.post(URL, json=form(**overrides), headers=as_user("U-REQ-01"))
        assert created.status_code == 201, created.text
        request_id = created.json()["data"]["id"]
        submitted = client.post(f"{URL}/{request_id}/submit", headers=as_user("U-REQ-01"))
        assert submitted.status_code == 200, submitted.text
        started = client.post(f"{URL}/{request_id}/analysis-runs", headers=as_user("U-BUY-01"))
        assert started.status_code == 202, started.text
        result = client.get(started.json()["data"]["status_url"], headers=as_user("U-BUY-01"))
        assert result.status_code == 200, result.text
        return result.json()["data"]


def reasons(entries: list[dict], key: str) -> dict[str, str]:
    return {entry[key]: entry["reason"] for entry in entries}


def count(session, model) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.tenant_id == TENANT_ID))


# --- SEED-A01 -------------------------------------------------------------------


def test_seed_a01_baseline(session, client):
    load_fixture_set(session, "FX-A-BASE")
    data = analyse(client)

    assert data["reused_assets"] == ["AST-0001", "AST-0002", "AST-0003"]
    assert reasons(data["excluded_assets"], "asset_tag") == {"AST-0004": "IN_USE", "AST-0005": "IN_USE"}
    assert data["gap"] == 7
    assert data["result_status"] == "READY"
    assert [(i["quote_key"], i["total_minor"]) for i in data["items"]] == [("Q-A", "5350000"), ("Q-C", "5510000")]
    assert reasons(data["excluded_items"], "quote_key") == {"Q-B": "EXPIRED"}
    assert data["rule_version"] == "RULE-DEMO-1"
    assert data["data_as_of"] == "2026-11-12T12:00:00+08:00"
    assert data["source_labels"] == {"market_data": "SYNTHETIC", "channel": "SIMULATED_STORE"}

    # cost and side effects: form path, no model, no plan
    assert data["status"] == "SUCCEEDED" and data["mode"] == "WORKFLOW" and data["input_kind"] == "FORM"
    assert data["model_call_count"] == 0
    assert count(session, PlanVersion) == 0
    assert count(session, TaskRun) == 1
    request = session.scalars(select(PurchaseRequest).where(PurchaseRequest.tenant_id == TENANT_ID)).one()
    assert request.status == "SUBMITTED"
    events = session.scalars(
        select(BusinessEvent.event_type).where(BusinessEvent.aggregate_id == request.id).order_by(BusinessEvent.sequence)
    ).all()
    assert events == ["REQUEST_CREATED", "REQUEST_SUBMITTED"]


# --- SEED-A02 -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variant", "fixture_set", "at", "first", "status", "excluded"),
    [
        ("v1", "FX-A-BASE", datetime(2026, 11, 19, 23, 59, 59, tzinfo=TZ), ("Q-A", "5350000"), "READY", {}),
        ("v2", "FX-A-BASE", datetime(2026, 11, 20, 0, 0, tzinfo=TZ), None, "NEEDS_QUOTE", {"Q-A": "EXPIRED", "Q-C": "EXPIRED"}),
        ("v3", "FX-A-QUOTEVAR", T0, ("Q-A", "5350000"), "READY", {"Q-D": "NOT_YET_VALID"}),
        ("v4", "FX-A-QUOTEVAR", datetime(2026, 11, 15, 0, 0, tzinfo=TZ), ("Q-D", "4950000"), "READY", {}),
        ("v5", "FX-A-QUOTEVAR", T0, ("Q-A", "5350000"), "READY", {"Q-E": "NOT_COMPARABLE"}),
    ],
)
def test_seed_a02_quote_validity_and_comparability(session, client, variant, fixture_set, at, first, status, excluded):
    load_fixture_set(session, fixture_set)
    data = analyse(client, at)
    assert data["result_status"] == status
    if first is None:
        assert data["items"] == []
    else:
        assert (data["items"][0]["quote_key"], data["items"][0]["total_minor"]) == first
    actual = reasons(data["excluded_items"], "quote_key")
    assert {key: actual.get(key) for key in excluded} == excluded
    assert data["model_call_count"] == 0


# --- SEED-A03 -------------------------------------------------------------------


@pytest.mark.parametrize("field", ["quantity", "budget_minor", "required_by"])
def test_seed_a03_missing_field_keeps_the_draft(session, client, field):
    load_fixture_set(session, "FX-A-BASE")
    with clock.frozen(T0):
        created = client.post(URL, json=form(purpose="直接帮我买", **{field: None}), headers=as_user("U-REQ-01"))
        assert created.status_code == 201
        draft = created.json()["data"]
        assert draft["status"] == "DRAFT" and draft["missing_fields"] == [field] and draft[field] is None

        submitted = client.post(f"{URL}/{draft['id']}/submit", headers=as_user("U-REQ-01"))
        assert submitted.status_code == 422
        error = submitted.json()["error"]
        assert error["code"] == "MISSING_REQUIRED_FIELDS" and error["details"] == {"fields": [field]}

        # still a draft, the missing value was not filled in, and it cannot be analysed
        again = client.get(f"{URL}/{draft['id']}", headers=as_user("U-REQ-01")).json()["data"]
        assert again["status"] == "DRAFT" and again[field] is None
        blocked = client.post(f"{URL}/{draft['id']}/analysis-runs", headers=as_user("U-BUY-01"))
        assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "INVALID_STATE"
    assert count(session, TaskRun) == 0 and count(session, PlanVersion) == 0


# --- SEED-A06 -------------------------------------------------------------------


def test_seed_a06_v1_stock_expiry_and_unknown_values(session, client):
    load_fixture_set(session, "FX-A-QUOTEVAR")
    data = analyse(client)
    assert [i["quote_key"] for i in data["items"]] == ["Q-A", "Q-C"]
    assert reasons(data["excluded_items"], "quote_key") == {
        "Q-B": "EXPIRED", "Q-D": "NOT_YET_VALID", "Q-E": "NOT_COMPARABLE",
        "Q-K": "INSUFFICIENT_STOCK", "Q-L": "OUT_OF_STOCK",
    }
    assert reasons(data["pending_items"], "quote_key") == {"Q-N": "PRICE_UNKNOWN", "Q-S": "STOCK_UNKNOWN"}
    assert data["model_call_count"] == 0


def test_seed_a06_v2_no_matching_model(session, client):
    load_fixture_set(session, "FX-A-QUOTEVAR")
    data = analyse(client, minimum_specs={"memory_gib": 64, "storage_bytes": 1_000_000_000_000})
    assert data["result_status"] == "NO_MATCH"
    assert data["items"] == [] and data["pending_items"] == []
    assert data["reused_assets"] == [] and data["gap"] == 10


# --- access ---------------------------------------------------------------------


def test_identity_and_scope_are_enforced(session, client):
    load_fixture_set(session, "FX-A-BASE")
    with clock.frozen(T0):
        assert client.post(URL, json=form()).status_code == 401
        assert client.post(URL, json=form(), headers=as_user("U-NOBODY")).status_code == 401
        assert client.post(URL, json=form(), headers=as_user("SVC-WF")).status_code == 401
        assert client.post(URL, json=form(), headers=as_user("U-BUY-01")).status_code == 403
        # a client cannot choose its own identity or status in the body
        assert client.post(URL, json=form(requester_id="x"), headers=as_user("U-REQ-01")).status_code == 422

        request_id = client.post(URL, json=form(), headers=as_user("U-REQ-01")).json()["data"]["id"]
        client.post(f"{URL}/{request_id}/submit", headers=as_user("U-REQ-01"))
        run_url = client.post(f"{URL}/{request_id}/analysis-runs", headers=as_user("U-BUY-01")).json()["data"]["status_url"]

        for outsider in ("U-REQ-02", "U-BUY-02"):  # another department: indistinguishable from "does not exist"
            for response in (
                client.get(f"{URL}/{request_id}", headers=as_user(outsider)),
                client.get(run_url, headers=as_user(outsider)),
                client.post(f"{URL}/{request_id}/analysis-runs", headers=as_user(outsider)),
            ):
                assert response.status_code == 404 and response.json()["error"]["code"] == "NOT_FOUND"
        # the requester may read the recommendation for their own request
        assert client.get(run_url, headers=as_user("U-REQ-01")).status_code == 200
