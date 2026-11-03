"""The concepts of a game's OKF bundle, written by code from a `GameRecord` (no model call).

`bundle(record)` returns every concept and file; `gamedb.sync` writes them. See gamedb for
the layout.
"""

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from spriteforge.core.security import redact
from spriteforge.library.looks import measure
from spriteforge.memory.okf.codec import UnsafeKnowledge, ensure_no_secrets
from spriteforge.memory.okf.models import Entry, Metadata, Source

from .art import Filled
from .art_review import sheet_image, thumb
from .codegen import GameCode
from .codeinfo import Module, read_code
from .design import GameDesign
from .levels import LevelRecord
from .menus import ACTIONS, MenuRecord, menus_markdown
from .motion import MotionRecord, motion_markdown
from .plan import PlanRecord
from .qa import SDK_EVENTS, STATE_KEYS, QASuite, Step
from .qa_runtime import describe_step
from .research import ResearchRecord
from .story import StoryBible
from .structure import GameStructure, structure_markdown

WITHHELD = "withheld from the record: it looked like a credential"


# --- what a game is made of ------------------------------------------------------------------


@dataclass
class ArtInfo:
    """One entity's locked art."""

    source: str  # "muse", "library <id>", "muse (locked, from v1)" ...
    version: int  # the build that made it
    sheet: bytes  # the sprite sheet / image the game loads (PNG)
    portrait: bytes  # its first frame at 4x (PNG)
    look: str  # measured colours, brightness, detail
    prompt: str = ""  # the Muse prompt, '' for library art

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.sheet).hexdigest()[:12]


def art_info(filled: dict[str, Filled], version: int) -> dict[str, ArtInfo]:
    """The locked art of a build, by entity id."""
    out = {}
    for eid, f in filled.items():
        made = int(f.record.get("version") or f.record.get("reused_from") or version)
        prompt = f.record.get("muse_prompt") or f.record.get("prompt") or ""
        out[eid] = ArtInfo(f.source, made, f.png, thumb(f), measure(sheet_image(f)).text,
                           str(prompt))  # fmt: skip
    return out


@dataclass
class Build:
    version: int
    source: str  # the report's artifact or file name
    report: dict  # report.json


@dataclass
class Decision:
    """The human at a gate: what they said and what it changed."""

    gate: str  # "G2 design v1", "G4 playtest v2"
    outcome: str  # "approved" | "changes requested" | "story added"
    words: str  # their feedback (redacted), '' for a plain approval
    changed: str  # what it changed
    build: int | None = None  # the build it is about, or the build that answered it
    asked_build: bool = False  # it asked for that build (approving a design, changes at G4)


@dataclass
class GameRecord:
    design: GameDesign
    design_source: str
    story: StoryBible | None = None
    story_source: str = ""
    builds: list[Build] = field(default_factory=list)
    art: dict[str, ArtInfo] = field(default_factory=dict)  # the latest build's
    screen: tuple[int, int] = (320, 180)
    scale: int = 1
    credits: list[str] = field(default_factory=list)
    code: GameCode | None = None  # the latest build's
    qa: QASuite | None = None  # the latest build's suite
    qa_export: bytes | None = None  # test_game_qa.py
    decisions: list[Decision] = field(default_factory=list)
    play: str | None = None
    research: ResearchRecord | None = None  # L2.2: similar games studied with web search
    research_source: str = ""
    plan: PlanRecord | None = None  # L2.2: the game plan of the current design
    plan_source: str = ""
    levels: list[LevelRecord] = field(default_factory=list)  # L2.3: the levels as data
    levels_source: str = ""
    level_files: dict[int, bytes] = field(default_factory=dict)  # what the SDK loads, per level
    level_previews: dict[int, bytes] = field(default_factory=dict)  # map previews (PNG)
    level_play: dict[int, dict] = field(default_factory=dict)  # the latest build's playtest
    motion: MotionRecord | None = None  # L2.4: the motion design of the current design
    motion_source: str = ""
    motion_gif: bytes | None = None  # the latest build's rows playing
    structure: GameStructure | None = None  # L2.5: levels in order, carries, saved, win
    structure_source: str = ""
    menus: MenuRecord | None = None  # L2.5: the menus as data (the latest build's version)
    menus_source: str = ""
    menus_file: bytes | None = None  # menus.json as the SDK loads it (theme in the art's colours)
    menu_shots: dict[str, bytes] = field(default_factory=dict)  # screen id -> PNG
    title_art: dict[str, bytes] = field(default_factory=dict)  # "title-art", "emblem" -> PNG

    @property
    def latest(self) -> Build | None:
        return self.builds[-1] if self.builds else None


# --- concepts --------------------------------------------------------------------------------


def make_entry(ident: str, type_: str, title: str, description: str, tags, body: str,
               sources: list[str], resource: str | None = None,
               status: str = "stable") -> Entry:  # fmt: skip
    fields = {
        "type": type_,
        "title": title,
        "description": re.sub(r"\s+", " ", description).strip()[:300],
        "tags": sorted({str(t).lower() for t in tags if str(t).strip()}),
        "sources": [Source(resource="spriteforge game", id=s) for s in sources if s],
        "status": status,
    }
    if resource:
        fields["resource"] = resource
    return Entry(id=ident, metadata=Metadata(**fields), body=body.strip() + "\n")


def bullets(items, empty: str = "- (none)") -> str:
    return "\n".join(f"- {i}" for i in items) or empty


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "x"


def safe_text(text: str) -> str:
    """Text the human or a model wrote, as it may go into the record."""
    text = redact(text)
    try:
        ensure_no_secrets(text)
    except UnsafeKnowledge:
        return f"({WITHHELD})"
    return text


def code_span(text: str) -> str:
    return f"`{text}`" if text else "-"


