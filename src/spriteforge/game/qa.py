"""QA tests: written from the design (not the code), played with real key presses, exported.

The QA Playtester (Muse Spark) writes `QATests` from the design, its checks and controls:
movement with real input, the level-up menu, the lose path, restart after losing and the HUD
state changing. Each test is a list of steps (data, never code) that `qa_runtime` plays in a
real browser while stepping game time; the bot playtest cannot find these bugs, because the bot
never presses a key, opens a menu by hand or restarts.

Strict JSON schema mode wants every field required, so a `Step` is one flat record: `do` says
what it is and the fields it does not use stay empty (cleared on validation). Limits: 12 tests,
40 steps and 120 game seconds per test, whitelisted keys, bounded values.

`run_qa` plays a list of tests against a build; `qa_failures` is their part of the repair
prompt; `export_tests` writes `tests/test_game_qa.py` (pytest + Playwright, self-contained: the
runtime is copied in and the tests are JSON inside one string literal built with `repr`, so no
model text can become code); `merge_suite` keeps earlier versions' tests as a regression suite
minus the ones feedback dropped.
"""

import json
import re
from pathlib import Path
from typing import Literal

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright
from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

from . import qa_runtime
from .design import GameDesign
from .qa_runtime import KEYS, MAX_TEST_SECONDS, MENU_WAIT, PRESS_FRAMES, describe_step, serve

MAX_TESTS, MAX_STEPS, MAX_SUITE = 12, 40, 24
MAX_LEVELS = 8  # a game's levels (structure.MAX_LEVELS); code adds one test per level (L2.6)
StepKind = Literal[
    "hold", "press", "wait", "choose", "expect_event", "expect_state", "expect_no_errors",
    "screenshot", "expect_menu", "select", "reload",
]  # fmt: skip
Key = Literal[tuple(KEYS)]
Op = Literal["==", "!=", "<", "<=", ">", ">=", ""]
EVENT = re.compile(r"^[a-z][a-z0-9_]{0,39}$")
PATH = re.compile(r"^[A-Za-z_]\w{0,30}(\.[A-Za-z_]\w{0,30}){0,3}$")
LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _-]{0,39}$")
# Events every build has: the SDK emits boot, restart, menu and choice; games emit win/lose.
SDK_EVENTS = ("boot", "restart", "menu", "choice", "win", "lose")
# A build with menus (L2.5): the SDK's flow emits these too, and sf.state has the difficulty.
FLOW_EVENTS = ("title", "menu_open", "menu_select", "pause", "resume", "level_start",
               "level_complete", "level_failed", "game_complete")  # fmt: skip
STATE_KEYS = ("hp", "x", "y", "time", "level", "score")
MENU_TEXT = re.compile(r"^[A-Za-z0-9][\w .,:'!?&+-]{0,39}$")


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


