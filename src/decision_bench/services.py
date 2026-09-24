from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Any

import jsonschema

from decision_bench.config import Settings
from decision_bench.domain import Bot, BotVersion, EvalRun, Run, RunNode, TaskPack
from decision_bench.engine import execute_run
from decision_bench.ports import ModelProvider, RunStore
from decision_bench.storage.sqlite import new_id, now


@dataclass
class AppState:
    settings: Settings
    repo: RunStore
    packs: dict[str, TaskPack]
    providers: dict[str, ModelProvider]


def resolve_model(settings: Settings, provider: str, model: str | None, providers: dict | None = None) -> str:
    cleaned = (model or "").strip()
    if cleaned:
        return cleaned
    if providers is not None:
        live = providers.get(provider)
        default = getattr(live, "default_model", "") if live is not None else ""
        if isinstance(default, str) and default.strip():
            return default.strip()
    if provider == "gemini":
        return settings.gemini_model
    if provider == "openrouter":
        return settings.openrouter_model
    return "demo"


def create_bot(
    state: AppState,
    *,
    name: str,
    summary: str,
    instructions: str,
    provider: str,
    model: str,
    pack_id: str | None,
    output_schema: dict,
    allowed_tools: list[str],
    allowed_bot_ids: list[str],
    max_steps: int,
    max_child_depth: int,
    max_tokens: int,
    require_delegation: bool,
) -> tuple[Bot, BotVersion]:
    cleaned_name = name.strip()
    if not cleaned_name or len(cleaned_name) > 80:
        raise ValueError("Name must be between 1 and 80 characters.")
    if not instructions.strip():
        raise ValueError("Instructions are required.")
    if provider not in state.providers:
        raise ValueError(f"Unknown provider {provider}.")
    if pack_id and pack_id not in state.packs:
        raise ValueError(f"Unknown pack {pack_id}.")
    validate_limits(max_steps, max_child_depth, max_tokens)
    jsonschema.Draft202012Validator.check_schema(output_schema)
    tools = normalize_tools(allowed_tools, allowed_bot_ids, require_delegation)
    known_bots = {bot.id for bot in state.repo.list_bots()}
    unknown = [bot_id for bot_id in allowed_bot_ids if bot_id not in known_bots]
    if unknown:
        raise ValueError("Unknown child bot: " + ", ".join(unknown))
    bot = Bot(id="bot-" + uuid.uuid4().hex[:8], name=cleaned_name, summary=summary.strip(), created_at=now())
    state.repo.upsert_bot(bot)
    version = state.repo.add_version(
        BotVersion(
            id="",
            bot_id=bot.id,
            version=0,
            instructions=instructions.strip(),
            provider=provider,
            model=resolve_model(state.settings, provider, model, state.providers),
            output_schema=output_schema,
            allowed_tools=tools,
            allowed_bot_ids=list(dict.fromkeys(allowed_bot_ids)),
            max_steps=max_steps,
            max_child_depth=max_child_depth,
            max_tokens=max_tokens,
            require_delegation=require_delegation,
            pack_id=pack_id or None,
            created_at=now(),
        )
    )
    return bot, version


