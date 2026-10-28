import pytest

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store


@pytest.fixture
def context(tmp_path):
    config = load_config()
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "mock-demo-v1", "budget": config.budget.model_dump()})
    return config, store, run, Budget(store, run.id, config.budget)
