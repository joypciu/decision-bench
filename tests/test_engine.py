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
    assert {child.bot_id for child in children} == {"security-checker", "migration-checker"}
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
