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
    sleep=time.sleep,
) -> dict:
    response: httpx.Response | None = None
    attempt = 0
    while True:
        started = time.perf_counter()
        response = client.post(url, headers=headers, json=payload)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        attempt += 1
        if response.status_code in {429, 503} and attempt < _retry_limit(response.status_code):
            sleep(_retry_delay(response, attempt))
            continue
        if response.status_code >= 400:
            raise ProviderError(provider_error_message(response))
        data = response.json()
        data["_latency_ms"] = elapsed_ms
        return data


def _retry_limit(status_code: int) -> int:
    if status_code == 503:
        return 4
    if status_code == 429:
        return 2
    return 1


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    header = response.headers.get("retry-after")
    if header:
        try:
            return min(float(header), 8.0)
        except ValueError:
            pass
    return float(min(2 ** (attempt - 1), 8))


def provider_error_message(response: httpx.Response) -> str:
    message = response.text
    try:
        payload = response.json()
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])
        elif isinstance(error, str):
            message = error
    compact = " ".join(message.split())
    return f"HTTP {response.status_code}: {compact[:300]}"


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
