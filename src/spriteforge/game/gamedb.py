"""The game database: one OKF corpus per game holding the canon later designs read.

Every later level, menu or cutscene design must stay true to the core story and the art the
human approved, without reading the code. The database keeps that canon as plain OKF entries
(Markdown with YAML frontmatter, in a git repo next to the run, like the memory corpus):

    story/overview, story/arc, story/canon     the story bible (StoryBible)
    characters/<entity>  (+ .png, -sheet.png)  a story character with its locked art
    world/<location>                           the story's places
    gameplay/rules                             controls, rules, progression, win/lose, HUD
    art/style, art/assets                      the art direction and every locked asset
    levels/level-1                             the current game as level 1

Entries are written by code from the artifacts (no model calls) and rewritten only when they
change. They start generated (unverified); `promote_all` marks everything as human-reviewed
when the human approves a gate, and any later change makes an entry unverified again.

`context_pack(db, focus)` is what a designer reads: the story overview, canon and arc, the
player, the gameplay rules and the art style, then the entries the focus names, with image
paths, within a token budget.
"""

import hashlib
from dataclasses import dataclass
from pathlib import Path

from spriteforge.core.state import Store
from spriteforge.library.looks import measure
from spriteforge.memory.okf.models import Entry, Metadata, Source
from spriteforge.memory.okf.search import rank
from spriteforge.memory.okf.store import KnowledgeStore

from .art import ArtResult
from .art_review import sheet_image, thumb
from .design import GameDesign
from .story import StoryBible

CATEGORIES = {"story", "characters", "world", "gameplay", "art", "levels"}
ACTOR = "spriteforge/game-db"
REVIEWER = "human:reviewer"


def open_db(root: Path, events: Store, run_id: str) -> KnowledgeStore:
    return KnowledgeStore(Path(root), events, run_id, categories=CATEGORIES)


def make_entry(ident: str, type_: str, title: str, description: str, tags, body: str,
               sources: list[str], resource: str | None = None) -> Entry:  # fmt: skip
    fields = {
        "type": type_,
        "title": title,
        "description": description.replace("\n", " ").strip(),
        "tags": sorted({str(t).lower() for t in tags if str(t).strip()}),
        "sources": [Source(resource="spriteforge game", id=s) for s in sources],
    }
    if resource:
        fields["resource"] = resource
    return Entry(id=ident, metadata=Metadata(**fields), body=body.strip() + "\n")


def bullets(items, empty: str = "- (none)") -> str:
    return "\n".join(f"- {i}" for i in items) or empty


# --- entries ---------------------------------------------------------------------------------


def story_entries(story: StoryBible, source: str) -> list[Entry]:
    cast = "\n".join(
        f"- [{c.name}](../characters/{c.entity}.md) ({c.role}): {c.look}" for c in story.characters
    )
    places = "\n".join(f"- [{loc.name}](../world/{loc.id}.md): {loc.description}"
                       for loc in story.locations)  # fmt: skip
    overview = f"""
# Logline
{story.logline}

# Premise
{story.premise}

# Setting
{story.setting}

# Tone and themes
{story.tone}. Themes: {", ".join(story.themes) or "-"}.

# Characters
{cast}

# Locations
{places or "- (none)"}
"""
    arc = "\n".join(
        f"{n}. {beat}" + (" (level 1: the current game)" if n == 1 else " (planned)")
        for n, beat in enumerate(story.arc, 1)
    )
    entries = [
        make_entry("story/overview", "Story", f"{story.title}: the story", story.logline,
                   ["story", *story.themes], overview, [source]),
        make_entry("story/arc", "Story Arc", f"{story.title}: the arc",
                   f"{len(story.arc)} beats; beat 1 is the current game", ["story", "arc", "levels"],
                   f"# Beats\n{arc}", [source]),
        make_entry("story/canon", "Canon", f"{story.title}: canon",
                   "Facts no later level, menu or cutscene may break", ["story", "canon"],
                   f"# Never break\n{bullets(story.canon)}", [source]),
    ]  # fmt: skip
    for loc in story.locations:
        body = f"# {loc.name}\n{loc.description}\n\n# Mood\n{loc.mood}"
        entries.append(make_entry(f"world/{loc.id}", "Location", loc.name, loc.description,
                                  ["location", loc.id], body, [source]))  # fmt: skip
    return entries


