from __future__ import annotations

from typing import Protocol

from decision_bench.domain import (
    Bot,
    BotVersion,
    Completion,
    EvalRun,
    Message,
    Run,
    Step,
    ToolSpec,
)


class ProviderError(Exception):
    pass


class ModelProvider(Protocol):
    name: str
    configured: bool
    detail: str

    def complete(
        self,
        *,
        model: str,
        messages: list[Message],
        tools: list[ToolSpec],
        schema: dict,
    ) -> Completion: ...


class RunStore(Protocol):
    def migrate(self) -> None: ...
    def upsert_bot(self, bot: Bot) -> None: ...
    def get_bot(self, bot_id: str) -> Bot | None: ...
    def list_bots(self) -> list[Bot]: ...
    def add_version(self, version: BotVersion) -> BotVersion: ...
    def latest_version(self, bot_id: str) -> BotVersion | None: ...
    def get_version(self, version_id: str) -> BotVersion | None: ...
    def list_versions(self, bot_id: str) -> list[BotVersion]: ...
    def create_run(self, run: Run) -> Run: ...
    def finish_run(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None,
        output: dict | None,
        schema_ok: bool,
        passed: bool,
        score: int,
        checks: list[dict],
        latency_ms: int,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None: ...
    def get_run(self, run_id: str) -> Run | None: ...
    def list_root_runs(self, limit: int = 20) -> list[Run]: ...
    def list_children(self, parent_run_id: str) -> list[Run]: ...
    def add_step(self, run_id: str, kind: str, name: str | None, payload: dict) -> Step: ...
    def list_steps(self, run_id: str) -> list[Step]: ...
    def create_eval(self, eval_run: EvalRun) -> None: ...
    def list_evals(self, limit: int = 20) -> list[EvalRun]: ...
    def get_eval(self, eval_id: str) -> EvalRun | None: ...
