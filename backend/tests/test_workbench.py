"""Workbench access and review separation using synthetic fixture data."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from it_procurement_api import clock
from it_procurement_api.db.models import RoleGrant
from it_procurement_api.fixtures import TENANT_ID, fixture_id, load_fixture_set, read_sets
from it_procurement_api.auth.identity import Actor
from it_procurement_api.review_policy import may_review_plan

T0 = datetime(2026, 11, 12, 12, 0, tzinfo=timezone(timedelta(hours=8)))


def h(key):
    return {"X-Demo-Principal": key}


def test_demo_header_is_disabled_without_opt_in(client, session, monkeypatch):
    load_fixture_set(session, "FX-A-BASE")
    monkeypatch.delenv("IT_PROCUREMENT_DEMO_IDENTITY", raising=False)
    response = client.get("/api/v1/auth/me", headers=h("U-REQ-01"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_NOT_CONFIGURED"


def test_workbench_tracks_request_state_and_scope(client, session):
    load_fixture_set(session, "FX-A-BASE")
    form = {k: v for k, v in read_sets()["sets"]["FX-A-BASE"]["request"].items() if k != "requester"}
    with clock.frozen(T0):
        assert client.get("/api/v1/auth/me").status_code == 401
        created = client.post("/api/v1/requests", json=form, headers=h("U-REQ-01"))
        assert created.status_code == 201
        request_id = created.json()["data"]["id"]
        own = client.get("/api/v1/tasks", headers=h("U-REQ-01")).json()["data"]["items"]
        assert len(own) == 1 and own[0]["allowed_actions"] == ["submit_request"]
        for outsider in ("U-REQ-02", "U-BUY-02"):
            assert client.get("/api/v1/tasks", headers=h(outsider)).json()["data"]["items"] == []
            assert client.get("/api/v1/dashboard", headers=h(outsider)).json()["data"]["requests"] == []
        assert client.post(f"/api/v1/requests/{request_id}/submit", headers=h("U-REQ-01")).status_code == 200
        assert client.get("/api/v1/tasks", headers=h("U-REQ-01")).json()["data"]["items"] == []
        buyer_tasks = client.get("/api/v1/tasks", headers=h("U-BUY-01")).json()["data"]["items"]
        assert len(buyer_tasks) == 1 and buyer_tasks[0]["allowed_actions"] == ["start_analysis"]
        assert client.get("/api/v1/dashboard", headers=h("U-BUY-01")).json()["data"]["request_count"] == 1
        assert client.post(f"/api/v1/requests/{request_id}/analysis-runs", headers=h("U-BUY-01")).status_code == 202
        assert client.get("/api/v1/tasks", headers=h("U-BUY-01")).json()["data"]["items"] == []


def test_multiple_grants_and_review_conflicts(client, session):
    load_fixture_set(session, "FX-A-BASE")
    with clock.frozen(T0):
        buyer = fixture_id("principal", "U-BUY-01")
        scope = session.scalars(select(RoleGrant.scope_id).where(
            RoleGrant.principal_id == buyer, RoleGrant.role == "BUYER"
        )).one()
        grant = RoleGrant(id=uuid.uuid4(), tenant_id=TENANT_ID, created_by=buyer,
                          principal_id=buyer, scope_id=scope, role="PROCUREMENT_APPROVER", valid_from=T0)
        session.add(grant)
        session.flush()
        me = client.get("/api/v1/auth/me", headers=h("U-BUY-01")).json()["data"]
        assert {g["role"] for g in me["grants"]} >= {"BUYER", "PROCUREMENT_APPROVER"}
        assert client.get("/api/v1/tasks", headers=h("U-BUY-01")).json()["data"]["items"] == []
        actor = Actor(buyer, me["display_name"], TENANT_ID, None,
                      frozenset({(g["role"], uuid.UUID(g["scope_id"])) for g in me["grants"]}))
        kwargs = dict(scope_id=scope, business_type="A", plan_status="PROCUREMENT_REVIEW",
                      plan_author_id=uuid.uuid4(), requester_id=uuid.uuid4(),
                      assigned_reviewer_id=buyer, review_kind="PROCUREMENT")
        assert may_review_plan(actor, **kwargs)
        assert not may_review_plan(actor, **{**kwargs, "plan_author_id": buyer})
        assert not may_review_plan(actor, **{**kwargs, "requester_id": buyer})
        assert not may_review_plan(actor, **{**kwargs, "assigned_reviewer_id": None})
        assert not may_review_plan(actor, **{**kwargs, "scope_id": uuid.uuid4()})
        technical = Actor(buyer, "dual", TENANT_ID, None, frozenset({("TECH_REVIEWER", scope)}))
        tech_case = {**kwargs, "business_type": "B", "plan_status": "TECH_REVIEW", "review_kind": "TECHNICAL"}
        assert may_review_plan(technical, **tech_case)
        assert not may_review_plan(technical, **{**tech_case, "plan_author_id": buyer})
        grant.revoked_at = T0
        session.flush()
        me_after = client.get("/api/v1/auth/me", headers=h("U-BUY-01")).json()["data"]
        assert "PROCUREMENT_APPROVER" not in {g["role"] for g in me_after["grants"]}
