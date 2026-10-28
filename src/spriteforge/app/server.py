import asyncio
import json
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from spriteforge.agents.workflow import WORKFLOW, advance_until_gate, workflow_step
from spriteforge.core.demo import demo_step
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.stages.workflow import WORKFLOW as INTAKE
from spriteforge.stages.workflow import workflow_step as intake_step


def create_app(store: Store, run_id: str | None = None, interval: float = 1.0) -> FastAPI:
    async def worker():
        while True:
            try:
                step = (
                    intake_step
                    if store.get_run(run_id).config.get("workflow") == INTAKE
                    else (
                        workflow_step
                        if store.get_run(run_id).config.get("workflow") == WORKFLOW
                        else demo_step
                    )
                )
                more = await asyncio.to_thread(step, store, run_id)
                if not more:
                    break
            except Exception as exc:  # noqa: BLE001 -- persist any worker failure at task boundary
                # No traceback/HTTP headers: errors are scrubbed at this boundary.
                app.state.worker_error = redact(str(exc))
                await asyncio.to_thread(store.fail, run_id, str(exc))
                break
            await asyncio.sleep(interval)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(worker()) if run_id else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(lifespan=lifespan)
    app.state.worker_error = None

    def require_run(id):
        try:
            return store.get_run(id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    @app.get("/", response_class=HTMLResponse)
    def index():
        return Path(__file__).with_name("index.html").read_text(encoding="utf-8")

    @app.get("/api/runs")
    def runs():
        return {
            "runs": [r.model_dump() for r in store.runs()],
            "active": run_id,
            "error": app.state.worker_error,
        }

    @app.get("/api/runs/{id}")
    def detail(id: str):
        run = require_run(id)
        return {
            "run": run.model_dump(),
            "usage": {
                "image_calls": sum(
                    bool(e.payload.get("image"))
                    for e in store.events(id)
                    if e.type == "cost.reserved"
                ),
                "estimated_cost": sum(
                    e.payload.get("estimated_cost", 0)
                    for e in store.events(id)
                    if e.type == "cost.reserved"
                ),
                "cap": run.config.get("budget", {}).get("max_image_calls", 150),
            },
            "artifacts": [a.model_dump(exclude={"data"}) for a in store.artifacts(id)],
            "gate": (gate.model_dump(mode="json") if (gate := Gates(store).pending(id)) else None),
        }

    @app.post("/api/runs/{id}/gates/{gate_id}")
    async def respond(id: str, gate_id: str, feedback: Feedback):
        run = require_run(id)
        if run.config.get("workflow") not in {WORKFLOW, INTAKE}:
            raise HTTPException(409, "Run does not use agent gates")
        try:
            if run.config.get("workflow") == INTAKE:
                pending = Gates(store).pending(id)
                if pending and pending.id == gate_id:
                    if feedback.action == "request_changes" and pending.stage != "concept":
                        raise ValueError("Changes can be requested at the concept review")
                    if feedback.action == "approve" and pending.stage == "concept":
                        from spriteforge.pixel.schemas import hex_color

                        for value in feedback.form.values():
                            hex_color(value)
            gate = await asyncio.to_thread(Gates(store).respond, id, gate_id, feedback)
        except KeyError:
            raise HTTPException(404, "Gate not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        # Advance only after the persisted human answer; the next gate pauses again.
        try:
            await asyncio.to_thread(
                intake_step if run.config.get("workflow") == INTAKE else advance_until_gate,
                store,
                id,
            )
        except Exception as exc:  # noqa: BLE001 -- persist scrubbed worker errors
            await asyncio.to_thread(store.fail, id, str(exc))
            raise HTTPException(500, redact(str(exc))) from None
        return {"gate": gate.model_dump(mode="json"), "run": store.get_run(id).model_dump()}

    @app.get("/api/artifacts/{id}")
    def artifact(id: str):
        with store.connect() as db:
            row = db.execute("SELECT data,mime FROM artifacts WHERE id=?", (id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Artifact not found")
        return Response(row[0], media_type=row[1], headers={"X-Content-Type-Options": "nosniff"})

    @app.get("/api/runs/{id}/events")
    async def events(
        id: str, request: Request, after: int = 0, last_event_id: str | None = Header(default=None)
    ):
        require_run(id)
        try:
            cursor = max(0, after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(400, "Invalid Last-Event-ID") from None

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                completed = store.get_run(id).status in {"completed", "aborted", "failed"}
                batch = await asyncio.to_thread(store.events, id, cursor)
                for event in batch:
                    cursor = event.id
                    yield f"id: {event.id}\ndata: {event.model_dump_json()}\n\n"
                if completed:
                    yield "event: done\ndata: {}\n\n"
                    break
                if app.state.worker_error:
                    yield (
                        "event: worker-error\ndata: " + json.dumps(app.state.worker_error) + "\n\n"
                    )
                    break
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
