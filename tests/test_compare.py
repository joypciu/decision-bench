from fastapi.testclient import TestClient


def test_comparison_shows_decisions_and_case_warning(app):
    with TestClient(app) as client:
        ids = [client.post("/api/runs", json={"bot_id": "change-lead", "provider": "demo", "input": text}).json()["run"]["id"]
               for text in ("Update README documentation.", "Bypass auth for all users.")]
        response = client.get("/compare", params={"left": ids[0], "right": ids[1]})
        assert response.status_code == 200
        assert "These runs use different case inputs" in response.text
        assert 'class="comparison-changed"' in response.text
        assert "Specialist results" in response.text
        assert all(f"/runs/{sid}" in response.text for sid in ids)
        assert "You selected the same run twice" in client.get("/compare", params={"left": ids[0], "right": ids[0]}).text


def test_comparison_handles_empty_and_missing_runs(app):
    with TestClient(app) as client:
        assert "Choose two saved runs" in client.get("/compare").text
        assert client.get("/compare?left=missing").status_code == 404
