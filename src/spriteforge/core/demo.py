"""A ten-checkpoint mock workflow; all progress and image bytes persist in SQLite."""

from uuid import uuid4

from pydantic import BaseModel

from spriteforge.config import BudgetConfig
from spriteforge.providers.base import ChatRequest, ImageRequest, Message, chat_json
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider

from .cost import Budget
from .state import Artifact, Store

STEPS = [
    "Brief received",
    "Designer proposed dungeon",
    "Art direction ready",
    "Hero image ready",
    "Hero checks passed",
    "Tile plan ready",
    "Tile image ready",
    "Contact sheet ready",
    "Mock QA complete",
    "Foundation demo complete",
]


class Plan(BaseModel):
    message: str


def demo_step(store: Store, run_id: str) -> bool:
    # Serialize workers through dispatch and commit: avoids duplicate calls on two resumes.
    # The OS lock is released automatically on process termination.
    import os

    lock_path = store.path.with_suffix(".demo.lock")
    with lock_path.open("a+b") as lock:
        if os.name == "nt":
            import msvcrt

            lock.seek(0)
            if not lock.read(1):
                lock.write(b"0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _step(store, run_id)
        finally:
            if os.name == "nt":
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def _step(store: Store, run_id: str) -> bool:
    run = store.get_run(run_id)
    if run.status == "completed":
        return False
    if run.config.get("workflow") != "mock-demo-v1":
        raise ValueError("Unknown workflow; cannot resume")
    budget = Budget(store, run_id, BudgetConfig.model_validate(run.config["budget"]))
    artifact = None
    if run.cursor == 1:
        chat_json(
            MockLLMProvider(budget),
            ChatRequest(messages=[Message(role="user", content="Plan a demo dungeon")]),
            Plan,
        )
    if run.cursor in (3, 6):
        name = "hero" if run.cursor == 3 else "tile"
        result = MockImageProvider(budget).generate(ImageRequest(prompt=name, size=(32, 32)))
        artifact = Artifact(id=uuid4().hex, run_id=run.id, name=name + ".png", data=result.data)
    store.advance(run.id, run.cursor, STEPS[run.cursor], artifact, final=run.cursor == 9)
    return True
