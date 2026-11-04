"""Harness flow: G1 questions -> G2 design gate -> build -> G4 playtest gate -> approval."""

import json

from fastapi.testclient import TestClient

from spriteforge.app.server import create_app
from spriteforge.config import load_config
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.stages.workflow import start_run, workflow_step


def answer(store, ident, **kwargs):
    ScriptedHuman([Feedback(action="approve", **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)
    through_menus(store, ident)


def through_menus(store, ident):
    """The Director's plan for G4/G5 feedback (tested in test_game_confirm) and G3 (the menus,
    L2.5, before the first G4; tested in test_game_flow): approve them."""
    gate = Gates(store).pending(ident)
    if gate is not None and gate.stage == "confirm":
        ScriptedHuman([Feedback(action="submit", text="")]).answer(Gates(store), ident)
        workflow_step(store, ident)
        gate = Gates(store).pending(ident)
    if gate is not None and gate.stage == "menus":
        ScriptedHuman([Feedback(action="submit", text="")]).answer(Gates(store), ident)
        workflow_step(store, ident)


def submit(store, ident, text=""):
    """The single feedback box: text asks for a new version, empty approves."""
    ScriptedHuman([Feedback(action="submit", text=text)]).answer(Gates(store), ident)
    workflow_step(store, ident)
    through_menus(store, ident)


def read(store, ident, name):
    return json.loads(next(a for a in store.artifacts(ident) if a.name == name).data)


def to_design_gate(tmp_path, story=None):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, load_config(), mock=True, goal="survive a slime arena, collect gems")
    workflow_step(store, run.id)
    answer(store, run.id, form={"genre": "genres/top-down-dungeon-crawler"})
    form = {"environment": "A slime cave"} | ({"story": story} if story else {})
    answer(store, run.id, form=form)
    answer(store, run.id)  # follow-up questions
    return store, run.id


def test_design_gate_then_playtest_gate_with_targeted_revisions(tmp_path):
    store, ident = to_design_gate(tmp_path)
    gate = Gates(store).pending(ident)
    assert gate.stage == "design" and gate.empty_feedback_approves
    assert gate.title.startswith("G2 Game design v1")
    design = read(store, ident, "game/design/v1.json")
    assert "survive a slime arena" in design["pitch"].lower() or design["checks"]

    # Design feedback -> design v2, still at the design gate (no build yet).
    submit(store, ident, "Make the session shorter")
    gate = Gates(store).pending(ident)
    assert gate.stage == "design" and gate.title.startswith("G2 Game design v2")
    assert not any(a.name.startswith("game/v1/") for a in store.artifacts(ident))

    # Approve the design -> build v1 (art, code, playtest, recording) -> playtest gate.
    submit(store, ident)
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and "all checks passed" in gate.title
    report = read(store, ident, "game/v1/report.json")
    assert report["passed"] and report["design_version"] == 2
    assert report["play"] == f"/api/runs/{ident}/play/v1/index.html"
    assert {c["event"] for c in report["checks"]} >= {"enemy_spawn", "win"}
    names = {a.name for a in store.artifacts(ident)}
    assert {"game/v1/poster.png", "game/v1/art.png", "game/v1/code.json"} <= names
    assert (tmp_path / "runs" / ident / "game" / "v1" / "build" / "index.html").is_file()

    # Feedback naming an entity -> only that entity is redrawn; the code and other art stay.
    submit(store, ident, "make the slime bright red")
    report2 = read(store, ident, "game/v2/report.json")
    sources = {a["entity"]: a["source"] for a in report2["art"]}
    assert sources["slime"] == "muse"
    assert sources["hero"] == "muse (locked, from v1)"
    assert [c["entity"] for c in report2["revision"]["art_changes"]] == ["slime"]
    assert read(store, ident, "game/v2/art/slime.json")["edit_of"] == 1
    assert read(store, ident, "game/v2/code.json") == read(store, ident, "game/v1/code.json")

    # Behaviour feedback -> the Programmer edits the game (mock keeps it), art stays locked.
    submit(store, ident, "enemies should be faster")
    report3 = read(store, ident, "game/v3/report.json")
    assert report3["revision"]["code_instructions"] == "enemies should be faster"
    assert {a["source"] for a in report3["art"]} <= {
        "muse (locked, from v1)",
        "muse (locked, from v2)",
    }

    # Empty feedback approves G4; G5 (the final review, L2.6) follows; approving it finishes.
    submit(store, ident)
    assert Gates(store).pending(ident).stage == "final"
    submit(store, ident)
    approval = read(store, ident, "approval.json")
    assert approval["mode"] == "game" and approval["version"] == 3 and approval["passed"]
    assert store.get_run(ident).status == "completed"


def test_g4_apply_suggestion_makes_targeted_rebuild(tmp_path, monkeypatch):
    """Each build gets an art review; Apply on a suggestion at G4 redraws only that entity."""
    from spriteforge.game import pipeline
    from spriteforge.game.art_review import ArtReview, ReviewIssue, Scores

    def review(slots, findings, design):
        issue = ReviewIssue(slot=slots.index("slime") + 1, problem="too plain next to the hero",
                            severity="minor", fix_prompt="add a glossy highlight")  # fmt: skip
        scores = Scores(consistency=4, readability=4, perspective=5, scale=5, palette=4)
        return ArtReview(scores=scores, named=[], issues=[issue])

    monkeypatch.setattr(pipeline, "fallback_review", review)
    store, ident = to_design_gate(tmp_path)
    submit(store, ident)  # approve the design: build v1
    report = read(store, ident, "game/v1/report.json")
    rv = report["art_review"]
    assert rv["reviewed_by"].startswith("pre-checks only") and rv["scores"]["consistency"] == 4
    assert [(i["n"], i["entity"], i["status"]) for i in rv["issues"]] == [
        (1, "slime", "suggestion")
    ]
    names = {a.name for a in store.artifacts(ident)}
    assert {"game/v1/review/sheet-4x.png", "game/v1/review/shot-1.png"} <= names
    assert set(rv["images"]) <= names and "game/v1/art-review.json" in names

    # Apply #1: the feedback text names nothing, the form carries the suggestion.
    human = ScriptedHuman(
        [Feedback(action="submit", text="apply it", form={"apply_suggestion": 1})]
    )
    human.answer(Gates(store), ident)
    workflow_step(store, ident)
    revision = read(store, ident, "game/v2/revision.json")
    assert [c["entity"] for c in revision["art_changes"]] == ["slime"]
    assert "add a glossy highlight" in revision["art_changes"][0]["prompt"]
    assert revision["code_instructions"] == "" and revision["design_changes"] == ""
    sources = {a["entity"]: a["source"] for a in read(store, ident, "game/v2/report.json")["art"]}
    assert sources.pop("slime") == "muse"
    assert set(sources.values()) == {"muse (locked, from v1)"}


def test_qa_regression_across_versions(tmp_path):
    """Every build runs its QA suite with real key presses; v1's tests rerun on v2, and feedback
    can drop a test."""
    store, ident = to_design_gate(tmp_path)
    submit(store, ident)  # approve the design: build v1
    gate = Gates(store).pending(ident)
    qa = read(store, ident, "game/v1/report.json")["qa"]
    names = [t["name"] for t in qa["tests"]]
    assert qa["failed"] == 0 and qa["passed"] == len(names) >= 5
    assert {"loses when idle", "restarts after losing"} <= set(names)
    assert f"QA {len(names)}/{len(names)} tests pass" in gate.title
    artifacts = {a.name for a in store.artifacts(ident)}
    assert {"game/design/v1-qa-tests.json", "game/v1/qa-tests.json", "game/v1/qa.json",
            "game/v1/test_game_qa.py", "game/v1/qa/loses-when-idle-lost.png"} <= artifacts  # fmt: skip
    assert qa["export_url"] == f"/api/runs/{ident}/play/v1/tests/test_game_qa.py"
    build = tmp_path / "runs" / ident / "game" / "v1" / "build"
    assert (build / "tests" / "test_game_qa.py").is_file()

    # "ignore the restart test": dropped from v2; the other v1 tests rerun (no new QA call, the
    # design is the same) and the code is not touched.
    submit(store, ident, "ignore the restart test")
    revision = read(store, ident, "game/v2/revision.json")
    assert revision["drop_qa_tests"] == ["restarts after losing"]
    assert revision["code_instructions"] == "" and revision["art_changes"] == []
    qa2 = read(store, ident, "game/v2/report.json")["qa"]
    tests2 = [t for t in qa2["tests"] if t.get("kind") != "check"]  # code checks are per build
    names1 = [t["name"] for t in qa["tests"] if t.get("kind") != "check"]
    assert [t["name"] for t in tests2] == [n for n in names1 if n != "restarts after losing"]
    assert {t["since"] for t in tests2} == {1} and qa2["dropped"] == ["restarts after losing"]
    assert qa2["failed"] == 0
    assert "game/design/v2-qa-tests.json" not in {a.name for a in store.artifacts(ident)}
    assert read(store, ident, "game/v2/code.json") == read(store, ident, "game/v1/code.json")


def bundle_of(store, ident, tmp_path):
    from spriteforge.game.gamedb import open_db

    return open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)


