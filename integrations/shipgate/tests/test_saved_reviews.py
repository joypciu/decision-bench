from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient

from shipgate.app import create_app, SAMPLE_DIFF
from shipgate.store import DeliveryStore


def test_summary_database_migration_preserves_existing_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE local_reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT NOT NULL, verdict TEXT NOT NULL, summary TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        connection.execute("INSERT INTO local_reviews(bot_id, verdict, summary) VALUES ('change-lead','ship','Legacy summary')")
    store = DeliveryStore(path)
    old = store.recent_local()[0]
    assert old["summary"] == "Legacy summary"
    assert old["available"] == 0
    assert store.get_local(old["id"]) is None
    new_id = store.add_local("change-lead", "block", "New review", payload={"diff": "synthetic"})
    assert DeliveryStore(path).get_local(new_id) == {"diff": "synthetic"}
    assert len(store.recent_local()) == 2


def test_saved_review_reopens_and_exports_without_inference(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("SHIPGATE_DATA", str(tmp_path))
    monkeypatch.setenv("SHIPGATE_PROVIDER", "demo")
    with TestClient(create_app()) as client:
        assert client.post("/reviews", data={"diff": SAMPLE_DIFF}).status_code == 200
        exported = client.get("/reviews/1/export")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]
        assert exported.json()["review"]["verdict"] == "block"
        assert exported.json()["diff"] == SAMPLE_DIFF
    restarted = create_app()
    monkeypatch.setattr(restarted.state.reviewer, "review", lambda *args: (_ for _ in ()).throw(AssertionError("Reopening must not run inference")))
    with TestClient(restarted) as client:
        response = client.get("/reviews/1")
        assert response.status_code == 200
        assert "auth.py" in response.text
        assert "Download review" in response.text
        assert client.get("/reviews/1/export").json() == exported.json()
        assert client.get("/reviews/999").status_code == 404
        assert client.get("/reviews/999/export").status_code == 404
