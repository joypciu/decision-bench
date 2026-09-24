from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    thought_signature: str | None = None


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]


@dataclass
class Message:
    role: str
    content: str = ""
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class Completion:
    text: str | None
    tool_calls: list[ToolCall]
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


@dataclass
class Bot:
    id: str
    name: str
    summary: str
    created_at: str


@dataclass
class BotVersion:
    id: str
    bot_id: str
    version: int
    instructions: str
    provider: str
    model: str
    output_schema: dict[str, Any]
    allowed_tools: list[str]
    allowed_bot_ids: list[str]
    max_steps: int
    max_child_depth: int
    max_tokens: int
    require_delegation: bool
    pack_id: str | None
    created_at: str


@dataclass
class Run:
    id: str
    bot_version_id: str
    bot_id: str
    parent_run_id: str | None
    root_run_id: str
    pack_id: str | None
    case_id: str | None
    depth: int
    status: str
    input_text: str
    output: dict[str, Any] | None
    schema_ok: bool
    passed: bool
    score: int
    checks: list[dict[str, Any]]
    latency_ms: int
    prompt_tokens: int
    completion_tokens: int
    provider: str
    model: str
    error: str | None
    created_at: str
    finished_at: str | None


@dataclass
class Step:
    id: str
    run_id: str
    position: int
    kind: str
    name: str | None
    payload: dict[str, Any]
    created_at: str


@dataclass
class EvalRun:
    id: str
    bot_id: str
    bot_version_id: str
    pack_id: str
    provider: str
    model: str
    pass_count: int
    case_count: int
    avg_latency_ms: int
    total_tokens: int
    results: list[dict[str, Any]]
    created_at: str


@dataclass
class GoldCase:
    id: str
    title: str
    input: str
    expected: dict[str, Any]
    expect_min_children: int = 0


@dataclass
class TaskPack:
    id: str
    name: str
    description: str
    output_schema: dict[str, Any]
    cases: list[GoldCase] = field(default_factory=list)


@dataclass
class Check:
    name: str
    passed: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass
class RunNode:
    run: Run
    bot_name: str
    steps: list[Step]
    children: list[RunNode]