def test_no_story_unless_asked_and_the_game_record(tmp_path, monkeypatch):
    """Without a story there is no story call and no story concept; the bundle holds the
    design, entities with art, code modules, QA, builds and decisions; feedback asking for a
    story at G4 writes it without a rebuild; `game db sync` rebuilds the same bundle."""
    import shutil
    import stat
    from pathlib import Path

    from typer.testing import CliRunner

    from spriteforge.game import pipeline
    from spriteforge.game.cli import app as game_cli
    from spriteforge.memory.okf.validate import validate

    def no_story_call(*args):
        raise AssertionError("the story was written without being asked for")

    monkeypatch.setattr(pipeline, "story_prompt", no_story_call)
    store, ident = to_design_gate(tmp_path)
    names = lambda: {a.name for a in store.artifacts(ident)}
    gate = Gates(store).pending(ident)
    assert read(store, ident, "brief.json")["story"] == "No story"
    assert gate.form_schema["properties"]["story"]["enum"] == ["No story", "Add a story"]
    assert not any("story" in n for n in names())
    db = bundle_of(store, ident, tmp_path)
    ids = {e.id for e in db.scan()[0]}
    assert {"game", "design/rules", "design/events", "entities/hero", "decisions/1"} <= ids
    assert not any(i.split("/")[0] in {"story", "characters", "world"} for i in ids)

    submit(store, ident)  # approve G2: promoted, then build v1 adds art, code, QA, the build
    tiers = {e.id: e.metadata.trust_tier for e in db.scan()[0]}
    assert tiers["design/rules"] == tiers["decisions/1"] == "human-reviewed"
    # New, or changed since the human saw it (the hero's entity now shows its art): unverified.
    assert tiers["code/modules/game"] == tiers["builds/v1"] == tiers["entities/hero"] == "unverified"
    assert {"art/assets/hero", "code/overview", "qa/suite", "qa/tests/loses-when-idle"} <= set(tiers)
    report = read(store, ident, "game/v1/report.json")
    # G3's decision (the menus approved) was recorded after build v1's report.
    assert report["game_db"]["concepts"] == len(tiers) - 1 and "decisions/2" in tiers
    root = db.root
    built = tmp_path / "runs" / ident / "game/v1/build/game.js"  # (text: the build has CRLF)
    assert (root / "references/code/game.js").read_text("utf-8") == built.read_text("utf-8")
    assert validate(db, external=False).errors == []

    # Feedback that asks for a story: the story is written, no new build, G4 shows v1 again.
    monkeypatch.undo()
    submit(store, ident, "add a story: the slime is a lost prince")
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and gate.title.startswith("G4 Playtest v1")
    assert not any(n.startswith("game/v2/") for n in names())
    story = [n for n in names() if n.startswith("game/story/") and n.endswith(".json")]
    assert story == ["game/story/v1-gate-6.json"]  # gate 6: G3 (the menus) is gate 5
    assert {a.id for a in store.artifacts(ident) if a.name == story[0]} <= set(gate.artifacts_to_show)
    assert "properties" not in gate.form_schema or "story" not in gate.form_schema["properties"]
    ids = {e.id for e in db.scan()[0]}
    assert {"story/overview", "story/canon", "characters/hero", "decisions/4"} <= ids
    assert "story changed" in next(e for e in db.scan()[0] if e.id == "decisions/4").body

    submit(store, ident)  # approve G4, then G5: the whole record is human-reviewed
    submit(store, ident)
    assert store.get_run(ident).status == "completed"
    stable = [e for e in db.scan()[0] if e.metadata.status == "stable"]
    assert {e.metadata.trust_tier for e in stable} == {"human-reviewed"}
    assert validate(db, external=False).errors == []

    # `game db sync` rebuilds the same bundle from the run's artifacts.
    before = {e.id: (e.body, e.metadata.resource) for e in db.scan()[0]}
    shutil.rmtree(root, onexc=lambda f, p, e: (Path(p).chmod(stat.S_IWRITE), f(p)))
    out = CliRunner().invoke(game_cli, ["db", "sync", str(tmp_path / "runs" / ident)])
    assert out.exit_code == 0, out.output
    db = bundle_of(store, ident, tmp_path)
    assert {e.id: (e.body, e.metadata.resource) for e in db.scan()[0]} == before