def game_concept(rec: GameRecord, modules: list[Module]) -> Entry:
    d, b = rec.design, rec.latest
    sections = []
    if rec.research:
        sections.append("[Research](/research/overview.md): similar games, patterns, feel numbers")
    if rec.plan:
        sections.append("[Game plan](/plan/overview.md): the human's requirements, movement "
                        "numbers, mechanics, levels, feel and animations")  # fmt: skip
    sections += [
        "[Rules](/design/rules.md): controls, rules, progression, difficulty, win and lose",
        "[Events and state](/design/events.md): what every build emits and exposes",
        "[Entities](/entities/index.md): everything the player sees",
    ]
    if rec.structure:
        sections.append("[Structure](/design/structure.md): the levels in order, what carries "
                        "over and what is saved")  # fmt: skip
    if rec.menus:
        sections.append("[Menus](/menus/overview.md): every screen around play, the theme and "
                        "the title art")  # fmt: skip
    if rec.art:
        sections.append("[Art style](/art/style.md) and [locked assets](/art/assets/index.md)")
    if modules:
        sections.append("[Code](/code/overview.md): files, scenes and what each one does")
    if rec.qa:
        sections.append("[QA tests](/qa/suite.md): tests written from the design")
    if b:
        sections.append(f"[Builds](/builds/index.md): the latest is [v{b.version}]"
                        f"(/builds/v{b.version}.md)")  # fmt: skip
    if rec.decisions:
        sections.append("[Decisions](/decisions/index.md): the human's words at each gate")
    if rec.story:
        sections.append("[Story](/story/overview.md), [characters](/characters/index.md) and "
                        "[world](/world/index.md)")  # fmt: skip
    if b:
        qa = b.report.get("qa") or {}
        status = (
            f"Build v{b.version} (design v{b.report.get('design_version', 1)}): "
            + ("all design checks pass" if b.report.get("passed") else "some design checks fail")
            + (f"; QA {qa['passed']}/{qa['passed'] + qa['failed']} tests pass" if qa else "")
            + f". Play: {code_span(rec.play or '')}"
        )
    else:
        status = "Not built yet: the design is waiting for approval."
    body = f"""
# {d.title}
{d.pitch}

# At a glance
{d.camera} camera, a {rec.screen[0]}x{rec.screen[1]} screen. {d.controls}. A full session lasts
{d.session_seconds} s. Story: {"yes, see [the story](/story/overview.md)" if rec.story else "none (the human did not ask for one)"}.

# Current version
{status}

# Sections
{bullets(sections)}
"""
    sources = [rec.design_source, b.source if b else ""]
    return make_entry("game", "Game", d.title, d.pitch, ["game", d.camera], body, sources)


def rules_concept(rec: GameRecord) -> Entry:
    d = rec.design
    body = f"""
# Camera and controls
{d.camera}. {d.controls}

# Player
{d.player}

# World
{d.world}

# Rules
{bullets(d.rules)}

# Progression
{bullets(d.progression)}

# Difficulty
{bullets(d.difficulty)}

# Win and lose
Win: {d.win}
Lose: {d.lose}
A full session lasts {d.session_seconds} s.

# HUD
{bullets(d.hud)}

# Checks
Every build must emit these [events](/design/events.md); the bot playtest counts them.
""" + "\n".join(
        f"- `{c.event}` at least {c.min_count}" + (f" by {c.by_second:g} s" if c.by_second else "")
        + f": {c.description}" for c in d.checks
    )  # fmt: skip
    return make_entry("design/rules", "Game Rules", f"{d.title}: rules",
                      f"{d.camera}; {d.controls}", ["design", "rules", d.camera], body,
                      [rec.design_source])  # fmt: skip


def events_concept(rec: GameRecord, modules: list[Module]) -> Entry:
    d = rec.design
    emitted = {}
    for m in modules:
        for e in m.events:
            emitted.setdefault(e, []).append(m.path)
    rows, seen = [], set()
    for c in d.checks:
        if c.event in seen:
            continue
        seen.add(c.event)
        when = f"at least {c.min_count}" + (f" by {c.by_second:g} s" if c.by_second else "")
        rows.append((c.event, "design check", f"{c.description} ({when})", emitted.get(c.event)))
    sdk = {"boot": "the game started", "restart": "Enter restarted the run after win or lose",
           "menu": "an sf.choose menu opened", "choice": "an sf.choose option was picked",
           "win": "the session was won", "lose": "the session was lost"}  # fmt: skip
    for e in SDK_EVENTS:
        if e not in seen:
            seen.add(e)
            rows.append((e, "SDK" if e not in {"win", "lose"} else "every game", sdk[e],
                         emitted.get(e)))  # fmt: skip
    for e, files in emitted.items():
        if e not in seen:
            seen.add(e)
            rows.append((e, "code", "emitted by the game (not a design check)", files))
    table = "\n".join(
        f"| `{e}` | {kind} | {what} | {', '.join(f'`{f}`' for f in files) if files else '-'} |"
        for e, kind, what, files in rows
    )
    set_by = {}
    for m in modules:
        for k in m.state:
            set_by.setdefault(k, []).append(m.path)
    keys = list(dict.fromkeys([*STATE_KEYS, *set_by]))
    need = {"hp": "player health", "x": "player x in pixels", "y": "player y in pixels",
            "time": "seconds since this run started (a restart resets it)",
            "level": "the level, when the design has levels",
            "score": "score, gems or kills, when the design has them"}  # fmt: skip
    state = "\n".join(
        f"| `{k}` | {need.get(k, 'set by the game')} | "
        f"{'required' if k in {'hp', 'x', 'y', 'time'} else 'optional'} | "
        f"{', '.join(f'`{f}`' for f in set_by.get(k, [])) or 'not set'} |"
        for k in keys
    )
    body = f"""
# Events
What the game reports with `sf.event(name, data)`; the playtest and the QA tests count these.

# Schema
## Events
| event | from | meaning | emitted in |
| --- | --- | --- | --- |
{table}

## sf.state
A plain object the game updates every frame; QA tests read it.

| key | meaning | contract | set in |
| --- | --- | --- | --- |
{state}
"""
    return make_entry("design/events", "Event Contract", f"{d.title}: events and state",
                      f"{len(rows)} events, {len(keys)} state keys", ["design", "events", "state"],
                      body, [rec.design_source, rec.latest.source if rec.latest else ""])  # fmt: skip


def entity_concepts(rec: GameRecord) -> list[Entry]:
    chars = {c.entity: c for c in rec.story.characters} if rec.story else {}
    entries = []
    for e in rec.design.entities:
        a = rec.art.get(e.id)
        art = (
            f"![{e.id}](/references/art/{e.id}.png)\n\n[Locked art](/art/assets/{e.id}.md): "
            f"{a.source}, made in build v{a.version}."
            if a
            else "No art yet: it is made when the game is built."
        )
        c = chars.get(e.id)
        story = (
            f"\n# Character\n[{c.name}](/characters/{e.id}.md), {c.role}. {c.personality}. "
            f"Goal: {c.goal}.\n"
            if c
            else ""
        )
        body = f"""
# {e.id}
{e.role}, {e.width}x{e.height} px on a 320x180 screen.

# Looks
{e.look}

# Behaviour
{e.behaviour}

# Animations
{", ".join(e.animations) or "none (static)"}
{story}
# Art
{art}
"""
        entries.append(make_entry(f"entities/{e.id}", "Entity", e.id, f"{e.role}: {e.look}",
                                  ["entity", e.role, e.id], body, [rec.design_source]))  # fmt: skip
    return entries


