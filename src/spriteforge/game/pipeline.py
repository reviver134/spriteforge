"""Prompt -> playable game: design, art (library first, else Muse Image), code, playtest, repair.

    design.json/.md     Game Designer (Muse Spark): rules, entities, checks
    story.json/.md      Game Designer: the story bible, only with --story (the human asked)
    sounds.json/.md     Sound Director: a synthesised sound for every moment of the game (L2.7),
                        fitted by code; the build's sounds.html edits them
    motion.json/.md     Art Director: the motion design (rows per entity with what each frame
                        shows, fps and loop; forms; effects), fitted by code (L2.4);
                        motion.png/.gif every animated row as filmstrips and playing
    art-plan.json       Art Director (Muse Spark): a library pick or a Muse request per entity
    art.json, art.png   what each entity actually got (library / tile set / Muse / placeholder)
    attempts/N/         each Programmer answer (files + notes) and its static/playtest report
    art-review.json     Art Reviewer (Muse Spark vision): scores, naming test, issues by sheet
                        number; review/ holds its inputs (sheets, screenshots) and before/after
                        pictures of fixed art; art-plan-reviewed.json the plan with the fixes
    build/              the playable game (open build/index.html)
    qa-tests.json       QA Playtester (Muse Spark): tests written from the design, played with
                        real key presses after the bot playtest; qa.json their results, qa/
                        their screenshots, build/tests/test_game_qa.py the Playwright export
    playtest.json       the final report; poster.png (+ demo.mp4 with ffmpeg)
    report.json         summary: who decided, sources, warnings, attempts, model calls and cost
    code.json           the final code (the Programmer's files)
    db/                 the game's record as an OKF bundle (design, entities, art, code, QA,
                        the build, the request; the story only with --story) for later agents
    run.sqlite3         the budget ledger for this build (call caps and cost estimate)

Used by `spriteforge game make` (no gates). Every model call goes through `Budget`; `--mock`
answers with deterministic stand-ins (fallback design, keyword art plan, fixture game).
Spark calls carry a per-task `reasoning_effort` (`llm.reasoning`) and `prompt_cache_key`.
Muse Image calls run in parallel (`game.image_workers`, one provider per thread) and live
requests are cached in `<workspace>/cache/muse-image` (`game.image_cache`).
"""

import base64
import io
import json
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright
from pydantic import ValidationError

from spriteforge.agents.roles import load_roles
from spriteforge.config import Config
from spriteforge.core.cost import Budget, ledger
from spriteforge.core.state import Store
from spriteforge.providers.base import ChatRequest, Message, SearchRequest, SearchResult, chat_json
from spriteforge.providers.http import ProviderError
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_search import MuseSearchProvider
from spriteforge.providers.muse_spark import MuseSparkProvider

from .art import (
    MUSE_CREDIT,
    ArtPlan,
    ArtResult,
    MuseOptions,
    art_prompt,
    art_sheet,
    keyword_plan,
    library_context,
    make_art,
)
from .art_checks import precheck
from .art_review import (
    ArtReview,
    entity_issues,
    fallback_review,
    fix_cap,
    fix_requests,
    plan_fixes,
    review_prompt,
    review_sheets,
    review_slots,
    reviewed_plan,
    run_fixes,
    screenshot,
    thumb,
)
from .build import build_game
from .codegen import (
    CodePatch,
    GameCode,
    PatchError,
    apply_patch,
    change_prompt,
    level_summary,
    mock_code,
    programmer_prompt,
    repair_prompt,
    static_check,
)
from .dbsource import folder_record
from .design import GameDesign, design_markdown, design_prompt, fallback_design
from .gamedb import open_db, sync_record
from .image_cache import ImageCache
from .level_gen import fallback_level
from .menus import (
    MenuRecord,
    MenuSpec,
    art_colours,
    checked,
    fallback_menu,
    fit_theme,
    menu_prompt,
    menu_revision_prompt,
    menus_markdown,
    menus_script,
    offline_revision,
)
from .menus import retry_prompt as menu_retry_prompt
from .levels import (
    SDK_EVENTS,
    LevelRecord,
    LevelSpec,
    evaluate,
    level_prompt,
    level_retry_prompt,
    level_revision_prompt,
    levels_script,
    player_body,
)
from .motion import (
    AnimationBrief,
    MotionRecord,
    breaks_from_design,
    breaks_of,
    brief_prompt,
    fallback_brief,
    filmstrips,
    keep_rows,
    make_motion,
    motion_gif,
    motion_markdown,
)
from .plan import (
    GamePlan,
    PlanRecord,
    fallback_plan,
    make_record,
    plan_brief,
    plan_prompt,
    plan_revision_prompt,
    retry_prompt,
)
from .playtest import failure_summary, playtest, playtest_levels, review_times
from .qa import (
    QATest,
    bind_selects,
    QATests,
    fallback_qa,
    qa_failures,
    qa_prompt,
    run_qa,
    screenshot_files,
    without_pictures,
    write_export,
)
from .record import READY, record_game, serve, wait_until
from .research import (
    Research,
    ResearchRecord,
    fallback_research,
    fit_research,
    research_prompt,
    structure_prompt,
)
from .structure import GameStructure, make_structure, structure_markdown
from .title_art import TitleArt, make_title_art, manifest_images
from .sounds import (
    SoundBrief,
    SoundRecord,
    keep_edits,
    sound_prompt,
    sound_report,
    sounds_markdown,
    sounds_script,
)
from .sounds import fallback_brief as fallback_sounds
from .sounds import make_record as make_sound_record
from .sounds import offline_revision as offline_sound_revision
from .sounds import retry_prompt as sound_retry_prompt
from .sounds import revision_prompt as sound_revision_prompt
from .story import (
    StoryBible,
    fallback_story,
    fit_story,
    story_markdown,
    story_prompt,
    story_revision_prompt,
)


def role_prompt(role: str) -> str:
    r = load_roles()[role]
    return f"You are the {r.name} of a small game studio. Goal: {r.goal}\n{r.prompt}"


