"""The studio's view of a run as a team of agents, derived from the append-only journal (events
and gates): who works in which order, what each produced and cost, and each agent's thread."""

import re

from pydantic import BaseModel

from spriteforge.core.gates import Gate, Gates
from spriteforge.core.state import Event, Store
from spriteforge.stages.chat_changes import changes

# Execution order, as a real run calls them.
AGENTS = [
    "Director",
    "Game Designer",
    "Level/Tile Designer",
    "Art Director",
    "Pixel Artist",
    "Menu Designer",
    "Sound Director",
    "Programmer",
    "QA Playtester",
    "Art Reviewer",
    "Librarian",
]

# The agents whose work a gate reviews (the Director's chat shows every gate as well).
GATE_OWNERS = {
    "design": ["Game Designer"],
    "concept": ["Art Director"],
    "assets": ["Art Director", "Pixel Artist"],
    "gameplay": ["Art Director", "Pixel Artist"],
    "menus": ["Menu Designer"],
    "playtest": ["Programmer", "QA Playtester"],
    "final": ["Librarian"],
}

# The Director narrates steps that other agents do ("build v1: the Programmer is writing the
# game"); the first matching pattern names the agent at work.
NARRATION = [
    (r"Art Reviewer|vision review", "Art Reviewer"),
    (r"Programmer|attempt \d+", "Programmer"),
    (r"menu", "Menu Designer"),
    (r"art \(|blocking issue", "Pixel Artist"),
    (r"playing the game|recording|QA|tests?\b", "QA Playtester"),
]

# Journal noise that is not an agent's own work (each memory read, the cost ledger).
QUIET = {"memory.retrieved", "cost.reserved", "cost.succeeded", "cost.failed"}
RUN_EVENTS = {"run.created", "run.failed", "run.completed", "run.retry_requested", "game.built"}


def owners(gate: Gate) -> list[str]:
    return ["Director", *GATE_OWNERS.get(gate.stage, [])]


def actor(event: Event) -> str | None:
    """The agent whose work this event is, or None (the human, the ledger, memory reads)."""
    if event.type == "cost.reserved":
        return event.payload.get("agent")
    if event.type in QUIET or event.agent not in AGENTS:
        return None
    if event.agent == "Director" and event.type == "game.progress":
        message = str(event.payload.get("message", ""))
        return next((a for p, a in NARRATION if re.search(p, message)), "Director")
    return event.agent


class AgentSummary(BaseModel):
    name: str
    status: str  # "waiting" | "working" | "needs_you" | "done"
    needs_you: int  # open gates this agent's chat holds
    artifacts: int
    calls: int
    images: int
    cost: float
    last: str | None  # its latest progress message


def summarize(store: Store, run_id: str, busy: bool) -> list[AgentSummary]:
    """`busy`: a worker is stepping this run now (so its latest actor is working)."""
    stats = {
        a: {"artifacts": 0, "calls": 0, "images": 0, "cost": 0.0, "last": None} for a in AGENTS
    }
    seen, attempts, current = set(), {}, None
    for event in store.events(run_id):
        who = actor(event)
        if event.type == "cost.reserved":
            # Calls booked before agents were recorded go to whoever was working then.
            attempts[event.payload["attempt_id"]] = who or current or "Director"
        elif event.type in {"cost.succeeded", "cost.failed"}:
            usage = event.payload["usage"]
            agent = attempts.get(event.payload["attempt_id"])
            if agent in stats and event.type == "cost.succeeded":
                stats[agent]["calls"] += 1
                stats[agent]["images"] += bool(usage.get("image"))
                stats[agent]["cost"] += usage.get("estimated_cost") or 0.0
        if who:
            seen.add(who)
            current = who
            if event.type == "artifact.created":
                stats[who]["artifacts"] += 1
            elif event.type == "game.progress":
                stats[who]["last"] = event.payload.get("message")
    gate = Gates(store).pending(run_id)
    waiting_on = set(owners(gate)) if gate else set()
    result = []
    for name in AGENTS:
        if name in waiting_on:
            status = "needs_you"
        elif busy and not gate and name == current:
            status = "working"
        elif name in seen:
            status = "done"
        else:
            status = "waiting"
        result.append(
            AgentSummary(
                name=name,
                status=status,
                needs_you=int(name in waiting_on),
                **{**stats[name], "cost": round(stats[name]["cost"], 6)},
            )
        )
    return result


def item(event: Event, kind: str, **fields) -> dict:
    return {"id": event.id, "at": event.timestamp, "agent": event.agent, "kind": kind, **fields}


def thread(store: Store, run_id: str, agent: str, after: int = 0) -> list[dict]:
    """The agent's chat as one timeline: its messages, progress, artifacts and the gates that
    review its work. The Director's thread is the run's timeline: every agent's progress, every
    gate and the run's milestones."""
    director = agent == "Director"
    gates = {g.id: g for g in Gates(store).all(run_id)}
    metas = artifact_metas(store, run_id)
    by_name = {m["name"]: m for m in metas.values()}

    def readable(meta):  # a JSON record is shown as the Markdown its agent wrote with it
        twin = by_name.get(meta["name"].removesuffix(".json") + ".md")
        return twin if meta["mime"] == "application/json" and twin else meta

    applied = {c["id"]: c["applied"] for c in changes(store, run_id)}
    items = []
    for event in store.events(run_id, after):
        kind, payload = event.type, event.payload
        if kind == "chat.user" and payload.get("to") == agent:
            fields = chat_fields(payload)
            if event.id in applied:  # a filed change: queued until a step takes it
                fields["status"] = "applied" if applied[event.id] else "queued"
            items.append(item(event, "chat", role="user", **fields))
        elif kind == "chat.agent" and event.agent == agent:
            items.append(item(event, "chat", role="agent", **chat_fields(payload)))
        elif kind in {"gate.opened", "gate.responded"}:
            gate = gates.get(payload.get("id"))
            if gate is None or agent not in owners(gate):
                continue
            if kind == "gate.opened":
                shown = [readable(metas[i]) for i in gate.artifacts_to_show if i in metas]
                items.append(
                    item(event, "gate", gate=gate.model_dump(mode="json"), artifacts=shown)
                )
            else:
                response = Gate.model_validate(payload).response
                items.append(
                    item(
                        event,
                        "gate_response",
                        gate_id=gate.id,
                        title=gate.title,
                        status=payload.get("status"),
                        text=response.text if response else "",
                        chat=len((response.form if response else {}).get("chat_changes") or []),
                    )
                )
        elif kind == "game.progress" and (director or actor(event) == agent):
            items.append(item(event, "progress", text=str(payload.get("message", ""))))
        elif kind == "artifact.created" and event.agent == agent:
            fields = {k: payload.get(k) for k in ("id", "name", "mime")}
            items.append(item(event, "artifact", artifact=fields))
        elif director and kind in RUN_EVENTS:
            items.append(item(event, "run", type=kind, message=payload.get("message")))
    return items


def artifact_metas(store: Store, run_id: str) -> dict[str, dict]:
    """id -> {id, name, mime} of every artifact of the run, without loading the data."""
    with store.connect() as db:
        rows = db.execute("SELECT id,name,mime FROM artifacts WHERE run_id=?", (run_id,))
        return {r["id"]: dict(r) for r in rows}


def chat_fields(payload: dict) -> dict:
    keys = ("text", "intent", "status", "error")
    return {k: payload[k] for k in keys if k in payload}
