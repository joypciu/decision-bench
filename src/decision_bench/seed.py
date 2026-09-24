from __future__ import annotations

from decision_bench.domain import Bot, BotVersion, TaskPack
from decision_bench.ports import RunStore
from decision_bench.storage.sqlite import now

SECURITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risk_level", "findings"],
    "properties": {
        "risk_level": {"type": "string", "enum": ["none", "low", "high"]},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["file", "reason"],
                "properties": {
                    "file": {"type": "string"},
                    "reason": {"type": "string"},
                },
            },
        },
    },
}

MIGRATION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["risk_level", "notes"],
    "properties": {
        "risk_level": {"type": "string", "enum": ["none", "low", "high"]},
        "notes": {"type": "string"},
    },
}

SEVERITY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["severity", "rationale"],
    "properties": {
        "severity": {"type": "string", "enum": ["sev1", "sev2", "sev3"]},
        "rationale": {"type": "string"},
    },
}

GAPS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gaps", "next_checks"],
    "properties": {
        "gaps": {"type": "array", "items": {"type": "string"}},
        "next_checks": {"type": "array", "items": {"type": "string"}},
    },
}


RESEARCH_SCHEMA = {
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
                "properties": {
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                },
            },
        },
    },
}


def seed_templates(repo: RunStore, packs: dict[str, TaskPack]) -> None:
    ensure(
        repo,
        bot_id="security-checker",
        name="Security checker",
        summary="Flags weakened authentication and exposed secrets.",
        instructions=(
            "You review a diff for authentication, authorization, and secret handling. "
            "Call finish with risk_level high when the change weakens an auth check or exposes a secret. "
            "Otherwise use risk_level none. Cite the file."
        ),
        schema=SECURITY_SCHEMA,
        tools=["read_case", "web_search", "fetch_url", "finish"],
        children=[],
        require_delegation=False,
        max_steps=3,
        max_child_depth=0,
        pack_id=None,
    )
    ensure(
        repo,
        bot_id="migration-checker",
        name="Migration checker",
        summary="Flags database schema changes.",
        instructions=(
            "You review a diff for database migrations. Call finish with risk_level high when the diff "
            "adds a migration, ALTER TABLE, or DROP TABLE. Otherwise use risk_level none."
        ),
        schema=MIGRATION_SCHEMA,
        tools=["read_case", "web_search", "fetch_url", "finish"],
        children=[],
        require_delegation=False,
        max_steps=3,
        max_child_depth=0,
        pack_id=None,
    )
    ensure(
        repo,
        bot_id="research-checker",
        name="Research checker",
        summary="Looks up public sources when the case is missing a fact.",
        instructions=(
            "You search for public facts the case does not already contain. "
            "Call web_search when a name, product, or error needs an outside source. "
            "Call fetch_url only for a public http or https page. "
            "Then finish with a short summary and the sources you actually used. "
            "If the case is self-contained, finish with an empty sources list."
        ),
        schema=RESEARCH_SCHEMA,
        tools=["read_case", "web_search", "fetch_url", "finish"],
        children=[],
        require_delegation=False,
        max_steps=4,
        max_child_depth=0,
        pack_id=None,
    )
    ensure(
        repo,
        bot_id="change-lead",
        name="Change-risk lead",
        summary="Spawns the checkers, then returns ship, revise, or block.",
        instructions=(
            "You lead a change-risk review. Delegate to security-checker and migration-checker before you finish. "
            "Return verdict block when the security checker reports risk_level high, revise when only the "
            "migration checker reports risk_level high, otherwise ship. Put the evidence in summary and risks."
        ),
        schema=packs["change_risk"].output_schema,
        tools=["read_case", "delegate", "delegate_parallel", "web_search", "fetch_url", "finish"],
        children=["security-checker", "migration-checker", "research-checker"],
        require_delegation=True,
        max_steps=6,
        max_child_depth=1,
        pack_id="change_risk",
    )
    ensure(
        repo,
        bot_id="severity-checker",
        name="Severity checker",
        summary="Assigns sev1, sev2, or sev3.",
        instructions=(
            "You assign incident severity. sev1 means a broad outage, all users, or data loss. "
            "sev2 means a degraded shared service. sev3 means a narrow impact. Call finish with severity and rationale."
        ),
        schema=SEVERITY_SCHEMA,
        tools=["read_case", "web_search", "fetch_url", "finish"],
        children=[],
        require_delegation=False,
        max_steps=3,
        max_child_depth=0,
        pack_id=None,
    )
    ensure(
        repo,
        bot_id="gaps-checker",
        name="Missing-facts checker",
        summary="Lists missing facts and the next checks.",
        instructions=(
            "You list facts the incident report does not establish and the checks a human should run next. "
            "Call finish with gaps and next_checks."
        ),
        schema=GAPS_SCHEMA,
        tools=["read_case", "web_search", "fetch_url", "finish"],
        children=[],
        require_delegation=False,
        max_steps=3,
        max_child_depth=0,
        pack_id=None,
    )
    ensure(
        repo,
        bot_id="incident-lead",
        name="Incident lead",
        summary="Spawns the checkers, then returns a triage decision.",
        instructions=(
            "You lead incident triage. Delegate to severity-checker and gaps-checker before you finish. "
            "Use sev1 for a broad outage, all users, or data loss, sev2 for a degraded shared service, "
            "and sev3 for a narrow impact. Return severity, summary, gaps, and next_checks."
        ),
        schema=packs["incident_triage"].output_schema,
        tools=["read_case", "delegate", "delegate_parallel", "web_search", "fetch_url", "finish"],
        children=["severity-checker", "gaps-checker", "research-checker"],
        require_delegation=True,
        max_steps=6,
        max_child_depth=1,
        pack_id="incident_triage",
    )


def ensure(
    repo: RunStore,
    *,
    bot_id: str,
    name: str,
    summary: str,
    instructions: str,
    schema: dict,
    tools: list[str],
    children: list[str],
    require_delegation: bool,
    max_steps: int,
    max_child_depth: int,
    pack_id: str | None,
) -> None:
    existing = repo.get_bot(bot_id)
    if existing is not None:
        current = repo.latest_version(bot_id)
        if current is None:
            return
        missing_tools = not set(tools).issubset(set(current.allowed_tools))
        missing_children = not set(children).issubset(set(current.allowed_bot_ids))
        if not missing_tools and not missing_children:
            return
        repo.add_version(
            BotVersion(
                id="",
                bot_id=bot_id,
                version=0,
                instructions=instructions,
                provider=current.provider,
                model=current.model,
                output_schema=current.output_schema,
                allowed_tools=list(dict.fromkeys([*current.allowed_tools, *tools])),
                allowed_bot_ids=list(dict.fromkeys([*current.allowed_bot_ids, *children])),
                max_steps=current.max_steps,
                max_child_depth=current.max_child_depth,
                max_tokens=current.max_tokens,
                require_delegation=current.require_delegation,
                pack_id=current.pack_id,
                created_at=now(),
            )
        )
        return
    created = now()
    repo.upsert_bot(Bot(id=bot_id, name=name, summary=summary, created_at=created))
    repo.add_version(
        BotVersion(
            id="",
            bot_id=bot_id,
            version=0,
            instructions=instructions,
            provider="demo",
            model="demo",
            output_schema=schema,
            allowed_tools=tools,
            allowed_bot_ids=children,
            max_steps=max_steps,
            max_child_depth=max_child_depth,
            max_tokens=12000,
            require_delegation=require_delegation,
            pack_id=pack_id,
            created_at=created,
        )
    )
