"""G3 through the real CLI and browser, using only offline providers."""

import os
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright

from spriteforge.config import load_config
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.stages.asset_store import ProductionStore
from spriteforge.stages.workflow import advance_until_gate, start_run


def test_contact_sheet_targeted_revision_and_cli_restart(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, load_config())
    advance_until_gate(store, run.id)
    for _ in range(5):
        ScriptedHuman([Feedback(action="approve")]).answer(Gates(store), run.id)
        advance_until_gate(store, run.id)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"

    def launch():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "spriteforge",
                "resume",
                run.id,
                "--assets",
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
        deadline = time.monotonic() + 40
        with httpx.Client() as client:
            while time.monotonic() < deadline:
                assert process.poll() is None
                try:
                    if client.get(url + "/api/runs").status_code == 200:
                        return process
                except httpx.TransportError:
                    pass
                time.sleep(0.1)
        process.terminate()
        process.wait(timeout=10)
        raise AssertionError("CLI did not start")

    process = launch()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1280, "height": 1000})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.set_default_timeout(180000)
            page.goto(url)
            expect(
                page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
            ).to_be_visible(timeout=90000)
            expect(page.locator("#gate-artifacts img[src]")).to_have_count(4)
            expect(page.locator('[data-asset-id="hero"]')).to_contain_text("PASS")
            first = ProductionStore(store, run.id).records()
            calls = len([e for e in store.events(run.id) if e.type == "cost.reserved"])
            process.terminate()
            process.wait(timeout=10)
            process = launch()
            page.reload()
            expect(
                page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
            ).to_be_visible(timeout=90000)
            assert ProductionStore(store, run.id).records() == first
            assert len([e for e in store.events(run.id) if e.type == "cost.reserved"]) == calls
            page.get_by_role("button", name="Change hero", exact=True).click()
            page.get_by_label("Feedback", exact=True).fill("Make the hero rounder")
            page.get_by_role("button", name="Request changes", exact=True).click()
            expect(
                page.get_by_role("heading", name="Confirm asset changes and dependent frames")
            ).to_be_visible(timeout=90000)
            expect(page.locator("#change-requests")).to_contain_text("hero__walk_1")
            assert ProductionStore(store, run.id).records() == first
            page.get_by_role("button", name="Approve", exact=True).click()
            expect(
                page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
            ).to_be_visible(timeout=90000)
            expect(page.locator('[data-asset-id="hero"]')).to_contain_text("v2")
            after = ProductionStore(store, run.id).records()
            assert {name for name in first if first[name] != after[name]} == {
                "hero",
                "hero__walk_0",
                "hero__walk_1",
            }
            page.screenshot(path=str(tmp_path / "g3-contact-sheet.png"), full_page=True)
            page.get_by_role("button", name="Approve", exact=True).click()
            expect(page.locator("#status")).to_contain_text("completed")
            assert not errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
