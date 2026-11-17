"""Current bounded LangGraph investigation runner for business B.

This is the existing two-node, in-memory prototype. Business confirmation,
durable recovery, authorization and procurement writes remain outside it.
"""

import json

from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from ..evidence import RESULTS, EvidenceError, list_documents, parse_ref, read_evidence, search_documents
from .contracts import AgentError, Limits, ModelClient, ModelError, RunState
from .tool_catalog import TOOL_SPECS, tool_definitions, validate_args


SYSTEM_PROMPT = """你是 AI 基础设施采购的技术调查助手。你的任务是查厂商技术文档，判断组件之间的支持关系。

规则：
1. 只能依据 read_evidence 读到的原文下结论。search_documents 的预览只用于决定读哪个片段，不能当证据。
2. 每条结论是 PASS、FAIL 或 INSUFFICIENT，并列出所依据片段的 ref。
3. 文档没有列出某个配件，不等于不兼容，应为 INSUFFICIENT。两份资料冲突也是 INSUFFICIENT。
4. 检索按英文型号和料号匹配，查询里要写型号或料号，例如 "XXV710-DA2 transceiver"。
5. 涉及两端设备时，两端的文档都要查。
6. 缺少用户才知道的条件时用 ask_user，把问题合并成一次。
7. 调查结束时必须调用 submit_findings。不要给出金额，不要声称已实机验证。"""

def check_findings(findings: list, read_refs: list[str]) -> tuple[list[dict], list[str]]:
    """Returns (accepted findings, problems). Any problem rejects the whole submission."""
    if not findings:
        return [], ["findings must not be empty"]
    accepted: list[dict] = []
    problems: list[str] = []
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            problems.append(f"finding {index} must be an object")
            continue
        constraint, result, evidence = item.get("constraint"), item.get("result"), item.get("evidence", [])
        if not isinstance(constraint, str) or not constraint.strip():
            problems.append(f"finding {index}: constraint is required")
        if result not in RESULTS:
            problems.append(f"finding {index}: result must be one of {', '.join(RESULTS)}")
        if not isinstance(evidence, list) or not all(isinstance(ref, str) for ref in evidence):
            problems.append(f"finding {index}: evidence must be a list of chunk references")
            continue
        for ref in evidence:
            try:
                parse_ref(ref)
            except EvidenceError:
                problems.append(f"finding {index}: {ref!r} is not a chunk reference")
                continue
            if ref not in read_refs:
                problems.append(f"finding {index}: {ref} was not read in this run; read it with read_evidence first")
        downgraded = result in ("PASS", "FAIL") and not evidence
        accepted.append({
            "constraint": constraint, "result": "INSUFFICIENT" if downgraded else result,
            "evidence": evidence, "downgraded": downgraded,
        })
    return ([], problems) if problems else (accepted, [])


def shrink(result: dict, max_chars: int) -> dict:
    """Cut a tool result to fit the context; marks what was cut."""
    def size(value: dict) -> int:
        return len(json.dumps(value, ensure_ascii=False))

    if size(result) <= max_chars:
        return result
    result = dict(result)
    if isinstance(result.get("content"), str):
        result["truncated"] = True
        while size(result) > max_chars and len(result["content"]) > 100:
            result["content"] = result["content"][: max(100, len(result["content"]) - (size(result) - max_chars))]
        return result
    for key in ("hits", "documents"):
        if isinstance(result.get(key), list):
            items = list(result[key])
            while len(items) > 1 and size({**result, key: items, "omitted": 0}) > max_chars:
                items.pop()
            result["omitted"] = len(result[key]) - len(items)
            result[key] = items
            break
    return result


def tool_message(call_id: str, result: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(result, ensure_ascii=False)}


