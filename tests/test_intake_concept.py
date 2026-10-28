import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from spriteforge.app.server import create_app
from spriteforge.config import BudgetConfig, load_config
from spriteforge.core.cost import BudgetExceeded
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.stages.intake import GameBrief
from spriteforge.stages.workflow import start_run, workflow_step


def setup(tmp_path, cap=150):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.budget = BudgetConfig(max_image_calls=cap)
    run = start_run(store, cfg)
    workflow_step(store, run.id)
    return store, run.id


def answer(store, ident, **kwargs):
    ScriptedHuman([Feedback(action="approve", **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)


def to_concept(store, ident):
    answer(store, ident, form={"genre": "genres/side-scrolling-platformer"})
    assert "jump" in json.dumps(Gates(store).pending(ident).form_schema).lower()
    answer(store, ident, form={"environment": "Cloud city", "controls": "touch"})
    gate = Gates(store).pending(ident)
    assert 2 <= len(gate.form_schema["properties"]) <= 4
    assert "Cloud city" in json.dumps(gate.form_schema)
    answer(store, ident)
    answer(store, ident)


def test_scripted_human_concept_revision_resume_and_lock(tmp_path):
    store, ident = setup(tmp_path)
    to_concept(store, ident)
    root = tmp_path / "runs" / ident
    brief = GameBrief.model_validate_json((root / "brief.json").read_text())
    assert brief.perspective == "side-scroll" and brief.controls == "touch"
    assert Image.open(root / "concept/v1-native.png").size == (320, 180)
    assert Image.open(root / "concept/v1-pixel.png").size == (960, 540)
    first = (root / "concept/v1.png").read_bytes()
    ScriptedHuman([Feedback(action="request_changes", text="Make the sky purple")]).answer(
        Gates(store), ident
    )
    workflow_step(store, ident)
    assert Gates(store).pending(ident).kind == "confirm_changes"
    assert not (root / "concept/v2.png").exists()
    # A fresh store and step must leave the pending confirmation untouched.
    store = Store(store.path)
    workflow_step(store, ident)
    answer(store, ident)
    assert (root / "concept/v1.png").read_bytes() == first
    assert (root / "concept/v2.png").read_bytes() != first
    assert (
        sum(e.payload.get("image", False) for e in store.events(ident) if e.type == "cost.reserved")
        == 2
    )
    gate = Gates(store).pending(ident)
    with TestClient(create_app(store)) as client:
        response = client.post(
            f"/api/runs/{ident}/gates/{gate.id}",
            json={"action": "approve", "form": {"color_0": "bad"}},
        )
        assert response.status_code == 409
        assert Gates(store).pending(ident).id == gate.id
    answer(store, ident, form={"color_0": "#123456"})
    assert store.get_run(ident).status == "completed"
    assert json.loads((root / "style.json").read_text())["palette"][0] == "#123456"
    assert (root / "anchor.png").read_bytes() == (root / "concept/v2.png").read_bytes()
    before = (root / "style.json").read_bytes()
    workflow_step(store, ident)
    assert (root / "style.json").read_bytes() == before


def test_revision_budget_and_abort(tmp_path):
    store, ident = setup(tmp_path, cap=1)
    to_concept(store, ident)
    ScriptedHuman([Feedback(action="request_changes", text="Change pose")]).answer(
        Gates(store), ident
    )
    workflow_step(store, ident)
    with pytest.raises(BudgetExceeded):
        answer(store, ident)
    assert not (tmp_path / "runs" / ident / "concept/v2.png").exists()
    assert (
        len([e for e in store.events(ident) if e.type == "cost.reserved" and e.payload["image"]])
        == 1
    )


def test_scene_preview_keeps_corners_and_nearest_pixels():
    from spriteforge.stages.concept import png, preview

    image = Image.new("RGB", (320, 180), "#00FF00")
    image.putpixel((0, 0), (255, 0, 0))
    native, enlarged, _ = preview(png(image), (320, 180))
    small = Image.open(io.BytesIO(native))
    big = Image.open(io.BytesIO(enlarged))
    assert small.getpixel((0, 0)) != small.getpixel((50, 50))
    assert big.getpixel((2, 2)) == small.getpixel((0, 0))


def test_failed_brief_resume_preserves_notes_and_checkpoints(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from spriteforge.cli import app
    from spriteforge.stages import workflow

    store, ident = setup(tmp_path)
    notes = ["Ride a bike through zombie hordes", "Ruined city with fallen poles"]
    answer(store, ident, text=notes[0])
    original = workflow.propose

    def fail(*args):
        raise ValueError("Meta HTTP 400: additionalProperties is required")

    monkeypatch.setattr(workflow, "propose", fail)
    with pytest.raises(ValueError, match="additionalProperties") as error:
        answer(store, ident, text=notes[1], form={"perspective": "side-scroll"})
    store.fail(ident, str(error.value))
    # Reproduce a legacy draft that did not save free-text feedback.
    with store.connect() as db:
        row = db.execute(
            "SELECT id, data FROM artifacts WHERE run_id=? AND name='brief-draft.json'", (ident,)
        ).fetchone()
        draft = json.loads(row["data"])
        draft.pop("notes")
        raw = json.dumps(draft).encode()
        db.execute("UPDATE artifacts SET data=? WHERE id=?", (raw, row["id"]))
    root = tmp_path / "runs" / ident
    (root / "brief-draft.json").write_bytes(raw)
    with TestClient(create_app(store)) as client:
        assert "additionalProperties" in client.get(f"/api/runs/{ident}").json()["error"]

    captured = []

    def capture(provider, role, prompt, schema):
        captured.append(prompt)
        return original(provider, role, prompt, schema)

    monkeypatch.setattr(workflow, "propose", capture)
    monkeypatch.setattr(
        "spriteforge.cli.serve", lambda store, run_id, *args: workflow_step(store, run_id)
    )
    result = CliRunner().invoke(
        app, ["resume", ident, "--workspace", str(tmp_path), "--no-open-browser"]
    )
    assert result.exit_code == 0, result.output
    assert Gates(store).pending(ident).stage == "followups"
    assert all(note in captured[0] for note in notes)
    assert (root / "brief-draft.json").read_bytes() == raw
    assert json.loads((root / "brief-notes.json").read_text())["notes"] == notes
    answer(store, ident)
    assert json.loads((root / "brief.json").read_text())["notes"] == notes
    assert all(note in captured[1] for note in notes)
    with TestClient(create_app(store)) as client:
        assert client.get(f"/api/runs/{ident}").json()["error"] is None
    with pytest.raises(ValueError, match="Only a failed run"):
        store.retry_failed(ident)
