"""Changes asked in an agent's chat, filed under that agent's slot of the `Revision` model so the
Director never has to re-route them (docs/ui-plan.md, phase 3).

A change is a `chat.user` event with a `slot`. It waits ("queued") until the pipeline reaches a
point that uses that slot, which takes it once and records `chat.applied`:

* the agent has not run yet: its first run (the first design, the first build);
* the run waits at a gate: that gate's change round (G2 takes design and story changes, G3 the
  menus, G4 and G5 every slot);
* otherwise it stays queued for the next change round that takes its slot.

Agent changes never run freely: they go through the same gated revision flow as gate feedback,
and accepted art stays locked except for the entities a change names.
"""

import re

from spriteforge.core.gates import Gates
from spriteforge.core.state import Store
from spriteforge.game.design import GameDesign
from spriteforge.game.revision import (
    DROP_VERBS,
    LEVEL_ASK,
    STORY_ASK,
    ArtChange,
    LevelChange,
    Revision,
    dropped_tests,
)

# Each agent's slot. "feedback" is gate feedback the Director routes as it does today.
SLOTS = {
    "Director": "feedback",
    "Game Designer": "design_changes",
    "Level/Tile Designer": "level_changes",
    "Art Director": "art_changes",
    "Pixel Artist": "art_changes",
    "Art Reviewer": "art_changes",
    "Menu Designer": "menu_changes",
    "Sound Director": "sound_changes",
    "Programmer": "code_instructions",
    "QA Playtester": "add_qa_checks",
    "Librarian": "feedback",
}
DESIGN = {"feedback", "design_changes", "story_changes"}
BUILD = {"art_changes", "code_instructions", "add_qa_checks", "drop_qa_tests", "level_changes",
         "menu_changes", "sound_changes"}  # fmt: skip
EVERY = DESIGN | BUILD
# What each gate's change round takes.
GATE_SLOTS = {"design": DESIGN, "menus": {"feedback", "menu_changes"}, "playtest": EVERY,
              "final": EVERY}  # fmt: skip
# First runs: the first design (from G1) and the first build (from G2 approved), done once
# an artifact matching the pattern exists.
FIRST = {"design-1": (DESIGN, r"game/design/v1\.json"), "build-1": (BUILD, r"game/v\d+/report\.json")}

DROP_ASK = re.compile(rf"\b({'|'.join(DROP_VERBS)})\b.*\b(tests?|checks?)\b")


def slot_for(agent: str, text: str) -> str:
    slot = SLOTS[agent]
    said = text.lower()
    if slot == "design_changes" and STORY_ASK.search(said):
        return "story_changes"
    if slot == "add_qa_checks" and DROP_ASK.search(said):
        return "drop_qa_tests"
    return slot


def named_entities(design: GameDesign | None, text: str) -> list[str]:
    """Entities the text names by id ("bat", "slime enemy" for slime_enemy, plurals too)."""
    said = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    return [e.id for e in (design.entities if design else [])
            if re.search(rf" {re.escape(e.id.replace('_', ' '))}s? ", said)]  # fmt: skip


def changes(store: Store, run_id: str) -> list[dict]:
    """Every filed chat change, oldest first: {id, agent, slot, text, applied (where or None)}."""
    filed, applied = [], {}
    for e in store.events(run_id):
        if e.type == "chat.user" and e.payload.get("slot"):
            filed.append({"id": e.id, "agent": e.payload.get("to"), "slot": e.payload["slot"],
                          "text": e.payload.get("text", "")})  # fmt: skip
        elif e.type == "chat.applied":
            applied.update(dict.fromkeys(e.payload.get("ids", []), e.payload.get("where")))
        elif e.type == "chat.unapplied":  # its round was stopped (Go back): queued again
            for i in e.payload.get("ids", []):
                applied.pop(i, None)
    return [{**c, "applied": applied.get(c["id"])} for c in filed]


def pending(store: Store, run_id: str, slots=EVERY) -> list[dict]:
    return [c for c in changes(store, run_id) if not c["applied"] and c["slot"] in slots]


def for_gate(store: Store, run_id: str) -> list[dict]:
    """The queued changes the open gate's Request changes would apply."""
    gate = Gates(store).pending(run_id)
    return pending(store, run_id, GATE_SLOTS.get(gate.stage, set())) if gate else []


def landing(store: Store, run_id: str, slot: str) -> str:
    """Where a change filed now is used: "gate" (the open gate's change round), "first" (the
    agent's first run, still to come) or "later" (the next change round that takes it)."""
    gate = Gates(store).pending(run_id)
    if gate and slot in GATE_SLOTS.get(gate.stage, set()):
        return "gate"
    with store.connect() as db:  # names only: the artifacts' data is not loaded
        names = {r[0] for r in db.execute("SELECT name FROM artifacts WHERE run_id=?", (run_id,))}
    def done(pattern):
        return any(re.fullmatch(pattern, n) for n in names)

    if any(slot in slots and not done(pattern) for slots, pattern in FIRST.values()):
        return "first"
    return "later"


def take(ctx, key: str, slots, done: bool = False) -> list[dict]:
    """The queued changes in `slots`, taken once for step `key` (a re-entered step gets the same
    ones). `done`: the step already finished before; it takes nothing new."""
    name = f"game/feedback/{key}-chat.json"
    if name in ctx.artifacts:
        return ctx.read(name)["changes"]
    if done:
        return []
    taken = pending(ctx.store, ctx.run_id, slots)
    if taken:
        ctx.save(name, {"changes": taken}, "Director")
        ctx.store.append_event(ctx.run_id, "Director", "chat.applied",
                               {"ids": [c["id"] for c in taken], "where": key})  # fmt: skip
    return taken


