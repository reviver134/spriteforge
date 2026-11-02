"""G2 design gate and G4 playtest gate: Muse Spark writes the game from the brief.

After the G1 questions:
  game/research.json/.md     Game Designer with web search (when G1 says research, the
                             default): reference games with the pages the searches returned,
                             patterns, feel numbers, pitfalls; code marks each reference URL
                             verified only when a search returned it (once per run)
  game/plan/vK.json/.md      Game Designer: the game plan for design vK: the human's requirements
                             word for word, movement numbers and the kinematics code derives,
                             abilities, mechanics, challenges, levels, feel, animations,
                             coverage, risks, and the problems code found (asked again once)
  game/design/vK.json/.md    Game Designer: rules, entities, progression, difficulty, checks,
                             written from plan vK
  game/design/vK-story.json/.md  Game Designer: the story bible (characters tied to entities,
                             world, arc, canon), shown at G2 and revised with the design
  game/levels/vL/level-n.json/.md  Level/Tile Designer (L2.3, side-view games with a plan): each
                             level as data, fitted and checked by code with the plan's movement
                             (reach.py), with the route the bot plays; levels.json indexes a set.
                             v1 is written after G2; feedback on one level makes a new set with
                             only that level revised; a design change re-checks every level
  game/db/                   the game database: the canon as OKF entries (story, characters with
                             their locked art, world, gameplay, art, levels), written from the
                             artifacts; approvals promote it to human-reviewed
  game/design/vK-structure.json/.md  code: the game's structure (levels in order, what carries
                             over, what is saved, win and lose), from design vK and its plan
  gate "design"              text -> plan and design v(K+1); empty -> build v1 from the
                             approved design
  game/menus/vM.json/.md     Menu Designer (L2.5): the menus as data (screens, items, theme,
                             what the title art shows), fitted and checked by code; v1 written
                             at G2 approval, a new version per menu feedback (G3, or G4's
                             menu_changes), refitted by code when the structure changes
  game/design/vK-motion.json/.md  Art Director (L2.4): the motion design for design vK: rows
                             per entity (what each frame shows, when it plays, fps, loop),
                             forms, effects (sf.fx), fitted by code; a design change keeps the
                             rows of every entity that still exists (its art stays locked)
  game/vN/art-plan.json      Art Director: library first, Muse Image for whatever is missing
  game/vN/motion.gif/.png    every animated row playing / as filmstrips, as code checked them
  game/vN/art/<id>.*         each entity's art (+ Muse raw and request, so art stays locked)
  game/vN/art.png            contact sheet
  game/vN/code.json          the Programmer's files after static checks, playtest and repairs
  game/vN/playtest.json      the full-session playtest with the game's own bot
  game/vN/art-review.json    Art Reviewer (vision): scores, naming test, issues by sheet number;
                             review/ holds its inputs and before/after pictures of fixed art;
                             art-plan-reviewed.json the plan with the fixes (the next version
                             starts from it, so fixed art stays locked)
  game/design/vK-qa-tests.json  QA Playtester: tests written from design vK (once per version)
  game/vN/qa-tests.json      the suite build vN runs: its design's tests plus earlier versions'
                             (a regression suite), minus tests feedback dropped
  game/vN/qa.json            QA results (real key presses after the bot playtest); qa/ their
                             screenshots; test_game_qa.py the Playwright export (also in
                             build/tests/, so the play route serves it)
  game/vN/levels/level-n.png the level's map preview with the game's art and the bot's route
  game/vN/menus/<screen>.png every screen of the menus as the player sees it; menus.png all of
                             them; menus/title-art.png, emblem.png (Muse, locked once drawn, or
                             code-drawn) and title-art.json
  gate "menus"               G3, after the first build with menus: text -> the Menu Designer
                             revises the menus -> v(N+1) with the same code and art; empty -> G4
  game/vN/poster.png         (+ demo.mp4 with ffmpeg)
  game/vN/report.json        what was built and by whom, art sources, warnings, attempts, play link
  gate "playtest"            text -> Director splits it into design/art/code/level changes ->
                             v(N+1); with Level n chosen on the form, the text goes to the Level
                             Designer for level n only (same code and art, every level replayed)
                             (a design change revises the plan too; entities that still exist
                             keep their locked art, only new ones are planned);
                             Apply on an art-review suggestion (form apply_suggestion=n) -> that
                             entity is redrawn as the suggestion says (no Director call);
                             "ignore the restart test" -> that QA test is dropped (Revision);
                             empty -> approval.json, run complete
The playable build is runs/<id>/game/vN/build, served at /api/runs/<id>/play/vN/index.html.
"""

import re
from pathlib import Path

from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates, new_gate
from spriteforge.game.art import ArtPick, ArtPlan, art_sheet
from spriteforge.game.codegen import GameCode
from spriteforge.game.design import GameDesign, design_markdown, revision_prompt
from spriteforge.game.gamedb import context_pack, open_db, promote_all, sync_record
from spriteforge.game.menus import MenuRecord, menus_markdown, menus_script, refit
from spriteforge.game.level_preview import first_frame, preview
from spriteforge.game.levels import LevelRecord, evaluate, level_markdown, player_body
from spriteforge.game.motion import MotionRecord, motion_markdown
from spriteforge.game.pipeline import (
    LevelPack,
    MenuPack,
    Models,
    art_sources,
    art_summary,
    build_and_test,
    call_summary,
    game_assets,
    level_pack,
    menu_files,
    menu_pack,
    menus_report,
    motion_previews,
    motion_report,
    plan_art,
    plan_new_art,
    produce_art,
    qa_report,
    review_art,
    revise_level,
    revise_menus,
    revise_plan,
    revise_story,
    uses_levels,
    write_code,
    write_design,
    write_level,
    write_menus,
    write_motion,
    write_plan,
    write_qa_tests,
    write_research,
    write_story,
)
from spriteforge.game.plan import PlanRecord, plan_brief, plan_markdown
from spriteforge.game.qa import QASuite, QATests, merge_suite, screenshot_files, write_export
from spriteforge.game.record import record_game
from spriteforge.game.research import ResearchRecord, research_brief, research_markdown
from spriteforge.game.revision import ArtChange, LevelChange, Revision, fallback_revision
from spriteforge.game.revision import revision_prompt as director_prompt
from spriteforge.game.story import StoryBible, story_markdown
from spriteforge.game.structure import GameStructure, make_structure, structure_markdown
from spriteforge.game.title_art import TitleArt

from .asset_store import ProductionStore
from .game_record import (
    LEVELS,
    MENUS,
    REPORT,
    RESEARCH,
    latest_version,
    next_story_name,
    run_record,
    shown,
    story_for_trigger,
    story_names,
)
from .intake import GameBrief

STAGES = {"design", "playtest"}
ADD_STORY = "Add a story"
WHOLE_GAME = "The whole game"
# G2 and G4 offer a story while the game has none (named characters, a world, an arc).
STORY_FORM = {
    "type": "object",
    "properties": {
        "story": {
            "type": "string",
            "title": "Story",
            "description": "The game has no story. Add one: named characters, a world and a "
            "story arc, written from the design (one Spark call).",
            "enum": ["No story", ADD_STORY],
            "default": "No story",
        }
    },
}