def test_story_when_the_brief_asks_and_add_a_story_at_g2(tmp_path):
    """G1 "Yes" writes the story with design v1; "Add a story" at G2 writes it there and shows
    the same design again; later design versions revise it."""
    from spriteforge.stages.intake import YES_STORY

    store, ident = to_design_gate(tmp_path, story=YES_STORY)
    names = lambda: {a.name: a.id for a in store.artifacts(ident)}
    gate = Gates(store).pending(ident)
    assert names()["game/story/v1-design-v1.json"] in gate.artifacts_to_show
    assert "story" not in gate.form_schema.get("properties", {})
    db = bundle_of(store, ident, tmp_path / "")
    assert {"story/overview", "characters/hero", "world/arena"} <= {e.id for e in db.scan()[0]}
    submit(store, ident, "Make the session shorter")  # design v2: its story is revised
    assert names()["game/story/v2-design-v2.json"] in Gates(store).pending(ident).artifacts_to_show

    # The draft never suggests a story the human did not ask for; "Add a story" at G2 does.
    other = tmp_path / "other"
    other.mkdir()
    store2, ident2 = to_design_gate(other)
    gate = Gates(store2).pending(ident2)
    ScriptedHuman([Feedback(action="submit", text="", form={"story": "Add a story"})]).answer(
        Gates(store2), ident2)  # fmt: skip
    workflow_step(store2, ident2)
    gate = Gates(store2).pending(ident2)
    assert gate.stage == "design" and gate.title.startswith("G2 Game design v1")
    assert "game/story/v1-gate-4.json" in {a.name for a in store2.artifacts(ident2)}
    assert not any(a.name.startswith("game/v1/") for a in store2.artifacts(ident2))


