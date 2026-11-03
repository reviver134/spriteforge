import asyncio
import os
import socket
import subprocess
import sys
import time

import httpx
from playwright.sync_api import expect, sync_playwright

from spriteforge.core.state import Store
from spriteforge.mcp.registry import Server
from spriteforge.mcp.service import MCPService


def test_browser_role_enable_and_durable_confirmation(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "extension-test"})
    (tmp_path / "sample.txt").write_text("sprite notes")
    service = MCPService(store)
    service.registry.add(
        "files",
        Server(
            command=sys.executable, args=["-m", "spriteforge.mcp.filesystem_read", str(tmp_path)]
        ),
    )
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
            for _ in range(100):
                try:
                    if client.get("/api/runs").status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert process.poll() is None
                time.sleep(0.1)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_default_timeout(30000)
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(url + "/classic")
                page.locator("#extensions summary").first.click()
                page.locator("#extension-role").select_option("Pixel Artist")
                expect(page.locator("#skill-list")).to_contain_text("pixel-art-prompting")
                page.get_by_role("button", name="Inspect tools: files").click()
                expect(
                    page.get_by_role("button", name="Enable files for Pixel Artist")
                ).to_be_enabled()
                page.get_by_role("button", name="Enable files for Pixel Artist").click()
                expect(
                    page.get_by_role("button", name="Disable files for Pixel Artist")
                ).to_be_visible()
                page.locator("#mcp-tool").fill("files.read_text")
                page.locator("#mcp-arguments").fill('{"path":"sample.txt"}')
                page.get_by_role("button", name="Request tool call").click()
                expect(page.locator("#mcp-confirmations")).to_contain_text("sample.txt")
                assert not [e for e in store.events(run.id) if e.type == "mcp.called"]
                page.reload()
                expect(page.get_by_role("button", name="Approve tool call")).to_be_visible()
                page.screenshot(path=str(tmp_path / "mcp-confirmation.png"), full_page=True)
                page.get_by_role("button", name="Approve tool call").click()
                expect(page.locator("#mcp-confirmations")).to_be_empty()
                assert len([e for e in store.events(run.id) if e.type == "mcp.called"]) == 1
                assert not errors
                browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_official_sdk_streamable_http(tmp_path):
    from spriteforge.mcp.client import MCPClient

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    source = tmp_path / "http_server.py"
    source.write_text(
        f'from mcp.server.fastmcp import FastMCP\ns=FastMCP("test",host="127.0.0.1",port={port},stateless_http=True)\n'
        '@s.tool()\ndef echo(text: str) -> str:\n    return text\ns.run(transport="streamable-http")\n'
    )
    process = subprocess.Popen(
        [sys.executable, str(source)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        url = f"http://127.0.0.1:{port}/mcp"
        for _ in range(100):
            try:
                httpx.get(url, timeout=1)
                break
            except httpx.TransportError:
                assert process.poll() is None
                time.sleep(0.1)

        async def exercise():
            client = MCPClient()
            server = Server(transport="http", url=url)
            assert (await client.discover(server))[0]["name"] == "echo"
            result = await client.call(server, "echo", {"text": "pixels"})
            assert not result["isError"] and "pixels" in str(result)

        asyncio.run(exercise())
    finally:
        process.terminate()
        process.wait(timeout=10)
