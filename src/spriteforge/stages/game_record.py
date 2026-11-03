"""A gated run's game record, read from its artifacts and gates (for its OKF bundle).

The stage writes the bundle (`runs/<id>/game/db`) from this after every step that changes
the game: a design version, a story, a build, a human decision. `spriteforge game db sync`
calls the same function, so a bundle can always be rebuilt from the run.

Stories: `game/story/v<S>-<trigger>.json`, S counting up; the trigger (`design-v2`, `gate-5`)
makes writing one idempotent when a step is re-entered. Runs from M11.1 kept one story per
design version (`game/design/v<K>-story.json`); those still count.
"""

import json
import re

from spriteforge.core.gates import Gate, Gates
from spriteforge.game.bundle import Build, Decision, GameRecord
from spriteforge.game.codegen import GameCode
from spriteforge.game.dbsource import build_manifest, credits, read_art
from spriteforge.game.design import GameDesign
from spriteforge.game.levels import LevelRecord
from spriteforge.game.menus import SCREENS, MenuRecord
from spriteforge.game.motion import MotionRecord
from spriteforge.game.plan import PlanRecord
from spriteforge.game.qa import QASuite
from spriteforge.game.research import ResearchRecord
from spriteforge.game.revision import Revision
from spriteforge.game.story import StoryBible
from spriteforge.game.structure import GameStructure

LEGACY_STORY = re.compile(r"game/design/v(\d+)-story\.json")
STORY = re.compile(r"game/story/v(\d+)-([a-z0-9-]+)\.json")
REPORT = re.compile(r"game/v(\d+)/report\.json")
DESIGN = re.compile(r"game/design/v(\d+)\.json")
PLAN = re.compile(r"game/plan/v(\d+)\.json")
MOTION = re.compile(r"game/design/v(\d+)-motion\.json")
LEVELS = re.compile(r"game/levels/v(\d+)/levels\.json")
MENUS = re.compile(r"game/menus/v(\d+)\.json")
SOUNDS = re.compile(r"game/sounds/v(\d+)\.json")
STRUCTURE = re.compile(r"game/design/v(\d+)-structure\.json")
RESEARCH = "game/research.json"


def story_names(names) -> list[str]:
    """Every story artifact, oldest first (M11.1 per-design stories, then numbered ones)."""
    legacy = sorted((int(m[1]), n) for n in names if (m := LEGACY_STORY.fullmatch(n)))
    new = sorted((int(m[1]), n) for n in names if (m := STORY.fullmatch(n)))
    return [n for _, n in legacy] + [n for _, n in new]


def story_for_trigger(names, trigger: str) -> str | None:
    return next((n for n in names if (m := STORY.fullmatch(n)) and m[2] == trigger), None)


def next_story_name(names, trigger: str) -> str:
    count = sum(1 for n in names if STORY.fullmatch(n))
    return f"game/story/v{count + 1}-{trigger}.json"


def latest_version(names, pattern: re.Pattern) -> int:
    return max((int(m[1]) for n in names if (m := pattern.fullmatch(n))), default=0)


def shown(ctx, gate: Gate, pattern: re.Pattern) -> int | None:
    """The version a gate showed (its design or build report among its artifacts)."""
    by_id = {a.id: name for name, a in ctx.artifacts.items()}
    found = [int(m[1]) for i in gate.artifacts_to_show
             if (m := pattern.fullmatch(by_id.get(i, "")))]  # fmt: skip
    return max(found) if found else None


def gate_revision(ctx, n: int, version: int | None = None) -> Revision | None:
    """What the Director made of gate n's feedback (runs before L2.1 kept it only as the next
    build's revision.json)."""
    for name in (f"game/feedback/gate-{n}-revision.json", f"game/v{(version or 0) + 1}/revision.json"):
        if name in ctx.artifacts:
            return Revision.model_validate(ctx.read(name))
    return None