def art_concepts(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes]]:
    if not rec.art:
        return [], {}
    d = rec.design
    roles = {e.id: e.role for e in d.entities}
    hero = next((e for e in d.entities if e.role == "player"), None)
    source = rec.latest.source if rec.latest else rec.design_source
    rows = "\n".join(
        f"| [{eid}](/art/assets/{eid}.md) | {roles.get(eid, '-')} | {a.source} | v{a.version} |"
        for eid, a in rec.art.items()
    )
    style = f"""
# Direction
{d.look}

# Screen and scale
{d.camera} view, a {rec.screen[0]}x{rec.screen[1]} screen (art scale {rec.scale}); the hero is
{hero.height * rec.scale if hero else "?"} px tall. Every sprite faces right; the game flips it.

# Locked assets
| entity | role | source | made in |
| --- | --- | --- | --- |
{rows}

# Rules for new art
Match the locked art: its outline, shading and pixel size. Colours named by the design win over
a style example's. Locked art is redrawn only when the human or a change asks for it.

# Credits
{bullets(rec.credits)}
"""
    if rec.motion:
        style += "\n# Motion\nHow each thing moves and reacts: [motion design](/art/motion.md).\n"
    entries = [make_entry("art/style", "Art Style", f"{d.title}: art style", d.look,
                          ["art", "style", d.camera], style, [rec.design_source, source])]  # fmt: skip
    files = {}
    if rec.motion:
        entry, extra = motion_concept(rec, source)
        entries.append(entry)
        files |= extra
    for eid, a in rec.art.items():
        files[f"references/art/{eid}.png"] = a.portrait
        files[f"references/art/{eid}-sheet.png"] = a.sheet
        body = f"""
# {eid}
![{eid}](/references/art/{eid}.png)

The first frame at 4x. The game loads [the sheet](/references/art/{eid}-sheet.png)
(image {a.digest}). Entity: [{eid}](/entities/{eid}.md).

# Source
{a.source}, locked in build v{a.version}. Redraw it only when a change asks for this entity.

# Muse prompt
{a.prompt.strip() or "- (library art: no prompt)"}

# Measured look
{a.look}
"""
        entries.append(make_entry(f"art/assets/{eid}", "Asset", f"{eid} art",
                                  f"{roles.get(eid, 'asset')}: {a.source}, v{a.version}",
                                  ["art", "asset", roles.get(eid, ""), eid], body, [source],
                                  resource=f"/references/art/{eid}.png"))  # fmt: skip
    return entries, files


MOTION_SCHEMA = """# Schema
`/references/art/motion.json` (the Art Director's motion design, fitted by code):
* `rows[]`: `entity` (design id), `name` (the animation's name in the sheet), `plays` (when the
  game shows it: idle, move, jump, fall, land, dash, hurt, attack, break, die, form, pickup,
  other), `form` (the ability a form row shows, else ''), `frames[]` (`shows`: what the frame
  shows, `airborne`: off the ground), `fps`, `loop`.
* `forms[]`: `ability`, `looks`, `tint` (#RRGGBB when its row fails), `particles`.
* `effects[]`: `name`, `on` (the SDK moment that plays it, or 'game'), `particles`, `colors`,
  `speed` (px/s at 320x180), `flash`, `shake` (0-1), `squash` (+ flat, - tall), `seconds`.
"""


def motion_concept(rec: GameRecord, source: str) -> tuple[Entry, dict[str, bytes]]:
    """art/motion: the motion design (L2.4), what code found in the latest build's rows and
    the rows playing."""
    m = rec.motion
    checks = (rec.latest.report.get("motion") or {}).get("rows", []) if rec.latest else []
    found = "\n".join(f"* {c['entity']}.{c['row']} ({c['plays']}, {c['frames']} frames): "
                      + ("; ".join([*c["problems"], *(f"repaired: {r}" for r in c["repaired"])]) or "ok")
                      for c in checks) or "- (no build yet)"  # fmt: skip
    files = {"references/art/motion.json": (m.brief.model_dump_json(indent=1) + "\n").encode()}
    gif = ""
    if rec.motion_gif:
        files["references/art/motion.gif"] = rec.motion_gif
        gif = "![every animated row playing](/references/art/motion.gif)\n\n"
    body = (gif + motion_markdown(m).replace("# Motion design", "# Rows, forms and effects", 1)
            + f"\n# Checked by code (latest build)\n{found}\n\n{MOTION_SCHEMA}")
    entities = sorted({r.entity for r in m.brief.rows})
    links = " ".join(f"[{e}](/art/assets/{e}.md)" for e in entities if e in rec.art)
    body += f"\n# Assets\n{links or '-'}\n"
    return make_entry("art/motion", "Motion Design", f"{rec.design.title}: motion design",
                      f"{len(m.brief.rows)} animation rows, {len(m.brief.forms)} forms, "
                      f"{len(m.brief.effects)} effects", ["art", "motion", "animation"], body,
                      [rec.motion_source, source], resource="/references/art/motion.json"), files  # fmt: skip


def checks_for(rec: GameRecord, events: list[str]) -> list[str]:
    by_event = {c.event: c.id for c in rec.design.checks}
    return [f"`{e}` (check `{by_event[e]}`)" if e in by_event else f"`{e}`" for e in events]


