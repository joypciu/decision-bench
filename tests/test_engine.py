from decision_bench.domain import Completion, ToolCall
from decision_bench.ports import ProviderError
from decision_bench.services import create_bot, run_eval, start_run


class ScriptProvider:
    name = "script"
    configured = True
    detail = "test double"

    def __init__(self, items):
        self.items = list(items)

    def complete(self, *, model, messages, tools, schema):
        del model, messages, tools, schema
        if not self.items:
            raise ProviderError("script exhausted")
        return self.items.pop(0)


def completion(calls, prompt_tokens=5, completion_tokens=5):
    return Completion(
        text=None,
        tool_calls=calls,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=1,
    )


def finish(payload, **tokens):
    return completion([ToolCall("finish", "finish", payload)], **tokens)


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {"answer": {"type": "string"}},
}


def test_gold_sets_pass_on_the_demo_provider(app):
    state = app.state.work
    change = run_eval(
        state,
        bot_id="change-lead",
        pack_id="change_risk",
        provider="demo",
        model="demo",
    )
    incident = run_eval(
        state,
        bot_id="incident-lead",
        pack_id="incident_triage",
        provider="demo",
        model="demo",
    )
    assert (change.pass_count, change.case_count) == (2, 2)
    assert (incident.pass_count, incident.case_count) == (2, 2)
    sample = state.repo.get_run(change.results[0]["run_id"])
    children = state.repo.list_children(sample.id)
    assert {child.bot_id for child in children} == {
        "security-checker",
        "migration-checker",
        "research-checker",
    }
    assert all(child.parent_run_id == sample.id for child in children)


def test_disallowed_delegate_is_rejected(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [completion([ToolCall("1", "delegate", {"bot_id": "nope", "task": "look"})])]
    )
    _bot, _version = create_bot(
        state,
        name="Picker",
        summary="",
        instructions="Delegate only to the allowlist.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["security-checker"],
        max_steps=1,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=_bot.id, text="case", provider="script", model="script")
    assert run.status == "failed"
    steps = state.repo.list_steps(run.id)
    assert any(step.payload.get("error", "").startswith("That bot is not") for step in steps)


def test_invalid_finish_can_be_repaired(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [finish({"answer": 1}), finish({"answer": "kept"})]
    )
    bot, _version = create_bot(
        state,
        name="Repair",
        summary="",
        instructions="Finish with answer.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=3,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="case", provider="script", model="script")
    assert run.status == "succeeded"
    assert run.output == {"answer": "kept"}
    assert run.schema_ok is True


def test_parallel_delegates_overlap_and_a_failure_does_not_cancel_the_sibling(app):
    from threading import Barrier

    state = app.state.work
    overlap = Barrier(2, timeout=10)

    class SlowProvider:
        name = "slow"
        configured = True
        detail = "slow"
        default_model = "slow"

        def complete(self, *, model, messages, tools, schema):
            del model, messages, tools, schema
            # Neither completion can finish until both calls are active.
            # A serial implementation breaks the barrier and fails child success.
            overlap.wait()
            return finish({"answer": "ok"})

    state.providers["slow"] = SlowProvider()
    left, _version = create_bot(
        state,
        name="Left",
        summary="",
        instructions="Finish.",
        provider="slow",
        model="slow",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    right, _version = create_bot(
        state,
        name="Right",
        summary="",
        instructions="Finish.",
        provider="slow",
        model="slow",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": left.id, "task": "left"}),
                    ToolCall("b", "delegate", {"bot_id": right.id, "task": "right"}),
                    ToolCall("c", "delegate", {"bot_id": "missing-bot", "task": "nope"}),
                ]
            ),
            finish({"answer": "done"}),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Parallel lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=[left.id, right.id],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="case", provider="script", model="script")
    children = state.repo.list_children(run.id)
    assert run.status == "succeeded"
    assert run.output == {"answer": "done"}
    assert {child.bot_id for child in children} == {left.id, right.id}
    assert all(child.status == "succeeded" and child.provider == "slow" for child in children)
    assert not overlap.broken


