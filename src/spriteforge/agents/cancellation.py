"""Cooperative cancellation boundary for synchronous provider/tool adapters."""

from contextvars import ContextVar
from threading import Event

cancelled: ContextVar[Event | None] = ContextVar("agent_cancelled", default=None)


def check_active() -> None:
    signal = cancelled.get()
    if signal is not None and signal.is_set():
        raise TimeoutError("Agent invocation has ended")