def code_concepts(rec: GameRecord, modules: list[Module]) -> tuple[list[Entry], dict[str, bytes]]:
    if rec.code is None:
        return [], {}
    source = rec.latest.source if rec.latest else ""
    files, entries, rows = {}, [], []
    contents = rec.code.as_dict()
    for m in modules:
        stem = slug(Path(m.path).stem)
        data = contents[m.path].encode("utf-8")
        try:
            ensure_no_secrets(contents[m.path])
            files[f"references/code/{m.path}"] = data
            resource, file_text = f"/references/code/{m.path}", f"[{m.path}](/references/code/{m.path})"
        except UnsafeKnowledge:
            resource, file_text = None, f"{m.path} ({WITHHELD})"
        role = "the entry point (calls sf.start)" if m.starts else "a helper loaded before game.js"
        body = f"""
# {m.path}
{file_text}: {m.lines} lines, {role}.

# Classes and scenes
{bullets(m.classes)}
Scene keys: {", ".join(f"`{s}`" for s in m.scenes) or "-"}

# Functions
{bullets(m.functions)}

# Events emitted
{bullets(checks_for(rec, m.events))}

# sf.state keys set
{", ".join(f"`{k}`" for k in m.state) or "-"}

# Input
{"Reads sf.input (arrows/WASD, action keys; the bot plays through it)." if m.reads_input else "Does not read sf.input."}
Keys read directly: {", ".join(m.keys) or "-"}. Flags: {", ".join(f"`sf.flags.{f}`" for f in m.flags) or "-"}.
{"Defines the autoplay bot policy." if m.autoplay else ""}

# Asset ids used
{", ".join(f"[{a}](/entities/{a}.md)" if a in rec.art or any(e.id == a for e in rec.design.entities) else f"`{a}`" for a in m.assets) or "-"}
Textures it makes itself: {", ".join(f"`{t}`" for t in m.textures) or "-"}

# Menus opened
{bullets(m.menus)}
"""
        entries.append(make_entry(f"code/modules/{stem}", "Code Module", m.path,
                                  f"{m.lines} lines; scenes {', '.join(m.scenes) or '-'}; "
                                  f"{len(m.events)} events", ["code", "module", m.path, *m.scenes],
                                  body, [source], resource=resource))  # fmt: skip
        rows.append(f"| [{m.path}](/code/modules/{stem}.md) | {m.lines} | "
                    f"{', '.join(m.scenes) or '-'} | {', '.join(m.events) or '-'} |")  # fmt: skip
    total = sum(m.lines for m in modules)
    body = f"""
# Files
Loaded in this order (`game.js` last). {total} lines in all.

| file | lines | scenes | events |
| --- | --- | --- | --- |
{chr(10).join(rows)}

# The Programmer's notes
{safe_text(rec.code.notes).strip() or "-"}

# Contract
The code reads input through `sf.input`, emits the [design's events](/design/events.md), keeps
`sf.state` up to date and uses only the [locked asset ids](/art/style.md).
"""
    entries.insert(0, make_entry("code/overview", "Code Overview", f"{rec.design.title}: code",
                                 f"{len(modules)} file(s), {total} lines", ["code", "overview"],
                                 body, [source]))  # fmt: skip
    return entries, files


def step_schema() -> str:
    rows = "\n".join(
        f"| `{name}` | {f.description or ''} |" for name, f in Step.model_fields.items()
    )
    return f"| field | meaning |\n| --- | --- |\n{rows}"


def qa_concepts(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes], list[str]]:
    """The suite, one concept per test; returns the ids of tests that were dropped."""
    if rec.qa is None:
        return [], {}, []
    b = rec.latest
    results = {t["name"]: t for t in ((b.report.get("qa") or {}).get("tests", []) if b else [])}
    files, resource = {}, None
    if rec.qa_export:
        try:
            ensure_no_secrets(rec.qa_export.decode("utf-8"))
            files["references/tests/test_game_qa.py"] = rec.qa_export
            resource = "/references/tests/test_game_qa.py"
        except UnsafeKnowledge:
            pass
    entries, rows = [], []
    for test, since in zip(rec.qa.tests, rec.qa.versions, strict=False):
        ident = f"qa/tests/{slug(test.name)}"
        r = results.get(test.name)
        if r is None:
            result = "Not run yet."
        elif r["passed"]:
            result = f"Passed in build v{b.version}."
        else:
            result = f"FAILED in build v{b.version} at step {r['failed_step']}: {r['failure']}"
        steps = "\n".join(f"{n}. {describe_step(s.model_dump())}"
                          for n, s in enumerate(test.steps, 1))  # fmt: skip
        body = f"""
# {test.name}
{test.purpose}

The player {"cannot die (god mode: hurt, but HP stays at least 1)" if test.god else "can die"}.
Written for build [v{since}](/builds/v{since}.md) from the design; part of [the suite](/qa/suite.md).

# Steps
{steps}

# Last result
{result}
"""
        if not any(x.version == since for x in rec.builds):
            body = body.replace(f"[v{since}](/builds/v{since}.md)", f"v{since}")
        entries.append(make_entry(ident, "QA Test", test.name, test.purpose,
                                  ["qa", "test", "god" if test.god else "lose-path"], body,
                                  [b.source if b else ""]))  # fmt: skip
        mark = "-" if r is None else ("pass" if r["passed"] else "FAIL")
        rows.append(f"| [{test.name}](/{ident}.md) | v{since} | {mark} |")
    dropped = [f"qa/tests/{slug(n)}" for n in rec.qa.dropped]
    body = f"""
# Tests
Written by the QA Playtester from the design (never the code) and played with real key presses
after the bot playtest; every build reruns the earlier builds' tests.

| test | since | last result |
| --- | --- | --- |
{chr(10).join(rows)}

Dropped by the human: {", ".join(rec.qa.dropped) or "-"}.
{"Notes: " + "; ".join(safe_text(n) for n in rec.qa.notes) if rec.qa.notes else ""}

# Export
{"[test_game_qa.py](/references/tests/test_game_qa.py): pytest + Playwright, runs next to the build." if resource else "No export yet."}

# Schema
A test is a name, a purpose, `god` (true: the player cannot die) and a list of steps. A step is
one flat record; the fields a step does not use stay empty.

{step_schema()}
"""
    entries.insert(0, make_entry("qa/suite", "QA Suite", f"{rec.design.title}: QA tests",
                                 f"{len(rec.qa.tests)} tests", ["qa", "suite"], body,
                                 [b.source if b else ""], resource=resource))  # fmt: skip
    return entries, files, dropped