def test_search_and_delegate_run_in_one_turn(app, monkeypatch):
    monkeypatch.setattr(
        "decision_bench.engine.web_search",
        lambda query, configs: {"query": query, "results": [{"title": "Auth note", "url": "https://example.com/auth"}]},
    )
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("s", "web_search", {"query": "auth bypass"}),
                    ToolCall("d", "delegate", {"bot_id": "security-checker", "task": "Review the diff."}),
                ]
            ),
            finish({"answer": "checked"}),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Search lead",
        summary="",
        instructions="Search and delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["web_search", "delegate", "finish"],
        allowed_bot_ids=["security-checker"],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="auth.py bypass auth", provider="script", model="script")
    steps = state.repo.list_steps(run.id)
    assert run.status == "succeeded"
    assert any(step.name == "web_search" and step.payload["results"][0]["title"] == "Auth note" for step in steps)
    children = state.repo.list_children(run.id)
    assert len(children) == 1
    assert children[0].provider == "demo"
    assert children[0].output["risk_level"] == "high"


def test_delegate_can_choose_a_provider_for_one_child(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": "security-checker", "task": "Review.", "provider": "demo"}),
                    ToolCall("b", "delegate", {"bot_id": "migration-checker", "task": "Review.", "provider": "missing"}),
                ]
            ),
            finish({"answer": "split"}),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Split lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["security-checker", "migration-checker"],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="readme only", provider="script", model="script")
    children = state.repo.list_children(run.id)
    steps = state.repo.list_steps(run.id)
    assert run.status == "succeeded"
    assert len(children) == 1
    assert children[0].bot_id == "security-checker"
    assert children[0].provider == "demo"
    assert any(
        str((step.payload or {}).get("error") or "").startswith("Provider missing")
        for step in steps
    )


def test_open_run_shows_progress_before_it_finishes(app):
    from decision_bench.services import open_run, perform_run

    state = app.state.work
    run = open_run(state, bot_id="change-lead", text="diff --git a/README.md b/README.md\n+Hello there\n", provider="demo")
    assert run.status == "running"
    assert state.repo.list_children(run.id) == []
    perform_run(state, run.id)
    finished = state.repo.get_run(run.id)
    assert finished.status == "succeeded"
    assert len(state.repo.list_children(run.id)) >= 2


def test_finish_waits_until_every_specialist_was_called(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion([ToolCall("a", "delegate", {"bot_id": "security-checker", "task": "Review."})]),
            finish({"answer": "too early"}),
            finish({"answer": "still early"}),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Incomplete lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["security-checker", "migration-checker"],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="readme only", provider="script", model="script")
    steps = state.repo.list_steps(run.id)
    assert run.status == "failed"
    assert any("migration-checker" in str((step.payload or {}).get("error") or "") for step in steps)


def test_research_summary_cannot_decide_the_pull_request():
    from decision_bench.engine import research_output_error

    class Step:
        name = "web_search"
        payload = {
            "results": [
                {
                    "title": "CVE-2020-14343",
                    "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343",
                    "snippet": "yaml.load before 5.4 allows arbitrary code execution.",
                }
            ]
        }

    sources = [{"title": "CVE-2020-14343", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}]
    verdict = research_output_error(
        {
            "summary": "PyYAML 5.3.1 is covered by CVE-2020-14343, so the pull request should not ship.",
            "sources": sources,
        },
        [Step()],
    )
    assert verdict is not None
    assert "pull request" in verdict
    assert research_output_error(
        {
            "summary": "PyYAML 5.3.1 is affected by CVE-2020-14343. The fix landed in 5.4.",
            "sources": sources,
        },
        [Step()],
    ) is None


