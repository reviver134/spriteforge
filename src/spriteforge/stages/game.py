"""G2 design gate and G4 playtest gate: Muse Spark writes the game from the brief.

After the G1 questions:
  game/design/vK.json/.md    Game Designer: rules, entities, progression, difficulty, checks
  game/design/vK-story.json/.md  Game Designer: the story bible (characters tied to entities,
                             world, arc, canon), shown at G2 and revised with the design
  game/db/                   the game database: the canon as OKF entries (story, characters with
                             their locked art, world, gameplay, art, levels), written from the
                             artifacts; approvals promote it to human-reviewed
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
  game/design/vK-qa-tests.json  QA Playtester: tests written from design vK (once per version)
  game/vN/qa-tests.json      the suite build vN runs: its design's tests plus earlier versions'
                             (a regression suite), minus tests feedback dropped
  game/vN/qa.json            QA results (real key presses after the bot playtest); qa/ their
                             screenshots; test_game_qa.py the Playwright export (also in
                             build/tests/, so the play route serves it)
  game/vN/poster.png         (+ demo.mp4 with ffmpeg)
  game/vN/report.json        what was built and by whom, art sources, warnings, attempts, play link
  gate "playtest"            text -> Director splits it into design/art/code changes -> v(N+1);
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
from spriteforge.game.gamedb import open_db, promote_all, sync_record
from spriteforge.game.pipeline import (
    Models,
    art_sources,
    art_summary,
    build_and_test,
    call_summary,
    plan_art,
    produce_art,
    qa_report,
    review_art,
    revise_story,
    write_code,
    write_design,
    write_qa_tests,
    write_story,
)
from spriteforge.game.qa import QASuite, QATests, merge_suite, screenshot_files, write_export
from spriteforge.game.record import record_game
from spriteforge.game.revision import ArtChange, Revision, fallback_revision
from spriteforge.game.revision import revision_prompt as director_prompt
from spriteforge.game.story import StoryBible, story_markdown

from .asset_store import ProductionStore
from .game_record import (
    REPORT,
    next_story_name,
    run_record,
    shown,
    story_for_trigger,
    story_names,
)
from .intake import GameBrief

STAGES = {"design", "playtest"}
ADD_STORY = "Add a story"
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
    story = "" if latest_story(ctx) else " (no story: choose Add a story to get one)"
    title = (
        f"G2 Game design v{version}: {design.title}{story}. Read the rules, entities and checks; "
        "type what to change, or leave feedback empty and submit to approve and build the game"
    )
    names = [f"game/design/v{version}.json", *story_names_shown(ctx)]
    extra = {} if latest_story(ctx) else {"form_schema": STORY_FORM}
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("design", title, [ctx.artifacts[n].id for n in names],
                 empty_feedback_approves=True, **extra),
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


def qa_suite(
    ctx: GameContext, version: int, design: GameDesign, design_version: int, drop=()
) -> QASuite | None:
    """The QA tests build vN runs: the previous build's suite minus `drop`, plus the QA
    Playtester's tests for this design version (written once per design version)."""
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
            tests, note = write_qa_tests(ctx.models, design)
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
        outcome = build_and_test(ctx.models, design, art, code, ctx.cfg, ctx.root / prefix, log,
                                 suite.tests if suite else None)  # fmt: skip
        review = None
        if ctx.cfg.game.review and outcome.build:
            log(f"build v{version}: the Art Reviewer is looking at the art in the running game")
            review = review_art(ctx.models, design, plan, art, outcome, ctx.cfg,
                                ctx.root / prefix, log, version,
                                suite.tests if suite else None)  # fmt: skip
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
    extra = {} if story else {"form_schema": STORY_FORM}
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate("playtest", title, [ctx.artifacts[n].id for n in names],
                 empty_feedback_approves=True, **extra),
    )


def gate_names(ctx: GameContext, version: int) -> list[str]:
    """What G4 shows for build vN: the video or poster, the art sheet, the QA export, the
    report and the design it was built from."""
    prefix = f"game/v{version}"
    design_version = ctx.read(f"{prefix}/report.json")["design_version"]
    names = [n for n in (f"{prefix}/demo.mp4", f"{prefix}/poster.png") if n in ctx.artifacts][:1]
    names += [n for n in (f"{prefix}/art.png", f"{prefix}/test_game_qa.py") if n in ctx.artifacts]
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
        plan = ctx.once(
            "game/v1/art-plan.json", ArtPlan, lambda: plan_art(ctx.models, design), "Art Director"
        )
        suite = qa_suite(ctx, 1, design, version)
        open_playtest_gate(ctx, 1, build_version(ctx, 1, design, version, plan, None, "",
                                                 suite=suite))  # fmt: skip
        return False

    if last.stage == "playtest" and last.status == "changes_requested":
        shown_version = shown(ctx, last, REPORT)
        version = shown_version + 1
        previous = ctx.read(f"game/v{shown_version}/report.json")
        design_version = previous["design_version"]
        design = load_design(ctx, design_version)
        feedback = feedback or "Try a different take."
        applied = suggestion_revision(previous, design, last.response.form.get("apply_suggestion"))
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

        role = "Art Reviewer" if applied else "Director"
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
        if changed and (has_story or revision.story_changes.strip()):
            ask = "\n".join(p for p in (revision.design_changes, revision.story_changes) if p.strip())
            ensure_story(ctx, f"design-v{design_version}", design, ask)
        elif revision.story_changes.strip():
            ensure_story(ctx, f"gate-{n}", design, revision.story_changes)
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
        suite = qa_suite(ctx, version, design, design_version, revision.drop_qa_tests)
        names = build_version(
            ctx, version, design, design_version, plan, base, instruction, revision, suite
        )
        open_playtest_gate(ctx, version, names)
        return False

    # Entering from the G1 follow-ups: write design v1 (and the story, only when the brief asks
    # for one) and ask the human to approve it.
    design = ctx.once(
        "game/design/v1.json",
        GameDesign,
        lambda: write_design(ctx.models, request_text(ctx.brief)),
        "Game Designer",
    )
    save_design(ctx, 1, design)
    if ctx.brief.wants_story:
        ensure_story(ctx, "design-v1", design)
    sync_db(ctx, "design v1")
    open_design_gate(ctx, 1)
    return False
