from __future__ import annotations

import time

import httpx
import jwt


class AppCredentials:
    def __init__(self, app_id: str, private_key: str) -> None:
        self.app_id = app_id
        self.private_key = private_key

    def assertion(self, *, issued_at: int | None = None) -> str:
        issued = int(time.time()) if issued_at is None else issued_at
        payload = {"iat": issued - 60, "exp": issued + 9 * 60, "iss": self.app_id}
        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def token_for(self, installation_id: int, client: httpx.Client) -> str:
        response = client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {self.assertion()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "shipgate",
            },
        )
        response.raise_for_status()
        token = response.json().get("token")
        if not token:
            raise RuntimeError("GitHub did not return an installation token.")
        return str(token)