def test_brief_suggests_a_story_only_when_the_human_asks():
    from spriteforge.stages.intake import NO_STORY, YES_STORY, BriefDraft, fit_draft

    draft = BriefDraft(perspective="side-scroll", environment="caves", mood="dark",
                       character="a slime", core_loop="jump", difficulty="Balanced",
                       session_length="5 minutes", controls="keyboard", references="none",
                       story=YES_STORY)  # fmt: skip
    assert fit_draft(draft, "a slime platformer with crates").story == NO_STORY
    assert fit_draft(draft, "a slime platformer with a story about a lost prince").story == YES_STORY
    assert BriefDraft.model_validate({k: v for k, v in draft.model_dump().items()
                                      if k != "story"}).story == NO_STORY  # fmt: skip


def test_identical_requests_come_from_the_cache_and_still_lock(tmp_path):
    """A second run in the same workspace makes the same Muse requests: every image comes
    from <workspace>/cache/muse-image with no call, and a rebuild locks it like any art."""
    store, first = to_design_gate(tmp_path)
    submit(store, first)
    assert {a["source"] for a in read(store, first, "game/v1/report.json")["art"]} == {"muse"}
    cached = list((tmp_path / "cache" / "muse-image").glob("*.png"))
    assert len(cached) == 4

    _, second = to_design_gate(tmp_path)
    submit(store, second)
    report = read(store, second, "game/v1/report.json")
    assert {a["source"] for a in report["art"]} == {"muse (cached)"}
    assert report["model_calls"]["image"] == 0
    assert read(store, second, "game/v1/art/hero.json")["request"]["cached"]

    submit(store, second, "make the slime bright red")
    sources = {a["entity"]: a["source"] for a in read(store, second, "game/v2/report.json")["art"]}
    assert sources["slime"] == "muse" and sources["hero"] == "muse (locked, from v1)"


