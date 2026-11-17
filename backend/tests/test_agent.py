"""Investigation loop (plan batch 2) with a scripted model stand-in.

These tests check what the runner enforces, not how well a real model
investigates. No model is called and no tokens are spent.
"""

import json

import pytest

from it_procurement_api.agent import (
    AgentError,
    Limits,
    ModelError,
    ModelReply,
    ScriptedModel,
    ToolCall,
    TOOL_SPECS,
    check_findings,
    resume_investigation,
    shrink,
    start_investigation,
    tool_definitions,
    validate_args,
)
from sample_docs import NIC, SWITCH

QUESTION = "ZZNIC900 网卡能否使用光模块 ZZPN0000001？"
TABLE_REF = f"{NIC}:p2:1"
_ids = iter(range(1, 10_000))


def call(name: str, **arguments) -> ToolCall:
    return ToolCall(id=f"c{next(_ids)}", name=name, arguments=arguments)


def reply(*calls: ToolCall, text: str = "", usage: dict | None = None) -> ModelReply:
    return ModelReply(text=text, tool_calls=tuple(calls), usage=usage or {"input_tokens": 100, "output_tokens": 20})


def finding(result: str = "PASS", evidence: tuple[str, ...] = (TABLE_REF,)) -> dict:
    return {"constraint": "网卡端支持光模块 ZZPN0000001", "result": result, "evidence": list(evidence)}


def tool_results(state: dict) -> list[dict]:
    return [json.loads(m["content"]) for m in state["messages"] if m["role"] == "tool"]


def event_types(state: dict) -> list[str]:
    return [event["type"] for event in state["events"]]


# --- pure checks ----------------------------------------------------------------


def test_tool_arguments_are_validated():
    search = TOOL_SPECS["search_documents"]["parameters"]
    assert validate_args(search, {"query": "ZZPN0000001", "limit": 3}) == []
    assert validate_args(search, {}) == ["missing argument: query"]
    assert validate_args(search, {"query": 5}) == ["argument query must be string"]
    assert validate_args(search, {"query": "x", "limit": True}) == ["argument limit must be integer"]
    assert validate_args(search, {"query": "x", "tenant_id": "other"}) == ["unknown argument: tenant_id"]
    assert validate_args(search, "query=x") == ["arguments must be a JSON object"]
    assert {tool["name"] for tool in tool_definitions()} == {
        "list_documents", "search_documents", "read_evidence", "ask_user", "submit_findings",
    }


def test_findings_may_only_cite_chunks_read_in_this_run():
    accepted, problems = check_findings([finding()], read_refs=[TABLE_REF])
    assert problems == [] and accepted[0]["result"] == "PASS" and accepted[0]["downgraded"] is False

    accepted, problems = check_findings([finding()], read_refs=[])
    assert accepted == [] and "was not read in this run" in problems[0]

    assert check_findings([], read_refs=[])[1] == ["findings must not be empty"]
    assert "result must be one of" in check_findings([finding(result="LIKELY")], [TABLE_REF])[1][0]
    assert "is not a chunk reference" in check_findings([finding(evidence=("page 2",))], [TABLE_REF])[1][0]
    assert check_findings(["yes it works"], [])[1] == ["finding 1 must be an object"]


def test_a_conclusion_without_evidence_becomes_insufficient():
    accepted, problems = check_findings([finding(result="PASS", evidence=())], read_refs=[])
    assert problems == [] and accepted[0]["result"] == "INSUFFICIENT" and accepted[0]["downgraded"] is True
    kept, _ = check_findings([finding(result="INSUFFICIENT", evidence=())], read_refs=[])
    assert kept[0]["downgraded"] is False


def test_shrink_cuts_long_results_and_says_so():
    long_text = shrink({"ref": "x:p1:0", "content": "a" * 5000}, 600)
    assert long_text["truncated"] is True and len(json.dumps(long_text)) <= 600
    many = shrink({"hits": [{"ref": f"x:p{i}:0", "preview": ["line " * 20]} for i in range(20)]}, 600)
    assert many["omitted"] > 0 and len(many["hits"]) + many["omitted"] == 20
    assert shrink({"hits": []}, 600) == {"hits": []}


# --- runs -----------------------------------------------------------------------