def save_version(state: AppState, bot_id: str, **kwargs) -> BotVersion:
    bot = state.repo.get_bot(bot_id)
    if bot is None:
        raise ValueError("Bot not found.")
    current = state.repo.latest_version(bot_id)
    if current is None:
        raise ValueError("Bot has no version.")
    payload = {
        "instructions": current.instructions,
        "provider": current.provider,
        "model": current.model,
        "pack_id": current.pack_id,
        "output_schema": current.output_schema,
        "allowed_tools": current.allowed_tools,
        "allowed_bot_ids": current.allowed_bot_ids,
        "max_steps": current.max_steps,
        "max_child_depth": current.max_child_depth,
        "max_tokens": current.max_tokens,
        "require_delegation": current.require_delegation,
    }
    payload.update(kwargs)
    if payload["provider"] not in state.providers:
        raise ValueError(f"Unknown provider {payload['provider']}.")
    if payload["pack_id"] and payload["pack_id"] not in state.packs:
        raise ValueError(f"Unknown pack {payload['pack_id']}.")
    validate_limits(payload["max_steps"], payload["max_child_depth"], payload["max_tokens"])
    jsonschema.Draft202012Validator.check_schema(payload["output_schema"])
    known_bots = {item.id for item in state.repo.list_bots()}
    unknown = [item for item in payload["allowed_bot_ids"] if item not in known_bots]
    if unknown:
        raise ValueError("Unknown child bot: " + ", ".join(unknown))
    tools = normalize_tools(payload["allowed_tools"], payload["allowed_bot_ids"], payload["require_delegation"])
    return state.repo.add_version(
        BotVersion(
            id="",
            bot_id=bot_id,
            version=0,
            instructions=str(payload["instructions"]).strip(),
            provider=payload["provider"],
            model=resolve_model(state.settings, payload["provider"], payload["model"], state.providers),
            output_schema=payload["output_schema"],
            allowed_tools=tools,
            allowed_bot_ids=list(dict.fromkeys(payload["allowed_bot_ids"])),
            max_steps=int(payload["max_steps"]),
            max_child_depth=int(payload["max_child_depth"]),
            max_tokens=int(payload["max_tokens"]),
            require_delegation=bool(payload["require_delegation"]),
            pack_id=payload["pack_id"] or None,
            created_at=now(),
        )
    )


def open_run(
    state: AppState,
    *,
    bot_id: str,
    text: str,
    provider: str | None = None,
    model: str | None = None,
    case_id: str | None = None,
    pack_id: str | None = None,
) -> Run:
    version = state.repo.latest_version(bot_id)
    if version is None:
        raise ValueError("Bot not found.")
    cleaned = text.strip()
    if not cleaned:
        raise ValueError("Case input is required.")
    if len(cleaned) > 50_000:
        raise ValueError("Case input is limited to 50,000 characters.")
    chosen = provider or version.provider
    if chosen not in state.providers:
        raise ValueError(f"Unknown provider {chosen}.")
    run = new_run(
        state.repo,
        version=version,
        input_text=cleaned,
        provider=chosen,
        model=resolve_model(state.settings, chosen, model or (version.model if provider is None else model), state.providers),
        pack_id=pack_id if pack_id is not None else version.pack_id,
        case_id=case_id,
        parent_run_id=None,
        root_run_id=None,
        depth=0,
    )
    return run


def start_run(
    state: AppState,
    *,
    bot_id: str,
    text: str,
    provider: str | None = None,
    model: str | None = None,
    case_id: str | None = None,
    pack_id: str | None = None,
) -> Run:
    run = open_run(
        state,
        bot_id=bot_id,
        text=text,
        provider=provider,
        model=model,
        case_id=case_id,
        pack_id=pack_id,
    )
    return execute_run(state.repo, state.providers, state.packs, run.id)


def perform_run(state: AppState, run_id: str) -> None:
    try:
        execute_run(state.repo, state.providers, state.packs, run_id)
    except Exception as exc:
        current = state.repo.get_run(run_id)
        if current is not None and current.status == "running":
            state.repo.finish_run(
                run_id,
                status="failed",
                error=str(exc),
                output=current.output,
                schema_ok=False,
                passed=False,
                score=0,
                checks=current.checks,
                latency_ms=current.latency_ms,
                prompt_tokens=current.prompt_tokens,
                completion_tokens=current.completion_tokens,
            )


