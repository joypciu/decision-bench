import hashlib
import hmac
import json
from pathlib import Path

import httpx

from shipgate.github import GitHub
from shipgate.review import Review, Risk
from shipgate.store import DeliveryStore
from shipgate.webhook import WebhookError, handle_webhook


SECRET = "test-secret"
DIFF = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
"""


def signed(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


class FakeGitHub(GitHub):
    def __init__(self) -> None:
        super().__init__("token", client=None)
        self.comments = []
        self.statuses = []
        self.reviews = []

    def fetch_diff(self, repo: str, number: int) -> str:
        assert (repo, number) == ("joypciu/example", 7)
        return DIFF

    def post_comment(self, repo: str, number: int, body: str) -> int:
        self.comments.append((repo, number, body))
        return 42

    def submit_review(self, repo: str, number: int, sha: str, body: str, event: str, comments: list | None = None) -> int:
        self.reviews.append((repo, number, sha, body, event, comments or []))
        return 42

    def post_status(self, repo: str, sha: str, state: str, description: str) -> None:
        self.statuses.append((repo, sha, state, description))


def review_diff(diff: str) -> Review:
    assert "bypass auth" in diff
    return Review("run-9", "block", "Auth bypass.", [Risk("high", "auth.py", "Authentication was weakened.")], "succeeded")


def test_webhook_reviews_a_pull_request_once(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    github = FakeGitHub()
    body = json.dumps(payload()).encode()
    first = handle_webhook(
        body,
        event="pull_request",
        signature=signed(body),
        secret=SECRET,
        github=github,
        store=store,
        review_diff=review_diff,
    )
    second = handle_webhook(
        body,
        event="pull_request",
        signature=signed(body),
        secret=SECRET,
        github=github,
        store=store,
        review_diff=review_diff,
    )
    assert first["verdict"] == "block"
    assert first["comment_id"] == 42
    assert first["commit_status"] == "failure"
    assert github.statuses == [
        ("joypciu/example", "abc123", "pending", "Reviewing the diff."),
        ("joypciu/example", "abc123", "failure", "block: auth.py"),
    ]
    assert second["status"] == "duplicate"
    assert len(github.reviews) == 1
    assert github.reviews[0][4] == "REQUEST_CHANGES"
    assert "**block**" in github.reviews[0][3]
    assert github.reviews[0][5] == [
        {"path": "auth.py", "line": 4, "side": "RIGHT", "body": "**high** Authentication was weakened."}
    ]


def test_rejected_line_comments_are_sent_again_without_them(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    github = RejectingLines()
    body = json.dumps(payload()).encode()
    result = handle_webhook(
        body,
        event="pull_request",
        signature=signed(body),
        secret=SECRET,
        github=github,
        store=store,
        review_diff=review_diff,
    )
    assert result["verdict"] == "block"
    assert github.reviews[0][5]
    assert github.reviews[1][5] == []
    assert store.seen("joypciu/example", "abc123") is True


class RejectingLines(FakeGitHub):
    def submit_review(self, repo: str, number: int, sha: str, body: str, event: str, comments: list | None = None) -> int:
        self.reviews.append((repo, number, sha, body, event, comments or []))
        if comments:
            request = httpx.Request("POST", "https://api.github.com/reviews")
            response = httpx.Response(422, request=request)
            raise httpx.HTTPStatusError("unprocessable", request=request, response=response)
        return 43


def test_a_failed_review_marks_the_commit_as_error(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    github = FakeGitHub()
    body = json.dumps(payload()).encode()

    def explode(_diff: str) -> Review:
        raise RuntimeError("provider down")

    try:
        handle_webhook(
            body,
            event="pull_request",
            signature=signed(body),
            secret=SECRET,
            github=github,
            store=store,
            review_diff=explode,
        )
    except WebhookError as exc:
        assert exc.status == 500
    else:
        raise AssertionError("expected the review failure")
    assert github.statuses[-1] == ("joypciu/example", "abc123", "error", "Review failed: provider down")
    assert github.comments == []
    assert github.reviews == []
    assert store.seen("joypciu/example", "abc123") is False


def test_a_draft_pull_request_is_not_reviewed(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    github = FakeGitHub()
    body = json.dumps({**payload(), "pull_request": {**payload()["pull_request"], "draft": True}}).encode()
    result = handle_webhook(
        body,
        event="pull_request",
        signature=signed(body),
        secret=SECRET,
        github=github,
        store=store,
        review_diff=review_diff,
    )
    assert result == {"status": "ignored", "action": "draft"}
    assert github.reviews == []
    assert github.statuses == []


def test_webhook_rejects_a_bad_signature(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    try:
        handle_webhook(
            b"{}",
            event="pull_request",
            signature="sha256=nope",
            secret=SECRET,
            github=FakeGitHub(),
            store=store,
            review_diff=review_diff,
        )
    except WebhookError as exc:
        assert exc.status == 401
    else:
        raise AssertionError("expected a signature error")


def test_github_fetches_the_diff_and_posts_a_comment():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers["accept"]))
        if request.method == "GET":
            return httpx.Response(200, text=DIFF)
        return httpx.Response(201, json={"id": 9})

    github = GitHub("token", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert "bypass auth" in github.fetch_diff("joypciu/example", 7)
    assert github.post_comment("joypciu/example", 7, "hello") == 9
    assert seen[0][2] == "application/vnd.github.diff"


def test_webhook_mints_an_installation_token(tmp_path: Path):
    store = DeliveryStore(tmp_path / "shipgate.sqlite")
    opened = []

    def open_github(installation_id: int) -> GitHub:
        opened.append(installation_id)
        return FakeGitHub()

    body = json.dumps(payload()).encode()
    result = handle_webhook(
        body,
        event="pull_request",
        signature=signed(body),
        secret=SECRET,
        open_github=open_github,
        store=store,
        review_diff=review_diff,
    )
    assert opened == [99]
    assert result["verdict"] == "block"


def payload() -> dict:
    return {
        "action": "opened",
        "installation": {"id": 99},
        "repository": {"full_name": "joypciu/example"},
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
    }
