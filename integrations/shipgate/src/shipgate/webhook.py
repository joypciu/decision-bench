from __future__ import annotations

import hashlib
import hmac
import json
from typing import Callable

import httpx

from shipgate.comment import format_comment, review_event, status_for
from shipgate.diffmap import inline_comments
from shipgate.github import GitHub
from shipgate.review import Review
from shipgate.store import DeliveryStore


REVIEW_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


class WebhookError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def verify_signature(body: bytes, secret: str, header: str | None) -> bool:
    if not secret or not header:
        return False
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={digest}", header)


def handle_webhook(
    body: bytes,
    *,
    event: str,
    signature: str | None,
    secret: str,
    github: GitHub | None = None,
    open_github: Callable[[int], GitHub] | None = None,
    store: DeliveryStore,
    review_diff: Callable[[str], Review],
) -> dict:
    if not verify_signature(body, secret, signature):
        raise WebhookError(401, "Invalid webhook signature.")
    if event != "pull_request":
        return {"status": "ignored", "event": event}
    payload = json.loads(body)
    action = str(payload.get("action") or "")
    if action not in REVIEW_ACTIONS:
        return {"status": "ignored", "action": action}
    pull = payload["pull_request"]
    repo = payload["repository"]["full_name"]
    sha = pull["head"]["sha"]
    number = int(pull["number"])
    if pull.get("draft"):
        return {"status": "ignored", "action": "draft"}
    if store.seen(repo, sha):
        return {"status": "duplicate", "sha": sha}
    if github is None:
        installation = payload.get("installation") or {}
        if open_github is None or "id" not in installation:
            raise WebhookError(400, "Webhook payload has no GitHub App installation.")
        github = open_github(int(installation["id"]))
    github.post_status(repo, sha, "pending", "Reviewing the diff.")
    diff = github.fetch_diff(repo, number)
    try:
        review = review_diff(diff)
    except Exception as exc:
        github.post_status(repo, sha, "error", f"Review failed: {exc}"[:140])
        raise WebhookError(500, "The review did not finish.") from exc
    body = format_comment(review)
    comments = inline_comments(review, diff)
    comment_id = _submit(github, repo, number, sha, body, review_event(review.verdict), comments)
    state, description = status_for(review)
    github.post_status(repo, sha, state, description)
    store.record(repo, sha, review.run_id, comment_id)
    return {"status": "reviewed", "verdict": review.verdict, "sha": sha, "comment_id": comment_id, "commit_status": state}


def _submit(github: GitHub, repo: str, number: int, sha: str, body: str, event: str, comments: list) -> int:
    try:
        return github.submit_review(repo, number, sha, body, event, comments)
    except httpx.HTTPStatusError:
        if not comments:
            raise
        return github.submit_review(repo, number, sha, body, event, None)
