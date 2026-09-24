from fastapi.testclient import TestClient


def test_settings_adds_a_provider_without_showing_the_key(app):
    client = TestClient(app)
    page = client.get("/settings")
    assert page.status_code == 200
    assert "Providers and keys" in page.text
    gemini = app.state.work.repo.get_provider_config("gemini")
    if gemini and gemini.api_key:
        assert gemini.api_key not in page.text

    secret = "gsk-test-secret-value"
    saved = client.post(
        "/settings/providers",
        data={
            "name": "groq",
            "kind": "openai",
            "base_url": "https://api.groq.com/openai/v1",
            "default_model": "llama-3.3-70b-versatile",
            "api_key": secret,
            "enabled": "on",
        },
        follow_redirects=True,
    )
    assert saved.status_code == 200
    assert secret not in saved.text
    assert "groq" in saved.text
    assert app.state.work.providers["groq"].configured is True
    assert app.state.work.providers["groq"].default_model == "llama-3.3-70b-versatile"

    listed = client.get("/api/providers")
    assert secret not in listed.text
    body = listed.json()
    groq = next(item for item in body if item["name"] == "groq")
    assert "api_key" not in groq
    assert groq["key_hint"].endswith("alue")

    kept = client.post(
        "/settings/providers",
        data={
            "name": "groq",
            "kind": "openai",
            "base_url": "https://api.groq.com/openai/v1",
            "default_model": "llama-3.3-70b-versatile",
            "api_key": "",
            "enabled": "on",
        },
        follow_redirects=True,
    )
    assert kept.status_code == 200
    assert app.state.work.repo.get_provider_config("groq").api_key == secret

    blocked = client.post("/settings/providers/gemini/delete", follow_redirects=True)
    assert "cannot" in blocked.text.lower() or "disabled" in blocked.text.lower()
    removed = client.post("/settings/providers/groq/delete", follow_redirects=True)
    assert removed.status_code == 200
    assert app.state.work.repo.get_provider_config("groq") is None
    assert "groq" not in app.state.work.providers
