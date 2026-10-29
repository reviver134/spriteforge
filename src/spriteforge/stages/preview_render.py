"""Export the real atlas/layout and capture Phaser at device scale 1 via Playwright."""

import io
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from PIL import Image, ImageColor
from playwright.sync_api import sync_playwright

from spriteforge.pixel.atlas import pack

from .concept import png
from .preview_assets import asset_record

TEMPLATE = Path(__file__).parents[1] / "templates" / "phaser"


def hud_text_color(palette):
    return max(
        palette,
        key=lambda c: sum(
            w * v for w, v in zip((0.2126, 0.7152, 0.0722), ImageColor.getrgb(c), strict=True)
        ),
    )


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def capture(directory):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(QuietHandler, directory=str(directory.parent))
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(
                    viewport={"width": 320, "height": 180}, device_scale_factor=1
                )
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{server.server_port}/{directory.name}/index.html")
                page.wait_for_function("window.previewReady === true", timeout=30000)
                errors += page.evaluate("window.previewErrors")
                if errors:
                    raise RuntimeError("Phaser preview failed: " + "; ".join(errors))
                if page.evaluate("Phaser.VERSION") != "3.90.0":
                    raise RuntimeError("Unexpected Phaser runtime version")
                return page.locator("canvas").screenshot()
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def render_preview(ctx, plan, layout):
    prefix = f"preview/v{plan.version}"
    names = [prefix + "/gameplay-4x.png", prefix + "/readability.json"]
    if all(n in ctx.artifacts for n in names):
        return names
    records = [asset_record(ctx, ident, version) for ident, version in plan.versions.items()]
    sprites = {r.id: Image.open(io.BytesIO(ctx.data(r.artifact))).convert("RGBA") for r in records}
    with TemporaryDirectory() as temp:
        pack(sprites, Path(temp))
        for filename in ("atlas.png", "atlas.json"):
            ctx.save(
                prefix + "/" + filename,
                (Path(temp) / filename).read_bytes(),
                "Programmer",
                "image/png" if filename.endswith("png") else "application/json",
            )
    for filename, source in [
        ("phaser.min.js", "vendor/phaser.min.js"),
        ("room.js", "room.js"),
        ("font.xml", "font.xml"),
        ("LICENSE.md", "vendor/LICENSE.md"),
    ]:
        ctx.save(
            prefix + "/runtime/" + filename,
            (TEMPLATE / source).read_bytes(),
            "Programmer",
            "text/plain",
        )
    ctx.save(
        prefix + "/index.html", (TEMPLATE / "preview.html").read_bytes(), "Programmer", "text/html"
    )
    ctx.save(prefix + "/style.json", plan.style.model_dump(), "Art Director")
    font = Image.open(TEMPLATE / "font.png").convert("RGBA")
    text_color = hud_text_color(plan.style.palette)
    tinted = Image.new("RGBA", font.size, ImageColor.getrgb(text_color))
    tinted.putalpha(font.getchannel("A"))
    ctx.save(prefix + "/font.png", png(tinted), "Programmer", "image/png")
    raw = capture(ctx.root / prefix)
    native = Image.open(io.BytesIO(raw)).convert("RGB")
    if native.size != (320, 180):
        raise ValueError("Phaser screenshot must be exactly 320x180")
    ctx.save(prefix + "/gameplay.png", raw, "Programmer", "image/png")
    ctx.save(
        prefix + "/gameplay-4x.png",
        png(native.resize((1280, 720), Image.Resampling.NEAREST)),
        "Programmer",
        "image/png",
    )
    colors = {
        "#" + "".join(f"{c:02X}" for c in pixel[:3])
        for sprite in sprites.values()
        for pixel in sprite.getdata()
        if pixel[3]
    }
    colors.update(
        [plan.style.palette[0], plan.style.palette[1], plan.style.palette[-1], text_color]
    )
    palette = [c for c in plan.style.palette if c in colors]
    checks = {r.id: ctx.read(r.provenance) for r in records}
    ctx.save(
        prefix + "/readability.json",
        {
            "version": plan.version,
            "hero_logical_height": checks["hero"]["logical_height"],
            "palette_size": len(palette),
            "palette": palette,
            "warnings": [
                f"{ident}: {warning}"
                for ident, check in checks.items()
                for warning in check["processing"]["warnings"]
            ],
            "assets": {
                r.id: {
                    "version": r.version,
                    "status": r.status,
                    "failures": checks[r.id]["failures"],
                }
                for r in records
            },
            "screen": [320, 180],
            "scale": 4,
            "renderer": "Phaser 3.90.0 / Canvas",
        },
        "QA Playtester",
    )
    ctx.save(prefix + "/registry.json", {r.id: r.model_dump() for r in records}, "Director")
    return names
