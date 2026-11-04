"""Interrupt and rewind (docs/ui-plan.md, phase 3b).

When the human changes the work of an agent that is running or already finished while a round
of work is in progress (the first build, a G3 or G4/G5 change round), **Go back** stops the
round instead of spending tokens on work the change would throw away:

1. `request_stop` records a stop request. `Budget.reserve`, the one place every model call
   passes through, refuses to start any new call while it is set and raises `Cancelled`; calls
   already in flight finish and are kept (Muse images land in the image cache, so asking for the
   same picture again costs nothing).
2. The worker catches `Cancelled` and calls `rewind`: the stopped round is recorded as
   superseded (its artifacts stay, never deleted), the chat changes it had taken are queued
   again, and the round starts over under a new round key (`gate-5.1`) with every queued change.
   Its build gets a new number: the stopped one's is never reused, so nothing locked is touched.
3. The revision flow redoes only what the changes are about (unchanged art stays locked and is
   reused from the shown build, code is edited, not rewritten) and goes on to the next gate;
   earlier gates are not reopened.

A round of the design gate (G2 changes) is one or two Spark calls: it is not stopped, and a
change asked meanwhile waits for the G2 card that follows.
"""

import json

from spriteforge.core.gates import Gate, Gates
from spriteforge.core.state import Store

# Answered gates whose round of work can be stopped and redone.
REWINDABLE = {("design", "approved"), ("menus", "changes_requested"),
              ("playtest", "changes_requested"), ("final", "changes_requested")}  # fmt: skip


def current_round(store: Store, run_id: str) -> tuple[Gate, int] | None:
    """The answered gate whose work is in progress and its number, when that work can be
    stopped; None at an open gate or in a round that cannot be."""
    gates = Gates(store)
    history = gates.all(run_id)
    if not history or gates.pending(run_id):
        return None
    last = history[-1]
    if last.stage == "confirm":  # the plan answered: the work is its G4/G5 gate's round
        i = next(i for i, g in enumerate(history) if g.id == last.parent_id)
        return history[i], i + 1
    return (last, len(history)) if (last.stage, last.status) in REWINDABLE else None


def rewinds(store: Store, run_id: str, gate_id: str) -> int:
    # A correction of the Director's plan (game_confirm.py) starts the round again as well.
    return sum(1 for e in store.events(run_id)
               if e.type in ("round.rewound", "round.replanned")
               and e.payload.get("gate") == gate_id)  # fmt: skip


def round_key(store: Store, run_id: str, gate: Gate, n: int) -> str:
    """The key of gate n's round of work: "5", or "5.1" after one Go back. Every checkpoint of
    the round (its revision, its chat changes, its build number) is named with it."""
    k = rewinds(store, run_id, gate.id)
    return f"{n}.{k}" if k else str(n)


def request_stop(store: Store, run_id: str, ids: list[int]) -> None:
    store.append_event(run_id, "Human", "run.stop_requested", {"changes": ids})


def clear_stop(store: Store, run_id: str, why: str) -> None:
    store.append_event(run_id, "Director", "run.stop_cleared", {"why": why})


def rewind(store: Store, run_id: str) -> str | None:
    """After `Cancelled`: supersede the stopped round and start it again with every queued
    change. Returns the new round key (None when no round could be redone: the step simply
    starts again from its checkpoints and the changes stay queued)."""
    found = current_round(store, run_id)
    if found is None:
        clear_stop(store, run_id, "nothing to go back to")
        return None
    gate, n = found
    old = round_key(store, run_id, gate, n)
    with store.connect() as db:
        row = db.execute("SELECT data FROM artifacts WHERE run_id=? AND name=?",
                         (run_id, f"game/feedback/gate-{old}-build.json")).fetchone()  # fmt: skip
    build = json.loads(row[0])["version"] if row else None
    # The chat changes the stopped round took: at its start (the first build's round takes them
    # as "build-1") and late, just before its Programmer ran.
    steps = {f"gate-{old}", f"build-{old}", f"code-v{build}"}
    if gate.stage == "design" and "." not in old:
        steps.add("build-1")
    taken = [i for e in store.events(run_id) if e.type == "chat.applied"
             and e.payload.get("where") in steps for i in e.payload.get("ids", [])]  # fmt: skip
    if taken:
        store.append_event(run_id, "Director", "chat.unapplied", {"ids": taken, "where": old})
    store.append_event(run_id, "Director", "round.superseded",
                       {"gate": gate.id, "round": old, "build": build})  # fmt: skip
    new = f"{n}.{rewinds(store, run_id, gate.id) + 1}"
    store.append_event(run_id, "Director", "round.rewound", {
        "gate": gate.id, "from": old, "round": new, "build": build,
        "message": (f"Went back: build v{build} was stopped" if build else "Went back")
                   + " to apply your changes"})  # fmt: skip
    clear_stop(store, run_id, "rewound")
    return new


# Taken just before the agent runs in a build (build_version), so a change for it asked while
# earlier agents of the round work needs no stop.
LATE = {"code_instructions"}


def round_info(store: Store, run_id: str) -> dict | None:
    """The round of work in progress, for the Go back card: its gate, key, first event, the
    build it makes and what it has cost so far. None when no round can be stopped."""
    found = current_round(store, run_id)
    if found is None:
        return None
    gate, n = found
    key = round_key(store, run_id, gate, n)
    start, cost = 0, 0.0
    for e in store.events(run_id):
        if e.payload.get("id" if e.type == "gate.responded" else "gate") == gate.id and e.type in (
            "gate.responded", "round.rewound"
        ):
            start, cost = e.id, 0.0
        elif e.type == "cost.succeeded" and e.id > start:
            cost += e.payload["usage"].get("estimated_cost") or 0.0
    with store.connect() as db:
        row = db.execute("SELECT data FROM artifacts WHERE run_id=? AND name=?",
                         (run_id, f"game/feedback/gate-{key}-build.json")).fetchone()  # fmt: skip
    build = json.loads(row[0])["version"] if row else None
    return {"gate": gate.id, "round": key, "start": start, "build": build, "cost": round(cost, 4)}
