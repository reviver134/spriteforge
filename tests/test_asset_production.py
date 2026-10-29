import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from spriteforge.app.server import create_app
from spriteforge.config import BudgetConfig, load_config
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.providers.base import ImageResult
from spriteforge.providers.mock import MockImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider
from spriteforge.stages.asset_context import ProductionContext
from spriteforge.stages.asset_models import (
    AssetSpec,
    Critique,
    Manifest,
    ManifestProposal,
    ObjectReading,
    ProductionPlan,
    SetIssue,
    SetReview,
    dependents,
)
from spriteforge.stages.asset_render import generate_task
from spriteforge.stages.asset_store import ProductionStore, digest
from spriteforge.stages.concept import png
from spriteforge.stages.production import enable_production
from spriteforge.stages.workflow import advance_until_gate, start_run


def spec(ident="hero", kind="character", **kwargs):
    return AssetSpec(
        id=ident,
        type=kind,
        size=(48, 48) if kind == "character" else (16, 16),
        frames={"idle": 1, "walk": 2} if kind == "character" else {"idle": 1},
        desc="A hero" if kind == "character" else "A stone floor",
        depends_on=[],
        refs=["anchor"],
        **kwargs,
    )


def answer(store, ident, **values):
    ScriptedHuman([Feedback(action="approve", **values)]).answer(Gates(store), ident)
    advance_until_gate(store, ident)


def approved_run(tmp_path, cap=150):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.budget = BudgetConfig(max_image_calls=cap)
    run = start_run(store, cfg, gameplay_preview=False)
    advance_until_gate(store, run.id)
    for _ in range(5):
        answer(store, run.id)
    assert store.get_run(run.id).status == "completed"
    enable_production(store, run.id)
    return store, run.id


@pytest.fixture
def fast_images(monkeypatch):
    """Keep provider request contracts intact, return small offline image fixtures."""
    seen = []
    original = MockImageProvider.edit

    def edit(provider, request):
        seen.append(request.model_copy(deep=True))
        return original(provider, request.model_copy(update={"size": (64, 64)}))

    monkeypatch.setattr(MockImageProvider, "edit", edit)
    return seen


def image_calls(store, ident):
    return sum(
        bool(e.payload.get("image")) for e in store.events(ident) if e.type == "cost.reserved"
    )


def test_manifest_graph_and_frame_validation():
    hero = spec()
    item = spec("item", "item").model_copy(update={"refs": ["hero"]})
    manifest = Manifest(assets=[item, hero])
    tasks = manifest.tasks()
    assert tasks[0].id == "hero"
    assert dependents(tasks, {"hero"}) == {t.id for t in tasks}
    for assets in [
        [hero, hero],
        [hero.model_copy(update={"depends_on": ["missing"]})],
        [hero.model_copy(update={"depends_on": ["hero"]})],
        [hero.model_copy(update={"depends_on": ["item"]}), item],
    ]:
        with pytest.raises(ValueError):
            Manifest(assets=assets)
    with pytest.raises(ValidationError):
        spec("../escape")
    with pytest.raises(ValidationError):
        AssetSpec.model_validate({**hero.model_dump(), "frames": {"walk": 2}})
    idle_animation = AssetSpec.model_validate({**hero.model_dump(), "frames": {"idle": 3}})
    tasks = Manifest(assets=[idle_animation]).tasks()
    assert [t.id for t in tasks] == ["hero", "hero__idle_1", "hero__idle_2"]
    assert all(t.base == "hero" and t.depends_on == ["hero"] for t in tasks[1:])


