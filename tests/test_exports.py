from fastapi.testclient import TestClient

def test_export_includes_specialists_and_preserves_run(app):
    client = TestClient(app)
    result = client.post("/api/runs", json={"bot_id": "change-lead", "provider": "demo", "input": "Update README documentation."}).json()
    run_id = result["run"]["id"]
    response = client.get(f"/api/runs/{run_id}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    report = response.json()
    assert report["format_version"] == 1
    assert report["tree"]["run"]["id"] == run_id
    assert len(report["tree"]["children"]) == 3
    assert client.get(f"/api/runs/{run_id}").json()["run"] == report["tree"]["run"]


def test_unknown_export_returns_404(app):
    assert TestClient(app).get("/api/runs/unknown/export").status_code == 404
