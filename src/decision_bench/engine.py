from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from decision_bench.domain import BotVersion, Check, Message, Run, ToolSpec
from decision_bench.packs import find_case
from decision_bench.ports import ModelProvider, ProviderError, RunStore
from decision_bench.scoring import schema_check, score_output, summarize
from decision_bench.websearch import fetch_url, web_search


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
    delegated = False
    last_output: dict | None = None
    steps_used = 0
    corrections = 0

    try:
        while steps_used < version.max_steps:
            steps_used += 1
            tools = available_tools(version, repo, run)
            offered = {spec.name for spec in tools}
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
            over_budget = prompt_tokens + completion_tokens > version.max_tokens
            has_finish = any(call.name == "finish" for call in completion.tool_calls)
            if over_budget and not has_finish:
                return fail("Token budget exhausted.", last_output)

            if completion.tool_calls and all(call.name not in offered for call in completion.tool_calls):
                messages.append(
                    Message(role="assistant", content=completion.text or "", tool_calls=completion.tool_calls)
                )
                for call in completion.tool_calls:
                    payload = {"error": f"{call.name} is closed. Call finish now."}
                    repo.add_step(run.id, "tool_result", call.name, payload)
                    messages.append(
                        Message(role="tool", content=json.dumps(payload), tool_call_id=call.id, name=call.name)
                    )
                if corrections < 2:
                    corrections += 1
                    steps_used -= 1
                continue

            if completion.tool_calls:
                messages.append(
                    Message(role="assistant", content=completion.text or "", tool_calls=completion.tool_calls)
                )
                ordered = order_tool_calls(completion.tool_calls)
                delegate_calls = [call for call in ordered if call.name == "delegate"]
                parallel_calls = [call for call in ordered if call.name == "delegate_parallel"]
                io_calls = [call for call in ordered if call.name in {"web_search", "fetch_url"}]
                if delegate_calls or parallel_calls or io_calls:
                    batch_results = run_parallel_tools(
                        repo, providers, packs, run, version, delegate_calls, parallel_calls, io_calls
                    )
                    delegated = delegated or any(
                        isinstance(payload, dict) and (payload.get("status") or payload.get("results"))
                        for _call, payload in batch_results
                    )
                    for call, payload in batch_results:
                        repo.add_step(run.id, "tool_result", call.name, payload)
                        messages.append(
                            Message(
                                role="tool",
                                content=json.dumps(payload),
                                tool_call_id=call.id,
                                name=call.name,
                            )
                        )
                for call in ordered:
                    if call.name in {"delegate", "delegate_parallel", "web_search", "fetch_url"}:
                        continue
                    if call.name == "read_case":
                        payload = {"input": run.input_text}
                    elif call.name == "finish":
                        output = call.arguments if isinstance(call.arguments, dict) else {}
                        check = schema_check(output, version.output_schema)
                        repo.add_step(run.id, "schema", "finish", check.as_dict())
                        missing = missing_bots(repo, run, version)
                        grounded = research_output_error(output, repo.list_steps(run.id))
                        covered = specialist_coverage_error(repo, run, output)
                        triage = triage_coverage_error(repo, run, output)
                        if missing:
                            payload = {"error": "Delegate to these bots before finish: " + ", ".join(missing)}
                        elif grounded:
                            payload = {"error": grounded}
                        elif covered:
                            payload = {"error": covered}
                        elif triage:
                            payload = {"error": triage}
                        elif version.require_delegation and not delegated:
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
                            if over_budget:
                                repo.add_step(run.id, "tool_result", call.name, payload)
                                return fail("Token budget exhausted.", last_output)
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
                saved = finish_if_a_checker_failed(
                    repo, packs, run, version, prompt_tokens, completion_tokens, elapsed()
                )
                if saved is not None:
                    return saved
                continue

            parsed = parse_object(completion.text)
            missing = missing_bots(repo, run, version)
            grounded = research_output_error(parsed, repo.list_steps(run.id)) if parsed else None
            covered = specialist_coverage_error(repo, run, parsed) if parsed else None
            triage = triage_coverage_error(repo, run, parsed) if parsed else None
            if parsed is not None and not missing and not grounded and not covered and not triage and not (version.require_delegation and not delegated):
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
            if missing:
                follow = "Delegate to these bots before finish: " + ", ".join(missing)
            elif grounded:
                follow = grounded
            elif covered:
                follow = covered
            elif triage:
                follow = triage
            else:
                follow = "Call the finish tool with a schema-valid object."
            messages.append(Message(role="user", content=follow))
    except ProviderError as exc:
        saved = finish_from_children(repo, packs, run, version, prompt_tokens, completion_tokens, elapsed(), str(exc))
        if saved is not None:
            return saved
        return fail(str(exc), last_output)

    saved = finish_from_children(
        repo, packs, run, version, prompt_tokens, completion_tokens, elapsed(), "Step budget exhausted."
    )
    if saved is not None:
        return saved
    return fail("Step budget exhausted.", last_output)