class Step(Contract):
    do: StepKind = Field(description="what the step does (see the task)")
    buttons: list[Key] = Field(
        description="hold: 1-4 keys held together; press: exactly 1; else [] (named buttons: the "
        "artifact store blanks any field named like key)"
    )
    seconds: float = Field(description="hold, wait: game seconds (at most 60); else 0")
    index: int = Field(description="choose: 0-based option of the open menu; else 0")
    event: str = Field(description="expect_event: the event name; else ''")
    min_count: int = Field(description="expect_event: at least this many since the test began")
    within_seconds: float = Field(description="expect_event: game seconds to wait (0 = now)")
    path: str = Field(description="expect_state: dotted path in sf.state, e.g. 'hp'; else ''")
    op: Op = Field(description="expect_state: comparison; else ''")
    value: str = Field(
        description="expect_state: a number, true/false, text, or '@start' (the value when the "
        "test began); else ''"
    )
    label: str = Field(description="screenshot: a short label; else ''")
    menu: str = Field(
        description="expect_menu: the screen id (title, main, pause, settings, results, "
        "game_over, victory ...); select: the item's label as shown; else ''"
    )

    @model_validator(mode="before")
    @classmethod
    def older(cls, data):
        """Steps saved before menus existed (L2.5) have no `menu`."""
        if isinstance(data, dict) and "menu" not in data:
            data = {**data, "menu": ""}
        return data

    @model_validator(mode="after")
    def check(self):
        do, blank = self.do, {"buttons": [], "seconds": 0.0, "index": 0, "event": "",
                              "min_count": 0, "within_seconds": 0.0, "path": "", "op": "",
                              "value": "", "label": "", "menu": ""}  # fmt: skip
        used = {"hold": {"buttons", "seconds"}, "press": {"buttons"}, "wait": {"seconds"},
                "choose": {"index"}, "expect_event": {"event", "min_count", "within_seconds"},
                "expect_state": {"path", "op", "value"}, "screenshot": {"label"},
                "expect_menu": {"menu"}, "select": {"menu"}}.get(do, set())  # fmt: skip
        if do == "hold":
            need(1 <= len(self.buttons) <= 4, "hold needs 1 to 4 keys")
            need(
                0 < self.seconds <= 60,
                f"hold seconds must be above 0 and at most 60, not {self.seconds}",
            )
        elif do == "press":
            need(len(self.buttons) == 1, "press needs exactly one key")
        elif do == "wait":
            need(
                0 < self.seconds <= 60,
                f"wait seconds must be above 0 and at most 60, not {self.seconds}",
            )
        elif do == "choose":
            need(0 <= self.index <= 8, "choose index must be 0-8")
        elif do == "expect_event":
            self.event = self.event.strip().lower()
            need(bool(EVENT.match(self.event)), f"bad event name {self.event!r}")
            need(1 <= self.min_count <= 10000, "min_count must be 1-10000")
            need(0 <= self.within_seconds <= MAX_TEST_SECONDS, "within_seconds must be 0-120")
        elif do == "expect_state":
            self.path, self.value = self.path.strip(), self.value.strip()
            need(bool(PATH.match(self.path)), f"bad state path {self.path!r}")
            need(self.op != "", "expect_state needs an op")
            need(0 < len(self.value) <= 40, "expect_state needs a value (at most 40 characters)")
        elif do == "screenshot":
            self.label = self.label.strip()
            need(bool(LABEL.match(self.label)), f"bad screenshot label {self.label!r}")
        elif do in ("expect_menu", "select"):
            self.menu = self.menu.strip()
            if do == "expect_menu":
                self.menu = self.menu.lower().replace(" ", "_")
            need(bool(MENU_TEXT.match(self.menu)), f"{do} needs a menu (a screen id or an item label)")
        for name, empty in blank.items():  # what the step does not use stays empty
            if name not in used:
                setattr(self, name, empty)
        return self

    def game_seconds(self) -> float:
        """The most game time this step can take."""
        return {
            "hold": self.seconds,
            "wait": self.seconds,
            "expect_event": self.within_seconds,
            "press": (PRESS_FRAMES + 1) / 30,
            "choose": MENU_WAIT + (PRESS_FRAMES + 1) / 30,
            "expect_menu": MENU_WAIT,
            "select": MENU_WAIT + 10 * (PRESS_FRAMES + 1) / 30,
        }.get(self.do, 0.0)


