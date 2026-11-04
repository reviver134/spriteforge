"""The studio's API: projects (runs started from a first message), the agents of a run, each
agent's thread and chat messages. Gates, artifacts, events and play stay in server.py."""

import asyncio
import json
import os
import re
from functools import cache

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from spriteforge.agents.workflow import WORKFLOW as AGENT_WORKFLOW
from spriteforge.config import Config
from spriteforge.core.cost import BudgetExceeded
from spriteforge.core.gates import Gates
from spriteforge.core.security import redact
from spriteforge.core.state import Store
from spriteforge.core.usage import usage_report
from spriteforge.stages.chat_changes import file_change, for_gate
from spriteforge.stages.rewind import LATE, request_stop, round_info
from spriteforge.stages.workflow import WORKFLOW as INTAKE

from .agents_view import AGENTS, actor, summarize, thread
from .chat import Intent, answer_question, classify
from .workers import FINAL, Workers

# Runs with human gates are projects; the rest are tools (memory audits, evals, CLI builds).
PROJECT_WORKFLOWS = {INTAKE, AGENT_WORKFLOW}
# Project card picture, best first: the build's poster, its title screen, the art sheet.
THUMBNAILS = [r"game/v(\d+)/poster\.png", r"game/v(\d+)/menus/title\.png", r"game/v(\d+)/art\.png",
              r"concept/v(\d+)\.png", r"preview/v(\d+)/gameplay-4x\.png"]  # fmt: skip


class NewProject(BaseModel):
    text: str = Field(min_length=1, max_length=10000)
    mock: bool = False


class Stop(BaseModel):
    change: int  # the chat change (its chat.user event id) the Go back card is about
    later: bool = False  # Queue for later: apply it at the next gate instead