def finish_if_a_checker_failed(repo, packs, run, version, prompt_tokens, completion_tokens, latency_ms):
    children = repo.list_children(run.id)
    if not children or not any(child.status != "succeeded" for child in children):
        return None
    if missing_bots(repo, run, version):
        return None
    return finish_from_children(
        repo,
        packs,
        run,
        version,
        prompt_tokens,
        completion_tokens,
        latency_ms,
        "Decided from the checkers that finished.",
    )


def finish_from_children(repo, packs, run, version, prompt_tokens, completion_tokens, latency_ms, reason: str):
    children = repo.list_children(run.id)
    if not children:
        return None
    fallback = decision_from_children(version, children, reason, run.input_text)
    if fallback is None or not schema_check(fallback, version.output_schema).passed:
        return None
    return complete_run(
        repo,
        packs,
        run,
        version,
        fallback,
        prompt_tokens,
        completion_tokens,
        latency_ms,
        True,
    )


def decision_from_children(version: BotVersion, children: list, reason: str, case: str = "") -> dict | None:
    succeeded = [child for child in children if child.status == "succeeded" and isinstance(child.output, dict)]
    failed = [child for child in children if child.status != "succeeded"]
    props = set((version.output_schema.get("properties") or {}).keys())
    excerpt = " ".join(case.split())[:240]
    reason_text = reason.strip()[:180]
    if reason_text.lower().startswith("decided from"):
        note = (excerpt + " " + reason_text).strip()
    else:
        note = (excerpt + " Lead model unavailable (" + reason_text + ").").strip()
    if "severity" in props and "gaps" in props:
        return _incident_from_children(succeeded, failed, note)
    if "verdict" in props and "risks" in props:
        return _change_from_children(succeeded, failed, note, case)
    return None


def _incident_from_children(succeeded: list, failed: list, note: str) -> dict | None:
    severity_child = next((child for child in succeeded if child.output.get("severity") and "gaps" not in child.output), None)
    if severity_child is None:
        return None
    gaps_child = next((child for child in succeeded if "gaps" in child.output and "severity" not in child.output), None)
    if gaps_child is None:
        gaps = [f"{child.bot_id} did not finish: {child.error or 'no output'}"[:240] for child in failed] or [
            "The missing-facts checker did not finish."
        ]
        checks = ["Retry the missing-facts checker."]
    else:
        gaps = [str(item) for item in gaps_child.output.get("gaps") or []] or ["No gap was recorded."]
        checks = [str(item) for item in gaps_child.output.get("next_checks") or []] or ["Retry the missing-facts checker."]
    rationale = str(severity_child.output.get("rationale") or severity_child.output.get("severity"))
    failed_note = ""
    if failed:
        failed_note = " Failed checkers: " + ", ".join(child.bot_id for child in failed) + "."
    return {
        "severity": severity_child.output["severity"],
        "summary": f"{note} {rationale}{failed_note}"[:1000],
        "gaps": gaps,
        "next_checks": checks,
    }