class Models:
    """Model access for one build: live Muse Spark / Muse Image, or offline stand-ins.
    `provider` (tests) replaces the chat model for every role, `images` the image model,
    `search` the web research model. `cache_dir`: where identical Muse requests are cached
    (None: no cache)."""

    def __init__(
        self,
        cfg: Config,
        budget: Budget,
        mock: bool,
        provider=None,
        images=None,
        cache_dir: Path | None = None,
        search=None,
    ):
        self.cfg, self.budget, self.mock = cfg, budget, mock
        self.provider, self.images, self.cache_dir = provider, images, cache_dir
        self.search = search

    @property
    def name(self) -> str:
        return "mock (offline stand-ins)" if self.mock and not self.provider else self.cfg.llm.model

    def ask(
        self,
        role: str,
        prompt: str,
        schema,
        fallback,
        task: str | None = None,
        images: list[bytes] = (),
    ):
        """`task` (design, art_plan, code, revision, review) picks the reasoning effort and
        the prompt cache key; prompts of one task share their stable prefix. `images` go with
        the prompt (vision)."""
        provider = self.provider or (
            MockLLMProvider(self.budget, [fallback.model_dump()])
            if self.mock
            else MuseSparkProvider(self.cfg.llm, self.budget)
        )
        try:
            messages = [
                Message(role="system", content=role_prompt(role)),
                Message(role="user", content=prompt, images=list(images)),
            ]
            request = ChatRequest(
                messages=messages,
                reasoning_effort=self.cfg.llm.reasoning.get(task) if task else None,
                cache_key=f"spriteforge-{task}" if task else None,
            )
            return chat_json(provider, request, schema)
        finally:
            if provider is not self.provider and hasattr(provider, "close"):
                provider.close()

    @property
    def can_search(self) -> bool:
        """Live web search (or an injected search provider in tests)."""
        return self.search is not None or not self.mock

    def web_research(self, role: str, prompt: str, schema, task: str = "research") -> SearchResult:
        """One Muse Spark call with web search (Responses API) answering in `schema`."""
        provider = self.search or MuseSearchProvider(self.cfg.llm, self.cfg.research, self.budget)
        try:
            return provider.research(
                SearchRequest(
                    instructions=role_prompt(role),
                    prompt=prompt,
                    json_schema=schema.model_json_schema(),
                    max_searches=self.cfg.research.max_searches,
                    reasoning_effort=self.cfg.llm.reasoning.get(task),
                    cache_key=f"spriteforge-{task}",
                )
            )
        finally:
            if provider is not self.search and hasattr(provider, "close"):
                provider.close()

    def image_generator(self) -> tuple[Callable, Callable]:
        """`generate(request, edit) -> png bytes`, safe across threads: each worker thread
        gets its own provider (its own httpx client); `close()` closes them all."""
        local, made, lock = threading.local(), [], threading.Lock()

        def provider():
            if self.images is not None:
                return self.images
            if not hasattr(local, "provider"):
                local.provider = (
                    MockImageProvider(self.budget)
                    if self.mock
                    else MuseImageProvider(self.cfg.image, self.budget)
                )
                with lock:
                    made.append(local.provider)
            return local.provider

        def generate(request, edit: bool) -> bytes:
            p = provider()
            return (p.edit(request) if edit else p.generate(request)).data

        def close():
            for p in made:
                getattr(p, "close", lambda: None)()

        return generate, close

    def image_cache(self) -> ImageCache | None:
        """The Muse request cache, keyed by this build's image provider (an injected test
        provider is never cached: its identity is unknown)."""
        if self.cache_dir is None or not self.cfg.game.image_cache or self.images is not None:
            return None
        img = self.cfg.image
        identity = (
            {"model": "mock/synthetic-image", "base_url": "mock", "generate": "generate",
             "edit": "edit"}
            if self.mock
            else {"model": img.model, "base_url": str(img.base_url),
                  "generate": img.generate_path, "edit": img.edit_path}
        )  # fmt: skip
        return ImageCache(self.cache_dir, identity)


@dataclass
class MakeResult:
    out: Path
    build: Path | None
    design: GameDesign
    report: dict


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_design(models: Models, request: str, plan: PlanRecord | None = None) -> GameDesign:
    """The design for a request, written from the game plan when there is one."""
    prompt = design_prompt(request, plan_brief(plan) if plan else "")
    fallback = fallback_design(request, platformer=bool(plan and plan.kinematics))
    return models.ask("Game Designer", prompt, GameDesign, fallback, "design")


# --- levels as data (L2.3) ----------------------------------------------------------------------


def uses_levels(design: GameDesign, plan: PlanRecord | None) -> bool:
    """Levels as data: a side-view game with a plan that has a jump and levels."""
    return bool(design.camera == "side-view" and plan and plan.kinematics and plan.plan.levels)


@dataclass
class LevelPack:
    """A game's levels as the build uses them."""

    records: list[LevelRecord]
    script: dict  # window.SF_LEVELS (levels.js)

    @property
    def summary(self) -> str:
        return level_summary(self.records)

    def playtest(self) -> list[dict]:
        """Per level: the game's own events it must make happen (the SDK's are checked over
        all levels: whether the bot dashes in a level depends on its route)."""
        return [{"n": r.spec.n, "name": r.spec.name, "steps": len(r.route),
                 "events": [e for e in r.spec.events if e not in SDK_EVENTS]}
                for r in self.records]  # fmt: skip


def level_pack(records: list[LevelRecord], design: GameDesign, plan: PlanRecord,
               manifest: dict | None = None, fps: int = 30) -> LevelPack:  # fmt: skip
    """The levels checked again with the player's real body from the build's art (the bot
    follows the route found for it), and the script the build loads."""
    body = player_body(design, manifest)
    checked, previous = [], None
    for r in records:
        again = evaluate(r.spec, r.spec.n, design, plan, previous, body, fps, r.retried, r.notes, r.by)
        checked.append(again)
        previous = again
    return LevelPack(checked, levels_script(checked, design, plan, body))


def better_level(a: LevelRecord, b: LevelRecord) -> LevelRecord:
    """The better of two answers: solvable first, then fewer problems (b wins a tie)."""
    rank = lambda r: (r.solved, -len(r.problems))
    return a if rank(a) > rank(b) else b


def write_level(models: Models, n: int, design: GameDesign, plan: PlanRecord,
                earlier: list[LevelRecord], context: str = "", fps: int = 30) -> LevelRecord:  # fmt: skip
    """The Level Designer's level n, fitted and checked by code; problems go back once."""
    body = player_body(design)
    prompt = level_prompt(n, design, plan, earlier, context)
    previous = earlier[-1] if earlier else None
    spec = models.ask("Level/Tile Designer", prompt, LevelSpec, fallback_level(n, design, plan), "level")
    record = evaluate(spec, n, design, plan, previous, body, fps, by=models.name)
    if not record.problems:
        return record
    again = models.ask("Level/Tile Designer", level_retry_prompt(prompt, record.problems), LevelSpec,
                       record.spec, "level")  # fmt: skip
    retried = evaluate(again, n, design, plan, previous, body, fps, True, record.notes, models.name)
    return better_level(record.model_copy(update={"retried": True}), retried)


def revise_level(models: Models, record: LevelRecord, said: str, design: GameDesign,
                 plan: PlanRecord, earlier: list[LevelRecord], fps: int = 30) -> LevelRecord:  # fmt: skip
    """Level n revised for the human's words (the offline stand-in turns its sections)."""
    n, body = record.spec.n, player_body(design)
    previous = earlier[-1] if earlier else None
    prompt = level_revision_prompt(record, said, design, plan, earlier)
    fallback = fallback_level(n, design, plan, variant=1 + len(said) % 3)
    spec = models.ask("Level/Tile Designer", prompt, LevelSpec, fallback, "level")
    revised = evaluate(spec, n, design, plan, previous, body, fps, by=models.name)
    if not revised.problems:
        return revised
    again = models.ask("Level/Tile Designer", level_retry_prompt(prompt, revised.problems), LevelSpec,
                       revised.spec, "level")  # fmt: skip
    retried = evaluate(again, n, design, plan, previous, body, fps, True, revised.notes, models.name)
    return better_level(revised.model_copy(update={"retried": True}), retried)


def write_research(models: Models, request: str, genre: str, genre_body: str,
                   side_view: bool) -> ResearchRecord:  # fmt: skip
    """The Game Designer studies similar games with live web search (one call; a second,
    structuring call only when the searched answer does not fit the schema). Offline, or when
    the search fails, the stand-in from the genre notes is used and the record says so."""
    fallback = fallback_research(genre_body, side_view)
    prompt = research_prompt(request, genre)
    if not models.can_search:
        answer = models.ask("Game Designer", prompt, Research, fallback, "research")
        return fit_research(answer, None, "offline stand-in from the genre notes (no web search)")
    try:
        found = models.web_research("Game Designer", prompt, Research)
    except ProviderError as exc:
        record = fit_research(fallback, None, "offline stand-in: the web search failed")
        record.notes.append(f"web search failed: {str(exc)[:300]}")
        return record
    by = f"{models.cfg.llm.model} with web search"
    try:
        research = Research.model_validate_json(found.content or "")
    except ValidationError:
        research = models.ask("Game Designer", structure_prompt(found.text, found.sources),
                              Research, fallback, "research")  # fmt: skip
        by += " (structured in a second call)"
    return fit_research(research, found, by)