def test_investigation_reads_evidence_then_submits(session, docs):
    model = ScriptedModel([
        reply(call("search_documents", query="ZZNIC900 ZZPN0000001")),
        reply(call("read_evidence", ref=TABLE_REF)),
        reply(call("submit_findings", findings=[finding()], summary="网卡指南第 2 页列有该模块")),
    ])
    state = start_investigation(session, model, QUESTION)

    assert (state["status"], state["end_reason"]) == ("SUCCEEDED", "FINDINGS_READY")
    assert state["findings"] == [{**finding(), "downgraded": False}]
    assert (state["model_calls"], state["tool_calls"]) == (3, 3)
    assert state["read_refs"] == [TABLE_REF]
    assert state["usage"] == {"input_tokens": 300, "output_tokens": 60, "unknown_calls": 0}
    assert event_types(state) == ["model", "tool", "model", "tool", "model", "tool", "end"]
    assert [event["seq"] for event in state["events"]] == list(range(1, 8))

    # the model saw the question first, then each tool result before its next turn
    assert model.requests[0][1] == {"role": "user", "content": QUESTION}
    assert json.loads(model.requests[1][-1]["content"])["hits"][0]["ref"] == TABLE_REF
    assert "ZZPN0000001 | 25G SR zzmodule" in json.loads(model.requests[2][-1]["content"])["content"]


def test_citing_a_chunk_that_was_only_searched_is_rejected_then_corrected(session, docs):
    model = ScriptedModel([
        reply(call("search_documents", query="ZZPN0000001")),
        reply(call("submit_findings", findings=[finding()])),  # cites the hit without reading it
        reply(call("read_evidence", ref=TABLE_REF)),
        reply(call("submit_findings", findings=[finding()])),
    ])
    state = start_investigation(session, model, QUESTION)
    assert state["status"] == "SUCCEEDED" and state["model_calls"] == 4
    assert "was not read in this run" in tool_results(state)[1]["error"]


def test_two_invalid_outputs_in_a_row_stop_the_run(session, docs):
    model = ScriptedModel([
        reply(call("place_order", sku="ZZPN0000001")),  # not a tool
        reply(call("read_evidence")),  # missing argument
    ])
    state = start_investigation(session, model, QUESTION)
    assert (state["status"], state["end_reason"]) == ("FAILED", "INVALID_OUTPUT")
    assert tool_results(state)[0] == {"error": "unknown tool: place_order"}
    assert state["findings"] == [] and state["read_refs"] == []


def test_plain_text_is_not_a_way_to_finish(session, docs):
    model = ScriptedModel([reply(text="可以使用。"), reply(text="我确定可以。")])
    state = start_investigation(session, model, QUESTION)
    assert (state["status"], state["end_reason"]) == ("FAILED", "INVALID_OUTPUT")
    assert state["model_calls"] == 2 and state["tool_calls"] == 0


def test_repeated_calls_are_answered_from_cache_and_end_as_no_progress(session, docs):
    same = dict(query="ZZPN0000001")
    model = ScriptedModel([reply(call("search_documents", **same)) for _ in range(5)])
    state = start_investigation(session, model, QUESTION)
    assert (state["status"], state["end_reason"]) == ("FAILED", "NO_PROGRESS")
    tools = [event for event in state["events"] if event["type"] == "tool"]
    assert [(t["executed"], t["cached"]) for t in tools] == [(True, False), (False, True), (False, True), (False, True)]
    assert state["tool_calls"] == 4 and len(model.requests) == 4  # every request is counted, cached or not


def test_model_request_limit_ends_the_run_without_a_further_request(session, docs):
    model = ScriptedModel([
        reply(call("search_documents", query="ZZPN0000001")),
        reply(call("read_evidence", ref=TABLE_REF)),
        reply(call("submit_findings", findings=[finding()])),
    ])
    state = start_investigation(session, model, QUESTION, Limits(max_model_calls=2))
    assert (state["status"], state["end_reason"]) == ("BUDGET_EXHAUSTED", "MODEL_CALLS")
    assert len(model.requests) == 2 and state["model_calls"] == 2
    assert state["read_refs"] == [TABLE_REF] and state["findings"] == []  # partial work is kept, not called success


def test_token_limit_is_checked_before_each_request(session, docs):
    model = ScriptedModel([
        reply(call("search_documents", query="ZZPN0000001")),  # 120 tokens reported
        reply(call("read_evidence", ref=TABLE_REF)),  # 240 in total: over the limit of 200
        reply(call("submit_findings", findings=[finding()])),
    ])
    state = start_investigation(session, model, QUESTION, Limits(max_total_tokens=200))
    assert (state["status"], state["end_reason"]) == ("BUDGET_EXHAUSTED", "TOKENS")
    assert len(model.requests) == 2 and state["findings"] == []