def build_concepts(rec: GameRecord) -> list[Entry]:
    answered = {}
    for n, d in enumerate(rec.decisions, 1):
        if d.asked_build and d.build:
            answered.setdefault(d.build, n)
    entries = []
    for b in rec.builds:
        r = b.report
        attempts = "\n".join(
            f"{a['attempt']}. {'passed' if a['passed'] else 'failed at ' + str(a.get('stage'))}"
            + (f" (repaired with {a['repair']})" if a.get("repair") else "")
            for a in r.get("attempts", [])
        )
        qa = r.get("qa") or {}
        rv = r.get("art_review") or {}
        review = (
            f"{rv.get('reviewed_by', '-')}; scores "
            + ", ".join(f"{k} {v}/5" for k, v in (rv.get("scores") or {}).items())
            + f"; {len(rv.get('issues', []))} issue(s), "
            f"{sum(bool(f.get('fixed')) for f in rv.get('fixes', []))} fixed"
            if rv
            else "-"
        )
        calls = r.get("model_calls") or {}
        rev = r.get("revision") or {}
        why = (
            f"[Decision {answered[b.version]}](/decisions/{answered[b.version]}.md) asked for it."
            if b.version in answered
            else "The first build of the approved design."
        )
        changed = bullets([
            f"design: {safe_text(rev['design_changes'])}" if rev.get("design_changes") else "",
            "art redrawn: " + ", ".join(c["entity"] for c in rev.get("art_changes", []))
            if rev.get("art_changes") else "",
            f"code: {safe_text(rev['code_instructions'])}" if rev.get("code_instructions") else "",
            "QA tests dropped: " + ", ".join(rev["drop_qa_tests"]) if rev.get("drop_qa_tests") else "",
            f"story: {safe_text(rev['story_changes'])}" if rev.get("story_changes") else "",
        ] if rev else [], "- nothing: a new build")  # fmt: skip
        changed = "\n".join(line for line in changed.splitlines() if line.strip() != "-")
        prev = f"Previous: [v{b.version - 1}](/builds/v{b.version - 1}.md). " if b.version > 1 else ""
        body = f"""
# Build v{b.version}
{"All design checks pass." if r.get("passed") else "Some design checks FAIL."} Design
v{r.get("design_version", 1)}, written by {r.get("chosen_by", "-")}. {prev}

# Checks
{bullets(("ok " if c.get("ok") else "FAIL ") + f"`{c['event']}` {c.get('count', '?')}/{c.get('min_count', '?')}" for c in r.get("checks", []))}
{bullets(safe_text(p) for p in r.get("problems", [])) if r.get("problems") else ""}

# Attempts
{attempts or "-"}

# QA
{f"{qa['passed']}/{qa['passed'] + qa['failed']} tests pass; see [the suite](/qa/suite.md)." if qa else "No QA tests."}

# Art review
{review}

# Cost
{calls.get("chat", 0)} Spark and {calls.get("image", 0)} Muse Image calls, about ${calls.get("estimated_cost_usd", 0)}.

# What changed and why
{why}
{changed}
"""
        entries.append(make_entry(f"builds/v{b.version}", "Build", f"Build v{b.version}",
                                  ("passed" if r.get("passed") else "failed")
                                  + (f"; QA {qa['passed']}/{qa['passed'] + qa['failed']}" if qa else ""),
                                  ["build", f"v{b.version}"], body, [b.source]))  # fmt: skip
    return entries


def decision_concepts(rec: GameRecord) -> list[Entry]:
    known = {b.version for b in rec.builds}
    entries = []
    for n, d in enumerate(rec.decisions, 1):
        if not d.build:
            build = "-"
        elif d.build not in known:
            build = f"Build v{d.build} answers it (not built yet)."
        elif d.asked_build:
            build = f"[Build v{d.build}](/builds/v{d.build}.md) answered it."
        else:
            build = f"It was about [build v{d.build}](/builds/v{d.build}.md)."
        words = safe_text(d.words).strip()
        body = f"""
# {d.gate}: {d.outcome}

# The human's words
{"> " + words.replace(chr(10), chr(10) + "> ") if words else "(no words: approved as shown)"}

# What it changed
{safe_text(d.changed) or "-"}

# Answered by
{build}
"""
        entries.append(make_entry(f"decisions/{n}", "Decision", f"Decision {n}: {d.gate}",
                                  f"{d.outcome}" + (f": {words[:120]}" if words else ""),
                                  ["decision", d.outcome], body, ["gates"]))  # fmt: skip
    return entries


def story_concepts(rec: GameRecord) -> list[Entry]:
    story, src = rec.story, rec.story_source
    if story is None:
        return []
    entities = {e.id: e for e in rec.design.entities}
    cast = "\n".join(f"- [{c.name}](/characters/{c.entity}.md) ({c.role}): {c.look}"
                     for c in story.characters)  # fmt: skip
    places = "\n".join(f"- [{loc.name}](/world/{loc.id}.md): {loc.description}"
                       for loc in story.locations)  # fmt: skip
    overview = f"""
# Logline
{story.logline}

# Premise
{story.premise}

# Setting
{story.setting}

# Tone and themes
{story.tone}. Themes: {", ".join(story.themes) or "-"}.

# Characters
{cast}

# Locations
{places or "- (none)"}

The [arc](/story/arc.md) and the [canon](/story/canon.md) no later level may break.
"""
    arc = "\n".join(f"{n}. {beat}" + (" (level 1: the current game)" if n == 1 else " (planned)")
                    for n, beat in enumerate(story.arc, 1))  # fmt: skip
    entries = [
        make_entry("story/overview", "Story", f"{story.title}: the story", story.logline,
                   ["story", *story.themes], overview, [src]),
        make_entry("story/arc", "Story Arc", f"{story.title}: the arc",
                   f"{len(story.arc)} beats; beat 1 is the current game", ["story", "arc", "levels"],
                   f"# Beats\n{arc}", [src]),
        make_entry("story/canon", "Canon", f"{story.title}: canon",
                   "Facts no later level, menu or cutscene may break", ["story", "canon"],
                   f"# Never break\n{bullets(story.canon)}", [src]),
    ]  # fmt: skip
    for loc in story.locations:
        body = f"# {loc.name}\n{loc.description}\n\n# Mood\n{loc.mood}"
        entries.append(make_entry(f"world/{loc.id}", "Location", loc.name, loc.description,
                                  ["location", loc.id], body, [src]))  # fmt: skip
    for c in story.characters:
        e = entities.get(c.entity)
        if e is None:
            continue
        art = (f"![{c.name}](/references/art/{c.entity}.png)\n\n[Locked art](/art/assets/{c.entity}.md)."
               if c.entity in rec.art else "No art yet.")  # fmt: skip
        body = f"""
# Who
{c.name}, {c.role}. {c.look}.

# Personality
{c.personality}

# Goal
{c.goal}

# Backstory
{c.backstory}

# Relationships
{c.relationships or "-"}

# In play
[Entity `{e.id}`](/entities/{e.id}.md): {e.role}, {e.behaviour}. Abilities: {c.abilities}.

# Art
{art}
"""
        entries.append(make_entry(f"characters/{c.entity}", "Character", c.name,
                                  f"{c.role}: {c.look}", ["character", c.role, e.role, e.id], body,
                                  [src, rec.design_source]))  # fmt: skip
    return entries


