"""Prompt -> playable game: design, art (library first, else Muse Image), code, playtest, repair.

    design.json/.md     Game Designer (Muse Spark): rules, entities, checks
    story.json/.md      Game Designer: the story bible, only with --story (the human asked)
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

import json
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from spriteforge.agents.roles import load_roles
from spriteforge.config import Config
from spriteforge.core.cost import Budget, ledger
from spriteforge.core.state import Store
from spriteforge.providers.base import ChatRequest, Message, chat_json
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.providers.muse_image import MuseImageProvider
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
    mock_code,
    programmer_prompt,
    repair_prompt,
    static_check,
)
from .dbsource import folder_record
from .design import GameDesign, design_markdown, design_prompt, fallback_design
from .gamedb import open_db, sync_record
from .image_cache import ImageCache
from .playtest import failure_summary, playtest, review_times
from .qa import (
    QATest,
    QATests,
    fallback_qa,
    qa_failures,
    qa_prompt,
    run_qa,
    screenshot_files,
    without_pictures,
    write_export,
)
from .record import record_game
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
    `provider` (tests) replaces the chat model for every role, `images` the image model.
    `cache_dir`: where identical Muse requests are cached (None: no cache)."""

    def __init__(
        self,
        cfg: Config,
        budget: Budget,
        mock: bool,
        provider=None,
        images=None,
        cache_dir: Path | None = None,
    ):
        self.cfg, self.budget, self.mock = cfg, budget, mock
        self.provider, self.images, self.cache_dir = provider, images, cache_dir

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


def write_design(models: Models, request: str) -> GameDesign:
    return models.ask(
        "Game Designer", design_prompt(request), GameDesign, fallback_design(request), "design"
    )


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


def write_qa_tests(models: Models, design: GameDesign) -> tuple[QATests, str]:
    """The QA Playtester's tests for a design, and a note when the offline tests stood in (an
    answer that broke the limits twice, or a failed call: QA is a check, the build goes on)."""
    fallback = fallback_qa(design)
    try:
        return models.ask("QA Playtester", qa_prompt(design), QATests, fallback, "qa"), ""
    except Exception as exc:  # noqa: BLE001
        first = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
        note = (
            f"the QA Playtester's tests could not be used ({type(exc).__name__}: {first}); "
            "the offline tests ran instead"
        )
        return fallback, note


def plan_art(models: Models, design: GameDesign) -> ArtPlan:
    prompt = art_prompt(design, library_context(design.camera, design))
    return models.ask("Art Director", prompt, ArtPlan, keyword_plan(design), "art_plan")


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
) -> GameCode:
    """New code for a design, or `base` changed as `instruction` says (feedback rounds)."""
    if base is None:
        prompt = programmer_prompt(design, manifest)
        return models.ask("Programmer", prompt, GameCode, mock_code(), "code")
    return edit_code(
        models, base, lambda error: change_prompt(design, base, instruction, manifest, error), log
    )[0]


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
    # The last playtest's screenshots for the art review: [{"t", "png"}]
    screenshots: list[dict] = field(default_factory=list)
    # The last attempt's QA results (run_qa: screenshots as PNG bytes); [] without QA tests
    qa: list[dict] = field(default_factory=list)