class QATest(Contract):
    name: str = Field(description="short unique name, e.g. 'restart after losing'")
    purpose: str = Field(description="what it proves, from the design, in one sentence")
    god: bool = Field(
        description="true: the player cannot die (hurt, but HP stays >= 1); false for the lose "
        "path and restart after losing"
    )
    start: Literal["play", "title"] = Field(
        description="play: the test starts in play, in `level` (the title is skipped); title: "
        "on the title screen (tests of the menus)"
    )
    level: int = Field(
        description="start play: the level the test starts in (1 = the first); a game with "
        "several levels can test each one directly. Ignored with start title"
    )
    steps: list[Step]

    @model_validator(mode="before")
    @classmethod
    def older(cls, data):
        """Tests saved before menus existed (L2.5) start in play; before L2.6, in level 1."""
        if isinstance(data, dict):
            data = {"start": "play", "level": 1, **data}
        return data

    @model_validator(mode="after")
    def check(self):
        self.name, self.purpose = self.name.strip(), self.purpose.strip()[:240]
        need(0 < len(self.name) <= 60, "a test needs a name of at most 60 characters")
        need(1 <= self.level <= MAX_LEVELS, f"level must be 1-{MAX_LEVELS}, not {self.level}")
        if self.start == "title":
            self.level = 1
        need(1 <= len(self.steps) <= MAX_STEPS, f"a test has 1 to {MAX_STEPS} steps")
        total = sum(s.game_seconds() for s in self.steps)
        need(total <= MAX_TEST_SECONDS, f"test {self.name!r} needs {total:g} game seconds; "
                                        f"at most {MAX_TEST_SECONDS}")  # fmt: skip
        return self


class QATests(Contract):
    tests: list[QATest]

    @model_validator(mode="after")
    def check(self):
        need(1 <= len(self.tests) <= MAX_TESTS, f"write 1 to {MAX_TESTS} tests")
        names = [t.name.casefold() for t in self.tests]
        need(len(set(names)) == len(names), "test names must be unique")
        return self


class QASuite(Contract):
    """The tests one build runs (game/vN/qa-tests.json): its design's tests plus earlier
    versions' tests (a regression suite), minus those feedback dropped."""

    tests: list[QATest]
    # The build version each test was first run in, in the order of `tests`. (Not a dict keyed
    # by test name: the artifact store blanks dict keys that look like secrets, e.g. "keys".)
    versions: list[int]
    dropped: list[str]
    notes: list[str]

    def since(self, name: str) -> int | None:
        return next((v for t, v in zip(self.tests, self.versions, strict=True) if t.name == name),
                    None)  # fmt: skip


# --- writing the tests ----------------------------------------------------------------------


def menu_lines(menus: dict) -> str:
    """The SDK's screens and their items as a QA Playtester reads them."""
    return "\n".join(
        f"* {sid}: " + ", ".join(f"'{i['label']}' ({i['action']})" for i in screen["items"])
        for sid, screen in menus["screens"].items()
    )


def menus_part(menus: dict | None) -> str:
    if not menus:
        return ""
    levels = len(menus.get("levels", [])) or 1
    between = " and between levels 'results'" if levels > 1 else ""
    several = (
        f"\nThe game has {levels} levels (" + ", ".join(f"{lv['n']}. {lv['name']}" for lv in
        menus["levels"]) + "). A test with start \"play\" and `level` n starts in level n "
        "(sf.state.level is n); code already adds one test per level that it starts, the player "
        "moves and nothing fails, so write level tests only for what a level itself promises."
        if levels > 1 else ""
    )  # fmt: skip
    return f"""
Menus (the SDK draws and runs them; they are data, the same in every build of this game):
the game opens on its title screen. A test with start "play" skips it and starts in `level`;
start "title" begins on the title screen. Every test starts with no saved progress.
* expect_menu: the screen `menu` (a screen id below) is open, waiting up to {MENU_WAIT:g} s
* select: moves the highlight to the item whose label (or action) is `menu` with the arrow
  keys and presses Enter (waits up to {MENU_WAIT:g} s for a menu screen)
* reload: opens the page again on the title screen; saved progress (levels unlocked, best
  results, settings) is kept; event counts start again
Escape pauses play (the pause screen; game time stops) and goes back in menus (on pause it
resumes). Screens and items:
{menu_lines(menus)}
Flow events: {", ".join(FLOW_EVENTS)}. sf.state.difficulty is easy, normal or hard (the
settings item cycles normal -> hard -> easy). After 'lose' the game_over screen opens
(about a second later); after the last level 'victory'{between}.
Selecting an item emits menu_select; opening a screen emits menu_open.{several}
"""