class GameContext(ProductionStore):
    def __init__(self, store, run_id):
        super().__init__(store, run_id)
        self.run = store.get_run(run_id)
        self.cfg = Config.model_validate(self.run.config["settings"])
        budget = Budget(store, run_id, BudgetConfig.model_validate(self.run.config["budget"]))
        # Identical Muse requests are answered from <workspace>/cache/muse-image.
        cache = Path(store.path).parent / "cache" / "muse-image"
        self.models = Models(self.cfg, budget, self.run.config["mock"], cache_dir=cache)
        self.brief = GameBrief.model_validate(self.read("brief.json"))

    @property
    def db(self):
        """The game database (OKF corpus) next to the run's artifacts."""
        return open_db(self.root / "game" / "db", self.store, self.run_id)

    def once(self, name: str, schema, make, role: str):
        """Ask a model at most once per artifact name (re-entry after a crash reuses it)."""
        if name in self.artifacts:
            return schema.model_validate(self.read(name))
        result = make()
        self.save(name, result.model_dump(), role)
        return result

    def log(self, role: str):
        return lambda message: self.store.append_event(
            self.run_id, role, "game.progress", {"message": message.strip()}
        )


def request_text(brief: GameBrief) -> str:
    """Everything the human said in G1, as the request the design is written from."""
    answers = "\n".join(f"- {k}: {v}" for k, v in brief.answers.items() if str(v).strip())
    notes = "\n".join(f"- {n}" for n in brief.notes if n.strip())
    return (
        f"Genre: {brief.genre.rsplit('/', 1)[-1]}\nCamera: {brief.perspective}\n"
        f"World: {brief.environment}\nMood and look: {brief.mood}\nPlayer: {brief.character}\n"
        f"Core loop: {brief.core_loop}\nDifficulty: {brief.difficulty}\n"
        f"Session length: {brief.session_length}\nControls: {brief.controls}\n"
        f"References: {brief.references}\n"
        f"Answers to the follow-up questions:\n{answers or '- (none)'}\n"
        f"The human's own words (most important):\n{notes or '- (none)'}"
    )


def human_words(ctx: GameContext) -> str:
    """Everything the human said: the G1 request and their words at every G2 and G4 gate (the
    plan's requirements are copied from these)."""
    said = [g.response.text.strip() for g in Gates(ctx.store).all(ctx.run_id)
            if g.stage in STAGES and g.response and (g.response.text or "").strip()]  # fmt: skip
    return request_text(ctx.brief) + "".join(f"\n- {w}" for w in said)


def side_view(ctx: GameContext) -> bool:
    text = ctx.brief.perspective + " " + ctx.brief.genre
    return bool(re.search(r"side|platform", text, re.IGNORECASE))


def ensure_research(ctx: GameContext) -> ResearchRecord | None:
    """The research, once per run, only when the G1 brief asks for it."""
    if RESEARCH not in ctx.artifacts:
        if not ctx.brief.wants_research:
            return None
        ctx.log("Game Designer")("studying similar games with web search")
        genre = ctx.read("genre.json") if "genre.json" in ctx.artifacts else {}
        title = genre.get("title") or ctx.brief.genre.rsplit("/", 1)[-1]
        record = write_research(ctx.models, request_text(ctx.brief), title,
                                genre.get("body", ""), side_view(ctx))  # fmt: skip
        ctx.save(RESEARCH, record.model_dump(), "Game Designer")
        ctx.save(RESEARCH.removesuffix(".json") + ".md", research_markdown(record).encode(),
                 "Game Designer", "text/markdown")  # fmt: skip
    return ResearchRecord.model_validate(ctx.read(RESEARCH))


def plan_name(version: int) -> str:
    return f"game/plan/v{version}.json"


def load_plan(ctx: GameContext, version: int) -> PlanRecord | None:
    """The plan of design `version` (runs before L2.2 have none)."""
    name = plan_name(version)
    return PlanRecord.model_validate(ctx.read(name)) if name in ctx.artifacts else None


def load_plan_at(ctx: GameContext, version: int) -> PlanRecord | None:
    """The plan a design version was written from: the latest plan at or before it."""
    for k in range(version, 0, -1):
        if plan := load_plan(ctx, k):
            return plan
    return None


def ensure_plan(ctx: GameContext, version: int, make) -> PlanRecord:
    """Plan v`version`, written once (`make()`), with its Markdown."""
    name = plan_name(version)
    if name not in ctx.artifacts:
        record = make()
        ctx.save(name, record.model_dump(), "Game Designer")
        ctx.save(name.removesuffix(".json") + ".md", plan_markdown(record).encode(),
                 "Game Designer", "text/markdown")  # fmt: skip
    return PlanRecord.model_validate(ctx.read(name))


def next_plan(ctx: GameContext, version: int, old: int, changes: str,
              said: str) -> PlanRecord | None:  # fmt: skip
    """Plan v`version` revised from plan v`old` for `changes`, with what the human `said` at
    the gate (none when v`old` had none)."""
    previous = load_plan(ctx, old)
    if previous is None:
        return None
    ctx.log("Game Designer")(f"revising the game plan (v{version})")
    return ensure_plan(ctx, version, lambda: revise_plan(
        ctx.models, previous, changes, human_words(ctx), said))  # fmt: skip


def design_versions(ctx: GameContext) -> int:
    """Design versions only (not the vK-story.json or vK-qa-tests.json beside them)."""
    return sum(1 for n in ctx.artifacts if re.fullmatch(r"game/design/v\d+\.json", n))


def load_design(ctx: GameContext, version: int) -> GameDesign:
    return GameDesign.model_validate(ctx.read(f"game/design/v{version}.json"))


def save_design(ctx: GameContext, version: int, design: GameDesign) -> None:
    name = f"game/design/v{version}"
    if f"{name}.json" not in ctx.artifacts:
        ctx.save(f"{name}.json", design.model_dump(), "Game Designer")
    if f"{name}.md" not in ctx.artifacts:
        ctx.save(f"{name}.md", design_markdown(design).encode(), "Game Designer", "text/markdown")
    ensure_structure(ctx, version, design)


def structure_name(version: int) -> str:
    return f"game/design/v{version}-structure.json"


def ensure_structure(ctx: GameContext, version: int, design: GameDesign) -> GameStructure:
    """The structure of design vK (L2.5), derived by code from the design and its plan."""
    name = structure_name(version)
    if name not in ctx.artifacts:
        plan = load_plan_at(ctx, version)
        structure = make_structure(design, plan, uses_levels(design, plan))
        ctx.save(name, structure.model_dump(), "Game Designer")
        ctx.save(name.removesuffix(".json") + ".md", structure_markdown(structure).encode(),
                 "Game Designer", "text/markdown")  # fmt: skip
    return GameStructure.model_validate(ctx.read(name))


# --- menus (L2.5) ---------------------------------------------------------------------------------


def menus_name(version: int) -> str:
    return f"game/menus/v{version}.json"


def load_menus(ctx: GameContext, version: int) -> MenuRecord:
    return MenuRecord.model_validate(ctx.read(menus_name(version)))


