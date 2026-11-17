"""Business A request endpoints (API-01, 02, 04, 05, 33 in docs/接口契约与错误码.md).

Flow: create draft -> submit -> start analysis -> read recommendations.
The analysis itself is `analysis.analyze`; this module loads the data it needs,
checks who is asking, and stores the result. No model is called on this path.

Not implemented yet: Idempotency-Key and If-Match handling, plan drafts,
reservations. Analysis runs synchronously; the 202 + status_url shape is kept
so it can become asynchronous later without changing clients.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import clock
from .analysis import AssetInfo, Need, QuoteInfo, analyze, to_snapshot
from .db.models import (
    Asset,
    BusinessEvent,
    PurchaseRequest,
    QuoteLine,
    QuoteVersion,
    RequestLine,
    RequestRevision,
    Sku,
    Supplier,
    TaskRun,
)
from .errors import ApiError, not_found
from .auth.identity import Actor, current_actor, get_session
from .schemas import RequestDraft, iso, missing_fields

router = APIRouter(prefix="/api/v1")
SessionDep = Annotated[Session, Depends(get_session)]
ActorDep = Annotated[Actor, Depends(current_actor)]


def envelope(data: dict, status: int = 200) -> JSONResponse:
    meta = {"request_id": str(uuid.uuid4()), "server_time": clock.now().isoformat()}
    return JSONResponse(status_code=status, content={"data": data, "meta": meta})


def record_event(session: Session, actor: Actor, request: PurchaseRequest, event_type: str, payload: dict) -> None:
    
    """State changes and their event are written in the same transaction."""
    count = session.scalar(
        select(func.count()).select_from(BusinessEvent).where(
            BusinessEvent.tenant_id == actor.tenant_id, BusinessEvent.aggregate_type == "purchase_request",
            BusinessEvent.aggregate_id == request.id,
        )
    )
    session.add(BusinessEvent(
        tenant_id=actor.tenant_id, created_by=actor.id, aggregate_type="purchase_request",
        aggregate_id=request.id, sequence=count + 1, event_type=event_type, actor_id=actor.id,
        occurred_at=clock.now(), payload=payload, correlation_id=uuid.uuid4(),
    ))


def can_see(actor: Actor, request: PurchaseRequest) -> bool:
    """The requester, or a buyer granted on the request's scope."""
    return request.requester_id == actor.id or actor.has("BUYER", request.scope_id)


def load_request(session: Session, actor: Actor, request_id: uuid.UUID) -> PurchaseRequest:
    request = session.get(PurchaseRequest, request_id)
    if request is None or request.tenant_id != actor.tenant_id or not can_see(actor, request):
        raise not_found()
    return request


def current_draft(session: Session, request: PurchaseRequest) -> tuple[RequestRevision, RequestLine, RequestDraft]:
    revision = session.get(RequestRevision, request.current_revision_id)
    line = session.scalars(select(RequestLine).where(RequestLine.request_revision_id == revision.id)).one()
    draft = RequestDraft(
        business_type=request.business_type, purpose=revision.purpose, category=line.category or None,
        quantity=line.quantity, template_key=line.template_key, minimum_specs=line.minimum_specs or None,
        budget_minor=revision.budget_minor, required_by=revision.required_by, destination=revision.destination,
    )
    return revision, line, draft


def request_view(request: PurchaseRequest, revision: RequestRevision, draft: RequestDraft) -> dict:
    return {
        "id": str(request.id), "request_no": request.request_no, "status": request.status,
        "row_version": request.row_version, "revision": revision.revision,
        "business_type": request.business_type, "purpose": draft.purpose, "category": draft.category,
        "quantity": draft.quantity, "template_key": draft.template_key,
        "minimum_specs": draft.minimum_specs.model_dump() if draft.minimum_specs else None,
        "budget_minor": None if draft.budget_minor is None else str(draft.budget_minor),
        "currency": revision.currency, "required_by": iso(draft.required_by), "destination": draft.destination,
        "missing_fields": missing_fields(draft),
    }


@router.post("/requests")
def create_request(draft: RequestDraft, session: SessionDep, actor: ActorDep) -> JSONResponse:
    scopes = actor.scopes("REQUESTER")
    if not scopes or actor.department_id is None:
        raise ApiError(403, "FORBIDDEN", "only a requester can create a request")
    base = dict(tenant_id=actor.tenant_id, created_by=actor.id)
    request = PurchaseRequest(
        id=uuid.uuid4(), request_no=f"REQ-{uuid.uuid4().hex[:8].upper()}", requester_id=actor.id,
        department_id=actor.department_id, scope_id=scopes[0], business_type=draft.business_type, **base,
    )
    session.add(request)
    session.flush()
    revision = RequestRevision(
        id=uuid.uuid4(), request_id=request.id, revision=1, purpose=draft.purpose, budget_minor=draft.budget_minor,
        required_by=draft.required_by, destination=draft.destination, missing_fields=missing_fields(draft), **base,
    )
    session.add(revision)
    session.flush()
    session.add(RequestLine(
        request_revision_id=revision.id, category=draft.category or "", quantity=draft.quantity,
        template_key=draft.template_key,
        minimum_specs=draft.minimum_specs.model_dump() if draft.minimum_specs else {}, **base,
    ))
    request.current_revision_id = revision.id
    record_event(session, actor, request, "REQUEST_CREATED", {"revision": 1})
    session.commit()
    return envelope(request_view(request, revision, draft), status=201)