def test_research_must_cite_an_official_advisory_when_search_returned_one():
    from decision_bench.engine import research_output_error

    class Step:
        name = "web_search"
        payload = {
            "results": [
                {
                    "title": "CVE-2020-14343",
                    "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343",
                    "snippet": "yaml.load before 5.4.",
                },
                {"title": "PyPI", "url": "https://pypi.org/project/PyYAML/", "snippet": "Package page."},
            ]
        }

    rejected = research_output_error(
        {
            "summary": "PyYAML 5.3.1 is affected by CVE-2020-14343.",
            "sources": [{"title": "PyPI", "url": "https://pypi.org/project/PyYAML/"}],
        },
        [Step()],
    )
    assert rejected is not None
    assert "nvd.nist.gov" in rejected
    assert research_output_error(
        {
            "summary": "PyYAML 5.3.1 is affected by CVE-2020-14343. The fix landed in 5.4.",
            "sources": [{"title": "CVE-2020-14343", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
        },
        [Step()],
    ) is None


def test_incident_lead_must_keep_the_severity_checker_result(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": "severity-checker", "task": "Assign severity."}),
                    ToolCall("b", "delegate", {"bot_id": "gaps-checker", "task": "List gaps."}),
                    ToolCall("c", "delegate", {"bot_id": "research-checker", "task": "Look up public facts."}),
                ]
            ),
            finish(
                {
                    "severity": "sev3",
                    "summary": "Narrow issue.",
                    "gaps": ["Something else."],
                    "next_checks": ["Look later."],
                }
            ),
            finish(
                {
                    "severity": "sev1",
                    "summary": "Production API outage. All users are affected.",
                    "gaps": ["Start time and customer communication are not both confirmed."],
                    "next_checks": ["Confirm the blast radius.", "Compare with the last healthy deploy."],
                }
            ),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Triage script",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["severity", "summary", "gaps", "next_checks"],
            "properties": {
                "severity": {"type": "string"},
                "summary": {"type": "string"},
                "gaps": {"type": "array"},
                "next_checks": {"type": "array"},
            },
        },
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["severity-checker", "gaps-checker", "research-checker"],
        max_steps=4,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(
        state,
        bot_id=parent.id,
        text="Production API outage. All users are affected and there is data loss.",
        provider="script",
        model="script",
    )
    assert run.status == "succeeded"
    assert run.output["severity"] == "sev1"


def test_a_lead_finishes_from_checkers_when_its_model_becomes_unavailable(app):
    state = app.state.work
    provider = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": "severity-checker", "task": "Assign severity."}),
                    ToolCall("b", "delegate", {"bot_id": "gaps-checker", "task": "List gaps."}),
                    ToolCall("c", "delegate", {"bot_id": "research-checker", "task": "Look up public facts."}),
                ]
            )
        ]
    )
    original = provider.complete

    def complete(*, model, messages, tools, schema):
        if provider.items:
            return original(model=model, messages=messages, tools=tools, schema=schema)
        raise ProviderError("HTTP 429: quota")

    provider.complete = complete
    state.providers["script"] = provider
    parent, _version = create_bot(
        state,
        name="Quota lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["severity", "summary", "gaps", "next_checks"],
            "properties": {
                "severity": {"type": "string", "enum": ["sev1", "sev2", "sev3"]},
                "summary": {"type": "string"},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "next_checks": {"type": "array", "items": {"type": "string"}},
            },
        },
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["severity-checker", "gaps-checker", "research-checker"],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(
        state,
        bot_id=parent.id,
        text="Production API outage. All users are affected and there is data loss.",
        provider="script",
        model="script",
    )
    assert run.status == "succeeded"
    assert run.output["severity"] == "sev1"
    assert "429" in run.output["summary"]
    assert any("customer communication" in gap.lower() for gap in run.output["gaps"])


