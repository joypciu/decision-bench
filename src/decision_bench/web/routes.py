from __future__ import annotations

import json
import csv
import io
from dataclasses import asdict

from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, JSONResponse, Response

from decision_bench.documents import extract_document
from decision_bench.domain import BotVersion
from decision_bench.eval_compare import compare_evaluations
from decision_bench.present import decision_of, summary_of
from decision_bench.provider_admin import public_provider, remove_provider, save_provider
from decision_bench.services import (
    AppState,
    create_bot,
    open_follow_up,
    open_run,
    parse_schema,
    perform_run,
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

    @app.get("/api/runs/{run_id}/export")
    def export_run(run_id: str):
        node = run_tree(work(app), run_id)
        if node is None:
            raise HTTPException(status_code=404, detail="Run not found.")
        return JSONResponse(
            {"format_version": 1, "tree": tree_dict(node)},
            headers={"Content-Disposition": f'attachment; filename="decision-{node.run.id}.json"'},
        )

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

    @app.get("/api/evals/{eval_id}")
    def get_evaluation(eval_id: str):
        evaluation = work(app).repo.get_eval(eval_id)
        if evaluation is None:
            raise HTTPException(404, "Evaluation not found.")
        return asdict(evaluation)

    @app.get("/api/evals/{eval_id}/export")
    def export_evaluation(eval_id: str):
        evaluation = work(app).repo.get_eval(eval_id)
        if evaluation is None:
            raise HTTPException(404, "Evaluation not found.")
        return JSONResponse({"format_version": 1, "evaluation": asdict(evaluation)},
                            headers={"Content-Disposition": f'attachment; filename="evaluation-{evaluation.id}.json"'})

    @app.get("/api/evaluation-comparison.csv")
    def export_eval_comparison(left: str, right: str):
        state = work(app)
        first, second = state.repo.get_eval(left), state.repo.get_eval(right)
        if first is None or second is None:
            raise HTTPException(404, "Evaluation not found.")
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["baseline_evaluation", "candidate_evaluation", "case_id", "title", "change",
                         "baseline_passed", "candidate_passed", "baseline_score", "candidate_score",
                         "baseline_tokens", "candidate_tokens", "baseline_run", "candidate_run"])
        def cell(value):
            if value is None:
                return ""
            if isinstance(value, str) and (value.startswith(("\t", "\r", "\n")) or value.lstrip().startswith(("=", "+", "-", "@"))):
                return "'" + value
            return value
        for row in compare_evaluations(first, second)["rows"]:
            before, after = row["before"] or {}, row["after"] or {}
            writer.writerow([cell(value) for value in [first.id, second.id, row["case_id"], row["title"], row["state"],
                before.get("passed"), after.get("passed"), before.get("score"), after.get("score"),
                before.get("tokens"), after.get("tokens"), before.get("run_id"), after.get("run_id")]])
        return Response("\ufeff" + output.getvalue(), media_type="text/csv", headers={
            "Content-Disposition": 'attachment; filename="evaluation-comparison.csv"', "Cache-Control": "no-store"})

    @app.get("/evals/compare")
    def compare_evals(request: Request, left: str = "", right: str = ""):
        state = work(request.app)
        selected = []
        for eval_id in (left, right):
            item = state.repo.get_eval(eval_id) if eval_id else None
            if eval_id and item is None:
                raise HTTPException(404, "Evaluation not found.")
            selected.append(item)
        comparison = compare_evaluations(*selected) if all(selected) else None
        return render(request, "eval_compare.html", active="evals", evals=state.repo.list_evals(100),
                      left=left, right=right, selected=selected, comparison=comparison, bot_names=bot_name_map(state))

    @app.get("/")
    def dashboard(request: Request, from_run: str = ""):
        state = work(request.app)
        draft = run_tree(state, from_run) if from_run else None
        if from_run and draft is None:
            raise HTTPException(404, "Run not found.")
        rows = provider_rows(state)
        available = {row["name"] for row in rows if row.get("kind") in {"demo", "gemini", "openai", "custom"}}
        return render(
            request,
            "dashboard.html",
            active="home",
            runs=state.repo.list_root_runs(100),
            evals=state.repo.list_evals(5),
            bot_names=bot_name_map(state),
            draft=draft,
            draft_provider=draft.run.provider if draft and draft.run.provider in available else "demo",
        )

    @app.get("/bots")
    def bots_page(request: Request):
        state = work(request.app)
        rows = []
        for bot in state.repo.list_bots():
            version = state.repo.latest_version(bot.id)
            rows.append({"bot": bot, "version": version})
        return render(request, "bots.html", active="bots", rows=rows)

    @app.get("/compare")
    def compare_runs(request: Request, left: str = "", right: str = ""):
        state = work(request.app)
        selected = []
        for run_id in (left, right):
            node = run_tree(state, run_id) if run_id else None
            if run_id and node is None:
                raise HTTPException(404, "Run not found.")
            selected.append(node)
        return render(request, "compare.html", active="compare", runs=state.repo.list_root_runs(100),
                      left=left, right=right, selected=selected, bot_names=bot_name_map(state))

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
    async def create_run_page(request: Request, background: BackgroundTasks):
        state = work(request.app)
        form = await request.form()
        try:
            run = open_run(
                state,
                bot_id=str(form.get("bot_id") or ""),
                text=str(form.get("input") or ""),
                provider=str(form.get("provider") or "") or None,
                model=str(form.get("model") or "") or None,
            )
        except ValueError as exc:
            return RedirectResponse(f"/bots/{form.get('bot_id')}?error={quote(str(exc))}", status_code=303)
        background.add_task(perform_run, state, run.id)
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    @app.post("/runs/{run_id}/follow-up")
    async def follow_up_page(request: Request, run_id: str, background: BackgroundTasks):
        state = work(request.app)
        form = await request.form()
        try:
            run = open_follow_up(state, run_id, str(form.get("task") or ""))
        except ValueError as exc:
            return RedirectResponse(f"/runs/{run_id}?error={quote(str(exc))}", status_code=303)
        background.add_task(perform_run, state, run.id)
        return RedirectResponse(f"/runs/{run.id}", status_code=303)

    @app.get("/documents")
    def documents_page(request: Request):
        return render(request, "documents.html", active="documents", error=None, result=None)

    @app.post("/documents")
    async def documents_extract(request: Request):
        form = await request.form()
        upload = form.get("file")
        try:
            if upload is None or not getattr(upload, "filename", ""):
                raise ValueError("Choose a file.")
            data = await upload.read()
            result = extract_document(data, upload.filename, crop=crop_box(form), page=page_number(form))
        except ValueError as exc:
            return render(request, "documents.html", status_code=400, active="documents", error=str(exc), result=None)
        return render(request, "documents.html", active="documents", error=None, result=result)

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        state = work(request.app)
        node = run_tree(state, run_id)
        if node is None:
            return RedirectResponse("/", status_code=303)
        return render(request, "run_detail.html", active="home", node=node, error=request.query_params.get("error"))

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

    @app.get("/evals/history")
    def evaluation_history(request: Request, q: str = Query("", max_length=120),
                           outcome: str = Query("all", pattern="^(all|passed|failed)$"),
                           page: int = Query(1, ge=1, le=100000)):
        state = work(request.app)
        rows, total = state.repo.search_evals(q=q, outcome=outcome, offset=(page - 1) * 20)
        return render(request, "eval_history.html", active="evals", evals=rows, total=total,
                      has_next=page*20 < total, q=q, outcome=outcome, page=page, bot_names=bot_name_map(state))

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

    @app.get("/settings")
    def settings_page(request: Request):
        return render(request, "settings.html", active="settings", error=request.query_params.get("error"), notice=request.query_params.get("notice"))

    @app.get("/api/providers")
    def api_providers() -> list[dict]:
        state = work(app)
        return [public_provider(state, config) for config in state.repo.list_provider_configs()]

    @app.post("/api/providers")
    def api_save_provider(body: dict) -> dict:
        state = work(app)
        try:
            config = save_provider(state, **provider_kwargs(body))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return public_provider(state, config)

    @app.post("/settings/providers")
    async def save_provider_page(request: Request):
        state = work(request.app)
        form = await request.form()
        try:
            save_provider(state, **provider_kwargs(form_provider_body(form)))
        except ValueError as exc:
            return RedirectResponse(f"/settings?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/settings?notice=Provider+saved.", status_code=303)

    @app.post("/settings/providers/{name}/delete")
    def delete_provider_page(name: str, request: Request):
        try:
            remove_provider(work(request.app), name)
        except ValueError as exc:
            return RedirectResponse(f"/settings?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/settings?notice=Provider+removed.", status_code=303)