@router.get("/requests/{request_id}")
def get_request(request_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> JSONResponse:
    request = load_request(session, actor, request_id)
    revision, _, draft = current_draft(session, request)
    return envelope(request_view(request, revision, draft))


@router.post("/requests/{request_id}/submit")
def submit_request(request_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> JSONResponse:
    request = load_request(session, actor, request_id)
    if request.requester_id != actor.id:
        raise ApiError(403, "FORBIDDEN", "only the requester can submit this request")
    if request.status != "DRAFT":
        raise ApiError(409, "INVALID_STATE", "only a draft can be submitted", {"status": request.status})
    revision, _, draft = current_draft(session, request)
    missing = missing_fields(draft)
    if missing:  # stays DRAFT; nothing is guessed
        raise ApiError(422, "MISSING_REQUIRED_FIELDS", "required fields are missing", {"fields": missing})
    request.status = "SUBMITTED"
    request.row_version += 1
    record_event(session, actor, request, "REQUEST_SUBMITTED", {"revision": revision.revision})
    session.commit()
    return envelope(request_view(request, revision, draft))


def load_assets(session: Session, request: PurchaseRequest) -> list[AssetInfo]:
    """Assets in the request's scope; the actual configuration is the asset's snapshot."""
    assets = session.scalars(
        select(Asset).where(Asset.tenant_id == request.tenant_id, Asset.scope_id == request.scope_id)
    ).all()
    return [
        AssetInfo(
            asset_tag=a.asset_tag, kind=a.kind, lifecycle_status=a.lifecycle_status,
            memory_gib=a.config_snapshot.get("memory_gib"), storage_bytes=a.config_snapshot.get("storage_bytes"),
        )
        for a in assets
    ]


def load_quotes(session: Session, request: PurchaseRequest) -> list[QuoteInfo]:
    rows = session.execute(
        select(QuoteVersion, QuoteLine, Sku, Supplier)
        .join(QuoteLine, QuoteLine.quote_version_id == QuoteVersion.id)
        .join(Sku, Sku.id == QuoteLine.sku_id)
        .join(Supplier, Supplier.id == QuoteVersion.supplier_id)
        .where(QuoteVersion.tenant_id == request.tenant_id, Sku.active, Sku.review_status == "VERIFIED")
    ).all()
    return [
        QuoteInfo(
            quote_key=quote.quote_key, supplier_code=supplier.supplier_code, channel_name=quote.channel_name,
            sku_code=sku.sku_code, memory_gib=sku.specs.get("memory_gib"),
            storage_bytes=sku.specs.get("storage_bytes"), status=quote.status, currency=quote.currency,
            tax_included=line.tax_included, unit_price_minor=line.unit_price_minor,
            shipping_minor=quote.shipping_minor, available_quantity=line.available_quantity,
            valid_from=quote.valid_from, valid_until=quote.valid_until,
            delivery_at=line.delivery_at or quote.delivery_at,
        )
        for quote, line, sku, supplier in rows
    ]


@router.post("/requests/{request_id}/analysis-runs")
def start_analysis(request_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> JSONResponse:
    request = load_request(session, actor, request_id)
    if request.status != "SUBMITTED":
        raise ApiError(409, "INVALID_STATE", "only a submitted request can be analysed", {"status": request.status})
    revision, _, draft = current_draft(session, request)
    need = Need(
        quantity=draft.quantity, min_memory_gib=draft.minimum_specs.memory_gib,
        min_storage_bytes=draft.minimum_specs.storage_bytes, budget_minor=draft.budget_minor,
        required_by=draft.required_by, currency=revision.currency,
    )
    moment = clock.now()
    result = analyze(need, load_assets(session, request), load_quotes(session, request), moment)
    run = TaskRun(
        id=uuid.uuid4(), tenant_id=actor.tenant_id, created_by=actor.id, request_id=request.id,
        request_revision_id=revision.id, actor_id=actor.id, mode="WORKFLOW", input_kind="FORM",
        status="SUCCEEDED", started_at=moment, finished_at=moment,
        result_snapshot=to_snapshot(result, need, moment),
    )
    session.add(run)
    session.commit()
    return envelope(
        {"run_id": str(run.id), "status": run.status, "status_url": f"/api/v1/runs/{run.id}/recommendations"},
        status=202,
    )


@router.get("/runs/{run_id}/recommendations")
def get_recommendations(run_id: uuid.UUID, session: SessionDep, actor: ActorDep) -> JSONResponse:
    run = session.get(TaskRun, run_id)
    if run is None or run.tenant_id != actor.tenant_id:
        raise not_found()
    load_request(session, actor, run.request_id)  # same visibility as the request
    return envelope({
        "run_id": str(run.id), "request_id": str(run.request_id), "status": run.status, "mode": run.mode,
        "input_kind": run.input_kind, "model_call_count": run.model_call_count, "tool_call_count": run.call_count,
        **(run.result_snapshot or {}),
    })
