"""G2 design gate and G4 playtest gate: Muse Spark writes the game from the brief.

After the G1 questions:
  game/design/vK.json/.md    Game Designer: rules, entities, progression, difficulty, checks
  gate "design"              text -> design v(K+1); empty -> build v1 from the approved design
  game/vN/art-plan.json      Art Director: library first, Muse Image for whatever is missing
  game/vN/art/<id>.*         each entity's art (+ Muse raw and request, so art stays locked)
  game/vN/art.png            contact sheet
  game/vN/code.json          the Programmer's files after static checks, playtest and repairs
  game/vN/playtest.json      the full-session playtest with the game's own bot
  game/vN/art-review.json    Art Reviewer (vision): scores, naming test, issues by sheet number;
                             review/ holds its inputs and before/after pictures of fixed art;
                             art-plan-reviewed.json the plan with the fixes (the next version
                             starts from it, so fixed art stays locked)
  game/vN/poster.png         (+ demo.mp4 with ffmpeg)
  game/vN/report.json        what was built and by whom, art sources, warnings, attempts, play link
  gate "playtest"            text -> Director splits it into design/art/code changes -> v(N+1);
                             Apply on an art-review suggestion (form apply_suggestion=n) -> that
                             entity is redrawn as the suggestion says (no Director call);
                             empty -> approval.json, run complete
The playable build is runs/<id>/game/vN/build, served at /api/runs/<id>/play/vN/index.html.
"""

from pathlib import Path

from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates, new_gate
from spriteforge.game.art import ArtPick, ArtPlan, art_sheet
from spriteforge.game.codegen import GameCode
from spriteforge.game.design import GameDesign, design_markdown, revision_prompt
from spriteforge.game.pipeline import (
    Models,
    art_sources,
    art_summary,
    build_and_test,
    call_summary,
    plan_art,
    produce_art,
    review_art,
    write_code,
    write_design,
)
from spriteforge.game.record import record_game
from spriteforge.game.revision import ArtChange, Revision, fallback_revision
from spriteforge.game.revision import revision_prompt as director_prompt

from .asset_store import ProductionStore
from .intake import GameBrief

STAGES = {"design", "playtest"}


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


def design_versions(ctx: GameContext) -> int:
    return sum(1 for n in ctx.artifacts if n.startswith("game/design/v") and n.endswith(".json"))


def load_design(ctx: GameContext, version: int) -> GameDesign:
    return GameDesign.model_validate(ctx.read(f"game/design/v{version}.json"))


def save_design(ctx: GameContext, version: int, design: GameDesign) -> None:
    name = f"game/design/v{version}"
    if f"{name}.json" not in ctx.artifacts:
        ctx.save(f"{name}.json", design.model_dump(), "Game Designer")
    if f"{name}.md" not in ctx.artifacts:
        ctx.save(f"{name}.md", design_markdown(design).encode(), "Game Designer", "text/markdown")


def open_design_gate(ctx: GameContext, version: int) -> None:
    design = load_design(ctx, version)
    title = (
        f"G2 Game design v{version}: {design.title}. Read the rules, entities and checks; type "
        "what to change, or leave feedback empty and submit to approve and build the game"
    )
    names = [f"game/design/v{version}.json"]
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate(
            "design", title, [ctx.artifacts[n].id for n in names], empty_feedback_approves=True
        ),
    )


def previous_art(ctx: GameContext, version: int):
    """Muse records of the previous build, so unchanged requests reuse their art."""

    def lookup(eid: str) -> dict | None:
        base = f"game/v{version - 1}/art/{eid}"
        if f"{base}.json" not in ctx.artifacts or f"{base}-raw.png" not in ctx.artifacts:
            return None
        record = ctx.read(f"{base}.json")
        return {**record, "raw": ctx.data(f"{base}-raw.png"), "png": ctx.data(f"{base}.png")}

    return lookup


def build_version(
    ctx: GameContext,
    version: int,
    design: GameDesign,
    design_version: int,
    plan: ArtPlan,
    base: GameCode | None,
    instruction: str,
    revision: Revision | None = None,
) -> list[str]:
    """Art, code, playtest, repair and recording for one build; returns gate artifact names."""
    prefix = f"game/v{version}"
    if f"{prefix}/report.json" not in ctx.artifacts:
        log = ctx.log("Director")
        log(f"build v{version}: art (library first, Muse Image for the rest)")
        art = produce_art(ctx.models, design, plan, ctx.cfg, previous_art(ctx, version))
        if base is None or instruction.strip():
            log(f"build v{version}: the Programmer is writing the game")
            code = write_code(ctx.models, design, art.manifest(), base, instruction, log)
        else:
            code = base  # feedback touched only the art: same code, new assets
        outcome = build_and_test(ctx.models, design, art, code, ctx.cfg, ctx.root / prefix, log)
        review = None
        if ctx.cfg.game.review and outcome.build:
            log(f"build v{version}: the Art Reviewer is looking at the art in the running game")
            review = review_art(ctx.models, design, plan, art, outcome, ctx.cfg,
                                ctx.root / prefix, log, version)  # fmt: skip
            art, outcome = review.art, review.outcome
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
        report = {
            "version": version,
            "title": design.title,
            "design_version": design_version,
            "chosen_by": ctx.models.name,
            "passed": bool(outcome.report["passed"]),
            "play": f"/api/runs/{ctx.run_id}/play/v{version}/index.html" if outcome.build else None,
            "checks": outcome.report.get("checks", []),
            "problems": outcome.report.get("problems", []) + outcome.report.get("errors", []),
            "art": art_sources(art),
            "warnings": art.warnings,
            "attempts": outcome.attempts,
            "revision": revision.model_dump() if revision else None,
            "art_review": review_report(review.report, prefix) if review else None,
            "model_calls": call_summary(ctx.store, ctx.run_id),
        }
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
    names = [n for n in (f"{prefix}/demo.mp4", f"{prefix}/poster.png") if n in ctx.artifacts][:1]
    names += [n for n in (f"{prefix}/art.png",) if n in ctx.artifacts]
    return [*names, f"{prefix}/report.json", f"game/design/v{design_version}.json"]


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
    return Revision(design_changes="", art_changes=[change], code_instructions="")


