import io
import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from typer.testing import CliRunner

from spriteforge.app.server import create_app
from spriteforge.cli import app
from spriteforge.config import BudgetConfig, init_workspace, load_config
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.demo import demo_step
from spriteforge.core.state import Artifact, Store


def test_workspace_preserves_user_files(tmp_path):
    root = init_workspace(tmp_path / "workspace")
    (root / "mcp.json").write_text("USER SETTINGS")
    assert init_workspace(root) == root
    assert (root / "mcp.json").read_text() == "USER SETTINGS"
    assert all((root / name).is_dir() for name in ("runs", "memory", "skills"))


def test_invalid_config(tmp_path):
    path = tmp_path / "invalid.yaml"
    path.write_text("workspace: .\nunknown: true")
    with pytest.raises(ValidationError):
        load_config(path)


def test_resume_from_database_only(context, tmp_path):
    _, store, run, _ = context
    for _ in range(5):
        assert demo_step(store, run.id)
    before = store.events(run.id)
    copied = tmp_path / "restored.sqlite3"
    with store.connect() as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(store.path, copied)
    restored = Store(copied)
    while demo_step(restored, run.id):
        pass
    assert restored.get_run(run.id).cursor == 10
    assert restored.get_run(run.id).status == "completed"
    assert restored.events(run.id)[: len(before)] == before
    assert len(restored.artifacts(run.id)) == 2
    for artifact in restored.artifacts(run.id):
        assert Image.open(io.BytesIO(artifact.data)).size == (32, 32)
    assert len([e for e in restored.events(run.id) if e.type == "cost.reserved"]) == 3
    count = len(restored.events(run.id))
    assert not demo_step(restored, run.id)
    assert len(restored.events(run.id)) == count


def test_atomic_checkpoint_and_event(context):
    _, store, run, _ = context
    invalid = Artifact(id="bad", run_id="wrong", name="bad", data=b"bad")
    with pytest.raises(ValueError):
        store.advance(run.id, 0, "should rollback", invalid)
    assert store.get_run(run.id).cursor == 0
    assert len(store.events(run.id)) == 1
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.advance(run.id, 0, "once"), range(2)))
    assert sorted(results) == [False, True]
    assert len(store.events(run.id)) == 2


def test_event_journal_is_append_only(context):
    _, store, _, _ = context
    with store.connect() as db:
        for statement in ("UPDATE events SET agent='other'", "DELETE FROM events"):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.execute(statement)


def test_failure_is_persisted_and_resumable(context):
    _, store, run, _ = context
    demo_step(store, run.id)
    store.fail(run.id, "test failure")
    restarted = Store(store.path)
    assert restarted.get_run(run.id).status == "failed"
    assert restarted.events(run.id)[-1].type == "run.failed"
    assert demo_step(restarted, run.id)
    assert restarted.get_run(run.id).cursor == 2
    assert restarted.get_run(run.id).status == "running"


@pytest.mark.parametrize(
    "config,image",
    [
        (BudgetConfig(max_image_calls=1), True),
        (BudgetConfig(max_calls_per_model=1), False),
        (BudgetConfig(max_estimated_cost=0.01), True),
        (BudgetConfig(max_total_calls=1), False),
    ],
)
def test_persistent_budget_caps(context, config, image):
    _, store, run, _ = context
    Budget(store, run.id, config).reserve("provider", "model", image)
    with pytest.raises(BudgetExceeded):
        Budget(Store(store.path), run.id, config).reserve("provider", "model", image)
    assert len(store.events(run.id)) == 2


def test_budget_concurrency(context):
    _, store, run, _ = context
    budget = Budget(store, run.id, BudgetConfig(max_image_calls=1))

    def call(_):
        try:
            budget.reserve("p", "m", True)
            return True
        except BudgetExceeded:
            return False

    with ThreadPoolExecutor(4) as pool:
        assert sum(pool.map(call, range(4))) == 1


def test_secrets_never_persist(context, monkeypatch):
    _, store, _, _ = context
    monkeypatch.setenv("META_API_KEY", "FAKE-CREDENTIAL-DO-NOT-LOG")
    run = store.create_run(
        {"api_key": "another secret", "description": "FAKE-CREDENTIAL-DO-NOT-LOG"}
    )
    store.advance(run.id, 0, "Bearer FAKE-CREDENTIAL-DO-NOT-LOG")
    with store.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "FAKE-CREDENTIAL" not in dump
    assert "another secret" not in dump


def test_api_sse_artifacts(context):
    _, store, run, _ = context
    while demo_step(store, run.id):
        pass
    with TestClient(create_app(store)) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/runs").json()["runs"][0]["status"] == "completed"
        detail = client.get(f"/api/runs/{run.id}").json()
        image = client.get("/api/artifacts/" + detail["artifacts"][0]["id"])
        assert image.headers["content-type"] == "image/png"
        assert image.content.startswith(b"\x89PNG")
        events = store.events(run.id)
        stream = client.get(
            f"/api/runs/{run.id}/events", headers={"Last-Event-ID": str(events[-2].id)}
        )
        assert stream.text.count("\nid:") == 0
        assert f"id: {events[-1].id}" in stream.text
        assert "event: done" in stream.text
        assert client.get("/api/runs/missing").status_code == 404
        assert client.get("/api/artifacts/missing").status_code == 404


def test_cli_runs(context):
    _, store, run, _ = context
    result = CliRunner().invoke(app, ["runs", "--workspace", str(store.path.parent)])
    assert result.exit_code == 0
    assert run.id in result.output
    missing = CliRunner().invoke(app, ["resume", "missing", "--workspace", str(store.path.parent)])
    assert missing.exit_code != 0
