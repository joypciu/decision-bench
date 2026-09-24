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
    import time

    state = app.state.work

    class SlowProvider:
        name = "slow"
        configured = True
        detail = "slow"
        default_model = "slow"

        def complete(self, *, model, messages, tools, schema):
            del model, messages, tools, schema
            time.sleep(0.35)
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
    started = time.perf_counter()
    run = start_run(state, bot_id=parent.id, text="case", provider="script", model="script")
    elapsed = time.perf_counter() - started
    children = state.repo.list_children(run.id)
    assert run.status == "succeeded"
    assert run.output == {"answer": "done"}
    assert {child.bot_id for child in children} == {left.id, right.id}
    assert all(child.status == "succeeded" and child.provider == "slow" for child in children)
    assert elapsed < 0.7


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


def test_token_budget_stops_the_run(app):
    state = app.state.work
    state.providers["script"] = ScriptProvider(
        [finish({"answer": "too big"}, prompt_tokens=80, completion_tokens=80)]
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