def qa_prompt(design: GameDesign, menus: dict | None = None) -> str:
    """`menus`: the build's menus (`menus.menus_script`), when the game has them."""
    events = sorted({c.event for c in design.checks} | set(SDK_EVENTS)
                    | (set(FLOW_EVENTS) if menus else set()))  # fmt: skip
    flow = (
        "\n6. the menus: from the title (start title) to play (expect_menu title, press enter, "
        "expect_menu main, select the start item, expect level_start), pause and resume with "
        "Escape (expect_menu pause, then expect resume), and a setting that shows in sf.state "
        "(difficulty); with a way to lose, game_over opens after losing and its retry item "
        "restarts."
        if menus
        else ""
    )
    return f"""Write QA tests for this game from its design alone (you do not see the code).
A harness plays each test in a real browser: it presses real keys, steps game time exactly,
and reads what the game exposes. Each test opens a fresh game.

Steps (fields a step does not use stay empty or 0):
* hold: the keys in `buttons` held together for `seconds` of game time (at most 60)
* press: the one key in `buttons`, pressed briefly
* wait: `seconds` of game time with no keys
* choose: pick option `index` (0-based) of the open menu (sf.choose, e.g. level-up upgrades);
  it waits up to {MENU_WAIT:g} s for the menu to open
* expect_event: `event` happened at least `min_count` times since the test began, waiting up to
  `within_seconds` of game time (0 = must already have happened)
* expect_state: sf.state `path` `op` `value`; value '@start' means its value when the test began
* expect_no_errors: no runtime errors so far
* screenshot: a picture named `label`
{menus_part(menus)}
Keys: {", ".join(KEYS)}. Arrows/WASD move; Space/Z/J are the action key; X/K/Shift the second
action (follow the design's controls).

What every build exposes:
* sf.state: hp (player health), x and y (player position in pixels), time (seconds since this
  run began; a restart resets it), and level and score when the design has them.
* events: {", ".join(events)}. 'menu' is emitted when an sf.choose menu opens, 'choice' when an
  option is picked, 'restart' when the game restarts, 'win'/'lose' when a run ends.
* After win or lose, Enter restarts the game.
* god true: the player is hurt but never dies (use it in every test but the lose path).

Write 5 to 8 tests, at most {MAX_STEPS} steps and {MAX_TEST_SECONDS} game seconds each (holds +
waits + within_seconds). They must include:
1. movement with real input: hold arrow keys, then expect x or y to differ from '@start';
2. the level-up or upgrade menu if the design has one: play until 'menu' happens (hold keys that
   move the player through the action), choose an option, expect 'choice', play on, expect no
   errors;
3. the lose path: god false, the player idle (or walking into danger); expect 'lose' within the
   time the design implies (at most {MAX_TEST_SECONDS - 20} s);
4. restart after losing: god false; expect 'lose'; press enter; expect 'restart' within 3 s;
   expect time < 5; move and expect no errors;
5. the HUD state changing: e.g. time grows, hp drops after being hurt, score rises.{flow}
Test only what the design promises; do not invent numbers it does not state. Use only the
event names and sf.state keys listed above.

Design:
{design.model_dump_json(indent=1)}"""


def blank_step(**fields) -> Step:
    base = {"buttons": [], "seconds": 0, "index": 0, "event": "", "min_count": 0,
            "within_seconds": 0, "path": "", "op": "", "value": "", "label": "", "menu": ""}  # fmt: skip
    return Step(**{**base, **fields})


def label_of(menus: dict, screen: str, action: str) -> str | None:
    """The action when the screen has an item for it (`select` finds items by their action
    too, so these tests survive new labels)."""
    items = (menus["screens"].get(screen) or {}).get("items", [])
    return next((action for i in items if i["action"] == action), None)