def as_revision(taken: list[dict], design: GameDesign | None, tests=(), levels: int = 0
                ) -> tuple[Revision, str]:  # fmt: skip
    """The changes as one Revision, and the words left for the Director to route (Director and
    Librarian messages, and changes this code cannot place: an art change naming no entity, a
    level change when the game has no levels, a drop naming no test)."""
    looks = {e.id: e.look for e in (design.entities if design else [])}
    rev = {"design_changes": [], "code_instructions": [], "story_changes": [], "menu_changes": [],
           "sound_changes": [], "art_changes": [], "level_changes": [], "drop_qa_tests": [],
           "add_qa_checks": []}  # fmt: skip
    routed = []
    for c in taken:
        slot, text = c["slot"], c["text"].strip()
        if slot == "art_changes":
            ids = named_entities(design, text)
            rev[slot] += [ArtChange(entity=i, prompt=f"{looks[i]}; {text}") for i in ids]
            routed += [] if ids else [text]
        elif slot == "level_changes":
            said = sorted({int(n) for n in LEVEL_ASK.findall(text.lower())})
            numbers = [n for n in said if n <= levels] or list(range(1, levels + 1))
            rev[slot] += [LevelChange(level=n, change=text) for n in numbers]
            routed += [] if numbers else [text]
        elif slot == "drop_qa_tests":
            names = dropped_tests(text, list(tests))
            rev[slot] += names
            routed += [] if names else [text]
        elif slot == "add_qa_checks":
            rev[slot].append(text)
        elif slot in rev:
            rev[slot].append(text)
        else:
            routed.append(text)
    text_slots = {"design_changes", "code_instructions", "story_changes", "menu_changes",
                  "sound_changes"}  # fmt: skip
    revision = Revision(**{k: "\n".join(v) if k in text_slots else v for k, v in rev.items()})
    return revision, "\n".join(routed)


def words(taken: list[dict]) -> str:
    """What the human asked, for a step that takes plain feedback (G2, G3, the first design)."""
    return "\n".join(f"{c['agent']}: {c['text'].strip()}" for c in taken)


def qa_asks(ctx, version: int) -> list[str]:
    """Checks the human asked for in build v1..vN's revisions: every later build keeps them."""
    asks = []
    for k in range(1, version + 1):
        name = f"game/v{k}/revision.json"
        if name in ctx.artifacts:
            asks += Revision.model_validate(ctx.read(name)).add_qa_checks
    return list(dict.fromkeys(asks))


def latest_design(store: Store, run_id: str) -> GameDesign | None:
    with store.connect() as db:
        rows = db.execute("SELECT name,data FROM artifacts WHERE run_id=? AND name GLOB "
                          "'game/design/v*.json'", (run_id,)).fetchall()  # fmt: skip
    found = [(int(m[1]), r["data"]) for r in rows
             if (m := re.fullmatch(r"game/design/v(\d+)\.json", r["name"]))]  # fmt: skip
    return GameDesign.model_validate_json(max(found)[1]) if found else None


WHAT = {
    "feedback": "I'll route it with the review's feedback",
    "design_changes": "the design is revised for it",
    "story_changes": "the story is written or revised for it",
    "code_instructions": "the Programmer edits the game for it",
    "menu_changes": "the menus are revised for it",
    "sound_changes": "the sounds are changed for it",
    "add_qa_checks": "I'll write this check and run it on every build from then on",
    "drop_qa_tests": "the QA tests it names are dropped",
}


def file_change(store: Store, run_id: str, agent: str, text: str) -> tuple[dict, str]:
    """A change asked in `agent`'s chat: the `chat.user` payload fields (with its slot, or
    without one when it cannot be placed yet) and the agent's reply."""
    slot = slot_for(agent, text)
    design = latest_design(store, run_id)
    if slot == "art_changes" and design is not None:
        ids = named_entities(design, text)
        if not ids:
            names = ", ".join(e.id.replace("_", " ") for e in design.entities)
            return {"status": "unplaced"}, (
                "Which one should change? Name it so only that art is redrawn and the rest stays "
                f"locked. This game has: {names}."
            )  # fmt: skip
        what = "I'll redraw " + ", ".join(i.replace("_", " ") for i in ids) + \
            " (only that; every other picture stays locked)"  # fmt: skip
    elif slot == "level_changes":
        said = sorted({int(n) for n in LEVEL_ASK.findall(text.lower())})
        what = ("the Level Designer revises " + (", ".join(f"level {n}" for n in said)
                if said else "every level") + " for it")  # fmt: skip
    else:
        what = WHAT.get(slot, "it is applied")
    where = landing(store, run_id, slot)
    if where == "gate":
        gate = Gates(store).pending(run_id)
        heading = re.split(r"[:.] ", gate.title, maxsplit=1)[0]
        reply = (f"Filed with the {heading} review: {what}. Press **Request changes** on that "
                 "card to build it, or tell me more first.")  # fmt: skip
    elif where == "first":
        reply = f"Noted for my first run: {what} when I get to it. Nothing stops meanwhile."
    else:
        reply = (f"Queued: {what}. It is built in at the next review where you press "
                 "**Request changes**.")  # fmt: skip
    return {"slot": slot, "status": "queued"}, reply