def test_g3_revision_confirmation_dependencies_resume_and_provenance(
    tmp_path, fast_images, monkeypatch
):
    store, ident = approved_run(tmp_path)
    original_ask = ProductionContext.ask

    def set_mismatch(ctx, name, role, prompt, schema, fallback, images=()):
        if schema == SetReview and "/cycles/0/" in name and "hero" in prompt:
            fallback = SetReview(
                style_match=2,
                issues=[
                    SetIssue(
                        asset_ids=["hero"],
                        issue="Hero scale differs from the set",
                        fix="Make the hero rounder",
                    )
                ],
            )
        return original_ask(ctx, name, role, prompt, schema, fallback, images)

    monkeypatch.setattr(ProductionContext, "ask", set_mismatch)
    advance_until_gate(store, ident)
    ctx = ProductionContext(store, ident)
    manifest = Manifest.model_validate(ctx.read("production/manifest.json"))
    first = ctx.records()
    assert len(first) == len(manifest.tasks())
    assert all(r.status == "accepted" for r in first.values())
    assert Gates(store).pending(ident).stage == "assets"
    assert any(n.endswith(".gif") for n in ctx.artifacts)
    assert all(ctx.read(r.provenance)["attempts"] for r in first.values())
    assert ctx.read("production/cycles/0/consistency.json")["issues"][0]["asset_ids"] == ["hero"]
    count = image_calls(store, ident)
    advance_until_gate(Store(store.path), ident)
    assert image_calls(store, ident) == count
    base = ctx.data(first["hero"].artifact)
    anchor = ctx.data("anchor.png")
    assert all(anchor in req.references for req in fast_images)
    assert any(
        req.references[:2] == [base, anchor] and "ONLY its pose" in req.prompt
        for req in fast_images
    )
    # Invalid targets cannot consume the pending gate.
    gate = Gates(store).pending(ident)
    with TestClient(create_app(store)) as client:
        response = client.post(
            f"/api/runs/{ident}/gates/{gate.id}",
            json={"action": "request_changes", "text": "Rounder", "form": {"targets": "unknown"}},
        )
        assert response.status_code == 409
    assert Gates(store).pending(ident).id == gate.id
    ScriptedHuman(
        [Feedback(action="request_changes", text="Make the hero rounder", form={"targets": "hero"})]
    ).answer(Gates(store), ident)
    advance_until_gate(store, ident)
    confirm = Gates(store).pending(ident)
    assert confirm.kind == "confirm_changes"
    assert set(confirm.changes.requests[0].target) == {"hero", "hero__walk_0", "hero__walk_1"}
    assert image_calls(store, ident) == count
    answer(store, ident)
    after = ProductionStore(store, ident).records()
    assert not ProductionStore(store, ident).read("production/cycles/1/consistency.json")["issues"]
    assert image_calls(store, ident) == count + 3
    for name, record in first.items():
        assert after[name].version == (2 if name.startswith("hero") else 1)
        if not name.startswith("hero"):
            assert after[name] == record
    answer(store, ident)
    assert store.get_run(ident).status == "completed"
    assert (tmp_path / "runs" / ident / "production/approval.json").exists()
    with pytest.raises(PermissionError, match="locked"):
        ctx.lock(first["hero"], ProductionPlan(versions={"hero": 1}, instructions={}))


def test_budget_stop_preserves_accepted_assets(tmp_path, fast_images):
    store, ident = approved_run(tmp_path, cap=3)
    advance_until_gate(store, ident)
    assert store.get_run(ident).status == "budget_exhausted"
    assert image_calls(store, ident) == 3  # Includes G2's concept.
    assert len(ProductionStore(store, ident).records()) == 2
    advance_until_gate(store, ident)
    assert image_calls(store, ident) == 3
    from spriteforge.app.server import create_app

    with TestClient(create_app(store)) as client:
        data = client.get(f"/api/runs/{ident}").json()
        assert "stopped" in data["error"] and "saved" in data["error"]
        assert len(data["production"]["assets"]) == 2


def test_seam_repairs_are_edits_then_deterministic(tmp_path, monkeypatch, fast_images):
    store, ident = approved_run(tmp_path)
    ctx = ProductionContext(store, ident)
    task = Manifest(assets=[spec("ground", "tile")]).tasks()[0]
    seen = []
    image = Image.new("RGB", (16, 16), ctx.style.palette[0])
    for y in range(16):
        image.putpixel((15, y), tuple(int(ctx.style.palette[-1][i : i + 2], 16) for i in (1, 3, 5)))

    def bad_seams(provider, request):
        provider.budget.reserve("mock", "synthetic-image", image=True)
        seen.append(request)
        return ImageResult(data=png(image))

    monkeypatch.setattr(MockImageProvider, "edit", bad_seams)
    record = generate_task(ctx, task, ProductionPlan(versions={task.id: 1}, instructions={}))
    assert record.attempts == 3 and record.deterministic_fix
    assert record.status == "accepted"
    # The third identical repair request reuses the content-addressed raw cache.
    assert [r.reasoning_strength for r in seen] == ["low", "high"]
    assert any(e.type == "asset.cache_hit" for e in store.events(ident))
    assert "Repair opposite edges" in seen[1].prompt
    assert len(seen[1].references) >= 2


