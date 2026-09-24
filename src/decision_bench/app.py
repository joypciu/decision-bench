from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from decision_bench.config import Settings
from decision_bench.packs import load_packs
from decision_bench.providers.registry import build_providers
from decision_bench.seed import seed_templates
from decision_bench.services import AppState
from decision_bench.storage.sqlite import SqliteRunStore
from decision_bench.web.routes import register_routes


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    database_path = settings.resolved_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    repo = SqliteRunStore(database_path)
    repo.migrate()
    packs = load_packs(settings.root / "packs")
    seed_templates(repo, packs)
    state = AppState(
        settings=settings,
        repo=repo,
        packs=packs,
        providers=build_providers(settings),
    )
    app = FastAPI(
        title="Decision Bench",
        version="0.1.0",
        summary="Structured decisions, bounded sub-agents, and provider evals.",
    )
    app.state.work = state
    templates = Jinja2Templates(directory=str(settings.root / "web" / "templates"))
    templates.env.filters["pretty"] = lambda value: json.dumps(value, indent=2, sort_keys=True)
    app.state.templates = templates
    static_dir = settings.root / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    register_routes(app)
    return app


def templates_dir(root: Path) -> Path:
    return root / "web" / "templates"
