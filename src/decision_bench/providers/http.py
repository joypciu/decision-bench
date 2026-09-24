from __future__ import annotations

import json
import time
from typing import Any

import httpx

from decision_bench.domain import Completion, Message, ToolCall, ToolSpec
from decision_bench.ports import ProviderError


def post_json(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    payload: dict,
) -> dict:
    response: httpx.Response | None = None
    for attempt in range(2):
        started = time.perf_counter()
        response = client.post(url, headers=headers, json=payload)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code in {429, 503} and attempt == 0:
            time.sleep(1.0)
            continue
        if response.status_code >= 400:
            raise ProviderError(f"HTTP {response.status_code}: {response.text[:400]}")
        data = response.json()
        data["_latency_ms"] = elapsed_ms
        return data
    raise ProviderError("Provider request failed")


def tool_call_payload(calls: list[ToolCall]) -> list[dict[str, Any]]:
    dumped = []
    for call in calls:
        dumped.append({"id": call.id, "name": call.name, "arguments": call.arguments})
    return dumped


def parse_arguments(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ProviderError("Tool arguments must be a JSON object")
    return parsed


def message_text(messages: list[Message]) -> str:
    return "\n".join(message.content for message in messages if message.content)
