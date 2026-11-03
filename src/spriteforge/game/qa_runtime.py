"""SpriteForge QA test runtime: plays one QA test against a game build with Playwright.

A QA test is data (JSON): a name, `god` and a list of steps. Steps are interpreted here, never
executed: `hold` and `press` send real key presses (Playwright keyboard), the game is stepped
in fixed frames of game time (`sfRun` on the build's SDK, logic only), and `expect_*` steps read
`sf.counts`, `sf.state` and `sf.errors`. This file imports only the standard library and
Playwright: the harness runs it, and the exporter copies it word for word into each build's
`tests/test_game_qa.py`, so the user runs exactly what the harness ran.

Event counts are since the test started (every test opens a fresh page; a `reload` step
starts them again). A pressed key stays down for a few frames, because games read keys once
per frame. The game keeps running after `win` or `lose`, so a test can press Enter and check
the restart.

A build with menus (L2.5) opens on its title screen; a test starts in play (`?play=1`) unless
its `start` is "title"; a test of one level (L2.6, `level` n > 1) opens `?level=n`. Every test starts with no saved progress (`?reset=1`); `reload` opens
the page again on the title with the progress kept. `expect_menu` waits for a screen of the
SDK's menus (sf.menu.screen) and `select` moves the highlight to an item with real arrow keys
and picks it with Enter.
"""

import base64
import json
import time
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
MENU_WAIT = 3.0  # game seconds `choose`, `expect_menu` and `select` wait for a menu to open


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
    if do == "expect_menu":
        return f"expect the {step['menu']} menu"
    if do == "select":
        return f"select '{step['menu']}' in the open menu"
    if do == "reload":
        return "reload the page (progress kept)"
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