def web_page(url: str) -> str:
    """A reference URL as it may go into the record ('' when it looks like a secret)."""
    return url if url and safe_text(url) == url else ""


def research_concepts(rec: GameRecord) -> list[Entry]:
    record, src = rec.research, rec.research_source
    if record is None:
        return []
    r = record.research
    refs, entries = [], []
    for ref, ok in record.references:
        ident = f"research/references/{slug(ref.title)}"
        url = web_page(ref.url)
        checked = ("a search returned this page" if ok else
                   "NOT among the pages the searches returned: check it before trusting it"
                   if url else "no page")  # fmt: skip
        refs.append(f"- [{ref.title}](/{ident}.md): {ref.learn}")
        body = f"""
# What to learn
{ref.learn}

# Page
{f"<{url}>" if url else "-"} ({checked})

# Use
A reference teaches patterns only: never copy its levels, characters, story or art.
"""
        entry = make_entry(ident, "Reference Game", ref.title, ref.learn,
                           ["research", "reference", "verified" if ok else "unverified"], body, [src])  # fmt: skip
        if url:
            entry.metadata.sources.append(Source(resource=url, title=ref.title))
        entries.append(entry)
    pats = "\n".join(f"- **{x.name}**: {x.how} For this game: {x.use}" for x in r.patterns)
    feel = "\n".join(f"| {f.name} | {f.value} | {f.why} |" for f in r.feel)
    queries = ", ".join(f"`{q}`" for q in record.queries)
    body = f"""
# How it was made
{record.by}; {record.searches} search call(s){f": {queries}" if queries else ""}. {len(record.sources)} pages returned.

# Reference games
{chr(10).join(refs) or "- (none)"}

# Patterns
{pats or "- (none)"}

# Feel numbers players expect
| what | value | why |
| --- | --- | --- |
{feel}

# Pitfalls
{bullets(r.pitfalls)}
"""
    overview = make_entry("research/overview", "Research", f"{rec.design.title}: research",
                          f"{len(r.references)} reference games, {len(r.patterns)} patterns",
                          ["research", "patterns", "feel"], body, [src])  # fmt: skip
    for ref, ok in record.references:
        if ok and (url := web_page(ref.url)):
            overview.metadata.sources.append(Source(resource=url, title=ref.title))
    return [overview, *entries]


def plan_concepts(rec: GameRecord) -> list[Entry]:
    record, src = rec.plan, rec.plan_source
    if record is None:
        return []
    p, k, m, title = record.plan, record.kinematics, record.plan.movement, rec.design.title
    covers = {}
    for c in p.coverage:
        covers.setdefault(c.requirement, []).append(f"{', '.join(c.parts)}: {c.how}")
    reqs = "\n".join(f"- **{r.id}** \"{safe_text(r.text)}\": " + ("; ".join(covers.get(r.id, []))
                     or "not covered") for r in p.requirements)  # fmt: skip
    levels = "\n".join(f"- [Level {lv.n}: {lv.name}](/plan/levels/{lv.n}.md): {lv.new_idea}"
                       for lv in p.levels)  # fmt: skip
    overview = f"""
# Requirements (the human's words)
{reqs or "- (none)"}

# Style
{p.style}

# Parts
- [Movement](/plan/movement.md): numbers and the jump physics code derived from them
- [Abilities, mechanics and challenges](/plan/mechanics.md)
- [Feel and animations](/plan/feel.md)

# Levels
{levels or "- (none)"}

# Risks
{bullets(p.risks)}

# Problems code found
{bullets(record.problems, "- none")}
"""
    rows = [("move_speed", m.move_speed, "tiles/s", "running speed"),
            ("jump_height", m.jump_height, "tiles", "the highest jump; 0 = no jump"),
            ("air_time", m.air_time, "s", "take-off to landing at the same height"),
            ("dash_distance", m.dash_distance, "tiles", "0 = no dash"),
            ("dash_cooldown", m.dash_cooldown, "s", "time between dashes"),
            ("coyote_time", m.coyote_time, "s", "a jump still works after leaving a ledge")]  # fmt: skip
    if k:
        rows += [("gravity", k.gravity, "px/s^2", "8 * jump height / air time^2 (code)"),
                 ("jump_velocity", k.jump_velocity, "px/s", "take-off speed, 4h / T (code)"),
                 ("jump_distance", k.jump_distance, "px", "covered in the air at full speed (code)"),
                 ("max_rise", k.max_rise, "tiles", "the highest platform top a jump reaches (code)"),
                 ("max_gap", k.max_gap, "tiles", "the widest gap a running jump clears (code)"),
                 ("max_gap_with_dash", k.max_gap_with_dash, "tiles", "with one dash in the air (code)"),
                 ("coyote", k.coyote, "px", "ledge forgiveness at full speed (code)")]  # fmt: skip
    table = "\n".join(f"| `{n}` | {v:g} | {u} | {w} |" for n, v, u, w in rows)
    movement = f"""
# Movement
{m.move_speed:g} tiles/s; {f"jump {m.jump_height:g} tiles in {m.air_time:g} s" if k else "no jump"}; {f"dash {m.dash_distance:g} tiles every {m.dash_cooldown:g} s" if m.dash_distance else "no dash"}. Tiles are {k.tile if k else 16} px.

# Schema
| field | value | unit | meaning |
| --- | --- | --- | --- |
{table}

Levels are checked against these numbers: a platform higher than `max_rise` or a gap wider than
`max_gap` (`max_gap_with_dash` once the dash is available) cannot be crossed.
"""
    abilities = "\n".join(f"- **{a.name}**: {a.does}" + (f" ({a.numbers})" if a.numbers else "")
                          + f"; from {a.unlocked_by}" for a in p.abilities)  # fmt: skip
    mechanics = "\n".join(f"- **{x.name}**: {x.what} For: {x.purpose}" for x in p.mechanics)
    challenges = "\n".join(f"- **{c.name}**: {c.tests}" + (f" Needs: {', '.join(c.needs)}." if c.needs else "")
                           for c in p.challenges)  # fmt: skip
    feel = "\n".join(f"| {f.event} | {f.see} | {f.hear or '-'} |" for f in p.feel)
    anims = "\n".join(f"| {a.entity} | {r.name} | {r.frames} | {'loop' if r.loop else 'once'} | {r.shows} |"
                      for a in p.animations for r in a.rows)  # fmt: skip
    entries = [
        make_entry("plan/overview", "Game Plan", f"{title}: game plan",
                   f"{len(p.requirements)} requirements, {len(p.levels)} levels",
                   ["plan", "requirements"], overview, [src]),
        make_entry("plan/movement", "Movement", f"{title}: movement and kinematics",
                   f"{m.move_speed:g} tiles/s" + (f", jump {m.jump_height:g} tiles, gaps up to {k.max_gap} tiles" if k else ""),
                   ["plan", "movement", "kinematics"], movement, [src]),
        make_entry("plan/mechanics", "Mechanics", f"{title}: abilities, mechanics, challenges",
                   f"{len(p.abilities)} abilities, {len(p.mechanics)} mechanics, {len(p.challenges)} challenges",
                   ["plan", "mechanics", "abilities", "challenges"],
                   f"# Abilities\n{abilities or '- (none)'}\n\n# Mechanics\n{mechanics or '- (none)'}\n\n"
                   f"# Challenge vocabulary\n{challenges or '- (none)'}", [src]),
        make_entry("plan/feel", "Feel", f"{title}: feel and animations",
                   "what the player sees and hears; animation rows per entity", ["plan", "feel", "animation"],
                   "# Feedback\n| event | see | hear |\n| --- | --- | --- |\n" + feel
                   + "\n\n# Animations\n| entity | row | frames | plays | shows |\n| --- | --- | --- | --- | --- |\n"
                   + anims, [src]),
    ]  # fmt: skip
    vocabulary = {c.name.casefold(): c for c in p.challenges}
    for lv in p.levels:
        tests = "\n".join(f"- **{name}**: " + (vocabulary[name.casefold()].tests
                          if name.casefold() in vocabulary else "(not in the vocabulary)")
                          for name in lv.challenges)  # fmt: skip
        body = f"""
# Teaches
{lv.teaching_goal}

# New idea
{lv.new_idea}

# Challenges
{tests or "- (none)"}

# Needs
{", ".join(lv.abilities) or "-"}. See [movement](/plan/movement.md) for what a jump can cross.

# Difficulty
{lv.difficulty} of 10
"""
        entries.append(make_entry(f"plan/levels/{lv.n}", "Level Plan", f"Level {lv.n}: {lv.name}",
                                  f"teaches {lv.teaching_goal}; new: {lv.new_idea}",
                                  ["plan", "level", f"level-{lv.n}"], body, [src]))  # fmt: skip
    return entries


