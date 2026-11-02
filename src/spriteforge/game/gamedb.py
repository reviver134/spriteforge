"""The game's record: everything about one game as an OKF v0.2 bundle that agents read.

Every later agent (level and menu designers, the Director) reads this bundle, not the code:

    game                         title, pitch, camera, current version, play link, sections
    design/rules                 controls, rules, progression, difficulty, win/lose, HUD
    design/events                # Schema: every event a build emits and every sf.state key
    entities/<id>                role, size, behaviour, animations, its asset (+ its character)
    art/style, art/assets/<id>   the art direction; each locked asset (4x portrait, sheet)
    code/overview                files, scenes, the Programmer's notes
    code/modules/<file>          one code file: scenes, functions, events, state, keys, assets
    qa/suite, qa/tests/<slug>    the QA tests: # Schema of a step; each test and its last result
    builds/v<N>                  verdict, attempts, QA, art review, cost, what changed and why
    decisions/<n>                the human's words at a gate and what they changed
    research/overview            similar games studied with web search: patterns, feel, pitfalls
    research/references/<slug>   one reference game, its page (an OKF source) and the lesson
    plan/overview                the human's requirements word for word and what covers each
    plan/movement                # Schema: movement numbers and the kinematics code derived
    plan/mechanics, plan/feel    abilities, mechanics, challenges; feedback and animation rows
    plan/levels/<n>              per level: teaching goal, new idea, challenges, difficulty
    story/*, characters/*, world/*   only when the human asked for a story

Files sit under `references/` (`art/<id>.png`, `code/<file>.js`, `tests/test_game_qa.py`);
the concept that describes a file names it in `resource`. Links are absolute from the bundle
root (`/entities/hero.md`). Concepts are written by code from a `GameRecord` (no model call)
and rewritten only when they change; they start generated (unverified), `promote_all` makes
them human-reviewed when the human approves a gate, a later change makes a concept unverified
again, and a concept that is no longer wanted (a dropped QA test) is deprecated, never deleted.

`context_pack(db, focus)` is what a designer reads: the game, the design rules, the story when
there is one, the player and the art style, then the concepts the focus names, in a budget.
"""

import re
from pathlib import Path

from spriteforge.core.state import Store
from spriteforge.memory.okf.models import Entry
from spriteforge.memory.okf.search import rank
from spriteforge.memory.okf.store import KnowledgeStore

from .bundle import (  # noqa: F401 -- the record types are part of this module's API
    ArtInfo,
    Build,
    Decision,
    GameRecord,
    art_info,
    bundle,
    portrait_of,
)

CATEGORIES = {"design", "entities", "art", "code", "qa", "builds", "decisions"}
STORY_CATEGORIES = {"story", "characters", "world"}
RESOURCES = {".png", ".gif", ".js", ".py", ".json"}  # .gif: the motion preview (L2.4)
ACTOR = "spriteforge/game-db"
REVIEWER = "human:reviewer"


def find_db(path: Path) -> Path:
    """The bundle of a `game make` folder (db/), a run folder (game/db/) or the bundle itself."""
    path = Path(path)
    for p in (path / "db", path / "game" / "db", path):
        if (p / "index.md").is_file():
            return p.resolve()
    raise FileNotFoundError(f"no game record (OKF bundle) under {path}")


def open_db(root: Path, events: Store, run_id: str) -> KnowledgeStore:
    # Story folders appear only with a story (no empty story/ index in a game without one).
    return KnowledgeStore(Path(root), events, run_id, categories=CATEGORIES, bundle=True,
                          resources=RESOURCES, title="Game record")  # fmt: skip


# --- writing -----------------------------------------------------------------------------------


def differs(old: Entry, new: Entry) -> bool:
    a, b = old.metadata, new.metadata
    return (
        old.body != new.body
        or (a.title, a.description, a.tags, a.resource, a.status)
        != (b.title, b.description, b.tags, b.resource, b.status)
    )