def booted(page, timeout: float = 30) -> bool:
    """Poll sf.ready with page.evaluate (not page.wait_for_function, whose polling a
    build's CSP blocks with an EvalError when the game is not ready at the first check)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            if page.evaluate("window.sf && window.sf.ready === true"):
                return True
        except Exception:  # noqa: BLE001, S110 -- the page is still navigating
            pass
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(50)


class Game:
    """One open build in one page: steps game time, presses keys, reads the SDK."""

    def __init__(self, page, url: str, god: bool, fps: int, start: str = "play", level: int = 1):
        self.page, self.fps, self.errors, self.url, self.god = page, fps, [], url, god
        page.on("pageerror", lambda e: self.errors.append(str(e)))
        play = "&play=1" if start != "title" else ""
        if start != "title" and int(level) > 1:
            play += f"&level={int(level)}"
        self.open(f"{url}/index.html?headless=1&reset=1{play}" + ("&god=1" if god else ""))
        self.start = self.now()
        self.offset = 0.0  # game time before the last reload

    def open(self, address: str) -> None:
        self.page.goto(address)
        if not booted(self.page):
            raise RuntimeError("the game never finished booting (sf.ready stayed false within 30 s)")

    def reload(self) -> None:
        """The page again, on its title screen, with the saved progress kept."""
        self.offset += self.elapsed()
        self.open(f"{self.url}/index.html?headless=1" + ("&god=1" if self.god else ""))
        self.start = self.now()

    def now(self) -> float:
        return float(self.page.evaluate("window.sf.clock.now"))

    def elapsed(self) -> float:
        return self.now() - self.start

    def total(self) -> float:
        """Game time since the test began, over reloads."""
        return getattr(self, "offset", 0.0) + self.elapsed()

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

    def screen_menu(self, wait: float):
        """The open screen of the SDK's menus (waiting up to `wait` game seconds), or None.
        Game time may stand still under a menu, so the wait counts frames stepped."""
        waited = 0.0
        while True:
            menu = self.menu()
            if menu and menu.get("screen"):
                return menu
            if waited >= wait - 1e-9:
                return None
            self.run(0.1)
            waited += 0.1

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
    if do == "expect_menu":
        want, waited = step["menu"], 0.0
        while True:
            menu = game.menu() or {}
            if menu.get("screen") == want:
                return None
            if waited >= MENU_WAIT - 1e-9:
                shown = menu.get("screen") or ("an sf.choose menu" if menu else "no menu")
                return f"the {want} menu did not open within {MENU_WAIT:g} s ({shown} is showing)"
            game.run(0.1)
            waited += 0.1
    if do == "select":
        menu = game.screen_menu(MENU_WAIT)
        if menu is None:
            return f"no menu screen opened within {MENU_WAIT:g} s"
        want = step["menu"].strip().lower()
        labels = [str(o).lower() for o in menu["options"]]
        actions = [str(a).lower() for a in menu.get("actions") or []]
        found = next((i for i, o in enumerate(labels) if o == want), None)
        if found is None:  # the item's action ('start', 'resume'): survives a new label
            found = next((i for i, a in enumerate(actions) if a == want.replace(" ", "_")), None)
        if found is None:
            found = next((i for i, o in enumerate(labels) if o.startswith(want) or want in o), None)
        if found is None:
            return f"the {menu['screen']} menu has no item '{step['menu']}' (items: {', '.join(menu['options'])})"
        for _ in range(len(labels) + 1):  # real arrow keys move the highlight
            index = int((game.menu() or {}).get("index", 0))
            if index == found:
                break
            game.press("down" if (found - index) % len(labels) <= len(labels) // 2 else "up")
        if int((game.menu() or {}).get("index", -1)) != found:
            return f"the arrow keys did not move the highlight to '{menu['options'][found]}'"
        before = game.count("menu_select")
        game.press("enter")
        if game.count("menu_select") <= before:
            return f"Enter did not pick '{menu['options'][found]}' (it may be locked)"
        return None
    if do == "reload":
        game.reload()
        return None
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
        game = Game(page, url, bool(test.get("god")), fps, test.get("start") or "play",
                    test.get("level") or 1)
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
        if why is None and game.total() > MAX_TEST_SECONDS + 1:
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
        result["game_seconds"] = round(game.total(), 1)
    except Exception:  # noqa: BLE001, S110 -- the page may be gone after a crash
        pass
    return result


# --- code checks (L2.8): the QA Playtester's own checks of this game, as code ------------------
#
# A check is a JavaScript function body the QA Playtester wrote for this game's scenarios. It
# runs inside the game's page (the browser is the sandbox: the build's CSP allows no network,
# and the harness checked the code for forbidden calls before it ran) with one helper, `qa`:
# game time (step, hold, press with key events the game reads like real keys), what the game
# shows (scene, sprites, camera, sf.state, events, errors) and `qa.expect(ok, message)`. A
# failed expect is a game failure; any other exception is a broken check (the QA
# Playtester's mistake, never the game's). The whole check has a wall-clock limit.

CHECK_SECONDS = 90  # game seconds a check may step
CHECK_WALL_MS = 60000  # wall-clock limit of one check (the browser stops the script)

CHECK_PRELUDE = r"""
(() => {
  const sf = window.sf, FPS = __FPS__, LIMIT = __LIMIT__;
  class QAFail extends Error {}
  const KEYS = {left: ['ArrowLeft', 37], right: ['ArrowRight', 39], up: ['ArrowUp', 38],
    down: ['ArrowDown', 40], w: ['KeyW', 87, 'w'], a: ['KeyA', 65, 'a'], s: ['KeyS', 83, 's'],
    d: ['KeyD', 68, 'd'], space: ['Space', 32, ' '], z: ['KeyZ', 90, 'z'], x: ['KeyX', 88, 'x'],
    c: ['KeyC', 67, 'c'], j: ['KeyJ', 74, 'j'], k: ['KeyK', 75, 'k'], shift: ['ShiftLeft', 16, 'Shift'],
    enter: ['Enter', 13], escape: ['Escape', 27]};
  let used = 0;
  const logs = [], held = new Set();
  const key = (type, name) => {
    const k = KEYS[String(name).toLowerCase()];
    if (!k) throw new QAFail('qa: unknown key ' + name + ' (keys: ' + Object.keys(KEYS).join(', ') + ')');
    const e = new KeyboardEvent(type, {key: k[2] || k[0], code: k[0], bubbles: true, cancelable: true});
    Object.defineProperty(e, 'keyCode', {get: () => k[1]});
    Object.defineProperty(e, 'which', {get: () => k[1]});
    window.dispatchEvent(e);
  };
  const gameScenes = () => (sf.game ? sf.game.scene.getScenes(true) : [])
    .filter(s => !String(s.sys.settings.key).startsWith('sf-'));
  const view = o => ({key: o.texture ? o.texture.key : '', x: o.x, y: o.y, visible: o.visible,
    alpha: o.alpha, active: o.active, w: o.displayWidth, h: o.displayHeight,
    vx: o.body ? o.body.velocity.x : 0, vy: o.body ? o.body.velocity.y : 0, flipX: !!o.flipX,
    rotation: o.rotation || 0, obj: o});
  const qa = {
    fps: FPS,
    step(seconds) {
      seconds = Math.max(0, +seconds || 0);
      if (used + seconds > LIMIT + 1e-6) throw new QAFail('the check ran past ' + LIMIT + ' game seconds');
      used += seconds;
      window.sfRun(seconds, FPS, true);
    },
    frames(n) { qa.step((+n || 1) / FPS); },
    down(name) { if (!held.has(name)) { held.add(name); key('keydown', name); } },
    up(name) { if (held.has(name)) { held.delete(name); key('keyup', name); } },
    hold(names, seconds) {
      names = [].concat(names);
      names.forEach(qa.down);
      try { qa.step(seconds); } finally { names.forEach(qa.up); }
    },
    press(name) { qa.hold([name], 3 / FPS); qa.step(1 / FPS); },
    scene: () => gameScenes()[0] || null,
    scenes: gameScenes,
    state: () => sf.state || {},
    count: name => (sf.counts[name] || {count: 0}).count,
    events: () => sf.events.slice(),
    errors: () => sf.errors.slice(),
    time: () => sf.clock.now,
    level: () => (sf.level && sf.level.current) || null,
    camera() {
      const s = qa.scene(), c = s && s.cameras.main.worldView;
      return c ? {x: c.x, y: c.y, width: c.width, height: c.height} : null;
    },
    onScreen(o, margin) {
      const c = qa.camera(), m = margin || 0;
      return !!c && o.x >= c.x - m && o.x <= c.x + c.width + m && o.y >= c.y - m && o.y <= c.y + c.height + m;
    },
    // Every live sprite or image of the game's scenes, optionally of one texture (a string or
    // a RegExp): {key, x, y, visible, alpha, active, w, h, vx, vy, flipX, rotation, obj}.
    sprites(texture) {
      const out = [];
      for (const s of gameScenes()) {
        for (const o of s.children.list) {
          if (!o.active || !o.texture || o.type === 'Text') continue;
          const k = o.texture.key;
          if (texture == null || (texture instanceof RegExp ? texture.test(k) : k === texture)) out.push(view(o));
        }
      }
      return out;
    },
    near(a, b, distance) { return Math.hypot(a.x - b.x, a.y - b.y) <= distance; },
    expect(ok, message) { if (!ok) throw new QAFail(String(message || 'an expectation failed')); },
    log(...parts) { if (logs.length < 40) logs.push(parts.map(p => typeof p === 'object' ? JSON.stringify(p) : String(p)).join(' ').slice(0, 300)); },
  };
  const before = sf.errors.length;
  try {
    (function (qa, sf) {
__CODE__
    })(qa, sf);
    return {ok: true, logs, used, gameErrors: sf.errors.slice(before, before + 3)};
  } catch (e) {
    return {ok: false, assertion: e instanceof QAFail, message: String((e && e.message) || e).slice(0, 400),
            where: String((e && e.stack) || '').split('\n').slice(1, 3).join(' | ').slice(0, 300),
            logs, used, gameErrors: sf.errors.slice(before, before + 3)};
  } finally {
    held.forEach(name => key('keyup', name));
  }
})()
"""


def check_expression(code: str, fps: int, limit: float = CHECK_SECONDS) -> str:
    return (CHECK_PRELUDE.replace("__FPS__", str(int(fps)))
            .replace("__LIMIT__", repr(float(limit))).replace("__CODE__", code))  # fmt: skip


def evaluate_check(page, code: str, fps: int) -> dict:
    """The check's outcome; the browser itself stops a check that runs past CHECK_WALL_MS (the
    DevTools protocol's evaluate timeout), and that is a broken check."""
    cdp = page.context.new_cdp_session(page)
    try:
        answer = cdp.send("Runtime.evaluate", {"expression": check_expression(code, fps),
                                               "returnByValue": True, "timeout": CHECK_WALL_MS})  # fmt: skip
    finally:
        try:
            cdp.detach()
        except Exception:  # noqa: BLE001, S110 -- the page may be gone
            pass
    if "exceptionDetails" in answer:
        detail = answer["exceptionDetails"]
        text = (detail.get("exception") or {}).get("description") or detail.get("text") or "?"
        return {"ok": False, "assertion": False, "message": str(text).splitlines()[0][:300],
                "where": "", "logs": [], "used": 0, "gameErrors": []}  # fmt: skip
    return (answer.get("result") or {}).get("value") or {
        "ok": False, "assertion": False, "message": "the check returned nothing", "where": "",
        "logs": [], "used": 0, "gameErrors": []}  # fmt: skip


