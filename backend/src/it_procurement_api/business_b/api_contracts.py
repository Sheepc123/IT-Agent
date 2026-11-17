"""Request and response payloads for planned business-B HTTP endpoints.

No route is registered by this module. Identity, If-Match and Idempotency-Key
come from trusted server context or headers, never from request-body fields.
"""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .contracts import Contract, DisplayedRange


class RequirementInput(Contract):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=80)
    value: str | int | float | bool | None
    unit: str | None = Field(default=None, max_length=32)
    is_hard: bool = False


class RequestDraftInput(Contract):
    description: str = Field(min_length=1, max_length=10_000)
    requirements: tuple[RequirementInput, ...] = ()

    @model_validator(mode="after")
    def unique_requirements(self):
        keys = [requirement.key for requirement in self.requirements]
        if len(keys) != len(set(keys)):
            raise ValueError("requirement keys must be unique")
        return self


class ConfirmRequestInput(Contract):
    confirmed_requirements: tuple[RequirementInput, ...]
    unknown_fields: tuple[str, ...] = ()

    @model_validator(mode="after")
    def disjoint_fields(self):
        keys = [requirement.key for requirement in self.confirmed_requirements]
        if len(keys) != len(set(keys)) or len(self.unknown_fields) != len(set(self.unknown_fields)) or set(keys).intersection(self.unknown_fields):
            raise ValueError("confirmed and unknown fields must be distinct")
        return self


class StartRunInput(Contract):
    request_revision: int = Field(strict=True, ge=1)
    mode: Literal["WORKFLOW", "FIXED_RAG", "AGENT"] = "AGENT"


class AnswerInput(Contract):
    question_id: UUID
    answer: str = Field(min_length=1, max_length=4000)


class CancelInput(Contract):
    reason: str | None = Field(default=None, max_length=500)


class RequestView(Contract):
    request_id: UUID
    revision: int = Field(strict=True, ge=1)
    row_version: int = Field(strict=True, ge=1)
    confirmed_revision: bool = False
    status: Literal["DRAFT", "SUBMITTED", "FULFILLING", "COMPLETED", "CANCELLED"]
    description: str
    requirements: tuple[RequirementInput, ...]
    missing_fields: tuple[str, ...] = ()


class RunAcceptedView(Contract):
    run_id: UUID
    request_id: UUID
    request_revision: int = Field(strict=True, ge=1)
    row_version: int = Field(default=1, strict=True, ge=1)
    status: Literal["QUEUED"] = "QUEUED"
    status_url: str = Field(min_length=1)


class PendingQuestionView(Contract):
    question_id: UUID
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class RunView(Contract):
    run_id: UUID
    request_id: UUID
    request_revision: int = Field(strict=True, ge=1)
    row_version: int = Field(strict=True, ge=1)
    status: Literal["QUEUED", "RUNNING", "WAITING_INPUT", "SUCCEEDED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"]
    end_reason: str | None = None
    result_completeness: Literal["NONE", "PARTIAL", "FULL"] = "NONE"
    model_calls: int = Field(strict=True, ge=0)
    tool_calls: int = Field(strict=True, ge=0)
    known_input_tokens: int = Field(strict=True, ge=0)
    known_output_tokens: int = Field(strict=True, ge=0)
    unknown_usage_calls: int = Field(strict=True, ge=0)
    pending_question: PendingQuestionView | None = None
    event_cursor: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def check_status_details(self):
        terminal = self.status in {"SUCCEEDED", "FAILED", "CANCELLED", "BUDGET_EXHAUSTED"}
        if terminal != (self.end_reason is not None):
            raise ValueError("end_reason must exist exactly for terminal runs")
        if (self.status == "WAITING_INPUT") != (self.pending_question is not None):
            raise ValueError("pending_question must exist exactly while waiting for input")
        return self


class RunEventView(Contract):
    sequence: int = Field(strict=True, ge=1)
    event_type: str = Field(min_length=1)
    occurred_at: AwareDatetime
    summary: str = Field(min_length=1)


class EventPageView(Contract):
    events: tuple[RunEventView, ...]
    next_cursor: int = Field(strict=True, ge=0)

    @model_validator(mode="after")
    def check_event_order(self):
        sequences = [event.sequence for event in self.events]
        if sequences != sorted(set(sequences)):
            raise ValueError("event sequences must be strictly increasing")
        if sequences and self.next_cursor < sequences[-1]:
            raise ValueError("next cursor cannot precede the last event")
        return self


class EvidenceView(Contract):
    evidence_id: UUID
    document_version_id: UUID
    document_title: str = Field(min_length=1)
    page_no: int = Field(strict=True, ge=1)
    displayed_range: DisplayedRange
    content: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    truncated: bool = False
    result_ref: str | None = None

    @model_validator(mode="after")
    def check_truncation(self):
        if self.truncated and not self.result_ref:
            raise ValueError("truncated evidence needs a reference to the complete source")
        return self