VALUE_SUFFIX = re.compile(r"\s*[:=-]?\s*\(?\b(easy|normal|hard|on|off)\b\)?\s*$", re.IGNORECASE)


def bind_selects(tests: list[QATest], menus: dict | None) -> list[QATest]:
    """`select` steps name the item's action instead of the label it had in `menus` (the menus
    the tests were written against), so a menu revision that renames an item keeps them
    working. Live 2026-11-02: "New Adventure" renamed to "Start" failed a test, and the
    Programmer, asked to fix it, rewrote the SDK's menus from game code."""
    if not menus:
        return list(tests)
    actions = {}
    for screen in menus["screens"].values():
        for item in screen["items"]:
            actions.setdefault(item["label"].strip().lower(), item["action"])
    known = {i["action"] for s in menus["screens"].values() for i in s["items"]}
    out = []
    for test in tests:
        steps = []
        for step in test.steps:
            if step.do == "select":
                want = step.menu.strip().lower()
                bare = VALUE_SUFFIX.sub("", want)
                action = (want.replace(" ", "_") if want.replace(" ", "_") in known
                          else actions.get(want) or actions.get(bare))  # fmt: skip
                if action and action != step.menu:
                    step = step.model_copy(update={"menu": action})
            steps.append(step)
        out.append(test.model_copy(update={"steps": steps}))
    return out


def flow_tests(design: GameDesign, menus: dict) -> list[QATest]:
    """Tests of the SDK's menus and flow."""
    s = blank_step
    start, settings = label_of(menus, "main", "start"), label_of(menus, "main", "settings")
    resume = label_of(menus, "pause", "resume")
    tests = []
    if start and resume:
        tests.append(QATest(
            name="title to play, pause and resume", god=True, start="title",
            purpose="The title leads to play; Escape pauses (time stops) and Resume goes on.",
            steps=[s(do="expect_menu", menu="title"), s(do="press", buttons=["enter"]),
                   s(do="expect_menu", menu="main"), s(do="select", menu=start),
                   s(do="expect_event", event="level_start", min_count=1, within_seconds=2),
                   s(do="wait", seconds=1), s(do="press", buttons=["escape"]),
                   s(do="expect_menu", menu="pause"),
                   s(do="expect_event", event="pause", min_count=1, within_seconds=0),
                   s(do="screenshot", label="paused"), s(do="select", menu=resume),
                   s(do="expect_event", event="resume", min_count=1, within_seconds=1),
                   s(do="hold", buttons=["right"], seconds=0.5), s(do="expect_no_errors")]))  # fmt: skip
    difficulty = label_of(menus, "settings", "difficulty")
    if start and settings and difficulty:
        tests.append(QATest(
            name="difficulty setting", god=True, start="title",
            purpose="Settings changes the difficulty the game reads, and it is kept.",
            steps=[s(do="press", buttons=["enter"]), s(do="select", menu=settings),
                   s(do="expect_menu", menu="settings"), s(do="select", menu=difficulty),
                   s(do="expect_state", path="difficulty", op="==", value="hard"),
                   s(do="reload"), s(do="expect_menu", menu="title"),
                   s(do="expect_state", path="difficulty", op="==", value="hard"),
                   s(do="expect_no_errors")]))  # fmt: skip
    retry = label_of(menus, "game_over", "retry")
    if retry and design.lose.strip():
        tests.append(QATest(
            name="game over and retry", god=False, start="play",
            purpose="Losing opens game over; Retry starts the level again.",
            steps=[s(do="expect_event", event="lose", min_count=1, within_seconds=100),
                   s(do="expect_menu", menu="game_over"), s(do="screenshot", label="game over"),
                   s(do="select", menu=retry),
                   s(do="expect_event", event="restart", min_count=1, within_seconds=2),
                   s(do="expect_state", path="time", op="<", value="5"),
                   s(do="expect_no_errors")]))  # fmt: skip
    return tests


