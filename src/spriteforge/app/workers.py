"""One background worker per active run: it steps the run's workflow until the next gate (or
the end), so several projects can be in progress at once in one studio process."""

import asyncio
from contextlib import suppress

from spriteforge.agents.workflow import WORKFLOW
from spriteforge.agents.workflow import workflow_step as agent_step
from spriteforge.core.demo import demo_step
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.stages.workflow import WORKFLOW as INTAKE
from spriteforge.stages.workflow import workflow_step as intake_step

FINAL = {"completed", "aborted", "failed", "budget_exhausted"}


def step_for(store: Store, run_id: str):
    workflow = store.get_run(run_id).config.get("workflow")
    return intake_step if workflow == INTAKE else agent_step if workflow == WORKFLOW else demo_step


class Workers:
    def __init__(self, store: Store, interval: float = 1.0):
        self.store, self.interval = store, interval
        self.tasks: dict[str, asyncio.Task] = {}
        self.errors: dict[str, str] = {}  # run id -> scrubbed message of the failure

    def busy(self, run_id: str) -> bool:
        task = self.tasks.get(run_id)
        return task is not None and not task.done()

    def start(self, run_id: str) -> bool:
        """Step this run in the background; False when its worker is already running.
        Needs a running event loop (call from a route or the app lifespan)."""
        if self.busy(run_id):
            return False
        self.errors.pop(run_id, None)
        self.tasks[run_id] = asyncio.create_task(self._work(run_id))
        return True

    async def _work(self, run_id: str):
        while True:
            try:
                if self.store.get_run(run_id).status in FINAL:
                    break
                more = await asyncio.to_thread(step_for(self.store, run_id), self.store, run_id)
                if not more:
                    break
            except Exception as exc:  # noqa: BLE001 -- persist any worker failure at task boundary
                # No traceback/HTTP headers: errors are scrubbed at this boundary.
                self.errors[run_id] = redact(str(exc))
                await asyncio.to_thread(self.store.fail, run_id, str(exc))
                break
            await asyncio.sleep(self.interval)

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        for task in self.tasks.values():
            with suppress(asyncio.CancelledError):
                await task
