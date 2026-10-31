"""The Programmer's output and the checks it must pass before it is ever run.

Muse Spark writes the whole game (`GameCode`: one or more plain-script files, `game.js` last).
Static checks reject what the build must never contain (network access, eval, modules, URLs,
its own `new Phaser.Game`), unknown asset ids, oversized output and syntax errors (`node
--check`, which parses without executing). Failures go back to the Programmer with the
playtest report in `repair_prompt`.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import Field

from spriteforge.agents.models import Contract

from .design import GameDesign

SDK_API = (Path(__file__).parent / "sdk" / "API.md").read_text(encoding="utf-8")
MOCK_GAME = Path(__file__).with_name("mock_game.js")
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


def assets_summary(manifest: dict) -> str:
    screen = manifest["screen"]
    lines = [
        f"Screen {screen[0]}x{screen[1]}, sf.assets.scale = {manifest['scale']} (sizes below are scaled)."
    ]
    for ident, a in manifest["sprites"].items():
        anims = ", ".join(f"{n} ({c} frames)" for n, (_, c) in a["anims"].items())
        lines.append(f"* sprite `{ident}` ({a['role']}) {a['frame'][0]}x{a['frame'][1]}: {anims}")
    for ident, a in manifest["images"].items():
        extra = f", parallax {a['parallax']:g}" if "parallax" in a else ""
        lines.append(f"* image `{ident}` ({a['role']}) {a['size'][0]}x{a['size'][1]}{extra}")
    return "\n".join(lines)


def programmer_prompt(design: GameDesign, manifest: dict) -> str:
    checks = "\n".join(
        f"* sf.event('{c.event}') - {c.description} (at least {c.min_count}"
        + (f", by {c.by_second:g} s)" if c.by_second else ")")
        for c in design.checks
    )
    return f"""Write the complete game below as plain browser scripts for Phaser 3.90 using the
SpriteForge SDK (reference at the end). Implement every rule, the progression and the difficulty
curve of the design exactly; do not add unrelated features.

Hard requirements (the harness checks them automatically):
* game.js is loaded last and calls sf.start({{scenes, autoplay}}). Extra helper files are
  allowed (loaded before game.js in the order you list them).
* Controls only through sf.input(scene). Time only through sf.clock.now or scene timers.
* Emit these events exactly when they happen, plus 'win' (or 'lose') once at the end:
{checks}
* The playtest runs the full {design.session_seconds} s session with your autoplay policy in god
  mode (sf.flags.god: the player can be hurt but HP never drops below 1). The policy must play
  well enough that every event above happens in time.
* Use only the asset ids listed below; characters, enemies, pickups and tiles must use them.
* No network, no modules, no eval, no URLs, no Date.now/performance.now.

Design (JSON):
{design.model_dump_json(indent=1)}

Assets:
{assets_summary(manifest)}

Phaser notes:
{SKILL.read_text(encoding="utf-8").split("---", 2)[-1].strip()}

SDK reference:
{SDK_API}"""


def repair_prompt(design: GameDesign, code: GameCode, failures: str, manifest: dict) -> str:
    files = "\n\n".join(f"=== {f.path} ===\n{f.content}" for f in code.files)
    return f"""The game below was built and playtested by the harness and failed. Fix every
problem listed, keep everything that works, and return ALL files complete (not a diff).

Problems:
{failures}

Design checks (each must happen in the {design.session_seconds} s autoplay session):
{json.dumps([c.model_dump() for c in design.checks], indent=1)}

Assets:
{assets_summary(manifest)}

Current files:
{files}

SDK reference:
{SDK_API}"""


def change_prompt(design: GameDesign, code: GameCode, instruction: str, manifest: dict) -> str:
    """A reviewer asked for a change: edit the working game, keep everything else."""
    files = "\n\n".join(f"=== {f.path} ===\n{f.content}" for f in code.files)
    return f"""The game below works and passed its playtest. The human reviewer asked for this
change; make it, keep every other behaviour and every sf.event that still applies, keep the
autoplay policy able to trigger all design checks, and return ALL files complete.

Change requested:
{instruction.strip()}

Design (JSON, already updated if the change affected it):
{design.model_dump_json(indent=1)}

Assets:
{assets_summary(manifest)}

Current files:
{files}

SDK reference:
{SDK_API}"""


def mock_code() -> GameCode:
    """What the offline Programmer returns: the bundled fixture game."""
    return GameCode(
        files=[CodeFile(path="game.js", content=MOCK_GAME.read_text(encoding="utf-8"))],
        notes="Offline fixture game (mock mode): an arena with spawning enemies and gems.",
    )