def write_plan(models: Models, request: str, notes: list[str], research: str,
               side_view: bool, lessons: str = "") -> PlanRecord:  # fmt: skip
    """The Game Designer's plan from the human's words, the research and the human-reviewed
    lessons of earlier games; code fits and checks it, and the problems it cannot repair go
    back to the Game Designer once."""
    prompt = plan_prompt(request, research, lessons)
    plan = models.ask("Game Designer", prompt, GamePlan, fallback_plan(notes, side_view), "plan")
    return checked_plan(models, plan, prompt, request)


def revise_plan(models: Models, record: PlanRecord, changes: str, words: str,
                said: str = "") -> PlanRecord:  # fmt: skip
    """The plan revised for feedback (the offline stand-in keeps it). `changes`: what to
    change; `said`: the human's own words at the gate (new requirements are copied from them;
    at G4 `changes` is the Director's summary, not the human's words)."""
    prompt = plan_revision_prompt(record.plan, changes, words, said)
    plan = models.ask("Game Designer", prompt, GamePlan, record.plan, "plan")
    return checked_plan(models, plan, prompt, words)


def checked_plan(models: Models, plan: GamePlan, prompt: str, words: str) -> PlanRecord:
    record = make_record(plan, words)
    if not record.problems:
        return record
    again = models.ask("Game Designer", retry_prompt(prompt, record.problems), GamePlan,
                       record.plan, "plan")  # fmt: skip
    return make_record(again, words, retried=True, notes=record.notes)


def write_story(models: Models, design: GameDesign, request: str) -> tuple[StoryBible, list[str]]:
    """The Game Designer's story bible for a design, fitted to it (notes on repairs). Called
    only when the human asked for a story."""
    story = models.ask("Game Designer", story_prompt(design, request), StoryBible,
                       fallback_story(design), "story")  # fmt: skip
    return fit_story(story, design)


def revise_story(
    models: Models, story: StoryBible, design: GameDesign, feedback: str
) -> tuple[StoryBible, list[str]]:
    """The story revised for a revised design (the offline stand-in keeps it as it was)."""
    revised = models.ask("Game Designer", story_revision_prompt(story, design, feedback),
                         StoryBible, story, "story")  # fmt: skip
    return fit_story(revised, design)


def write_qa_tests(models: Models, design: GameDesign,
                   menus: dict | None = None) -> tuple[QATests, str]:  # fmt: skip
    """The QA Playtester's tests for a design (and its menus, `menus_script`), and a note when
    the offline tests stood in (an answer that broke the limits twice, or a failed call: QA is
    a check, the build goes on)."""
    fallback = fallback_qa(design, menus)
    try:
        tests = models.ask("QA Playtester", qa_prompt(design, menus), QATests, fallback, "qa")
        return tests.model_copy(update={"tests": bind_selects(tests.tests, menus)}), ""
    except Exception as exc:  # noqa: BLE001
        first = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
        note = (
            f"the QA Playtester's tests could not be used ({type(exc).__name__}: {first}); "
            "the offline tests ran instead"
        )
        return fallback, note


# --- menus and flow (L2.5) ----------------------------------------------------------------------


def write_menus(models: Models, design: GameDesign, structure: GameStructure, context: str = "",
                why: str = "") -> MenuRecord:  # fmt: skip
    """The Menu Designer's menus, fitted and checked by code; problems go back once."""
    prompt = menu_prompt(design, structure, context)
    offline = fallback_menu(design, structure)
    spec = models.ask("Menu Designer", prompt, MenuSpec, offline, "menus")
    record = checked(spec, structure, design, models.name, why=why)
    if not record.problems:
        return record
    again = models.ask("Menu Designer", menu_retry_prompt(prompt, record.problems), MenuSpec,
                       record.spec, "menus")  # fmt: skip
    second = checked(again, structure, design, models.name, True, record.notes, why)
    return second if len(second.problems) <= len(record.problems) else record


def revise_menus(models: Models, record: MenuRecord, feedback: str, design: GameDesign,
                 structure: GameStructure, why: str = "") -> MenuRecord:  # fmt: skip
    """The menus revised for the human's words (the offline stand-in takes a colour they name)."""
    prompt = menu_revision_prompt(record.spec, feedback, design, structure)
    spec = models.ask("Menu Designer", prompt, MenuSpec, offline_revision(record.spec, feedback),
                      "menus")  # fmt: skip
    revised = checked(spec, structure, design, models.name, why=why)
    if not revised.problems:
        return revised
    again = models.ask("Menu Designer", menu_retry_prompt(prompt, revised.problems), MenuSpec,
                       revised.spec, "menus")  # fmt: skip
    second = checked(again, structure, design, models.name, True, revised.notes, why)
    return second if len(second.problems) <= len(revised.problems) else revised


@dataclass
class MenuPack:
    """A build's menus: the record's spec with the theme in the art's colours, the title art,
    and what the build loads (window.SF_MENUS, the title art's images)."""

    record: MenuRecord
    spec: MenuSpec  # the theme fitted to the art
    title_art: TitleArt
    script: dict
    images: dict  # assets.json image entries (role "menu")
    files: dict[str, bytes]
    notes: list[str]

    def assets(self, art: ArtResult) -> tuple[dict, dict[str, bytes]]:
        return game_assets(art, self)


def game_assets(art: ArtResult, menus: "MenuPack | None",
                sounds: dict | None = None) -> tuple[dict, dict[str, bytes]]:  # fmt: skip
    """The build's assets.json and asset files: the art's, plus the title art with menus, plus
    the game's own sound events (the Programmer emits them) with sounds."""
    manifest, files = art.manifest(), art.files()
    if menus:
        manifest["images"] = {**manifest["images"], **menus.images}
        files = {**files, **menus.files}
    if sounds and sounds.get("custom"):
        manifest["sound_events"] = dict(sounds["custom"])
    return manifest, files


# --- sounds (L2.7) --------------------------------------------------------------------------------


def plan_feel(plan: PlanRecord | None) -> str:
    """What the game plan says players hear, per moment."""
    return "\n".join(f"* {f.event}: {f.hear}" for f in plan.plan.feel if f.hear.strip()) if plan else ""


def write_sounds(models: Models, design: GameDesign, levels: bool, menus: bool,
                 plan: PlanRecord | None = None, context: str = "", why: str = "") -> SoundRecord:  # fmt: skip
    """The Sound Director's sounds, fitted and checked by code; problems go back once."""
    prompt = sound_prompt(design, levels, menus, plan_feel(plan), context)
    brief = models.ask("Sound Director", prompt, SoundBrief, fallback_sounds(design, levels, menus),
                       "sounds")  # fmt: skip
    record = make_sound_record(brief, design, models.name, levels, menus, why)
    if not record.problems:
        return record
    again = models.ask("Sound Director", sound_retry_prompt(prompt, record.problems), SoundBrief,
                       brief, "sounds")  # fmt: skip
    second = make_sound_record(again, design, models.name, levels, menus, why, True)
    return second if len(second.problems) <= len(record.problems) else record


