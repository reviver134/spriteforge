"""The Programmer's output and the checks it must pass before it is ever run.

Muse Spark writes the whole game (`GameCode`: one or more plain-script files, `game.js` last).
Static checks reject what the build must never contain (network access, eval, modules, URLs,
its own `new Phaser.Game`), unknown asset ids, oversized output and syntax errors (`node
--check`, which parses without executing). Failures go back to the Programmer with the
playtest report in `repair_prompt`. Repairs and change requests come back as `CodePatch`
edits applied by code (`apply_patch`); a patch that does not apply cleanly makes that round
ask for complete files instead. Every prompt starts with `code_context`, so the long
reference part is a cacheable prefix.
"""

import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import Field

from spriteforge.agents.models import Contract

from .design import GameDesign

SDK_API = (Path(__file__).parent / "sdk" / "API.md").read_text(encoding="utf-8")
LEVELS_API = (Path(__file__).parent / "sdk" / "LEVELS.md").read_text(encoding="utf-8")
MOCK_GAME = Path(__file__).with_name("mock_game.js")
MOCK_PLATFORMER = Path(__file__).with_name("mock_platformer.js")
SKILL = Path(__file__).parents[1] / "skills" / "builtin" / "phaser-arcade-physics" / "SKILL.md"
FILE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,40}\.js$")
MAX_FILES = 8
FORBIDDEN = [
    (r"\bfetch\s*\(", "fetch()"),
    (r"\bXMLHttpRequest\b", "XMLHttpRequest"),
    (r"\bWebSocket\b", "WebSocket"),
    (r"\bEventSource\b", "EventSource"),
    (r"\bsendBeacon\b", "navigator.sendBeacon"),
    (r"\bimportScripts\b|\bnew\s+(Shared)?Worker\b", "workers"),
    (r"\beval\s*\(", "eval()"),
    (r"\bnew\s+Function\s*\(|\bFunction\s*\(\s*['\"]", "Function() constructors"),
    (r"\bimport\s*\(", "dynamic import()"),
    (r"(?m)^\s*(import|export)\s", "ES module import/export"),
    (r"\brequire\s*\(", "require()"),
    (r"https?://|//[a-z0-9.-]+\.[a-z]{2,}/", "URLs"),
    (r"document\.cookie", "cookies"),
    (r"\bnew\s+Phaser\.Game\b", "new Phaser.Game (use sf.start)"),
    (r"\bDate\.now\s*\(|\bperformance\.now\s*\(", "wall-clock time (use sf.clock.now)"),
]
ASSET_USE = re.compile(
    r"(?:sf\.sprite\([^,()]*,[^,()]*,[^,()]*,|\.add\.(?:image|tileSprite|sprite)\([^()]*?,)"
    r"\s*['\"]([a-z0-9_]+)['\"]"
)
# Keys the game makes itself (generateTexture('bullet'), textures.createCanvas('glow', ...)).
OWN_TEXTURE = re.compile(r"(?:generateTexture|createCanvas|addCanvas)\(\s*['\"]([a-z0-9_]+)['\"]")


class CodeFile(Contract):
    path: str = Field(description="file name like game.js or enemies.js (no folders)")
    content: str


class GameCode(Contract):
    files: list[CodeFile]
    notes: str = Field(description="How the game is organised and what the autoplay bot does")

    def as_dict(self) -> dict[str, str]:
        return {f.path: f.content for f in self.files}


def strip_comments(code: str) -> str:
    code = re.sub(r"/\*.*?\*/", " ", code, flags=re.DOTALL)
    return re.sub(r"(?m)(^|[\s;{}])//[^\n]*", r"\1", code)


