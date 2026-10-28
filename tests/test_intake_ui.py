"""Exercise the milestone acceptance path through the actual browser and CLI."""

import os
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright


def test_intake_browser(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "spriteforge",
            "start",
            "--intake",
            "--mock",
            "--workspace",
            str(tmp_path),
            "--port",
            str(port),
            "--no-open-browser",
        ],
        env={**os.environ, "PYTHONPATH": "src"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=url) as client:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                assert process.poll() is None
                try:
                    runs = client.get("/api/runs").json()["runs"]
                    if runs:
                        ident = runs[0]["id"]
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("CLI did not start")
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                page.set_default_timeout(40000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url)

                def approve(title):
                    expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                    page.get_by_role("button", name="Approve", exact=True).click()

                approve("G1 Choose a genre")
                expect(
                    page.get_by_role("heading", name="G1 Game brief", exact=True)
                ).to_be_visible()
                page.get_by_label("Environment", exact=True).fill("A moon garden")
                page.get_by_role("button", name="touch", exact=True).click()
                page.get_by_role(
                    "button", name="I'm not sure, suggest options", exact=True
                ).first.click()
                approve("G1 Game brief")
                expect(page.locator("#custom-fields")).to_contain_text("moon garden")
                approve("G1 Follow-up questions")
                expect(
                    page.get_by_role("heading", name="G2 Choose a concept style", exact=True)
                ).to_be_visible()
                assert page.locator("#custom-fields span").count() >= 8
                approve("G2 Choose a concept style")
                expect(
                    page.get_by_role("heading", name="G2 CONCEPT v1 — not final art", exact=True)
                ).to_be_visible()
                expect(page.locator("#gate-artifacts img")).to_have_count(2)
                page.get_by_label("Feedback", exact=True).fill("Make the hero rounder")
                page.get_by_role("button", name="Request changes", exact=True).click()
                approve("Confirm concept change requests")
                expect(
                    page.get_by_role("heading", name="G2 CONCEPT v2 — not final art", exact=True)
                ).to_be_visible()
                expect(page.locator("#usage")).to_contain_text("2 / 150 image calls")
                page.reload()
                expect(
                    page.get_by_role("heading", name="G2 CONCEPT v2 — not final art", exact=True)
                ).to_be_visible()
                page.get_by_label("Palette swatch 1", exact=True).fill("#123456")
                page.screenshot(path=str(tmp_path / "concept-v2.png"), full_page=True)
                approve("G2 CONCEPT v2 — not final art")
                expect(page.locator("#status")).to_contain_text("completed")
                root = tmp_path / "runs" / ident
                assert (root / "style.json").exists()
                assert (root / "anchor.png").read_bytes() == (root / "concept/v2.png").read_bytes()
                assert not errors
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
