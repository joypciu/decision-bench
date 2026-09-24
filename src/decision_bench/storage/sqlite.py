from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from decision_bench.domain import Bot, BotVersion, EvalRun, ProviderConfig, Run, Step


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex


SCHEMA = """
CREATE TABLE IF NOT EXISTS bots (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_versions (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots(id),
    version INTEGER NOT NULL,
    instructions TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    output_schema TEXT NOT NULL,
    allowed_tools TEXT NOT NULL,
    allowed_bot_ids TEXT NOT NULL,
    max_steps INTEGER NOT NULL,
    max_child_depth INTEGER NOT NULL,
    max_tokens INTEGER NOT NULL,
    require_delegation INTEGER NOT NULL,
    pack_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (bot_id, version)
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    bot_version_id TEXT NOT NULL,
    bot_id TEXT NOT NULL,
    parent_run_id TEXT,
    root_run_id TEXT NOT NULL,
    pack_id TEXT,
    case_id TEXT,
    depth INTEGER NOT NULL,
    status TEXT NOT NULL,
    input_text TEXT NOT NULL,
    output_json TEXT,
    schema_ok INTEGER NOT NULL DEFAULT 0,
    passed INTEGER NOT NULL DEFAULT 0,
    score INTEGER NOT NULL DEFAULT 0,
    checks_json TEXT NOT NULL DEFAULT '[]',
    latency_ms INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS steps (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    name TEXT,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_runs (
    id TEXT PRIMARY KEY,
    bot_id TEXT NOT NULL,
    bot_version_id TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    pass_count INTEGER NOT NULL,
    case_count INTEGER NOT NULL,
    avg_latency_ms INTEGER NOT NULL,
    total_tokens INTEGER NOT NULL,
    results_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS provider_configs (
    name TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    api_key TEXT NOT NULL DEFAULT '',
    default_model TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);
CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at);
CREATE INDEX IF NOT EXISTS idx_steps_run ON steps(run_id, position);
"""


class SqliteRunStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def migrate(self) -> None:
        with self._conn() as connection:
            connection.executescript(SCHEMA)

    def upsert_bot(self, bot: Bot) -> None:
        with self._conn() as connection:
            connection.execute(
                """
                INSERT INTO bots (id, name, summary, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name = excluded.name, summary = excluded.summary
                """,
                (bot.id, bot.name, bot.summary, bot.created_at),
            )

    def get_bot(self, bot_id: str) -> Bot | None:
        with self._conn() as connection:
            row = connection.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)).fetchone()
        return _bot(row) if row else None

    def list_bots(self) -> list[Bot]:
        with self._conn() as connection:
            rows = connection.execute("SELECT * FROM bots ORDER BY name").fetchall()
        return [_bot(row) for row in rows]

    def add_version(self, version: BotVersion) -> BotVersion:
        with self._conn() as connection:
            current = connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM bot_versions WHERE bot_id = ?",
                (version.bot_id,),
            ).fetchone()
            number = int(current["version"]) + 1
            stored = BotVersion(
                id=f"{version.bot_id}-v{number}",
                bot_id=version.bot_id,
                version=number,
                instructions=version.instructions,
                provider=version.provider,
                model=version.model,
                output_schema=version.output_schema,
                allowed_tools=list(version.allowed_tools),
                allowed_bot_ids=list(version.allowed_bot_ids),
                max_steps=version.max_steps,
                max_child_depth=version.max_child_depth,
                max_tokens=version.max_tokens,
                require_delegation=version.require_delegation,
                pack_id=version.pack_id,
                created_at=version.created_at or now(),
            )
            connection.execute(
                """
                INSERT INTO bot_versions (
                    id, bot_id, version, instructions, provider, model, output_schema,
                    allowed_tools, allowed_bot_ids, max_steps, max_child_depth, max_tokens,
                    require_delegation, pack_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    stored.id,
                    stored.bot_id,
                    stored.version,
                    stored.instructions,
                    stored.provider,
                    stored.model,
                    json.dumps(stored.output_schema),
                    json.dumps(stored.allowed_tools),
                    json.dumps(stored.allowed_bot_ids),
                    stored.max_steps,
                    stored.max_child_depth,
                    stored.max_tokens,
                    int(stored.require_delegation),
                    stored.pack_id,
                    stored.created_at,
                ),
            )
        return stored

    def latest_version(self, bot_id: str) -> BotVersion | None:
        with self._conn() as connection:
            row = connection.execute(
                """
                SELECT * FROM bot_versions
                WHERE bot_id = ?
                ORDER BY version DESC
                LIMIT 1
                """,
                (bot_id,),
            ).fetchone()
        return _version(row) if row else None

    def get_version(self, version_id: str) -> BotVersion | None:
        with self._conn() as connection:
            row = connection.execute("SELECT * FROM bot_versions WHERE id = ?", (version_id,)).fetchone()
        return _version(row) if row else None

    def list_versions(self, bot_id: str) -> list[BotVersion]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM bot_versions WHERE bot_id = ? ORDER BY version DESC",
                (bot_id,),
            ).fetchall()
        return [_version(row) for row in rows]

    def create_run(self, run: Run) -> Run:
        with self._conn() as connection:
            connection.execute(
                """
                INSERT INTO runs (
                    id, bot_version_id, bot_id, parent_run_id, root_run_id, pack_id, case_id,
                    depth, status, input_text, output_json, schema_ok, passed, score, checks_json,
                    latency_ms, prompt_tokens, completion_tokens, provider, model, error,
                    created_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.bot_version_id,
                    run.bot_id,
                    run.parent_run_id,
                    run.root_run_id,
                    run.pack_id,
                    run.case_id,
                    run.depth,
                    run.status,
                    run.input_text,
                    json.dumps(run.output) if run.output is not None else None,
                    int(run.schema_ok),
                    int(run.passed),
                    run.score,
                    json.dumps(run.checks),
                    run.latency_ms,
                    run.prompt_tokens,
                    run.completion_tokens,
                    run.provider,
                    run.model,
                    run.error,
                    run.created_at,
                    run.finished_at,
                ),
            )
        return run

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
    ) -> None:
        with self._conn() as connection:
            connection.execute(
                """
                UPDATE runs
                SET status = ?, error = ?, output_json = ?, schema_ok = ?, passed = ?, score = ?,
                    checks_json = ?, latency_ms = ?, prompt_tokens = ?, completion_tokens = ?,
                    finished_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    error,
                    json.dumps(output) if output is not None else None,
                    int(schema_ok),
                    int(passed),
                    score,
                    json.dumps(checks),
                    latency_ms,
                    prompt_tokens,
                    completion_tokens,
                    now(),
                    run_id,
                ),
            )

    def get_run(self, run_id: str) -> Run | None:
        with self._conn() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    def list_root_runs(self, limit: int = 20) -> list[Run]:
        with self._conn() as connection:
            rows = connection.execute(
                """
                SELECT * FROM runs
                WHERE parent_run_id IS NULL
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_run(row) for row in rows]

    def list_children(self, parent_run_id: str) -> list[Run]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE parent_run_id = ? ORDER BY created_at",
                (parent_run_id,),
            ).fetchall()
        return [_run(row) for row in rows]

    def add_step(self, run_id: str, kind: str, name: str | None, payload: dict) -> Step:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 AS position FROM steps WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            step = Step(
                id=new_id(),
                run_id=run_id,
                position=int(row["position"]),
                kind=kind,
                name=name,
                payload=payload,
                created_at=now(),
            )
            connection.execute(
                """
                INSERT INTO steps (id, run_id, position, kind, name, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (step.id, step.run_id, step.position, step.kind, step.name, json.dumps(payload), step.created_at),
            )
        return step

    def list_steps(self, run_id: str) -> list[Step]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM steps WHERE run_id = ? ORDER BY position",
                (run_id,),
            ).fetchall()
        return [_step(row) for row in rows]

    def create_eval(self, eval_run: EvalRun) -> None:
        with self._conn() as connection:
            connection.execute(
                """
                INSERT INTO eval_runs (
                    id, bot_id, bot_version_id, pack_id, provider, model, pass_count,
                    case_count, avg_latency_ms, total_tokens, results_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_run.id,
                    eval_run.bot_id,
                    eval_run.bot_version_id,
                    eval_run.pack_id,
                    eval_run.provider,
                    eval_run.model,
                    eval_run.pass_count,
                    eval_run.case_count,
                    eval_run.avg_latency_ms,
                    eval_run.total_tokens,
                    json.dumps(eval_run.results),
                    eval_run.created_at,
                ),
            )

    def list_evals(self, limit: int = 20) -> list[EvalRun]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_eval(row) for row in rows]

    def get_eval(self, eval_id: str) -> EvalRun | None:
        with self._conn() as connection:
            row = connection.execute("SELECT * FROM eval_runs WHERE id = ?", (eval_id,)).fetchone()
        return _eval(row) if row else None

    def list_provider_configs(self) -> list[ProviderConfig]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT * FROM provider_configs ORDER BY builtin DESC, name"
            ).fetchall()
        return [_provider(row) for row in rows]

    def get_provider_config(self, name: str) -> ProviderConfig | None:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT * FROM provider_configs WHERE name = ?",
                (name,),
            ).fetchone()
        return _provider(row) if row else None

    def upsert_provider_config(self, config: ProviderConfig) -> None:
        with self._conn() as connection:
            connection.execute(
                """
                INSERT INTO provider_configs (
                    name, kind, base_url, api_key, default_model, enabled, builtin, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    kind = excluded.kind,
                    base_url = excluded.base_url,
                    api_key = excluded.api_key,
                    default_model = excluded.default_model,
                    enabled = excluded.enabled,
                    builtin = excluded.builtin
                """,
                (
                    config.name,
                    config.kind,
                    config.base_url,
                    config.api_key,
                    config.default_model,
                    int(config.enabled),
                    int(config.builtin),
                    config.created_at,
                ),
            )

    def delete_provider_config(self, name: str) -> None:
        with self._conn() as connection:
            connection.execute("DELETE FROM provider_configs WHERE name = ?", (name,))


