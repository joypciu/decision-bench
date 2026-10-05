"""User flows for local Shipgate review persistence, including a real server restart."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time

import httpx
from playwright.sync_api import sync_playwright, expect

root = Path(__file__).resolve().parents[1]
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
base = f"http://127.0.0.1:{port}"
process = None


def stop():
    global process
    if process:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process = None


def start(directory):
    global process
    process = subprocess.Popen([sys.executable, str(root / "e2e/shipgate_server.py"), str(port), directory], cwd=root)
    for attempt in range(100):
        if process.poll() is not None:
            raise RuntimeError("Shipgate browser server exited")
        try:
            if httpx.get(base + "/health", timeout=1).status_code == 200:
                return
        except httpx.TransportError:
            pass
        time.sleep(0.1)
    raise RuntimeError("Shipgate browser server did not start")


with tempfile.TemporaryDirectory(prefix="shipgate-browser-") as directory:
    try:
        start(directory)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base)
            page.get_by_role("button", name="Auth bypass", exact=True).click()
            page.get_by_role("button", name="Review", exact=True).click()
            expect(page.locator(".verdict h2")).to_have_text("block", timeout=30000)
            expect(page.locator(".risks")).to_contain_text("auth.py")
            expect(page.locator("#comment-text")).to_contain_text("**block**")
            page.get_by_role("link", name="Saved review #1", exact=True).click()
            saved_url = page.url
            with page.expect_download() as pending:
                page.get_by_role("link", name="Download review", exact=True).click()
            assert pending.value.failure() is None
            exported = page.request.get(pending.value.url).json()
            assert exported["review"]["verdict"] == "block"
            assert "bypass auth" in exported["diff"]
            stop()
            start(directory)
            page.goto(saved_url)
            expect(page.locator(".verdict h2")).to_have_text("block")
            assert page.request.get(base + "/reviews/1/export").json() == exported
            page.goto(base)
            page.get_by_role("link", name="Open review #1", exact=False).click()
            expect(page.locator(".verdict h2")).to_have_text("block")
            page.get_by_role("button", name="Readme wording", exact=True).click()
            page.get_by_role("button", name="Review", exact=True).click()
            expect(page.locator(".verdict h2")).to_have_text("ship", timeout=30000)
            page.get_by_role("link", name="Saved review #2", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            if os.environ.get("SHIPGATE_E2E_SCREENSHOT"):
                page.screenshot(path=os.environ["SHIPGATE_E2E_SCREENSHOT"], full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), page.evaluate("[...document.querySelectorAll('body *')].filter(e => e.getBoundingClientRect().right > innerWidth).map(e => ({tag:e.tagName,class:e.className,width:e.getBoundingClientRect().width})).slice(0,20)")
            assert not errors, errors
            browser.close()
        print("PASS: sample diff review, risks/comment, saved links, export action/content, real server restart, recent review reopening, second review, mobile; no browser exceptions")
    finally:
        stop()