LEVEL_SCHEMA = """| field | meaning |
| --- | --- |
| `n`, `name` | the level's number and name |
| `cols`, `rows` | the map's size in tiles (16 px at scale 1) |
| `map` | the tile rows, top first; a character is a tile in `tiles`, ' ' is empty |
| `tiles` | character -> `kind` (solid, one_way, hazard) and the `entity` that draws it |
| `things` | `id`, `kind` (crate, breakable, pickup, enemy, checkpoint, exit), `entity`, `col`, `row` (bottom cell), `cells`, `needs` (ability that opens or beats it), `opened_by` (touch, dash, never), `holds` (what a crate drops), `gives` (the ability a pickup gives), `patrol` (an enemy's columns) |
| `start`, `player` | the player's start cell and entity |
| `route` | the bot's steps code found: walk, jump or fall (take-off `x0`, speed `vx`, `wait`, `dash`), touch, dash, collect, exit |
| `exclusive` | abilities that replace each other |"""


def level_concepts(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes]]:
    """levels/<n>: each level as data (L2.3), its map preview and what code and the bot found."""
    entries, files = [], {}
    plans = {lv.n: lv for lv in rec.plan.plan.levels} if rec.plan else {}
    for r in rec.levels:
        s, n = r.spec, r.spec.n
        lp = plans.get(n)
        play = rec.level_play.get(n) or {}
        resource = None
        if n in rec.level_files:
            files[f"references/levels/{n}.json"] = rec.level_files[n]
            resource = f"/references/levels/{n}.json"
        if n in rec.level_previews:
            files[f"references/levels/{n}.png"] = rec.level_previews[n]
        # A thing with no entity of its own (a checkpoint the design has no art for) is drawn
        # by the SDK: no link (live 2026-11-03: /entities/.md failed the bundle's validation).
        ids = {x.id for x in rec.design.entities}
        shows = lambda eid: f"[{eid}](/entities/{eid}.md)" if eid in ids else (f"`{eid}`" if eid else "drawn by the SDK")
        legend = "\n".join(f"| `{e.char}` | {e.kind} | {shows(e.entity)} | {e.needs or '-'} | "
                           f"{e.opened_by} | {e.holds or '-'} | {e.gives or '-'} |" for e in s.legend)  # fmt: skip
        route = ", ".join(st["do"] + (f" {st['thing']}" if st.get("thing") else "")
                          for st in r.route if st["do"] != "walk")  # fmt: skip
        played = (f"cleared by the bot in {play.get('game_seconds')} game seconds" if play.get("cleared")
                  else f"not cleared ({play.get('ended')})" if play else "not played yet")  # fmt: skip
        body = f"""
# Plan
{f"Teaches {lp.teaching_goal}. New idea: {lp.new_idea}. [Level plan](/plan/levels/{n}.md)" if lp else "-"}

# Map
{s.notes.strip() or "-"}

""" + ("[Map preview](/references/levels/" + str(n) + ".png) (the bot's route as a line)\n\n" if n in rec.level_previews else "") + f"""```
{chr(10).join(s.rows)}
```

| char | kind | entity | needs | opened by | holds | gives |
| --- | --- | --- | --- | --- | --- | --- |
{legend}

Challenges: {", ".join(f"{c.name} (column {c.column})" for c in s.challenges) or "-"}. Events: {", ".join(s.events) or "-"}.

# Code's check
{"The exit can be reached with the plan's movement" if r.solved else "The exit CANNOT be reached"}; difficulty {r.difficulty:g} (code's measure). Bot route: {route or "-"}.

Problems:
{bullets([safe_text(p) for p in r.problems], "- none")}

# Playtest
{played}{"; missing events: " + ", ".join(play.get("missing", [])) if play.get("missing") else ""}

# Schema
{LEVEL_SCHEMA}
"""
        entries.append(make_entry(f"levels/{n}", "Level", f"Level {n}: {s.name}",
                                  f"{len(s.rows[0]) if s.rows else 0}x{len(s.rows)} tiles; "
                                  + (f"teaches {lp.teaching_goal}" if lp else s.notes),
                                  ["level", f"level-{n}"], body, [rec.levels_source], resource))  # fmt: skip
    return entries, files