def _change_from_children(succeeded: list, failed: list, note: str, case: str = "") -> dict | None:
    if not succeeded:
        return None
    risks = []
    for child in succeeded:
        output = child.output
        if output.get("risk_level") == "high" and "findings" in output:
            for finding in output.get("findings") or []:
                if isinstance(finding, dict) and finding.get("file"):
                    risks.append(
                        {
                            "severity": "high",
                            "file": str(finding["file"]),
                            "reason": str(finding.get("reason") or "Security finding.")[:300],
                        }
                    )
        elif output.get("risk_level") == "high" and "notes" in output:
            risks.append(
                {
                    "severity": "medium" if not risks else "high",
                    "file": str(output.get("file") or "schema") or "schema",
                    "reason": str(output.get("notes") or "A database migration needs a second look.")[:300],
                }
            )
        elif output.get("sources"):
            risks.append(
                {
                    "severity": "high",
                    "file": dependency_file(case),
                    "reason": str(output.get("summary") or "A public advisory matches a pinned version.")[:300],
                }
            )
    verdict = "ship" if not risks else "block"
    if len(risks) == 1 and risks[0]["severity"] == "medium":
        verdict = "revise"
    failed_note = ""
    if failed:
        failed_note = " Failed checkers: " + ", ".join(child.bot_id for child in failed) + "."
    return {"verdict": verdict, "summary": f"{note}{failed_note}"[:1000], "risks": risks}


def dependency_file(case: str) -> str:
    import re

    match = re.search(r"[\w./-]+\.(?:txt|toml|in)", case, flags=re.IGNORECASE)
    return match.group(0) if match else "dependency"


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

    requested = str(arguments.get("provider") or "").strip()
    if requested:
        live = providers.get(requested)
        if live is None or not getattr(live, "configured", False):
            return {"error": f"Provider {requested} is not available.", "bot_id": bot_id}, False
        child_provider = requested
        child_model = getattr(live, "default_model", "") or run.model
    else:
        override = run.provider != version.provider or run.model != version.model
        child_provider = run.provider if override else child_version.provider
        child_model = run.model if override else child_version.model
    child = new_run(
        repo,
        version=child_version,
        input_text=f"{task}\n\nCASE:\n{run.input_text}",
        provider=child_provider,
        model=child_model,
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
        "brief": child_brief(bot_id, finished),
    }, True


def order_tool_calls(calls: list) -> list:
    delegates = [call for call in calls if call.name in {"delegate", "delegate_parallel"}]
    rest = [call for call in calls if call.name not in {"delegate", "delegate_parallel", "finish"}]
    finishes = [call for call in calls if call.name == "finish"]
    return delegates + rest + finishes


