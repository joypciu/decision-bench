from dataclasses import replace

from fastapi.testclient import TestClient


def test_history_search_filters_before_pagination_and_preserves_saved_evals(app):
    with TestClient(app) as client:
        response = client.post("/api/evals", json={"bot_id": "change-lead", "pack_id": "change_risk", "provider": "demo"})
        original = response.json()
        saved = app.state.work.repo.get_eval(original["id"])
        for number in range(24):
            app.state.work.repo.create_eval(replace(saved, id=f"synthetic-record-{number:02}",
                created_at=f"2026-10-05T00:{number:02}:00", pass_count=2 if number % 2 else 0,
                model="search%_literal" if number == 0 else "synthetic-model"))
        repo = app.state.work.repo
        first, total = repo.search_evals(q="synthetic-record")
        assert total == 24 and len(first) == 20
        second, _ = repo.search_evals(q="synthetic-record", offset=20)
        assert len(second) == 4
        assert not set(item.id for item in first) & set(item.id for item in second)
        assert repo.search_evals(q="synthetic-record", outcome="passed")[1] == 12
        assert repo.search_evals(q="synthetic-record", outcome="failed")[1] == 12
        assert repo.search_evals(q="%_literal")[1] == 1
        assert repo.search_evals(q="' OR 1=1 --")[1] == 0
        assert repo.search_evals(q="Change-risk lead")[1] == 25
        result = client.get("/evals/history?q=synthetic-record")
        assert result.status_code == 200 and "24 matching evaluations" in result.text
        assert "Older evaluations" in result.text
        result = client.get("/evals/history?q=synthetic-record&page=2")
        assert "Newer evaluations" in result.text
        assert "No evaluations match" in client.get("/evals/history?q=missing").text
        assert client.get("/evals/history?page=0").status_code == 422
        assert client.get("/evals/history?outcome=invalid").status_code == 422
        assert client.get("/evals/history", params={"q": "x"*121}).status_code == 422
        assert client.get(f"/api/evals/{original['id']}").json() == original