def sync(db: KnowledgeStore, entries: list[Entry], files: dict[str, bytes], message: str) -> dict:
    """Write the concepts and files that changed and deprecate every concept no longer wanted
    (never delete one); one commit; counts."""
    current = {e.id: e for e in db.scan()[0]}
    wanted = {e.id for e in entries}
    changed = [e for e in entries if e.id not in current or differs(current[e.id], e)]
    changed += [
        Entry(id=i, metadata=e.metadata.model_copy(update={"status": "deprecated"}), body=e.body)
        for i, e in current.items()
        if i not in wanted and e.metadata.status != "deprecated"
    ]
    new_files = {
        name: data
        for name, data in files.items()
        if not (db.root / name).is_file() or (db.root / name).read_bytes() != data
    }
    if changed or new_files:
        db.write_batch(changed, message, actor=ACTOR, files=new_files)
    return {"entries": len(entries), "written": len(changed), "files": len(new_files)}


def sync_record(db: KnowledgeStore, rec: GameRecord, message: str) -> dict:
    entries, files = bundle(rec)
    return sync(db, entries, files, message)


def promote_all(db: KnowledgeStore, why: str, human: str = REVIEWER,
                only: tuple[str, ...] = ()) -> int:  # fmt: skip
    """Every concept not yet human-reviewed becomes human-reviewed (the human approved what
    the gate showed); with `only`, just the concepts whose id starts with one of them (G3
    shows the menus, not the build). One commit; returns how many."""
    entries = [e for e in db.scan()[0]
               if e.metadata.trust_tier != "human-reviewed" and e.metadata.status != "deprecated"
               and (not only or e.id.startswith(only))]  # fmt: skip
    if entries:
        db.write_batch(entries, why, actor=ACTOR, verified_by=human, preserve_generation=True)
    return len(entries)


# --- reading ------------------------------------------------------------------------------------


def approx_tokens(text: str) -> int:
    """About 4 characters per token (the estimate the rest of the harness uses)."""
    return len(text) // 4 + 1


def excerpt(text: str, tokens: int) -> str:
    """Whole lines from the start, within `tokens`; "..." when something was left out."""
    if approx_tokens(text) <= tokens:
        return text
    kept, used = [], 0
    for line in text.splitlines():
        if used + approx_tokens(line) > tokens - 1:
            break
        kept.append(line)
        used += approx_tokens(line)
    return "\n".join([*kept, "..."])


ALWAYS = [("game", 160), ("design/rules", 320), ("story/overview", 300), ("story/canon", 200),
          ("story/arc", 160), ("PLAYER", 200), ("plan/movement", 200), ("art/style", 180)]  # fmt: skip
# Concepts about the build history, not the game's canon: found only when the focus asks.
HISTORY = ("builds/", "decisions/", "qa/tests/")


def context_pack(db: KnowledgeStore, focus: str = "", budget: int = 2000) -> str:
    """The canon a designer needs, in about `budget` tokens: the game, its rules, the story
    (when there is one), the player and the art style, then the concepts `focus` names by
    relevance. Concept bodies only, never code."""
    entries = {e.id: e for e in db.scan()[0] if e.metadata.status != "deprecated"}
    player = next((e.id for e in entries.values()
                   if e.metadata.type == "Entity" and "player" in e.metadata.tags), None)  # fmt: skip
    title = entries["game"].title if "game" in entries else "this game"
    parts = [
        (f"# Game record: {title}\nFrom its OKF bundle ({len(entries)} concepts). Stay consistent "
        "with it; read more with game_show(id).")
    ]
    used = approx_tokens(parts[0])

    def add(entry: Entry, tokens: int) -> None:
        nonlocal used
        image = ""
        if entry.metadata.resource:
            image = f"\nResource: {entry.metadata.resource}"
        elif entry.metadata.type == "Entity" and f"art/assets/{entry.id.split('/')[-1]}" in entries:
            image = f"\nImage: /references/art/{entry.id.split('/')[-1]}.png"
        body = "\n".join(line for line in entry.body.strip().splitlines() if line.strip())
        text = f"\n## {entry.title} (`{entry.id}`){image}\n" + excerpt(body, tokens)
        parts.append(text)
        used += approx_tokens(text)

    shown = set()
    for ident, tokens in ALWAYS:
        ident = player if ident == "PLAYER" else ident
        if ident in entries and used + 40 < budget:
            add(entries[ident], min(tokens, budget - used - 20))
            shown.add(ident)
    if focus.strip():
        pool = [e for i, e in entries.items() if i not in shown
                and (not i.startswith(HISTORY) or re.search(r"build|decision|test|qa", focus, re.IGNORECASE))]  # fmt: skip
        for entry, _ in rank(focus, pool):
            if budget - used < 80:
                break
            add(entry, min(260, budget - used - 20))
    return "\n".join(parts)
