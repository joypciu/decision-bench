from __future__ import annotations

from typing import Any

import httpx

from decision_bench.domain import Completion, Message, ToolCall, ToolSpec
from decision_bench.ports import ProviderError
from decision_bench.providers.http import parse_arguments, post_json


class GeminiProvider:
    name = "gemini"
    configured = True

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_s: float,
        detail: str = "Google AI Studio",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.detail = detail
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
        model_name = model.removeprefix("models/")
        url = f"{self.base_url}/models/{model_name}:generateContent?key={self.api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_text(messages)}]},
            "contents": to_gemini_contents(messages),
            "tools": [{"functionDeclarations": [to_gemini_function(tool) for tool in tools]}],
        }
        with httpx.Client(timeout=self.timeout_s, transport=self._transport) as client:
            data = post_json(client, url, headers={}, payload=payload)
        return parse_gemini(data)


def system_text(messages: list[Message]) -> str:
    parts = [message.content for message in messages if message.role == "system" and message.content]
    return "\n\n".join(parts) or "Follow the tool schema."


def to_gemini_contents(messages: list[Message]) -> list[dict]:
    contents: list[dict] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            contents.append(
                {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "name": message.name or "tool",
                                "response": {"result": message.content},
                            }
                        }
                    ],
                }
            )
            continue
        parts: list[dict[str, Any]] = []
        if message.content:
            parts.append({"text": message.content})
        for call in message.tool_calls or []:
            parts.append({"functionCall": {"name": call.name, "args": call.arguments}})
        if parts:
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
    if not contents:
        contents.append({"role": "user", "parts": [{"text": "Review the case."}]})
    return contents


def to_gemini_function(tool: ToolSpec) -> dict:
    return {
        "name": tool.name,
        "description": tool.description,
        "parameters": to_gemini_schema(tool.parameters),
    }


def to_gemini_schema(schema: dict) -> dict:
    converted: dict[str, Any] = {}
    if "type" in schema:
        converted["type"] = str(schema["type"]).upper()
    if schema.get("description"):
        converted["description"] = schema["description"]
    if "enum" in schema:
        converted["enum"] = schema["enum"]
    if "properties" in schema:
        converted["properties"] = {key: to_gemini_schema(value) for key, value in schema["properties"].items()}
    if "required" in schema:
        converted["required"] = schema["required"]
    if "items" in schema and isinstance(schema["items"], dict):
        converted["items"] = to_gemini_schema(schema["items"])
    if not converted:
        converted = {"type": "OBJECT", "properties": {"note": {"type": "STRING"}}}
    return converted


def parse_gemini(data: dict) -> Completion:
    feedback = data.get("promptFeedback") or {}
    if feedback.get("blockReason"):
        raise ProviderError(f"Gemini blocked the prompt: {feedback['blockReason']}")
    candidates = data.get("candidates") or []
    if not candidates:
        raise ProviderError("Gemini returned no candidates")
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    texts: list[str] = []
    calls: list[ToolCall] = []
    for index, part in enumerate(parts):
        if part.get("text"):
            texts.append(part["text"])
        function_call = part.get("functionCall")
        if function_call:
            calls.append(
                ToolCall(
                    id=f"call_{index}",
                    name=function_call["name"],
                    arguments=parse_arguments(function_call.get("args") or {}),
                )
            )
    usage = data.get("usageMetadata") or {}
    return Completion(
        text="\n".join(texts) or None,
        tool_calls=calls,
        prompt_tokens=int(usage.get("promptTokenCount") or 0),
        completion_tokens=int(usage.get("candidatesTokenCount") or 0),
        latency_ms=int(data.get("_latency_ms") or 0),
    )
