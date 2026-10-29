"""Gate 2 acceptance: renderer pixels, bounded calls, revisions and immutable approval."""

import json
import shutil

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from playwright.sync_api import Error as BrowserError
from pydantic import ValidationError

from spriteforge.app.server import create_app
from spriteforge.config import load_config
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.state import Store
from spriteforge.providers.mock import MockImageProvider
from spriteforge.stages.concept import center_crop
from spriteforge.stages.intake import GameBrief
from spriteforge.stages.preview_assets import PreviewContext
from spriteforge.stages.preview_models import RoomLayout, block_manifest, example_layout
from spriteforge.stages.preview_render import capture_scene
from spriteforge.stages.workflow import advance_until_gate, start_run, workflow_step


@pytest.fixture
def small_mock(monkeypatch):
    generate = MockImageProvider.generate
    seen = []

    def image(provider, request):
        seen.append(request)
        return generate(
            provider,
            request.model_copy(
                update={"size": (192, 128) if request.size == (1536, 1024) else (64, 64)}
            ),
        )

    monkeypatch.setattr(MockImageProvider, "generate", image)
    return seen


def calls(store, ident):
    return [e for e in store.events(ident) if e.type == "cost.reserved" and e.payload["image"]]


def answer(store, ident, action="approve", **values):
    gate = Gates(store).pending(ident)
    Gates(store).respond(ident, gate.id, Feedback(action=action, **values))
    advance_until_gate(store, ident)


def setup(tmp_path, cap=16, mood=True):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.preview.max_image_calls = cap
    cfg.preview.mood_board = mood
    run = start_run(store, cfg)
    advance_until_gate(store, run.id)
    answer(store, run.id, form={"genre": "genres/side-scrolling-platformer"})
    answer(
        store,
        run.id,
        form={
            "perspective": "side-scroll",
            "environment": "Ruined city street",
            "character": "Bike rider",
        },
    )
    answer(store, run.id)
    answer(store, run.id)
    return store, run.id


def test_mood_crop_preserves_geometry_and_landscape_request(tmp_path, small_mock):
    source = Image.new("RGB", (1536, 1024), "black")
    ImageDraw.Draw(source).rectangle((500, 300, 699, 499), fill="white")
    result = center_crop(source)
    assert result.size == (1536, 864)
    box = result.getbbox()
    assert box[2] - box[0] == box[3] - box[1] == 200
    square = center_crop(Image.new("RGB", (1600, 1600)))
    assert square.width * 9 == square.height * 16
    store, ident = setup(tmp_path)
    assert Gates(store).pending(ident).stage == "mood_board"
    assert small_mock[0].size == (1536, 1024)
    assert "No text" in small_mock[0].prompt
    assert len(calls(store, ident)) == 1


