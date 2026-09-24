from __future__ import annotations

import json

import httpx

from decision_bench.domain import Completion, Message, ToolCall, ToolSpec
from decision_bench.ports import ProviderError
from decision_bench.providers.http import parse_arguments, post_json


class OpenAICompatibleProvider:
    configured = True

    def __init__(
        self,
        *,
        name: str,
        api_key: str,
        base_url: str,
        timeout_s: float,
        detail: str,
        default_model: str = "",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.name = name
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.detail = detail
        self.default_model = default_model
        self._transport = transport

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        schema: dict,
    ) -> Completion:
        del schema
        payload = {
            "model": model,
            "messages": [to_openai_message(message) for message in messages],
            "tools": [to_openai_tool(tool) for tool in tools],
        }
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:8000"
            headers["X-Title"] = "Decision Bench"
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            data = post_json(client, f"{self.base_url}/chat/completions", headers=headers, payload=payload)
        return parse_openai(data)


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        detail: str = "OpenRouter",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            name="openrouter",
            api_key=api_key,
            base_url=base_url,
            timeout_s=timeout_s,
            detail=detail,
            transport=transport,
        )


def to_openai_message(message: Message) -> dict:
    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id or "",
            "content": message.content,
        }
    body: dict = {"role": message.role, "content": message.content or ""}
    if message.tool_calls:
        body["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in message.tool_calls
        ]
    return body


def to_openai_tool(tool: ToolSpec) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def parse_openai(data: dict) -> Completion:
    choices = data.get("choices") or []
    if not choices:
        raise ProviderError("OpenRouter returned no choices")
    message = choices[0].get("message") or {}
    calls: list[ToolCall] = []
    for index, tool_call in enumerate(message.get("tool_calls") or []):
        function = tool_call.get("function") or {}
        calls.append(
            ToolCall(
                id=tool_call.get("id") or f"call_{index}",
                name=function.get("name") or "",
                arguments=parse_arguments(function.get("arguments")),
            )
        )
    usage = data.get("usage") or {}
    return Completion(
        text=message.get("content"),
        tool_calls=calls,
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        latency_ms=int(data.get("_latency_ms") or 0),
    )