def test_semantic_failure_becomes_placeholder_and_blind_reading_has_no_description(
    tmp_path, monkeypatch, fast_images
):
    store, ident = approved_run(tmp_path)
    ctx = ProductionContext(store, ident)
    task = Manifest(assets=[spec()]).tasks()[0]
    original = ctx.ask
    readings = []

    def reject(name, role, prompt, schema, fallback, images=()):
        if schema == ObjectReading:
            readings.append((prompt, Image.open(io.BytesIO(images[0])).size))
        if schema == Critique:
            fallback = Critique(
                style_match=1,
                readability=1,
                matches_desc=False,
                issues=["Looks like a box"],
                fix="Use a clear hero silhouette",
            )
        return original(name, role, prompt, schema, fallback, images)

    monkeypatch.setattr(ctx, "ask", reject)
    record = generate_task(ctx, task, ProductionPlan(versions={task.id: 1}, instructions={}))
    assert record.status == "placeholder" and record.attempts == 3
    assert "Looks like a box" in record.issues
    assert len(fast_images) == 3
    assert all(task.spec.desc not in p and size == (384, 384) for p, size in readings)
    before = image_calls(store, ident)
    assert (
        generate_task(ctx, task, ProductionPlan(versions={task.id: 1}, instructions={})) == record
    )
    assert image_calls(store, ident) == before


@pytest.mark.parametrize("crash_point", ["cache", "critic"])
def test_resume_after_raw_checkpoint_uses_cache_and_skips_accepted(
    tmp_path, monkeypatch, fast_images, crash_point
):
    store, ident = approved_run(tmp_path)
    ctx = ProductionContext(store, ident)
    task = Manifest(assets=[spec()]).tasks()[0]
    plan = ProductionPlan(versions={task.id: 1}, instructions={})

    def crash(*args, **kwargs):
        raise RuntimeError("process interrupted after raw save")

    if crash_point == "critic":
        monkeypatch.setattr(ctx, "ask", crash)
    else:
        save = ctx.save

        def crash_before_raw(name, *args, **kwargs):
            if name.endswith("/raw.png"):
                crash()
            return save(name, *args, **kwargs)

        monkeypatch.setattr(ctx, "save", crash_before_raw)
    with pytest.raises(RuntimeError):
        generate_task(ctx, task, plan)
    before = image_calls(store, ident)
    fresh = ProductionContext(store, ident)
    record = generate_task(fresh, task, plan)
    assert record.status == "accepted" and image_calls(store, ident) == before
    if crash_point == "cache":
        assert any(e.type == "asset.cache_hit" for e in store.events(ident))
    # Reconstructing the filesystem from SQLite does not dispatch a new call.
    path = fresh.root / record.artifact
    path.unlink()
    fresh.restore_files()
    assert digest(path.read_bytes()) == digest(fresh.data(record.artifact))


def test_production_model_schemas_are_strict(context, monkeypatch):
    config, _, _, budget = context
    monkeypatch.setenv("META_API_KEY", "fake-test-key")

    def inspect_schema(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema["additionalProperties"] is False
                assert set(schema.get("required", [])) == set(schema["properties"])
            for value in schema.values():
                inspect_schema(value)
        elif isinstance(schema, list):
            for value in schema:
                inspect_schema(value)

    def handler(request):
        inspect_schema(json.loads(request.content)["response_format"]["json_schema"]["schema"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    from spriteforge.providers.base import ChatRequest

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        for model in [ManifestProposal, Critique, ObjectReading, SetReview]:
            MuseSparkProvider(config.llm, budget, client).chat(
                ChatRequest(messages=[], json_schema=model.model_json_schema())
            )