def test_real_phaser_render_targeted_changes_palette_and_lock(tmp_path, small_mock):
    store, ident = setup(tmp_path)
    answer(store, ident)  # Approve mood, build blocks, render real Phaser in Chromium.
    root = tmp_path / "runs" / ident
    assert Gates(store).pending(ident).stage == "gameplay"
    assert len(calls(store, ident)) == 13
    native = np.array(Image.open(root / "preview/v1/gameplay.png").convert("RGB"))
    large = np.array(Image.open(root / "preview/v1/gameplay-4x.png"))
    assert native.shape == (180, 320, 3)
    assert np.array_equal(large, np.repeat(np.repeat(native, 4, axis=0), 4, axis=1))
    # HUD is code/font-drawn: its background differs from the heart and key text pixels.
    assert np.any(native[5:11, 7:14] != native[0, 0])
    assert np.any(native[5:11, 68:92] != native[0, 0])
    audit = json.loads((root / "preview/v1/render-audit.json").read_text())
    assert audit["hero"]["on_screen_height"] == audit["hero"]["logical_height"]
    assert audit["hero"]["on_screen_height"] < 48  # opaque artwork, not its canvas
    assert audit["sprites"] and all(s["scale_x"] == s["scale_y"] == 1 for s in audit["sprites"])
    bad = tmp_path / "scale-regression"
    shutil.copytree(root / "preview/v1", bad)
    room = bad / "runtime/room.js"
    original = room.read_text()
    # Test non-hero sprites and tiles too, not just the named hero.
    for asset, scale, error in [
        ("hero", 1.5, "Non-integer"),
        ("enemy_1", 0.5, "Non-integer"),
        ("floor_1", 1.25, "Non-integer"),
        ("hero", 2, "scale 1"),
    ]:
        room.write_text(
            original.replace(
                "const hud=",
                f"this.children.list.find(o=>o.frame?.name==='{asset}').setScale({scale});const hud=",
            )
        )
        with pytest.raises(BrowserError, match=error):
            capture_scene(bad)
    ctx = PreviewContext(store, ident)
    registry = ctx.read("preview/v1/registry.json")
    originals = {k: ctx.data(r["artifact"]) for k, r in registry.items()}
    assert not ctx.records()  # Human has not locked the draft asset set yet.
    assert all(
        r["references"]
        for n, a in ctx.artifacts.items()
        if n.startswith("preview/assets/") and n.endswith("/request.json")
        for r in [json.loads(a.data)]
    )
    gate = Gates(store).pending(ident)
    with TestClient(create_app(store)) as client:
        response = client.post(
            f"/api/runs/{ident}/gates/{gate.id}",
            json={
                "action": "request_changes",
                "text": "Change hero",
                "form": {"targets": "unknown"},
            },
        )
        assert response.status_code == 409
    answer(store, ident, "request_changes", text="Make the hero rounder", form={"targets": "hero"})
    assert Gates(store).pending(ident).stage == "preview_revise"
    workflow_step(Store(store.path), ident)
    assert len(calls(store, ident)) == 13
    answer(store, ident)
    assert len(calls(store, ident)) == 14
    ctx = PreviewContext(store, ident)
    revised = ctx.read("preview/v2/registry.json")
    assert {k for k in registry if registry[k] != revised[k]} == {"hero"}
    assert all(ctx.data(revised[k]["artifact"]) == originals[k] for k in originals if k != "hero")
    assert ctx.data("preview/v1/layout.json") == ctx.data("preview/v2/layout.json")
    answer(store, ident, "request_changes", text="Add more walls", form={"targets": "layout"})
    answer(store, ident)
    assert len(calls(store, ident)) == 14
    ctx = PreviewContext(store, ident)
    assert ctx.read("preview/v3/layout.json") != ctx.read("preview/v2/layout.json")
    answer(
        store,
        ident,
        "request_changes",
        text="Use this palette",
        form={"targets": "palette", "palette": "#101020,#404060,#88AA88,#EECC88"},
    )
    answer(store, ident)
    assert len(calls(store, ident)) == 14
    answer(store, ident)
    assert store.get_run(ident).status == "completed"
    ctx = PreviewContext(store, ident)
    assert len(ctx.records()) == 12
    style = ctx.read("style.json")
    assert style["palette"] == ctx.read("preview/v4/readability.json")["palette"]
    assert set(style["palette"]) <= {"#101020", "#404060", "#88AA88", "#EECC88"}
    for record in ctx.records().values():
        assert not ctx.read(record.provenance)["failures"]
    assert ctx.data("anchor.png") == ctx.data(ctx.records()["hero"].artifact)
    assert not workflow_step(store, ident)
    assert len(calls(store, ident)) == 14


def test_stage_cap_preserves_work(tmp_path, small_mock):
    store, ident = setup(tmp_path, cap=2)
    answer(store, ident)
    assert store.get_run(ident).status == "budget_exhausted"
    assert len(calls(store, ident)) == 2
    assert (tmp_path / "runs" / ident / "preview/assets/hero/v1/sprite.png").exists()
    assert not workflow_step(store, ident)


