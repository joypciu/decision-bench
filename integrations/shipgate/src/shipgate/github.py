from __future__ import annotations

import httpx


class GitHub:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self.token = token
        self._client = client

    def fetch_diff(self, repo: str, number: int) -> str:
        response = self._send("GET", f"https://api.github.com/repos/{repo}/pulls/{number}", accept="application/vnd.github.diff")
        response.raise_for_status()
        return response.text

    def post_comment(self, repo: str, number: int, body: str) -> int:
        response = self._send(
            "POST",
            f"https://api.github.com/repos/{repo}/issues/{number}/comments",
            json={"body": body},
        )
        response.raise_for_status()
        return int(response.json()["id"])

    def submit_review(
        self,
        repo: str,
        number: int,
        sha: str,
        body: str,
        event: str,
        comments: list | None = None,
    ) -> int:
        payload = {"commit_id": sha, "body": body, "event": event}
        if comments:
            payload["comments"] = comments
        response = self._send(
            "POST",
            f"https://api.github.com/repos/{repo}/pulls/{number}/reviews",
            json=payload,
        )
        response.raise_for_status()
        return int(response.json()["id"])

    def post_status(self, repo: str, sha: str, state: str, description: str) -> None:
        response = self._send(
            "POST",
            f"https://api.github.com/repos/{repo}/statuses/{sha}",
            json={"state": state, "context": "shipgate", "description": description[:140]},
        )
        response.raise_for_status()

    def _send(self, method: str, url: str, **kwargs) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": kwargs.pop("accept", "application/vnd.github+json"),
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "shipgate",
        }
        if self._client is not None:
            return self._client.request(method, url, headers=headers, **kwargs)
        with httpx.Client(timeout=30) as client:
            return client.request(method, url, headers=headers, **kwargs)
