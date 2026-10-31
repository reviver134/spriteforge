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


def submit(store, ident, text=""):
    """The single feedback box: text asks for a new version, empty approves."""
    ScriptedHuman([Feedback(action="submit", text=text)]).answer(Gates(store), ident)
    workflow_step(store, ident)


def read(store, ident, name):
    return json.loads(next(a for a in store.artifacts(ident) if a.name == name).data)


def to_design_gate(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, load_config(), mock=True, goal="survive a slime arena, collect gems")
    workflow_step(store, run.id)
    answer(store, run.id, form={"genre": "genres/top-down-dungeon-crawler"})
    answer(store, run.id, form={"environment": "A slime cave"})
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

    # Empty feedback approves: approval.json, run complete.
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
