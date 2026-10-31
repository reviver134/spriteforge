"""G2 design gate and G4 playtest gate: Muse Spark writes the game from the brief.

After the G1 questions:
  game/design/vK.json/.md    Game Designer: rules, entities, progression, difficulty, checks
  gate "design"              text -> design v(K+1); empty -> build v1 from the approved design
  game/vN/art-plan.json      Art Director: library first, Muse Image for whatever is missing
  game/vN/art/<id>.*         each entity's art (+ Muse raw and request, so art stays locked)
  game/vN/art.png            contact sheet
  game/vN/code.json          the Programmer's files after static checks, playtest and repairs
  game/vN/playtest.json      the full-session playtest with the game's own bot
  game/vN/poster.png         (+ demo.mp4 with ffmpeg)
  game/vN/report.json        what was built and by whom, art sources, warnings, attempts, play link
  gate "playtest"            text -> Director splits it into design/art/code changes -> v(N+1);
                             empty -> approval.json, run complete
The playable build is runs/<id>/game/vN/build, served at /api/runs/<id>/play/vN/index.html.
"""

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
    write_code,
    write_design,
)
from spriteforge.game.record import record_game
from spriteforge.game.revision import Revision, fallback_revision
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
        self.models = Models(self.cfg, budget, self.run.config["mock"])
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
            code = write_code(ctx.models, design, art.manifest(), base, instruction)
        else:
            code = base  # feedback touched only the art: same code, new assets
        outcome = build_and_test(ctx.models, design, art, code, ctx.cfg, ctx.root / prefix, log)
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
        revision = ctx.once(
            f"game/v{version}/revision.json",
            Revision,
            lambda: ctx.models.ask(
                "Director",
                director_prompt(
                    design, {a["entity"]: a["source"] for a in previous["art"]}, feedback
                ),
                Revision,
                fallback_revision(design, feedback),
            ),
            "Director",
        )
        changed = bool(revision.design_changes.strip())
        if changed:
            old = design
            design_version = design_versions(ctx) + 1
            design = ctx.once(
                f"game/design/v{design_version}.json",
                GameDesign,
                lambda: ctx.models.ask(
                    "Game Designer", revision_prompt(old, revision.design_changes), GameDesign, old
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
            else ArtPlan.model_validate(ctx.read(f"game/v{version - 1}/art-plan.json"))
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
