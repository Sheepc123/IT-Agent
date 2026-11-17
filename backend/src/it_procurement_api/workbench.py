"""Read-only workbench backed by persisted requests and current grants.

Only request submission and A analysis have executable task sources today.
Review, fulfilment and B investigation wait for their assignment/state tables.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .db.models import Principal, PurchaseRequest, RequestRevision, TaskRun
from .auth.identity import Actor, current_actor, get_session
from .requests_api import envelope

router = APIRouter(prefix="/api/v1")
SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[Actor, Depends(current_actor)]


def visible_requests(session: Session, actor: Actor) -> list[PurchaseRequest]:
    """Limit rows in SQL by tenant, ownership and currently granted buyer scopes."""
    buyer_scopes = actor.scopes("BUYER")
    return list(session.scalars(
        select(PurchaseRequest).where(
            PurchaseRequest.tenant_id == actor.tenant_id,
            or_(PurchaseRequest.requester_id == actor.id,
                PurchaseRequest.scope_id.in_(buyer_scopes) if buyer_scopes else False),
        ).order_by(PurchaseRequest.created_at.desc(), PurchaseRequest.id.desc()).limit(101)
    ).all())


def request_snapshots(session: Session, requests: list[PurchaseRequest]) -> dict:
    ids = [row.current_revision_id for row in requests if row.current_revision_id]
    if not ids:
        return {}
    return {row.id: row for row in session.scalars(
        select(RequestRevision).where(RequestRevision.tenant_id == requests[0].tenant_id, RequestRevision.id.in_(ids))
    ).all()}


def task_items(session: Session, actor: Actor, requests: list[PurchaseRequest], revisions: dict) -> list[dict]:
    """Derive only tasks with a known actor and actual backend state."""
    revision_ids = [row.current_revision_id for row in requests if row.current_revision_id]
    successful = set(session.scalars(
        select(TaskRun.request_revision_id).where(
            TaskRun.tenant_id == actor.tenant_id,
            TaskRun.request_revision_id.in_(revision_ids), TaskRun.status == "SUCCEEDED",
        )
    ).all()) if revision_ids else set()
    items: list[dict] = []
    for request in requests:
        revision = revisions.get(request.current_revision_id)
        if revision is None:
            continue
        base = {
            "id": str(request.id), "request_id": str(request.id),
            "request_no": request.request_no, "business_type": request.business_type,
            "subject": revision.purpose or request.request_no,
            "status": request.status, "resource_type": "purchase_request",
            "assigned_to": str(actor.id),
        }
        if request.requester_id == actor.id and request.status == "DRAFT":
            missing = revision.missing_fields or []
            items.append({**base, "task_type": "REQUEST_SUBMISSION", "role": "REQUESTER",
                          "title": "补全并提交需求" if missing else "提交采购需求",
                          "detail": "缺少：" + "、".join(missing) if missing else "需求已具备提交条件。",
                          "allowed_actions": [] if missing else ["submit_request"]})
        if (request.business_type == "A" and request.status == "SUBMITTED"
                and actor.has("BUYER", request.scope_id)
                and request.current_revision_id not in successful):
            items.append({**base, "task_type": "START_A_ANALYSIS", "role": "BUYER",
                          "title": "分析设备需求", "detail": "核对资产与模拟报价，形成建议。",
                          "allowed_actions": ["start_analysis"]})
    return items


@router.get("/auth/me")
def auth_me(session: SessionDep, actor: ActorDep) -> JSONResponse:
    principal = session.get(Principal, actor.id)
    return envelope({
        "id": str(actor.id), "display_name": principal.display_name,
        "department_id": str(actor.department_id) if actor.department_id else None,
        "tenant_id": str(actor.tenant_id), "identity_source": actor.identity_source,
        "grants": [{"role": role, "scope_id": str(scope)} for role, scope in sorted(actor.grants)],
    })


@router.get("/tasks")
def list_tasks(session: SessionDep, actor: ActorDep) -> JSONResponse:
    requests = visible_requests(session, actor)
    page = requests[:100]
    return envelope({"items": task_items(session, actor, page, request_snapshots(session, page)),
                     "has_more": len(requests) > 100})


@router.get("/dashboard")
def dashboard(session: SessionDep, actor: ActorDep) -> JSONResponse:
    requests = visible_requests(session, actor)
    page = requests[:100]
    revisions = request_snapshots(session, page)
    return envelope({"requests": [
        {"id": str(row.id), "request_no": row.request_no,
         "business_type": row.business_type, "status": row.status,
         "title": revisions[row.current_revision_id].purpose or row.request_no}
        for row in page if row.current_revision_id in revisions
    ], "request_count": len(page), "has_more": len(requests) > 100})
