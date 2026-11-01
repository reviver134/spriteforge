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
from .qa import SDK_EVENTS, STATE_KEYS, QASuite, Step
from .qa_runtime import describe_step
from .story import StoryBible

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
    sections = [
        "[Rules](/design/rules.md): controls, rules, progression, difficulty, win and lose",
        "[Events and state](/design/events.md): what every build emits and exposes",
        "[Entities](/entities/index.md): everything the player sees",
    ]
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
    entries = [make_entry("art/style", "Art Style", f"{d.title}: art style", d.look,
                          ["art", "style", d.camera], style, [rec.design_source, source])]  # fmt: skip
    files = {}
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


def bundle(rec: GameRecord) -> tuple[list[Entry], dict[str, bytes]]:
    """Every concept and file of a game's record."""
    modules = read_code(rec.code.as_dict()) if rec.code else []
    art, art_files = art_concepts(rec)
    code, code_files = code_concepts(rec, modules)
    qa, qa_files, _ = qa_concepts(rec)
    entries = [game_concept(rec, modules), rules_concept(rec), events_concept(rec, modules),
               *entity_concepts(rec), *art, *code, *qa, *build_concepts(rec),
               *decision_concepts(rec), *story_concepts(rec)]  # fmt: skip
    return entries, art_files | code_files | qa_files


def portrait_of(sheet: bytes, frame: tuple[int, int] | None) -> Filled:
    """A Filled stand-in for art read back from files (sync from a run or a folder)."""
    kind = "sprite" if frame else "image"
    if frame is None:
        w, h = Image.open(io.BytesIO(sheet)).size
        frame = (w, h)
    return Filled(png=sheet, source="", reasons=[], meta={"frame": list(frame)}, kind=kind)