def revise_sounds(models: Models, record: SoundRecord, feedback: str, design: GameDesign,
                  levels: bool, menus: bool, why: str = "") -> SoundRecord:  # fmt: skip
    """The sounds revised for the human's words; cues it leaves as they were keep their layers
    (the human's edits in the sound editor survive)."""
    prompt = sound_revision_prompt(record, feedback, design, levels, menus)
    brief = models.ask("Sound Director", prompt, SoundBrief,
                       offline_sound_revision(record, feedback), "sounds")  # fmt: skip
    return keep_edits(record, make_sound_record(brief, design, models.name, levels, menus, why))


def menu_pack(models: Models, record: MenuRecord, design: GameDesign, art: ArtResult, cfg: Config,
              previous: TitleArt | None = None) -> MenuPack:  # fmt: skip
    """The menus as a build uses them: the theme snapped to the colours measured from the
    locked art (contrast repaired), the title art (Muse in the art's style, locked once
    drawn; code-drawn offline or when Muse fails), the script the SDK loads."""
    theme, notes = fit_theme(record.spec.theme, art_colours([f.png for f in art.filled.values()]))
    spec = record.spec.model_copy(update={"theme": theme})
    generate, close, cache = None, lambda: None, None
    if cfg.game.title_art and (not models.mock or models.images is not None):
        generate, close = models.image_generator()
        cache = models.image_cache()
    try:
        title = make_title_art(spec, design, art, generate, cache, previous)
    finally:
        close()
    if not cfg.game.title_art:
        title.source = "code-drawn: game.title_art is off"
    images, files = manifest_images(title)
    script = menus_script(spec, record.structure, design, art.credits,
                          {"title": "sf_title_art", "emblem": "sf_emblem"})  # fmt: skip
    return MenuPack(record, spec, title, script, images, files, notes + title.notes)


SCREEN_SHOT_ORDER = ("title", "main", "level_select", "settings", "controls", "credits", "pause",
                     "results", "game_over", "victory")  # fmt: skip


def menu_shots(build_dir: Path, script: dict, fps: int = 30) -> tuple[dict[str, bytes], list[str]]:
    """Every screen of the build's menus as the player sees it (PNG at game size), and errors.
    Screens over play are taken a second into level 1."""
    shots, errors = {}, []
    screens = [s for s in SCREEN_SHOT_ORDER if s in script["screens"]]
    with serve(Path(build_dir)) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 320, "height": 180})
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{url}/index.html?headless=1&reset=1&god=1")
            if not wait_until(page, READY):
                return {}, ["the build never finished booting"]
            for sid in screens:
                if sid in {"pause", "results", "game_over", "victory"}:
                    if page.evaluate("window.sf.flow.state") == "title":
                        page.evaluate("window.sf.flow.newGame(1)")
                        page.evaluate(f"window.sfRun(1, {fps}, true)")
                    page.evaluate(f"window.sf.flow.preview({json.dumps(sid)})")
                else:
                    if page.evaluate("window.sf.flow.state") != "title":
                        page.evaluate("window.sf.flow.toTitle()")
                        page.evaluate(f"window.sfRun(0.2, {fps}, true)")
                    page.evaluate(f"window.sf.flow.preview({json.dumps(sid)})")
                data = page.evaluate(f"window.sfShot({fps})")
                shots[sid] = base64.b64decode(data.split(",", 1)[1])
            errors += page.evaluate("window.sf.errors")
        finally:
            browser.close()
    return shots, list(dict.fromkeys(errors))[:10]


