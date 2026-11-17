"""Compatibility exports for the existing business-B investigation API.

New model adapters import contracts and tool_catalog directly. Existing
callers can continue importing names from it_procurement_api.agent. Runner
names are loaded only when requested, avoiding a graph dependency for model
serialization.
"""

from importlib import import_module

from .contracts import AgentError, Limits, ModelClient, ModelError, ModelReply, RunState, ScriptedModel, ToolCall
from .tool_catalog import TOOL_SPECS, tool_definitions, validate_args

_RUNNER_EXPORTS = frozenset({
    "SYSTEM_PROMPT", "build_graph", "check_findings", "initial_state",
    "resume_investigation", "shrink", "start_investigation", "tool_message",
})


def __getattr__(name: str):
    if name in _RUNNER_EXPORTS:
        return getattr(import_module(".runner", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentError", "Limits", "ModelClient", "ModelError", "ModelReply",
    "RunState", "ScriptedModel", "ToolCall", "TOOL_SPECS",
    "tool_definitions", "validate_args", *_RUNNER_EXPORTS,
]