def build_and_test(
    models: Models,
    design: GameDesign,
    art: ArtResult,
    code: GameCode,
    cfg: Config,
    out: Path,
    log: Callable[[str], None] = print,
    qa: list[QATest] | None = None,
) -> BuildOutcome:
    """Static checks, build, playtest with the bot, then the QA tests (real key presses);
    failures of either go back to the Programmer (bounded)."""
    manifest = art.manifest()
    asset_ids = set(manifest["sprites"]) | set(manifest["images"])
    attempts, report, build_dir, shots, qa_results = [], {}, None, [], []
    capture = review_times(design.session_seconds) if cfg.game.review else []
    for n in range(cfg.game.max_repairs + 1):
        folder = out / "attempts" / str(n + 1)
        (folder / "files").mkdir(parents=True, exist_ok=True)
        for f in code.files:
            (folder / "files" / f.path.replace("/", "_")).write_text(f.content, encoding="utf-8")
        (folder / "notes.txt").write_text(code.notes, encoding="utf-8")
        problems = static_check(code, asset_ids, cfg.game.max_code_kb)
        if problems:
            report = {"passed": False, "stage": "static", "problems": problems}
            failures = "Static checks failed (the game was not run):\n" + "\n".join(problems)
        else:
            build_dir = build_game(
                out / "build",
                title=design.title,
                controls=design.controls,
                files=code.as_dict(),
                assets=manifest,
                images=art.files(),
                credits=art.credits,
            )
            result = playtest(
                build_dir,
                design.checks,
                design.session_seconds,
                fps=cfg.game.playtest_fps,
                capture_at=capture,
            )
            shots = result.pop("screenshots", [])
            report = {"stage": "playtest", **result}
            failures = failure_summary(report) if not report["passed"] else ""
            qa_results = []
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
        if ok or n == cfg.game.max_repairs:
            break
        log("  Programmer is repairing the game")
        fixed, how = edit_code(
            models,
            code,
            lambda error, c=code, f=failures: repair_prompt(design, c, f, manifest, error),
            log,
        )
        if fixed == code:  # the mock (or a stuck model) returned the same files: stop early
            break
        attempts[-1]["repair"] = how
        code = fixed
    if report["stage"] == "static":
        build_dir, shots = None, []  # the last answer never passed static checks: no build
    return BuildOutcome(code, report, build_dir, attempts, shots, qa_results)


def replay(design: GameDesign, art: ArtResult, code: GameCode, cfg: Config, out: Path):
    """Build the same code with other art and play the session again: (build, report)."""
    build_dir = build_game(
        out / "build",
        title=design.title,
        controls=design.controls,
        files=code.as_dict(),
        assets=art.manifest(),
        images=art.files(),
        credits=art.credits,
    )
    result = playtest(build_dir, design.checks, design.session_seconds, fps=cfg.game.playtest_fps)
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
    offline = fallback_review(slots, findings, design)
    failed, start = None, models.budget.spent()
    try:
        answer = models.ask(
            "Art Reviewer",
            review_prompt(design, art, slots, findings, len(shots)),
            ArtReview,
            offline,
            "review",
            images=[sheet, zoomed, *shots],
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
        build_dir, report = replay(design, fixed_art, outcome.code, cfg, out)
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
            replay(design, art, outcome.code, cfg, out)  # back to the art that passed
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
    qa_tests, qa_note = None, ""
    if cfg.game.qa:
        log("qa: QA Playtester is writing tests from the design")
        qa_tests, qa_note = write_qa_tests(models, design)
        save_json(out / "qa-tests.json", qa_tests.model_dump())
        log(f"  {len(qa_tests.tests)} QA tests" + (f" ({qa_note})" if qa_note else ""))

    log("art: Art Director is choosing library art (Muse Image for anything missing)")
    plan = plan_art(models, design)
    save_json(out / "art-plan.json", plan.model_dump())
    start = time.perf_counter()
    art = produce_art(models, design, plan, cfg)
    art_seconds = round(time.perf_counter() - start, 1)
    log(f"  art took {art_seconds} s")
    save_art(out, art)
    for eid, f in art.filled.items():
        log(f"  {eid}: {f.source}")

    log("code: Programmer is writing the game")
    code = write_code(models, design, art.manifest())
    outcome = build_and_test(models, design, art, code, cfg, out, log,
                             qa_tests.tests if qa_tests else None)  # fmt: skip
    review = None
    if cfg.game.review and outcome.build:
        log("review: Art Reviewer is looking at the art in the running game")
        review = review_art(models, design, plan, art, outcome, cfg, out, log,
                            qa=qa_tests.tests if qa_tests else None)  # fmt: skip
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
