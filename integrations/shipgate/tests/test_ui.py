from pathlib import Path

from fastapi.testclient import TestClient

from shipgate.app import INCIDENT_REPORT, MIGRATION_DIFF, README_DIFF, SAMPLE_DIFF, create_app


def test_home_page_reviews_a_pasted_diff(monkeypatch, tmp_path):
    monkeypatch.setenv("DECISION_BENCH_ROOT", str(Path(__file__).resolve().parents[3]))
    monkeypatch.setenv("SHIPGATE_DATA", str(tmp_path))
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    app = create_app()
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Paste a diff" in home.text
    assert "GitHub app not set" in home.text
    result = client.post("/reviews", data={"diff": SAMPLE_DIFF})
    assert result.status_code == 200
    assert "block" in result.text
    assert "auth.py" in result.text
    assert "Comment on line" in result.text
    assert "Pull request review" in result.text
    assert "**block**" in result.text
    assert "Request changes" in result.text or "REQUEST_CHANGES" in result.text
    assert "1 file" in result.text
    assert "Recent reviews" in result.text
    migration = client.post("/reviews", data={"diff": MIGRATION_DIFF})
    assert "revise" in migration.text
    readme = client.post("/reviews", data={"diff": README_DIFF})
    assert "ship" in readme.text
    incident = client.post("/reviews", data={"diff": INCIDENT_REPORT, "bot_id": "incident-lead"})
    assert incident.status_code == 200
    assert "sev1" in incident.text
    rejected = client.post("/reviews", data={"diff": SAMPLE_DIFF, "bot_id": "nope"})
    assert rejected.status_code == 400
    empty = client.post("/reviews", data={"diff": "   "})
    assert empty.status_code == 400