def test_a_failed_checker_does_not_require_another_lead_model_call(app):
    class BoomProvider:
        name = "boom"
        configured = True
        detail = "fails"
        default_model = "boom"

        def complete(self, *, model, messages, tools, schema):
            del model, messages, tools, schema
            raise ProviderError("HTTP 429: quota")

    state = app.state.work
    state.providers["boom"] = BoomProvider()
    broken, _broken_version = create_bot(
        state,
        name="Broken checker",
        summary="",
        instructions="Fail.",
        provider="boom",
        model="boom",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    calls = []
    provider = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": "severity-checker", "task": "Assign severity."}),
                    ToolCall("b", "delegate", {"bot_id": broken.id, "task": "List gaps."}),
                ]
            )
        ]
    )
    original = provider.complete

    def complete(*, model, messages, tools, schema):
        calls.append(1)
        if not provider.items:
            raise ProviderError("lead should not be called again")
        return original(model=model, messages=messages, tools=tools, schema=schema)

    provider.complete = complete
    state.providers["script"] = provider
    parent, _version = create_bot(
        state,
        name="Partial lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["severity", "summary", "gaps", "next_checks"],
            "properties": {
                "severity": {"type": "string", "enum": ["sev1", "sev2", "sev3"]},
                "summary": {"type": "string"},
                "gaps": {"type": "array", "items": {"type": "string"}},
                "next_checks": {"type": "array", "items": {"type": "string"}},
            },
        },
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["severity-checker", broken.id],
        max_steps=3,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(
        state,
        bot_id=parent.id,
        text="Production API outage. All users are affected and there is data loss.",
        provider="script",
        model="script",
    )
    assert calls == [1]
    assert run.status == "succeeded"
    assert run.output["severity"] == "sev1"
    assert "Lead model unavailable" not in run.output["summary"]
    assert "Decided from the checkers" in run.output["summary"]
    assert "429" in " ".join(run.output["gaps"])


def test_research_finish_rejects_a_source_the_search_did_not_return(app, monkeypatch):
    monkeypatch.setattr(
        "decision_bench.engine.web_search",
        lambda query, configs: {
            "query": query,
            "results": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343", "snippet": "PyYAML before 5.4."}],
        },
    )
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion([ToolCall("s", "web_search", {"query": "PyYAML 5.3.1"})]),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "Invented", "url": "https://example.com/invented"}],
                }
            ),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
                }
            ),
        ]
    )
    bot, _version = create_bot(
        state,
        name="Research script",
        summary="",
        instructions="Search once.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "sources"],
            "properties": {
                "summary": {"type": "string"},
                "sources": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["title", "url"],
                        "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
                    },
                },
            },
        },
        allowed_tools=["web_search", "finish"],
        allowed_bot_ids=[],
        max_steps=4,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="requirements.txt adds pyyaml==5.3.1", provider="script", model="script")
    assert run.status == "succeeded"
    assert run.output["sources"][0]["url"] == "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"


def test_lead_must_copy_a_high_finding_into_risks(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("a", "delegate", {"bot_id": "security-checker", "task": "Review."}),
                    ToolCall("b", "delegate", {"bot_id": "migration-checker", "task": "Review."}),
                    ToolCall("c", "delegate", {"bot_id": "research-checker", "task": "Review."}),
                ]
            ),
            finish(
                {
                    "verdict": "block",
                    "summary": "blocked",
                    "risks": [{"severity": "high", "file": "README.md", "reason": "wording"}],
                }
            ),
            finish(
                {
                    "verdict": "block",
                    "summary": "blocked",
                    "risks": [{"severity": "high", "file": "auth.py", "reason": "bypass"}],
                }
            ),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Coverage lead",
        summary="",
        instructions="Delegate.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "summary", "risks"],
            "properties": {
                "verdict": {"type": "string"},
                "summary": {"type": "string"},
                "risks": {"type": "array"},
            },
        },
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["security-checker", "migration-checker", "research-checker"],
        max_steps=4,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="auth.py bypass auth", provider="script", model="script")
    steps = state.repo.list_steps(run.id)
    assert run.status == "succeeded"
    assert run.output["risks"][0]["file"] == "auth.py"
    assert any("security-checker" in str((step.payload or {}).get("error") or "") for step in steps)


