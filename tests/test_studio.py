"""Phase 0 of the studio (docs/ui-plan.md): the React build is served at /, the old page at
/classic, a bare `spriteforge` opens it, and the localhost guard refuses cross-site writes."""

import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from typer.testing import CliRunner

from spriteforge import cli
from spriteforge.app.local_only import refusal
from spriteforge.app.server import STATIC, create_app
from spriteforge.config import init_workspace, load_config
from spriteforge.core.state import Store
from spriteforge.stages.workflow import start_run


@pytest.mark.parametrize(
    ("method", "headers", "refused"),
    [
        ("GET", {"host": "127.0.0.1:8765"}, False),
        ("GET", {"host": "localhost:8765"}, False),
        ("GET", {"host": "evil.example:8765"}, True),  # DNS rebinding
        ("GET", {}, True),
        ("POST", {"host": "127.0.0.1:8765"}, False),  # curl, tests: no Origin header
        ("POST", {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:8765"}, False),
        ("POST", {"host": "localhost:5173", "origin": "http://localhost:5173"}, False),  # Vite
        ("POST", {"host": "127.0.0.1:8765", "origin": "https://evil.example"}, True),
        ("POST", {"host": "127.0.0.1:8765", "origin": "http://127.0.0.1:9999"}, True),
        ("POST", {"host": "127.0.0.1:8765", "origin": "null"}, True),
        ("DELETE", {"host": "127.0.0.1:8765", "sec-fetch-site": "cross-site"}, True),
        ("POST", {"host": "127.0.0.1:8765", "sec-fetch-site": "same-origin"}, False),
    ],
)
def test_local_only_refusal(method, headers, refused):
    assert (refusal(method, headers) is not None) is refused


def test_studio_routes_and_cross_site_writes(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "mock-demo-v1"})
    with TestClient(create_app(store)) as client:
        home = client.get("/")
        assert home.status_code == 200 and '<div id="root">' in home.text
        assert 'id="timeline"' in client.get("/classic").text
        script = next(p.name for p in (STATIC / "assets").glob("index-*.js"))
        assert client.get(f"/assets/{script}").status_code == 200
        assert client.get("/api/runs").json()["runs"][0]["id"] == run.id
        assert client.get("/api/runs", headers={"host": "evil.example"}).status_code == 403
        gate = f"/api/runs/{run.id}/gates/g1"
        cross = client.post(gate, json={"action": "approve"}, headers={"origin": "https://x.io"})
        assert cross.status_code == 403
        assert cross.json() == {"detail": "Same-origin request required"}
        # The same request from the studio's own origin reaches the route (which refuses it
        # for its own reason: this demo run has no agent gates).
        own = client.post(gate, json={"action": "approve"}, headers={"origin": "http://testserver"})
        assert own.status_code == 409


def test_bare_command_opens_the_studio(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "setup", lambda config, workspace: ("cfg", "store"))
    monkeypatch.setattr(cli, "serve", lambda *args, **kwargs: calls.append((args, kwargs)))
    result = CliRunner().invoke(cli.app, [])
    assert result.exit_code == 0, result.output
    assert calls == [(("store", None, 8765, True, "cfg"), {})]


def test_studio_smoke_in_browser(tmp_path):
    cfg = load_config(None)
    store = Store(init_workspace(tmp_path) / "state.sqlite3")
    run = start_run(store, cfg, mock=True, goal="A slime that squishes through a cave")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    process = subprocess.Popen(
        [sys.executable, "-m", "spriteforge", "studio", "--workspace", str(tmp_path),
         "--port", str(port), "--no-open-browser"],
        # Without a key (and no .env read), so the studio offers mock only.
        env={**{k: v for k, v in os.environ.items() if k != "META_API_KEY"},
             "SPRITEFORGE_ENV_FILE": str(tmp_path / "no.env"),
             "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        with httpx.Client(base_url=url) as client:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                assert process.poll() is None, "studio exited"
                try:
                    if client.get("/api/runs").status_code == 200:
                        break
                except httpx.TransportError:
                    time.sleep(0.1)
            else:
                raise AssertionError("studio did not start")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(url)
            expect(page.get_by_role("heading", name="SpriteForge Studio")).to_be_visible()
            loaded = page.evaluate(
                "document.fonts.ready.then(f => [...f].some("
                "x => x.family.includes('Press Start 2P') && x.status === 'loaded'))"
            )
            assert loaded, "the bundled pixel font did not load"
            expect(page.get_by_role("status")).to_contain_text("No META_API_KEY")
            expect(page.get_by_role("button", name="Start with Muse")).to_have_count(0)
            projects = page.get_by_role("list", name="Projects").get_by_role("link")
            # Made before the studio started, so no worker has stepped it yet.
            expect(projects).to_contain_text("A slime that squishes through a cave")
            expect(projects).to_contain_text("pending")

            # Enter in the idea box starts the project and opens its Director chat.
            idea = page.get_by_label("Describe your game")
            idea.fill("A bat that rings a belfry")
            idea.press("Enter")
            page.wait_for_url(re.compile(r"/\?project=[0-9a-f]{32}$"))
            chat = page.get_by_role("region", name="Chat with Director")
            expect(chat).to_contain_text("A bat that rings a belfry")
            agents = page.get_by_role("navigation", name="Agents")
            expect(agents.get_by_role("link")).to_have_count(11)
            director = agents.get_by_role("link", name=re.compile(r"^Director"))
            expect(director.locator(".badge")).to_have_text("1", timeout=40000)

            # Every gate is answered from its card in the chat until the project is finished.
            seen = []
            for _ in range(10):
                card = chat.locator(".gate-card.open")
                expect(card).to_have_count(1, timeout=120000)
                title = card.locator("h3").inner_text()
                seen.append(title)
                card.get_by_role("button", name="Approve", exact=True).click()
                expect(chat.locator(".gate-card.open h3", has_text=title)).to_have_count(0)
                if title.startswith("G5"):
                    break
            assert [t.split()[0] for t in seen] == ["G1", "G1", "G1", "G2", "G3", "G4", "G5"]
            assert not any("(" in t and ")" not in t for t in seen), seen
            expect(page.locator(".topbar").get_by_role("link", name="Play v1")).to_be_visible()
            expect(chat).to_contain_text("Finished", timeout=60000)
            # Nothing is left working or waiting on the human (an agent the game did not need,
            # such as the Level/Tile Designer for a one-screen game, stays "waiting").
            expect(agents.locator(".agent-working, .agent-needs_you")).to_have_count(0)
            expect(director).to_have_class(re.compile("agent-done"))

            agents.get_by_role("link", name="Pixel Artist").click()
            expect(page).to_have_url(re.compile(r"agent=Pixel\+Artist"))
            art = page.get_by_role("region", name="Chat with Pixel Artist")
            expect(art.locator(".image-grid img").first).to_be_visible()

            agents.get_by_role("link", name="Game Designer").click()
            box = page.get_by_label("Message Game Designer")
            box.fill("Why did you pick this genre?")
            box.press("Enter")
            designer = page.get_by_role("region", name="Chat with Game Designer")
            expect(designer.locator(".bubble.agent")).to_contain_text("Offline mock")
            expect(designer.locator(".doc summary").first).to_be_visible()

            page.go_back()  # the URL is the place: back returns to the Pixel Artist
            expect(page.get_by_role("region", name="Chat with Pixel Artist")).to_be_visible()
            page.get_by_role("link", name="Projects").click()
            slime = page.get_by_role("list", name="Projects").get_by_role(
                "link", name=re.compile("A slime that squishes")
            )
            slime.click()
            page.wait_for_url(f"{url}/?project={run.id}")
            expect(page.get_by_role("region", name="Chat with Director")).to_contain_text(
                "G1 Choose a genre"
            )
            assert not errors
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)
