from __future__ import annotations

import json
from typing import Any

from decision_bench.domain import Completion, Message, ToolCall, ToolSpec


class DemoProvider:
    name = "demo"
    configured = True
    detail = "Deterministic stand-in. Runs without an API key."
    default_model = "demo"

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        schema: dict,
    ) -> Completion:
        del model
        delegate = next((tool for tool in tools if tool.name == "delegate"), None)
        already_called = any(message.role == "tool" for message in messages)
        if delegate is not None and not already_called:
            enum = ((delegate.parameters.get("properties") or {}).get("bot_id") or {}).get("enum") or []
            calls = [
                ToolCall(
                    id=f"call_{index}",
                    name="delegate",
                    arguments={"bot_id": bot_id, "task": "Check your specialty and finish with your schema."},
                )
                for index, bot_id in enumerate(enum)
            ]
            if calls:
                return Completion(None, calls, 24, 16, 4)
        payload = infer_output(schema, messages)
        finish = next((tool for tool in tools if tool.name == "finish"), None)
        if finish is None:
            return Completion(json.dumps(payload), [], 24, 16, 4)
        return Completion(
            None,
            [ToolCall(id="call_finish", name="finish", arguments=payload)],
            24,
            18,
            4,
        )


def infer_output(schema: dict, messages: list[Message]) -> dict[str, Any]:
    props = set((schema.get("properties") or {}).keys())
    case = first_user_text(messages)
    payloads = tool_payloads(messages)
    if "verdict" in props:
        return change_lead(case, payloads)
    if "gaps" in props and "severity" in props and "summary" in props:
        return incident_lead(case)
    if "sources" in props and "summary" in props:
        return {"summary": "No external lookup was required for this case.", "sources": []}
    if "findings" in props:
        return security_checker(case)
    if "notes" in props and "risk_level" in props:
        return migration_checker(case)
    if "rationale" in props and "severity" in props:
        return {"severity": classify_severity(case), "rationale": "Classified from the reported impact."}
    if "gaps" in props and "next_checks" in props:
        return {
            "gaps": ["Start time and customer communication are not both confirmed."],
            "next_checks": ["Confirm the blast radius.", "Compare with the last healthy deploy."],
        }
    return fill_schema(schema)


def change_lead(case: str, payloads: list[dict]) -> dict[str, Any]:
    excerpt = " ".join(case.split())[:360] or "No material risk."
    security_high = any(
        item.get("risk_level") == "high" and "findings" in item for item in walk(payloads)
    )
    migration_high = any(item.get("risk_level") == "high" and "notes" in item for item in walk(payloads))
    if security_high:
        return {
            "verdict": "block",
            "summary": excerpt,
            "risks": [
                {
                    "severity": "high",
                    "file": "auth.py" if "auth.py" in case else "unknown",
                    "reason": "Authentication or secret handling was weakened.",
                }
            ],
        }
    if migration_high:
        return {
            "verdict": "revise",
            "summary": excerpt,
            "risks": [
                {
                    "severity": "medium",
                    "file": "schema",
                    "reason": "A database migration needs a second look.",
                }
            ],
        }
    return {"verdict": "ship", "summary": excerpt, "risks": []}


def incident_lead(case: str) -> dict[str, Any]:
    return {
        "severity": classify_severity(case),
        "summary": " ".join(case.split())[:360],
        "gaps": ["Customer communication status is not stated."],
        "next_checks": ["Confirm who is affected.", "Check the latest deploy."],
    }


def security_checker(case: str) -> dict[str, Any]:
    lowered = case.lower()
    high = any(
        word in lowered
        for word in ("bypass auth", "is_authenticated", "password", "api_key", "secret", "credential")
    )
    if not high:
        return {"risk_level": "none", "findings": []}
    return {
        "risk_level": "high",
        "findings": [
            {
                "file": "auth.py" if "auth.py" in lowered else "unknown",
                "reason": "Authentication or secret handling was weakened.",
            }
        ],
    }


def migration_checker(case: str) -> dict[str, Any]:
    lowered = case.lower()
    if any(word in lowered for word in ("drop table", "alter table", "migration")):
        return {"risk_level": "high", "notes": "A database migration is in the diff."}
    return {"risk_level": "none", "notes": "No schema change in the diff."}


def classify_severity(case: str) -> str:
    lowered = case.lower()
    if any(word in lowered for word in ("outage", "data loss", "all users")):
        return "sev1"
    if "degraded" in lowered:
        return "sev2"
    return "sev3"


def first_user_text(messages: list[Message]) -> str:
    for message in messages:
        if message.role != "user":
            continue
        if "CASE:\n" in message.content:
            return message.content.split("CASE:\n", 1)[1]
        return message.content
    return ""


def tool_payloads(messages: list[Message]) -> list[dict]:
    payloads = []
    for message in messages:
        if message.role != "tool":
            continue
        try:
            parsed = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            payloads.append(parsed)
    return payloads


def walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def fill_schema(schema: dict) -> Any:
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {key: fill_schema(child) for key, child in (schema.get("properties") or {}).items()}
    if kind == "array":
        return []
    if kind == "boolean":
        return False
    if kind in {"number", "integer"}:
        return 0
    return "demo"
