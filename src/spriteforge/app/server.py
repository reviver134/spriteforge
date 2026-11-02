import asyncio
import json
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from spriteforge.agents.workflow import WORKFLOW, advance_until_gate, workflow_step
from spriteforge.core.demo import demo_step
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.core.usage import usage_report
from spriteforge.stages.workflow import WORKFLOW as INTAKE
from spriteforge.stages.workflow import advance_until_gate as advance_intake
from spriteforge.stages.workflow import workflow_step as intake_step


def latest_play(store, run_id) -> dict | None:
    """The newest game build that can be played (its report has a play link): shown even while
    the next build runs and no gate is open."""
    reports = {}
    for a in store.artifacts(run_id):
        if m := re.fullmatch(r"game/v(\d+)/report\.json", a.name):
            reports[int(m[1])] = a
    for version in sorted(reports, reverse=True):
        play = json.loads(reports[version].data).get("play")
        if play:
            return {"version": version, "url": play}
    return None


def create_app(
    store: Store, run_id: str | None = None, interval: float = 1.0, mcp_service=None
) -> FastAPI:
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
    from .extensions import router

    app.include_router(router(store, mcp_service))
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
        # Gates only reference already-persisted artifacts. Read the gate first so
        # a concurrent worker cannot return a new gate with an older artifact list.
        gate = Gates(store).pending(id)
        production = None
        preview = None
        if run.config.get("gameplay_preview"):
            from spriteforge.stages.asset_store import ProductionStore

            saved = ProductionStore(store, id)
            reports = [
                n
                for n in saved.artifacts
                if n.startswith("preview/v") and n.endswith("/readability.json")
            ]
            versions = sorted(reports, key=lambda n: int(n.split("/")[1][1:]))
            preview = {
                "report": saved.read(versions[-1]) if versions else None,
                "versions": [
                    {
                        "version": int(n.split("/")[1][1:]),
                        "artifact": saved.artifacts[
                            n.replace("readability.json", "gameplay-4x.png")
                        ].id,
                    }
                    for n in versions
                ],
                "cap": min(
                    run.config["settings"].get("preview", {}).get("max_image_calls", 16),
                    run.config["budget"]["max_image_calls"],
                ),
            }
        if run.config.get("asset_production"):
            from spriteforge.stages.asset_store import ProductionStore

            assets = ProductionStore(store, id)
            reports = sorted(n for n in assets.artifacts if n.endswith("/consistency.json"))
            production = {
                "assets": [r.model_dump() for r in assets.records().values()],
                "consistency": assets.read(max(reports, key=lambda n: int(n.split("/")[2])))
                if reports
                else None,
            }
        return {
            "run": run.model_dump(),
            "play": latest_play(store, id),
            "production": production,
            "preview": preview,
            "error": next(
                (
                    e.payload.get("message")
                    for e in reversed(store.events(id))
                    if e.type
                    in {"run.failed", "production.budget_exhausted", "preview.budget_exhausted"}
                ),
                None,
            )
            if run.status in {"failed", "budget_exhausted"}
            else None,
            "usage": usage_report(store, id).model_dump(),
            "artifacts": [a.model_dump(exclude={"data"}) for a in store.artifacts(id)],
            "gate": gate.model_dump(mode="json") if gate else None,
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
                    feedback = feedback.resolved(pending)
                    if feedback.action == "request_changes" and pending.stage not in {
                        "concept",
                        "assets",
                        "gameplay",
                        "design",
                        "playtest",
                    }:
                        raise ValueError("Changes can be requested at the concept review")
                    if feedback.action == "request_changes" and pending.stage == "assets":
                        from spriteforge.stages.production import selected_targets

                        selected_targets(store, id, feedback)
                    if feedback.action == "approve" and pending.stage == "concept":
                        from spriteforge.pixel.schemas import hex_color

                        for value in feedback.form.values():
                            hex_color(value)
                    if pending.stage == "gameplay":
                        from spriteforge.stages.asset_models import Manifest
                        from spriteforge.stages.asset_store import ProductionStore
                        from spriteforge.stages.preview_models import PreviewChange
                        from spriteforge.stages.preview_review import validate_change

                        saved = ProductionStore(store, id)
                        if feedback.action == "approve":
                            if any(
                                feedback.form.get(k, "").strip() for k in ("targets", "palette")
                            ):
                                raise ValueError(
                                    "Use Request changes to apply asset, layout or palette edits"
                                )
                            reports = [
                                n for n in saved.artifacts if n.endswith("/readability.json")
                            ]
                            report = saved.read(
                                max(reports, key=lambda n: int(n.split("/")[1][1:]))
                            )
                            if any(r["failures"] for r in report["assets"].values()):
                                raise ValueError("Fix failing asset validators before approval")
                        elif (
                            feedback.action == "request_changes"
                            and feedback.form.get("targets", "").strip()
                        ):
                            validate_change(
                                PreviewChange(
                                    targets=[
                                        s.strip()
                                        for s in feedback.form["targets"].split(",")
                                        if s.strip()
                                    ],
                                    instruction=feedback.text,
                                    palette=[
                                        s.strip()
                                        for s in feedback.form.get("palette", "").split(",")
                                        if s.strip()
                                    ],
                                ),
                                Manifest.model_validate(saved.read("preview/manifest.json")),
                            )
            gate = await asyncio.to_thread(Gates(store).respond, id, gate_id, feedback)
        except KeyError:
            raise HTTPException(404, "Gate not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        # Advance only after the persisted human answer; the next gate pauses again.
        try:
            await asyncio.to_thread(
                advance_intake if run.config.get("workflow") == INTAKE else advance_until_gate,
                store,
                id,
            )
        except Exception as exc:  # noqa: BLE001 -- persist scrubbed worker errors
            await asyncio.to_thread(store.fail, id, str(exc))
            raise HTTPException(500, redact(str(exc))) from None
        return {"gate": gate.model_dump(mode="json"), "run": store.get_run(id).model_dump()}

    @app.get("/api/runs/{id}/play/{version}/{path:path}")
    def play(id: str, version: str, path: str):
        """Serve a generated game build so the reviewer can play it, not just watch it."""
        import mimetypes
        import re

        require_run(id)
        if not re.fullmatch(r"v\d+", version):
            raise HTTPException(404, "Unknown build version")
        root = (store.path.parent / "runs" / id / "game" / version / "build").resolve()
        target = (root / (path or "index.html")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404, "Not part of this build")
        media = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return Response(target.read_bytes(), media_type=media,
                        headers={"X-Content-Type-Options": "nosniff"})  # fmt: skip

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
                completed = store.get_run(id).status in {
                    "completed",
                    "aborted",
                    "failed",
                    "budget_exhausted",
                }
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
