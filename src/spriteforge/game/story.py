"""The story bible: who the characters are and what the story is, written from the design.

The design says what the game does; the story bible says who is in it and why, so every later
level, menu and cutscene stays consistent. The Game Designer writes it right after each design
version; it is shown at G2 with the design and revised with it. Each character is one design
entity (its art and behaviour), exactly one is the player; the arc's first beat is the current
game, the rest are levels that may follow; canon lists what no later level may break.

`fit_story` repairs an answer against the design instead of rejecting it (unknown entities
dropped, a missing player added, names and ids made unique, lists bounded).
"""

import re

from pydantic import Field

from spriteforge.agents.models import Contract

from .design import GameDesign, snake

MAX_ARC, MAX_CANON, MAX_THEMES = 7, 12, 5


class Character(Contract):
    entity: str = Field(description="the design entity id this character is (its art and play)")
    name: str
    role: str = Field(description="hero, villain, boss, minion, ally, neutral ...")
    look: str = Field(description="what they look like, consistent with the entity's look")
    personality: str
    backstory: str = Field(description="their history in two sentences at most")
    goal: str = Field(description="what they want")
    abilities: str = Field(description="what they can do in play, as the design says")
    relationships: str = Field(description="how they relate to the other characters; '' if none")


class Location(Contract):
    id: str = Field(description="snake_case id")
    name: str
    description: str
    mood: str


class StoryBible(Contract):
    title: str
    logline: str = Field(description="the story in one sentence")
    premise: str = Field(description="the situation when the game starts, 2-4 sentences")
    setting: str = Field(description="the world: place, era, what is normal there")
    tone: str
    themes: list[str]
    characters: list[Character]
    locations: list[Location]
    arc: list[str] = Field(
        description="3-7 story beats in order; the first is this game's session, the rest are "
        "levels that may follow"
    )
    canon: list[str] = Field(description="4-10 facts no later level may break")


def story_prompt(design: GameDesign, request: str) -> str:
    return f"""Write the story bible for this game: the canon every later level, menu and
cutscene will be written from, so they stay consistent with each other.
* characters: one per design entity that is a creature or person (the player, each enemy
  type, bosses, allies; a prop only when it is a character). `entity` is its id in the design;
  keep its look and behaviour, give it a name, personality, goal and a short backstory.
  Exactly one character is the player.
* locations: the places of the story, starting with the world of this game.
* arc: 3 to 7 story beats in order. The first is this game's session as designed; the others
  are levels that may follow and raise the stakes.
* canon: 4 to 10 short facts no later level may break (who is who, the rules of the world,
  what never happens).
Keep the human's words; invent nothing that contradicts the design. Short, concrete sentences.

The human's request:
{request.strip()}

Design:
{design.model_dump_json(indent=1)}"""


def story_revision_prompt(story: StoryBible, design: GameDesign, feedback: str) -> str:
    return f"""Revise this story bible so it fits the revised design and the feedback. Keep the
names, the canon and everything that still fits; change only what must change.

Feedback:
{feedback.strip()}

Revised design:
{design.model_dump_json(indent=1)}

Current story bible:
{story.model_dump_json(indent=1)}"""


def fallback_story(design: GameDesign) -> StoryBible:
    """Offline Game Designer: a plain story read off the design."""
    cast = [e for e in design.entities if e.role in {"player", "enemy"}]
    title = lambda e: e.id.replace("_", " ").title()
    player = next(e for e in design.entities if e.role == "player")
    characters = [
        Character(
            entity=e.id,
            name=title(e),
            role="hero" if e.role == "player" else "enemy",
            look=e.look,
            personality="determined" if e.role == "player" else "relentless",
            backstory=design.player if e.role == "player" else f"{title(e)}s roam {design.world}",
            goal=design.win if e.role == "player" else f"stop {title(player)}",
            abilities=e.behaviour,
            relationships="" if e.role == "player" else f"hunts {title(player)}",
        )
        for e in cast
    ]
    return StoryBible(
        title=design.title,
        logline=design.pitch.split(".")[0].strip() + ".",
        premise=design.pitch,
        setting=design.world,
        tone=design.look,
        themes=["survival"],
        characters=characters,
        locations=[
            Location(id="arena", name="The arena", description=design.world, mood=design.look)
        ],
        arc=[f"{design.title}: {design.win}", "A harder place, a stronger foe", "The final stand"],
        canon=[*design.rules[:4], f"Lose: {design.lose}"],
    )


def fit_story(story: StoryBible, design: GameDesign) -> tuple[StoryBible, list[str]]:
    """The story repaired against the design: characters only for real entities (one per
    entity), the player always present, unique names and location ids, bounded lists.
    Returns (story, notes on what was repaired)."""
    notes, entities = [], {e.id: e for e in design.entities}
    player = next(e for e in design.entities if e.role == "player")
    chars, names = [], set()
    for c in story.characters:
        c = c.model_copy(update={"entity": snake(c.entity), "name": c.name.strip()})
        if c.entity not in entities:
            notes.append(f"character {c.name!r}: no design entity {c.entity!r}; left out")
            continue
        if any(o.entity == c.entity for o in chars):
            notes.append(f"character {c.name!r}: entity {c.entity!r} already has a character")
            continue
        base, n = c.name or c.entity.replace("_", " ").title(), 2
        name = base
        while name.casefold() in names:
            name, n = f"{base} {n}", n + 1
        names.add(name.casefold())
        chars.append(c.model_copy(update={"name": name}))
    if not any(c.entity == player.id for c in chars):
        notes.append(f"no character for the player entity {player.id!r}; added one")
        hero = fallback_story(design).characters[0]
        chars.insert(0, hero)
    locations, ids = [], set()
    for loc in story.locations:
        ident = snake(loc.id or loc.name)
        if ident not in ids:
            ids.add(ident)
            locations.append(loc.model_copy(update={"id": ident}))
    clean = lambda items, n: [re.sub(r"\s+", " ", i).strip() for i in items if i.strip()][:n]
    fitted = story.model_copy(
        update={
            "characters": chars,
            "locations": locations,
            "arc": clean(story.arc, MAX_ARC),
            "canon": clean(story.canon, MAX_CANON),
            "themes": clean(story.themes, MAX_THEMES),
        }
    )
    return fitted, notes


def story_markdown(story: StoryBible) -> str:
    chars = "\n".join(
        f"* **{c.name}** (`{c.entity}`, {c.role}): {c.look}. {c.personality}. Goal: {c.goal}."
        for c in story.characters
    )
    places = "\n".join(
        f"* **{loc.name}** (`{loc.id}`): {loc.description}" for loc in story.locations
    )
    arc = "\n".join(f"{n}. {beat}" for n, beat in enumerate(story.arc, 1))
    canon = "\n".join(f"* {fact}" for fact in story.canon)
    return (
        f"# {story.title}: story bible\n\n_{story.logline}_\n\n{story.premise}\n\n"
        f"**Setting:** {story.setting}  \n**Tone:** {story.tone}  \n"
        f"**Themes:** {', '.join(story.themes) or '-'}\n\n## Characters\n{chars}\n\n"
        f"## Locations\n{places or '- (none)'}\n\n## Arc\n{arc}\n\n## Canon\n{canon or '- (none)'}\n"
    )
