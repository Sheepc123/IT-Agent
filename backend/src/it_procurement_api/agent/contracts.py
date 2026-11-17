"""Shared model and run-state contracts for the current investigation runner.

These types contain no graph, database, or vendor-client imports. The larger
business-B RunContext and evidence/proposal contracts are still being specified
under P17; this module preserves the existing runner wire shapes.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict


class AgentError(RuntimeError):
    pass


class ModelError(RuntimeError):
    """The model request failed. The run stops; it is not retried."""


@dataclass(frozen=True)
class Limits:
    max_model_calls: int = 12
    max_tool_calls: int = 30
    max_total_tokens: int = 200_000  # input + output over the whole run, as reported by the model
    max_questions: int = 2
    max_bad_outputs: int = 2  # invalid outputs in a row
    max_stale_steps: int = 3  # tool requests in a row that bring nothing new
    max_result_chars: int = 4000  # one tool result as seen by the model


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Any  # a dict when the model behaves


@dataclass(frozen=True)
class ModelReply:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    usage: dict | None = None  # {"input_tokens": n, "output_tokens": n}; None means unknown


class ModelClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply: ...


@dataclass
class ScriptedModel:
    """Model stand-in: returns prepared replies in order and records what it was sent."""

    replies: list[ModelReply]
    requests: list[list[dict]] = field(default_factory=list)

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        if len(self.requests) >= len(self.replies):
            raise AssertionError("the model was called more often than scripted")
        self.requests.append(list(messages))
        return self.replies[len(self.requests) - 1]


class RunState(TypedDict, total=False):
    question: str
    messages: list[dict]
    status: str  # RUNNING | WAITING_INPUT | SUCCEEDED | BUDGET_EXHAUSTED | FAILED
    end_reason: str | None
    model_calls: int
    tool_calls: int
    questions_asked: int
    bad_outputs: int
    stale_steps: int
    read_refs: list[str]
    seen_hits: list[str]
    call_cache: dict[str, str]
    pending: list[dict]
    waiting_call_id: str | None
    pending_question: str | None
    findings: list[dict]
    usage: dict
    events: list[dict]


