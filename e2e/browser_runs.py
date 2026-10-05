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
        assert not errors, errors
        browser.close()
    print("PASS: edited-case prefill/new decision/original preservation, empty comparison, two UI reviews, changed decisions, case warning, specialists, export action/payload, reload, mobile theme, same-run warning, search; no browser exceptions")
finally:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
