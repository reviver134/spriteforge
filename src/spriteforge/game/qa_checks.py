"""Code checks (L2.8): the QA Playtester writes its own checks of this game, as code.

Any game can be asked for, so no fixed list of steps or bot can know what a game must do. After
the Programmer's first answer, the QA Playtester reads the design (what the game must do) and
the code (where things live: scenes, sprite groups, arrays, functions) and writes 3-8 checks of
this game's own scenarios: "an enemy dies only when a hero bullet touches it", "enemy bullets
fly until they leave the screen", "standing on a platform never hurts the hero". Each check is
a JavaScript function body run inside the game's page with one helper, `qa` (qa_runtime): it
sets a scene up (moves the player, waits, holds keys), lets game time run and asserts with
`qa.expect`. A failed expect goes to the Programmer like a failed QA test; an exception in the
check itself is the QA Playtester's mistake: it gets the error once to fix the check, and a
check still broken is shown at G4 and never blamed on the game.

Models propose; code enforces: every check is parsed (`node --check`), limited in size and
checked for forbidden calls (no network, storage, eval, timers, page navigation, changes to the
SDK, and no faking outcomes with sf.event or the level engine's open/take/drop) before it runs;
the browser page is the sandbox and the check has a game-time and a wall-clock limit.
"""

import re

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

from .codegen import GameCode, strip_comments, syntax_errors
from .design import GameDesign
from .qa_runtime import CHECK_SECONDS

