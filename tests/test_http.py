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
    assert body["tree"]["decision"] == "demo"

    page = client.post(
        "/evals",
        data={"bot_id": "change-lead", "pack_id": "change_risk", "provider": "demo", "model": "demo"},
        follow_redirects=True,
    )
    assert page.status_code == 200
    assert "2/2 passed" in page.text

    lead = client.post(
        "/api/runs",
        json={
            "bot_id": "change-lead",
            "provider": "demo",
            "input": (
                "diff --git a/auth.py b/auth.py\n"
                "--- a/auth.py\n"
                "+++ b/auth.py\n"
                "-    if user.is_authenticated:\n"
                "+    if True:  # bypass auth\n"
            ),
        },
    )
    assert lead.status_code == 200
    lead_body = lead.json()
    assert lead_body["tree"]["decision"] == "block"
    decisions = {child["decision"] for child in lead_body["tree"]["children"]}
    assert "high" in decisions and "none" in decisions
    home_after = client.get("/")
    assert "block" in home_after.text
    detail = client.get(f"/runs/{lead_body['run']['id']}")
    assert "Security checker" in detail.text
    assert "Migration checker" in detail.text
    evals = client.get("/evals")
    assert 'value="change-lead" selected' in evals.text

    health = client.get("/api/health")
    assert health.status_code == 200
    assert {item["name"] for item in health.json()["providers"]} >= {"demo", "gemini", "openrouter"}