def new_run(
    repo: RunStore,
    *,
    version: BotVersion,
    input_text: str,
    provider: str,
    model: str,
    pack_id: str | None,
    case_id: str | None,
    parent_run_id: str | None,
    root_run_id: str | None,
    depth: int,
) -> Run:
    run_id = new_id()
    run = Run(
        id=run_id,
        bot_version_id=version.id,
        bot_id=version.bot_id,
        parent_run_id=parent_run_id,
        root_run_id=root_run_id or run_id,
        pack_id=pack_id,
        case_id=case_id,
        depth=depth,
        status="running",
        input_text=input_text,
        output=None,
        schema_ok=False,
        passed=False,
        score=0,
        checks=[],
        latency_ms=0,
        prompt_tokens=0,
        completion_tokens=0,
        provider=provider,
        model=model,
        error=None,
        created_at=now(),
        finished_at=None,
    )
    return repo.create_run(run)


def run_eval(
    state: AppState,
    *,
    bot_id: str,
    pack_id: str,
    provider: str,
    model: str | None,
) -> EvalRun:
    version = state.repo.latest_version(bot_id)
    if version is None:
        raise ValueError("Bot not found.")
    pack = state.packs.get(pack_id)
    if pack is None:
        raise ValueError("Pack not found.")
    if provider not in state.providers:
        raise ValueError(f"Unknown provider {provider}.")
    chosen_model = resolve_model(state.settings, provider, model, state.providers)
    results: list[dict[str, Any]] = []
    for case in pack.cases:
        run = start_run(
            state,
            bot_id=bot_id,
            text=case.input,
            provider=provider,
            model=chosen_model,
            case_id=case.id,
            pack_id=pack.id,
        )
        results.append(
            {
                "case_id": case.id,
                "title": case.title,
                "run_id": run.id,
                "passed": run.passed,
                "score": run.score,
                "latency_ms": run.latency_ms,
                "tokens": run.prompt_tokens + run.completion_tokens,
                "error": run.error,
                "checks": run.checks,
            }
        )
    pass_count = sum(1 for item in results if item["passed"])
    latencies = [item["latency_ms"] for item in results] or [0]
    eval_run = EvalRun(
        id=new_id(),
        bot_id=bot_id,
        bot_version_id=version.id,
        pack_id=pack_id,
        provider=provider,
        model=chosen_model,
        pass_count=pass_count,
        case_count=len(results),
        avg_latency_ms=round(sum(latencies) / len(latencies)),
        total_tokens=sum(item["tokens"] for item in results),
        results=results,
        created_at=now(),
    )
    state.repo.create_eval(eval_run)
    return eval_run


def run_tree(state: AppState, run_id: str) -> RunNode | None:
    run = state.repo.get_run(run_id)
    if run is None:
        return None
    bot = state.repo.get_bot(run.bot_id)
    return RunNode(
        run=run,
        bot_name=bot.name if bot else run.bot_id,
        steps=state.repo.list_steps(run_id),
        children=[child for child_run in state.repo.list_children(run_id) if (child := run_tree(state, child_run.id))],
    )


def validate_limits(max_steps: int, max_child_depth: int, max_tokens: int) -> None:
    if not 1 <= int(max_steps) <= 12:
        raise ValueError("max_steps must be between 1 and 12.")
    if not 0 <= int(max_child_depth) <= 3:
        raise ValueError("max_child_depth must be between 0 and 3.")
    if not 100 <= int(max_tokens) <= 20000:
        raise ValueError("max_tokens must be between 100 and 20000.")


def normalize_tools(tools: list[str], children: list[str], require_delegation: bool) -> list[str]:
    allowed = {"read_case", "delegate", "delegate_parallel", "web_search", "fetch_url", "finish"}
    chosen = [tool for tool in tools if tool in allowed]
    if "finish" not in chosen:
        chosen.append("finish")
    if children or require_delegation:
        if "delegate" not in chosen:
            chosen.append("delegate")
        if "delegate_parallel" not in chosen:
            chosen.append("delegate_parallel")
    return list(dict.fromkeys(chosen))


def parse_schema(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Output schema is not valid JSON: {exc.msg}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Output schema must be a JSON object.")
    return parsed
