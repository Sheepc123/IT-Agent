"""Versioned input contracts and declared policy for planned Agent tools.

Validation here does not execute tools or authorize a caller. The application
service must supply RunContext, check live ACL, reserve cost and dispatch the
implementation. The output envelope is ToolResult from contracts.py.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from .contracts import AuthorizedToolCall, Contract

TOOL_SCHEMA_VERSION = "1"


class EmptyInput(Contract):
    pass


class SearchAssetsInput(Contract):
    category: str | None = None
    filters: dict[str, str | int | float | bool] = Field(default_factory=dict)
    limit: int = Field(default=10, strict=True, ge=1, le=20)


class EstimateResourcesInput(Contract):
    workload_ref: str = Field(min_length=1)
    assumptions: tuple[str, ...] = ()


class SearchCatalogInput(Contract):
    category: str = Field(min_length=1)
    exact_sku: str | None = None
    specifications: dict[str, str | int | float | bool] = Field(default_factory=dict)
    limit: int = Field(default=10, strict=True, ge=1, le=20)


class ListDocumentsInput(Contract):
    query: str | None = None
    document_kind: str | None = None
    limit: int = Field(default=10, strict=True, ge=1, le=20)


class SearchDocumentsInput(Contract):
    query: str = Field(min_length=1)
    document_version_ids: tuple[UUID, ...] = Field(default=(), max_length=10)
    limit: int = Field(default=10, strict=True, ge=1, le=20)


class ReadEvidenceInput(Contract):
    chunk_ref: str = Field(min_length=1)
    region: str | None = None


class QueryRelationsInput(Contract):
    entity_ref: str = Field(min_length=1)
    relation_types: tuple[str, ...] = Field(min_length=1, max_length=5)
    max_depth: int = Field(default=1, strict=True, ge=1, le=2)


class CheckConstraintsInput(Contract):
    candidate_id: UUID
    fact_refs: tuple[UUID, ...] = Field(min_length=1, max_length=50)


class SolveConfigurationsInput(Contract):
    candidate_ids: tuple[UUID, ...] = Field(min_length=1, max_length=20)
    objective: Literal["MIN_TOTAL_COST", "EARLIEST_DELIVERY"]


class GetQuotesInput(Contract):
    sku_codes: tuple[str, ...] = Field(min_length=1, max_length=20)
    quantity: int = Field(strict=True, ge=1)
    destination: str = Field(min_length=1)
    required_by: AwareDatetime


class BuildBomInput(Contract):
    candidate_id: UUID
    quantity: int = Field(strict=True, ge=1)


class QuestionDraft(Contract):
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AskUserInput(Contract):
    questions: tuple[QuestionDraft, ...] = Field(min_length=1, max_length=5)


class FindingDraft(Contract):
    constraint_id: str = Field(min_length=1)
    candidate_id: UUID
    result: Literal["PASS", "FAIL", "INSUFFICIENT"]
    supporting_evidence: tuple[UUID, ...] = ()
    opposing_evidence: tuple[UUID, ...] = ()
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_draft_evidence(self):
        if self.result in ("PASS", "FAIL") and not self.supporting_evidence:
            raise ValueError("PASS/FAIL requires supporting evidence")
        if self.result in ("PASS", "FAIL") and self.opposing_evidence:
            raise ValueError("conflicting evidence requires INSUFFICIENT")
        return self


class SubmitFindingsInput(Contract):
    findings: tuple[FindingDraft, ...] = Field(min_length=1)
    summary: str = Field(min_length=1)


@dataclass(frozen=True)
class ToolPolicy:
    input_model: type[Contract]
    effect: Literal["READ", "COMPUTE", "RUN_WRITE"]
    authorization_action: str
    timeout_seconds: int
    idempotent: bool


TOOL_POLICIES: dict[str, ToolPolicy] = {
    "get_request_context": ToolPolicy(EmptyInput, "READ", "REQUEST_READ", 5, True),
    "search_assets": ToolPolicy(SearchAssetsInput, "READ", "ASSET_SEARCH", 10, True),
    "estimate_resources": ToolPolicy(EstimateResourcesInput, "COMPUTE", "REQUEST_READ", 10, True),
    "search_catalog": ToolPolicy(SearchCatalogInput, "READ", "CATALOG_SEARCH", 10, True),
    "list_documents": ToolPolicy(ListDocumentsInput, "READ", "DOCUMENT_LIST", 10, True),
    "search_documents": ToolPolicy(SearchDocumentsInput, "READ", "DOCUMENT_SEARCH", 15, True),
    "read_evidence": ToolPolicy(ReadEvidenceInput, "READ", "DOCUMENT_READ", 15, True),
    "query_relations": ToolPolicy(QueryRelationsInput, "READ", "RELATION_QUERY", 10, True),
    "check_constraints": ToolPolicy(CheckConstraintsInput, "COMPUTE", "REQUEST_READ", 15, True),
    "solve_configurations": ToolPolicy(SolveConfigurationsInput, "COMPUTE", "REQUEST_READ", 30, True),
    "get_quotes": ToolPolicy(GetQuotesInput, "READ", "QUOTE_READ", 15, True),
    "build_bom": ToolPolicy(BuildBomInput, "COMPUTE", "REQUEST_READ", 15, True),
    "ask_user": ToolPolicy(AskUserInput, "RUN_WRITE", "RUN_ASK", 5, True),
    "submit_findings": ToolPolicy(SubmitFindingsInput, "RUN_WRITE", "RUN_SUBMIT_DRAFT", 10, True),
}


def validate_tool_input(name: str, arguments: dict, version: str = TOOL_SCHEMA_VERSION) -> Contract:
    """Parse the target tool's arguments; unknown names/versions/fields fail closed."""
    policy = TOOL_POLICIES.get(name)
    if policy is None:
        raise ValueError(f"unknown tool: {name}")
    if version != TOOL_SCHEMA_VERSION:
        raise ValueError(f"unsupported tool schema version: {version}")
    return policy.input_model.model_validate(arguments)


def validate_authorized_call(call: AuthorizedToolCall) -> Contract:
    """Recheck the assigned tool name, schema version and arguments before dispatch."""
    return validate_tool_input(call.tool_name, call.arguments, call.tool_version)
