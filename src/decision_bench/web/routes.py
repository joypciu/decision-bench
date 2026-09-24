from __future__ import annotations

import json
from dataclasses import asdict

from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from decision_bench.domain import BotVersion
from decision_bench.services import (
    AppState,
    create_bot,
    parse_schema,
    run_eval,
    run_tree,
    save_version,
    start_run,
)


def register_routes(app: FastAPI) -> None:
    @app.get("/api/health")
    def health() -> dict:
        state = work(app)
        return {
            "status": "ok",
            "providers": provider_rows(state),
            "bots": len(state.repo.list_bots()),
            "packs": sorted(state.packs),
        }

    @app.get("/api/bots")
    def api_bots() -> list[dict]:
        state = work(app)
        payload = []
        for bot in state.repo.list_bots():
            version = state.repo.latest_version(bot.id)
            payload.append({"bot": asdict(bot), "version": asdict(version) if version else None})
        return payload

    @app.post("/api/bots")
    def api_create_bot(body: dict) -> dict:
        state = work(app)
        try:
            bot, version = create_bot(state, **bot_kwargs(body))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"bot": asdict(bot), "version": asdict(version)}

    @app.post("/api/runs")
    def api_run(body: dict) -> dict:
        state = work(app)
        try:
            run = start_run(
                state,
                bot_id=str(body.get("bot_id") or ""),
                text=str(body.get("input") or ""),
                provider=body.get("provider") or None,
                model=body.get("model") or None,
                case_id=body.get("case_id"),
                pack_id=body.get("pack_id"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run": asdict(run), "tree": tree_dict(run_tree(state, run.id))}

    @app.get("/api/runs/{run_id}")
    def api_get_run(run_id: str) -> dict:
        state = work(app)
        node = run_tree(state, run_id)
        if node is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return tree_dict(node)

    @app.post("/api/evals")
    def api_eval(body: dict) -> dict:
        state = work(app)
        try:
            result = run_eval(
                state,
                bot_id=str(body.get("bot_id") or ""),
                pack_id=str(body.get("pack_id") or ""),
                provider=str(body.get("provider") or "demo"),
                model=body.get("model") or None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return asdict(result)

    @app.get("/")
    def dashboard(request: Request):
        state = work(request.app)
        return render(
            request,
            "dashboard.html",
            active="home",
            runs=state.repo.list_root_runs(12),
            evals=state.repo.list_evals(5),
            bot_names=bot_name_map(state),
        )

    @app.get("/bots")
    def bots_page(request: Request):
        state = work(request.app)
        rows = []
        for bot in state.repo.list_bots():
            version = state.repo.latest_version(bot.id)
            rows.append({"bot": bot, "version": version})
        return render(request, "bots.html", active="bots", rows=rows)

    @app.get("/bots/new")
    def new_bot_page(request: Request):
        state = work(request.app)
        source = request.query_params.get("from")
        prefill = prefill_from_bot(state, source) if source else default_prefill(state)
        return render(request, "bot_form.html", active="bots", error=None, prefill=prefill, **form_lists(state))

    @app.post("/bots")
    async def create_bot_page(request: Request):
        state = work(request.app)
        form = await request.form()
        try:
            body = form_to_body(form)
            bot, _version = create_bot(state, **bot_kwargs(body))
        except ValueError as exc:
            return render(
                request,
                "bot_form.html",
                status_code=400,
                active="bots",
                error=str(exc),
                prefill=safe_prefill(form),
                **form_lists(state),
            )
        return RedirectResponse(f"/bots/{bot.id}", status_code=303)

    @app.get("/bots/{bot_id}")
    def bot_detail(request: Request, bot_id: str):
        state = work(request.app)
        bot = state.repo.get_bot(bot_id)
        if bot is None:
            return RedirectResponse("/bots", status_code=303)
        return render(
            request,
            "bot_detail.html",
            active="bots",
            bot=bot,
            versions=state.repo.list_versions(bot_id),
            latest=state.repo.latest_version(bot_id),
            error=request.query_params.get("error"),
            **form_lists(state),
        )

    @app.post("/bots/{bot_id}/versions")
    async def new_version(request: Request, bot_id: str):
        state = work(request.app)
        form = await request.form()
        body = form_to_body(form)
        try:
            save_version(state, bot_id, **{key: body[key] for key in body if key not in {"name", "summary"}})
        except ValueError as exc:
            return RedirectResponse(f"/bots/{bot_id}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/bots/{bot_id}", status_code=303)

    @app.get("/packs")
    def packs_page(request: Request):
        state = work(request.app)
        return render(request, "packs.html", active="packs", pack_list=list(state.packs.values()))

    @app.post("/runs")
    async def create_run_page(request: Request):
        state = work(request.app)
        form = await request.form()
        try:
            run = start_run(
                state,
                bot_id=str(form.get("bot_id") or ""),
                text=str(form.get("input") or ""),
                provider=str(form.get("provider") or "") or None,
                model=str(form.get("model") or "") or None,
            )
        except ValueError as exc:
            return RedirectResponse(f"/bots/{form.get('bot_id')}?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        state = work(request.app)
        node = run_tree(state, run_id)
        if node is None:
            return RedirectResponse("/", status_code=303)
        return render(request, "run_detail.html", active="home", node=node)

    @app.get("/evals")
    def evals_page(request: Request):
        state = work(request.app)
        selected = None
        eval_id = request.query_params.get("id")
        if eval_id:
            selected = state.repo.get_eval(eval_id)
        return render(
            request,
            "evals.html",
            active="evals",
            evals=state.repo.list_evals(20),
            selected=selected,
            error=request.query_params.get("error"),
            bot_names=bot_name_map(state),
        )

    @app.post("/evals")
    async def create_eval_page(request: Request):
        state = work(request.app)
        form = await request.form()
        try:
            result = run_eval(
                state,
                bot_id=str(form.get("bot_id") or ""),
                pack_id=str(form.get("pack_id") or ""),
                provider=str(form.get("provider") or "demo"),
                model=str(form.get("model") or "") or None,
            )
        except ValueError as exc:
            return RedirectResponse(f"/evals?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/evals?id={result.id}", status_code=303)


def work(app: FastAPI) -> AppState:
    return app.state.work


def provider_rows(state: AppState) -> list[dict]:
    return [
        {"name": provider.name, "configured": provider.configured, "detail": provider.detail}
        for provider in state.providers.values()
    ]


def bot_name_map(state: AppState) -> dict[str, str]:
    return {bot.id: bot.name for bot in state.repo.list_bots()}


def form_lists(state: AppState) -> dict:
    return {
        "provider_rows": provider_rows(state),
        "pack_list": list(state.packs.values()),
        "pack_options": [
            {"id": pack.id, "name": pack.name, "output_schema": pack.output_schema}
            for pack in state.packs.values()
        ],
        "all_bots": state.repo.list_bots(),
    }


def default_prefill(state: AppState) -> dict:
    pack = next(iter(state.packs.values()))
    return {
        "name": "",
        "summary": "",
        "instructions": "Decide from the case. Call finish with an object that matches the schema.",
        "provider": "demo",
        "model": "demo",
        "pack_id": pack.id,
        "output_schema": pack.output_schema,
        "allowed_tools": ["read_case", "finish"],
        "allowed_bot_ids": [],
        "max_steps": 6,
        "max_child_depth": 1,
        "max_tokens": 8000,
        "require_delegation": False,
    }


def prefill_from_bot(state: AppState, bot_id: str) -> dict:
    bot = state.repo.get_bot(bot_id)
    version = state.repo.latest_version(bot_id)
    if bot is None or version is None:
        return default_prefill(state)
    return version_prefill(bot.name, bot.summary, version)


def version_prefill(name: str, summary: str, version: BotVersion) -> dict:
    return {
        "name": name,
        "summary": summary,
        "instructions": version.instructions,
        "provider": version.provider,
        "model": version.model,
        "pack_id": version.pack_id or "",
        "output_schema": version.output_schema,
        "allowed_tools": version.allowed_tools,
        "allowed_bot_ids": version.allowed_bot_ids,
        "max_steps": version.max_steps,
        "max_child_depth": version.max_child_depth,
        "max_tokens": version.max_tokens,
        "require_delegation": version.require_delegation,
    }


def form_to_body(form) -> dict:
    pack_id = str(form.get("pack_id") or "").strip() or None
    schema_raw = str(form.get("output_schema") or "").strip()
    return {
        "name": str(form.get("name") or ""),
        "summary": str(form.get("summary") or ""),
        "instructions": str(form.get("instructions") or ""),
        "provider": str(form.get("provider") or "demo"),
        "model": str(form.get("model") or ""),
        "pack_id": pack_id,
        "output_schema": parse_schema(schema_raw) if schema_raw else {},
        "allowed_tools": list(form.getlist("allowed_tools")),
        "allowed_bot_ids": list(form.getlist("allowed_bot_ids")),
        "max_steps": parse_int(form.get("max_steps"), "max_steps"),
        "max_child_depth": parse_int(form.get("max_child_depth"), "max_child_depth"),
        "max_tokens": parse_int(form.get("max_tokens"), "max_tokens"),
        "require_delegation": form.get("require_delegation") == "on",
    }


def bot_kwargs(body: dict) -> dict:
    schema = body.get("output_schema")
    if isinstance(schema, str):
        schema = parse_schema(schema)
    return {
        "name": str(body.get("name") or ""),
        "summary": str(body.get("summary") or ""),
        "instructions": str(body.get("instructions") or ""),
        "provider": str(body.get("provider") or "demo"),
        "model": str(body.get("model") or ""),
        "pack_id": body.get("pack_id") or None,
        "output_schema": schema or {},
        "allowed_tools": list(body.get("allowed_tools") or []),
        "allowed_bot_ids": list(body.get("allowed_bot_ids") or []),
        "max_steps": int(body.get("max_steps") or 6),
        "max_child_depth": int(body.get("max_child_depth") or 0),
        "max_tokens": int(body.get("max_tokens") or 8000),
        "require_delegation": bool(body.get("require_delegation")),
    }


def safe_prefill(form) -> dict:
    try:
        schema = parse_schema(str(form.get("output_schema") or "{}"))
    except ValueError:
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    return {
        "name": str(form.get("name") or ""),
        "summary": str(form.get("summary") or ""),
        "instructions": str(form.get("instructions") or ""),
        "provider": str(form.get("provider") or "demo"),
        "model": str(form.get("model") or ""),
        "pack_id": str(form.get("pack_id") or ""),
        "output_schema": schema,
        "allowed_tools": list(form.getlist("allowed_tools")),
        "allowed_bot_ids": list(form.getlist("allowed_bot_ids")),
        "max_steps": form.get("max_steps") or 6,
        "max_child_depth": form.get("max_child_depth") or 0,
        "max_tokens": form.get("max_tokens") or 8000,
        "require_delegation": form.get("require_delegation") == "on",
    }


def parse_int(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer.") from exc


def render(request: Request, name: str, status_code: int = 200, **extra):
    state = work(request.app)
    context = {
        "active": "",
        "provider_rows": provider_rows(state),
        "bots": state.repo.list_bots(),
        "packs": state.packs,
        **extra,
    }
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
    )


def tree_dict(node) -> dict | None:
    if node is None:
        return None
    return {
        "run": asdict(node.run),
        "bot_name": node.bot_name,
        "steps": [asdict(step) for step in node.steps],
        "children": [tree_dict(child) for child in node.children],
    }
