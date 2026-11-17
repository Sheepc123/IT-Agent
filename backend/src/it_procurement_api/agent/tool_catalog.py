"""Model-visible tool catalog and basic argument validation.

The catalog has no graph or knowledge-store import, so model adapters can
serialize tool definitions without loading the investigation runner.
"""

from typing import Any


TOOL_SPECS: dict[str, dict] = {
    "list_documents": {
        "description": "列出已入库的技术文档，可用型号或文档名过滤。",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": []},
    },
    "search_documents": {
        "description": "按型号、料号等英文词项检索片段，返回片段 ref、页码和命中行预览。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string"}, "document_key": {"type": "string"}, "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    "read_evidence": {
        "description": "按 ref 读取一个片段的原文、页码和文档版本。只有读过的片段才能作为证据。",
        "parameters": {"type": "object", "properties": {"ref": {"type": "string"}}, "required": ["ref"]},
    },
    "ask_user": {
        "description": "向用户提出缺少的条件，暂停等待回答。",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    "submit_findings": {
        "description": "提交结论并结束。findings 每项含 constraint、result（PASS/FAIL/INSUFFICIENT）、evidence（ref 列表）。",
        "parameters": {
            "type": "object",
            "properties": {
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "constraint": {"type": "string"},
                            "result": {"type": "string", "enum": ["PASS", "FAIL", "INSUFFICIENT"]},
                            "evidence": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["constraint", "result", "evidence"],
                    },
                },
                "summary": {"type": "string"},
            },
            "required": ["findings"],
        },
    },
}
_JSON_TYPES = {"string": str, "integer": int, "array": list, "object": dict}


def tool_definitions() -> list[dict]:
    return [{"name": name, **spec} for name, spec in TOOL_SPECS.items()]


def validate_args(parameters: dict, args: Any) -> list[str]:
    """Problems with a tool's arguments; empty when they are acceptable."""
    if not isinstance(args, dict):
        return ["arguments must be a JSON object"]
    properties = parameters["properties"]
    problems = [f"missing argument: {name}" for name in parameters["required"] if name not in args]
    for name, value in args.items():
        if name not in properties:
            problems.append(f"unknown argument: {name}")
            continue
        expected = _JSON_TYPES[properties[name]["type"]]
        if not isinstance(value, expected) or isinstance(value, bool):
            problems.append(f"argument {name} must be {properties[name]['type']}")
    return problems


