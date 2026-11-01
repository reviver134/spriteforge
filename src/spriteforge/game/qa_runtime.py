"""SpriteForge QA test runtime: plays one QA test against a game build with Playwright.

A QA test is data (JSON): a name, `god` and a list of steps. Steps are interpreted here, never
executed: `hold` and `press` send real key presses (Playwright keyboard), the game is stepped
in fixed frames of game time (`sfRun` on the build's SDK, logic only), and `expect_*` steps read
`sf.counts`, `sf.state` and `sf.errors`. This file imports only the standard library and
Playwright: the harness runs it, and the exporter copies it word for word into each build's
`tests/test_game_qa.py`, so the user runs exactly what the harness ran.

Event counts are since the test started (every test opens a fresh page). A pressed key stays
down for a few frames, because games read keys once per frame. The game keeps running after
`win` or `lose`, so a test can press Enter and check the restart.
"""

import base64
import json
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# QA key names -> Playwright key names.
KEYS = {
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "w": "KeyW",
    "a": "KeyA",
    "s": "KeyS",
    "d": "KeyD",
    "space": "Space",
    "z": "KeyZ",
    "x": "KeyX",
    "j": "KeyJ",
    "k": "KeyK",
    "shift": "Shift",
    "enter": "Enter",
    "escape": "Escape",
    **{str(n): f"Digit{n}" for n in range(1, 10)},
}
OPS = ("==", "!=", "<", "<=", ">", ">=")
MAX_TEST_SECONDS = 120  # game seconds a test may run
CHUNK = 1.0  # game seconds per browser call while holding or waiting
PRESS_FRAMES = 3  # frames a pressed key stays down
MENU_WAIT = 3.0  # game seconds `choose` waits for a menu to open


def describe_step(step: dict) -> str:
    """A step in words: "hold right+space 2 s", "expect event lose >= 1 within 60 s"."""
    do = step["do"]
    if do == "hold":
        return f"hold {'+'.join(step['buttons'])} {step['seconds']:g} s"
    if do == "press":
        return f"press {step['buttons'][0]}"
    if do == "wait":
        return f"wait {step['seconds']:g} s"
    if do == "choose":
        return f"choose option {step['index'] + 1} of the open menu"
    if do == "expect_event":
        within = f" within {step['within_seconds']:g} s" if step["within_seconds"] else " now"
        return f"expect event {step['event']} >= {step['min_count']}{within}"
    if do == "expect_state":
        return f"expect state {step['path']} {step['op']} {step['value']}"
    if do == "expect_no_errors":
        return "expect no errors"
    return f"screenshot {step['label']}"


def state_value(state, path: str):
    """The value at a dotted path of sf.state, or KeyError."""
    value = state
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            raise KeyError(path)
        value = value[part]
    return value


def literal(text: str):
    """A step's expected value: a number, true/false, or text."""
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return float(text)
    except ValueError:
        return text.strip()