def level_tests(menus: dict | None) -> list[QATest]:
    """One test per level of a game with several (L2.6): it starts in that level, which
    begins, moves with real keys and runs without errors. Code adds them to every build's
    suite (`merge_suite`), so every build checks every level with real input (the bot's run
    is the other half)."""
    levels = (menus or {}).get("levels", [])
    if len(levels) < 2:
        return []
    s = blank_step
    return [QATest(
        name=f"level {lv['n']} starts and plays", god=True, start="play", level=lv["n"],
        purpose=f"Level {lv['n']} ({lv['name']}) starts on its own and the player moves in it.",
        steps=[s(do="expect_event", event="level_start", min_count=1, within_seconds=2),
               s(do="expect_state", path="level", op="==", value=str(lv["n"])),
               s(do="hold", buttons=["right"], seconds=1.5),
               s(do="expect_state", path="x", op=">", value="@start"),
               s(do="screenshot", label=f"level {lv['n']}"), s(do="expect_no_errors")],
    ) for lv in levels[:MAX_LEVELS]]  # fmt: skip


def with_level_tests(tests: list[QATest], menus: dict | None) -> list[QATest]:
    """`tests` plus the per-level tests code writes (a test of the same name is kept)."""
    names = {t.name.casefold() for t in tests}
    return [*tests, *(t for t in level_tests(menus) if t.name.casefold() not in names)]


def fallback_qa(design: GameDesign, menus: dict | None = None) -> QATests:
    """Offline QA Playtester: tests every design can take (movement, no errors, the HUD clock,
    the first timed check, losing and restarting), and with menus the flow (title to play,
    pause, a setting kept over a reload, game over and retry)."""
    s = blank_step
    # A side view moves down by falling, not by a key: there, Space jumps (y gets smaller).
    vertical = ([s(do="hold", buttons=["space"], seconds=0.25),
                 s(do="expect_state", path="y", op="<", value="@start")]
                if design.camera == "side-view" else
                [s(do="hold", buttons=["down"], seconds=1),
                 s(do="expect_state", path="y", op=">", value="@start")])  # fmt: skip
    tests = [
        QATest(name="moves with the arrow keys", god=True,
               purpose="Real key presses move the player.",
               steps=[s(do="hold", buttons=["right"], seconds=1.5),
                      s(do="expect_state", path="x", op=">", value="@start"),
                      *vertical,
                      s(do="expect_no_errors")]),
        QATest(name="plays without errors", god=True,
               purpose="Moving in every direction and both action keys cause no errors.",
               steps=[s(do="hold", buttons=["left", "up"], seconds=2), s(do="press", buttons=["space"]),
                      s(do="hold", buttons=["d"], seconds=2), s(do="press", buttons=["x"]),
                      s(do="wait", seconds=3), s(do="expect_no_errors"),
                      s(do="screenshot", label="playing")]),
        QATest(name="hud time advances", god=True, purpose="The run's time grows with play.",
               steps=[s(do="wait", seconds=3),
                      s(do="expect_state", path="time", op=">=", value="2")]),
    ]  # fmt: skip
    timed = next((c for c in design.checks if c.by_second and c.event not in SDK_EVENTS
                  and c.by_second <= MAX_TEST_SECONDS - 20), None)  # fmt: skip
    if timed:
        tests.append(QATest(name=f"{timed.id} in time", god=True, purpose=timed.description[:200],
                            steps=[s(do="hold", buttons=["left"], seconds=1),
                                   s(do="expect_event", event=timed.event,
                                     min_count=min(timed.min_count, 10000),
                                     within_seconds=timed.by_second)]))  # fmt: skip
    if design.lose.strip():
        lose = s(do="expect_event", event="lose", min_count=1, within_seconds=100)
        tests += [
            QATest(name="loses when idle", god=False, purpose=f"The lose path: {design.lose}"[:200],
                   steps=[lose, s(do="screenshot", label="lost")]),
            QATest(name="restarts after losing", god=False,
                   purpose="Enter restarts the run after a loss and it starts over.",
                   steps=[lose, s(do="press", buttons=["enter"]),
                          s(do="expect_event", event="restart", min_count=1, within_seconds=3),
                          s(do="expect_state", path="time", op="<", value="5"),
                          s(do="hold", buttons=["right"], seconds=1),
                          s(do="expect_state", path="hp", op=">", value="0"),
                          s(do="expect_no_errors")]),
        ]  # fmt: skip
    if menus:
        tests += flow_tests(design, menus)
    return QATests(tests=tests[:MAX_TESTS])