MAX_CHECKS, MIN_CHECKS, MAX_CODE = 8, 1, 4000
CODE_BUDGET = 30000  # characters of the game's code shown to the QA Playtester
FORBIDDEN = [
    (r"\bfetch\s*\(|XMLHttpRequest|WebSocket|EventSource|sendBeacon", "network calls"),
    (r"\beval\s*\(|\bFunction\s*\(|\bimport\s*\(|\brequire\s*\(", "eval, Function, import or require"),
    (r"(?m)^\s*(import|export)\s", "module import/export"),
    (r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", "URLs"),
    (r"localStorage|sessionStorage|indexedDB|document\.cookie", "browser storage"),
    (r"\bwindow\.open\b|\blocation\s*(\.\s*(href|assign|replace|reload)\b|=)|\bhistory\.", "page navigation"),
    (r"\bset(Timeout|Interval)\s*\(|requestAnimationFrame|\bWorker\s*\(|postMessage|\bawait\b|\bPromise\b",
     "timers, workers or async code (use qa.step for time)"),
    (r"document\.write|\.innerHTML\s*=", "rewriting the page"),
    (r"\bsf\s*\.\s*(event|fx|start|choose|input|flow|level\s*\.\s*(open|take|drop|form|respawn))\s*\(",
     ("calling the SDK to make things happen (sf.event, sf.level.open ...): a check plays the "
      "game, it never fakes an outcome")),
    (r"\bsf\s*\.\s*[A-Za-z_]+\s*=(?!=)|\bwindow\s*\.\s*(sf|sfRun|sfShot|sfCapture)\s*=", "changing the SDK"),
]  # fmt: skip


def need(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


class CodeCheck(Contract):
    name: str = Field(description="short unique name, e.g. 'enemies die only from hero bullets'")
    purpose: str = Field(description="the rule of this game it proves, in one sentence")
    god: bool = Field(description="true: the player is hurt but never dies (most checks); false "
                      "only for a check of losing")
    start: str = Field(description="'play' (the check starts in play, in `level`) or 'title'")
    level: int = Field(description="the level the check starts in (1 = the first)")
    code: str = Field(description="the JavaScript function body (see the task): sets up, steps "
                      "game time with qa, asserts with qa.expect")

    @model_validator(mode="after")
    def check(self):
        self.name, self.purpose = self.name.strip()[:60], self.purpose.strip()[:240]
        need(bool(self.name), "a check needs a name")
        self.start = "title" if self.start.strip().lower() == "title" else "play"
        self.level = max(1, min(8, self.level))
        need(0 < len(self.code.strip()) <= MAX_CODE, f"check {self.name!r}: code must be 1-{MAX_CODE} characters")
        return self


class CodeChecks(Contract):
    checks: list[CodeCheck]

    @model_validator(mode="after")
    def check(self):
        need(MIN_CHECKS <= len(self.checks) <= MAX_CHECKS, f"write {MIN_CHECKS} to {MAX_CHECKS} checks")
        names = [c.name.casefold() for c in self.checks]
        need(len(set(names)) == len(names), "check names must be unique")
        return self


def check_problems(check: CodeCheck) -> list[str]:
    """Why a check may not run: a forbidden call, a syntax error."""
    bare = strip_comments(check.code)
    out = [f"check '{check.name}' uses {why}" for pattern, why in FORBIDDEN if re.search(pattern, bare)]
    wrapped = "(function (qa, sf) {\n" + check.code + "\n});\n"
    out += [f"check '{check.name}': " + e.split(": ", 1)[-1] for e in syntax_errors({"check.js": wrapped})]
    return out


def usable(checks: CodeChecks) -> tuple[list[CodeCheck], list[str]]:
    """The checks that may run, and why the others were left out."""
    kept, notes = [], []
    for c in checks.checks:
        problems = check_problems(c)
        if problems:
            notes += problems
        else:
            kept.append(c)
    return kept, notes


# --- the prompt ------------------------------------------------------------------------------------

QA_API = f"""The check is the body of `function (qa, sf) {{ ... }}`, run synchronously inside the
running game (the game is paused between frames; only qa.step moves time). `sf` is the SDK
(read it, never change it): sf.state (hp, x, y, time, level, score ...), sf.counts[name].count,
sf.level.current (levels as data: .player, .list of things {{id, kind, entity, open, sprite}},
.tile pixels per tile, .grounded) and the scene's own objects (qa.scene()).
qa:
* qa.step(seconds) / qa.frames(n): let game time run (at most {CHECK_SECONDS} s per check)
* qa.hold(keys, seconds), qa.press(key), qa.down(key), qa.up(key): key presses the game reads
  like real keys; keys: left right up down w a s d space z x c j k shift enter escape
* qa.scene(): the game's scene object (its fields, groups and arrays: read them); qa.camera():
  {{x, y, width, height}} of the view; qa.onScreen(obj, margin)
* qa.sprites(texture): live sprites/images, all or of one texture key (string or RegExp):
  [{{key, x, y, visible, alpha, active, w, h, vx, vy, flipX, rotation, obj}}]
* qa.state(), qa.count(event), qa.events(), qa.errors(), qa.time(), qa.level()
* qa.expect(condition, message): fails the check with the message (say what was seen)
* qa.log(...): notes shown with the result; qa.near(a, b, pixels)
You may move things to set a scene up (e.g. put the player next to an enemy:
`p.x = e.x - 3 * T; p.body.reset(p.x, p.y)`) and read any object, but never make an outcome
happen yourself (no sf.event, no killing or opening things from the check): the game must
do it while time runs. No timers, async code, network, storage or page changes."""

EXAMPLE = """// Example (another game): coins are collected only when the player touches them.
const s = qa.scene(), L = qa.level(), T = L ? L.tile : 32;
const coins = qa.sprites('coin');
qa.expect(coins.length > 0, 'no coin sprites in level 1');
const before = qa.state().score || 0;
qa.step(2);                                   // standing still far away
qa.expect((qa.state().score || 0) === before, 'score rose with nothing collected');
const c = coins[0].obj, p = L.player;
p.body.reset(c.x - 2 * T, c.y);               // set the scene up: two tiles left of a coin
qa.hold('right', 1.5);                        // walk into it with real keys
qa.expect(!c.active || !c.visible, 'the coin is still there after walking into it');
qa.expect((qa.state().score || 0) > before, 'score did not rise after taking a coin');"""


def code_text(code: GameCode, budget: int = CODE_BUDGET) -> str:
    """The game's files for the QA Playtester, cut to `budget` characters (each file shortened
    in proportion)."""
    total = sum(len(f.content) for f in code.files) or 1
    parts = []
    for f in code.files:
        room = max(800, int(budget * len(f.content) / total))
        text = f.content if len(f.content) <= room else f.content[:room] + "\n/* ... cut ... */"
        parts.append(f"--- {f.path} ---\n{text}")
    return "\n".join(parts)


def check_prompt(design: GameDesign, code: GameCode, levels: str = "") -> str:
    return f"""Write code checks for this game: 3 to {MAX_CHECKS} small scenario tests of its own
rules, as JavaScript that runs inside the running game.

The design is the truth: check what the design promises (how things are beaten, what hurts the
player, what projectiles do, what is collected, what changes the score, how levels end). Read
the code only to find where things live (scene fields, groups, arrays, sprite keys, functions).
The code may be wrong: when it does something the design does not promise (e.g. enemies dying
without being hit, a bullet that never goes away, a block that kills the player), write a check
that catches it. Check behaviour a player sees, each check one rule, with a message that says
what went wrong. Prefer setting a scene up (move the player next to the thing) and playing it
with real keys over long waits. Every check starts a fresh game.

{QA_API}

{EXAMPLE}

Design:
{design.model_dump_json(indent=1)}
{("Levels (data the SDK builds):" + chr(10) + levels + chr(10)) if levels else ""}
The game's code:
{code_text(code)}"""


def repair_prompt(prompt: str, broken: list[dict], checks: list[CodeCheck]) -> str:
    """The checks that failed by themselves (an exception in the check, not a failed expect)."""
    by_name = {c.name: c for c in checks}
    lines = []
    for r in broken:
        c = by_name.get(r["name"])
        lines.append(f"* '{r['name']}': {r['failure']}\n  code:\n{c.code if c else '?'}")
    return (prompt + "\n\nThese checks failed by themselves (their own code threw, the game was "
            "not at fault). Write the whole list again: fix them using the game's real names "
            "from the code; keep the other checks as they are.\n" + "\n".join(lines))  # fmt: skip


def fallback_checks(design: GameDesign) -> CodeChecks:
    """The offline QA Playtester: checks every game can take."""
    side = design.camera == "side-view"
    return CodeChecks(checks=[
        CodeCheck(name="plays without errors", god=True, start="play", level=1,
                  purpose="Moving and both action keys for a few seconds cause no runtime errors.",
                  code="qa.hold(['right'], 1.5);\nqa.press('space');\nqa.hold(['left'], 1);\n"
                       "qa.press('x');\nqa.step(2);\n"
                       "qa.expect(qa.errors().length === 0, 'runtime errors: ' + qa.errors().slice(0, 2).join(' | '));"),
        CodeCheck(name="the player moves with the keys", god=True, start="play", level=1,
                  purpose="Holding right moves the player right." + (" (side view)" if side else ""),
                  code="const x0 = qa.state().x;\nqa.hold('right', 1);\n"
                       "qa.expect(qa.state().x > x0, 'x stayed ' + x0 + ' -> ' + qa.state().x);"),
    ])  # fmt: skip


def check_failures(results: list[dict], checks: list[CodeCheck]) -> str:
    """Failed checks (not broken ones), for the Programmer's repair prompt."""
    failed = [r for r in results if r.get("kind") == "check" and not r["passed"] and not r.get("broken")]
    if not failed:
        return ""
    code = {c.name: c.code for c in checks}
    lines = [("QA code checks failed. The QA Playtester wrote them from the design for this game "
              "and ran them in the game; fix the game (not the checks) where it breaks the rule:")]
    for r in failed:
        lines.append(f"* check '{r['name']}' ({r.get('purpose', '')}): {r['failure']}"
                     + (f". Notes: {'; '.join(r.get('logs') or [])[:300]}" if r.get("logs") else "")
                     + f"\n  the check:\n{code.get(r['name'], '')[:1500]}")  # fmt: skip
    return "\n".join(lines)