def menu_sheet(shots: dict[str, bytes]) -> bytes | None:
    """Every screen on one sheet (two columns, each with its name)."""
    if not shots:
        return None
    images = [(sid, Image.open(io.BytesIO(data)).convert("RGB")) for sid, data in shots.items()]
    w, h = images[0][1].size
    cols, label = 2, 14
    rows = (len(images) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (w + 8) + 8, rows * (h + label + 8) + 8), "#1b1d27")
    draw = ImageDraw.Draw(sheet)
    for i, (sid, img) in enumerate(images):
        x, y = 8 + (i % cols) * (w + 8), 8 + (i // cols) * (h + label + 8)
        draw.text((x, y), sid.replace("_", " "), fill="#e8e8f0")
        sheet.paste(img, (x, y + label))
    out = io.BytesIO()
    sheet.save(out, "PNG")
    return out.getvalue()


def plan_art(models: Models, design: GameDesign, motion: MotionRecord | None = None) -> ArtPlan:
    prompt = art_prompt(design, library_context(design.camera, design), motion)
    return models.ask("Art Director", prompt, ArtPlan, keyword_plan(design), "art_plan")


def write_motion(models: Models, design: GameDesign, plan: PlanRecord | None = None,
                 levels: list[LevelRecord] | None = None,
                 previous: MotionRecord | None = None) -> MotionRecord:  # fmt: skip
    """The Art Director's motion design (L2.4): rows per entity, forms, effects; fitted and
    checked by code. `levels`: what they open decides which entities need a break or die row.
    `previous` (a design change): entities that still exist keep their rows (their locked art
    is not redrawn); no call when every entity has rows already."""
    breaks = breaks_of(levels) if levels else breaks_from_design(design)
    offline = fallback_brief(design, plan, breaks)
    if previous is not None:
        have = set(previous.entities) | {r.entity for r in previous.brief.rows}
        new = [e for e in design.entities if e.id not in have and e.role != "background"]
        if not new:
            brief = keep_rows(previous, AnimationBrief(rows=[], forms=[], effects=[]), design)
            return make_motion(brief, design, plan, breaks, ["rows kept from the previous design"])
        ids = {e.id for e in new}
        part = design.model_copy(update={"entities": new})
        offline = AnimationBrief(rows=[r for r in offline.rows if r.entity in ids], forms=[], effects=[])
        answer = models.ask("Art Director", brief_prompt(part, plan, breaks), AnimationBrief,
                            offline, "motion")  # fmt: skip
        return make_motion(keep_rows(previous, answer, design), design, plan, breaks,
                           [f"rows kept for every entity but {', '.join(e.id for e in new)}"])  # fmt: skip
    answer = models.ask("Art Director", brief_prompt(design, plan, breaks), AnimationBrief,
                        offline, "motion")  # fmt: skip
    return make_motion(answer, design, plan, breaks)


def motion_previews(art: ArtResult) -> dict[str, bytes]:
    """motion.png (every animated row as a filmstrip) and motion.gif (them playing)."""
    sprites = {}
    for eid, f in art.filled.items():
        if f.kind == "sprite":
            sprites[eid] = (f.png, f.meta)
        for lid, (data, meta) in f.extra.items():
            if "anims" in meta:
                sprites[lid] = (data, meta)
    out = {}
    if strip := filmstrips(sprites):
        out["motion.png"] = strip
    if gif := motion_gif(sprites):
        out["motion.gif"] = gif
    return out


def motion_report(art: ArtResult) -> dict:
    """report.json "motion": each entity's rows as checked by code (problems, repairs)."""
    return {
        "rows": [{**c.model_dump(), "source": f.source} for f in art.filled.values() for c in f.checks],
        "breaks": sorted(lid for f in art.filled.values() for lid, (_, m) in f.extra.items() if "anims" in m),
        "forms": art.forms() if art.motion else {},
        "effects": [x.name for x in art.motion.brief.effects] if art.motion else [],
    }


def plan_new_art(models: Models, design: GameDesign, previous: ArtPlan,
                 motion: MotionRecord | None = None) -> ArtPlan:
    """The art plan after a design change: every entity that still exists keeps its pick (so
    its locked art is reused); only entities new to the design go to the Art Director (no call
    when there are none). Entities the feedback names are redrawn by `apply_art_changes`."""
    ids = {e.id for e in design.entities}
    kept = [p for p in previous.picks if p.entity in ids]
    known = {p.entity for p in kept}
    new = [e for e in design.entities if e.id not in known]
    if not new:
        return previous.model_copy(update={"picks": kept})
    extra = plan_art(models, design.model_copy(update={"entities": new}), motion)
    added = [p for p in extra.picks if p.entity in {e.id for e in new}]
    return previous.model_copy(update={"picks": kept + added})


def muse_options(cfg: Config, **changes) -> MuseOptions:
    return replace(
        MuseOptions(
            sizes=cfg.image.sizes,
            retries=cfg.game.art_retry,
            grid_confidence=cfg.game.native_grid_confidence,
            sheet_frames=cfg.game.sheet_frames,
            adapt_size=cfg.game.adapt_size,
            workers=cfg.game.image_workers,
        ),
        **changes,
    )


def produce_art(
    models: Models,
    design: GameDesign,
    plan: ArtPlan,
    cfg: Config,
    previous: Callable[[str], dict | None] = lambda eid: None,
    motion: MotionRecord | None = None,
) -> ArtResult:
    generate, close = models.image_generator()
    try:
        options = muse_options(cfg)
        return make_art(
            design,
            plan,
            generate=generate,
            max_calls=cfg.game.max_art_calls,
            previous=previous,
            options=options,
            cache=models.image_cache(),
            motion=motion,
        )
    finally:
        close()


def art_summary(art: ArtResult) -> dict:
    return {
        "screen": list(art.screen),
        "scale": art.scale,
        "warnings": art.warnings,
        # Lists, not dicts keyed by entity id: secret scrubbing blanks keys like "key"/"monkey".
        "entities": [
            {
                "entity": eid,
                "source": f.source,
                "reasons": f.reasons,
                "muse_prompt": f.record.get("muse_prompt"),
                "request": f.record.get("request"),
                "spec": f.record.get("spec"),
                "frame": f.record.get("frame"),
            }
            for eid, f in art.filled.items()
        ],
    }


def art_sources(art: ArtResult) -> list[dict]:
    return [{"entity": eid, "source": f.source} for eid, f in art.filled.items()]


def write_code(
    models: Models,
    design: GameDesign,
    manifest: dict,
    base: GameCode | None = None,
    instruction: str = "",
    log: Callable[[str], None] = lambda message: None,
    levels: LevelPack | None = None,
) -> GameCode:
    """New code for a design, or `base` changed as `instruction` says (feedback rounds)."""
    summary = levels.summary if levels else ""
    if base is None:
        prompt = programmer_prompt(design, manifest, summary)
        return models.ask("Programmer", prompt, GameCode, mock_code(levels is not None), "code")
    return edit_code(
        models, base,
        lambda error: change_prompt(design, base, instruction, manifest, error, summary), log,
    )[0]  # fmt: skip


def edit_code(
    models: Models,
    code: GameCode,
    prompt_for: Callable[[str | None], str],
    log: Callable[[str], None],
) -> tuple[GameCode, str]:
    """Ask for edits (`CodePatch`) and apply them; when they do not apply cleanly, ask again
    for complete files. `prompt_for(patch_error)` builds the prompt. Returns (code, "edits" |
    "full files"). The offline stand-in answers with no edits: the code stays as it was."""
    patch = models.ask("Programmer", prompt_for(None), CodePatch, CodePatch(edits=[], notes=""),
                       "code")  # fmt: skip
    try:
        return apply_patch(code, patch), "edits"
    except PatchError as exc:
        log(f"  the edits did not apply ({exc}); asking for complete files")
        return models.ask("Programmer", prompt_for(str(exc)), GameCode, code, "code"), "full files"


@dataclass
class BuildOutcome:
    code: GameCode
    report: dict
    build: Path | None
    attempts: list[dict]
    # The kept attempt's playtest screenshots for the art review: [{"t", "png"}]
    screenshots: list[dict] = field(default_factory=list)
    # The kept attempt's QA results (run_qa: screenshots as PNG bytes); [] without QA tests
    qa: list[dict] = field(default_factory=list)


def attempt_rank(report: dict, qa_results: list[dict], n: int) -> tuple:
    """How good an attempt is: everything passes, then it booted, then the playtest passed,
    then more design checks, more QA tests, fewer runtime errors; a tie goes to the later
    attempt. A repair can make a game worse (live 2026-11-02: the last repair broke the weapon
    pods that the attempt before it had working), so the best attempt is kept, not the last."""
    booted = report["stage"] != "static" and report.get("ended") != "no-boot"
    qa_passed = sum(r["passed"] for r in qa_results)
    return (
        report["passed"] and qa_passed == len(qa_results),
        booted,
        report["passed"],
        sum(c["ok"] for c in report.get("checks", [])),
        qa_passed,
        -len(report.get("errors", [])),
        n,
    )


def build_and_test(
    models: Models,
    design: GameDesign,
    art: ArtResult,
    code: GameCode,
    cfg: Config,
    out: Path,
    log: Callable[[str], None] = print,
    qa: list[QATest] | None = None,
    levels: LevelPack | None = None,
    menus: MenuPack | None = None,
    sounds: dict | None = None,
) -> BuildOutcome:
    """Static checks, build, playtest with the bot (each level on its own for a game with
    levels as data), then the QA tests (real key presses); failures of either go back to the
    Programmer (bounded). `menus`: the SDK's menus and flow go into every build; `sounds`
    (`window.SF_SOUNDS`) the sounds and their editor."""
    manifest, images = game_assets(art, menus, sounds)
    asset_ids = set(manifest["sprites"]) | set(manifest["images"])
    attempts, report, build_dir, shots, qa_results = [], {}, None, [], []
    best = None  # (rank, attempt index, code, report, shots, qa results)
    capture = review_times(design.session_seconds) if cfg.game.review else []

    def build(files: GameCode) -> Path:
        return build_game(out / "build", title=design.title, controls=design.controls,
                          files=files.as_dict(), assets=manifest, images=images,
                          credits=art.credits, levels=levels.script if levels else None,
                          menus=menus.script if menus else None, sounds=sounds)  # fmt: skip

    for n in range(cfg.game.max_repairs + 1):
        folder = out / "attempts" / str(n + 1)
        (folder / "files").mkdir(parents=True, exist_ok=True)
        for f in code.files:
            (folder / "files" / f.path.replace("/", "_")).write_text(f.content, encoding="utf-8")
        (folder / "notes.txt").write_text(code.notes, encoding="utf-8")
        problems = static_check(code, asset_ids, cfg.game.max_code_kb)
        shots, qa_results = [], []
        if problems:
            report = {"passed": False, "stage": "static", "problems": problems}
            failures = "Static checks failed (the game was not run):\n" + "\n".join(problems)
        else:
            build_dir = build(code)
            result = play(build_dir, design, cfg, levels, capture)
            shots = result.pop("screenshots", [])
            report = {"stage": "playtest", **result}
            failures = failure_summary(report) if not report["passed"] else ""
            if qa and report["ended"] != "no-boot":
                qa_results = run_qa(build_dir, qa, cfg.game.playtest_fps)
                failed = sum(not r["passed"] for r in qa_results)
                report["qa"] = {"passed": len(qa_results) - failed, "failed": failed,
                                "results": without_pictures(qa_results)}  # fmt: skip
                if failed:
                    failures = "\n".join(p for p in (failures, qa_failures(qa_results)) if p)
        qa_ok = all(r["passed"] for r in qa_results)
        ok = report["passed"] and qa_ok
        save_json(folder / "report.json", report)
        attempts.append(
            {
                "attempt": n + 1,
                "passed": ok,
                "stage": report["stage"] if not report["passed"] or qa_ok else "qa",
                "summary": "all checks passed" if ok else failures,
            }
        )
        if report.get("qa"):
            attempts[-1]["qa"] = {k: report["qa"][k] for k in ("passed", "failed")}
        status = "passed" if ok else f"failed at {attempts[-1]['stage']}"
        log(f"  attempt {n + 1}: {status}")
        rank = attempt_rank(report, qa_results, n)
        if best is None or rank > best[0]:
            best = (rank, n, code, report, shots, qa_results)
        if ok or n == cfg.game.max_repairs:
            break
        log("  Programmer is repairing the game")
        summary = levels.summary if levels else ""
        fixed, how = edit_code(
            models,
            code,
            lambda error, c=code, f=failures, s=summary: repair_prompt(design, c, f, manifest, error, s),
            log,
        )
        if fixed == code:  # the mock (or a stuck model) returned the same files: stop early
            break
        attempts[-1]["repair"] = how
        code = fixed
    _, kept, code, report, shots, qa_results = best
    attempts[kept]["kept"] = True
    if kept != len(attempts) - 1:
        log(f"  keeping attempt {kept + 1}: the later repairs made the game worse")
        if report["stage"] != "static":
            build_dir = build(code)  # the build folder holds the last attempt: rebuild the kept one
    if report["stage"] == "static":
        build_dir, shots = None, []  # no answer passed static checks: no build
    return BuildOutcome(code, report, build_dir, attempts, shots, qa_results)


def play(build_dir: Path, design: GameDesign, cfg: Config, levels: "LevelPack | None",
         capture: list[float] = ()) -> dict:  # fmt: skip
    """The bot playtest: the whole session, or each level on its own."""
    if levels:
        return playtest_levels(build_dir, design.checks, levels.playtest(),
                               fps=cfg.game.playtest_fps, capture=bool(capture))  # fmt: skip
    return playtest(build_dir, design.checks, design.session_seconds, fps=cfg.game.playtest_fps,
                    capture_at=capture)  # fmt: skip


def replay(design: GameDesign, art: ArtResult, code: GameCode, cfg: Config, out: Path,
           levels: "LevelPack | None" = None, menus: "MenuPack | None" = None,
           sounds: dict | None = None):  # fmt: skip
    """Build the same code with other art and play the session again: (build, report)."""
    manifest, images = game_assets(art, menus, sounds)
    build_dir = build_game(
        out / "build",
        title=design.title,
        controls=design.controls,
        files=code.as_dict(),
        assets=manifest,
        images=images,
        credits=art.credits,
        levels=levels.script if levels else None,
        menus=menus.script if menus else None,
        sounds=sounds,
    )
    result = play(build_dir, design, cfg, levels)
    return build_dir, {"stage": "playtest", **result}


@dataclass
class Reviewed:
    art: ArtResult  # with the kept fixes
    outcome: BuildOutcome  # replayed when art was fixed
    plan: ArtPlan  # the plan with the kept fixes as Muse picks (a later version locks them)
    answer: ArtReview  # what the reviewer answered (by sheet number)
    report: dict  # report.json "art_review"
    images: dict[str, bytes]  # review/<name>: sheets, screenshots, before/after pictures


def review_art(
    models: Models,
    design: GameDesign,
    plan: ArtPlan,
    art: ArtResult,
    outcome: BuildOutcome,
    cfg: Config,
    out: Path,
    log: Callable[[str], None] = print,
    version: int = 1,
    qa: list[QATest] | None = None,
    levels: LevelPack | None = None,
    menus: MenuPack | None = None,
    sounds: dict | None = None,
) -> Reviewed:
    """The Art Reviewer looks at all the art together and in the running game, blocking
    issues are fixed once (capped), and the same code is played again with the fixed art (and
    the QA tests run again: art sets hitboxes, so a fix can change play). If a QA test that
    passed fails with the fixed art, the fixes are reverted too. If
    a game that passed fails with it, the fixes are reverted. No second review."""
    slots = review_slots(art)
    findings = precheck(design, art)
    sheet, zoomed = review_sheets(art, slots)
    shots = [screenshot(s["png"]) for s in outcome.screenshots]
    images = {"sheet.png": sheet, "sheet-4x.png": zoomed}
    images |= {f"shot-{i + 1}.png": data for i, data in enumerate(shots)}
    strips = motion_previews(art).get("motion.png") if art.motion else None
    if strips:  # the motion design's rows as filmstrips (L2.4)
        images["motion.png"] = strips
    offline = fallback_review(slots, findings, design)
    failed, start = None, models.budget.spent()
    try:
        answer = models.ask(
            "Art Reviewer",
            review_prompt(design, art, slots, findings, len(shots), rows=bool(strips),
                          levels=[s["level"] for s in outcome.screenshots if "level" in s]),
            ArtReview,
            offline,
            "review",
            images=[sheet, zoomed, *shots, *([strips] if strips else [])],
        )
    except Exception as exc:  # noqa: BLE001 -- the review is advice; the build stands without it
        failed = f"{type(exc).__name__}: {str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ''}"
        log(f"  the vision review failed ({failed}); using the pre-checks alone")
        answer = offline
    issues, named, notes = entity_issues(answer, slots, design, art)
    review_cost = models.budget.spent() - start
    g = cfg.game
    cap = fix_cap(g.review_max_fixes, g.review_cost_cap, review_cost, cfg.budget.image_cost)
    why = (
        f"fix cap ({cap}) reached"
        if cap == g.review_max_fixes
        else f"review cost cap reached (${g.review_cost_cap:.2f}; the review cost "
        f"${review_cost:.3f}, {cap} fix(es) fit)"
    )
    fixes = fix_requests(plan_fixes(issues, design, art, cap, why), issues, design, art)
    if fixes:
        log(f"  fixing {len(fixes)} blocking issue(s): {', '.join(f.entity for f in fixes)}")
        generate, close = models.image_generator()
        try:
            options = muse_options(cfg, retries=0, reasoning=cfg.game.review_image_reasoning)
            notes += run_fixes(fixes, design, plan, art, generate=generate, options=options,
                               cache=models.image_cache(), version=version)  # fmt: skip
        finally:
            close()
    fix_cost = models.budget.spent() - start - review_cost
    kept = [fx for fx in fixes if fx.after is not None]
    fixed_art, replayed = art, None
    if kept:
        filled = {eid: f for eid, f in art.filled.items()}
        filled |= {fx.entity: fx.after for fx in kept}
        credits = art.credits + ([MUSE_CREDIT] if MUSE_CREDIT not in art.credits else [])
        fixed_art = replace(art, filled=filled, credits=credits,
                            picks=art.picks | {fx.entity: fx.pick for fx in kept})  # fmt: skip
        log("  playing the game again with the fixed art")
        build_dir, report = replay(design, fixed_art, outcome.code, cfg, out, levels, menus, sounds)
        replayed = {"passed": report["passed"], "reverted": False}
        new_qa = (
            run_qa(build_dir, qa, cfg.game.playtest_fps)
            if qa and outcome.qa and report["ended"] != "no-boot"
            else []
        )
        # QA tests that passed with the old art and fail with the new (live 2026-11-01: fixed
        # player and enemy sprites changed the hitboxes, and the lose path no longer happened).
        broke = [new["name"] for new, old in zip(new_qa, outcome.qa, strict=False)
                 if old["passed"] and not new["passed"]]  # fmt: skip
        if new_qa:
            failed = sum(not r["passed"] for r in new_qa)
            replayed["qa"] = {"passed": len(new_qa) - failed, "failed": failed}
        if (outcome.report["passed"] and not report["passed"]) or broke:
            replay(design, art, outcome.code, cfg, out, levels, menus, sounds)  # back to the art that passed
            replayed["reverted"] = True
            if outcome.report["passed"] and not report["passed"]:
                why = "reverted: the game failed its playtest with the new art"
                notes.append(f"{why} ({failure_summary(report).splitlines()[0]})")
            else:
                why = "reverted: QA tests failed with the new art"
                notes.append(f"{why}: " + ", ".join(f"'{n}'" for n in broke))
            for fx in kept:
                fx.notes.append(why)
                fx.after = None
            kept, fixed_art = [], art
        else:
            if new_qa:
                report["qa"] = {**replayed["qa"], "results": without_pictures(new_qa)}
            outcome = replace(outcome, report=report, build=build_dir, qa=new_qa or outcome.qa)
    for fx in fixes:
        for i in issues:
            if i.n in fx.issues:
                i.status = "fixed" if fx.after else "not fixed: " + ("; ".join(fx.notes) or "?")
        if fx.after:
            images[f"{fx.entity}-before.png"] = thumb(fx.before)
            images[f"{fx.entity}-after.png"] = thumb(fx.after)
    effort = cfg.llm.reasoning.get("review")
    if failed:
        reviewed_by = f"pre-checks only (the vision review failed: {failed})"
    elif models.mock and not models.provider:
        reviewed_by = "pre-checks only (offline stand-in)"
    else:
        reviewed_by = f"{models.name} (vision" + (f", reasoning {effort})" if effort else ")")
    report = {
        "reviewed_by": reviewed_by,
        "scores": answer.scores.model_dump(),
        "named": named,
        "findings": [f.model_dump() for f in findings],
        "issues": [i.dump() for i in issues],
        "fixes": [
            {
                "entity": fx.entity,
                "issues": fx.issues,
                "mode": fx.mode,
                "fixed": fx.after is not None,
                "notes": fx.notes,
                "before": f"{fx.entity}-before.png" if fx.after else None,
                "after": f"{fx.entity}-after.png" if fx.after else None,
            }
            for fx in fixes
        ],
        "replay": replayed,
        "cost": {
            "review": round(review_cost, 4),
            "fixes": round(fix_cost, 4),
            "cap": g.review_cost_cap,
        },
        "notes": notes,
        "images": sorted(images),
    }
    return Reviewed(fixed_art, outcome, reviewed_plan(plan, kept), answer, report, images)


def qa_report(results: list[dict], tests: list[QATest], note: str = "") -> dict:
    """report.json "qa": every test with its purpose, steps, result and screenshot files
    (`screenshot_files` names them first)."""
    return {
        "passed": sum(r["passed"] for r in results),
        "failed": sum(not r["passed"] for r in results),
        "note": note,
        "tests": [
            {
                "name": r["name"],
                "purpose": r.get("purpose", ""),
                "god": r.get("god", False),
                "passed": r["passed"],
                "failed_step": r["failed_step"],
                "failure": r["failure"],
                "steps": r["steps"],
                "state": r["state"],
                "game_seconds": r["game_seconds"],
                "screenshots": [s["file"] for s in r["screenshots"] if "file" in s],
            }
            for r in results
        ],
        "count": len(tests),
    }


def save_qa(out: Path, outcome: BuildOutcome, tests: list[QATest], fps: int, note: str) -> dict:
    """qa.json, qa/<test>-<label>.png and build/tests/test_game_qa.py; returns the report."""
    pictures = screenshot_files(outcome.qa)
    for name, data in pictures.items():
        (out / "qa").mkdir(exist_ok=True)
        (out / "qa" / name).write_bytes(data)
    report = qa_report(outcome.qa, tests, note)
    save_json(out / "qa.json", report)
    if outcome.build:
        report["export"] = str(write_export(outcome.build, tests, fps))
    return report


def menus_report(menus: MenuPack, shots: list[str], errors: list[str], prefix: str = "") -> dict:
    """report.json "menus": the screens shot, the title art and what code repaired."""
    return {
        "screens": [f"{prefix}menus/{sid}.png" for sid in shots],
        "sheet": f"{prefix}menus.png" if shots else None,
        "title_art": {**menus.title_art.record(), "title": f"{prefix}menus/title-art.png",
                      "emblem": f"{prefix}menus/emblem.png"},
        "theme": menus.spec.theme.model_dump(),
        "notes": menus.notes,
        "problems": menus.record.problems,
        "errors": errors,
    }  # fmt: skip


def menu_files(menus: MenuPack, build: Path, fps: int) -> tuple[dict[str, bytes], list[str], list[str]]:
    """menus/<screen>.png, menus.png, the title art (and Muse's raw pictures): the files a
    build's menus leave, with the screens shot and the errors seen."""
    shots, errors = menu_shots(build, menus.script, fps)
    files = {f"menus/{sid}.png": data for sid, data in shots.items()}
    if sheet := menu_sheet(shots):
        files["menus.png"] = sheet
    files["menus/title-art.png"] = menus.title_art.title
    files["menus/emblem.png"] = menus.title_art.emblem
    files |= {f"menus/{part}-raw.png": data for part, data in menus.title_art.raw.items()}
    return files, list(shots), errors


def save_menus(out: Path, menus: MenuPack, build: Path, fps: int) -> dict:
    files, shots, errors = menu_files(menus, build, fps)
    for name, data in files.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(data)
    save_json(out / "menus" / "title-art.json", menus.title_art.record())
    return menus_report(menus, shots, errors)


def save_art(out: Path, art: ArtResult) -> None:
    save_json(out / "art.json", art_summary(art))
    if sheet := art_sheet(art.filled):
        (out / "art.png").write_bytes(sheet)
    for eid, f in art.filled.items():
        if f.record.get("raw"):  # what Muse returned, before keying and fitting
            (out / "art-raw").mkdir(exist_ok=True)
            (out / "art-raw" / f"{eid}.png").write_bytes(f.record["raw"])


def call_summary(store: Store, run_id: str) -> dict:
    with store.connect() as db:
        calls = list(ledger(db, run_id).values())
    return {
        "chat": sum(1 for c in calls if not c.image),
        "image": sum(1 for c in calls if c.image),
        "failed": sum(1 for c in calls if c.status == "failed"),
        "estimated_cost_usd": round(sum(c.estimated_cost for c in calls), 4),
    }


def make_game(
    request: str,
    out: Path,
    cfg: Config,
    *,
    mock: bool = False,
    provider=None,
    images=None,
    record: bool = True,
    log: Callable[[str], None] = print,
    story: bool = False,
) -> MakeResult:
    """Build a game for `request` into `out` (replaced). `story`: the human asked for a story
    (named characters, a world, an arc); without it there is no story call. Never raises on a
    failing game: the report says what failed; it raises only on budget exhaustion or provider
    errors."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    store = Store(out / "run.sqlite3")
    run = store.create_run(
        {"workflow": "game-make", "budget": cfg.budget.model_dump(), "mock": mock}
    )
    cache = None if mock else Path(cfg.workspace).expanduser() / "cache" / "muse-image"
    models = Models(cfg, Budget(store, run.id, cfg.budget), mock, provider, images, cache)

    log("design: Game Designer is writing the design doc")
    design = write_design(models, request)
    save_json(out / "design.json", design.model_dump())
    (out / "design.md").write_text(design_markdown(design), encoding="utf-8")
    log(
        f"  {design.title}: {len(design.entities)} entities, {len(design.checks)} checks, "
        f"{design.session_seconds} s session"
    )
    story_notes = []
    if story:
        log("story: Game Designer is writing the story bible (asked for with --story)")
        bible, story_notes = write_story(models, design, request)
        save_json(out / "story.json", bible.model_dump())
        (out / "story.md").write_text(story_markdown(bible), encoding="utf-8")
        log(f"  {bible.logline} ({len(bible.characters)} characters)")
    structure = make_structure(design, None, False)
    save_json(out / "structure.json", structure.model_dump())
    (out / "structure.md").write_text(structure_markdown(structure), encoding="utf-8")
    menus_record = None
    if cfg.game.menus:
        log("menus: Menu Designer is writing the menus")
        menus_record = write_menus(models, design, structure, why="written from the design")
        save_json(out / "menus.json", menus_record.model_dump())
        (out / "menus.md").write_text(menus_markdown(menus_record.spec, menus_record.notes,
                                                     menus_record.problems), encoding="utf-8")  # fmt: skip
        log(f"  {len(menus_record.spec.screens)} screens"
            + (f", {len(menus_record.problems)} problem(s) code could not fix" if menus_record.problems else ""))
    sound_record = None
    if cfg.game.sounds:
        log("sounds: Sound Director is choosing a sound for every moment")
        sound_record = write_sounds(models, design, False, menus_record is not None,
                                    why="written from the design")  # fmt: skip
        save_json(out / "sounds.json", sound_record.model_dump())
        (out / "sounds.md").write_text(sounds_markdown(sound_record), encoding="utf-8")
        log(f"  {len(sound_record.sounds.cues)} sounds"
            + (f", {len(sound_record.problems)} problem(s)" if sound_record.problems else ""))
    sounds = sounds_script(sound_record, design.title) if sound_record else None
    qa_tests, qa_note = None, ""
    if cfg.game.qa:
        log("qa: QA Playtester is writing tests from the design")
        flow = menus_script(menus_record.spec, structure, design) if menus_record else None
        qa_tests, qa_note = write_qa_tests(models, design, flow)
        save_json(out / "qa-tests.json", qa_tests.model_dump())
        log(f"  {len(qa_tests.tests)} QA tests" + (f" ({qa_note})" if qa_note else ""))

    motion = None
    if cfg.game.motion:
        log("motion: Art Director is designing how each thing moves and reacts")
        motion = write_motion(models, design)
        save_json(out / "motion.json", motion.model_dump())
        (out / "motion.md").write_text(motion_markdown(motion), encoding="utf-8")
        log(f"  {len(motion.brief.rows)} rows, {len(motion.brief.forms)} forms, "
            f"{len(motion.brief.effects)} effects")  # fmt: skip
    log("art: Art Director is choosing library art (Muse Image for anything missing)")
    plan = plan_art(models, design, motion)
    save_json(out / "art-plan.json", plan.model_dump())
    start = time.perf_counter()
    art = produce_art(models, design, plan, cfg, motion=motion)
    art_seconds = round(time.perf_counter() - start, 1)
    log(f"  art took {art_seconds} s")
    save_art(out, art)
    for eid, f in art.filled.items():
        log(f"  {eid}: {f.source}")

    menus = None
    if menus_record:
        log("menus: the title art (Muse Image in the game's style, else drawn by code)")
        menus = menu_pack(models, menus_record, design, art, cfg)
        log(f"  title art: {menus.title_art.source}")
    log("code: Programmer is writing the game")
    code = write_code(models, design, game_assets(art, menus, sounds)[0])
    outcome = build_and_test(models, design, art, code, cfg, out, log,
                             qa_tests.tests if qa_tests else None, menus=menus, sounds=sounds)  # fmt: skip
    review = None
    if cfg.game.review and outcome.build:
        log("review: Art Reviewer is looking at the art in the running game")
        review = review_art(models, design, plan, art, outcome, cfg, out, log,
                            qa=qa_tests.tests if qa_tests else None, menus=menus, sounds=sounds)  # fmt: skip
        art, outcome = review.art, review.outcome
        save_json(out / "art-review.json", review.answer.model_dump())
        for name, data in review.images.items():
            (out / "review").mkdir(exist_ok=True)
            (out / "review" / name).write_bytes(data)
        if review.plan != plan:
            save_json(out / "art-plan-reviewed.json", review.plan.model_dump())
        save_art(out, art)
        scores = review.report["scores"]
        log(f"  scores {scores}; {len(review.report['issues'])} issue(s), "
            f"{sum(f['fixed'] for f in review.report['fixes'])} fixed")  # fmt: skip
    save_json(out / "playtest.json", outcome.report)
    qa = None
    if qa_tests:
        qa = save_qa(out, outcome, qa_tests.tests, cfg.game.playtest_fps, qa_note)
        log(f"  QA: {qa['passed']} of {qa['passed'] + qa['failed']} tests pass")

    save_json(out / "code.json", outcome.code.model_dump())
    for name, data in motion_previews(art).items():
        (out / name).write_bytes(data)
    menu_report = None
    if menus and outcome.build:
        log("menus: a screenshot of every screen")
        menu_report = save_menus(out, menus, outcome.build, cfg.game.playtest_fps)

    recording = None
    if outcome.build and record:
        log("record: capturing the autoplay")
        recording = record_game(
            outcome.build, out, seconds=cfg.game.record_seconds, scale=4 // art.scale
        )
    summary = {
        "request": request,
        "title": design.title,
        "chosen_by": models.name,
        "passed": bool(outcome.report["passed"]),
        "play": str(outcome.build / "index.html") if outcome.build else None,
        "art": art_sources(art),
        "art_seconds": art_seconds,
        "warnings": art.warnings,
        "attempts": outcome.attempts,
        "art_review": review.report if review else None,
        "qa": qa,
        "story": bool(story),
        "motion": motion_report(art) if motion else None,
        "menus": menu_report,
        "sounds": sound_report(sound_record, 1, outcome.report.get("sounds", {})) if sound_record else None,
        "recording": recording.summary if recording else None,
        "model_calls": call_summary(store, run.id),
    }
    save_json(out / "report.json", summary)
    log("record: writing the game's OKF bundle (db/)")
    db = open_db(out / "db", store, run.id)
    sync_record(db, folder_record(out, request), "Game record: build v1 (game make)")
    summary["game_db"] = {"path": str(db.root), "concepts": len(db.scan()[0]),
                          "story_notes": story_notes}  # fmt: skip
    save_json(out / "report.json", summary)
    return MakeResult(out, outcome.build, design, summary)
