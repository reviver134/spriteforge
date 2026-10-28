"""Run against `spriteforge start --mock --no-open-browser` on localhost."""

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--url", default="http://127.0.0.1:8765")
parser.add_argument("--channel", default="chrome")
parser.add_argument("--output", type=Path, default=Path("workspace/ui-smoke.png"))
args = parser.parse_args()
args.output.parent.mkdir(parents=True, exist_ok=True)
with sync_playwright() as playwright:
    browser = playwright.chromium.launch(channel=args.channel, headless=True)
    page = browser.new_page(viewport={"width": 1100, "height": 1000})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(args.url)
    page.wait_for_function(
        "document.getElementById('status').textContent.includes('completed')", timeout=25000
    )
    page.locator("#artifacts img").nth(1).wait_for()
    page.wait_for_function("[...document.images].every(i=>i.complete && i.naturalWidth===32)")
    assert page.locator("#timeline li").count() == 14
    assert not page.locator("#error").inner_text()
    assert not errors, errors
    page.screenshot(path=str(args.output), full_page=True)
    browser.close()
print(f"Browser smoke passed: {args.output}")