def save_menus(ctx: GameContext, record: MenuRecord) -> int:
    version = latest_version(ctx.artifacts, MENUS) + 1
    name = menus_name(version)
    ctx.save(name, record.model_dump(), "Menu Designer")
    ctx.save(name.removesuffix(".json") + ".md",
             menus_markdown(record.spec, record.notes, record.problems).encode(),
             "Menu Designer", "text/markdown")  # fmt: skip
    return version


def ensure_menus(ctx: GameContext, design: GameDesign, design_version: int) -> int | None:
    """The menus a build of design vK uses: v1 written by the Menu Designer from the game's
    record (once), later the latest version, refitted by code when the structure changed.
    None when `game.menus` is off."""
    if not ctx.cfg.game.menus:
        return None
    structure = ensure_structure(ctx, design_version, design)
    latest = latest_version(ctx.artifacts, MENUS)
    if not latest:
        ctx.log("Menu Designer")("writing the menus: every screen, the theme and the title art")
        record = write_menus(ctx.models, design, structure,
                             context_pack(ctx.db, "menus title screen look art style", 1500),
                             f"written from design v{design_version}")  # fmt: skip
        return save_menus(ctx, record)
    record = load_menus(ctx, latest)
    again = refit(record, structure, design)
    return latest if again is record else save_menus(ctx, again)


def next_menus(ctx: GameContext, n: int, old: int, feedback: str, design: GameDesign,
               design_version: int, where: str) -> int:  # fmt: skip
    """The menus revised for gate n's feedback (once per gate: a re-entered step reuses it)."""
    pointer = f"game/feedback/gate-{n}-menus.json"
    if pointer not in ctx.artifacts:
        ctx.log("Menu Designer")("revising the menus for the feedback")
        structure = ensure_structure(ctx, design_version, design)
        record = revise_menus(ctx.models, load_menus(ctx, old), feedback, design, structure,
                              f"{where}: {feedback.strip()[:200]}")  # fmt: skip
        ctx.save(pointer, {"version": save_menus(ctx, record), "from": old}, "Menu Designer")
    return ctx.read(pointer)["version"]


def flow_script(ctx: GameContext, menus: int | None, design: GameDesign) -> dict | None:
    """The menus as the QA Playtester reads them (no theme needed)."""
    if not menus:
        return None
    record = load_menus(ctx, menus)
    return menus_script(record.spec, record.structure, design)


def previous_title(ctx: GameContext, version: int) -> TitleArt | None:
    """The previous build's title art, so an unchanged request reuses it (locked)."""
    base = f"game/v{version - 1}/menus"
    if f"{base}/title-art.json" not in ctx.artifacts or f"{base}/title-art.png" not in ctx.artifacts:
        return None
    rec = ctx.read(f"{base}/title-art.json")
    return TitleArt(ctx.data(f"{base}/title-art.png"), ctx.data(f"{base}/emblem.png"),
                    rec.get("source", ""), rec.get("prompts", {}), [], {},
                    rec.get("version") or version - 1)  # fmt: skip


def latest_story(ctx: GameContext) -> StoryBible | None:
    names = story_names(ctx.artifacts)
    return StoryBible.model_validate(ctx.read(names[-1])) if names else None


def ensure_story(ctx: GameContext, trigger: str, design: GameDesign, ask: str = "") -> str:
    """The story for `trigger` (`design-v2`, `gate-5`), written once: the latest story revised
    for `design` and `ask`, or a new one when the game has none. Only ever called when the
    human asked for a story (G1 "Yes", "Add a story", feedback asking for one)."""
    if name := story_for_trigger(ctx.artifacts, trigger):
        return name
    old = latest_story(ctx)
    log = ctx.log("Game Designer")
    if old is None:
        log("writing the story bible (the human asked for a story)")
        request = request_text(ctx.brief) + (f"\nWhat they asked for the story: {ask}" if ask else "")
        story, notes = write_story(ctx.models, design, request)
    else:
        log("revising the story bible")
        story, notes = revise_story(ctx.models, old, design,
                                    ask or "Keep the story; fit it to the revised design.")  # fmt: skip
    name = next_story_name(ctx.artifacts, trigger)
    ctx.save(name, story.model_dump(), "Game Designer")
    text = story_markdown(story) + "".join(f"\n> repaired: {n}" for n in notes)
    ctx.save(name.removesuffix(".json") + ".md", text.encode(), "Game Designer", "text/markdown")
    return name


def sync_db(ctx: GameContext, why: str, pending: tuple[int, dict] | None = None) -> dict:
    """Rewrite the game's OKF bundle from the run (only what changed)."""
    db = ctx.db
    counts = sync_record(db, run_record(ctx, pending), f"Game record: {why}")
    return {"path": str(db.root), "concepts": len(db.scan()[0]), **counts}


def story_names_shown(ctx: GameContext) -> list[str]:
    names = story_names(ctx.artifacts)
    return names[-1:]


def open_design_gate(ctx: GameContext, version: int) -> None:
    design = load_design(ctx, version)
    plan = load_plan(ctx, version)
    story = "" if latest_story(ctx) else " (no story: choose Add a story to get one)"
    problems = (f" The plan has {len(plan.problems)} problem(s) code could not fix (listed in "
                "the plan)." if plan and plan.problems else "")  # fmt: skip
    title = (
        f"G2 Game design v{version}: {design.title}{story}. Read the "
        + ("research, the " if RESEARCH in ctx.artifacts else "")
        + ("game plan, then the " if plan else "")
        + "rules, entities and checks; type what to change, or leave feedback empty and submit "
        "to approve and build the game." + problems
    )
    names = [n for n in (RESEARCH, plan_name(version)) if n in ctx.artifacts]
    names += [f"game/design/v{version}.json", *story_names_shown(ctx)]
    names += [n for n in (structure_name(version),) if n in ctx.artifacts]
    extra = {} if latest_story(ctx) else {"form_schema": STORY_FORM}
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("design", title, [ctx.artifacts[n].id for n in names],
                 empty_feedback_approves=True, **extra),
    )


def level_name(version: int, n: int) -> str:
    return f"game/levels/v{version}/level-{n}.json"


def load_levels(ctx: GameContext, version: int) -> list[LevelRecord]:
    index = ctx.read(f"game/levels/v{version}/levels.json")
    return [LevelRecord.model_validate(ctx.read(level_name(version, n))) for n in index["levels"]]


def ensure_levels(ctx: GameContext, version: int, design_version: int, count: int, make,
                  why: str) -> list[LevelRecord]:  # fmt: skip
    """Level set v`version`: each level made once by `make(n, earlier)` (re-entry after a crash
    reuses what is saved), then its index."""
    index = f"game/levels/v{version}/levels.json"
    if index in ctx.artifacts:
        return load_levels(ctx, version)
    records, changed = [], []
    for n in range(1, count + 1):
        name = level_name(version, n)
        if name not in ctx.artifacts:
            record, was_changed = make(n, records)
            ctx.save(name, record.model_dump(), "Level/Tile Designer")
            ctx.save(name.removesuffix(".json") + ".md", level_markdown(record).encode(),
                     "Level/Tile Designer", "text/markdown")  # fmt: skip
            changed += [n] if was_changed else []
        records.append(LevelRecord.model_validate(ctx.read(name)))
    ctx.save(index, {"version": version, "design_version": design_version,
                     "levels": [r.spec.n for r in records], "changed": changed, "why": why,
                     "problems": sum(len(r.problems) for r in records)}, "Level/Tile Designer")  # fmt: skip
    return records