def test_used_search_is_not_offered_again(app, monkeypatch):
    monkeypatch.setattr(
        "decision_bench.engine.web_search",
        lambda query, configs: {
            "query": query,
            "results": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343", "snippet": "PyYAML"}],
        },
    )
    state = app.state.work
    seen = []
    provider = ScriptProvider(
        [
            completion([ToolCall("s", "web_search", {"query": "PyYAML 5.3.1"})]),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
                }
            ),
        ]
    )
    original = provider.complete

    def complete(*, model, messages, tools, schema):
        seen.append([tool.name for tool in tools])
        return original(model=model, messages=messages, tools=tools, schema=schema)

    provider.complete = complete
    state.providers["script"] = provider
    bot, _version = create_bot(
        state,
        name="One search",
        summary="",
        instructions="Search once.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "sources"],
            "properties": {
                "summary": {"type": "string"},
                "sources": {"type": "array"},
            },
        },
        allowed_tools=["web_search", "fetch_url", "finish"],
        allowed_bot_ids=[],
        max_steps=3,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="pyyaml==5.3.1", provider="script", model="script")
    assert run.status == "succeeded"
    assert "web_search" in seen[0]
    assert "web_search" not in seen[1]
    assert "fetch_url" not in seen[1]


def test_a_closed_tool_call_does_not_consume_the_step_budget(app, monkeypatch):
    monkeypatch.setattr(
        "decision_bench.engine.web_search",
        lambda query, configs: {
            "query": query,
            "results": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343", "snippet": "PyYAML"}],
        },
    )
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion([ToolCall("s", "web_search", {"query": "PyYAML"})]),
            completion([ToolCall("again", "web_search", {"query": "PyYAML again"})]),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
                }
            ),
        ]
    )
    bot, _version = create_bot(
        state,
        name="Closed search",
        summary="",
        instructions="Search once.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "sources"],
            "properties": {"summary": {"type": "string"}, "sources": {"type": "array"}},
        },
        allowed_tools=["web_search", "finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="pyyaml==5.3.1", provider="script", model="script")
    assert run.status == "succeeded"
    assert run.output["sources"][0]["url"].startswith("https://nvd.nist.gov/")


def test_a_valid_finish_is_kept_when_that_call_crosses_the_token_budget(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [finish({"answer": "kept"}, prompt_tokens=80, completion_tokens=80)]
    )
    bot, _version = create_bot(
        state,
        name="Finish over budget",
        summary="",
        instructions="Finish.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=100,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="case", provider="script", model="script")
    assert run.status == "succeeded"
    assert run.output == {"answer": "kept"}


def test_only_one_search_runs_when_two_are_requested_together(app, monkeypatch):
    calls = []

    def fake_search(query, configs):
        calls.append(query)
        return {
            "query": query,
            "results": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343", "snippet": "PyYAML"}],
        }

    monkeypatch.setattr("decision_bench.engine.web_search", fake_search)
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("s1", "web_search", {"query": "PyYAML 5.3.1"}),
                    ToolCall("s2", "web_search", {"query": "PyYAML again"}),
                ]
            ),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
                }
            ),
        ]
    )
    bot, _version = create_bot(
        state,
        name="Double search",
        summary="",
        instructions="Search once.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "sources"],
            "properties": {"summary": {"type": "string"}, "sources": {"type": "array"}},
        },
        allowed_tools=["web_search", "finish"],
        allowed_bot_ids=[],
        max_steps=3,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="pyyaml==5.3.1", provider="script", model="script")
    steps = state.repo.list_steps(run.id)
    assert run.status == "succeeded"
    assert calls == ["PyYAML 5.3.1"]
    assert any("Search limit reached" in str((step.payload or {}).get("note") or "") for step in steps)


