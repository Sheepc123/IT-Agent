"""Compare the agent with the fixed baseline on the checked questions (plan batch 3).

    uv run python -m it_procurement_api.evaluate check          # 1 small real request
    uv run python -m it_procurement_api.evaluate ask B1-NET-05  # one question, agent only, with trace
    uv run python -m it_procurement_api.evaluate run            # all checked questions, both modes

All three commands call the real model and spend tokens.

Both modes get the same task text and the same documents:
- baseline: one search with the question, the top 5 chunks, one model request;
- agent: the investigation loop in agent.py.

Scoring is deterministic. The task lists the constraints to judge as C1, C2, ...
and a finding is matched to its constraint by that number, then its result is
compared with the expected one. Because the constraints are given, this does
not measure whether a model would have thought of them by itself.
"""

import argparse
import json
import re
import time
from hashlib import sha256
from pathlib import Path

from sqlalchemy.orm import Session

from . import clock
from .agent import (
    TOOL_SPECS,
    Limits,
    ModelClient,
    ModelError,
    check_findings,
    resume_investigation,
    start_investigation,
    validate_args,
)
from .db import new_session
from .evidence import QUESTIONS_PATH, baseline_context, load_questions, parse_ref
from .model_client import check_model, client_from_settings
from .settings import REPO_ROOT

RESULTS_DIR = REPO_ROOT / "data" / "processed" / "eval_runs"
NO_USER_ANSWER = "评测运行中没有用户可以回答。请按已知条件继续，条件不足的约束给出 INSUFFICIENT。"
BASELINE_PROMPT = """你是 AI 基础设施采购的技术调查助手。下面给出一个问题和检索到的厂商文档片段。

规则：
1. 只能依据给出的片段下结论，evidence 只能填片段的 ref。
2. 每条结论是 PASS、FAIL 或 INSUFFICIENT。
3. 文档没有列出某个配件，不等于不兼容，应为 INSUFFICIENT。片段不足以判断时也是 INSUFFICIENT。
4. 必须调用 submit_findings 提交结论。不要给出金额，不要声称已实机验证。"""
SUBMIT_TOOL = {"name": "submit_findings", **TOOL_SPECS["submit_findings"]}


# --- task text and scoring (pure) -----------------------------------------------


def task_prompt(question: dict) -> str:
    """The question plus the numbered constraints to judge."""
    expected = question.get("expected") or []
    if not expected:
        return question["question"]
    lines = [question["question"], "", "请对下面每一项约束分别给出结论。constraint 字段必须以编号开头，例如 \"C1 ……\"："]
    lines += [f"C{number}: {item['constraint']}" for number, item in enumerate(expected, start=1)]
    return "\n".join(lines)


def score_run(question: dict, findings: list[dict]) -> dict:
    """Compare findings with the expected results, constraint by constraint."""
    rows = []
    for number, item in enumerate(question.get("expected") or [], start=1):
        pattern = re.compile(rf"^\s*C{number}(?!\d)", re.IGNORECASE)
        got = next((f for f in findings if pattern.match(f["constraint"])), None)
        rows.append({
            "id": f"C{number}", "expected": item["result"], "got": got["result"] if got else None,
            "correct": bool(got and got["result"] == item["result"]), "evidence": got["evidence"] if got else [],
            "downgraded": bool(got and got.get("downgraded")),
        })
    expected_pages = {(e["document_key"], e["page"]) for e in question.get("evidence") or []}
    cited_pages = {parse_ref(ref)[:2] for row in rows for ref in row["evidence"]}
    return {
        "constraints": rows, "correct": sum(row["correct"] for row in rows), "total": len(rows),
        "expected_pages_cited": len(cited_pages & expected_pages), "expected_pages": len(expected_pages),
    }


# --- the two modes ---------------------------------------------------------------


def run_baseline(session: Session, model: ModelClient, question: dict, limit: int = 5, max_chars: int = 20_000) -> dict:
    """Fixed flow: one search, the top chunks, one model request, findings checked like the agent's."""
    context = baseline_context(session, question["question"], limit, max_chars)
    refs = [chunk["ref"] for chunk in context["chunks"]]
    parts = [task_prompt(question), "", "检索到的片段（evidence 只能引用这些 ref）："]
    parts += [f"\n[{chunk['ref']}] {chunk['kind']}\n{chunk['content']}" for chunk in context["chunks"]]
    if not refs:
        parts.append("（没有检索到片段）")
    messages = [{"role": "system", "content": BASELINE_PROMPT}, {"role": "user", "content": "\n".join(parts)}]
    record: dict = {
        "mode": "baseline", "model_calls": 1, "tool_calls": 0, "context_refs": refs, "findings": [],
        "usage": {"input_tokens": 0, "output_tokens": 0, "unknown_calls": 0},
    }
    try:
        reply = model.complete(messages, [SUBMIT_TOOL])
    except ModelError as exc:
        return {**record, "status": "FAILED", "end_reason": "MODEL_ERROR", "detail": str(exc)[:200]}
    if reply.usage is None:
        record["usage"]["unknown_calls"] = 1
    else:
        record["usage"].update(reply.usage)
    call = next((c for c in reply.tool_calls if c.name == "submit_findings"), None)
    problems = ["no submit_findings call"] if call is None else validate_args(SUBMIT_TOOL["parameters"], call.arguments)
    if not problems:
        record["findings"], problems = check_findings(call.arguments["findings"], refs)
    if problems:
        return {**record, "status": "FAILED", "end_reason": "INVALID_OUTPUT", "detail": "; ".join(problems)[:300]}
    return {**record, "status": "SUCCEEDED", "end_reason": "FINDINGS_READY"}