def run_check(page, url: str, check: dict, fps: int = 30) -> dict:
    """Run one code check against the build at `url`. The result has run_test's shape, plus
    "kind": "check" and "broken" (the check itself failed: not the game's fault)."""
    result = {"name": check["name"], "passed": False, "failed_step": None, "failure": "",
              "steps": [], "state": {}, "events": {}, "errors": [], "game_seconds": 0.0,
              "screenshots": [], "kind": "check", "broken": False, "logs": []}  # fmt: skip
    try:
        game = Game(page, url, bool(check.get("god")), fps, check.get("start") or "play",
                    check.get("level") or 1)
    except Exception as exc:  # noqa: BLE001 -- a build that does not boot fails the check
        result["failure"] = f"the game did not boot: {str(exc).splitlines()[0][:200]}"
        return result
    try:
        out = evaluate_check(page, check["code"], fps)
    except Exception as exc:  # noqa: BLE001 -- the browser call itself failed
        out = {"ok": False, "assertion": False, "message": str(exc).splitlines()[0][:300],
               "where": "", "logs": [], "used": 0, "gameErrors": []}  # fmt: skip
    game_errors = [e for e in out.get("gameErrors") or [] if e]
    if out.get("ok") and not game_errors:
        result["passed"] = True
    elif out.get("ok") or out.get("assertion"):
        result["failure"] = (out.get("message") if not out.get("ok") else
                             f"runtime error in the game: {game_errors[0]}")  # fmt: skip
    else:
        result["broken"] = True
        result["failure"] = f"the check itself failed: {out.get('message')}" + (
            f" (at {out['where']})" if out.get("where") else "")
    result["steps"] = [{"step": "code check", "ok": result["passed"], "detail": result["failure"]}]
    result["logs"] = list(out.get("logs") or [])
    try:
        result["screenshots"].append({"label": "end" if result["passed"] else "failed",
                                      "png": game.shot()})  # fmt: skip
    except Exception:  # noqa: BLE001, S110 -- a picture is a bonus
        pass
    try:
        result["state"] = game.state()
        result["events"] = {k: v["count"] for k, v in page.evaluate("window.sf.counts").items()}
        result["errors"] = game.all_errors()[:10]
        result["game_seconds"] = round(game.total(), 1)
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