def test_optional_mood_requires_first_sprite_approval(tmp_path, small_mock):
    store, ident = setup(tmp_path, mood=False)
    gate = Gates(store).pending(ident)
    assert gate.stage == "sprite_anchor"
    assert len(calls(store, ident)) == 1
    ctx = PreviewContext(store, ident)
    assert "preview/reference.json" not in ctx.artifacts
    assert not any(n.startswith("mood/") for n in ctx.artifacts)
    answer(store, ident)
    ctx = PreviewContext(store, ident)
    assert ctx.read("preview/reference.json")["approved_gate"] == gate.id
    assert len(calls(store, ident)) == 12


@pytest.mark.parametrize("perspective", ["top-down", "side-scroll", "isometric"])
def test_layout_contract(perspective):
    brief = GameBrief(perspective=perspective)
    layout = example_layout(brief)
    manifest = block_manifest(brief)
    layout.validate_for(brief, manifest)
    bad = layout.model_dump()
    bad["tiles"][0] = ["floor_1"]
    with pytest.raises(ValidationError):
        RoomLayout.model_validate(bad)
    layout.environment = "Wrong environment"
    with pytest.raises(ValueError, match="brief"):
        layout.validate_for(brief, manifest)


def test_interrupted_image_is_not_replayed(tmp_path, small_mock):
    store, ident = setup(tmp_path)
    ctx = PreviewContext(store, ident)
    ctx.save("preview/interrupted/request.json", {"prompt": "already dispatched"})
    before = len(calls(store, ident))
    with pytest.raises(RuntimeError, match="interrupted"):
        ctx.image("preview/interrupted", "repeat")
    assert len(calls(store, ident)) == before


def test_terrain_presentation_background_is_not_quantized_into_tile():
    from spriteforge.pixel.schemas import Style
    from spriteforge.stages.asset_render import process
    from spriteforge.stages.concept import png

    raw = Image.new("RGB", (64, 64), "#FF00FF")
    ImageDraw.Draw(raw).rectangle((8, 8, 55, 55), fill="#454452")
    style = Style(palette=["#181825", "#454452", "#E03636"])
    task = next(t for t in block_manifest(GameBrief()).tasks() if t.id == "floor_1")
    image, failures, report = process(png(raw), task, style, scale_mode="native")
    assert not failures
    assert set(image.getdata()) == {(69, 68, 82, 255)}
    assert "Cropped chroma" in report.warnings[0]


def test_reprocessing_supersedes_only_unapproved_review(tmp_path, small_mock):
    from spriteforge.stages.preview_repair import reprocess_draft

    store, ident = setup(tmp_path)
    answer(store, ident)
    first_gate = Gates(store).pending(ident)
    ctx = PreviewContext(store, ident)
    old = ctx.data("preview/v1/gameplay.png")
    reprocess_draft(
        store,
        ident,
        ["floor_1", "prop_2"],
        "Correct deterministic tile cleanup and prop readability",
        canvas_overrides={"prop_2": (48, 32)},
    )
    assert Gates(store).all(ident)[-1].status == "superseded"
    advance_until_gate(store, ident)
    assert Gates(store).pending(ident).id != first_gate.id
    assert len(calls(store, ident)) == 13
    ctx = PreviewContext(store, ident)
    assert ctx.data("preview/v1/gameplay.png") == old
    assert ctx.read("preview/v2/registry.json")["floor_1"]["version"] == 2
    assert Image.open(ctx.root / "preview/assets/prop_2/v2/sprite.png").size == (48, 32)
    reprocess_draft(store, ident, [], "User requested a brighter hero", generate_targets=["hero"])
    advance_until_gate(store, ident)
    assert len(calls(store, ident)) == 14
    answer(store, ident)
    ctx = PreviewContext(store, ident)
    accepted = ctx.read(ctx.read("approval.json")["manifest"])
    assert next(a["size"] for a in accepted["assets"] if a["id"] == "prop_2") == [48, 32]
    with pytest.raises(ValueError, match="unapproved"):
        reprocess_draft(store, ident, ["floor_1"], "Must not edit approved art")