@dataclass
class CharacterArt:
    source: str  # where the art came from ("muse", "library ...", "muse (locked, from v1)")
    version: int  # the build version that made it
    portrait: bytes  # its first frame at 4x (PNG)
    sheet: bytes  # the sprite sheet the game loads (PNG)
    look: str  # measured colours, brightness, detail

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.sheet).hexdigest()[:12]


def character_art(art: ArtResult, version: int) -> dict[str, CharacterArt]:
    """The locked art of every entity in a build, by entity id."""
    out = {}
    for eid, f in art.filled.items():
        made = f.record.get("version") or f.record.get("reused_from") or version
        out[eid] = CharacterArt(f.source, int(made), thumb(f), f.png, measure(sheet_image(f)).text)
    return out


def character_entries(
    story: StoryBible,
    design: GameDesign,
    art: dict[str, CharacterArt],
    sources: list[str],
) -> tuple[list[Entry], dict[str, bytes]]:
    entities = {e.id: e for e in design.entities}
    entries, files = [], {}
    for c in story.characters:
        e = entities.get(c.entity)
        if e is None:
            continue
        a = art.get(c.entity)
        if a is not None:
            files[f"characters/{c.entity}.png"] = a.portrait
            files[f"characters/{c.entity}-sheet.png"] = a.sheet
            art_text = (
                f"![{c.name}]({c.entity}.png)\n\nLocked art: {a.source}, made in build v{a.version} "
                f"(image {a.digest}); the game loads [{c.entity}-sheet.png]({c.entity}-sheet.png). "
                f"Looks: {a.look}. Redraw it only when a change asks for this character."
            )
        else:
            art_text = "No art yet: it is made when the game is built."
        body = f"""
# Who
{c.name}, {c.role}. {c.look}.

# Personality
{c.personality}

# Goal
{c.goal}

# Backstory
{c.backstory}

# Relationships
{c.relationships or "-"}

# In play
Design entity `{e.id}` ({e.role}, {e.width}x{e.height} px on a 320x180 screen). {e.behaviour}.
Abilities: {c.abilities}. Animations: {", ".join(e.animations) or "-"}.

# Art
{art_text}
"""
        entries.append(make_entry(
            f"characters/{c.entity}", "Character", c.name, f"{c.role}: {c.look}",
            ["character", c.role, e.role, e.id], body, sources,
            resource=f"characters/{c.entity}.png" if a else None,
        ))  # fmt: skip
    return entries, files


def gameplay_entry(design: GameDesign, source: str) -> Entry:
    checks = "\n".join(
        f"- `{c.event}` at least {c.min_count}"
        + (f" by {c.by_second:g} s" if c.by_second else "") + f": {c.description}"
        for c in design.checks
    )  # fmt: skip
    body = f"""
# Camera and controls
{design.camera}. {design.controls}

# Player
{design.player}

# Rules
{bullets(design.rules)}

# Progression
{bullets(design.progression)}

# Difficulty
{bullets(design.difficulty)}

# Win and lose
Win: {design.win}
Lose: {design.lose}
A run lasts {design.session_seconds} s.

# HUD
{bullets(design.hud)}

# Events every build emits
{checks}
"""
    return make_entry("gameplay/rules", "Gameplay", f"{design.title}: gameplay",
                      f"{design.camera}; {design.controls}", ["gameplay", design.camera], body,
                      [source])  # fmt: skip


