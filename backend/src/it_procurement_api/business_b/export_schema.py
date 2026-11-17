"""Export deterministic, versioned JSON Schemas for the future B workbench."""

import argparse
import json
from pathlib import Path

from pydantic import BaseModel

from . import api_contracts, contracts
from .tool_contracts import TOOL_POLICIES, TOOL_SCHEMA_VERSION

SCHEMA_VERSION = "p17-v1"
MODELS: dict[str, type[BaseModel]] = {
    name: getattr(module, name)
    for module, names in (
        (contracts, ("RunContext", "AuthorizedToolCall", "ToolResult", "Evidence", "Finding", "Proposal", "BudgetCall")),
        (api_contracts, (
            "RequestDraftInput", "ConfirmRequestInput", "StartRunInput", "AnswerInput", "CancelInput",
            "RequestView", "RunAcceptedView", "RunView", "EventPageView", "EvidenceView",
        )),
    )
    for name in names
}


def schema_bundle() -> dict:
    """Return self-contained validation schemas and target tool policy metadata."""
    return {
        "schema_version": SCHEMA_VERSION,
        "models": {name: model.model_json_schema(mode="validation") for name, model in sorted(MODELS.items())},
        "tools": {
            name: {
                "version": TOOL_SCHEMA_VERSION,
                "effect": policy.effect,
                "authorization_action": policy.authorization_action,
                "timeout_seconds": policy.timeout_seconds,
                "idempotent": policy.idempotent,
                "input_schema": policy.input_model.model_json_schema(mode="validation"),
                "output_schema_ref": "ToolResult",
            }
            for name, policy in sorted(TOOL_POLICIES.items())
        },
    }


def write_schema(path: Path) -> None:
    """Write the reviewed schema artifact; only explicit CLI calls mutate files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(schema_bundle(), ensure_ascii=False, sort_keys=True, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_schema(args.output)


if __name__ == "__main__":
    main()