# --- running them ------------------------------------------------------------------------------


def run_qa(build_dir: Path, tests: list, fps: int = 30) -> list[dict]:
    """Play every test against the build, each in a fresh page. Results as `run_test` returns
    them, plus "purpose" and "god"; screenshots are PNG bytes (pop them before saving JSON).
    `tests` may hold code checks (qa_checks.CodeCheck, L2.8) too: run with `run_check`."""
    results = []
    with serve(Path(build_dir)) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for test in tests:
                context = browser.new_context(viewport={"width": 320, "height": 180})
                try:
                    data = test.model_dump()
                    run = qa_runtime.run_check if "code" in data else qa_runtime.run_test
                    result = run(context.new_page(), url, data, fps)
                except PlaywrightError as exc:
                    result = {"name": test.name, "passed": False, "failed_step": None,
                              "failure": f"the browser failed: {str(exc).splitlines()[0][:200]}",
                              "steps": [], "state": {}, "events": {}, "errors": [],
                              "game_seconds": 0.0, "screenshots": []}  # fmt: skip
                finally:
                    context.close()
                results.append({**result, "purpose": test.purpose, "god": test.god})
        finally:
            browser.close()
    return results


def qa_failures(results: list[dict]) -> str:
    """The failed QA tests, for the Programmer's repair prompt."""
    failed = [r for r in results if not r["passed"] and r.get("kind") != "check"]
    if not failed:
        return ""
    lines = [
        (
            "QA tests failed. They press real keys (not the bot), were written from the design "
            "without seeing the code, and rely on the SDK contract: sf.state hp/x/y/time "
            "(level/score), Enter restarts after win or lose, sf.choose menus picked with digit "
            "keys. Fix the game wherever it breaks the design or that contract:"
        )
    ]
    for r in failed:
        where = (f"at step {r['failed_step']} of {len(r['steps'])} "
                 f"({r['steps'][-1]['step']})" if r["failed_step"] else "")  # fmt: skip
        done = "; ".join(f"{i}. {s['step']} ({'ok' if s['ok'] else 'FAILED'})"
                         for i, s in enumerate(r["steps"], 1))  # fmt: skip
        lines.append(
            f"* QA test '{r['name']}' ({r.get('purpose', '')}) failed {where}: {r['failure']}. "
            f"Steps: {done}. sf.state then: {json.dumps(r['state'])[:300]}"
            + (f". Errors: {' | '.join(map(str, r['errors'][:2]))}" if r["errors"] else "")
        )
    return "\n".join(lines)


def without_pictures(results: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k != "screenshots"} for r in results]


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "test"


def screenshot_files(results: list[dict]) -> dict[str, bytes]:
    """{"<test>-<label>.png": png} for every QA screenshot (names unique)."""
    out: dict[str, bytes] = {}
    for r in results:
        for shot in r["screenshots"]:
            name, n = f"{slug(r['name'])}-{slug(shot['label'])}", 2
            while f"{name}.png" in out:
                name, n = f"{slug(r['name'])}-{slug(shot['label'])}-{n}", n + 1
            out[f"{name}.png"] = shot["png"]
            shot["file"] = f"{name}.png"
    return out


