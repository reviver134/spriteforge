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
from .record import READY, serve, wait_until

CHUNK_SECONDS = 30  # game seconds per browser call (keeps each evaluate short)
GRACE_SECONDS = 30  # the session may end a little after its nominal length
HUD_AT = 3.0  # game seconds: the first look at the HUD (then after every browser call)


def look_at_hud(page, seen: dict[str, str]) -> None:
    """Add what the SDK's HUD check finds now (texts over each other or off the screen) to
    `seen`, by place, so a counter that changes is one problem. A build made before the check
    has no `sf.hudProblems` and is not looked at."""
    try:
        found = page.evaluate("window.sf && window.sf.hudProblems ? window.sf.hudProblems() : []")
    except PlaywrightError:
        return
    for f in found or []:
        seen.setdefault(f["key"], f["message"])


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
    hud: bool = True,
) -> dict:
    """Run the build and return the report (also the repair input for the Programmer). With
    `capture_at`, the report has "screenshots": [{"t", "png" (bytes)}]; pop them before saving
    it as JSON."""
    errors: list[str] = []
    hud_seen: dict[str, str] = {}
    ended, t, counts, state, sounds = "timeout", 0.0, {}, {}, {}
    limit = session_seconds + GRACE_SECONDS
    pending, shots = sorted(capture_at), []
    with serve(Path(build_dir)) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 320, "height": 180})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{url}/index.html?autoplay=1&headless=1&god=1")
            if not wait_until(page, READY):
                ended = "no-boot"

            def shot() -> None:
                url = page.evaluate(f"window.sfShot({fps})")
                shots.append({"t": round(t, 1), "png": base64.b64decode(url.split(",", 1)[1])})

            while ended != "no-boot" and t < limit:
                before = t
                chunk = min(CHUNK_SECONDS, limit - t)
                if pending:
                    chunk = min(chunk, max(pending[0] - t, 1 / fps))
                if hud and t < HUD_AT - 0.5 / fps:
                    chunk = min(chunk, HUD_AT - t)
                try:
                    result = page.evaluate(f"window.sfRun({chunk}, {fps})")
                except PlaywrightError as exc:  # e.g. sfRun missing because sf.js broke
                    errors.append(str(exc).splitlines()[0])
                    ended = "error"
                    break
                t = float(result["t"])
                if hud:
                    look_at_hud(page, hud_seen)
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
                sounds = page.evaluate("window.sf.soundCount || {}")
            errors += page.evaluate("window.sf ? window.sf.errors : []")
        finally:
            browser.close()
    results = evaluate_checks(checks, counts)
    unique_errors = list(dict.fromkeys(errors))[:20]
    return {
        "passed": not unique_errors
        and not hud_seen
        and ended not in {"no-boot", "stalled"}
        and all(r["ok"] for r in results),
        "ended": ended,
        "game_seconds": round(t, 1),
        "session_seconds": session_seconds,
        "errors": unique_errors,
        "hud": list(hud_seen.values()),
        "checks": results,
        "events": {name: c["count"] for name, c in sorted(counts.items())},
        "state": state,
        "sounds": sounds,  # how often each sound played (sfsound.js counts them, silently)
        **({"screenshots": shots} if capture_at else {}),
    }


LEVEL_SECONDS = 20  # game seconds per level, plus STEP_SECONDS per step of the bot's route
STEP_SECONDS = 6


def merge_counts(per_level: list[dict]) -> dict:
    """Event counts of every level's run together (times stay per level)."""
    out: dict[str, dict] = {}
    for counts in per_level:
        for name, c in counts.items():
            m = out.setdefault(name, {"count": 0, "first": c["first"], "last": c["last"], "times": []})
            m["count"] += c["count"]
            m["first"], m["last"] = min(m["first"], c["first"]), max(m["last"], c["last"])
            m["times"] = (m["times"] + c.get("times", []))[:300]
    return out


