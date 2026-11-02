"""Serve a build locally and record its autoplay: fixed-step frames, encoded with ffmpeg."""

import base64
import json
import shutil
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

READY = "window.sf && window.sf.ready === true"


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve(directory: Path):
    """Serve one build folder on 127.0.0.1 (random port) for the headless browser."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def wait_until(page, expression: str, timeout: float = 30) -> bool:
    """Poll `expression` with `page.evaluate` until it is true; False after `timeout` seconds.

    Not `page.wait_for_function`: when the expression is not true at its first check, it
    polls by compiling the string in the page, which a build's CSP (no 'unsafe-eval') blocks
    with an EvalError. Slow-loading builds then looked like they never booted (L2.2 live run:
    a build with 24 entities was reported "no-boot" while it ran fine in a browser)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if page.evaluate(expression):
                return True
        except PlaywrightError:  # the page is still navigating: not ready yet
            pass
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(50)


def encode(frames_dir: Path, fps: int, scale: int, out: Path) -> Path | None:
    """PNG frames -> H.264 mp4 scaled with nearest neighbour; None when ffmpeg is missing."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-framerate",
        str(fps),
        "-i",
        str(frames_dir / "%05d.png"),
        "-vf",
        f"scale=iw*{scale}:ih*{scale}:flags=neighbor,format=yuv420p",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "16",
        "-movflags",
        "+faststart",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    return out


@dataclass
class Recording:
    video: Path | None
    poster: Path
    summary: dict


def record_game(
    build_dir: Path, out_dir: Path, *, seconds: float = 20, fps: int = 30, scale: int = 2
) -> Recording:
    """Play the build's own autoplay for `seconds` (?demo=1&capture=1) and save demo.mp4 and
    poster.png. Frames are stepped by the SDK, so the same build always gives the same video."""
    out_dir.mkdir(parents=True, exist_ok=True)
    total = int(seconds * fps)
    with TemporaryDirectory() as tmp, serve(Path(build_dir)) as url:
        frames_dir = Path(tmp)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 320, "height": 180})
                errors: list[str] = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(f"{url}/index.html?demo=1&capture=1")
                if not wait_until(page, READY):
                    raise RuntimeError("the game never finished booting (sf.ready stayed false)")
                n = 0
                while n < total:
                    batch = page.evaluate(f"window.sfCapture({min(fps, total - n)}, {fps})")
                    for data_url in batch:
                        png = base64.b64decode(data_url.split(",", 1)[1])
                        (frames_dir / f"{n:05d}.png").write_bytes(png)
                        n += 1
                counts = page.evaluate("window.sf.counts")
                errors += page.evaluate("window.sf.errors")
            finally:
                browser.close()
        poster = out_dir / "poster.png"
        shutil.copy(frames_dir / f"{min(total - 1, int(fps * 2.5)):05d}.png", poster)
        video = encode(frames_dir, fps, scale, out_dir / "demo.mp4")
    summary = {
        "seconds": seconds,
        "fps": fps,
        "frames": total,
        "video": video.name if video else None,
        "events": {name: c["count"] for name, c in counts.items()},
        "errors": errors[:20],
    }
    (out_dir / "recording.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return Recording(video, poster, summary)