def art_entries(design: GameDesign, art: ArtResult, chars: dict[str, CharacterArt],
                source: str) -> list[Entry]:  # fmt: skip
    roles = {e.id: e.role for e in design.entities}
    rows = "\n".join(
        f"| `{eid}` | {roles.get(eid, '-')} | {a.source} | v{a.version} | {a.look} |"
        for eid, a in chars.items()
    )
    hero = next((e for e in design.entities if e.role == "player"), None)
    style = f"""
# Direction
{design.look}

# Screen and scale
{design.camera} view, a {art.screen[0]}x{art.screen[1]} screen (art scale {art.scale}); the hero
is {hero.height * art.scale if hero else "?"} px tall. Every sprite faces right; the game flips it.

# Rules for new art
Match the locked art below: its outline, shading and pixel size. New characters and places
follow this direction and the story's tone; colours named by the design win over a style
example's.
"""
    assets = f"""
# Locked assets
| entity | role | source | made in | looks |
| --- | --- | --- | --- | --- |
{rows}

# Credits
{bullets(art.credits)}
"""
    return [
        make_entry("art/style", "Art Style", f"{design.title}: art style", design.look,
                   ["art", "style", design.camera], style, [source]),
        make_entry("art/assets", "Asset Registry", f"{design.title}: locked art",
                   f"{len(chars)} locked assets", ["art", "assets"], assets, [source]),
    ]  # fmt: skip


def level_entry(design: GameDesign, story: StoryBible, version: int, report: dict,
                source: str) -> Entry:  # fmt: skip
    by_role = lambda role: ", ".join(f"`{e.id}`" for e in design.entities if e.role == role) or "-"
    qa = report.get("qa") or {}
    qa_text = f"; QA {qa['passed']}/{qa['passed'] + qa['failed']} tests pass" if qa else ""
    places = ", ".join(loc.name for loc in story.locations[:1]) or design.world
    body = f"""
# Level 1: {design.title}
Story beat: {story.arc[0] if story.arc else "-"}

# Objective
Win: {design.win}
Lose: {design.lose}
A run lasts {design.session_seconds} s.

# Where
{places}. {design.world}

# Cast
Player: {by_role("player")}. Enemies: {by_role("enemy")}. Pickups: {by_role("pickup")}.
Props: {by_role("prop")}. Tiles: {by_role("tile")}.

# Build
Version {version}: {"all design checks pass" if report.get("passed") else "some design checks fail"}{qa_text}.
"""
    return make_entry("levels/level-1", "Level", f"Level 1: {design.title}",
                      story.arc[0] if story.arc else design.pitch, ["level", "level-1"], body,
                      [source])  # fmt: skip


# --- writing -----------------------------------------------------------------------------------


def differs(old: Entry, new: Entry) -> bool:
    a, b = old.metadata, new.metadata
    return (
        old.body != new.body
        or (a.title, a.description, a.tags, a.resource)
        != (b.title, b.description, b.tags, b.resource)
        or a.status != "stable"
    )


def sync(db: KnowledgeStore, entries: list[Entry], files: dict[str, bytes], message: str,
         scope: tuple[str, ...] = ()) -> dict:  # fmt: skip
    """Write the entries and files that changed, and deprecate entries under the `scope`
    prefixes that are no longer wanted (a renamed character); one commit; counts."""
    current = {e.id: e for e in db.scan()[0]}
    wanted = {e.id for e in entries}
    changed = [e for e in entries if e.id not in current or differs(current[e.id], e)]
    changed += [
        Entry(id=i, metadata=e.metadata.model_copy(update={"status": "deprecated"}), body=e.body)
        for i, e in current.items()
        if i not in wanted and i.startswith(scope) and scope and e.metadata.status != "deprecated"
    ]
    new_files = {
        name: data
        for name, data in files.items()
        if not (db.root / name).is_file() or (db.root / name).read_bytes() != data
    }
    if changed or new_files:
        db.write_batch(changed, message, actor=ACTOR, files=new_files)
    return {"entries": len(entries), "written": len(changed), "files": len(new_files)}


