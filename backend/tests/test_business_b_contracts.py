"""Contract gates for identity, evidence, budget, API shape and tool input."""

import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from it_procurement_api.business_b.api_contracts import EventPageView, RequestDraftInput, RunEventView, RunView
from it_procurement_api.business_b.contracts import (
    ActualUsage, AuthorizedToolCall, BudgetCall, BudgetReservation, DisplayedRange,
    Evidence, Finding, Proposal, ProposalCandidate, RunContext, ToolResult,
    VersionPins, check_finding_evidence,
)
from it_procurement_api.business_b.export_schema import schema_bundle
from it_procurement_api.business_b.tool_contracts import TOOL_POLICIES, validate_authorized_call, validate_tool_input

NOW = datetime(2026, 11, 16, 10, tzinfo=timezone.utc)


def context() -> RunContext:
    return RunContext(
        run_id=uuid4(), tenant_id=uuid4(), principal_id=uuid4(), scope_id=uuid4(),
        request_id=uuid4(), versions=VersionPins(
            request_revision=1, data_release_id="data-1", knowledge_release_id="docs-1", rule_version="r1",
        ), budget_id=uuid4(), deadline_at=NOW,
    )


def evidence(ctx: RunContext, *, preview: bool = False) -> Evidence:
    return Evidence(
        evidence_id=uuid4(), run_id=ctx.run_id, tenant_id=ctx.tenant_id,
        acl_scope_id=ctx.scope_id, knowledge_release_id=ctx.versions.knowledge_release_id,
        document_version_id=uuid4(), content_hash="a" * 64, page_no=2,
        displayed_range=DisplayedRange(start=0, end=80), source_uri="vendor-guide.pdf",
        read_at=NOW, is_preview=preview,
    )


def finding(ref) -> Finding:
    return Finding(
        constraint_id="gpu_slot", candidate_id=uuid4(), result="PASS",
        supporting_evidence=(ref,), applicability="server SKU-1", reason="vendor table row",
    )


def test_run_context_rejects_extra_authority_and_bad_versions():
    ctx = context()
    with pytest.raises(ValidationError):
        RunContext.model_validate({**ctx.model_dump(), "role": "ADMIN"})
    with pytest.raises(ValidationError):
        VersionPins(request_revision=0, data_release_id="d", knowledge_release_id="k", rule_version="r")
    with pytest.raises(ValidationError):
        RunContext.model_validate({**ctx.model_dump(), "deadline_at": datetime(2026, 11, 16)})


def test_evidence_metadata_rejects_preview_wrong_release_and_bad_span():
    ctx = context()
    read = evidence(ctx)
    assert check_finding_evidence(finding(read.evidence_id), {read.evidence_id: read}, ctx) == []
    preview = evidence(ctx, preview=True)
    assert any("preview" in p for p in check_finding_evidence(finding(preview.evidence_id), {preview.evidence_id: preview}, ctx))
    old = read.model_copy(update={"knowledge_release_id": "docs-old"})
    assert any("release" in p for p in check_finding_evidence(finding(read.evidence_id), {read.evidence_id: old}, ctx))
    assert any("unknown" in p for p in check_finding_evidence(finding(read.evidence_id), {}, ctx))
    with pytest.raises(ValidationError):
        DisplayedRange(start=10, end=10)
    with pytest.raises(ValidationError):
        Evidence.model_validate({**read.model_dump(), "content_hash": "wrong"})


def test_findings_do_not_turn_missing_or_conflicting_evidence_into_pass():
    with pytest.raises(ValidationError):
        Finding(constraint_id="c", candidate_id=uuid4(), result="PASS", applicability="sku", reason="unsupported")
    with pytest.raises(ValidationError):
        Finding(
            constraint_id="c", candidate_id=uuid4(), result="PASS", supporting_evidence=(uuid4(),),
            opposing_evidence=(uuid4(),), applicability="sku", reason="conflict",
        )
    gap = Finding(constraint_id="c", candidate_id=uuid4(), result="INSUFFICIENT", applicability="sku", reason="no guide")
    assert gap.supporting_evidence == ()


def test_result_errors_and_truncation_are_explicit():
    with pytest.raises(ValidationError):
        ToolResult(logical_call_id=uuid4(), status="ERROR", summary="timeout")
    with pytest.raises(ValidationError):
        ToolResult(logical_call_id=uuid4(), status="OK", summary="partial", truncated=True)
    result = ToolResult(
        logical_call_id=uuid4(), status="ERROR", summary="timeout", error_code="PROVIDER_UNAVAILABLE",
        retryable=True, truncated=True, result_ref="result-1",
    )
    assert result.retryable and result.result_ref == "result-1"


def test_unknown_budget_usage_cannot_be_reported_as_settled():
    reserve = BudgetReservation(max_input_tokens=100, max_output_tokens=50, max_cost_minor=30)
    call = dict(logical_call_id=uuid4(), run_id=uuid4(), budget_id=uuid4(), reserved_max=reserve)
    assert BudgetCall(**call, status="UNKNOWN").actual_usage is None
    with pytest.raises(ValidationError):
        BudgetCall(**call, status="SETTLED", actual_usage=ActualUsage(input_tokens=10, output_tokens=5))
    assert BudgetCall(
        **call, status="SETTLED", actual_usage=ActualUsage(input_tokens=10, output_tokens=5, cost_minor=8),
    ).actual_usage.cost_minor == 8
    with pytest.raises(ValidationError):
        BudgetCall(**call, status="RESERVED", actual_usage=ActualUsage(cost_minor=0))


