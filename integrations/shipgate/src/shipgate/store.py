from __future__ import annotations

import sqlite3
import json
from pathlib import Path


class DeliveryStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS deliveries (
                    repo TEXT NOT NULL,
                    sha TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    comment_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (repo, sha)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS local_reviews (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            columns = {row[1] for row in connection.execute("PRAGMA table_info(local_reviews)")}
            if "payload" not in columns:
                connection.execute("ALTER TABLE local_reviews ADD COLUMN payload TEXT")

    def seen(self, repo: str, sha: str) -> bool:
        with self._conn() as connection:
            row = connection.execute(
                "SELECT 1 FROM deliveries WHERE repo = ? AND sha = ?",
                (repo, sha),
            ).fetchone()
        return row is not None

    def record(self, repo: str, sha: str, run_id: str, comment_id: int) -> None:
        with self._conn() as connection:
            connection.execute(
                "INSERT INTO deliveries (repo, sha, run_id, comment_id) VALUES (?, ?, ?, ?)",
                (repo, sha, run_id, comment_id),
            )

    def add_local(self, bot_id: str, verdict: str, summary: str, *, payload: dict | None = None) -> int:
        with self._conn() as connection:
            cursor = connection.execute(
                "INSERT INTO local_reviews (bot_id, verdict, summary, payload) VALUES (?, ?, ?, ?)",
                (bot_id, verdict, summary[:180], json.dumps(payload) if payload is not None else None),
            )
            return cursor.lastrowid

    def get_local(self, review_id: int) -> dict | None:
        with self._conn() as connection:
            row = connection.execute("SELECT payload FROM local_reviews WHERE id = ?", (review_id,)).fetchone()
        return json.loads(row["payload"]) if row and row["payload"] else None

    def recent_local(self, limit: int = 8) -> list[dict]:
        with self._conn() as connection:
            rows = connection.execute(
                "SELECT id, bot_id, verdict, summary, created_at, payload IS NOT NULL AS available FROM local_reviews ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def _conn(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection
