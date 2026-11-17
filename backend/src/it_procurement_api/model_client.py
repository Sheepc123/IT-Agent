"""DeepSeek chat client behind the agent's ModelClient interface (plan batch 3).

Requests and responses use the OpenAI-compatible chat completions format. That
format was written from memory and NOT checked against the current DeepSeek
documentation. Before trusting it, run once:

    uv run python -m it_procurement_api.evaluate check

which sends one small request and reports whether the reply has the expected
shape (tool call, JSON arguments, token usage).

One HTTP request per call, no automatic retry. The API key comes from settings
(DEEPSEEK_API_KEY in infra/.env) and is never printed or put into errors.
"""

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

from .agent.contracts import ModelError, ModelReply, ToolCall
from .agent.tool_catalog import tool_definitions
from .settings import get_settings

Transport = Callable[[str, dict, dict, float], dict]


def http_post_json(url: str, headers: dict, body: dict, timeout: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={**headers, "Content-Type": "application/json"}, method="POST",
    )
    # `from None`: the original exception carries the request and its Authorization header.
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        raise ModelError(f"HTTP {exc.code}: {detail}") from None
    except json.JSONDecodeError:
        raise ModelError("the response is not JSON") from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelError(f"request failed: {type(exc).__name__}") from None


def to_api_messages(messages: list[dict]) -> list[dict]:
    """The runner's messages in chat-completions form; tool arguments become JSON text."""
    converted = []
    for message in messages:
        if message["role"] == "assistant":
            item: dict = {"role": "assistant", "content": message.get("content") or ""}
            if message.get("tool_calls"):
                item["tool_calls"] = [
                    {
                        "id": call["id"], "type": "function",
                        "function": {
                            "name": call["name"],
                            "arguments": call["arguments"] if isinstance(call["arguments"], str)
                            else json.dumps(call["arguments"], ensure_ascii=False),
                        },
                    }
                    for call in message["tool_calls"]
                ]
            converted.append(item)
        elif message["role"] == "tool":
            converted.append({"role": "tool", "tool_call_id": message["tool_call_id"], "content": message["content"]})
        else:
            converted.append({"role": message["role"], "content": message["content"]})
    return converted


def to_api_tools(tools: list[dict]) -> list[dict]:
    return [
        {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in tools
    ]


def parse_reply(data: dict) -> ModelReply:
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ModelError("the response has no choices[0].message") from None
    calls = []
    for index, raw in enumerate(message.get("tool_calls") or []):
        function = raw.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError:
                pass  # left as text: the runner rejects it and tells the model why
        calls.append(ToolCall(id=raw.get("id") or f"call_{index}", name=function.get("name") or "", arguments=arguments))
    usage = data.get("usage") or {}
    known = isinstance(usage.get("prompt_tokens"), int) and isinstance(usage.get("completion_tokens"), int)
    return ModelReply(
        text=message.get("content") or "", tool_calls=tuple(calls),
        usage={"input_tokens": usage["prompt_tokens"], "output_tokens": usage["completion_tokens"]} if known else None,
    )


@dataclass
class DeepSeekClient:
    api_key: str = field(repr=False)
    base_url: str = "https://api.deepseek.com"
    model: str = "deepseek-chat"
    timeout: float = 60.0
    max_output_tokens: int = 1500
    transport: Transport = field(default=http_post_json, repr=False)
    last_raw: dict | None = field(default=None, repr=False)

    def complete(self, messages: list[dict], tools: list[dict]) -> ModelReply:
        body: dict = {
            "model": self.model, "messages": to_api_messages(messages), "max_tokens": self.max_output_tokens,
            "temperature": 0, "stream": False,
        }
        if tools:
            body["tools"] = to_api_tools(tools)
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        self.last_raw = self.transport(url, {"Authorization": f"Bearer {self.api_key}"}, body, self.timeout)
        return parse_reply(self.last_raw)


def client_from_settings() -> DeepSeekClient:
    settings = get_settings()
    if settings.deepseek_api_key is None:
        raise ModelError("DEEPSEEK_API_KEY is not set in infra/.env; the model path is closed")
    return DeepSeekClient(
        api_key=settings.deepseek_api_key.get_secret_value(), base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
    )


def check_model(client: DeepSeekClient) -> tuple[bool, list[str]]:
    """One small real request with one tool. Returns (shape is as expected, report lines).

    Reports field names only, never message content or the key.
    """
    tool = next(t for t in tool_definitions() if t["name"] == "list_documents")
    messages = [
        {"role": "system", "content": "You are a connectivity test. Follow the instruction exactly."},
        {"role": "user", "content": "Call the list_documents tool with query set to \"test\"."},
    ]
    reply = client.complete(messages, [tool])
    raw = client.last_raw or {}
    message = (raw.get("choices") or [{}])[0].get("message") or {}
    call = reply.tool_calls[0] if reply.tool_calls else None
    checks = [
        ("the reply contains a tool call", call is not None),
        ("the tool name is list_documents", call is not None and call.name == "list_documents"),
        ("the arguments parse to a JSON object", call is not None and isinstance(call.arguments, dict)),
        ("token usage is reported as prompt_tokens / completion_tokens", reply.usage is not None),
    ]
    lines = [f"{'OK  ' if ok else 'FAIL'} {label}" for label, ok in checks]
    lines += [
        f"model: {raw.get('model')}  (configured: {client.model})",
        f"response fields: {sorted(raw)}",
        f"message fields: {sorted(message)}",
        f"usage fields: {sorted(raw.get('usage') or {})}",
        f"usage: {reply.usage}",
    ]
    return all(ok for _, ok in checks), lines