def test_proposal_limits_candidates_and_keeps_draft_separate_from_formal():
    ctx = context()
    candidate = ProposalCandidate(candidate_id=uuid4(), bom_ref="bom-1", currency="CNY")
    base = dict(proposal_id=uuid4(), request_id=ctx.request_id, version=1, versions=ctx.versions)
    assert Proposal(**base, stage="INVESTIGATION_DRAFT", completeness="PARTIAL", candidates=(candidate,)).stage == "INVESTIGATION_DRAFT"
    with pytest.raises(ValidationError):
        Proposal(**base, stage="FORMAL", completeness="PARTIAL", candidates=(candidate,))
    with pytest.raises(ValidationError):
        Proposal(**base, stage="FORMAL", completeness="FULL", candidates=(candidate,), selected_candidate_id=candidate.candidate_id)
    reviewed = finding(uuid4()).model_copy(update={"candidate_id": candidate.candidate_id, "review_status": "VERIFIED"})
    priced = candidate.model_copy(update={
        "quote_ref": "quote-1", "total_minor": 10000, "quote_valid_until": NOW, "findings": (reviewed,),
    })
    assert Proposal(
        **base, stage="FORMAL", completeness="FULL", candidates=(priced,),
        selected_candidate_id=candidate.candidate_id,
    ).stage == "FORMAL"
    with pytest.raises(ValidationError):
        Proposal(**base, stage="INVESTIGATION_DRAFT", completeness="PARTIAL", candidates=(candidate,) * 4)
    with pytest.raises(ValidationError):
        ProposalCandidate(candidate_id=uuid4(), bom_ref="bom-1", currency="CNY", total_minor=-1)


def test_http_body_rejects_identity_and_duplicate_requirements():
    valid = RequestDraftInput.model_validate({
        "description": "需要 GPU 服务器", "requirements": [{"key": "gpu_count", "value": 4, "is_hard": True}],
    })
    assert valid.requirements[0].value == 4
    with pytest.raises(ValidationError):
        RequestDraftInput.model_validate({"description": "购买服务器", "tenant_id": str(uuid4())})
    with pytest.raises(ValidationError):
        RequestDraftInput.model_validate({"description": "购买服务器", "requirements": [
            {"key": "gpu_count", "value": 4}, {"key": "gpu_count", "value": 8},
        ]})


def test_run_view_status_and_event_cursor_are_consistent():
    base = dict(
        run_id=uuid4(), request_id=uuid4(), request_revision=1, row_version=1,
        model_calls=0, tool_calls=0, known_input_tokens=0, known_output_tokens=0,
        unknown_usage_calls=0, event_cursor=0,
    )
    assert RunView(**base, status="RUNNING").status == "RUNNING"
    with pytest.raises(ValidationError):
        RunView(**base, status="SUCCEEDED")
    with pytest.raises(ValidationError):
        RunView(**base, status="WAITING_INPUT")
    event = RunEventView(sequence=2, event_type="started", occurred_at=NOW, summary="调查开始")
    with pytest.raises(ValidationError):
        EventPageView(events=(event,), next_cursor=1)


def test_target_tools_have_strict_inputs_and_no_approval_or_order_tool():
    assert len(TOOL_POLICIES) == 14
    assert not {"approve", "place_order", "change_roles"}.intersection(TOOL_POLICIES)
    assert validate_tool_input("get_request_context", {}) is not None
    with pytest.raises(ValidationError):
        validate_tool_input("get_request_context", {"tenant_id": str(uuid4())})
    with pytest.raises(ValidationError):
        validate_tool_input("search_documents", {"query": "gpu", "limit": 100})
    with pytest.raises(ValueError, match="unknown tool"):
        validate_tool_input("place_order", {})
    with pytest.raises(ValueError, match="unsupported tool schema version"):
        validate_tool_input("search_documents", {"query": "gpu"}, version="2")
    with pytest.raises(ValidationError):
        AuthorizedToolCall(
            logical_call_id=uuid4(), run_id=uuid4(), tool_name="search_documents", tool_version="1",
            arguments={"query": "gpu", "scope_id": str(uuid4())},
        )
    call = AuthorizedToolCall(
        logical_call_id=uuid4(), run_id=uuid4(), tool_name="search_documents", tool_version="1",
        arguments={"query": "GPU server"},
    )
    assert validate_authorized_call(call).query == "GPU server"
    with pytest.raises(ValueError, match="unsupported tool schema version"):
        validate_authorized_call(call.model_copy(update={"tool_version": "2"}))


def test_frontend_schema_artifact_matches_backend_models():
    path = Path(__file__).parents[2] / "frontend/contracts/business_b.schema.json"
    bundle = schema_bundle()
    assert json.loads(path.read_text()) == bundle
    assert bundle["schema_version"] == "p17-v1"
    assert bundle["models"]["RequestDraftInput"]["additionalProperties"] is False
    assert bundle["tools"]["submit_findings"]["effect"] == "RUN_WRITE"
