"""Development-only database-backed application login and opaque sessions."""
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..db.models import LocalCredential, LocalSession, Principal
from ..errors import ApiError
from ..fixtures import TENANT_ID
from .identity import get_session, local_login_enabled
from .password import token_digest, verify_password

router = APIRouter(prefix="/api/v1/auth")
SessionDep = Annotated[Session, Depends(get_session)]
SESSION_SECONDS = 3600


class LoginInput(BaseModel):
    """Credentials accepted by the development-only login endpoint."""

    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
def login(body: LoginInput, session: SessionDep) -> dict:
    """Validate a local account and create a revocable database session."""
    if not local_login_enabled():
        raise ApiError(503, "LOCAL_LOGIN_DISABLED", "local application login is disabled")
    principals = session.scalars(select(Principal).where(
        Principal.tenant_id == TENANT_ID, Principal.display_name == body.username,
        Principal.kind == "USER", Principal.enabled,
    ).limit(2)).all()
    if len(principals) != 1:
        raise ApiError(401, "INVALID_CREDENTIALS", "invalid username or password")
    principal = principals[0]
    credential = session.scalar(select(LocalCredential).where(
        LocalCredential.tenant_id == TENANT_ID, LocalCredential.principal_id == principal.id,
    ))
    if credential is None or not verify_password(body.password, credential.password_hash):
        raise ApiError(401, "INVALID_CREDENTIALS", "invalid username or password")
    token = secrets.token_urlsafe(32)
    expires_at = clock.now() + timedelta(seconds=SESSION_SECONDS)
    session.add(LocalSession(tenant_id=TENANT_ID, created_by=principal.id,
                             principal_id=principal.id, token_hash=token_digest(token), expires_at=expires_at))
    session.commit()
    return {"access_token": token, "token_type": "bearer", "expires_in": SESSION_SECONDS}


@router.post("/logout", status_code=204)
def logout(session: SessionDep, authorization: Annotated[str | None, Header()] = None) -> None:
    """Revoke the current database session before the frontend clears its cookie."""
    if not local_login_enabled():
        raise ApiError(503, "LOCAL_LOGIN_DISABLED", "local application login is disabled")
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "AUTH_REQUIRED", "missing session")
    row = session.scalar(select(LocalSession).where(
        LocalSession.tenant_id == TENANT_ID,
        LocalSession.token_hash == token_digest(authorization[7:]),
        LocalSession.revoked_at.is_(None), LocalSession.expires_at > clock.now(),
    ))
    if row is None:
        raise ApiError(401, "AUTH_REQUIRED", "invalid session")
    row.revoked_at = clock.now()
    session.commit()