# --- the regression suite ------------------------------------------------------------------------


def merge_suite(
    previous: QASuite | None,
    new: list[QATest] | None,
    version: int,
    drop: list[str] = (),
    notes: list[str] = (),
) -> QASuite:
    """Earlier tests (minus `drop`, matched by name) plus `new` ones (a same-named new test
    replaces the old); at most MAX_SUITE, newest kept."""
    dropped = {d.casefold().strip() for d in drop if d.strip()}
    tests: dict[str, QATest] = {}
    since: dict[str, int] = {}
    for t in previous.tests if previous else []:
        if t.name.casefold() not in dropped:
            tests[t.name.casefold()] = t
            since[t.name] = previous.since(t.name) or version
    for t in new or []:
        if t.name.casefold() not in dropped:
            old = tests.pop(t.name.casefold(), None)
            if old is not None:
                since.pop(old.name, None)
            tests[t.name.casefold()] = t
            since[t.name] = version
    kept = sorted(tests.values(), key=lambda t: -since[t.name])[:MAX_SUITE]
    kept = [t for t in tests.values() if t in kept]
    gone = sorted({*(previous.dropped if previous else []), *(d for d in drop if d.strip())})
    return QASuite(tests=kept, versions=[since[t.name] for t in kept], dropped=gone,
                   notes=list(notes))  # fmt: skip


# --- exporting them --------------------------------------------------------------------------------

EXPORT_HEADER = """# QA tests for this game build: written by the SpriteForge QA Playtester from the game's
# design and played by the harness before you saw the build.
#
#   pip install pytest playwright && python -m playwright install chromium
#   pytest tests            (from the build folder, or pass the path of this folder)
#
# Everything below up to "The tests" is the SpriteForge QA runtime, copied unchanged. The tests
# themselves are data (JSON in one string): steps are interpreted; a code check's JavaScript
# runs only inside the game's browser page, never in Python.
"""

EXPORT_GLUE = """

# ---------------------------------------------------------------------------------------------
# The tests: data written by the QA Playtester, played by the runtime above.
import pathlib  # noqa: E402

import pytest  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

BUILD = pathlib.Path(__file__).resolve().parents[1]
DATA = json.loads(DATA_JSON)


@pytest.fixture(scope="module")
def site():
    with serve(BUILD) as url:
        yield url


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        chromium = pw.chromium.launch(headless=True)
        yield chromium
        chromium.close()


@pytest.mark.parametrize("qa", DATA["tests"], ids=[t["name"] for t in DATA["tests"]])
def test_qa(qa, site, browser):
    context = browser.new_context(viewport={"width": 320, "height": 180})
    try:
        run = run_check if "code" in qa else run_test  # a code check runs in the page (L2.8)
        result = run(context.new_page(), site, qa, DATA["fps"])
    finally:
        context.close()
    assert result["passed"], "step %s: %s" % (result["failed_step"], result["failure"])
"""


def export_tests(tests: list, fps: int = 30) -> str:
    """`tests/test_game_qa.py` for a build: the runtime, then the tests as JSON inside a single
    string literal made by `repr` (model text never becomes code)."""
    runtime = Path(qa_runtime.__file__).read_text(encoding="utf-8")
    data = json.dumps({"fps": fps, "tests": [t.model_dump() for t in tests]}, indent=1)
    return EXPORT_HEADER + runtime + "\n\nDATA_JSON = " + repr(data) + "\n" + EXPORT_GLUE


def write_export(build_dir: Path, tests: list, fps: int = 30) -> Path:
    path = Path(build_dir) / "tests" / "test_game_qa.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(export_tests(tests, fps), encoding="utf-8")
    return path


def step_texts(test: QATest) -> list[str]:
    return [describe_step(s.model_dump()) for s in test.steps]