class ChatMessage(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    attachments: list[str] = Field(default_factory=list)  # reference ids (phase 4)
    intent: Intent | None = None  # None: classified; the user can override a wrong guess


@cache
def chromium_installed() -> bool:
    """Playwright's Chromium plays and checks every build; checked once per process."""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            return os.path.exists(p.chromium.executable_path)
    except Exception:  # noqa: BLE001 -- any failure here means "not usable"
        return False


def project(store: Store, run, busy: bool) -> dict:
    with store.connect() as db:
        created = db.execute(
            "SELECT timestamp FROM events WHERE run_id=? ORDER BY id LIMIT 1", (run.id,)
        ).fetchone()
        images = db.execute(
            "SELECT id,name FROM artifacts WHERE run_id=? AND mime LIKE 'image/%'", (run.id,)
        ).fetchall()
        designs = db.execute(
            "SELECT name,data FROM artifacts WHERE run_id=? AND name GLOB 'game/design/v*.json'",
            (run.id,),
        ).fetchall()
    thumbnail = None
    for pattern in THUMBNAILS:
        found = [(int(m[1]), r["id"]) for r in images if (m := re.fullmatch(pattern, r["name"]))]
        if found:
            thumbnail = max(found)[1]
            break
    title = None
    versions = [(int(m[1]), r["data"]) for r in designs
                if (m := re.fullmatch(r"game/design/v(\d+)\.json", r["name"]))]  # fmt: skip
    if versions:
        title = json.loads(max(versions)[1]).get("title")
    goal = " ".join(str(run.config.get("goal") or "").split())
    gate = Gates(store).pending(run.id)
    return {
        "id": run.id,
        "title": title or (goal[:80] + "…" if len(goal) > 80 else goal) or "Untitled game",
        "idea": goal,
        "status": run.status,
        "workflow": run.config.get("workflow"),
        "mock": bool(run.config.get("mock")),
        "created": created[0] if created else None,
        "cost": round(usage_report(store, run.id).known_estimated_cost, 4),
        "thumbnail": thumbnail,
        "gate": {"id": gate.id, "title": gate.title, "stage": gate.stage} if gate else None,
        "busy": busy,
    }


def agent_cap(run) -> float | None:
    """Each agent's allowance since the human's last action (None: the run's caps are per run)."""
    budget = run.config.get("budget") or {}
    return budget.get("max_agent_cost", 10) if budget.get("per_request") else None


def studio_router(store: Store, workers: Workers, cfg: Config) -> APIRouter:
    api = APIRouter(prefix="/api")

    def require_run(id):
        try:
            return store.get_run(id)
        except KeyError:
            raise HTTPException(404, "Run not found") from None

    def require_agent(name):
        if name not in AGENTS:
            raise HTTPException(404, "Unknown agent")
        return name

    @api.get("/setup")
    async def setup():
        """The first-run check: what live mode needs, in plain words."""
        return {
            "meta_api_key": bool(os.environ.get("META_API_KEY")),
            "chromium": await asyncio.to_thread(chromium_installed),
        }

    @api.get("/projects")
    def projects():
        runs = [r for r in store.runs() if r.config.get("workflow") in PROJECT_WORKFLOWS]
        return {"projects": [project(store, r, workers.busy(r.id)) for r in runs]}

    @api.post("/projects", status_code=201)
    async def create(body: NewProject):
        from spriteforge.stages.workflow import start_run

        if not body.mock and not os.environ.get("META_API_KEY"):
            raise HTTPException(
                409, "No META_API_KEY is set, so Muse cannot be called. Add your key to .env "
                "and restart, or try offline (mock)."
            )  # fmt: skip
        run = await asyncio.to_thread(start_run, store, cfg, mock=body.mock, goal=body.text)
        store.append_event(run.id, "Human", "chat.user", {"to": "Director", "text": body.text,
                                                          "intent": "idea"})  # fmt: skip
        workers.start(run.id)
        return project(store, store.get_run(run.id), workers.busy(run.id))

    @api.get("/runs/{id}/project")
    def one(id: str):
        return project(store, require_run(id), workers.busy(id))

    @api.post("/runs/{id}/resume")
    async def resume(id: str):  # async: the worker task needs the server's event loop
        """Carry on a run whose worker is not running (the studio restarted mid-step, or the
        run failed): the step functions continue from the durable checkpoint."""
        run = require_run(id)
        if run.status == "failed" and run.config.get("workflow") == INTAKE:
            store.retry_failed(id)
        elif run.status == "budget_exhausted":  # Continue: a fresh allowance (cost policy)
            store.continue_run(id)
        elif run.status in FINAL:
            raise HTTPException(409, f"This project is {run.status.replace('_', ' ')}")
        return {"started": workers.start(id)}

    @api.get("/runs/{id}/agents")
    def agents(id: str):
        run = require_run(id)
        # A finished run's worker may still be returning from its last step: not busy.
        busy = workers.busy(id) and run.status not in FINAL
        gate = Gates(store).pending(id)
        from .server import latest_play

        with store.connect() as db:
            last = db.execute("SELECT MAX(id) FROM events WHERE run_id=?", (id,)).fetchone()[0]
        return {
            "agents": [a.model_dump() for a in summarize(store, id, busy)],
            "last_event": last or 0,  # the event stream continues from here
            "play": latest_play(store, id),
            "status": run.status,
            "busy": busy,
            "gate": gate.model_dump(mode="json") if gate else None,
            "queued": for_gate(store, id),  # chat changes the open gate's Request changes applies
            "agent_cap": agent_cap(run),
            "error": workers.errors.get(id),
        }

    @api.get("/runs/{id}/agents/{agent:path}/thread")
    def agent_thread(id: str, agent: str, after: int = 0):
        require_run(id)
        return {"items": thread(store, id, require_agent(agent), after)}

    @api.post("/runs/{id}/agents/{agent:path}/messages", status_code=201)
    async def message(id: str, agent: str, body: ChatMessage):
        require_run(id)
        require_agent(agent)
        intent = body.intent or classify(body.text)
        cursor = max((e.id for e in store.events(id)), default=0)
        user = {"to": agent, "text": body.text, "intent": intent}
        if intent == "change":
            # Filed under the agent's Revision slot; the gated pipeline applies it (chat_changes).
            fields, reply = await asyncio.to_thread(file_change, store, id, agent, body.text)
            store.append_event(id, "Human", "chat.user", {**user, **fields})
            answer = {"text": reply, "intent": "change"}
            info = round_info(store, id) if fields.get("slot") and workers.busy(id) else None
            if info:
                answer = await asyncio.to_thread(go_back_offer, id, agent, fields["slot"], info)
            store.append_event(id, agent, "chat.agent", answer)
        else:
            store.append_event(id, "Human", "chat.user", user)
            try:
                reply = {"text": await asyncio.to_thread(answer_question, store, id, agent,
                                                         body.text)}  # fmt: skip
            except BudgetExceeded as exc:
                reply = {"text": f"I can't answer now: {exc}.", "error": True}
            except Exception as exc:  # noqa: BLE001 -- the reason is shown, scrubbed
                reply = {"text": "I couldn't answer: " + redact(str(exc))[:300], "error": True}
            store.append_event(id, agent, "chat.agent", {**reply, "intent": "question"})
        return {"items": thread(store, id, agent, cursor)}

    def go_back_offer(id: str, agent: str, slot: str, info: dict) -> dict:
        """A change asked while a round of work runs: the agent's reply, with the Go back card
        when the change concerns work this round already did (or is doing)."""
        events = store.events(id, info["start"])
        change = max(e.id for e in events if e.type == "chat.user")
        build = f"build v{info['build']}" if info["build"] else "this round"
        if slot in LATE and not any(actor(e) == agent for e in events):
            return {"text": f"Noted: I'll use it when I get to it in {build}. Nothing stops.",
                    "intent": "change"}  # fmt: skip
        working = next((a.name for a in summarize(store, id, True) if a.status == "working"),
                       "Director")  # fmt: skip
        return {
            "text": f"This stops the {working} ({build}, ${info['cost']:.2f} so far) and goes "
                    f"back to the {agent}, so no more is spent on work this change replaces.",
            "intent": "change",
            "rewind": {"change": change, "round": info["round"], "working": working,
                       "build": info["build"], "cost": info["cost"]},
        }  # fmt: skip

    @api.post("/runs/{id}/stop")
    def stop(id: str, body: Stop):
        """The Go back card's answer: stop the round now (the worker rewinds it with every
        queued change) or keep the change queued for the next gate."""
        require_run(id)
        if body.later:
            store.append_event(id, "Human", "rewind.declined", {"change": body.change})
            return {"stopping": False}
        info = round_info(store, id)
        if info is None or not workers.busy(id):
            raise HTTPException(409, "That work already finished: your change waits for the "
                                "review that is open now")  # fmt: skip
        request_stop(store, id, [body.change])
        return {"stopping": True, "round": info["round"]}

    return api
