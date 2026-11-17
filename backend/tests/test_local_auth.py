"""Database-backed local application login and revocable sessions."""
from sqlalchemy import select

from it_procurement_api.db.models import LocalCredential, LocalSession, Principal
from it_procurement_api.fixtures import TENANT_ID
from it_procurement_api.auth.password import hash_password, verify_password


def test_local_login_session_and_logout(client, session, monkeypatch):
    monkeypatch.setenv("IT_PROCUREMENT_LOCAL_LOGIN", "1")
    principal = session.scalar(select(Principal).where(
        Principal.tenant_id == TENANT_ID, Principal.display_name == "U-REQ-01"))
    existing = session.scalar(select(LocalCredential).where(LocalCredential.principal_id == principal.id))
    if existing:
        existing.password_hash = hash_password("test-password")
    else:
        session.add(LocalCredential(tenant_id=TENANT_ID, created_by=principal.id,
                                    principal_id=principal.id, password_hash=hash_password("test-password")))
    session.flush()
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.post("/api/v1/auth/login", json={"username": "U-REQ-01", "password": "wrong"}).status_code == 401
    response = client.post("/api/v1/auth/login", json={"username": "U-REQ-01", "password": "test-password"})
    assert response.status_code == 200
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["data"]["identity_source"] == "local_session"
    row = session.scalar(select(LocalSession).where(LocalSession.principal_id == principal.id,
                                                     LocalSession.revoked_at.is_(None)))
    assert row is not None and row.token_hash != token
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_password_is_salted_and_local_login_is_opt_in(client, monkeypatch):
    first, second = hash_password("same"), hash_password("same")
    assert first != second and verify_password("same", first)
    assert not verify_password("other", first)
    monkeypatch.delenv("IT_PROCUREMENT_LOCAL_LOGIN", raising=False)
    response = client.post("/api/v1/auth/login", json={"username": "u", "password": "p"})
    assert response.status_code == 503
