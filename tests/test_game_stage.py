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