def compare(actual, op: str, expected) -> bool | None:
    """None when the two cannot be compared with `op`."""
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    if number(actual) and number(expected):
        a, b = float(actual), float(expected)
    elif op in ("==", "!="):
        a, b = actual, expected
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.lower(), b.lower()
    else:
        return None
    return {"==": a == b, "!=": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


class Game:
    """One open build in one page: steps game time, presses keys, reads the SDK."""

    def __init__(self, page, url: str, god: bool, fps: int):
        self.page, self.fps, self.errors = page, fps, []
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        page.goto(f"{url}/index.html?headless=1" + ("&god=1" if god else ""))
        page.wait_for_function("window.sf && window.sf.ready === true", timeout=30000)
        self.start = self.now()

    def now(self) -> float:
        return float(self.page.evaluate("window.sf.clock.now"))

    def elapsed(self) -> float:
        return self.now() - self.start

    def run(self, seconds: float) -> None:
        while seconds > 1e-9:
            chunk = min(CHUNK, seconds)
            self.page.evaluate("([s, f]) => window.sfRun(s, f, true)", [chunk, self.fps])
            seconds -= chunk

    def count(self, event: str) -> int:
        return int(self.page.evaluate("(n) => (window.sf.counts[n] || {count: 0}).count", event))

    def state(self) -> dict:
        return self.page.evaluate("window.sf.state") or {}

    def menu(self):
        return self.page.evaluate("window.sf.menu")

    def all_errors(self) -> list:
        return list(dict.fromkeys(self.errors + list(self.page.evaluate("window.sf.errors"))))

    def press(self, key: str) -> None:
        self.page.keyboard.down(KEYS[key])
        self.run(PRESS_FRAMES / self.fps)
        self.page.keyboard.up(KEYS[key])
        self.run(1 / self.fps)

    def shot(self) -> bytes:
        url = self.page.evaluate("(f) => window.sfShot(f)", self.fps)
        return base64.b64decode(url.split(",", 1)[1])


def do_step(game: Game, step: dict, start_state: dict, shots: list) -> str | None:
    """Run one step; the reason it failed, or None."""
    do = step["do"]
    if do == "hold":
        for key in step["buttons"]:
            game.page.keyboard.down(KEYS[key])
        try:
            game.run(step["seconds"])
        finally:
            for key in step["buttons"]:
                game.page.keyboard.up(KEYS[key])
        return None
    if do == "press":
        game.press(step["buttons"][0])
        return None
    if do == "wait":
        game.run(step["seconds"])
        return None
    if do == "choose":
        waited = 0.0
        while game.menu() is None and waited < MENU_WAIT:
            game.run(0.1)
            waited += 0.1
        menu = game.menu()
        if menu is None:
            return f"no menu (sf.choose) opened within {MENU_WAIT:g} s"
        if step["index"] >= len(menu["options"]):
            return f"the menu '{menu['title']}' has only {len(menu['options'])} options"
        before = game.count("choice")
        game.press(str(step["index"] + 1))
        if game.count("choice") <= before:
            return f"pressing {step['index'] + 1} did not pick an option of '{menu['title']}'"
        return None
    if do == "expect_event":
        waited, within = 0.0, step["within_seconds"]
        while True:
            count = game.count(step["event"])
            if count >= step["min_count"]:
                return None
            if waited >= within - 1e-9:
                return (
                    f"event '{step['event']}' happened {count} times"
                    + (f" in {within:g} s of waiting" if within else "")
                    + f", expected at least {step['min_count']}"
                )
            chunk = min(CHUNK, within - waited)
            game.run(chunk)
            waited += chunk
    if do == "expect_state":
        state = game.state()
        try:
            actual = state_value(state, step["path"])
        except KeyError:
            return f"sf.state has no '{step['path']}' (sf.state: {json.dumps(state)[:200]})"
        if step["value"].strip() == "@start":
            try:
                expected = state_value(start_state, step["path"])
            except KeyError:
                return f"sf.state had no '{step['path']}' when the test started"
        else:
            expected = literal(step["value"])
        ok = compare(actual, step["op"], expected)
        if ok is None:
            return f"cannot compare {step['path']} = {actual!r} with {step['op']} {expected!r}"
        if not ok:
            return f"{step['path']} is {actual!r}, expected {step['op']} {expected!r}"
        return None
    if do == "expect_no_errors":
        errors = game.all_errors()
        return f"runtime errors: {' | '.join(map(str, errors[:3]))}" if errors else None
    shots.append({"label": step["label"], "png": game.shot()})
    return None


def run_test(page, url: str, test: dict, fps: int = 30) -> dict:
    """Play one QA test in `page` against the build served at `url`. Returns {name, passed,
    failed_step (1-based or None), failure, steps [{step, ok, detail}], state, events,
    errors, game_seconds, screenshots [{label, png bytes}]}."""
    result = {"name": test["name"], "passed": False, "failed_step": None, "failure": "",
              "steps": [], "state": {}, "events": {}, "errors": [], "game_seconds": 0.0,
              "screenshots": []}  # fmt: skip
    try:
        game = Game(page, url, bool(test.get("god")), fps)
    except Exception as exc:  # noqa: BLE001 -- a build that does not boot fails the test
        result["failure"] = f"the game did not boot: {str(exc).splitlines()[0][:200]}"
        return result
    start_state = game.state()
    for n, step in enumerate(test["steps"], 1):
        known = len(game.all_errors())
        try:
            why = do_step(game, step, start_state, result["screenshots"])
        except Exception as exc:  # noqa: BLE001 -- a browser error fails this step
            why = f"the browser call failed: {str(exc).splitlines()[0][:200]}"
        errors = game.all_errors()
        if why is None and len(errors) > known and step["do"] != "expect_no_errors":
            why = f"runtime error: {errors[known]}"
        if why is None and game.elapsed() > MAX_TEST_SECONDS + 1:
            why = f"the test ran past {MAX_TEST_SECONDS} game seconds"
        result["steps"].append(
            {"step": describe_step(step), "ok": why is None, "detail": why or ""}
        )
        if why is not None:
            result["failed_step"], result["failure"] = n, why
            try:
                result["screenshots"].append({"label": f"failed at step {n}", "png": game.shot()})
            except Exception:  # noqa: BLE001, S110 -- a picture is a bonus
                pass
            break
    else:
        result["passed"] = True
    try:
        result["state"] = game.state()
        result["events"] = {
            k: v["count"] for k, v in game.page.evaluate("window.sf.counts").items()
        }
        result["errors"] = game.all_errors()[:10]
        result["game_seconds"] = round(game.elapsed(), 1)
    except Exception:  # noqa: BLE001, S110 -- the page may be gone after a crash
        pass
    return result


class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@contextmanager
def serve(directory):
    """Serve a build folder on 127.0.0.1 (random port) for the browser."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(directory)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
