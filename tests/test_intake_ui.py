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
            "--agents",
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
                page.set_default_timeout(180000)
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
                    page.get_by_role(
                        "heading", name="G2 Optional mood board, not final art", exact=True
                    )
                ).to_be_visible()
                approve("G2 Optional mood board, not final art")
                expect(
                    page.get_by_role("heading", name="G2 Gameplay preview v1", exact=True)
                ).to_be_visible(timeout=180000)
                expect(page.locator("#gate-artifacts img")).to_have_count(2)
                expect(page.locator("#gate-artifacts")).to_contain_text(
                    "This is what your game will look like"
                )
                expect(page.locator("#preview-summary")).to_contain_text("logical pixels tall")
                main_box = page.locator("#gate-artifacts .gameplay-image").bounding_box()
                mood_box = page.locator("#gate-artifacts .mood-image").bounding_box()
                assert main_box["width"] > mood_box["width"]
                assert abs(main_box["y"] - mood_box["y"]) < 2
                page.get_by_label("Feedback", exact=True).fill("Make the hero rounder")
                page.get_by_label("Change targets (assets, layout or palette)", exact=True).fill(
                    "hero"
                )
                page.get_by_role("button", name="Request changes", exact=True).click()
                approve("Confirm gameplay preview changes")
                expect(
                    page.get_by_role("heading", name="G2 Gameplay preview v2", exact=True)
                ).to_be_visible(timeout=180000)
                expect(page.locator("#usage")).to_contain_text("14 / 150 image calls")
                expect(page.locator("#preview-history a")).to_have_count(2)
                page.reload()
                expect(
                    page.get_by_role("heading", name="G2 Gameplay preview v2", exact=True)
                ).to_be_visible()
                page.screenshot(path=str(tmp_path / "gameplay-v2.png"), full_page=True)
                approve("G2 Gameplay preview v2")
                expect(page.locator("#status")).to_contain_text("completed")
                root = tmp_path / "runs" / ident
                assert (root / "style.json").exists()
                assert (root / "anchor.png").read_bytes() == (
                    root / "preview/assets/hero/v2/sprite.png"
                ).read_bytes()
                assert (root / "preview/locked-assets.json").exists()
                assert not errors
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