def test_fetch_is_skipped_when_search_already_returned_an_advisory(app, monkeypatch):
    monkeypatch.setattr(
        "decision_bench.engine.web_search",
        lambda query, configs: {
            "query": query,
            "results": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343", "snippet": "PyYAML before 5.4."}],
        },
    )
    fetched = []
    monkeypatch.setattr("decision_bench.engine.fetch_url", lambda url: fetched.append(url) or {"url": url, "text": "page"})
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion(
                [
                    ToolCall("s", "web_search", {"query": "PyYAML 5.3.1"}),
                    ToolCall("f", "fetch_url", {"url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}),
                ]
            ),
            finish(
                {
                    "summary": "CVE-2020-14343 affects PyYAML before 5.4.",
                    "sources": [{"title": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2020-14343"}],
                }
            ),
        ]
    )
    bot, _version = create_bot(
        state,
        name="Skip fetch",
        summary="",
        instructions="Search once.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema={
            "type": "object",
            "additionalProperties": False,
            "required": ["summary", "sources"],
            "properties": {"summary": {"type": "string"}, "sources": {"type": "array"}},
        },
        allowed_tools=["web_search", "fetch_url", "finish"],
        allowed_bot_ids=[],
        max_steps=3,
        max_child_depth=0,
        max_tokens=4000,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="pyyaml==5.3.1", provider="script", model="script")
    steps = state.repo.list_steps(run.id)
    assert run.status == "succeeded"
    assert fetched == []
    assert any("official advisory" in str((step.payload or {}).get("note") or "") for step in steps)


def test_a_second_delegate_includes_the_previous_result(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [
            completion([ToolCall("a", "delegate", {"bot_id": "security-checker", "task": "Review."})]),
            completion([ToolCall("b", "delegate", {"bot_id": "security-checker", "task": "Name the file again."})]),
            finish({"answer": "done"}),
        ]
    )
    parent, _version = create_bot(
        state,
        name="Follow-up lead",
        summary="",
        instructions="Delegate twice.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["delegate", "finish"],
        allowed_bot_ids=["security-checker"],
        max_steps=4,
        max_child_depth=1,
        max_tokens=4000,
        require_delegation=True,
    )
    run = start_run(state, bot_id=parent.id, text="auth.py bypass auth", provider="script", model="script")
    children = state.repo.list_children(run.id)
    assert run.status == "succeeded"
    assert len(children) == 2
    assert children[1].input_text.startswith("FOLLOW-UP:")
    assert "YOUR PREVIOUS RESULT" in children[1].input_text
    assert '"risk_level": "high"' in children[1].input_text
    assert "CASE:\nauth.py bypass auth" in children[1].input_text


def test_follow_up_page_runs_the_same_bot_again(app):
    from fastapi.testclient import TestClient

    client = TestClient(app)
    created = client.post(
        "/api/runs",
        json={"bot_id": "security-checker", "provider": "demo", "input": "auth.py bypass auth"},
    )
    run_id = created.json()["run"]["id"]
    page = client.post(f"/runs/{run_id}/follow-up", data={"task": "Confirm the file name."})
    assert page.status_code == 200
    assert "YOUR PREVIOUS RESULT" in page.text
    assert "high" in page.text
    assert "Ask a follow-up" in page.text


def test_token_budget_stops_the_run(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [Completion(text="too big", tool_calls=[], prompt_tokens=80, completion_tokens=80, latency_ms=1)]
    )
    bot, _version = create_bot(
        state,
        name="Budget",
        summary="",
        instructions="Finish.",
        provider="script",
        model="script",
        pack_id=None,
        output_schema=SCHEMA,
        allowed_tools=["finish"],
        allowed_bot_ids=[],
        max_steps=2,
        max_child_depth=0,
        max_tokens=100,
        require_delegation=False,
    )
    run = start_run(state, bot_id=bot.id, text="case", provider="script", model="script")
    assert run.status == "failed"
    assert run.error == "Token budget exhausted."
