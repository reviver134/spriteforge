"""Which agent is making the model call now: the cost ledger records it with each call, so the
studio can show what every agent cost and which one is working."""

from contextlib import contextmanager
from contextvars import ContextVar

acting_agent: ContextVar[str | None] = ContextVar("acting_agent", default=None)


@contextmanager
def acting(agent: str):
    token = acting_agent.set(agent)
    try:
        yield
    finally:
        acting_agent.reset(token)
