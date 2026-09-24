from fastapi.testclient import TestClient


def test_dashboard_and_eval_flow(app):
    client = TestClient(app)
    home = client.get("/")
    assert home.status_code == 200
    assert "Change-risk lead" in home.text
    assert "Incident lead" in home.text

    created = client.post(
        "/api/bots",
        json={
            "name": "Echo",
            "summary": "Returns a fixed answer.",
            "instructions": "Call finish.",
            "provider": "demo",
            "model": "demo",
            "output_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["answer"],
                "properties": {"answer": {"type": "string"}},
            },
            "allowed_tools": ["finish"],
            "max_steps": 2,
            "max_child_depth": 0,
            "max_tokens": 2000,
        },
    )
    assert created.status_code == 200
    bot_id = created.json()["bot"]["id"]

    run = client.post("/api/runs", json={"bot_id": bot_id, "input": "hello", "provider": "demo"})
    assert run.status_code == 200
    body = run.json()
    assert body["run"]["status"] == "succeeded"
    assert body["run"]["output"]["answer"] == "demo"

    page = client.post(
        "/evals",
        data={"bot_id": "change-lead", "pack_id": "change_risk", "provider": "demo", "model": "demo"},
        follow_redirects=True,
    )
    assert page.status_code == 200
    assert "2/2 passed" in page.text

    health = client.get("/api/health")
    assert health.status_code == 200
    assert {item["name"] for item in health.json()["providers"]} == {"demo", "gemini", "openrouter"}