def test_play_route_serves_only_the_build(tmp_path):
    store, ident = to_design_gate(tmp_path)
    with TestClient(create_app(store)) as client:
        gate = Gates(store).pending(ident)
        r = client.post(f"/api/runs/{ident}/gates/{gate.id}", json={"action": "submit", "text": ""})
        assert r.status_code == 200, r.text
        gate = Gates(store).pending(ident)
        assert gate.stage == "menus"  # G3 first (L2.5): approved through the API too
        r = client.post(f"/api/runs/{ident}/gates/{gate.id}", json={"action": "submit", "text": ""})
        assert r.status_code == 200, r.text
        assert Gates(store).pending(ident).stage == "playtest"
        ok = client.get(f"/api/runs/{ident}/play/v1/index.html")
        assert ok.status_code == 200 and "sf.js" in ok.text
        assert client.get(f"/api/runs/{ident}/play/v1/sf.js").status_code == 200
        assert client.get(f"/api/runs/{ident}/play/v1/../../state.sqlite3").status_code == 404
        assert client.get(f"/api/runs/{ident}/play/v1/..%2F..%2Fbrief.json").status_code == 404
        assert client.get(f"/api/runs/{ident}/play/x1/index.html").status_code == 404
        # Feedback text through the API makes a new version at the playtest gate.
        gate = Gates(store).pending(ident)
        r = client.post(
            f"/api/runs/{ident}/gates/{gate.id}", json={"action": "submit", "text": "more enemies"}
        )
        assert r.status_code == 200, r.text
        assert Gates(store).pending(ident).title.startswith("G4 Playtest v2")


def test_g1_genre_comes_from_the_prompt_and_accepts_any_genre(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    goal = "a side scrolling game where a fox jumps between platforms collecting berries"
    run = start_run(store, load_config(), mock=True, goal=goal)
    workflow_step(store, run.id)
    rule = Gates(store).pending(run.id).form_schema["properties"]["genre"]
    # Free text with suggestions (not a fixed list); the default is read from the prompt.
    assert "enum" not in rule and rule["default"] == "Side-scrolling platformer"
    assert {"Turn-based roguelike", "Scrolling shmup"} <= set(rule["suggestions"])

    # A genre that is not seeded becomes a custom genre the brief and design are written for.
    answer(store, run.id, form={"genre": "Fox berry dash"})
    genre = read(store, run.id, "genre.json")
    assert genre["id"] == "custom/fox-berry-dash" and genre["title"] == "Fox berry dash"
    assert "# Adaptive questions" in genre["body"]
    brief_form = Gates(store).pending(run.id).form_schema["properties"]
    assert any(k.startswith("seed_") for k in brief_form)
    assert brief_form["perspective"]["default"] == brief_form["perspective"]["suggestions"][0]
    answer(store, run.id)
    answer(store, run.id)
    assert read(store, run.id, "brief.json")["genre"] == "custom/fox-berry-dash"
    assert Gates(store).pending(run.id).stage == "design"


def test_g1_genre_pick_helpers():
    from spriteforge.stages.intake import GenrePick, custom_genre_body, genre_form, match_genre

    class Meta:
        description = "Waves of monsters"

    class Entry:
        id, title, metadata, body = "genres/shmup", "Scrolling shmup", Meta(), ""

    genres = [Entry()]
    pick = GenrePick(
        genre="Top-down arena survival",
        description="Survive 10 minutes against monsters from the corners.",
        closest=["genres/shmup", "genres/unknown"],
        questions=["Auto-attack?", "How many upgrade choices?"],
    )
    rule = genre_form(pick, genres, "Any genre.")["properties"]["genre"]
    assert rule["suggestions"] == ["Top-down arena survival", "Scrolling shmup"]
    assert match_genre("scrolling SHMUP", genres) is genres[0]
    assert match_genre("genres/shmup", genres) is genres[0]
    assert match_genre("Top-down arena survival", genres) is None
    body = custom_genre_body(pick, genres)
    assert "1. Auto-attack?" in body and "* Scrolling shmup: Waves of monsters" in body
