from fastapi.testclient import TestClient


def test_draft_prefills_original_case_without_modifying_it(app):
    with TestClient(app) as client:
        original = client.post("/api/runs", json={"bot_id": "incident-lead", "provider": "demo", "input": "Production outage. </textarea><script>literal</script>"}).json()["run"]
        response = client.get("/", params={"from_run": original["id"]})
        assert response.status_code == 200
        assert "Editing a copy" in response.text
        assert "&lt;/textarea&gt;&lt;script&gt;literal&lt;/script&gt;" in response.text
        assert '<option value="incident-lead" selected>' in response.text
        assert client.get(f"/api/runs/{original['id']}").json()["run"] == original
        assert client.get("/?from_run=missing").status_code == 404