def work(app: FastAPI) -> AppState:
    return app.state.work


def provider_rows(state: AppState) -> list[dict]:
    rows = [
        {
            "name": "demo",
            "configured": True,
            "detail": "Deterministic stand-in. Runs without an API key.",
            "kind": "demo",
            "builtin": True,
            "enabled": True,
            "key_hint": "",
            "base_url": "",
            "default_model": "demo",
        }
    ]
    seen = {"demo"}
    for config in state.repo.list_provider_configs():
        seen.add(config.name)
        rows.append(public_provider(state, config))
    for name, provider in state.providers.items():
        if name in seen:
            continue
        rows.append(
            {
                "name": name,
                "configured": provider.configured,
                "detail": provider.detail,
                "kind": "custom",
                "builtin": False,
                "enabled": True,
                "key_hint": "",
                "base_url": "",
                "default_model": getattr(provider, "default_model", "") or "",
            }
        )
    return rows


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


def provider_kwargs(body: dict) -> dict:
    return {
        "name": str(body.get("name") or ""),
        "kind": str(body.get("kind") or "openai"),
        "base_url": str(body.get("base_url") or ""),
        "api_key": str(body.get("api_key") or ""),
        "default_model": str(body.get("default_model") or ""),
        "enabled": bool(body.get("enabled")),
    }


