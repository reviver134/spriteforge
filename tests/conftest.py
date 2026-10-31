import pytest

from spriteforge.config import load_config
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