def first_levels(ctx: GameContext, design: GameDesign, design_version: int, plan: PlanRecord) -> int:
    """Level set v1 (or the next one when a game gains levels later): every level written by
    the Level Designer from the plan and the game's record."""
    version = latest_version(ctx.artifacts, LEVELS) + 1
    log = ctx.log("Level/Tile Designer")

    def make(n: int, earlier: list[LevelRecord]):
        lp = plan.plan.levels[n - 1]
        log(f"writing level {n}: {lp.name}")
        focus = f"level {n}: {lp.name}: {lp.teaching_goal}; new idea: {lp.new_idea}"
        record = write_level(ctx.models, n, design, plan, earlier, context_pack(ctx.db, focus, 1500),
                             ctx.cfg.game.playtest_fps)  # fmt: skip
        log(f"level {n}: " + ("checked by code, no problems" if not record.problems else
                              f"{len(record.problems)} problem(s) code could not fix"))  # fmt: skip
        return record, True

    ensure_levels(ctx, version, design_version, len(plan.plan.levels), make, "written from the plan")
    return version


def next_levels(ctx: GameContext, old: int, design: GameDesign, design_version: int,
                plan: PlanRecord, changes: dict[int, str], recheck: str = "") -> int:  # fmt: skip
    """Level set v(latest + 1) from set `old`: levels in `changes` revised for the human's words;
    with `recheck` (a design change), every other level is checked again with the new design
    and plan and revised only when that finds new problems. The rest stay as they were."""
    version = latest_version(ctx.artifacts, LEVELS) + 1
    before = load_levels(ctx, old)
    log = ctx.log("Level/Tile Designer")
    fps = ctx.cfg.game.playtest_fps

    def make(n: int, earlier: list[LevelRecord]):
        record = before[n - 1] if n <= len(before) else None
        if record is None:
            log(f"writing level {n} (new in the plan)")
            return write_level(ctx.models, n, design, plan, earlier, "", fps), True
        if n in changes:
            log(f"revising level {n} for the feedback")
            return revise_level(ctx.models, record, changes[n], design, plan, earlier, fps), True
        if not recheck:
            return record, False
        again = evaluate(record.spec, n, design, plan, earlier[-1] if earlier else None,
                         player_body(design), fps, record.retried, record.notes, record.by)  # fmt: skip
        new = [p for p in again.problems if p not in record.problems]
        if not new:
            return again, False
        log(f"level {n} has new problems after the design change; revising it")
        said = f"The design changed: {recheck}. Fix what code found: " + "; ".join(new)
        return revise_level(ctx.models, again, said, design, plan, earlier, fps), True

    why = "; ".join([f"level {n}: {c}" for n, c in sorted(changes.items())]
                    + ([f"design v{design_version}: {recheck}"] if recheck else []))  # fmt: skip
    ensure_levels(ctx, version, design_version, len(plan.plan.levels), make, why)
    return version


def level_previews(ctx: GameContext, prefix: str, pack: LevelPack, design: GameDesign, art) -> dict:
    """Each level's map preview with the game's art; artifact names by level number."""
    frames = {eid: first_frame(f.png, f.meta.get("frame") if f.kind == "sprite" else None)
              for eid, f in art.filled.items()}  # fmt: skip
    out = {}
    for r in pack.records:
        name = f"{prefix}/levels/level-{r.spec.n}.png"
        ctx.save(name, preview(r, design, frames), "Level/Tile Designer", "image/png")
        out[r.spec.n] = name
    return out


def level_report(pack: LevelPack, played: dict, previews: dict, play: str | None) -> list[dict]:
    """report.json "levels": per level its play link, preview, code's check and the playtest."""
    results = {r["n"]: r for r in played.get("levels", [])}
    out = []
    for r in pack.records:
        res = results.get(r.spec.n, {})
        out.append({"n": r.spec.n, "name": r.spec.name,
                    "play": f"{play}?level={r.spec.n}" if play else None,
                    "preview": previews.get(r.spec.n), "solved": r.solved,
                    "difficulty": r.difficulty, "problems": r.problems,
                    "cleared": res.get("cleared", False), "ended": res.get("ended"),
                    "game_seconds": res.get("game_seconds"), "bot": res.get("bot"),
                    "missing": res.get("missing", []), "events": res.get("events", {})})  # fmt: skip
    return out


def motion_name(version: int) -> str:
    return f"game/design/v{version}-motion.json"


def ensure_motion(ctx: GameContext, design_version: int, design: GameDesign,
                  plan: PlanRecord | None, levels: int | None) -> MotionRecord | None:  # fmt: skip
    """The motion design for design vK, written once (L2.4). After a design change the newest
    earlier one is kept for every entity that still exists. None when `game.motion` is off."""
    if not ctx.cfg.game.motion:
        return None
    name = motion_name(design_version)
    if name not in ctx.artifacts:
        earlier = next((motion_name(k) for k in range(design_version - 1, 0, -1)
                        if motion_name(k) in ctx.artifacts), None)  # fmt: skip
        previous = MotionRecord.model_validate(ctx.read(earlier)) if earlier else None
        ctx.log("Art Director")("designing how each thing moves and reacts (motion design)")
        record = write_motion(ctx.models, design, plan, load_levels(ctx, levels) if levels else None,
                              previous)  # fmt: skip
        ctx.save(name, record.model_dump(), "Art Director")
        ctx.save(name.removesuffix(".json") + ".md", motion_markdown(record).encode(),
                 "Art Director", "text/markdown")  # fmt: skip
    return MotionRecord.model_validate(ctx.read(name))


def previous_art(ctx: GameContext, version: int):
    """Muse records of the previous build, so unchanged requests reuse their art."""

    def lookup(eid: str) -> dict | None:
        base = f"game/v{version - 1}/art/{eid}"
        if f"{base}.json" not in ctx.artifacts or f"{base}-raw.png" not in ctx.artifacts:
            return None
        record = ctx.read(f"{base}.json")
        return {**record, "raw": ctx.data(f"{base}-raw.png"), "png": ctx.data(f"{base}.png")}

    return lookup


def qa_suite(
    ctx: GameContext, version: int, design: GameDesign, design_version: int, drop=(),
    menus: int | None = None,
) -> QASuite | None:
    """The QA tests build vN runs: the previous build's suite minus `drop`, plus the QA
    Playtester's tests for this design version (written once per design version, with the
    menus when the game has them)."""
    if not ctx.cfg.game.qa:
        return None
    name = f"game/v{version}/qa-tests.json"
    if name in ctx.artifacts:
        return QASuite.model_validate(ctx.read(name))
    before = f"game/v{version - 1}/qa-tests.json"
    previous = QASuite.model_validate(ctx.read(before)) if before in ctx.artifacts else None
    old_design = (
        ctx.read(f"game/v{version - 1}/report.json")["design_version"] if version > 1 else None
    )
    new, notes = None, []
    if previous is None or design_version != old_design:
        written = f"game/design/v{design_version}-qa-tests.json"
        if written not in ctx.artifacts:
            tests, note = write_qa_tests(ctx.models, design, flow_script(ctx, menus, design))
            ctx.save(written, tests.model_dump(), "QA Playtester")
            notes += [note] if note else []
        new = QATests.model_validate(ctx.read(written))
    suite = merge_suite(previous, new, version, list(drop), notes)
    ctx.save(name, suite.model_dump(), "QA Playtester")
    return suite


