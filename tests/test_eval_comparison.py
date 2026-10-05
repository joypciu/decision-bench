from dataclasses import replace

from fastapi.testclient import TestClient

from decision_bench.eval_compare import compare_evaluations


def evaluation(client):
    response = client.post("/api/evals", json={"bot_id": "change-lead", "pack_id": "change_risk", "provider": "demo"})
    assert response.status_code == 200
    return response.json()


def test_eval_report_and_comparison_routes(app):
    with TestClient(app) as client:
        saved = evaluation(client)
        assert all(len(r["case_fingerprint"]) == 64 for r in saved["results"])
        export = client.get(f"/api/evals/{saved['id']}/export")
        assert export.status_code == 200
        assert "attachment" in export.headers["content-disposition"]
        assert export.json()["evaluation"] == saved
        assert client.get(f"/api/evals/{saved['id']}").json() == saved
        response = client.get("/evals/compare", params={"left": saved["id"], "right": saved["id"]})
        assert response.status_code == 200
        assert "You selected the same evaluation twice" in response.text
        assert 'data-case-state="unchanged"' in response.text
        assert client.get("/evals/compare?left=missing").status_code == 404
        assert client.get("/api/evals/missing/export").status_code == 404
        assert client.get("/api/evals/missing").status_code == 404
        assert "Choose two saved evaluations" in client.get("/evals/compare").text


def test_comparison_excludes_changed_missing_and_duplicate_context(app):
    with TestClient(app) as client:
        saved = evaluation(client)
    first = app.state.work.repo.get_eval(saved["id"])
    row = dict(first.results[0])
    baseline = replace(first, results=[{**row, "passed": True}], pass_count=1, case_count=1)
    failed = replace(first, results=[{**row, "passed": False}], pass_count=0, case_count=1)
    assert compare_evaluations(baseline, failed)["counts"] == {"regressed": 1}
    assert compare_evaluations(failed, baseline)["counts"] == {"improved": 1}
    changed = replace(failed, results=[{**failed.results[0], "case_fingerprint": "different"}])
    assert compare_evaluations(baseline, changed)["counts"] == {"case_changed": 1}
    legacy = replace(failed, results=[{k: v for k, v in failed.results[0].items() if k != "case_fingerprint"}])
    assert compare_evaluations(baseline, legacy)["comparable"] == 0
    assert compare_evaluations(baseline, legacy)["counts"] == {"unknown_context": 1}
    other_pack = replace(failed, pack_id="different")
    assert compare_evaluations(baseline, other_pack)["counts"] == {"different_pack": 1}
    added = replace(failed, results=[{**row, "case_id": "new"}])
    assert compare_evaluations(baseline, added)["counts"] == {"removed": 1, "added": 1}
    duplicates = replace(failed, results=[row, row])
    assert compare_evaluations(baseline, duplicates)["counts"] == {"ambiguous": 1}


def test_evaluation_pins_lead_version_when_bot_changes_mid_run(app, monkeypatch):
    from decision_bench import services

    state = app.state.work
    original = services.start_run
    initial = state.repo.latest_version("change-lead")
    calls = []

    def updating_run(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            services.save_version(state, "change-lead", instructions="An updated lead version.")
        return result

    monkeypatch.setattr(services, "start_run", updating_run)
    result = services.run_eval(state, bot_id="change-lead", pack_id="change_risk", provider="demo", model=None)
    assert state.repo.latest_version("change-lead").id != initial.id
    assert result.bot_version_id == initial.id
    assert all(run.bot_version_id == initial.id for run in calls)
