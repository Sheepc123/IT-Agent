"""Evaluation harness (plan batch 3) with the scripted model. No real model is called."""

import json
from datetime import datetime, timezone

from it_procurement_api import clock
from it_procurement_api.agent import Limits, ModelError, ModelReply, ScriptedModel, ToolCall
from it_procurement_api.evaluate import (
    NO_USER_ANSWER,
    evaluate_questions,
    run_agent,
    run_baseline,
    save_results,
    score_run,
    summarize,
    task_prompt,
)
from sample_docs import NIC, SWITCH

TABLE_REF = f"{NIC}:p2:1"
QUESTION = {
    "id": "T-1",
    "question": "ZZNIC900 网卡能否使用光模块 ZZPN0000001？",
    "expected": [
        {"constraint": "网卡端支持光模块 ZZPN0000001", "result": "PASS"},
        {"constraint": "交换机端支持光模块 ZZPN0000001", "result": "INSUFFICIENT"},
    ],
    "evidence": [{"document_key": NIC, "page": 2}],
}
GOOD = [
    {"constraint": "C1 网卡端支持", "result": "PASS", "evidence": [TABLE_REF]},
    {"constraint": "C2 交换机端未列出", "result": "INSUFFICIENT", "evidence": []},
]


def reply(name: str, **arguments) -> ModelReply:
    return ModelReply(tool_calls=(ToolCall(id=f"c-{name}-{len(arguments)}", name=name, arguments=arguments),),
                      usage={"input_tokens": 200, "output_tokens": 40})


def test_task_prompt_numbers_the_constraints():
    prompt = task_prompt(QUESTION)
    assert prompt.startswith(QUESTION["question"])
    assert "C1: 网卡端支持光模块 ZZPN0000001" in prompt and "C2: 交换机端支持光模块 ZZPN0000001" in prompt
    assert task_prompt({"question": "只有问题"}) == "只有问题"


def test_score_matches_findings_by_constraint_number():
    score = score_run(QUESTION, GOOD)
    assert (score["correct"], score["total"]) == (2, 2)
    assert (score["expected_pages_cited"], score["expected_pages"]) == (1, 1)

    wrong = score_run(QUESTION, [{"constraint": "c1：支持", "result": "FAIL", "evidence": []}])
    assert [(row["id"], row["got"], row["correct"]) for row in wrong["constraints"]] == [("C1", "FAIL", False), ("C2", None, False)]

    # C10 is not C1, and a finding without a number matches nothing
    unmatched = score_run(QUESTION, [
        {"constraint": "C10 别的", "result": "PASS", "evidence": []},
        {"constraint": "网卡端支持", "result": "PASS", "evidence": []},
    ])
    assert unmatched["correct"] == 0 and unmatched["constraints"][0]["got"] is None
    assert score_run({"question": "无预期"}, GOOD)["total"] == 0


def test_baseline_is_one_request_over_the_retrieved_chunks(session, docs):
    model = ScriptedModel([reply("submit_findings", findings=GOOD)])
    record = run_baseline(session, model, QUESTION)

    assert (record["status"], record["model_calls"], record["tool_calls"]) == ("SUCCEEDED", 1, 0)
    assert record["context_refs"] == [TABLE_REF, f"{NIC}:p1:0"]
    assert record["usage"] == {"input_tokens": 200, "output_tokens": 40, "unknown_calls": 0}
    assert score_run(QUESTION, record["findings"])["correct"] == 2
    prompt = model.requests[0][1]["content"]
    assert f"[{TABLE_REF}] TABLE" in prompt and "ZZPN0000001 | 25G SR zzmodule" in prompt and "C2:" in prompt
    assert len(model.requests) == 1


def test_baseline_may_only_cite_the_chunks_it_was_given(session, docs):
    outside = [{"constraint": "C1", "result": "PASS", "evidence": [f"{SWITCH}:p5:1"]}]
    record = run_baseline(session, ScriptedModel([reply("submit_findings", findings=outside)]), QUESTION)
    assert (record["status"], record["end_reason"]) == ("FAILED", "INVALID_OUTPUT")
    assert record["findings"] == [] and "was not read in this run" in record["detail"]

    text_only = run_baseline(session, ScriptedModel([ModelReply(text="可以使用", usage=None)]), QUESTION)
    assert text_only["end_reason"] == "INVALID_OUTPUT" and text_only["usage"]["unknown_calls"] == 1


def test_baseline_model_failure_is_recorded(session, docs):
    class Failing:
        def complete(self, messages, tools):
            raise ModelError("timeout")

    record = run_baseline(session, Failing(), QUESTION)
    assert (record["status"], record["end_reason"], record["findings"]) == ("FAILED", "MODEL_ERROR", [])


def test_agent_run_answers_user_questions_with_a_fixed_reply(session, docs):
    model = ScriptedModel([
        reply("ask_user", question="设备之间的距离？"),
        reply("read_evidence", ref=TABLE_REF),
        reply("submit_findings", findings=GOOD),
    ])
    record = run_agent(session, model, QUESTION)
    assert (record["status"], record["model_calls"], record["questions_asked"]) == ("SUCCEEDED", 3, 1)
    assert json.loads(model.requests[1][-1]["content"]) == {"answer": NO_USER_ANSWER}
    assert model.requests[0][1]["content"] == task_prompt(QUESTION)
    assert record["read_refs"] == [TABLE_REF] and record["events"][-1]["type"] == "end"


def test_both_modes_are_scored_summarized_and_saved(session, docs, tmp_path, capsys):
    model = ScriptedModel([
        reply("submit_findings", findings=GOOD[:1]),  # baseline: only C1
        reply("read_evidence", ref=TABLE_REF),  # agent
        reply("submit_findings", findings=GOOD),
    ])
    records = evaluate_questions(session, model, [QUESTION], ("baseline", "agent"), Limits())
    assert [(r["mode"], r["score"]["correct"]) for r in records] == [("baseline", 1), ("agent", 2)]
    assert "T-1" in capsys.readouterr().out

    summary = summarize(records)
    assert summary["baseline"]["constraints_correct"] == 1 and summary["baseline"]["questions_all_correct"] == 0
    assert summary["agent"] == {
        "questions": 1, "questions_all_correct": 1, "constraints_correct": 2, "constraints": 2, "succeeded": 1,
        "model_calls": 2, "tool_calls": 2, "input_tokens": 400, "output_tokens": 80, "unknown_usage_calls": 0,
    }

    with clock.frozen(datetime(2026, 11, 16, 8, 30, tzinfo=timezone.utc)):
        path = save_results(records, {"model": "scripted"}, tmp_path)
    assert path.name == "20261116T083000Z.json"
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["config"] == {"model": "scripted"} and len(saved["records"]) == 2 and saved["summary"] == summary
