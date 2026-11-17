"""Resolve local application sessions or opt-in test headers to current database grants.

Local password login is for development; enterprise identity remains planned.
"""

import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..db import new_session
from ..db.models import LocalSession, Principal, RoleGrant
from ..errors import ApiError
from ..fixtures import TENANT_ID
from .password import token_digest


@dataclass(frozen=True)
class Actor:
    """Authenticated principal and currently effective role grants."""

    id: uuid.UUID
    key: str
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None
    grants: frozenset[tuple[str, uuid.UUID]]  # (role, scope_id)
    identity_source: str = "demo_header"

    def has(self, role: str, scope_id: uuid.UUID) -> bool:
        """Check one role within one scope."""
        return (role, scope_id) in self.grants

    def scopes(self, role: str) -> list[uuid.UUID]:
        """List scopes where the principal currently has a role."""
        return sorted(scope for granted, scope in self.grants if granted == role)


def get_session() -> Iterator[Session]:
    """Provide a database session for one HTTP request."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def local_login_enabled() -> bool:
    """Allow password login only when explicitly enabled for local development."""
    return os.getenv("IT_PROCUREMENT_LOCAL_LOGIN", "").lower() in {"1", "true"}


def current_actor(
    session: Annotated[Session, Depends(get_session)],
    authorization: Annotated[str | None, Header()] = None,
    x_demo_principal: Annotated[str | None, Header()] = None,
) -> Actor:
    """Resolve the caller, then reload current grants for every request."""
    principal = None
    source = "local_session"
    if authorization and authorization.startswith("Bearer "):
        if not local_login_enabled():
            raise ApiError(503, "AUTH_NOT_CONFIGURED", "authentication is not configured")
        digest = token_digest(authorization[7:])
        auth_session = session.scalar(select(LocalSession).where(
            LocalSession.tenant_id == TENANT_ID, LocalSession.token_hash == digest,
            LocalSession.revoked_at.is_(None), LocalSession.expires_at > clock.now(),
        ))
        if auth_session is not None:
            principal = session.get(Principal, auth_session.principal_id)
    elif x_demo_principal and os.getenv("IT_PROCUREMENT_DEMO_IDENTITY", "").lower() in {"1", "true"}:
        source = "demo_header"
        principal = session.scalars(select(Principal).where(
            Principal.tenant_id == TENANT_ID, Principal.display_name == x_demo_principal,
            Principal.enabled,
        )).one_or_none()
    elif not any(os.getenv(name, "").lower() in {"1", "true"} for name in
                 ("IT_PROCUREMENT_LOCAL_LOGIN", "IT_PROCUREMENT_DEMO_IDENTITY")):
        raise ApiError(503, "AUTH_NOT_CONFIGURED", "authentication is not configured")
    if principal is None or principal.kind != "USER" or not principal.enabled or principal.tenant_id != TENANT_ID:
        raise ApiError(401, "AUTH_REQUIRED", "invalid or missing session")
    moment = clock.now()
    grants = session.scalars(
        select(RoleGrant).where(
            RoleGrant.tenant_id == TENANT_ID, RoleGrant.principal_id == principal.id,
            RoleGrant.revoked_at.is_(None), RoleGrant.valid_from <= moment,
        )
    ).all()
    return Actor(
        id=principal.id, key=principal.display_name, tenant_id=principal.tenant_id,
        department_id=principal.department_id, identity_source=source,
        grants=frozenset(
            (g.role, g.scope_id) for g in grants if g.valid_until is None or moment < g.valid_until
        ),
    )