def playtest_levels(build_dir: Path, checks: list[Check], levels: list[dict], *, fps: int = 30,
                    capture: bool = False, hud: bool = True) -> dict:  # fmt: skip
    """Play each level on its own (`?level=n&only=1`) with the SDK's route bot in god mode and
    check the design's events over all levels. `levels`: [{"n", "name", "events", "steps"}]
    (the events each level makes happen). With `capture`, one drawn screenshot per level a few
    seconds in (for the art review)."""
    errors: list[str] = []
    hud_seen: dict[str, str] = {}
    results, shots, counts_by_level, state, sounds = [], [], [], {}, {}
    with serve(Path(build_dir)) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for lv in levels:
                page = browser.new_page(viewport={"width": 320, "height": 180})
                mine: list[str] = []
                page.on("pageerror", lambda e, mine=mine: mine.append(str(e)))
                page.goto(f"{url}/index.html?autoplay=1&headless=1&god=1&only=1&level={lv['n']}")
                ended, t, counts, bot = "timeout", 0.0, {}, {}
                limit = LEVEL_SECONDS + STEP_SECONDS * lv.get("steps", 10)
                if not wait_until(page, READY):
                    ended = "no-boot"
                shot_at = 4.0 if capture else None
                while ended != "no-boot" and t < limit:
                    before = t
                    chunk = min(CHUNK_SECONDS, limit - t)
                    if shot_at is not None:
                        chunk = min(chunk, max(shot_at - t, 1 / fps))
                    if hud and t < HUD_AT - 0.5 / fps:
                        chunk = min(chunk, HUD_AT - t)
                    try:
                        result = page.evaluate(f"window.sfRun({chunk}, {fps})")
                    except PlaywrightError as exc:
                        mine.append(str(exc).splitlines()[0])
                        ended = "error"
                        break
                    t = float(result["t"])
                    if hud:
                        look_at_hud(page, hud_seen)
                    if shot_at is not None and t >= shot_at - 0.5 / fps:
                        shot_at = None
                        data = page.evaluate(f"window.sfShot({fps})")
                        shots.append({"t": round(t, 1), "level": lv["n"],
                                      "png": base64.b64decode(data.split(",", 1)[1])})  # fmt: skip
                    counts = page.evaluate("window.sf.counts")
                    bot = page.evaluate("window.sf.bot") or {}
                    if "level_complete" in counts or "win" in counts:
                        ended = "cleared"
                        break
                    if result["errors"]:
                        ended = "error"
                        break
                    if bot.get("status") == "stuck":
                        ended = "stuck"
                        break
                    if t <= before:
                        ended = "stalled"
                        break
                if ended != "no-boot":
                    counts = page.evaluate("window.sf.counts")
                    state = page.evaluate("window.sf.state")
                    bot = page.evaluate("window.sf.bot") or bot
                    for name, k in (page.evaluate("window.sf.soundCount || {}") or {}).items():
                        sounds[name] = sounds.get(name, 0) + k
                mine += page.evaluate("window.sf ? window.sf.errors : []")
                page.close()
                mine = list(dict.fromkeys(mine))[:10]
                errors += [f"level {lv['n']}: {e}" for e in mine]
                events = {name: c["count"] for name, c in sorted(counts.items())}
                missing = [e for e in lv.get("events", []) if e not in counts]
                counts_by_level.append(counts)
                results.append({"n": lv["n"], "name": lv.get("name", ""), "cleared": ended == "cleared",
                                "ended": ended, "game_seconds": round(t, 1), "bot": bot,
                                "events": events, "expected": lv.get("events", []),
                                "missing": missing, "errors": mine,
                                "ok": ended == "cleared" and not missing and not mine})  # fmt: skip
        finally:
            browser.close()
    merged = merge_counts(counts_by_level)
    checked = evaluate_checks(checks, merged)
    unique_errors = list(dict.fromkeys(errors))[:20]
    ok = (all(r["ok"] for r in results) and all(c["ok"] for c in checked) and not unique_errors
          and not hud_seen)  # fmt: skip
    worst = next((r["ended"] for r in results if not r["cleared"]), "cleared")
    return {
        "passed": ok and bool(results),
        "ended": worst if results else "no-boot",
        "game_seconds": round(sum(r["game_seconds"] for r in results), 1),
        "session_seconds": sum(LEVEL_SECONDS + STEP_SECONDS * lv.get("steps", 10) for lv in levels),
        "errors": unique_errors,
        "hud": list(hud_seen.values()),
        "checks": checked,
        "events": {name: c["count"] for name, c in sorted(merged.items())},
        "state": state,
        "sounds": sounds,
        "levels": results,
        **({"screenshots": shots} if capture else {}),
    }


def level_failures(report: dict) -> list[str]:
    lines = []
    for r in report.get("levels", []):
        bot = r.get("bot") or {}
        if not r["cleared"]:
            why = {"stuck": f"the bot got stuck: {bot.get('detail') or '?'}",
                   "timeout": f"time ran out at step {bot.get('step', '?')} of {bot.get('steps', '?')}",
                   "no-boot": "the level never finished booting", "error": "a runtime error stopped it",
                   "stalled": "the game loop stopped advancing"}.get(r["ended"], r["ended"])  # fmt: skip
            lines.append(f"Level {r['n']} ({r['name']}) was not cleared: {why}. Events seen: {r['events']}.")
        if r.get("missing"):
            lines.append(f"Level {r['n']} never emitted {', '.join(r['missing'])} (the level makes "
                         "these happen on the bot's way to the exit: check the touch hook and the "
                         "sf.event calls).")  # fmt: skip
    return lines


def failure_summary(report: dict) -> str:
    """Plain-text failures for the Programmer's repair prompt."""
    lines = level_failures(report)
    if report["ended"] == "no-boot":
        lines.append("The game never finished booting (sf.ready stayed false within 30 s).")
    if report["ended"] == "stalled":
        lines.append("The game loop stopped advancing.")
    lines += [f"Runtime error: {e}" for e in report["errors"]]
    if report.get("hud"):
        lines += report["hud"][:8]
        if len(report["hud"]) > 8:
            lines.append(f"... and {len(report['hud']) - 8} more HUD problems of the same kind.")
        lines.append(
            "Fix the HUD: sf.text's x and y are screen pixels, but its size is in design pixels "
            "(the SDK multiplies it by sf.assets.scale, so size 8 is 48 pixels tall on a "
            "1920x1080 screen). Use size 8-12, put each line at least size * sf.assets.scale * "
            "1.3 pixels below the one above, and keep every line inside the screen."
        )
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