def decisions(ctx) -> list[Decision]:
    """The human's decisions: the G1 brief, then every answered design, menus, playtest and
    final (G5) gate."""
    brief = ctx.brief
    out = [Decision(
        gate="G1 brief", outcome="approved", words="\n".join(n for n in brief.notes if n.strip()),
        changed=f"Genre {brief.genre}; camera {brief.perspective}; story: {brief.story}; "
                + (f"research by {ctx.read(RESEARCH)['by']}" if RESEARCH in ctx.artifacts
                   else "no research"),
    )]  # fmt: skip
    for n, g in enumerate(Gates(ctx.store).all(ctx.run_id), 1):
        if g.response is None or g.stage not in {"design", "menus", "playtest", "final"}:
            continue
        words = (g.response.text or "").strip()
        if g.stage == "menus":
            v = shown(ctx, g, REPORT) or 1
            gate = f"G3 menus (build v{v})"
            if g.status == "approved":
                out.append(Decision(gate, "approved", words, f"the menus of build v{v} approved: "
                                    "its playtest (G4) follows", v))  # fmt: skip
            else:
                out.append(Decision(gate, "changes requested", words, "the menus revised: build "
                                    f"v{v + 1} with the same code and art", v + 1, True))  # fmt: skip
            continue
        added = story_for_trigger(ctx.artifacts, f"gate-{n}")
        if g.stage == "design":
            k = shown(ctx, g, DESIGN) or 1
            gate = f"G2 design v{k}"
            if g.status == "approved" and added:
                out.append(Decision(gate, "story added", words, f"a story was written for design v{k}"))
            elif g.status == "approved":
                out.append(Decision(gate, "approved", words, f"design v{k} approved: build v1 is made from it", 1, True))
            else:
                story = " and its story" if story_for_trigger(ctx.artifacts, f"design-v{k + 1}") else ""
                out.append(Decision(gate, "changes requested", words, f"design v{k + 1}{story}"))
            continue
        v = shown(ctx, g, REPORT) or 1
        gate = f"G5 final review (build v{v})" if g.stage == "final" else f"G4 playtest v{v}"
        if g.status == "approved" and g.stage == "final":
            out.append(Decision(gate, "approved", words, f"the game is finished with build v{v}: its "
                                "record human-reviewed, the lessons in the shared memory", v))  # fmt: skip
        elif g.status == "approved" and added:
            out.append(Decision(gate, "story added", words, "a story was written for the game", v))
        elif g.status == "approved":
            out.append(Decision(gate, "approved", words, f"build v{v} approved: the final review "
                                "(G5) follows", v))  # fmt: skip
        else:
            rev = gate_revision(ctx, n, v)
            if rev and rev.story_only:
                out.append(Decision(gate, "story changed", words, f"story: {rev.story_changes}", v))
                continue
            parts = [
                f"design: {rev.design_changes}" if rev and rev.design_changes else "",
                "art redrawn: " + ", ".join(c.entity for c in rev.art_changes)
                if rev and rev.art_changes else "",
                f"code: {rev.code_instructions}" if rev and rev.code_instructions else "",
                "QA tests dropped: " + ", ".join(rev.drop_qa_tests)
                if rev and rev.drop_qa_tests else "",
                f"story: {rev.story_changes}" if rev and rev.story_changes else "",
                f"menus: {rev.menu_changes}" if rev and rev.menu_changes else "",
            ]  # fmt: skip
            changed = "; ".join(p for p in parts if p) or "a new build"
            out.append(Decision(gate, "changes requested", words, changed, v + 1, True))
    return out


