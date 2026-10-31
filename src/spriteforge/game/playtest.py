"""Deterministic playtest: run the whole session headless with the game's own bot and check
the design's events.

The page is loaded with `?autoplay=1&headless=1&god=1`: logic-only steps (Phaser's
headlessStep, no drawing, so a 10 minute session takes seconds), the game's autoplay policy
instead of the keyboard, and god mode (the bot may be hurt but not killed) so it reaches the
end of the session. The SDK steps fixed frames, so the same build always gives the same report.

`capture_at` (game seconds) adds screenshots for the art review: at each time one frame is
drawn (`sfShot`; the game logic is the same as in a logic-only step), so sprites appear on the
real floor at game scale with the HUD and crowds. A session that ends before a capture time
gets one last shot when it ends.
"""

import base64
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from .design import Check
from .record import serve

CHUNK_SECONDS = 30  # game seconds per browser call (keeps each evaluate short)
GRACE_SECONDS = 30  # the session may end a little after its nominal length


def evaluate_checks(checks: list[Check], counts: dict) -> list[dict]:
    results = []
    for c in checks:
        info = counts.get(c.event) or {}
        if c.by_second:  # half a second of slack for frame-stepped timers
            got = sum(1 for t in info.get("times", []) if t <= c.by_second + 0.5)
        else:
            got = info.get("count", 0)
        results.append(
            {
                "id": c.id,
                "event": c.event,
                "description": c.description,
                "min_count": c.min_count,
                "by_second": c.by_second,
                "count": got,
                "first_at": info.get("first"),
                "ok": got >= c.min_count,
            }
        )
    return results


def review_times(session_seconds: float) -> list[float]:
    """Early, middle and late in the session: when the art review's screenshots are taken."""
    return [round(session_seconds * f, 1) for f in (0.15, 0.5, 0.85)]


def playtest(
    build_dir: Path,
    checks: list[Check],
    session_seconds: int,
    *,
    fps: int = 30,
    capture_at: list[float] = (),
) -> dict:
    """Run the build and return the report (also the repair input for the Programmer). With
    `capture_at`, the report has "screenshots": [{"t", "png" (bytes)}]; pop them before saving
    it as JSON."""
    errors: list[str] = []
    ended, t, counts, state = "timeout", 0.0, {}, {}
    limit = session_seconds + GRACE_SECONDS
    pending, shots = sorted(capture_at), []
    with serve(Path(build_dir)) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 320, "height": 180})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{url}/index.html?autoplay=1&headless=1&god=1")
            try:
                page.wait_for_function("window.sf && window.sf.ready === true", timeout=30000)
            except PlaywrightError:
                ended = "no-boot"

            def shot() -> None:
                url = page.evaluate(f"window.sfShot({fps})")
                shots.append({"t": round(t, 1), "png": base64.b64decode(url.split(",", 1)[1])})

            while ended != "no-boot" and t < limit:
                before = t
                chunk = min(CHUNK_SECONDS, limit - t)
                if pending:
                    chunk = min(chunk, max(pending[0] - t, 1 / fps))
                try:
                    result = page.evaluate(f"window.sfRun({chunk}, {fps})")
                except PlaywrightError as exc:  # e.g. sfRun missing because sf.js broke
                    errors.append(str(exc).splitlines()[0])
                    ended = "error"
                    break
                t = float(result["t"])
                if pending and t >= pending[0] - 0.5 / fps:
                    pending.pop(0)
                    shot()
                counts = page.evaluate("window.sf.counts")
                if "win" in counts or "lose" in counts:
                    ended = "win" if "win" in counts else "lose"
                    break
                if result["errors"]:
                    ended = "error"
                    break
                if t <= before:  # the game loop is not advancing
                    ended = "stalled"
                    break
            if ended != "no-boot":
                if pending and ended != "error":  # ended before a capture time: its last frame
                    shot()
                counts = page.evaluate("window.sf.counts")
                state = page.evaluate("window.sf.state")
            errors += page.evaluate("window.sf ? window.sf.errors : []")
        finally:
            browser.close()
    results = evaluate_checks(checks, counts)
    unique_errors = list(dict.fromkeys(errors))[:20]
    return {
        "passed": not unique_errors
        and ended not in {"no-boot", "stalled"}
        and all(r["ok"] for r in results),
        "ended": ended,
        "game_seconds": round(t, 1),
        "session_seconds": session_seconds,
        "errors": unique_errors,
        "checks": results,
        "events": {name: c["count"] for name, c in sorted(counts.items())},
        "state": state,
        **({"screenshots": shots} if capture_at else {}),
    }


def failure_summary(report: dict) -> str:
    """Plain-text failures for the Programmer's repair prompt."""
    lines = []
    if report["ended"] == "no-boot":
        lines.append("The game never finished booting (sf.ready stayed false within 30 s).")
    if report["ended"] == "stalled":
        lines.append("The game loop stopped advancing.")
    lines += [f"Runtime error: {e}" for e in report["errors"]]
    for r in report["checks"]:
        if not r["ok"]:
            when = f" by {r['by_second']:g} s" if r["by_second"] else ""
            lines.append(
                f"Check '{r['id']}' failed: event '{r['event']}' happened {r['count']} times"
                f"{when}, needed {r['min_count']} ({r['description']})."
            )
    lines.append(
        f"The bot (god mode) played {report['game_seconds']} of {report['session_seconds']} "
        f"game seconds; ended: {report['ended']}; events seen: {report['events']}."
    )
    return "\n".join(lines)