def run_parallel_tools(repo, providers, packs, run, version, delegate_calls, parallel_calls, io_calls):
    def one(bot_id: str, task: str, provider: str = "") -> dict:
        payload, _ok = handle_delegate(
            repo,
            providers,
            packs,
            run,
            version,
            {"bot_id": bot_id, "task": task, "provider": provider},
        )
        return payload

    def io(call, arguments: dict) -> dict:
        if call.name == "web_search":
            query = str(arguments.get("query") or "")
            with gate:
                if used["web_search"] >= 1:
                    return {
                        "query": query,
                        "results": [],
                        "note": "Search limit reached. Do not search again. Finish from the case and earlier results.",
                    }
                used["web_search"] += 1
            return web_search(query, repo.list_provider_configs())
        with gate:
            if used["fetch_url"] >= 1:
                return {"error": "Fetch limit reached. Finish from the case and the page you already fetched."}
            used["fetch_url"] += 1
        return fetch_url(str(arguments.get("url") or ""))

    gate = threading.Lock()
    used = {
        "web_search": sum(1 for step in repo.list_steps(run.id) if step.name == "web_search"),
        "fetch_url": sum(1 for step in repo.list_steps(run.id) if step.name == "fetch_url"),
    }

    singles = []
    for call in delegate_calls:
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        singles.append(
            (
                call,
                str(arguments.get("bot_id") or ""),
                str(arguments.get("task") or ""),
                str(arguments.get("provider") or ""),
            )
        )
    ios = []
    for call in io_calls:
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        ios.append((call, arguments))

    search_ios = [item for item in ios if item[0].name == "web_search"]
    fetch_ios = [item for item in ios if item[0].name == "fetch_url"]
    results = []
    batch = [("delegate", call, bot_id, task, provider) for call, bot_id, task, provider in singles]
    batch += [("io", call, arguments, "", "") for call, arguments in search_ios]
    if len(batch) == 1 and batch[0][0] == "delegate":
        call, bot_id, task, provider = singles[0]
        results.append((call, one(bot_id, task, provider)))
    elif batch:
        def run_item(item):
            kind, call, first, second, third = item
            try:
                if kind == "delegate":
                    return call, one(first, second, third)
                return call, io(call, first)
            except Exception as exc:
                return call, {"error": str(exc)[:300]}

        with ThreadPoolExecutor(max_workers=min(4, len(batch))) as pool:
            results.extend(pool.map(run_item, batch))

    official = has_official_advisory([payload for _call, payload in results]) or has_official_advisory(
        [step.payload for step in repo.list_steps(run.id) if step.name == "web_search"]
    )
    if fetch_ios and official:
        for call, arguments in fetch_ios:
            results.append(
                (
                    call,
                    {
                        "url": str(arguments.get("url") or ""),
                        "note": "An official advisory is already in the search results. Finish from those snippets.",
                    },
                )
            )
    elif fetch_ios:
        fetch_batch = [("io", call, arguments, "", "") for call, arguments in fetch_ios]

        def run_fetch(item):
            _kind, call, arguments, _second, _third = item
            try:
                return call, io(call, arguments)
            except Exception as exc:
                return call, {"error": str(exc)[:300]}

        with ThreadPoolExecutor(max_workers=min(4, len(fetch_batch))) as pool:
            results.extend(pool.map(run_fetch, fetch_batch))

    for call in parallel_calls:
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        tasks = arguments.get("tasks") if isinstance(arguments.get("tasks"), list) else []
        pairs = [
            (
                str(item.get("bot_id") or ""),
                str(item.get("task") or ""),
                str(item.get("provider") or ""),
            )
            for item in tasks
            if isinstance(item, dict)
        ]
        if not pairs:
            results.append((call, {"error": "Each task needs bot_id and task."}))
            continue
        with ThreadPoolExecutor(max_workers=min(4, len(pairs))) as pool:
            payloads = list(pool.map(lambda item: one(item[0], item[1], item[2]), pairs))
        results.append((call, {"results": payloads}))
    return results


def has_official_advisory(payloads) -> bool:
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        for item in payload.get("results") or []:
            if not isinstance(item, dict) or not str(item.get("snippet") or "").strip():
                continue
            url = str(item.get("url") or "").lower()
            if any(part in url for part in ("nvd.nist.gov", "cve.org", "osv.dev", "github.com/advisories")):
                return True
    return False