def form_provider_body(form) -> dict:
    return {
        "name": str(form.get("name") or ""),
        "kind": str(form.get("kind") or "openai"),
        "base_url": str(form.get("base_url") or ""),
        "api_key": str(form.get("api_key") or ""),
        "default_model": str(form.get("default_model") or ""),
        "enabled": form.get("enabled") == "on",
    }


def page_number(form) -> int:
    raw = form.get("page")
    if raw in (None, ""):
        return 1
    try:
        page = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Page must be a whole number.") from exc
    if page < 1:
        raise ValueError("Page must be 1 or greater.")
    return page


def crop_box(form) -> tuple[int, int, int, int] | None:
    raw = [form.get(name) for name in ("crop_left", "crop_top", "crop_right", "crop_bottom")]
    if all(value in (None, "") for value in raw):
        return None
    try:
        left, top, right, bottom = (int(value) for value in raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("Crop values must be whole numbers.") from exc
    return left, top, right, bottom


def parse_int(value, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an integer.") from exc


def render(request: Request, name: str, status_code: int = 200, **extra):
    state = work(request.app)
    rows = provider_rows(state)
    context = {
        "active": "",
        "provider_rows": rows,
        "bots": ordered_bots(state),
        "bot_names": bot_name_map(state),
        "packs": state.packs,
        "chat_provider_rows": [
            row for row in rows if row.get("kind") in {"demo", "gemini", "openai", "custom"}
        ],
        **extra,
    }
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
    )


def ordered_bots(state: AppState):
    ranked = []
    for bot in state.repo.list_bots():
        version = state.repo.latest_version(bot.id)
        lead = bool(version and (version.require_delegation or version.pack_id))
        ranked.append((0 if lead else 1, bot.name.lower(), bot))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in ranked]


def tree_dict(node) -> dict | None:
    if node is None:
        return None
    return {
        "run": asdict(node.run),
        "decision": decision_of(node.run.output),
        "summary": summary_of(node.run.output),
        "bot_name": node.bot_name,
        "steps": [asdict(step) for step in node.steps],
        "children": [tree_dict(child) for child in node.children],
    }