def promote_all(db: KnowledgeStore, why: str, human: str = REVIEWER) -> int:
    """Every entry not yet human-reviewed becomes human-reviewed (the human approved what the
    gate showed). One commit; returns how many."""
    entries = [e for e in db.scan()[0] if e.metadata.trust_tier != "human-reviewed"]
    if entries:
        db.write_batch(entries, why, actor=ACTOR, verified_by=human, preserve_generation=True)
    return len(entries)


def sync_story(db: KnowledgeStore, story: StoryBible, design: GameDesign, story_source: str,
               design_source: str, art: dict[str, CharacterArt] | None = None) -> dict:  # fmt: skip
    """The story, characters (with their art when known), world and gameplay entries."""
    chars, files = character_entries(story, design, art or {}, [story_source, design_source])
    entries = story_entries(story, story_source) + chars + [gameplay_entry(design, design_source)]
    return sync(db, entries, files, f"Game database: story and gameplay from {story_source}",
                ("story/", "characters/", "world/", "gameplay/"))  # fmt: skip


def sync_build(db: KnowledgeStore, story: StoryBible, design: GameDesign, art: ArtResult,
               version: int, report: dict, sources: dict[str, str]) -> dict:  # fmt: skip
    """After a build: characters with their locked art, the art entries and level 1.
    `sources`: {"story", "design", "build"} artifact names."""
    chars = character_art(art, version)
    entries, files = character_entries(story, design, chars, [sources["story"], sources["build"]])
    entries += story_entries(story, sources["story"]) + [gameplay_entry(design, sources["design"])]
    entries += art_entries(design, art, chars, sources["build"])
    entries.append(level_entry(design, story, version, report, sources["build"]))
    return sync(db, entries, files, f"Game database: build v{version}",
                ("story/", "characters/", "world/", "gameplay/", "art/"))  # fmt: skip


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


ALWAYS = [("story/overview", 320), ("story/canon", 200), ("story/arc", 160),
          ("PLAYER", 260), ("gameplay/rules", 320), ("art/style", 180)]  # fmt: skip


def context_pack(db: KnowledgeStore, focus: str = "", budget: int = 2000) -> str:
    """The canon a designer needs, in about `budget` tokens: the story overview, canon and arc,
    the player character, the gameplay rules and the art style, then the entries `focus`
    names (characters, places, levels) by relevance, each with its image path."""
    entries = {e.id: e for e in db.scan()[0] if e.metadata.status != "deprecated"}
    player = next((e.id for e in entries.values()
                   if e.metadata.type == "Character" and "player" in e.metadata.tags), None)  # fmt: skip
    title = entries["story/overview"].title if "story/overview" in entries else "this game"
    head = (
        f"# Game canon: {title}\nFrom the game database ({len(entries)} entries, {db.root}). "
        "Stay consistent with it; full entries are in that folder."
    )
    parts = [head]
    used = approx_tokens(parts[0])

    def add(entry: Entry, tokens: int) -> None:
        nonlocal used
        image = f"\nImage: {db.root / entry.metadata.resource}" if entry.metadata.resource else ""
        body = "\n".join(line for line in entry.body.strip().splitlines() if line.strip())
        text = f"\n## {entry.title} (`{entry.id}`){image}\n" + excerpt(body, tokens)
        parts.append(text)
        used += approx_tokens(text)

    shown = set()
    for ident, tokens in ALWAYS:
        ident = player if ident == "PLAYER" else ident
        if ident in entries and used + 40 < budget:
            add(entries[ident], min(tokens, budget - used))
            shown.add(ident)
    if focus.strip():
        for entry, _ in rank(focus, [e for i, e in entries.items() if i not in shown]):
            if budget - used < 80:
                break
            add(entry, min(260, budget - used - 20))
    return "\n".join(parts)
