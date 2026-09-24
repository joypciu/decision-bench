from __future__ import annotations

import json
import time
from typing import Any

from decision_bench.domain import BotVersion, Check, Message, Run, ToolSpec
from decision_bench.packs import find_case
from decision_bench.ports import ModelProvider, ProviderError, RunStore
from decision_bench.scoring import schema_check, score_output, summarize


ABSOLUTE_DEPTH_CEILING = 8


def execute_run(
    repo: RunStore,
    providers: dict[str, ModelProvider],
    packs: dict,
    run_id: str,
) -> Run:
    run = repo.get_run(run_id)
    if run is None:
        raise KeyError(run_id)
    version = repo.get_version(run.bot_version_id)
    if version is None:
        raise KeyError(run.bot_version_id)
    started = time.perf_counter()
    prompt_tokens = 0
    completion_tokens = 0

    def elapsed() -> int:
        return int((time.perf_counter() - started) * 1000)

    def fail(message: str, output: dict | None = None, checks: list[Check] | None = None) -> Run:
        stored = checks or [Check("run", False, message)]
        _, score = summarize(stored)
        repo.finish_run(
            run.id,
            status="failed",
            error=message,
            output=output,
            schema_ok=False,
            passed=False,
            score=score if output is not None else 0,
            checks=[item.as_dict() for item in stored],
            latency_ms=elapsed(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        reloaded = repo.get_run(run.id)
        assert reloaded is not None
        return reloaded

    if run.depth > ABSOLUTE_DEPTH_CEILING:
        return fail("Depth ceiling reached.")

    provider = providers.get(run.provider)
    if provider is None:
        return fail(f"Unknown provider {run.provider}.")

    messages = [
        Message(role="system", content=system_prompt(version)),
        Message(role="user", content=run.input_text),
    ]
    tools = tool_specs(version)
    delegated = False
    last_output: dict | None = None

    try:
        for _ in range(version.max_steps):
            completion = provider.complete(
                model=run.model,
                messages=messages,
                tools=tools,
                schema=version.output_schema,
            )
            prompt_tokens += completion.prompt_tokens
            completion_tokens += completion.completion_tokens
            repo.add_step(
                run.id,
                "model",
                run.provider,
                {
                    "text": completion.text,
                    "tool_calls": [
                        {"id": call.id, "name": call.name, "arguments": call.arguments}
                        for call in completion.tool_calls
                    ],
                    "latency_ms": completion.latency_ms,
                },
            )
            if prompt_tokens + completion_tokens > version.max_tokens:
                return fail("Token budget exhausted.", last_output)

            if completion.tool_calls:
                messages.append(
                    Message(role="assistant", content=completion.text or "", tool_calls=completion.tool_calls)
                )
                for call in completion.tool_calls:
                    if call.name == "read_case":
                        payload = {"input": run.input_text}
                    elif call.name == "delegate":
                        payload, did_delegate = handle_delegate(
                            repo, providers, packs, run, version, call.arguments
                        )
                        delegated = delegated or did_delegate
                    elif call.name == "finish":
                        output = call.arguments if isinstance(call.arguments, dict) else {}
                        check = schema_check(output, version.output_schema)
                        repo.add_step(run.id, "schema", "finish", check.as_dict())
                        if version.require_delegation and not delegated:
                            payload = {"error": "Delegate to an allowed bot before finish."}
                        elif check.passed:
                            return complete_run(
                                repo,
                                packs,
                                run,
                                version,
                                output,
                                prompt_tokens,
                                completion_tokens,
                                elapsed(),
                                delegated,
                            )
                        else:
                            last_output = output
                            payload = {"error": check.detail}
                    else:
                        payload = {"error": f"Unknown tool {call.name}."}
                    repo.add_step(run.id, "tool_result", call.name, payload)
                    messages.append(
                        Message(
                            role="tool",
                            content=json.dumps(payload),
                            tool_call_id=call.id,
                            name=call.name,
                        )
                    )
                continue

            parsed = parse_object(completion.text)
            if parsed is not None and not (version.require_delegation and not delegated):
                check = schema_check(parsed, version.output_schema)
                repo.add_step(run.id, "schema", "text", check.as_dict())
                if check.passed:
                    return complete_run(
                        repo,
                        packs,
                        run,
                        version,
                        parsed,
                        prompt_tokens,
                        completion_tokens,
                        elapsed(),
                        delegated,
                    )
                last_output = parsed
            messages.append(Message(role="assistant", content=completion.text or ""))
            messages.append(Message(role="user", content="Call the finish tool with a schema-valid object."))
    except ProviderError as exc:
        return fail(str(exc), last_output)

    return fail("Step budget exhausted.", last_output)


def complete_run(
    repo: RunStore,
    packs: dict,
    run: Run,
    version: BotVersion,
    output: dict,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: int,
    delegated: bool,
) -> Run:
    case = find_case(packs, run.pack_id, run.case_id)
    child_count = len(repo.list_children(run.id))
    checks = score_output(
        output,
        version.output_schema,
        case,
        child_count,
        enforce_children=version.require_delegation,
    )
    if version.require_delegation and case is None and not delegated:
        checks.append(Check("delegation", False, "This bot must spawn a sub-agent before it finishes."))
    passed, score = summarize(checks)
    repo.finish_run(
        run.id,
        status="succeeded",
        error=None,
        output=output,
        schema_ok=True,
        passed=passed,
        score=score,
        checks=[item.as_dict() for item in checks],
        latency_ms=latency_ms,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
    )
    reloaded = repo.get_run(run.id)
    assert reloaded is not None
    return reloaded


def handle_delegate(
    repo: RunStore,
    providers: dict[str, ModelProvider],
    packs: dict,
    run: Run,
    version: BotVersion,
    arguments: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    bot_id = str(arguments.get("bot_id") or "")
    task = str(arguments.get("task") or "Review the case and finish with your schema.")
    if bot_id not in version.allowed_bot_ids:
        return {"error": "That bot is not on this bot's allowlist.", "bot_id": bot_id}, False
    if run.depth >= version.max_child_depth:
        return {"error": "Child depth budget exhausted."}, False
    child_version = repo.latest_version(bot_id)
    if child_version is None:
        return {"error": "Bot not found.", "bot_id": bot_id}, False
    from decision_bench.services import new_run

    child = new_run(
        repo,
        version=child_version,
        input_text=f"{task}\n\nCASE:\n{run.input_text}",
        provider=run.provider,
        model=run.model,
        pack_id=run.pack_id,
        case_id=None,
        parent_run_id=run.id,
        root_run_id=run.root_run_id,
        depth=run.depth + 1,
    )
    finished = execute_run(repo, providers, packs, child.id)
    return {
        "bot_id": bot_id,
        "status": finished.status,
        "output": finished.output,
        "error": finished.error,
        "passed": finished.passed,
    }, True


def tool_specs(version: BotVersion) -> list[ToolSpec]:
    specs: list[ToolSpec] = []
    if "read_case" in version.allowed_tools:
        specs.append(
            ToolSpec(
                name="read_case",
                description="Return the case input for this run.",
                parameters={
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                },
            )
        )
    if (
        "delegate" in version.allowed_tools
        and version.allowed_bot_ids
        and version.max_child_depth > 0
    ):
        specs.append(
            ToolSpec(
                name="delegate",
                description="Spawn one allowed bot and wait for its structured result.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["bot_id", "task"],
                    "properties": {
                        "bot_id": {"type": "string", "enum": list(version.allowed_bot_ids)},
                        "task": {"type": "string"},
                    },
                },
            )
        )
    if "finish" in version.allowed_tools:
        specs.append(
            ToolSpec(
                name="finish",
                description="Submit the final decision. Arguments must match the output schema.",
                parameters=version.output_schema,
            )
        )
    return specs


def system_prompt(version: BotVersion) -> str:
    schema = json.dumps(version.output_schema, indent=2)
    children = ", ".join(version.allowed_bot_ids) or "none"
    rule = (
        "You must call delegate for the specialist bots before finish."
        if version.require_delegation
        else "Do not delegate. Call finish when the decision is ready."
    )
    return (
        f"{version.instructions.strip()}\n\n"
        f"Tools: {', '.join(version.allowed_tools) or 'none'}.\n"
        f"Bots you may spawn: {children}.\n"
        f"{rule}\n"
        "Call finish with an object that matches this JSON schema and no extra keys:\n"
        f"{schema}"
    )


def parse_object(text: str | None) -> dict | None:
    if not text:
        return None
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.startswith("json"):
            candidate = candidate[4:]
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