def structure_concept(rec: GameRecord) -> list[Entry]:
    """design/structure (L2.5): the levels in order, what carries over and what is saved."""
    s = rec.structure
    if s is None:
        return []
    links = ", ".join(f"[level {lv.n}](/levels/{lv.n}.md)" for lv in s.levels
                      if any(r.spec.n == lv.n for r in rec.levels))  # fmt: skip
    body = structure_markdown(s).replace("# Game structure", "# Structure", 1)
    if links:
        body += f"\nLevels as data: {links}\n"
    if rec.menus:
        body += "\nThe SDK's flow plays it: see [the menus](/menus/overview.md).\n"
    return [make_entry("design/structure", "Game Structure", f"{rec.design.title}: structure",
                       f"{len(s.levels)} level(s); win: {s.win}", ["design", "structure", "levels"],
                       body, [rec.structure_source])]  # fmt: skip


MENUS_SCHEMA = """# Schema
`/references/menus.json` (window.SF_MENUS, what the SDK's flow loads):
* `title`, `subtitle`: the title screen's words (the SDK draws all text).
* `theme`: `panel`, `border`, `text`, `highlight`, `dim` (#RRGGBB, fitted by code to the locked
  art's measured colours, text contrast checked) and `style` (framed, double, solid).
* `screens.<id>`: `title`, `subtitle`, `items[]` (`label`, `action`). Screen ids: title, main,
  pause, settings, controls, credits, level_select, results, game_over, victory.
* `levels[]`: `n`, `name`, `goal` (the structure); `can_lose`; `controls[]`; `credits[]`;
  `difficulty` (name -> the multiplier the game reads as sf.settings.multiplier); `art`
  (`title`, `emblem`: image ids in assets.json, role menu).
Actions: """ + ", ".join(f"`{a}` ({w})" for a, w in ACTIONS.items()) + "."


def menu_concepts(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes]]:
    """menus/overview and menus/<screen> (L2.5): the menus as data, their screenshots and the
    title art."""
    m = rec.menus
    if m is None:
        return [], {}
    files, resource = {}, None
    if rec.menus_file:
        files["references/menus.json"] = rec.menus_file
        resource = "/references/menus.json"
    pictures = ""
    for part, name in (("title-art", "title"), ("emblem", "emblem")):
        if part in rec.title_art:
            files[f"references/art/{name}.png"] = rec.title_art[part]
            pictures += f"![{part.replace('-', ' ')}](/references/art/{name}.png) "
    report = (rec.latest.report.get("menus") or {}) if rec.latest else {}
    art_source = (report.get("title_art") or {}).get("source", "")
    screens = "\n".join(f"- [{s.id}](/menus/{s.id}.md): " + (", ".join(i.label for i in s.items) or "-")
                         for s in m.spec.screens)  # fmt: skip
    body = (
        f"{pictures}\n\n" if pictures else ""
    ) + menus_markdown(m.spec, m.notes, m.problems).replace(f"# Menus: {m.spec.title}", "# Menus", 1) + f"""
# Title art
{art_source or "(not built yet)"}

# Screens
{screens}

# Written by
{m.by}{" (asked again once with code's problems)" if m.retried else ""}; {m.why or "-"}

{MENUS_SCHEMA}
"""
    entries = [make_entry("menus/overview", "Menus", f"{rec.design.title}: menus",
                          f"{len(m.spec.screens)} screens rendered by the SDK; theme "
                          f"{m.spec.theme.panel} / {m.spec.theme.highlight}",
                          ["menus", "ui", "title"], body, [rec.menus_source], resource)]  # fmt: skip
    for s in m.spec.screens:
        shot = ""
        if s.id in rec.menu_shots:
            files[f"references/menus/{s.id}.png"] = rec.menu_shots[s.id]
            shot = f"![the {s.id} screen](/references/menus/{s.id}.png)\n\n"
        items = "\n".join(f"{i}. {it.label} ({it.action}: {ACTIONS.get(it.action, '?')})"
                           for i, it in enumerate(s.items, 1)) or "- (no items)"  # fmt: skip
        body = f"""{shot}# {s.title or m.spec.title}
{s.subtitle or ""}

# Items
{items}

Part of [the menus](/menus/overview.md).
"""
        entries.append(make_entry(f"menus/{s.id}", "Menu Screen", f"Menu: {s.id.replace('_', ' ')}",
                                  f"{len(s.items)} items: " + ", ".join(i.label for i in s.items),
                                  ["menus", s.id], body, [rec.menus_source], resource))  # fmt: skip
    return entries, files


def bundle(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes]]:
    """Every concept and file of a game's record."""
    modules = read_code(rec.code.as_dict()) if rec.code else []
    art, art_files = art_concepts(rec)
    code, code_files = code_concepts(rec, modules)
    qa, qa_files, _ = qa_concepts(rec)
    levels, level_files = level_concepts(rec)
    menus, menu_files = menu_concepts(rec)
    entries = [game_concept(rec, modules), rules_concept(rec), events_concept(rec, modules),
               *entity_concepts(rec), *art, *code, *qa, *build_concepts(rec),
               *decision_concepts(rec), *story_concepts(rec), *research_concepts(rec),
               *plan_concepts(rec), *levels, *structure_concept(rec), *menus]  # fmt: skip
    return entries, art_files | code_files | qa_files | level_files | menu_files


def portrait_of(sheet: bytes, frame: tuple[int, int] | None) -> Filled:
    """A Filled stand-in for art read back from files (sync from a run or a folder)."""
    kind = "sprite" if frame else "image"
    if frame is None:
        w, h = Image.open(io.BytesIO(sheet)).size
        frame = (w, h)
    return Filled(png=sheet, source="", reasons=[], meta={"frame": list(frame)}, kind=kind)
