from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import httpx

from shipgate.app_auth import AppCredentials
from shipgate.comment import format_comment, review_event, status_for
from shipgate.diffmap import diff_stats, inline_comments
from shipgate.github import GitHub
from shipgate.review import Reviewer, Review, Risk
from shipgate.paths import bench_root as find_bench_root
from shipgate.store import DeliveryStore
from shipgate.webhook import WebhookError, handle_webhook


def load_private_key() -> str:
    raw = os.environ.get("GITHUB_APP_PRIVATE_KEY", "")
    if not raw:
        path = os.environ.get("GITHUB_APP_PRIVATE_KEY_PATH", "")
        if path:
            raw = Path(path).read_text(encoding="utf-8")
    return raw.replace("\\n", "\n")


def create_app() -> FastAPI:
    bench_root = find_bench_root()
    data = Path(os.environ.get("SHIPGATE_DATA", Path(__file__).resolve().parents[2] / "data"))
    reviewer = Reviewer.open(
        bench_root=bench_root,
        database=data / "decision_bench.sqlite",
        provider=os.environ.get("SHIPGATE_PROVIDER", "demo"),
    )
    store = DeliveryStore(data / "shipgate.sqlite")
    secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    private_key = load_private_key()
    app_id = os.environ.get("GITHUB_APP_ID", "")
    app = FastAPI(title="Shipgate", version="0.1.0")
    app.state.reviewer = reviewer
    app.state.store = store
    app.state.secret = secret
    app.state.token = token
    app.state.credentials = AppCredentials(app_id, private_key) if app_id and private_key else None
    root = Path(__file__).resolve().parents[2]
    templates = Jinja2Templates(directory=str(root / "web" / "templates"))
    app.mount("/static", StaticFiles(directory=str(root / "web" / "static")), name="static")

    def page_context(request: Request, **extra):
        return {
            "request": request,
            "provider": os.environ.get("SHIPGATE_PROVIDER", "demo"),
            "github_app": app.state.credentials is not None,
            "sample": SAMPLE_DIFF,
            "samples": SAMPLES,
            "jobs": JOBS,
            **extra,
        }

    @app.get("/")
    def home(request: Request):
        return templates.TemplateResponse(
            request,
            "home.html",
            page_context(request, review=None, diff="", error=None, bot_id="change-lead", recent=app.state.store.recent_local()),
        )

    @app.get("/history")
    def review_history(request: Request, q: str = Query("", max_length=120),
                       verdict: str = Query("all", pattern="^(all|ship|revise|block|unknown)$"),
                       page: int = Query(1, ge=1, le=100000)):
        history = app.state.store.history_local(q=q, verdict=verdict, page=page)
        return templates.TemplateResponse(request, "history.html", page_context(
            request, history=history, q=q, verdict=verdict, page=page))

    @app.post("/reviews")
    async def review_page(request: Request):
        form = await request.form()
        diff = str(form.get("diff") or "")
        bot_id = str(form.get("bot_id") or "change-lead")
        if bot_id not in JOBS:
            return templates.TemplateResponse(
                request,
                "home.html",
                page_context(request, review=None, diff=diff, error="Choose a change-risk or incident review.", bot_id="change-lead", recent=app.state.store.recent_local()),
                status_code=400,
            )
        if not diff.strip():
            return templates.TemplateResponse(
                request,
                "home.html",
                page_context(request, review=None, diff="", error="Paste a diff first.", bot_id=bot_id, recent=app.state.store.recent_local()),
                status_code=400,
            )
        try:
            review = app.state.reviewer.review(diff, bot_id)
        except ValueError as exc:
            return templates.TemplateResponse(
                request,
                "home.html",
                page_context(request, review=None, diff=diff, error=str(exc), bot_id=bot_id, recent=app.state.store.recent_local()),
                status_code=400,
            )
        review_id = app.state.store.add_local(bot_id, review.verdict, review.summary,
                                             payload={"review": asdict(review), "diff": diff, "bot_id": bot_id})
        github_event = review_event(review.verdict) if review.verdict in {"ship", "revise", "block"} else ""
        commit_state = status_for(review)[0] if github_event else ""
        return templates.TemplateResponse(
            request,
            "home.html",
            page_context(
                request,
                review=review,
                review_id=review_id,
                diff=diff,
                error=None,
                comment=format_comment(review),
                lines=inline_comments(review, diff),
                bot_id=bot_id,
                stats=diff_stats(diff),
                github_event=github_event,
                commit_state=commit_state,
                recent=app.state.store.recent_local(),
            ),
        )

    def comparison_payload(left: int | None, right: int | None):
        snapshots = []
        for identity in (left, right):
            saved = app.state.store.get_local(identity) if identity else None
            if identity and saved is None:
                raise HTTPException(404, "Complete saved review not found. Summary-only reviews cannot be compared.")
            snapshots.append(saved)
        return snapshots

    @app.get("/compare")
    def compare_reviews(request: Request, left: int | None = Query(None, ge=1), right: int | None = Query(None, ge=1)):
        snapshots = comparison_payload(left, right)
        choices = [row for row in app.state.store.recent_local(100) if row["available"]]
        for identity, saved in zip((left, right), snapshots):
            if saved and not any(row["id"] == identity for row in choices):
                choices.append({"id": identity, "verdict": saved["review"]["verdict"], "summary": saved["review"]["summary"]})
        return templates.TemplateResponse(request, "compare.html", page_context(request,
            left=left, right=right, snapshots=snapshots, choices=choices,
            counts=[diff_stats(saved["diff"]) if saved else None for saved in snapshots]))

    @app.get("/compare/export")
    def export_review_comparison(left: int = Query(..., ge=1), right: int = Query(..., ge=1)):
        first, second = comparison_payload(left, right)
        return JSONResponse({"format_version": 1, "baseline": {"id": left, **first}, "candidate": {"id": right, **second}},
                            headers={"Content-Disposition": 'attachment; filename="shipgate-comparison.json"'})

    @app.get("/reviews/{review_id}")
    def saved_review(request: Request, review_id: int):
        saved = app.state.store.get_local(review_id)
        if saved is None:
            raise HTTPException(404, "Saved review not found. Older summary-only reviews cannot be reopened.")
        values = dict(saved["review"])
        values["risks"] = [Risk(**risk) for risk in values["risks"]]
        review = Review(**values)
        diff = saved["diff"]
        event = review_event(review.verdict) if review.verdict in {"ship", "revise", "block"} else ""
        return templates.TemplateResponse(request, "home.html", page_context(
            request, review=review, review_id=review_id, diff=diff, error=None,
            bot_id=saved["bot_id"], comment=format_comment(review), lines=inline_comments(review, diff),
            stats=diff_stats(diff), github_event=event, commit_state=status_for(review)[0] if event else "",
            recent=app.state.store.recent_local()))

    @app.get("/reviews/{review_id}/export")
    def export_review(review_id: int):
        saved = app.state.store.get_local(review_id)
        if saved is None:
            raise HTTPException(404, "Saved review not found.")
        return JSONResponse({"format_version": 1, **saved},
                            headers={"Content-Disposition": f'attachment; filename="shipgate-review-{review_id}.json"'})

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/github/webhook")
    async def webhook(request: Request) -> dict:
        body = await request.body()
        try:
            credentials = app.state.credentials
            override = getattr(app.state, "github_override", None)

            def open_github(installation_id: int) -> GitHub:
                with httpx.Client(timeout=30) as client:
                    token = credentials.token_for(installation_id, client)
                return GitHub(token)

            if override is not None:
                github = override
                opener = None
            elif credentials is not None:
                github = None
                opener = open_github
            else:
                github = GitHub(app.state.token)
                opener = None
            return handle_webhook(
                body,
                event=request.headers.get("X-GitHub-Event", ""),
                signature=request.headers.get("X-Hub-Signature-256"),
                secret=app.state.secret,
                github=github,
                open_github=opener,
                store=app.state.store,
                review_diff=app.state.reviewer.review,
            )
        except WebhookError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    return app


SAMPLE_DIFF = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
         return True
     return False
"""

MIGRATION_DIFF = """diff --git a/migrations/2026_drop_users.py b/migrations/2026_drop_users.py
--- /dev/null
+++ b/migrations/2026_drop_users.py
@@ -0,0 +1,2 @@
+def upgrade():
+    execute("DROP TABLE users")
"""

README_DIFF = """diff --git a/README.md b/README.md
--- a/README.md
+++ b/README.md
@@ -1 +1 @@
-Hello
+Hello there
"""

INCIDENT_REPORT = """Production API outage. All users are affected and there is data loss in orders since 02:10 UTC.
"""

SAMPLES = [
    {"id": "block", "label": "Auth bypass", "diff": SAMPLE_DIFF, "bot_id": "change-lead"},
    {"id": "revise", "label": "Drop table", "diff": MIGRATION_DIFF, "bot_id": "change-lead"},
    {"id": "ship", "label": "Readme wording", "diff": README_DIFF, "bot_id": "change-lead"},
    {"id": "sev1", "label": "Outage report", "diff": INCIDENT_REPORT, "bot_id": "incident-lead"},
]

JOBS = {
    "change-lead": "Change-risk",
    "incident-lead": "Incident triage",
}