def available_tools(version: BotVersion, repo: RunStore, run: Run) -> list[ToolSpec]:
    steps = repo.list_steps(run.id)
    used = {step.name for step in steps}
    hidden = {name for name in ("web_search", "fetch_url", "read_case") if name in used}
    if has_official_advisory([step.payload for step in steps if step.name == "web_search"]):
        hidden.add("fetch_url")
    return [spec for spec in tool_specs(version) if spec.name not in hidden]


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
                description="Spawn one allowed bot and wait for its structured result. Several delegate calls in one turn run at the same time.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["bot_id", "task"],
                    "properties": {
                        "bot_id": {"type": "string", "enum": list(version.allowed_bot_ids)},
                        "task": {"type": "string"},
                        "provider": {
                            "type": "string",
                            "description": "Optional configured provider for this child. Omit to use the child bot's saved provider.",
                        },
                    },
                },
            )
        )
        specs.append(
            ToolSpec(
                name="delegate_parallel",
                description="Spawn several allowed bots at the same time and wait for all of them.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["tasks"],
                    "properties": {
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["bot_id", "task"],
                                "properties": {
                                    "bot_id": {"type": "string", "enum": list(version.allowed_bot_ids)},
                                    "task": {"type": "string"},
                                    "provider": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            )
        )
    if "web_search" in version.allowed_tools:
        specs.append(
            ToolSpec(
                name="web_search",
                description="Search public web sources and return titles, URLs, and short snippets.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["query"],
                    "properties": {"query": {"type": "string"}},
                },
            )
        )
    if "fetch_url" in version.allowed_tools:
        specs.append(
            ToolSpec(
                name="fetch_url",
                description="Fetch the text of one public web page. Refuses local or private addresses.",
                parameters={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["url"],
                    "properties": {"url": {"type": "string"}},
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


def missing_bots(repo: RunStore, run: Run, version: BotVersion) -> list[str]:
    if not version.require_delegation or not version.allowed_bot_ids:
        return []
    seen = {child.bot_id for child in repo.list_children(run.id)}
    for step in repo.list_steps(run.id):
        if step.name not in {"delegate", "delegate_parallel"}:
            continue
        payload = step.payload or {}
        if payload.get("bot_id"):
            seen.add(str(payload["bot_id"]))
        for item in payload.get("results") or []:
            if isinstance(item, dict) and item.get("bot_id"):
                seen.add(str(item["bot_id"]))
    return [bot_id for bot_id in version.allowed_bot_ids if bot_id not in seen]


def research_output_error(output: dict | None, steps: list) -> str | None:
    if not isinstance(output, dict) or "sources" not in output or "summary" not in output:
        return None
    sources = output.get("sources")
    if not isinstance(sources, list):
        return None
    found: list[str] = []
    snippets: list[str] = []
    for step in steps:
        if step.name != "web_search":
            continue
        for item in (step.payload or {}).get("results") or []:
            if not isinstance(item, dict):
                continue
            url = _normal_url(str(item.get("url") or ""))
            if url:
                found.append(url)
            snippets.append(f"{item.get('title', '')} {item.get('snippet', '')}".lower())
    if sources and not found:
        return "sources must be URLs returned by web_search. Search once, or finish with an empty sources list."
    bad = []
    for source in sources:
        if not isinstance(source, dict):
            bad.append("invalid source")
            continue
        url = _normal_url(str(source.get("url") or ""))
        if url not in found:
            bad.append(url or "missing url")
    if bad:
        return "These source URLs were not in the search results: " + ", ".join(bad[:3])
    official = [url for url in found if _official_advisory(url)]
    cited = [_normal_url(str(source.get("url") or "")) for source in sources if isinstance(source, dict)]
    if official and not any(url in official for url in cited):
        return "sources must include an official advisory from the search results: " + official[0]
    blob = " ".join(snippets)
    drifted = [
        phrase
        for phrase in (
            "auth bypass",
            "authentication",
            "drop table",
            "alter table",
            "migration",
            "users table",
            "pull request",
            "should not ship",
            "should ship",
            "cannot ship",
        )
        if phrase in str(output.get("summary") or "").lower() and phrase not in blob
    ]
    if drifted:
        return "summary must describe only the external lookup. Remove: " + ", ".join(drifted)
    return None


def _normal_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def _official_advisory(url: str) -> bool:
    return any(part in url for part in ("nvd.nist.gov", "cve.org", "osv.dev", "github.com/advisories"))


def triage_coverage_error(repo: RunStore, run: Run, output: dict | None) -> str | None:
    if not isinstance(output, dict) or "severity" not in output or "gaps" not in output:
        return None
    for child in repo.list_children(run.id):
        if child.status != "succeeded" or not isinstance(child.output, dict):
            continue
        child_output = child.output
        child_severity = child_output.get("severity")
        if child_severity and "gaps" not in child_output and child_severity != output.get("severity"):
            return f"severity must match the severity checker ({child_severity})."
        if "gaps" in child_output and "severity" not in child_output:
            lead_gaps = " ".join(str(item) for item in output.get("gaps") or []).lower()
            child_gaps = [str(gap) for gap in child_output.get("gaps") or [] if str(gap).strip()]
            if child_gaps and not any(gap.lower() in lead_gaps for gap in child_gaps):
                return "gaps must include a gap named by the missing-facts checker: " + child_gaps[0]
    return None


def specialist_coverage_error(repo: RunStore, run: Run, output: dict | None) -> str | None:
    if not isinstance(output, dict) or "risks" not in output:
        return None
    blob = json.dumps(output.get("risks") or []).lower()
    missing = []
    for child in repo.list_children(run.id):
        if child.status != "succeeded" or not isinstance(child.output, dict):
            continue
        child_output = child.output
        tokens: list[str] = []
        if child_output.get("risk_level") == "high":
            for finding in child_output.get("findings") or []:
                if isinstance(finding, dict) and finding.get("file"):
                    tokens.append(str(finding["file"]).lower())
            named = str(child_output.get("file") or "").strip().lower()
            if named:
                tokens.append(named)
            notes = str(child_output.get("notes") or "").lower()
            tokens.extend(
                token
                for token in notes.replace("\\", "/").split()
                if "." in token and token.strip(".,:;")[-3:] in {".py", "txt", "sql", "yml", "aml"}
            )
        summary = str(child_output.get("summary") or "").lower()
        if child_output.get("sources") and "pyyaml" in summary:
            tokens.append("pyyaml")
        if tokens and not any(token.strip(".,:;") in blob for token in tokens):
            missing.append(f"{child.bot_id} ({tokens[0].strip('.,:;')})")
    if not missing:
        return None
    return "Add a risk for each child that reported a problem. Missing: " + ", ".join(missing)


def child_brief(bot_id: str, finished: Run) -> str:
    output = finished.output if isinstance(finished.output, dict) else {}
    if finished.status != "succeeded":
        return f"{bot_id} failed: {finished.error or 'no output'}"
    if "findings" in output:
        files = [str(item.get("file")) for item in output.get("findings") or [] if isinstance(item, dict)]
        return f"{bot_id} risk_level={output.get('risk_level')} files={files or ['none']}"
    if "notes" in output and "risk_level" in output:
        return f"{bot_id} risk_level={output.get('risk_level')} notes={str(output.get('notes'))[:180]}"
    if "sources" in output:
        titles = [str(item.get("title")) for item in output.get("sources") or [] if isinstance(item, dict)]
        return f"{bot_id} sources={titles[:3] or ['none']} summary={str(output.get('summary'))[:220]}"
    if "severity" in output and "gaps" not in output:
        return f"{bot_id} severity={output.get('severity')}"
    if "gaps" in output:
        return f"{bot_id} gaps={output.get('gaps')}"
    return f"{bot_id} status=succeeded"


def system_prompt(version: BotVersion) -> str:
    schema = json.dumps(version.output_schema, indent=2)
    children = ", ".join(version.allowed_bot_ids) or "none"
    rule = (
        "Call delegate once for every bot in the allowlist before finish. "
        "Delegate calls in the same turn run at the same time. "
        "Decide only from each child's brief and output. Do not invent a file a child did not name."
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
