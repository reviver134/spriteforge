"""Headless browser acceptance: real CLI process death while a gate is paused."""

import os
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright


def wait_for_run(client, process):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        assert process.poll() is None, "CLI exited unexpectedly"
        try:
            runs = client.get("/api/runs").json()["runs"]
            run = next(
                (r for r in runs if r["config"].get("workflow") == "agent-runtime-mock-v1"), None
            )
            if run:
                return run["id"]
        except (httpx.TransportError, KeyError):
            pass
        time.sleep(0.1)
    raise AssertionError("CLI did not start")


def test_browser_gate_restart_and_hero_feedback(tmp_path):
    # Locator assertions have a separate timeout from page actions. This workflow
    # intentionally advances several one-second checkpoints between review gates.
    expect.set_options(timeout=40000)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    common = ["--workspace", str(tmp_path), "--port", str(port), "--no-open-browser"]
    env = {**os.environ, "PYTHONPATH": "src"}
    process = None
    with (
        (tmp_path / "runtime.log").open("w+") as log,
        httpx.Client(base_url=url, timeout=30) as client,
    ):

        def launch(args):
            return subprocess.Popen(
                [sys.executable, "-m", "spriteforge", *args, *common],
                env=env,
                stdout=log,
                stderr=log,
            )

        try:
            process = launch(["start", "--mock", "--agents", "--legacy"])
            run_id = wait_for_run(client, process)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1200, "height": 1000})
                page.set_default_timeout(40000)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(url, wait_until="domcontentloaded")
                expect(page.get_by_role("heading", name="G1 Brief", exact=True)).to_be_visible()
                page.get_by_label("Game brief").fill("Small top-down dungeon crawler")
                page.get_by_role("button", name="Approve", exact=True).click()
                expect(
                    page.get_by_role("heading", name="G2 Concept preview", exact=True)
                ).to_be_visible()
                page.get_by_role("button", name="Approve", exact=True).click()
                expect(
                    page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
                ).to_be_visible()
                expect(page.locator("#gate-artifacts img")).to_have_count(3)
                before = client.get(f"/api/runs/{run_id}").json()
                assert before["run"]["status"] == "paused"
                gate_id = before["gate"]["id"]
                images = [a for a in before["artifacts"] if a["name"].startswith("assets/")]
                process.kill()
                process.wait(timeout=10)
                process = launch(["resume", run_id, "--legacy"])
                assert wait_for_run(client, process) == run_id
                page.reload(wait_until="domcontentloaded")
                expect(
                    page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
                ).to_be_visible()
                restored = client.get(f"/api/runs/{run_id}").json()
                assert restored["gate"]["id"] == gate_id
                assert all(a in restored["artifacts"] for a in images)
                page.get_by_label("Art", exact=True).check()
                page.get_by_label("Feedback", exact=True).fill("make the hero rounder")
                page.get_by_role("button", name="Request changes", exact=True).click()
                expect(
                    page.get_by_role("heading", name="Confirm parsed change requests", exact=True)
                ).to_be_visible()
                expect(page.locator("#change-requests")).to_contain_text('["hero"]')
                proposed = client.get(f"/api/runs/{run_id}").json()
                assert proposed["gate"]["changes"]["requests"][0]["target"] == ["hero"]
                assert (
                    len([a for a in proposed["artifacts"] if a["name"].startswith("assets/")]) == 2
                )
                expect(page.get_by_role("button", name="Approve", exact=True)).to_be_enabled()
                page.screenshot(path=str(tmp_path / "change-confirmation.png"), full_page=True)
                page.get_by_role("button", name="Approve", exact=True).click()
                expect(
                    page.get_by_role("heading", name="G3 Asset contact sheet", exact=True)
                ).to_be_visible()
                revised = client.get(f"/api/runs/{run_id}").json()
                names = {a["name"] for a in revised["artifacts"]}
                assert "assets/hero/v2.png" in names
                assert "assets/tile/v2.png" not in names
                assert not errors
                page.get_by_role("button", name="Abort", exact=True).click()
                expect(page.locator("#status")).to_contain_text("aborted")
                browser.close()
        finally:
            if process and process.poll() is None:
                process.kill()
                process.wait(timeout=10)