def syntax_errors(files: dict[str, str]) -> list[str]:
    """`node --check` parses each file as a classic script without running it."""
    node = shutil.which("node")
    if not node:
        return []
    out = []
    with TemporaryDirectory() as tmp:
        for name, code in files.items():
            path = Path(tmp) / name
            path.write_text(code, encoding="utf-8")
            proc = subprocess.run(
                [node, "--check", str(path)],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode:
                detail = proc.stderr.replace(str(path), name).strip().splitlines()
                out.append(f"{name}: syntax error: " + " | ".join(detail[:5]))
    return out


def static_check(code: GameCode, asset_ids: set[str], max_kb: int = 200) -> list[str]:
    """Problems that stop a build from being run; [] when it may be built and played."""
    files = code.as_dict()
    problems = []
    if len(files) != len(code.files):
        problems.append("two files have the same name")
    if "game.js" not in files:
        problems.append("game.js is missing (it is loaded last and calls sf.start)")
    if len(files) > MAX_FILES:
        problems.append(f"{len(files)} files; at most {MAX_FILES}")
    for name in files:
        if not FILE_NAME.match(name):
            problems.append(f"bad file name {name!r}: use lowercase names like enemies.js")
    size = sum(len(c.encode()) for c in files.values()) / 1024
    if size > max_kb:
        problems.append(f"{size:.0f} KB of code; the limit is {max_kb} KB")
    own = {k for c in files.values() for k in OWN_TEXTURE.findall(strip_comments(c))}
    for name, content in files.items():
        bare = strip_comments(content)
        for pattern, what in FORBIDDEN:
            if re.search(pattern, bare):
                problems.append(f"{name}: uses {what}, which generated games may not")
        for asset in sorted(set(ASSET_USE.findall(bare)) - asset_ids - own):
            problems.append(f"{name}: unknown asset id {asset!r}")
    if "game.js" in files and "sf.start(" not in strip_comments(files["game.js"]):
        problems.append("game.js never calls sf.start({scenes, autoplay})")
    if not problems:
        problems += syntax_errors(files)
    return problems


class CodeEdit(Contract):
    path: str = Field(description="file name like game.js")
    old: str = Field(
        description="exact text copied from the current file, occurring there exactly once "
        "(include enough lines to make it unique); '' = `new` is the whole file (new or "
        "rewritten)"
    )
    new: str = Field(description="the replacement text")


class CodePatch(Contract):
    """Repairs and changes as edits, not whole files: far fewer output tokens."""

    edits: list[CodeEdit]
    notes: str = Field(description="What changed and why ('' keeps the previous notes)")


class PatchError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def apply_patch(code: GameCode, patch: CodePatch) -> GameCode:
    """Apply edits in order. Every `old` must occur exactly once in its file at that point;
    otherwise nothing is applied and PatchError lists every edit that failed."""
    files = code.as_dict()
    problems = []
    for i, edit in enumerate(patch.edits, 1):
        if not edit.old:
            files[edit.path] = edit.new
            continue
        if edit.path not in files:
            problems.append(f"edit {i}: there is no file {edit.path}")
            continue
        count = files[edit.path].count(edit.old)
        if count != 1:
            problems.append(
                f"edit {i} ({edit.path}): the old text occurs {count} times, not exactly once"
            )
            continue
        files[edit.path] = files[edit.path].replace(edit.old, edit.new, 1)
    if problems:
        raise PatchError(problems)
    return GameCode(
        files=[CodeFile(path=p, content=c) for p, c in files.items()],
        notes=patch.notes.strip() or code.notes,
    )


def assets_summary(manifest: dict) -> str:
    screen = manifest["screen"]
    lines = [
        f"Screen {screen[0]}x{screen[1]}, sf.assets.scale = {manifest['scale']} (sizes below are scaled)."
    ]
    for ident, a in manifest["sprites"].items():
        rows = a.get("rows") or {}
        anims = ", ".join(
            f"{n} ({c} frames"
            + (f", {rows[n]['plays']}{' ' + rows[n]['form'] if rows[n].get('form') else ''}, "
               f"{'loop' if rows[n]['loop'] else 'once'}" if n in rows else "") + ")"
            for n, (_, c) in a["anims"].items())  # fmt: skip
        lines.append(f"* sprite `{ident}` ({a['role']}) {a['frame'][0]}x{a['frame'][1]}: {anims}")
    for ident, a in manifest["images"].items():
        extra = f", parallax {a['parallax']:g}" if "parallax" in a else ""
        lines.append(f"* image `{ident}` ({a['role']}) {a['size'][0]}x{a['size'][1]}{extra}")
    if fx := manifest.get("fx"):
        lines.append("Effects (sf.fx(name, x, y, sprite); the level engine plays the ones with an "
                     "`on` itself): " + ", ".join(f"{n} (on {e['on']})" for n, e in fx.items()))
    if forms := manifest.get("forms"):
        lines.append("Forms (the player's look while it holds the ability; the SDK shows it): "
                     + ", ".join(f"{n} ({f['row'] or 'tint ' + f['tint']})" for n, f in forms.items()))
    return "\n".join(lines)


def code_context(design: GameDesign, manifest: dict, levels: str = "") -> str:
    """The stable start of every Programmer prompt: API reference, design, assets (and, for a
    game with levels as data, the level engine's reference and what the levels hold). New-game,
    repair and change prompts for one design share it word for word, so Spark's automatic
    prefix cache serves it (cached input costs about an eighth); the parts that change
    (code, failures, the instruction) come after it."""
    return f"""Reference for writing a SpriteForge game: plain browser scripts for Phaser 3.90
using the SpriteForge SDK.

Phaser notes:
{SKILL.read_text(encoding="utf-8").split("---", 2)[-1].strip()}

SDK reference:
{SDK_API}
""" + (f"\n{LEVELS_API}\n" if levels else "") + f"""
Design (JSON):
{design.model_dump_json(indent=1)}

Assets:
{assets_summary(manifest)}
""" + (f"\nLevels:\n{levels}\n" if levels else "")


def level_requirements(design: GameDesign) -> str:
    checks = "\n".join(f"* sf.event('{c.event}') - {c.description} (at least {c.min_count}, "
                       "counted over all levels)" for c in design.checks)
    return f"""Hard requirements (the harness checks them automatically):
* game.js is loaded last and calls sf.start({{scenes}}) with one level scene that calls
  sf.level.build(this, hooks) in create(). No autoplay, no player controller, no level geometry:
  the SDK does them (see "Levels as data").
* Mechanics in hooks.touch(thing, how) exactly as each thing's data says (needs, opened_by,
  holds, gives): the SDK's bot plays every level on its own (god mode) along the route code found,
  touching, dashing into and collecting what the route needs; it gets stuck when a thing does not
  behave as its data says.
* Emit these events exactly when they happen (the SDK already emits level_start,
  level_complete, checkpoint, respawn, jump, dash and win):
{checks}
* Each level must make its listed events happen on the bot's way to the exit.
* Use only the asset ids listed above. Time only through sf.clock.now or scene timers.
* sf.state: the SDK sets level, x, y and time; add hp and score (when the design has them) with
  Object.assign(sf.state, {{...}}) every frame. QA tests read them and press real keys
  (arrows/WASD, Space jumps, X/Shift dashes, Enter after win restarts).
* No network, no modules, no eval, no URLs, no Date.now/performance.now."""


def requirements(design: GameDesign, levels: str = "") -> str:
    if levels:
        return level_requirements(design)
    checks = "\n".join(
        f"* sf.event('{c.event}') - {c.description} (at least {c.min_count}"
        + (f", by {c.by_second:g} s)" if c.by_second else ")")
        for c in design.checks
    )
    return f"""Hard requirements (the harness checks them automatically):
* game.js is loaded last and calls sf.start({{scenes, autoplay}}). Extra helper files are
  allowed (loaded before game.js in the order you list them).
* Controls only through sf.input(scene). Time only through sf.clock.now or scene timers.
* Emit these events exactly when they happen, plus 'win' (or 'lose') once at the end:
{checks}
* The playtest runs the full {design.session_seconds} s session with your autoplay policy in god
  mode (sf.flags.god: the player can be hurt but HP never drops below 1). The policy must play
  well enough that every event above happens in time.
* Use only the asset ids listed above; characters, enemies, pickups and tiles must use them.
* sf.state always holds hp, x, y (player position) and time (seconds since this run started;
  a restart resets it), plus level and score when the design has them: QA tests read them.
* After 'win' or 'lose', Enter restarts the run with this.scene.restart() and everything starts
  over: QA tests press real keys (arrows/WASD, Space, Enter, digits for sf.choose menus).
* A player who presses nothing can lose (when the design has a way to lose): no auto-fire,
  auto-attack or auto-dodge outside the autoplay policy. A QA test stands still and expects
  'lose' (QA caught auto-fire that made an idle player unbeatable in 2 of 3 live builds).
* No network, no modules, no eval, no URLs, no Date.now/performance.now."""


EDITS = """Answer with edits, not whole files: each edit names a file, `old` is an exact
snippet copied from the current file that occurs there exactly once (include enough
surrounding lines to make it unique) and `new` replaces it. Edits apply in order. To add a
file, or to rewrite one completely, use old = "" with the whole file in `new`."""


def current_files(code: GameCode) -> str:
    return "\n\n".join(f"=== {f.path} ===\n{f.content}" for f in code.files)


def programmer_prompt(design: GameDesign, manifest: dict, levels: str = "") -> str:
    return f"""{code_context(design, manifest, levels)}
# Task
Write the complete game described above. Implement every rule, the progression and the
difficulty curve of the design exactly; do not add unrelated features.

{requirements(design, levels)}"""


def repair_prompt(
    design: GameDesign,
    code: GameCode,
    failures: str,
    manifest: dict,
    patch_error: str | None = None,
    levels: str = "",
) -> str:
    """Repair as edits (`CodePatch`); with `patch_error` (the edits did not apply), as
    complete files (`GameCode`)."""
    answer = (
        f"Your edits could not be applied ({patch_error}). Return ALL files complete this time."
        if patch_error is not None
        else EDITS
    )
    return f"""{code_context(design, manifest, levels)}
# Current files
{current_files(code)}

# Task
The game above was built and playtested by the harness and failed. Fix every problem
listed, keep everything that works.

{requirements(design, levels)}

Problems:
{failures}

{answer}"""


def change_prompt(
    design: GameDesign,
    code: GameCode,
    instruction: str,
    manifest: dict,
    patch_error: str | None = None,
    levels: str = "",
) -> str:
    """A reviewer asked for a change: edit the working game, keep everything else. The design
    above is already updated if the change affected it."""
    answer = (
        f"Your edits could not be applied ({patch_error}). Return ALL files complete this time."
        if patch_error is not None
        else EDITS
    )
    return f"""{code_context(design, manifest, levels)}
# Current files
{current_files(code)}

# Task
The game above works and passed its playtest. The human reviewer asked for a change; make
it, keep every other behaviour and every sf.event that still applies, and keep the autoplay
policy able to trigger all design checks (the design above is already updated if the
change affected it).

{requirements(design, levels)}

Change requested:
{instruction.strip()}

{answer}"""


def mock_code(levels: bool = False) -> GameCode:
    """What the offline Programmer returns: the bundled fixture game (the platformer fixture
    for a game with levels as data)."""
    if levels:
        return GameCode(
            files=[CodeFile(path="game.js", content=MOCK_PLATFORMER.read_text(encoding="utf-8"))],
            notes="Offline fixture platformer (mock mode): every kind of level thing from its data.",
        )
    return GameCode(
        files=[CodeFile(path="game.js", content=MOCK_GAME.read_text(encoding="utf-8"))],
        notes="Offline fixture game (mock mode): an arena with spawning enemies and gems.",
    )


def level_summary(levels: list) -> str:
    """What the levels hold, for the Programmer: every kind of thing (from the legends), the
    exclusive abilities and the events each level makes happen. `levels`: LevelRecords."""
    kinds = {}
    for r in levels:
        for e in r.spec.legend:
            if e.kind in {"solid", "one_way", "hazard", "start"}:
                continue
            opened = e.opened_by if e.kind in {"crate", "breakable", "enemy"} else ""
            parts = (("needs", e.needs), ("opened by", opened), ("holds", e.holds), ("gives", e.gives))
            line = f"* {e.kind} `{e.entity}`" + "".join(f"; {k} {v}" for k, v in parts if v)
            kinds.setdefault(line, None)
    exclusive = sorted({a for r in levels for a in r.spec.exclusive})
    events = "\n".join(f"* level {r.spec.n} ({r.spec.name}): {', '.join(r.spec.events) or '-'}"
                       for r in levels)  # fmt: skip
    return (f"{len(levels)} levels (data the SDK loads). Things in them (touch() must handle "
            "each as its data says):\n" + "\n".join(kinds) + "\n"
            + (f"Exclusive abilities (taking one drops the others): {', '.join(exclusive)}\n"
               if exclusive else "")
            + f"Events each level makes happen on the bot's way to the exit:\n{events}")  # fmt: skip