def initial_state(question: str) -> RunState:
    return RunState(
        question=question,
        messages=[{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": question}],
        status="RUNNING", end_reason=None, model_calls=0, tool_calls=0, questions_asked=0, bad_outputs=0,
        stale_steps=0, read_refs=[], seen_hits=[], call_cache={}, pending=[], waiting_call_id=None,
        pending_question=None, findings=[], usage={"input_tokens": 0, "output_tokens": 0, "unknown_calls": 0},
        events=[],
    )


def _event(events: list[dict], kind: str, **data) -> list[dict]:
    return [*events, {"seq": len(events) + 1, "type": kind, **data}]


# --- graph ----------------------------------------------------------------------


def build_graph(model: ModelClient, session: Session, limits: Limits):
    """The compiled two-node graph. The model, session and limits are fixed for the run."""

    def call_model(state: RunState) -> dict:
        spent = state["usage"]["input_tokens"] + state["usage"]["output_tokens"]
        exhausted = (
            "MODEL_CALLS" if state["model_calls"] >= limits.max_model_calls
            else "TOKENS" if spent >= limits.max_total_tokens
            else None
        )
        if exhausted:  # checked before the request, so the limit is never exceeded by a further call
            return {
                "status": "BUDGET_EXHAUSTED", "end_reason": exhausted, "pending": [],
                "events": _event(state["events"], "end", reason=exhausted),
            }
        try:
            reply = model.complete(state["messages"], tool_definitions())
        except ModelError as exc:
            return {
                "status": "FAILED", "end_reason": "MODEL_ERROR", "pending": [], "model_calls": state["model_calls"] + 1,
                "events": _event(state["events"], "end", reason="MODEL_ERROR", detail=str(exc)[:200]),
            }
        usage = dict(state["usage"])
        if reply.usage is None:
            usage["unknown_calls"] += 1  # unknown is not zero
        else:
            usage["input_tokens"] += reply.usage.get("input_tokens", 0)
            usage["output_tokens"] += reply.usage.get("output_tokens", 0)
        calls = [{"id": call.id, "name": call.name, "arguments": call.arguments} for call in reply.tool_calls]
        messages = [*state["messages"], {"role": "assistant", "content": reply.text, "tool_calls": calls}]
        update: dict = {
            "model_calls": state["model_calls"] + 1, "usage": usage, "messages": messages, "pending": calls,
            "events": _event(state["events"], "model", tool_calls=[call["name"] for call in calls]),
        }
        if not calls:  # plain text is not a way to finish
            bad = state["bad_outputs"] + 1
            update["bad_outputs"] = bad
            if bad >= limits.max_bad_outputs:
                update.update(status="FAILED", end_reason="INVALID_OUTPUT")
                update["events"] = _event(update["events"], "end", reason="INVALID_OUTPUT")
            else:
                messages.append({"role": "user", "content": "请调用工具继续调查，或用 submit_findings 提交结论。"})
        return update

    def run_tools(state: RunState) -> dict:
        messages = list(state["messages"])
        events = list(state["events"])
        read_refs, seen_hits, cache = list(state["read_refs"]), list(state["seen_hits"]), dict(state["call_cache"])
        tool_calls, bad, stale = state["tool_calls"], state["bad_outputs"], state["stale_steps"]
        questions = state["questions_asked"]
        update: dict = {}
        calls = state["pending"]

        for position, call in enumerate(calls):
            if tool_calls >= limits.max_tool_calls:
                update.update(status="BUDGET_EXHAUSTED", end_reason="TOOL_CALLS")
                break
            tool_calls += 1
            name, args = call["name"], call["arguments"]
            spec = TOOL_SPECS.get(name)
            problems = [f"unknown tool: {name}"] if spec is None else validate_args(spec["parameters"], args)
            executed = cached = False

            if problems:
                result = {"error": "; ".join(problems)}
                bad += 1
            elif name == "submit_findings":
                accepted, problems = check_findings(args["findings"], read_refs)
                if problems:
                    result = {"error": "; ".join(problems)}
                    bad += 1
                else:
                    update.update(status="SUCCEEDED", end_reason="FINDINGS_READY", findings=accepted)
                    events = _event(events, "tool", name=name, executed=True, cached=False)
                    break
            elif name == "ask_user":
                if questions >= limits.max_questions:
                    result = {"error": "question limit reached; continue with what is known"}
                    stale += 1
                else:
                    questions += 1
                    update.update(
                        status="WAITING_INPUT", end_reason="NEEDS_INPUT", waiting_call_id=call["id"],
                        pending_question=args["question"],
                    )
                    events = _event(events, "tool", name=name, executed=True, cached=False)
                    for skipped in calls[position + 1:]:  # every tool call needs an answer before the next request
                        messages.append(tool_message(skipped["id"], {"error": "not executed: waiting for the user"}))
                    break
            else:
                bad = 0
                fingerprint = name + json.dumps(args, sort_keys=True, ensure_ascii=False)
                if fingerprint in cache:
                    content, cached = cache[fingerprint], True
                    stale += 1
                else:
                    executed = True
                    try:
                        if name == "list_documents":
                            result = {"documents": list_documents(session, args.get("query"))}
                            progress = bool(result["documents"])
                        elif name == "search_documents":
                            limit = max(1, min(args.get("limit", 5), 10))
                            hits = search_documents(session, args["query"], args.get("document_key"), limit)
                            new = [hit["ref"] for hit in hits if hit["ref"] not in seen_hits]
                            seen_hits.extend(new)
                            result, progress = {"hits": hits}, bool(new)
                        else:
                            result = read_evidence(session, args["ref"])
                            progress = result["ref"] not in read_refs
                            if progress:
                                read_refs.append(result["ref"])
                    except EvidenceError as exc:
                        result, progress = {"error": str(exc)}, False
                    stale = 0 if progress else stale + 1
                    content = json.dumps(shrink(result, limits.max_result_chars), ensure_ascii=False)
                    cache[fingerprint] = content
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
                events = _event(events, "tool", name=name, executed=executed, cached=cached)
                if stale >= limits.max_stale_steps:
                    update.update(status="FAILED", end_reason="NO_PROGRESS")
                    break
                continue

            # invalid call, rejected findings or refused question: tell the model why
            messages.append(tool_message(call["id"], result))
            events = _event(events, "tool", name=name, executed=False, cached=False, error=result["error"][:200])
            if bad >= limits.max_bad_outputs:
                update.update(status="FAILED", end_reason="INVALID_OUTPUT")
                break
            if stale >= limits.max_stale_steps:
                update.update(status="FAILED", end_reason="NO_PROGRESS")
                break

        if update.get("status") not in (None, "WAITING_INPUT"):
            events = _event(events, "end", reason=update["end_reason"])
        return {
            **update, "messages": messages, "events": events, "read_refs": read_refs, "seen_hits": seen_hits,
            "call_cache": cache, "tool_calls": tool_calls, "bad_outputs": bad, "stale_steps": stale,
            "questions_asked": questions, "pending": [],
        }

    def after_model(state: RunState) -> str:
        if state["status"] != "RUNNING":
            return END
        return "run_tools" if state["pending"] else "call_model"

    def after_tools(state: RunState) -> str:
        return "call_model" if state["status"] == "RUNNING" else END

    graph = StateGraph(RunState)
    graph.add_node("call_model", call_model)
    graph.add_node("run_tools", run_tools)
    graph.add_edge(START, "call_model")
    graph.add_conditional_edges("call_model", after_model, {"run_tools": "run_tools", "call_model": "call_model", END: END})
    graph.add_conditional_edges("run_tools", after_tools, {"call_model": "call_model", END: END})
    return graph.compile()


def _run(session: Session, model: ModelClient, state: RunState, limits: Limits) -> RunState:
    # Our own counters stop the run; the framework's step limit only has to be out of the way.
    step_limit = 2 * limits.max_model_calls + 10
    return build_graph(model, session, limits).invoke(state, config={"recursion_limit": step_limit})


def start_investigation(session: Session, model: ModelClient, question: str, limits: Limits = Limits()) -> RunState:
    """Run until findings are submitted, the user is needed, or a limit is hit. Returns the final state."""
    return _run(session, model, initial_state(question), limits)


def resume_investigation(
    session: Session, model: ModelClient, state: RunState, answer: str, limits: Limits = Limits()
) -> RunState:
    """Continue a run that is waiting for the user. Counters carry on; nothing is reset."""
    if state["status"] != "WAITING_INPUT":
        raise AgentError(f"only a run waiting for input can be resumed (status {state['status']})")
    messages = [*state["messages"], tool_message(state["waiting_call_id"], {"answer": answer})]
    resumed = RunState(**{
        **state, "messages": messages, "status": "RUNNING", "end_reason": None, "waiting_call_id": None,
        "pending_question": None, "pending": [],
    })
    return _run(session, model, resumed, limits)