def save_qa(ctx: GameContext, prefix: str, outcome, suite: QASuite) -> dict:
    """QA results, screenshots and the Playwright export as artifacts; report.json "qa"."""
    pictures = screenshot_files(outcome.qa)
    for name, data in pictures.items():
        ctx.save(f"{prefix}/qa/{name}", data, "QA Playtester", "image/png")
    report = qa_report(outcome.qa, suite.tests, "; ".join(suite.notes))
    for test in report["tests"]:
        test["screenshots"] = [f"{prefix}/qa/{n}" for n in test["screenshots"]]
        test["since"] = suite.since(test["name"])
    report["dropped"] = suite.dropped
    if outcome.build:
        path = write_export(outcome.build, suite.tests, ctx.cfg.game.playtest_fps)
        ctx.save(f"{prefix}/test_game_qa.py", path.read_bytes(), "QA Playtester", "text/x-python")
        report["export"] = f"{prefix}/test_game_qa.py"
        report["export_url"] = f"/api/runs/{ctx.run_id}/play/{prefix.split('/')[-1]}/tests/" \
                               "test_game_qa.py"  # fmt: skip
    ctx.save(f"{prefix}/qa.json", report, "QA Playtester")
    return report


def build_version(
    ctx: GameContext,
    version: int,
    design: GameDesign,
    design_version: int,
    plan: ArtPlan,
    base: GameCode | None,
    instruction: str,
    revision: Revision | None = None,
    suite: QASuite | None = None,
    levels: int | None = None,
    game_plan: PlanRecord | None = None,
    motion: MotionRecord | None = None,
    menus: int | None = None,
) -> list[str]:
    """Art, code, playtest, repair and recording for one build; returns gate artifact names.
    `levels`: the level set the build plays (levels as data), checked with `game_plan`;
    `motion`: the motion design the art's rows follow; `menus`: the menus version the SDK
    renders (title art, theme in the art's colours, screenshots of every screen)."""
    prefix = f"game/v{version}"
    if f"{prefix}/report.json" not in ctx.artifacts:
        log = ctx.log("Director")
        log(f"build v{version}: art (library first, Muse Image for the rest)")
        art = produce_art(ctx.models, design, plan, ctx.cfg, previous_art(ctx, version), motion)
        pack = None
        if levels and game_plan:
            pack = level_pack(load_levels(ctx, levels), design, game_plan, art.manifest(),
                              ctx.cfg.game.playtest_fps)  # fmt: skip
        flow: MenuPack | None = None
        if menus:
            log(f"build v{version}: the menus (v{menus}) and their title art")
            flow = menu_pack(ctx.models, load_menus(ctx, menus), design, art, ctx.cfg,
                             previous_title(ctx, version))  # fmt: skip
            if not flow.title_art.version:
                flow.title_art.version = version
        if base is None or instruction.strip():
            log(f"build v{version}: the Programmer is writing the game")
            code = write_code(ctx.models, design, game_assets(art, flow)[0], base, instruction, log,
                              pack)  # fmt: skip
        else:
            code = base  # feedback touched only the art, the levels or the menus: same code
        outcome = build_and_test(ctx.models, design, art, code, ctx.cfg, ctx.root / prefix, log,
                                 suite.tests if suite else None, pack, flow)  # fmt: skip
        review = None
        if ctx.cfg.game.review and outcome.build:
            log(f"build v{version}: the Art Reviewer is looking at the art in the running game")
            review = review_art(ctx.models, design, plan, art, outcome, ctx.cfg,
                                ctx.root / prefix, log, version,
                                suite.tests if suite else None, pack, flow)  # fmt: skip
            art, outcome = review.art, review.outcome
        qa = save_qa(ctx, prefix, outcome, suite) if suite else None  # after the review's replay
        recording = None
        if outcome.build:
            log(f"build v{version}: recording the autoplay")
            recording = record_game(
                outcome.build,
                ctx.root / prefix / "recording",
                seconds=ctx.cfg.game.record_seconds,
                scale=4 // art.scale,
            )
        for eid, f in art.filled.items():
            ctx.save(f"{prefix}/art/{eid}.png", f.png, "Pixel Artist", "image/png")
            record = {k: v for k, v in f.record.items() if k != "raw"}
            record = {
                "entity": eid,
                "source": f.source,
                "reasons": f.reasons,
                **record,
                "version": record.get("reused_from") or version,
            }
            if f.record.get("raw"):
                ctx.save(
                    f"{prefix}/art/{eid}-raw.png", f.record["raw"], "Pixel Artist", "image/png"
                )
            ctx.save(f"{prefix}/art/{eid}.json", record, "Pixel Artist")
        if review:
            for name, data in review.images.items():
                ctx.save(f"{prefix}/review/{name}", data, "Art Reviewer", "image/png")
            ctx.save(f"{prefix}/art-review.json", review.answer.model_dump(), "Art Reviewer")
            if review.plan != plan:
                ctx.save(f"{prefix}/art-plan-reviewed.json", review.plan.model_dump(),
                         "Art Reviewer")  # fmt: skip
        if sheet := art_sheet(art.filled):
            ctx.save(f"{prefix}/art.png", sheet, "Art Director", "image/png")
        for name, data in motion_previews(art).items():
            ctx.save(f"{prefix}/{name}", data, "Art Director",
                     "image/gif" if name.endswith(".gif") else "image/png")  # fmt: skip
        ctx.save(f"{prefix}/art.json", art_summary(art), "Art Director")
        ctx.save(f"{prefix}/code.json", outcome.code.model_dump(), "Programmer")
        ctx.save(f"{prefix}/playtest.json", outcome.report, "QA Playtester")
        if recording:
            ctx.save(
                f"{prefix}/poster.png", recording.poster.read_bytes(), "QA Playtester", "image/png"
            )
            if recording.video:
                ctx.save(
                    f"{prefix}/demo.mp4", recording.video.read_bytes(), "QA Playtester", "video/mp4"
                )
        play = f"/api/runs/{ctx.run_id}/play/v{version}/index.html" if outcome.build else None
        previews = level_previews(ctx, prefix, pack, design, art) if pack else {}
        menu_part = {}
        if flow and outcome.build:
            log(f"build v{version}: a screenshot of every menu screen")
            files, shots, errors = menu_files(flow, outcome.build, ctx.cfg.game.playtest_fps)
            for name, data in files.items():
                ctx.save(f"{prefix}/{name}", data, "Menu Designer", "image/png")
            ctx.save(f"{prefix}/menus/title-art.json",
                     {**flow.title_art.record(), "version": flow.title_art.version}, "Pixel Artist")  # fmt: skip
            menu_part = {"menus_version": menus, "menus": menus_report(flow, shots, errors, prefix + "/")}
        report = {
            "version": version,
            "title": design.title,
            "design_version": design_version,
            **({"levels_version": levels,
                "levels": level_report(pack, outcome.report, previews, play)} if pack else {}),
            "chosen_by": ctx.models.name,
            "passed": bool(outcome.report["passed"]),
            "play": play,
            "checks": outcome.report.get("checks", []),
            "problems": outcome.report.get("problems", []) + outcome.report.get("errors", []),
            "art": art_sources(art),
            "warnings": art.warnings,
            "attempts": outcome.attempts,
            "revision": revision.model_dump() if revision else None,
            "art_review": review_report(review.report, prefix) if review else None,
            "motion": motion_report(art) if motion else None,
            **menu_part,
            "qa": qa,
            "model_calls": call_summary(ctx.store, ctx.run_id),
        }
        report["game_db"] = sync_db(ctx, f"build v{version}", (version, report))
        ctx.save(f"{prefix}/report.json", report, "Director")
        ctx.store.append_event(
            ctx.run_id,
            "Director",
            "game.built",
            {
                "version": version,
                "passed": report["passed"],
                "attempts": len(outcome.attempts),
                "art": report["art"],
                "warnings": art.warnings,
            },
        )
    return gate_names(ctx, version)


