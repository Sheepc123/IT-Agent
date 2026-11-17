"""DeepSeek client (plan batch 3) with a fake transport. No network, no tokens."""

import json

import pytest

from it_procurement_api.agent import ModelError, tool_definitions
from it_procurement_api.model_client import DeepSeekClient, check_model, parse_reply, to_api_messages, to_api_tools

KEY = "sk-test-not-a-real-key"


def response(tool_calls=None, content="", usage=True) -> dict:
    data = {"model": "deepseek-chat", "choices": [{"message": {"role": "assistant", "content": content, "tool_calls": tool_calls}}]}
    if usage:
        data["usage"] = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
    return data


def api_call(name: str, arguments: str, call_id: str = "call_1") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}}


def test_messages_are_converted_to_chat_completions_form():
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "read_evidence", "arguments": {"ref": "X:p1:0"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "{\"content\": \"原文\"}"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c2", "name": "read_evidence", "arguments": "not json"}]},
    ]
    converted = to_api_messages(messages)
    assert converted[:2] == messages[:2]
    assert converted[2]["tool_calls"] == [api_call("read_evidence", "{\"ref\": \"X:p1:0\"}", "c1")]
    assert converted[3] == {"role": "tool", "tool_call_id": "c1", "content": "{\"content\": \"原文\"}"}
    assert converted[4]["tool_calls"][0]["function"]["arguments"] == "not json"  # unparsed text is passed back as it was


def test_tools_are_wrapped_as_functions():
    tools = to_api_tools(tool_definitions())
    assert {tool["type"] for tool in tools} == {"function"}
    submit = next(tool["function"] for tool in tools if tool["function"]["name"] == "submit_findings")
    assert submit["parameters"]["properties"]["findings"]["items"]["properties"]["result"]["enum"] == ["PASS", "FAIL", "INSUFFICIENT"]


def test_reply_parsing():
    reply = parse_reply(response([api_call("search_documents", "{\"query\": \"XXV710-DA2\"}")]))
    assert reply.tool_calls[0].name == "search_documents" and reply.tool_calls[0].arguments == {"query": "XXV710-DA2"}
    assert reply.usage == {"input_tokens": 120, "output_tokens": 30}

    assert parse_reply(response([api_call("list_documents", "")])).tool_calls[0].arguments == {}
    assert parse_reply(response([api_call("read_evidence", "{broken")])).tool_calls[0].arguments == "{broken"
    assert parse_reply(response(content="plain text")).tool_calls == ()
    assert parse_reply(response(usage=False)).usage is None  # unknown, not zero
    for bad in ({}, {"choices": []}, {"choices": [{}]}):
        with pytest.raises(ModelError):
            parse_reply(bad)


def test_client_sends_one_request_and_keeps_the_key_out_of_sight():
    sent = []

    def transport(url, headers, body, timeout):
        sent.append((url, headers, body, timeout))
        return response([api_call("list_documents", "{}")])

    client = DeepSeekClient(api_key=KEY, base_url="https://api.example.invalid/", transport=transport)
    reply = client.complete([{"role": "user", "content": "hi"}], tool_definitions())

    assert len(sent) == 1 and reply.tool_calls[0].name == "list_documents"
    url, headers, body, _ = sent[0]
    assert url == "https://api.example.invalid/chat/completions"
    assert headers == {"Authorization": f"Bearer {KEY}"}
    assert body["model"] == "deepseek-chat" and body["temperature"] == 0 and body["stream"] is False
    assert len(body["tools"]) == 5 and body["max_tokens"] == 1500
    assert KEY not in repr(client) and KEY not in json.dumps(body)


def test_transport_failure_is_a_model_error_and_is_not_retried():
    attempts = []

    def transport(url, headers, body, timeout):
        attempts.append(1)
        raise ModelError("HTTP 500: upstream")

    client = DeepSeekClient(api_key=KEY, transport=transport)
    with pytest.raises(ModelError, match="HTTP 500"):
        client.complete([{"role": "user", "content": "hi"}], [])
    assert len(attempts) == 1


def test_check_model_reports_shape_without_content():
    good = DeepSeekClient(api_key=KEY, transport=lambda *a: response([api_call("list_documents", "{\"query\": \"test\"}")]))
    ok, lines = check_model(good)
    assert ok and all(line.startswith("OK") for line in lines[:4])
    assert KEY not in "\n".join(lines)

    no_tool = DeepSeekClient(api_key=KEY, transport=lambda *a: response(content="I cannot call tools", usage=False))
    ok, lines = check_model(no_tool)
    assert not ok and [line[:4] for line in lines[:4]] == ["FAIL", "FAIL", "FAIL", "FAIL"]
