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


def test_history_search_pagination_and_legacy_records(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("SHIPGATE_DATA", str(tmp_path))
    monkeypatch.setenv("SHIPGATE_PROVIDER", "demo")
    app = create_app()
    store = app.state.store
    store.add_local("change-lead", "block", "Rare 100%_match <script>literal</script>")
    for i in range(21):
        store.add_local("change-lead", "ship", f"Documentation {i}")
    assert store.history_local()["has_next"]
    assert len(store.history_local()["rows"]) == 20
    assert len(store.history_local(page=2)["rows"]) == 2
    for query in ("Rare", "%", "_", "100%_match"):
        assert store.history_local(q=query)["total"] == 1
    assert store.history_local(q="Rare", verdict="ship")["total"] == 0
    assert store.history_local(q="' OR 1=1 --")["total"] == 0
    with TestClient(app) as client:
        assert "Older reviews" in client.get("/history").text
        response = client.get("/history", params={"q": "Rare", "verdict": "block"})
        assert "1 matching review" in response.text
        assert "Summary only" in response.text
        assert "&lt;script&gt;literal&lt;/script&gt;" in response.text
        assert "No reviews match" in client.get("/history?q=missing").text
        assert client.get("/history?page=0").status_code == 422
        assert client.get("/history?verdict=invalid").status_code == 422
        assert client.get("/history", params={"q": "x"*121}).status_code == 422