def review_report(report: dict, prefix: str) -> dict:
    """The review for report.json, with its pictures named as artifacts (for G4)."""
    named = lambda n: f"{prefix}/review/{n}" if n else None
    return {
        **report,
        "images": [named(n) for n in report["images"]],
        "fixes": [
            {**f, "before": named(f["before"]), "after": named(f["after"])} for f in report["fixes"]
        ],
    }


def suggestion_revision(report: dict, design: GameDesign, n) -> Revision | None:
    """Apply on a G4 art-review suggestion: redraw that entity as the suggestion says. None
    when there is no such suggestion (the feedback text then goes to the Director)."""
    issues = (report.get("art_review") or {}).get("issues", [])
    issue = next((i for i in issues if str(i["n"]) == str(n) and i["status"] != "fixed"), None)
    looks = {e.id: e.look for e in design.entities}
    if issue is None or issue["entity"] not in looks:
        return None
    change = ArtChange(
        entity=issue["entity"], prompt=f"{looks[issue['entity']]}; {issue['fix_prompt']}"
    )
    return Revision(design_changes="", art_changes=[change], code_instructions="",
                    drop_qa_tests=[])  # fmt: skip


def last_plan(ctx: GameContext, version: int) -> ArtPlan:
    """A build's final art plan: with the art review's fixes when it made any."""
    reviewed = f"game/v{version}/art-plan-reviewed.json"
    name = reviewed if reviewed in ctx.artifacts else f"game/v{version}/art-plan.json"
    return ArtPlan.model_validate(ctx.read(name))


def open_playtest_gate(ctx: GameContext, version: int, names: list[str]) -> None:
    report = ctx.read(f"game/v{version}/report.json")
    verdict = "all checks passed" if report["passed"] else "SOME CHECKS FAILED (see the report)"
    if levels := report.get("levels"):
        verdict += f"; {sum(lv['cleared'] for lv in levels)}/{len(levels)} levels cleared by the bot"
    if qa := report.get("qa"):
        total = qa["passed"] + qa["failed"]
        verdict += f"; QA {qa['passed']}/{total} tests pass" + (
            f" ({qa['failed']} failing: test or game bug?)" if qa["failed"] else ""
        )
    story = latest_story(ctx)
    title = (
        f"G4 Playtest v{version}: {report['title']}, {verdict}. Watch the autoplay or play it; "
        "type what to change, or leave feedback empty and submit to approve"
        + ("" if story else " (no story: choose Add a story to get one)")
    )
    names = [*names, *(n for n in story_names_shown(ctx) if n not in names)]
    form = {} if story else dict(STORY_FORM["properties"])
    if levels:
        title += ("; to change one level, pick it under Level (the others stay as they are)")
        form["level"] = {"type": "string", "title": "Level",
                         "description": "Which level the feedback is about (the whole game goes "
                                        "to the Director).",
                         "enum": [WHOLE_GAME, *(f"Level {lv['n']}: {lv['name']}" for lv in levels)],
                         "default": WHOLE_GAME}  # fmt: skip
    extra = {"form_schema": {"type": "object", "properties": form}} if form else {}
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("playtest", title, [ctx.artifacts[n].id for n in names],
                 empty_feedback_approves=True, **extra),
    )


def menus_approved(ctx: GameContext) -> bool:
    return any(g.stage == "menus" and g.status == "approved" for g in Gates(ctx.store).all(ctx.run_id))


def open_after_build(ctx: GameContext, version: int, names: list[str]) -> None:
    """G3 (the menus) after a build with menus until the human approved them once; then G4."""
    report = ctx.read(f"game/v{version}/report.json")
    if (report.get("menus") or {}).get("screens") and not menus_approved(ctx):
        open_menus_gate(ctx, version)
    else:
        open_playtest_gate(ctx, version, names)


def open_menus_gate(ctx: GameContext, version: int) -> None:
    prefix = f"game/v{version}"
    report = ctx.read(f"{prefix}/report.json")
    m, info = report["menus_version"], report["menus"]
    source = info["title_art"]["source"]
    names = [n for n in (f"{prefix}/menus.png", f"{prefix}/menus/title-art.png",
                         f"{prefix}/menus/emblem.png") if n in ctx.artifacts]  # fmt: skip
    names += sorted((n for n in ctx.artifacts if re.fullmatch(rf"{prefix}/levels/level-\d+\.png", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[-1]))  # fmt: skip
    names += [menus_name(m), f"{prefix}/report.json"]
    problems = (f" Code could not fix {len(info['problems'])} problem(s) (listed in the menus)."
                if info.get("problems") else "")  # fmt: skip
    title = (
        f"G3 Menus v{m} (build v{version}: {report['title']}): every screen of the menus, the title "
        f"art ({source})" + (" and the level previews" if report.get("levels") else "")
        + ". Type what to change in the menus or the title art (labels, colours, what the title "
        "picture shows), or leave feedback empty and submit to approve them and go on to the "
        "playtest (G4)." + problems
    )
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("menus", title, [ctx.artifacts[n].id for n in names], empty_feedback_approves=True),
    )


def gate_names(ctx: GameContext, version: int) -> list[str]:
    """What G4 shows for build vN: the video or poster, the art sheet, the QA export, the
    report and the design it was built from."""
    prefix = f"game/v{version}"
    design_version = ctx.read(f"{prefix}/report.json")["design_version"]
    names = [n for n in (f"{prefix}/demo.mp4", f"{prefix}/poster.png") if n in ctx.artifacts][:1]
    names += sorted((n for n in ctx.artifacts if re.fullmatch(rf"{prefix}/levels/level-\d+\.png", n)),
                    key=lambda n: int(re.findall(r"\d+", n)[-1]))  # fmt: skip
    names += [n for n in (f"{prefix}/menus.png", f"{prefix}/motion.gif", f"{prefix}/art.png",
                          f"{prefix}/test_game_qa.py") if n in ctx.artifacts]  # fmt: skip
    return [*names, f"{prefix}/report.json", f"game/design/v{design_version}.json"]


