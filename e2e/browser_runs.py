"""Exercise saved decisions and comparison through the UI; uses demo inference."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
process = subprocess.Popen([sys.executable, str(root / "e2e/browser_server.py"), str(port)], cwd=root)
try:
    for attempt in range(100):
        if process.poll() is not None:
            raise RuntimeError("Test server exited")
        try:
            if httpx.get(base + "/", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    else:
        raise RuntimeError("Test server did not start")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(base + "/compare")
        expect(page.get_by_text("Choose two saved runs", exact=False)).to_be_visible()
        ids = []
        for sample, decision in [("Documentation change", "ship"), ("Security review", "block")]:
            page.goto(base)
            page.get_by_role("button", name=sample, exact=False).click()
            page.get_by_role("button", name="Review", exact=True).click()
            expect(page.locator("h1.decision")).to_have_text(decision, timeout=30000)
            ids.append(page.url.rsplit("/", 1)[1])
        page.get_by_role("link", name="Compare run", exact=True).click()
        expect(page.locator('select[name="left"]')).to_have_value(ids[1])
        page.get_by_label("First run", exact=True).select_option(ids[0])
        page.get_by_label("Second run", exact=True).select_option(ids[1])
        page.get_by_role("button", name="Compare runs", exact=True).click()
        decision_row = page.locator(".comparison tr").filter(has=page.get_by_role("rowheader", name="Decision", exact=False))
        expect(decision_row).to_contain_text("ship")
        expect(decision_row).to_contain_text("block")
        expect(decision_row).to_have_class("comparison-changed")
        expect(page.get_by_text("These runs use different case inputs", exact=False)).to_be_visible()
        expect(page.get_by_role("heading", name="Specialist results")).to_have_count(2)
        with page.expect_download() as pending:
            page.get_by_role("link", name="Download report", exact=True).first.click()
        assert pending.value.failure() is None
        exported = page.request.get(pending.value.url).json()
        assert exported["tree"]["run"]["id"] == ids[0]
        assert len(exported["tree"]["children"]) == 3
        page.reload()
        expect(page.locator('select[name="left"]')).to_have_value(ids[0])
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_role("button", name="Dark", exact=True).click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if os.environ.get("BENCH_E2E_SCREENSHOT"):
            page.screenshot(path=os.environ["BENCH_E2E_SCREENSHOT"], full_page=True)
        page.get_by_label("Second run", exact=True).select_option(ids[0])
        page.get_by_role("button", name="Compare runs", exact=True).click()
        expect(page.get_by_text("You selected the same run twice", exact=False)).to_be_visible()
        page.goto(base)
        page.get_by_label("Search recent runs").fill("no-matches")
        expect(page.locator("#no-run-matches")).to_be_visible()
        page.goto(base + "/runs/" + ids[1])
        page.get_by_role("link", name="Review edited case", exact=True).click()
        expect(page.get_by_text("Editing a copy", exact=False)).to_be_visible()
        expect(page.locator('textarea[name="input"]')).to_contain_text("bypass auth")
        expect(page.locator('select[name="bot_id"]')).to_have_value("change-lead")
        expect(page.locator('select[name="provider"]')).to_have_value("demo")
        expect(page.locator('input[name="model"]')).to_have_value("demo")
        page.locator('textarea[name="input"]').fill("Update README documentation.")
        page.get_by_role("button", name="Review", exact=True).click()
        expect(page.locator("h1.decision")).to_have_text("ship", timeout=30000)
        assert page.url.rsplit("/", 1)[1] not in ids
        original = page.request.get(base + "/api/runs/" + ids[1]).json()["run"]
        assert original["output"]["verdict"] == "block"
        assert "bypass auth" in original["input_text"]
        page.set_viewport_size({"width": 1440, "height": 1000})
        evaluation_ids = []
        for _ in range(2):
            page.goto(base + "/evals")
            page.locator('select[name="bot_id"]').select_option("change-lead")
            page.locator('select[name="pack_id"]').select_option("change_risk")
            page.locator('select[name="provider"]').select_option("demo")
            page.get_by_role("button", name="Run gold set", exact=True).click()
            expect(page.get_by_role("link", name="Download evaluation", exact=True)).to_be_visible(timeout=30000)
            evaluation_ids.append(page.url.split("id=", 1)[1])
        page.get_by_role("link", name="Compare evaluations", exact=True).click()
        expect(page.get_by_label("Baseline evaluation")).to_have_value(evaluation_ids[1])
        page.get_by_label("Baseline evaluation").select_option(evaluation_ids[0])
        page.get_by_label("Candidate evaluation").select_option(evaluation_ids[1])
        page.get_by_role("button", name="Compare evaluations", exact=True).click()
        expect(page.locator('tr[data-case-state="unchanged"]')).to_have_count(2)
        page.get_by_label("Search cases", exact=True).fill("AUTH-BYPASS")
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(1)
        page.get_by_label("Case change", exact=True).select_option("regressed")
        expect(page.get_by_text("No cases match these filters.", exact=True)).to_be_visible()
        page.get_by_label("Case change", exact=True).select_option("unchanged")
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(1)
        page.reload()
        expect(page.get_by_label("Search cases", exact=True)).to_have_value("AUTH-BYPASS")
        expect(page.get_by_label("Case change", exact=True)).to_have_value("unchanged")
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(1)
        with page.expect_download() as comparison_download:
            page.get_by_role("link", name="Export all cases CSV", exact=True).click()
        assert comparison_download.value.failure() is None
        import csv, io
        csv_response = page.request.get(comparison_download.value.url)
        comparison_rows = list(csv.DictReader(io.StringIO(csv_response.text().lstrip("\ufeff"))))
        assert len(comparison_rows) == 2
        assert all(row["change"] == "unchanged" for row in comparison_rows)
        page.get_by_role("button", name="Clear case filters", exact=True).click()
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(2)
        with page.expect_download() as download:
            page.get_by_role("link", name="Download candidate", exact=True).click()
        assert download.value.failure() is None
        report = page.request.get(download.value.url).json()["evaluation"]
        assert report["id"] == evaluation_ids[1]
        assert report["pass_count"] == report["case_count"] == 2
        assert all(len(item["case_fingerprint"]) == 64 for item in report["results"])
        page.reload()
        expect(page.get_by_label("Candidate evaluation")).to_have_value(evaluation_ids[1])
        page.set_viewport_size({"width": 390, "height": 844})
        page.get_by_label("Search cases", exact=True).fill("README")
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(1)
        page.get_by_role("button", name="Clear case filters", exact=True).click()
        expect(page.locator('tr[data-case-state]:visible')).to_have_count(2)
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if os.environ.get("BENCH_EVAL_SCREENSHOT"):
            page.screenshot(path=os.environ["BENCH_EVAL_SCREENSHOT"], full_page=True)
        page.locator('tr[data-case-state="unchanged"] a').first.click()
        expect(page.get_by_role("link", name="Download report", exact=False)).to_be_visible()
        assert page.request.post(base + "/test/evaluation-history").status == 200
        page.goto(base + "/evals")
        page.get_by_role("link", name="Search all evaluations", exact=True).click()
        page.get_by_label("Search evaluations", exact=True).fill("synthetic-history")
        page.get_by_role("button", name="Search history", exact=True).click()
        history_table = page.get_by_role("table", name="Saved evaluations", exact=True)
        expect(history_table.locator("tbody tr")).to_have_count(20)
        page.get_by_role("link", name="Older evaluations", exact=True).click()
        expect(history_table.locator("tbody tr")).to_have_count(4)
        expect(page.get_by_label("Search evaluations", exact=True)).to_have_value("synthetic-history")
        page.get_by_role("link", name="Newer evaluations", exact=True).click()
        page.get_by_label("Evaluation outcome", exact=True).select_option("failed")
        page.get_by_role("button", name="Search history", exact=True).click()
        expect(history_table.locator("tbody tr")).to_have_count(12)
        page.reload()
        expect(page.get_by_label("Evaluation outcome", exact=True)).to_have_value("failed")
        with page.expect_download() as history_download:
            history_table.get_by_role("link", name="Download", exact=True).first.click()
        assert history_download.value.failure() is None
        assert page.request.get(history_download.value.url).json()["evaluation"]["pass_count"] == 0
        history_table.locator("tbody tr a").first.click()
        expect(page.get_by_role("link", name="Download evaluation", exact=True)).to_be_visible()
        page.go_back()
        page.get_by_label("Search evaluations", exact=True).fill("missing <script>")
        page.get_by_role("button", name="Search history", exact=True).click()
        expect(page.get_by_text("No evaluations match these filters.", exact=True)).to_be_visible()
        page.get_by_role("link", name="Clear history filters", exact=True).click()
        expect(page.get_by_label("Evaluation outcome", exact=True)).to_have_value("all")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if os.environ.get("BENCH_HISTORY_SCREENSHOT"):
            page.screenshot(path=os.environ["BENCH_HISTORY_SCREENSHOT"], full_page=True)
        assert not errors, errors
        browser.close()
    print("PASS: evaluation history/search/pagination/outcomes/retained filters/downloads/reopening/empty/mobile, case search/state/no matches/clear/reload/mobile/full CSV while filtered, gold-set creation/evaluation comparison/report/fingerprints/run links/mobile, edited-case prefill/new decision/original preservation, run comparison, warnings, specialists, exports, reload, theme, search; no browser exceptions")
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
