"""Development stand-in for authentication. NOT real authentication.

The caller names a demo principal in the `X-Demo-Principal` header (for example
`U-BUY-01`). The server looks the principal up and reads its unrevoked role
grants; nothing else in the request can change identity, tenant or roles.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .db import new_session
from .db.models import Principal, RoleGrant
from .errors import ApiError
from .fixtures import TENANT_ID


@dataclass(frozen=True)
class Actor:
    id: uuid.UUID
    key: str
    tenant_id: uuid.UUID
    department_id: uuid.UUID | None
    grants: frozenset[tuple[str, uuid.UUID]]  # (role, scope_id)

    def has(self, role: str, scope_id: uuid.UUID) -> bool:
        return (role, scope_id) in self.grants

    def scopes(self, role: str) -> list[uuid.UUID]:
        return sorted(scope for granted, scope in self.grants if granted == role)


def get_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()


def current_actor(
    session: Annotated[Session, Depends(get_session)],
    x_demo_principal: Annotated[str | None, Header()] = None,
) -> Actor:
    if not x_demo_principal:
        raise ApiError(401, "AUTH_REQUIRED", "missing X-Demo-Principal header")
    principal = session.scalars(
        select(Principal).where(
            Principal.tenant_id == TENANT_ID, Principal.display_name == x_demo_principal, Principal.enabled
        )
    ).one_or_none()
    if principal is None or principal.kind != "USER":
        raise ApiError(401, "AUTH_REQUIRED", "unknown or disabled principal")
    moment = clock.now()
    grants = session.scalars(
        select(RoleGrant).where(
            RoleGrant.tenant_id == TENANT_ID, RoleGrant.principal_id == principal.id,
            RoleGrant.revoked_at.is_(None), RoleGrant.valid_from <= moment,
        )
    ).all()
    return Actor(
        id=principal.id, key=principal.display_name, tenant_id=principal.tenant_id,
        department_id=principal.department_id,
        grants=frozenset(
            (g.role, g.scope_id) for g in grants if g.valid_until is None or moment < g.valid_until
        ),
    )