def apply_art_changes(plan: ArtPlan, design: GameDesign, revision: Revision) -> ArtPlan:
    """Entities the feedback wants redrawn become Muse requests with the new look (an edit of
    the previous image when it was Muse art); every other pick is kept."""
    wanted = {c.entity: c.prompt for c in revision.art_changes if c.prompt.strip()}
    ids = {e.id for e in design.entities}
    picks = [
        p.model_copy(
            update={
                "source": "muse",
                "asset": "",
                "prompt": wanted[p.entity],
                "reason": "redrawn per feedback",
            }
        )
        if p.entity in wanted
        else p
        for p in plan.picks
        if p.entity in ids
    ]
    known = {p.entity for p in picks}
    picks += [
        ArtPick(
            entity=eid,
            source="muse",
            asset="",
            prompt=prompt,
            reference="",
            recolor="",
            reason="redrawn per feedback",
        )
        for eid, prompt in wanted.items()
        if eid in ids and eid not in known
    ]
    return plan.model_copy(update={"picks": picks})


def asks_story(gate) -> bool:
    """The human chose Add a story on the gate's form."""
    return bool(gate.response) and gate.response.form.get("story") == ADD_STORY


def form_level(gate) -> int | None:
    """The level the human picked on the G4 form (None: the whole game)."""
    picked = str((gate.response.form if gate.response else {}).get("level") or "")
    match = re.match(r"Level (\d+)", picked)
    return int(match[1]) if match else None


