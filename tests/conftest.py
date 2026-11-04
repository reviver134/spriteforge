import pytest
from fastapi.testclient import TestClient

from spriteforge.app.server import create_app
from spriteforge.config import init_workspace, load_config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import ScriptedHuman
from spriteforge.core.state import Store


@pytest.fixture(autouse=True)
def empty_asset_library(tmp_path_factory, monkeypatch):
    """Tests see an empty asset library unless they build one, so G2 keeps its Muse path."""
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(tmp_path_factory.mktemp("no-library")))


@pytest.fixture
def scripted_human():
    return ScriptedHuman


@pytest.fixture
def context(tmp_path):
    config = load_config()
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "mock-demo-v1", "budget": config.budget.model_dump()})
    return config, store, run, Budget(store, run.id, config.budget)


@pytest.fixture
def studio(tmp_path, monkeypatch):
    """The studio's API in mock mode (no key): (store, TestClient)."""
    monkeypatch.delenv("META_API_KEY", raising=False)
    store = Store(init_workspace(tmp_path) / "state.sqlite3")
    with TestClient(create_app(store, interval=0.01, cfg=load_config(None))) as client:
        yield store, client