def _provider(row: sqlite3.Row) -> ProviderConfig:
    return ProviderConfig(
        name=row["name"],
        kind=row["kind"],
        base_url=row["base_url"],
        api_key=row["api_key"],
        default_model=row["default_model"],
        enabled=bool(row["enabled"]),
        builtin=bool(row["builtin"]),
        created_at=row["created_at"],
    )


def _bot(row: sqlite3.Row) -> Bot:
    return Bot(id=row["id"], name=row["name"], summary=row["summary"], created_at=row["created_at"])


def _version(row: sqlite3.Row) -> BotVersion:
    return BotVersion(
        id=row["id"],
        bot_id=row["bot_id"],
        version=row["version"],
        instructions=row["instructions"],
        provider=row["provider"],
        model=row["model"],
        output_schema=json.loads(row["output_schema"]),
        allowed_tools=json.loads(row["allowed_tools"]),
        allowed_bot_ids=json.loads(row["allowed_bot_ids"]),
        max_steps=row["max_steps"],
        max_child_depth=row["max_child_depth"],
        max_tokens=row["max_tokens"],
        require_delegation=bool(row["require_delegation"]),
        pack_id=row["pack_id"],
        created_at=row["created_at"],
    )


def _run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        bot_version_id=row["bot_version_id"],
        bot_id=row["bot_id"],
        parent_run_id=row["parent_run_id"],
        root_run_id=row["root_run_id"],
        pack_id=row["pack_id"],
        case_id=row["case_id"],
        depth=row["depth"],
        status=row["status"],
        input_text=row["input_text"],
        output=json.loads(row["output_json"]) if row["output_json"] else None,
        schema_ok=bool(row["schema_ok"]),
        passed=bool(row["passed"]),
        score=row["score"],
        checks=json.loads(row["checks_json"] or "[]"),
        latency_ms=row["latency_ms"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        provider=row["provider"],
        model=row["model"],
        error=row["error"],
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _step(row: sqlite3.Row) -> Step:
    return Step(
        id=row["id"],
        run_id=row["run_id"],
        position=row["position"],
        kind=row["kind"],
        name=row["name"],
        payload=json.loads(row["payload"]),
        created_at=row["created_at"],
    )


def _eval(row: sqlite3.Row) -> EvalRun:
    return EvalRun(
        id=row["id"],
        bot_id=row["bot_id"],
        bot_version_id=row["bot_version_id"],
        pack_id=row["pack_id"],
        provider=row["provider"],
        model=row["model"],
        pass_count=row["pass_count"],
        case_count=row["case_count"],
        avg_latency_ms=row["avg_latency_ms"],
        total_tokens=row["total_tokens"],
        results=json.loads(row["results_json"]),
        created_at=row["created_at"],
    )


def loads_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value or "{}")
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("Expected a JSON object")