def game_step(store, run_id) -> bool:
    ctx = GameContext(store, run_id)
    history = Gates(store).all(run_id)
    last, n = history[-1], len(history)  # n: this gate's number (story and revision keys)
    feedback = ((last.response.text if last.response else "") or "").strip()
    has_story = latest_story(ctx) is not None

    if last.stage == "playtest" and last.status == "approved":
        version = shown(ctx, last, REPORT)
        if asks_story(last) and not has_story:
            # Approved, but they want a story first: write it and show the same build again.
            design = load_design(ctx, ctx.read(f"game/v{version}/report.json")["design_version"])
            ensure_story(ctx, f"gate-{n}", design)
            sync_db(ctx, f"story added at G4 (build v{version})")
            open_playtest_gate(ctx, version, gate_names(ctx, version))
            return False
        report = ctx.read(f"game/v{version}/report.json")
        ctx.save(
            "approval.json",
            {
                "gate": last.id,
                "mode": "game",
                "version": version,
                "design": f"game/design/v{report['design_version']}.json",
                "build": f"game/v{version}/build",
                "code": f"game/v{version}/code.json",
                "report": f"game/v{version}/report.json",
                "passed": report["passed"],
            },
            "Director",
        )
        sync_db(ctx, f"G4 approved build v{version}")
        promote_all(ctx.db, f"G4 approved: build v{version}")
        store.advance(run_id, ctx.run.cursor, "G4 approved; game build locked", final=True)
        return False

    if last.stage == "menus" and last.status == "approved":
        version = shown(ctx, last, REPORT)
        m = ctx.read(f"game/v{version}/report.json").get("menus_version")
        sync_db(ctx, f"G3 approved menus v{m} (build v{version})")
        promote_all(ctx.db, f"G3 approved: menus v{m}", only=("menus/", "design/structure"))
        open_playtest_gate(ctx, version, gate_names(ctx, version))
        return False

    if last.stage == "menus" and last.status == "changes_requested":
        # Menus are data: the Menu Designer revises them and the same code and art are built
        # again with them (no Programmer call), then G3 again.
        shown_version = shown(ctx, last, REPORT)
        version = shown_version + 1
        previous = ctx.read(f"game/v{shown_version}/report.json")
        design_version = previous["design_version"]
        design = load_design(ctx, design_version)
        feedback = feedback or "Try a different take."
        menus = next_menus(ctx, n, previous["menus_version"], feedback, design, design_version, "G3")
        revision = ctx.once(f"game/feedback/gate-{n}-revision.json", Revision,
                            lambda: Revision(design_changes="", art_changes=[], code_instructions="",
                                             drop_qa_tests=[], story_changes="", level_changes=[],
                                             menu_changes=feedback), "Menu Designer")  # fmt: skip
        if f"game/v{version}/revision.json" not in ctx.artifacts:
            ctx.save(f"game/v{version}/revision.json", revision.model_dump(), "Menu Designer")
        plan = last_plan(ctx, shown_version)
        if f"game/v{version}/art-plan.json" not in ctx.artifacts:
            ctx.save(f"game/v{version}/art-plan.json", plan.model_dump(), "Art Director")
        base = GameCode.model_validate(ctx.read(f"game/v{shown_version}/code.json"))
        game_plan = load_plan_at(ctx, design_version)
        levels = previous.get("levels_version")
        motion = ensure_motion(ctx, design_version, design, game_plan, levels)
        suite = qa_suite(ctx, version, design, design_version, menus=menus)
        names = build_version(ctx, version, design, design_version, plan, base, "", revision, suite,
                              levels, game_plan, motion, menus)  # fmt: skip
        open_after_build(ctx, version, names)
        return False

    if last.stage == "design" and last.status == "changes_requested":
        version = design_versions(ctx) + 1
        previous = load_design(ctx, version - 1)
        feedback = feedback or "Try a different take."
        plan = next_plan(ctx, version, version - 1, feedback, feedback)
        design = ctx.once(
            f"game/design/v{version}.json",
            GameDesign,
            lambda: ctx.models.ask(
                "Game Designer",
                revision_prompt(previous, feedback, plan_brief(plan) if plan else ""),
                GameDesign,
                previous,
                "design",
            ),
            "Game Designer",
        )
        save_design(ctx, version, design)
        if has_story or asks_story(last):  # a story follows the design; one starts on request
            ensure_story(ctx, f"design-v{version}", design, feedback)
        sync_db(ctx, f"design v{version}")
        open_design_gate(ctx, version)
        return False

    if last.stage == "design" and last.status == "approved":
        version = design_versions(ctx)
        design = load_design(ctx, version)
        if asks_story(last) and not has_story:
            # "Add a story" with no other feedback: write it and show the design again.
            ensure_story(ctx, f"gate-{n}", design)
            sync_db(ctx, f"story added at G2 (design v{version})")
            open_design_gate(ctx, version)
            return False
        sync_db(ctx, f"G2 approved design v{version}")
        promote_all(ctx.db, f"G2 approved: design v{version}" + (" and its story" if has_story else ""))
        game_plan = load_plan(ctx, version)
        levels = None
        if uses_levels(design, game_plan):
            levels = latest_version(ctx.artifacts, LEVELS) or first_levels(ctx, design, version, game_plan)
            sync_db(ctx, f"levels v{levels}")
        motion = ensure_motion(ctx, version, design, game_plan, levels)
        menus = ensure_menus(ctx, design, version)
        if menus:
            sync_db(ctx, f"menus v{menus}")
        plan = ctx.once(
            "game/v1/art-plan.json", ArtPlan, lambda: plan_art(ctx.models, design, motion),
            "Art Director",
        )
        suite = qa_suite(ctx, 1, design, version, menus=menus)
        open_after_build(ctx, 1, build_version(ctx, 1, design, version, plan, None, "",
                                               suite=suite, levels=levels, game_plan=game_plan,
                                               motion=motion, menus=menus))  # fmt: skip
        return False

    if last.stage == "playtest" and last.status == "changes_requested":
        shown_version = shown(ctx, last, REPORT)
        version = shown_version + 1
        previous = ctx.read(f"game/v{shown_version}/report.json")
        design_version = previous["design_version"]
        design = load_design(ctx, design_version)
        feedback = feedback or "Try a different take."
        applied = suggestion_revision(previous, design, last.response.form.get("apply_suggestion"))
        picked = form_level(last)
        if applied is None and picked is not None:  # feedback on one level: the Level Designer
            applied = Revision(design_changes="", art_changes=[], code_instructions="",
                               drop_qa_tests=[], story_changes="",
                               level_changes=[LevelChange(level=picked, change=feedback)])  # fmt: skip
        tests = {t["name"]: t["passed"] for t in (previous.get("qa") or {}).get("tests", [])}

        def director() -> Revision:
            answer = applied or ctx.models.ask(
                "Director",
                director_prompt(
                    design, {a["entity"]: a["source"] for a in previous["art"]}, feedback, tests
                ),
                Revision,
                fallback_revision(design, feedback, list(tests)),
                "revision",
            )
            if asks_story(last) and not answer.story_changes.strip():
                answer = answer.model_copy(update={"story_changes": "Add a story"})
            return answer

        role = ("Level/Tile Designer" if picked is not None else "Art Reviewer") if applied else "Director"
        revision = ctx.once(f"game/feedback/gate-{n}-revision.json", Revision, director, role)
        if revision.story_only:
            # Only the story changes: write it and show the same build again (no rebuild).
            ensure_story(ctx, f"gate-{n}", design, revision.story_changes)
            sync_db(ctx, f"story at G4 (build v{shown_version})")
            open_playtest_gate(ctx, shown_version, gate_names(ctx, shown_version))
            return False
        if f"game/v{version}/revision.json" not in ctx.artifacts:
            ctx.save(f"game/v{version}/revision.json", revision.model_dump(), role)
        changed = bool(revision.design_changes.strip())
        if changed:
            old, old_version = design, design_version
            # Chosen once per build: a step re-entered after a failure (a budget cap, a timeout)
            # reuses it instead of counting the versions again (live 2026-11-02 made a stray v3).
            chosen = f"game/v{version}/design-version.json"
            if chosen not in ctx.artifacts:
                ctx.save(chosen, {"version": design_versions(ctx) + 1}, "Game Designer")
            design_version = ctx.read(chosen)["version"]
            game_plan = next_plan(ctx, design_version, old_version, revision.design_changes,
                                  feedback)
            brief = plan_brief(game_plan) if game_plan else ""
            design = ctx.once(
                f"game/design/v{design_version}.json",
                GameDesign,
                lambda: ctx.models.ask(
                    "Game Designer",
                    revision_prompt(old, revision.design_changes, brief),
                    GameDesign,
                    old,
                    "design",
                ),
                "Game Designer",
            )
            save_design(ctx, design_version, design)
        if changed and (has_story or revision.story_changes.strip()):
            ask = "\n".join(p for p in (revision.design_changes, revision.story_changes) if p.strip())
            ensure_story(ctx, f"design-v{design_version}", design, ask)
        elif revision.story_changes.strip():
            ensure_story(ctx, f"gate-{n}", design, revision.story_changes)
        # A design change keeps every existing entity's art (locked) and motion rows; only new
        # entities are planned, and entities the feedback names are redrawn (apply_art_changes).
        motion = ensure_motion(ctx, design_version, design, load_plan_at(ctx, design_version),
                               previous.get("levels_version"))  # fmt: skip
        base_plan = (
            ctx.once(
                f"game/v{version}/art-plan-new.json",
                ArtPlan,
                lambda: plan_new_art(ctx.models, design, last_plan(ctx, version - 1), motion),
                "Art Director",
            )
            if changed
            else last_plan(ctx, version - 1)
        )
        plan = apply_art_changes(base_plan, design, revision)
        if f"game/v{version}/art-plan.json" not in ctx.artifacts:
            ctx.save(f"game/v{version}/art-plan.json", plan.model_dump(), "Art Director")
        instruction = "\n".join(
            part
            for part in (
                revision.code_instructions.strip(),
                f"The design changed: {revision.design_changes.strip()}" if changed else "",
            )
            if part
        )
        base = GameCode.model_validate(ctx.read(f"game/v{version - 1}/code.json"))
        # Menus: kept, refitted by code to a changed structure, or revised for menu feedback.
        # A build from before menus existed (L2.5) stays without them.
        menus = previous.get("menus_version")
        if menus and changed:
            menus = ensure_menus(ctx, design, design_version) or menus
        if menus and revision.menu_changes.strip():
            menus = next_menus(ctx, n, menus, revision.menu_changes, design, design_version, "G4")
        suite = qa_suite(ctx, version, design, design_version, revision.drop_qa_tests, menus)
        game_plan = load_plan_at(ctx, design_version)
        levels = previous.get("levels_version")
        changes = {c.level: c.change for c in revision.level_changes if c.change.strip()}
        if uses_levels(design, game_plan):
            built = f"game/v{version}/levels-version.json"
            if built in ctx.artifacts:
                levels = ctx.read(built)["version"]
            elif levels is None:
                levels = first_levels(ctx, design, design_version, game_plan)
            elif changed or changes:
                levels = next_levels(ctx, levels, design, design_version, game_plan, changes,
                                     revision.design_changes.strip() if changed else "")  # fmt: skip
            if built not in ctx.artifacts:
                ctx.save(built, {"version": levels}, "Level/Tile Designer")
        else:
            levels = None
        names = build_version(
            ctx, version, design, design_version, plan, base, instruction, revision, suite,
            levels, game_plan, motion, menus,
        )
        open_after_build(ctx, version, names)
        return False

    # Entering from the G1 follow-ups: the research (when the brief asks for it), the game plan,
    # design v1 written from the plan (and the story, only when the brief asks for one); then
    # ask the human to approve them.
    plan = None
    if "game/design/v1.json" not in ctx.artifacts:  # runs from before L2.2 have no plan
        research = ensure_research(ctx)
        ctx.log("Game Designer")("writing the game plan")
        plan = ensure_plan(ctx, 1, lambda: write_plan(
            ctx.models, request_text(ctx.brief), ctx.brief.notes,
            research_brief(research) if research else "", side_view(ctx)))  # fmt: skip
    design = ctx.once(
        "game/design/v1.json",
        GameDesign,
        lambda: write_design(ctx.models, request_text(ctx.brief), plan),
        "Game Designer",
    )
    save_design(ctx, 1, design)
    if ctx.brief.wants_story:
        ensure_story(ctx, "design-v1", design)
    sync_db(ctx, "design v1")
    open_design_gate(ctx, 1)
    return False
