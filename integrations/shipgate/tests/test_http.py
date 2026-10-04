import hashlib
import hmac
import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from shipgate.app import create_app
from shipgate.github import GitHub


DIFF = """diff --git a/auth.py b/auth.py
--- a/auth.py
+++ b/auth.py
@@ -4,7 +4,7 @@ def allow(user):
-    if user.is_authenticated:
+    if True:  # bypass auth
         return True
     return False
"""
SECRET = "route-secret"


class RouteGitHub(GitHub):
    def __init__(self) -> None:
        super().__init__("token")
        self.comments = []
        self.statuses = []

    def fetch_diff(self, repo: str, number: int) -> str:
        return DIFF

    def submit_review(self, repo: str, number: int, sha: str, body: str, event: str, comments: list | None = None) -> int:
        self.comments.append(body)
        self.inline = comments or []
        assert event == "REQUEST_CHANGES"
        return 11

    def post_status(self, repo: str, sha: str, state: str, description: str) -> None:
        self.statuses.append((state, description))


def signed(body: bytes) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_webhook_route_blocks_the_auth_bypass(monkeypatch, tmp_path):
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("SHIPGATE_DATA", str(tmp_path))
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY", raising=False)
    app = create_app()
    github = RouteGitHub()
    app.state.github_override = github
    client = TestClient(app)
    body = json.dumps(
        {
            "action": "opened",
            "repository": {"full_name": "joypciu/example"},
            "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        }
    ).encode()
    rejected = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=nope"},
    )
    assert rejected.status_code == 401
    accepted = client.post(
        "/github/webhook",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": signed(body)},
    )
    assert accepted.status_code == 200
    assert accepted.json()["verdict"] == "block"
    assert "**block**" in github.comments[0]
    assert "`auth.py`" in github.comments[0]
    assert github.inline[0]["path"] == "auth.py"
    assert github.inline[0]["line"] > 0
    assert github.statuses[-1] == ("failure", "block: auth.py")
    os.environ.pop("GITHUB_WEBHOOK_SECRET", None)
