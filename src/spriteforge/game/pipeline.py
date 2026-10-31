"""Prompt -> playable game: design, art (library first, else Muse Image), code, playtest, repair.

    design.json/.md     Game Designer (Muse Spark): rules, entities, checks
    art-plan.json       Art Director (Muse Spark): a library pick or a Muse request per entity
    art.json, art.png   what each entity actually got (library / tile set / Muse / placeholder)
    attempts/N/         each Programmer answer (files + notes) and its static/playtest report
    build/              the playable game (open build/index.html)
    playtest.json       the final report; poster.png (+ demo.mp4 with ffmpeg)
    report.json         summary: who decided, sources, warnings, attempts, model calls and cost
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
from dataclasses import dataclass
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
    ArtPlan,
    ArtResult,
    MuseOptions,
    art_prompt,
    art_sheet,
    keyword_plan,
    library_context,
    make_art,
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
from .design import GameDesign, design_markdown, design_prompt, fallback_design
from .image_cache import ImageCache
from .playtest import failure_summary, playtest
from .record import record_game


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

    def ask(self, role: str, prompt: str, schema, fallback, task: str | None = None):
        """`task` (design, art_plan, code, revision) picks the reasoning effort and the
        prompt cache key; prompts of one task share their stable prefix."""
        provider = self.provider or (
            MockLLMProvider(self.budget, [fallback.model_dump()])
            if self.mock
            else MuseSparkProvider(self.cfg.llm, self.budget)
        )
        try:
            messages = [
                Message(role="system", content=role_prompt(role)),
                Message(role="user", content=prompt),
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


def plan_art(models: Models, design: GameDesign) -> ArtPlan:
    prompt = art_prompt(design, library_context(design.camera, design))
    return models.ask("Art Director", prompt, ArtPlan, keyword_plan(design), "art_plan")


def produce_art(
    models: Models,
    design: GameDesign,
    plan: ArtPlan,
    cfg: Config,
    previous: Callable[[str], dict | None] = lambda eid: None,
) -> ArtResult:
    generate, close = models.image_generator()
    try:
        options = MuseOptions(
            sizes=cfg.image.sizes,
            retries=cfg.game.art_retry,
            grid_confidence=cfg.game.native_grid_confidence,
            sheet_frames=cfg.game.sheet_frames,
            adapt_size=cfg.game.adapt_size,
            workers=cfg.game.image_workers,
        )
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


def build_and_test(
    models: Models,
    design: GameDesign,
    art: ArtResult,
    code: GameCode,
    cfg: Config,
    out: Path,
    log: Callable[[str], None] = print,
) -> BuildOutcome:
    """Static checks, build, playtest; failures go back to the Programmer (bounded)."""
    manifest = art.manifest()
    asset_ids = set(manifest["sprites"]) | set(manifest["images"])
    attempts, report, build_dir = [], {}, None
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
                build_dir, design.checks, design.session_seconds, fps=cfg.game.playtest_fps
            )
            report = {"stage": "playtest", **result}
            failures = failure_summary(report)
        save_json(folder / "report.json", report)
        attempts.append(
            {
                "attempt": n + 1,
                "passed": report["passed"],
                "stage": report["stage"],
                "summary": "all checks passed" if report["passed"] else failures,
            }
        )
        status = "passed" if report["passed"] else f"failed at {report['stage']}"
        log(f"  attempt {n + 1}: {status}")
        if report["passed"] or n == cfg.game.max_repairs:
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
        build_dir = None  # the last answer never passed static checks; no build was made
    return BuildOutcome(code, report, build_dir, attempts)


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
) -> MakeResult:
    """Build a game for `request` into `out` (replaced). Never raises on a failing game: the
    report says what failed; it raises only on budget exhaustion or provider errors."""
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

    log("art: Art Director is choosing library art (Muse Image for anything missing)")
    plan = plan_art(models, design)
    save_json(out / "art-plan.json", plan.model_dump())
    start = time.perf_counter()
    art = produce_art(models, design, plan, cfg)
    art_seconds = round(time.perf_counter() - start, 1)
    log(f"  art took {art_seconds} s")
    save_json(out / "art.json", art_summary(art))
    if sheet := art_sheet(art.filled):
        (out / "art.png").write_bytes(sheet)
    for eid, f in art.filled.items():
        log(f"  {eid}: {f.source}")
        if f.record.get("raw"):  # what Muse returned, before keying and fitting
            (out / "art-raw").mkdir(exist_ok=True)
            (out / "art-raw" / f"{eid}.png").write_bytes(f.record["raw"])

    log("code: Programmer is writing the game")
    code = write_code(models, design, art.manifest())
    outcome = build_and_test(models, design, art, code, cfg, out, log)
    save_json(out / "playtest.json", outcome.report)

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
        "recording": recording.summary if recording else None,
        "model_calls": call_summary(store, run.id),
    }
    save_json(out / "report.json", summary)
    return MakeResult(out, outcome.build, design, summary)
