"""The model adapter can use shared contracts without loading the graph runner."""

import subprocess
import sys

from it_procurement_api.agent import ModelReply, ToolCall, tool_definitions
from it_procurement_api.agent.contracts import ModelReply as ContractModelReply
from it_procurement_api.agent.contracts import ToolCall as ContractToolCall


def test_legacy_exports_are_shared_contracts():
    assert ModelReply is ContractModelReply
    assert ToolCall is ContractToolCall
    assert len(tool_definitions()) == 5


def test_model_client_import_does_not_load_graph_runner():
    code = """
import sys
from it_procurement_api.model_client import DeepSeekClient
assert 'it_procurement_api.agent.runner' not in sys.modules
from it_procurement_api.agent import start_investigation
assert 'it_procurement_api.agent.runner' in sys.modules
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr


def test_legacy_runner_waits_and_resumes_without_database_calls():
    from it_procurement_api.agent import ScriptedModel, resume_investigation, start_investigation

    model = ScriptedModel([
        ModelReply(tool_calls=(ToolCall("question-1", "ask_user", {"question": "需要多少 GPU？"}),),
                   usage={"input_tokens": 10, "output_tokens": 5}),
        ModelReply(tool_calls=(ToolCall("finding-1", "submit_findings", {
            "findings": [{"constraint": "GPU 数量待确认", "result": "INSUFFICIENT", "evidence": []}],
        }),), usage={"input_tokens": 10, "output_tokens": 5}),
    ])
    waiting = start_investigation(None, model, "我需要 GPU 服务器")
    assert waiting["status"] == "WAITING_INPUT"
    assert waiting["pending_question"] == "需要多少 GPU？"
    result = resume_investigation(None, model, waiting, "4 张")
    assert result["status"] == "SUCCEEDED"
    assert result["findings"][0]["result"] == "INSUFFICIENT"
    assert result["model_calls"] == 2
