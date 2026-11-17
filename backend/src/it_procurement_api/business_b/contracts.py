"""Validated business-B facts exchanged across application, tools and Agent.

These models describe shapes and local invariants. They do not grant access,
prove that evidence supports a claim, settle money, or approve a purchase.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, allow_inf_nan=False)


class VersionPins(Contract):
    request_revision: int = Field(strict=True, ge=1)
    data_release_id: str = Field(min_length=1)
    knowledge_release_id: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)


class RunContext(Contract):
    """Server-created identity, scope, version and budget pins for one run."""

    run_id: UUID
    tenant_id: UUID
    principal_id: UUID
    scope_id: UUID
    request_id: UUID
    versions: VersionPins
    budget_id: UUID
    deadline_at: AwareDatetime


class AuthorizedToolCall(Contract):
    """A model suggestion after the server assigns an ID and tool version."""

    logical_call_id: UUID
    run_id: UUID
    tool_name: str = Field(min_length=1)
    tool_version: str = Field(min_length=1)
    arguments: dict[str, Any]

    @model_validator(mode="after")
    def reject_authority_in_arguments(self):
        forbidden = {
            "tenant_id", "principal_id", "scope_id", "budget_id", "run_id",
            "request_revision", "data_release_id", "knowledge_release_id", "rule_version",
        }
        if forbidden.intersection(self.arguments):
            raise ValueError("tool arguments cannot override server authority or version pins")
        return self


class ToolResult(Contract):
    logical_call_id: UUID
    status: Literal["OK", "INSUFFICIENT", "ERROR"]
    summary: str = Field(min_length=1)
    result_ref: str | None = None
    sources: tuple[UUID, ...] = ()
    data_as_of: AwareDatetime | None = None
    scope_id: UUID | None = None
    truncated: bool = False
    pending_fields: tuple[str, ...] = ()
    error_code: str | None = None
    retryable: bool = False

    @model_validator(mode="after")
    def check_outcome(self):
        if self.status == "ERROR" and not self.error_code:
            raise ValueError("ERROR requires error_code")
        if self.status != "ERROR" and (self.error_code or self.retryable):
            raise ValueError("non-error results cannot carry an error or retry flag")
        if self.truncated and not self.result_ref:
            raise ValueError("truncated results require a full-result reference")
        return self


class DisplayedRange(Contract):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)

    @model_validator(mode="after")
    def check_order(self):
        if self.end <= self.start:
            raise ValueError("displayed range must be nonempty")
        return self


class Evidence(Contract):
    """A recorded, actually displayed document span; preview is not proof."""

    evidence_id: UUID
    run_id: UUID
    tenant_id: UUID
    acl_scope_id: UUID
    knowledge_release_id: str = Field(min_length=1)
    document_version_id: UUID
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_no: int = Field(strict=True, ge=1)
    displayed_range: DisplayedRange
    bbox: tuple[float, float, float, float] | None = None
    source_uri: str = Field(min_length=1)
    read_at: AwareDatetime
    is_preview: bool = False

    @model_validator(mode="after")
    def check_box(self):
        if self.bbox is not None:
            x0, y0, x1, y1 = self.bbox
            if x1 <= x0 or y1 <= y0:
                raise ValueError("bbox must have positive width and height")
        return self


class Finding(Contract):
    constraint_id: str = Field(min_length=1)
    candidate_id: UUID
    result: Literal["PASS", "FAIL", "INSUFFICIENT"]
    supporting_evidence: tuple[UUID, ...] = ()
    opposing_evidence: tuple[UUID, ...] = ()
    assumptions: tuple[str, ...] = ()
    applicability: str = Field(min_length=1)
    review_status: Literal["PENDING", "VERIFIED", "REJECTED"] = "PENDING"
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_evidence_shape(self):
        if self.result in ("PASS", "FAIL") and not self.supporting_evidence:
            raise ValueError("PASS/FAIL requires supporting evidence")
        if self.result in ("PASS", "FAIL") and self.opposing_evidence:
            raise ValueError("conflicting evidence requires INSUFFICIENT")
        if len(set(self.supporting_evidence)) != len(self.supporting_evidence) or len(set(self.opposing_evidence)) != len(self.opposing_evidence):
            raise ValueError("evidence references must be unique")
        if set(self.supporting_evidence).intersection(self.opposing_evidence):
            raise ValueError("one evidence item cannot both support and oppose")
        return self


def check_finding_evidence(finding: Finding, evidence_by_id: dict[UUID, Evidence], context: RunContext) -> list[str]:
    """Check read/preview/scope/version metadata; not semantic entailment or live ACL."""
    problems = []
    for evidence_id in (*finding.supporting_evidence, *finding.opposing_evidence):
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            problems.append(f"unknown evidence: {evidence_id}")
            continue
        if evidence.is_preview:
            problems.append(f"preview is not evidence: {evidence_id}")
        if evidence.run_id != context.run_id or evidence.tenant_id != context.tenant_id:
            problems.append(f"evidence belongs to another run or tenant: {evidence_id}")
        if evidence.acl_scope_id != context.scope_id:
            problems.append(f"evidence belongs to another scope: {evidence_id}")
        if evidence.knowledge_release_id != context.versions.knowledge_release_id:
            problems.append(f"evidence has another knowledge release: {evidence_id}")
    return problems


class ProposalCandidate(Contract):
    candidate_id: UUID
    bom_ref: str = Field(min_length=1)
    quote_ref: str | None = None
    total_minor: int | None = Field(default=None, strict=True, ge=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    quote_valid_until: AwareDatetime | None = None
    findings: tuple[Finding, ...] = ()


class Proposal(Contract):
    """A versioned draft/formal payload, never an approval or computed price proof."""

    proposal_id: UUID
    request_id: UUID
    version: int = Field(strict=True, ge=1)
    versions: VersionPins
    stage: Literal["INVESTIGATION_DRAFT", "FORMAL"]
    completeness: Literal["FULL", "PARTIAL"]
    candidates: tuple[ProposalCandidate, ...] = Field(max_length=3)
    selected_candidate_id: UUID | None = None

    @model_validator(mode="after")
    def check_selection(self):
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("proposal candidates must be unique")
        if self.selected_candidate_id is not None and self.selected_candidate_id not in ids:
            raise ValueError("selected candidate must be in the proposal")
        if self.stage == "FORMAL":
            if self.completeness != "FULL" or self.selected_candidate_id is None:
                raise ValueError("formal proposal requires a full result and selected candidate")
            selected = next(candidate for candidate in self.candidates if candidate.candidate_id == self.selected_candidate_id)
            if selected.total_minor is None or selected.quote_ref is None or selected.quote_valid_until is None:
                raise ValueError("formal selection needs a priced, dated quote")
            if not selected.findings or any(
                finding.result != "PASS" or finding.review_status != "VERIFIED" for finding in selected.findings
            ):
                raise ValueError("formal selection needs reviewed PASS findings")
        return self


class BudgetReservation(Contract):
    max_input_tokens: int = Field(strict=True, ge=0)
    max_output_tokens: int = Field(strict=True, ge=0)
    max_cost_minor: int = Field(strict=True, ge=0)


class ActualUsage(Contract):
    input_tokens: int | None = Field(default=None, strict=True, ge=0)
    output_tokens: int | None = Field(default=None, strict=True, ge=0)
    cost_minor: int | None = Field(default=None, strict=True, ge=0)


class BudgetCall(Contract):
    logical_call_id: UUID
    run_id: UUID
    budget_id: UUID
    reserved_max: BudgetReservation
    actual_usage: ActualUsage | None = None
    status: Literal["RESERVED", "SETTLED", "UNKNOWN"]

    @model_validator(mode="after")
    def check_settlement(self):
        if self.status == "RESERVED" and self.actual_usage is not None:
            raise ValueError("reserved call cannot have actual usage")
        if self.status == "SETTLED" and (
            self.actual_usage is None or self.actual_usage.input_tokens is None
            or self.actual_usage.output_tokens is None or self.actual_usage.cost_minor is None
        ):
            raise ValueError("settled call requires complete actual usage")
        return self