def run_agent(session: Session, model: ModelClient, question: dict, limits: Limits = Limits()) -> dict:
    """The investigation loop. Questions to the user get a fixed "nobody can answer" reply."""
    state = start_investigation(session, model, task_prompt(question), limits)
    while state["status"] == "WAITING_INPUT":  # ends: the number of questions is limited
        state = resume_investigation(session, model, state, NO_USER_ANSWER, limits)
    return {
        "mode": "agent", "status": state["status"], "end_reason": state["end_reason"], "findings": state["findings"],
        "model_calls": state["model_calls"], "tool_calls": state["tool_calls"], "usage": state["usage"],
        "questions_asked": state["questions_asked"], "read_refs": state["read_refs"], "events": state["events"],
    }


def evaluate_questions(
    session: Session, model: ModelClient, questions: list[dict], modes: tuple[str, ...], limits: Limits = Limits()
) -> list[dict]:
    records = []
    for question in questions:
        for mode in modes:
            started = time.monotonic()
            record = run_baseline(session, model, question) if mode == "baseline" else run_agent(session, model, question, limits)
            record.update(
                question_id=question["id"], seconds=round(time.monotonic() - started, 1),
                score=score_run(question, record["findings"]),
            )
            records.append(record)
            print(format_record(record), flush=True)
    return records


def summarize(records: list[dict]) -> dict:
    """Totals per mode."""
    summary: dict = {}
    for record in records:
        total = summary.setdefault(record["mode"], {
            "questions": 0, "questions_all_correct": 0, "constraints_correct": 0, "constraints": 0, "succeeded": 0,
            "model_calls": 0, "tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "unknown_usage_calls": 0,
        })
        score = record["score"]
        total["questions"] += 1
        total["questions_all_correct"] += int(score["total"] > 0 and score["correct"] == score["total"])
        total["constraints_correct"] += score["correct"]
        total["constraints"] += score["total"]
        total["succeeded"] += int(record["status"] == "SUCCEEDED")
        total["model_calls"] += record["model_calls"]
        total["tool_calls"] += record["tool_calls"]
        total["input_tokens"] += record["usage"]["input_tokens"]
        total["output_tokens"] += record["usage"]["output_tokens"]
        total["unknown_usage_calls"] += record["usage"]["unknown_calls"]
    return summary


def format_record(record: dict) -> str:
    score, usage = record["score"], record["usage"]
    tokens = usage["input_tokens"] + usage["output_tokens"]
    unknown = f" (+{usage['unknown_calls']} calls unknown)" if usage["unknown_calls"] else ""
    return (
        f"{record['question_id']:<10} {record['mode']:<8} {score['correct']}/{score['total']} "
        f"{record['status']}/{record['end_reason']}  model {record['model_calls']}  tools {record['tool_calls']}  "
        f"tokens {tokens}{unknown}  {record['seconds']}s"
    )


def save_results(records: list[dict], config: dict, directory: Path = RESULTS_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{clock.now().strftime('%Y%m%dT%H%M%SZ')}.json"
    payload = {"config": config, "summary": summarize(records), "records": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m it_procurement_api.evaluate")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="one small real request: is the API shape what the client expects?")
    ask = commands.add_parser("ask", help="run the agent on one question id, or on free text, and print the trace")
    ask.add_argument("question")
    run = commands.add_parser("run", help="agent and baseline on the checked questions; saves a result file")
    run.add_argument("--mode", choices=("both", "agent", "baseline"), default="both")
    run.add_argument("--ids", nargs="*", help="only these question ids")
    args = parser.parse_args()

    try:
        model = client_from_settings()
    except ModelError as exc:
        raise SystemExit(str(exc))

    if args.command == "check":
        try:
            ok, lines = check_model(model)
        except ModelError as exc:
            raise SystemExit(f"request failed: {exc}")
        print("\n".join(lines))
        if not ok:
            raise SystemExit("the API shape differs from what model_client.py expects; do not run the evaluation yet")
        return

    questions = load_questions()
    with new_session() as session:
        if args.command == "ask":
            question = next((q for q in questions if q["id"] == args.question), {"id": "ad-hoc", "question": args.question})
            record = run_agent(session, model, question)
            for event in record["events"]:
                print({key: value for key, value in event.items() if key != "seq"})
            print(json.dumps(record["findings"], ensure_ascii=False, indent=2))
            record.update(question_id=question["id"], seconds=0.0, score=score_run(question, record["findings"]))
            print(format_record(record))
            return

        chosen = [q for q in questions if q["status"] == "CHECKED_AGAINST_CHUNKS" and (not args.ids or q["id"] in args.ids)]
        if not chosen:
            raise SystemExit("no checked questions selected")
        modes = ("baseline", "agent") if args.mode == "both" else (args.mode,)
        records = evaluate_questions(session, model, chosen, modes)
        config = {
            "model": model.model, "base_url": model.base_url, "temperature": 0, "limits": Limits().__dict__,
            "question_file_sha256": sha256(QUESTIONS_PATH.read_bytes()).hexdigest(), "question_ids": [q["id"] for q in chosen],
            "evidence_verification": "expected results come from parsed chunks, not from hardware or expert review",
        }
        path = save_results(records, config)
        print(json.dumps(summarize(records), ensure_ascii=False, indent=2))
        print(f"saved: {path.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
