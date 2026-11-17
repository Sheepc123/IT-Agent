"""Seed the requested development application account after loading FX-A-BASE."""
import os

from sqlalchemy import select

from .. import clock
from ..db import new_session
from ..db.models import AccessScope, LocalCredential, Principal, RoleGrant
from ..fixtures import TENANT_ID, fixture_id
from .password import hash_password

ROLES = ("REQUESTER", "IT_ASSET_ADMIN", "BUYER", "TECH_REVIEWER",
         "PROCUREMENT_APPROVER", "AUDITOR", "SYS_ADMIN")
USERNAME = "admin0"


def ensure_account(password: str) -> None:
    """Idempotently create the demo user, password hash, and grants in PostgreSQL."""
    if not password:
        raise ValueError("DEV_TEST_PASSWORD must not be empty")
    with new_session() as session:
        scopes = session.scalars(select(AccessScope).where(AccessScope.tenant_id == TENANT_ID)).all()
        if not scopes:
            raise RuntimeError("load FX-A-BASE before seeding the application user")
        org = next((scope for scope in scopes if scope.kind == "ORG"), None)
        if org is None:
            raise RuntimeError("demo ORG scope is missing")
        user_id = fixture_id("principal", USERNAME)
        existing = session.scalars(select(Principal).where(
            Principal.tenant_id == TENANT_ID, Principal.display_name == USERNAME,
        )).all()
        if existing and (len(existing) != 1 or existing[0].id != user_id):
            raise RuntimeError("admin0 conflicts with an existing principal")
        principal = existing[0] if existing else Principal(
            id=user_id, tenant_id=TENANT_ID, created_by=user_id, kind="USER",
            display_name=USERNAME, department_id=next(
                (scope.department_id for scope in scopes if scope.kind == "DEPARTMENT"), None),
        )
        principal.enabled = True
        if not existing:
            session.add(principal)
            session.flush()
        credential = session.scalar(select(LocalCredential).where(
            LocalCredential.tenant_id == TENANT_ID, LocalCredential.principal_id == user_id,
        ))
        if credential is None:
            credential = LocalCredential(tenant_id=TENANT_ID, created_by=user_id, principal_id=user_id)
            session.add(credential)
        credential.password_hash = hash_password(password)
        current = {(grant.role, grant.scope_id) for grant in session.scalars(select(RoleGrant).where(
            RoleGrant.tenant_id == TENANT_ID, RoleGrant.principal_id == user_id,
            RoleGrant.revoked_at.is_(None),
        )).all()}
        for scope in scopes:
            for role in ROLES:
                if (role, scope.id) not in current:
                    session.add(RoleGrant(tenant_id=TENANT_ID, created_by=user_id,
                                          principal_id=user_id, scope_id=scope.id,
                                          role=role, valid_from=clock.now()))
        session.commit()


def main() -> None:
    """Read the temporary password and seed the database-backed demo account."""
    password = os.environ.get("DEV_TEST_PASSWORD")
    if not password:
        raise SystemExit("set DEV_TEST_PASSWORD in this command only")
    ensure_account(password)
    print("application test account admin0 seeded with seven roles; password stored as scrypt hash")


if __name__ == "__main__":
    main()