def test_tool_request_limit_counts_every_call_in_a_reply(session, docs):
    model = ScriptedModel([reply(
        call("search_documents", query="ZZPN0000001"),
        call("search_documents", query="ZZPN0000002"),
        call("search_documents", query="zzmodule"),
    )])
    state = start_investigation(session, model, QUESTION, Limits(max_tool_calls=2))
    assert (state["status"], state["end_reason"]) == ("BUDGET_EXHAUSTED", "TOOL_CALLS")
    assert state["tool_calls"] == 2 and len(tool_results(state)) == 2


def test_ask_user_pauses_and_resume_continues_without_resetting_counters(session, docs):
    model = ScriptedModel([
        reply(call("ask_user", question="设备之间的距离是多少？")),
        reply(call("read_evidence", ref=TABLE_REF)),
        reply(call("submit_findings", findings=[finding()])),
    ])
    waiting = start_investigation(session, model, QUESTION)
    assert (waiting["status"], waiting["end_reason"]) == ("WAITING_INPUT", "NEEDS_INPUT")
    assert waiting["pending_question"] == "设备之间的距离是多少？" and len(model.requests) == 1
    assert "end" not in event_types(waiting)

    done = resume_investigation(session, model, waiting, "2 米")
    assert done["status"] == "SUCCEEDED" and done["pending_question"] is None
    assert (done["model_calls"], done["tool_calls"], done["questions_asked"]) == (3, 3, 1)
    assert json.loads(model.requests[1][-1]["content"]) == {"answer": "2 米"}

    with pytest.raises(AgentError):
        resume_investigation(session, model, done, "再问一次")


def test_question_limit_refuses_further_questions(session, docs):
    model = ScriptedModel([
        reply(call("ask_user", question="距离？")),
        reply(call("ask_user", question="光纤类型？")),
        reply(call("submit_findings", findings=[finding(result="INSUFFICIENT", evidence=())])),
    ])
    limits = Limits(max_questions=1)
    waiting = start_investigation(session, model, QUESTION, limits)
    done = resume_investigation(session, model, waiting, "2 米", limits)
    assert done["status"] == "SUCCEEDED" and done["questions_asked"] == 1
    assert "question limit reached" in tool_results(done)[1]["error"]


def test_long_evidence_is_cut_before_it_reaches_the_model(session, docs):
    model = ScriptedModel([
        reply(call("read_evidence", ref=f"{NIC}:p4:0")),
        reply(call("submit_findings", findings=[finding(result="INSUFFICIENT", evidence=())])),
    ])
    state = start_investigation(session, model, QUESTION, Limits(max_result_chars=800))
    seen = model.requests[1][-1]["content"]
    assert len(seen) <= 800 and json.loads(seen)["truncated"] is True
    assert state["read_refs"] == [f"{NIC}:p4:0"]


def test_unknown_usage_is_counted_as_unknown_not_zero(session, docs):
    model = ScriptedModel([
        ModelReply(tool_calls=(call("read_evidence", ref=TABLE_REF),), usage=None),
        reply(call("submit_findings", findings=[finding()])),
    ])
    state = start_investigation(session, model, QUESTION)
    assert state["usage"] == {"input_tokens": 100, "output_tokens": 20, "unknown_calls": 1}


def test_model_failure_stops_the_run_and_is_not_retried(session, docs):
    class FailingModel:
        calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            raise ModelError("timeout")

    model = FailingModel()
    state = start_investigation(session, model, QUESTION)
    assert (state["status"], state["end_reason"]) == ("FAILED", "MODEL_ERROR")
    assert model.calls == 1 and state["model_calls"] == 1


def test_tool_errors_go_back_to_the_model(session, docs):
    model = ScriptedModel([
        reply(call("read_evidence", ref=f"{SWITCH}:p9:0")),  # no such chunk
        reply(call("read_evidence", ref=f"{SWITCH}:p5:1")),
        reply(call("submit_findings", findings=[finding(result="INSUFFICIENT", evidence=(f"{SWITCH}:p5:1",))])),
    ])
    state = start_investigation(session, model, QUESTION)
    assert state["status"] == "SUCCEEDED" and "no such chunk" in tool_results(state)[0]["error"]
    assert state["read_refs"] == [f"{SWITCH}:p5:1"]