def run_record(ctx, pending: tuple[int, dict] | None = None) -> GameRecord:
    """The record of a run so far. `pending`: (version, report) of a build whose report is
    not saved yet (the bundle is written just before it)."""
    names = ctx.artifacts
    k = latest_version(names, DESIGN)
    rec = GameRecord(design=GameDesign.model_validate(ctx.read(f"game/design/v{k}.json")),
                     design_source=f"game/design/v{k}.json")  # fmt: skip
    if stories := story_names(names):
        rec.story, rec.story_source = StoryBible.model_validate(ctx.read(stories[-1])), stories[-1]
    if RESEARCH in names:
        rec.research, rec.research_source = ResearchRecord.model_validate(ctx.read(RESEARCH)), RESEARCH
    # The plan of the current design (the latest one at or before it).
    plans = sorted(int(m[1]) for n in names if (m := PLAN.fullmatch(n)) and int(m[1]) <= k)
    if plans:
        name = f"game/plan/v{plans[-1]}.json"
        rec.plan, rec.plan_source = PlanRecord.model_validate(ctx.read(name)), name
    motions = sorted(int(m[1]) for n in names if (m := MOTION.fullmatch(n)) and int(m[1]) <= k)
    if motions:
        name = f"game/design/v{motions[-1]}-motion.json"
        rec.motion, rec.motion_source = MotionRecord.model_validate(ctx.read(name)), name
    reports = {int(m[1]): ctx.read(n) for n in names if (m := REPORT.fullmatch(n))}
    if pending:
        reports[pending[0]] = pending[1]
    rec.builds = [Build(v, f"game/v{v}/report.json",
                        {k2: x for k2, x in r.items() if k2 != "game_db"})
                  for v, r in sorted(reports.items())]  # fmt: skip
    rec.decisions = decisions(ctx)
    structures = sorted(int(m[1]) for n in names if (m := STRUCTURE.fullmatch(n)) and int(m[1]) <= k)
    if structures:
        name = f"game/design/v{structures[-1]}-structure.json"
        rec.structure, rec.structure_source = GameStructure.model_validate(ctx.read(name)), name
    menus_built = rec.builds[-1].report.get("menus_version") if rec.builds else None
    if menu_set := menus_built or latest_version(names, MENUS):
        name = f"game/menus/v{menu_set}.json"
        rec.menus, rec.menus_source = MenuRecord.model_validate(ctx.read(name)), name
    built = rec.builds[-1].report.get("levels_version") if rec.builds else None
    if level_set := built or latest_version(names, LEVELS):
        index = ctx.read(f"game/levels/v{level_set}/levels.json")
        rec.levels = [LevelRecord.model_validate(ctx.read(f"game/levels/v{level_set}/level-{n}.json"))
                      for n in index["levels"]]  # fmt: skip
        rec.levels_source = f"game/levels/v{level_set}/levels.json"
    if not rec.builds:
        return rec
    v = rec.builds[-1].version
    prefix, report = f"game/v{v}", rec.builds[-1].report
    rec.play = report.get("play")
    build = ctx.root / prefix / "build"
    entities = {e.id for e in rec.design.entities}
    sheets = {a["entity"]: ctx.data(f"{prefix}/art/{a['entity']}.png") for a in report.get("art", [])
              if f"{prefix}/art/{a['entity']}.png" in names and a["entity"] in entities}  # fmt: skip
    records = {eid: ctx.read(f"{prefix}/art/{eid}.json") for eid in sheets
               if f"{prefix}/art/{eid}.json" in names}  # fmt: skip
    manifest = build_manifest(build)
    rec.art = read_art(sheets, manifest, records, v)
    art = ctx.read(f"{prefix}/art.json") if f"{prefix}/art.json" in names else {}
    rec.screen = tuple(manifest.get("screen") or art.get("screen") or (320, 180))
    rec.scale = int(manifest.get("scale") or art.get("scale") or 1)
    rec.credits = credits(build)
    if f"{prefix}/motion.gif" in names:
        rec.motion_gif = ctx.data(f"{prefix}/motion.gif")
    if f"{prefix}/code.json" in names:
        rec.code = GameCode.model_validate(ctx.read(f"{prefix}/code.json"))
    if f"{prefix}/qa-tests.json" in names:
        rec.qa = QASuite.model_validate(ctx.read(f"{prefix}/qa-tests.json"))
    if f"{prefix}/test_game_qa.py" in names:
        rec.qa_export = ctx.data(f"{prefix}/test_game_qa.py")
    if report.get("menus_version"):
        rec.menu_shots = {sid: ctx.data(f"{prefix}/menus/{sid}.png") for sid in SCREENS
                          if f"{prefix}/menus/{sid}.png" in names}  # fmt: skip
        rec.title_art = {part: ctx.data(f"{prefix}/menus/{part}.png") for part in ("title-art", "emblem")
                         if f"{prefix}/menus/{part}.png" in names}  # fmt: skip
        if (build / "menus.json").exists():
            rec.menus_file = (build / "menus.json").read_bytes()
    if rec.levels and report.get("levels_version"):
        played = {lv["n"]: lv for lv in report.get("levels", [])}
        data = {}
        if (build / "levels.json").exists():
            data = {lv["n"]: lv for lv in json.loads((build / "levels.json").read_text(encoding="utf-8"))["levels"]}
        for r in rec.levels:
            n = r.spec.n
            if f"{prefix}/levels/level-{n}.png" in names:
                rec.level_previews[n] = ctx.data(f"{prefix}/levels/level-{n}.png")
            if n in data:
                rec.level_files[n] = (json.dumps(data[n], indent=1) + "\n").encode()
            rec.level_play[n] = played.get(n, {})
    return rec