def last_plan(ctx: GameContext, version: int) -> ArtPlan:
    """A build's final art plan: with the art review's fixes when it made any."""
    reviewed = f"game/v{version}/art-plan-reviewed.json"
    name = reviewed if reviewed in ctx.artifacts else f"game/v{version}/art-plan.json"
    return ArtPlan.model_validate(ctx.read(name))


def open_playtest_gate(ctx: GameContext, version: int, names: list[str]) -> None:
    report = ctx.read(f"game/v{version}/report.json")
    verdict = "all checks passed" if report["passed"] else "SOME CHECKS FAILED (see the report)"
    title = (
        f"G4 Playtest v{version}: {report['title']}, {verdict}. Watch the autoplay or play it; "
        "type what to change, or leave feedback empty and submit to approve"
    )
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate(
            "playtest", title, [ctx.artifacts[n].id for n in names], empty_feedback_approves=True
        ),
    )


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


def game_step(store, run_id) -> bool:
    ctx = GameContext(store, run_id)
    history = Gates(store).all(run_id)
    last = history[-1]
    builds = [g for g in history if g.stage == "playtest"]
    feedback = ((last.response.text if last.response else "") or "").strip()

    if last.stage == "playtest" and last.status == "approved":
        version = len(builds)
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
        store.advance(run_id, ctx.run.cursor, "G4 approved; game build locked", final=True)
        return False

    if last.stage == "design" and last.status == "changes_requested":
        version = design_versions(ctx) + 1
        previous = load_design(ctx, version - 1)
        design = ctx.once(
            f"game/design/v{version}.json",
            GameDesign,
            lambda: ctx.models.ask(
                "Game Designer",
                revision_prompt(previous, feedback or "Try a different take."),
                GameDesign,
                previous,
                "design",
            ),
            "Game Designer",
        )
        save_design(ctx, version, design)
        open_design_gate(ctx, version)
        return False

    if last.stage == "design" and last.status == "approved":
        version = design_versions(ctx)
        design = load_design(ctx, version)
        plan = ctx.once(
            "game/v1/art-plan.json", ArtPlan, lambda: plan_art(ctx.models, design), "Art Director"
        )
        open_playtest_gate(ctx, 1, build_version(ctx, 1, design, version, plan, None, ""))
        return False

    if last.stage == "playtest" and last.status == "changes_requested":
        version = len(builds) + 1
        previous = ctx.read(f"game/v{version - 1}/report.json")
        design_version = previous["design_version"]
        design = load_design(ctx, design_version)
        feedback = feedback or "Try a different take."
        applied = suggestion_revision(previous, design, last.response.form.get("apply_suggestion"))
        revision = ctx.once(
            f"game/v{version}/revision.json",
            Revision,
            lambda: (
                applied
                or ctx.models.ask(
                    "Director",
                    director_prompt(
                        design, {a["entity"]: a["source"] for a in previous["art"]}, feedback
                    ),
                    Revision,
                    fallback_revision(design, feedback),
                    "revision",
                )
            ),
            "Art Reviewer" if applied else "Director",
        )
        changed = bool(revision.design_changes.strip())
        if changed:
            old = design
            design_version = design_versions(ctx) + 1
            design = ctx.once(
                f"game/design/v{design_version}.json",
                GameDesign,
                lambda: ctx.models.ask(
                    "Game Designer",
                    revision_prompt(old, revision.design_changes),
                    GameDesign,
                    old,
                    "design",
                ),
                "Game Designer",
            )
            save_design(ctx, design_version, design)
        base_plan = (
            ctx.once(
                f"game/v{version}/art-plan-new.json",
                ArtPlan,
                lambda: plan_art(ctx.models, design),
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
        names = build_version(
            ctx, version, design, design_version, plan, base, instruction, revision
        )
        open_playtest_gate(ctx, version, names)
        return False

    # Entering from the G1 follow-ups: write design v1 and ask the human to approve it.
    design = ctx.once(
        "game/design/v1.json",
        GameDesign,
        lambda: write_design(ctx.models, request_text(ctx.brief)),
        "Game Designer",
    )
    save_design(ctx, 1, design)
    open_design_gate(ctx, 1)
    return False
