"""Game design doc: what the prompt asks for, written so code can be checked against it.

The Game Designer (Muse Spark) turns a prompt (or the G1 brief) into a `GameDesign`: every
entity the player will see (it drives the art search and Muse prompts), the rules, the
progression and difficulty curve, and `checks`, observable events the generated code must emit
(`sf.event`) so the playtest can prove the rules actually happen.
"""

import re
from typing import Literal

from pydantic import Field, model_validator

from spriteforge.agents.models import Contract

Role = Literal["player", "enemy", "pickup", "projectile", "prop", "tile", "background"]
SNAKE = re.compile(r"[^a-z0-9]+")


def snake(text: str) -> str:
    return SNAKE.sub("_", text.lower()).strip("_") or "x"


class Entity(Contract):
    id: str = Field(description="snake_case id used by the code and the art, e.g. 'skeleton'")
    role: Role
    look: str = Field(description="What it looks like: subject, colours, materials, pose")
    behaviour: str = Field(description="What it does in the game")
    width: int = Field(description="Pixels on a 320x180 screen; tiles are 16")
    height: int
    animations: list[str] = Field(description="e.g. idle, walk, attack, death; [] if static")


class Check(Contract):
    id: str
    description: str = Field(description="The rule this proves, in plain words")
    event: str = Field(description="snake_case name the code passes to sf.event()")
    min_count: int = Field(description="How many times the event must happen")
    by_second: float = Field(description="Game-time deadline in seconds; 0 = any time")


class GameDesign(Contract):
    title: str
    pitch: str = Field(description="Two or three sentences a player would read")
    camera: Literal["top-down", "side-view"]
    controls: str = Field(description="Keys and what they do (arrows/WASD, Space, X)")
    session_seconds: int = Field(description="Length of a full winning session in seconds")
    player: str = Field(description="Who the player is, stats and abilities")
    world: str = Field(description="Where it happens and how big the play area is")
    look: str = Field(description="Art direction: palette, mood, level of detail")
    entities: list[Entity]
    rules: list[str] = Field(description="Every rule of play, one per line")
    progression: list[str] = Field(description="Upgrades, levels, unlocks; [] if none")
    difficulty: list[str] = Field(description="How difficulty changes over time")
    win: str
    lose: str
    hud: list[str] = Field(description="What the HUD shows")
    checks: list[Check]

    @model_validator(mode="after")
    def normalise(self):
        seen = set()
        for e in self.entities:
            e.id = snake(e.id)
            if e.id in seen:
                raise ValueError(f"duplicate entity id {e.id!r}")
            seen.add(e.id)
            e.width, e.height = (max(8, min(128, v)) for v in (e.width, e.height))
            e.animations = [snake(a) for a in e.animations][:8]
        if sum(e.role == "player" for e in self.entities) != 1:
            raise ValueError("exactly one entity must have role 'player'")
        if not self.checks:
            raise ValueError("list at least one check (an sf.event the playtest can count)")
        self.session_seconds = max(20, min(3600, self.session_seconds))
        for c in self.checks:
            c.id, c.event = snake(c.id), snake(c.event)
            c.min_count = max(1, c.min_count)
            # A deadline at (or past) the end of the session just means "during the session".
            c.by_second = 0.0 if c.by_second >= self.session_seconds else max(0.0, c.by_second)
        return self


def design_prompt(request: str) -> str:
    return (
        "Write the game design document for this request. Keep every rule, number and mechanic "
        "the human asked for; fill gaps with the simplest choice that fits; invent nothing that "
        "contradicts the request. This document is given to a programmer who writes the whole "
        "game in Phaser 3, and to an art team who finds or draws a sprite for each entity.\n"
        "* entities: everything the player sees (player, each enemy type, pickups, projectiles, "
        "props, floor/wall/ground tiles, background). Sizes are pixels on a 320x180 screen.\n"
        "* checks: one per important rule, as an event the code will emit with sf.event(name) "
        "the moment it happens, plus 'win' (or 'lose' for endless games). A bot that cannot die "
        "plays the full session, so every check must be reachable by simple play. Use by_second "
        "for rules about timing (e.g. first spawn within 5 s, difficulty rises by 120 s).\n"
        f"\nRequest:\n{request.strip()}"
    )


def revision_prompt(design: "GameDesign", feedback: str) -> str:
    return (
        "Revise this game design document. Change only what the feedback asks for and keep "
        "everything else identical, including entity ids and check event names that still "
        f"apply.\n\nFeedback:\n{feedback.strip()}\n\nCurrent design:\n{design.model_dump_json()}"
    )


def fallback_design(request: str) -> GameDesign:
    """Offline stand-in for the Game Designer (mock runs): a small arena game whose events the
    bundled mock game emits, titled after the request."""
    words = re.findall(r"[A-Za-z]+", request)[:4]
    side = bool(re.search(r"\b(side|platform\w*|jump\w*)\b", request, re.IGNORECASE))
    return GameDesign(
        title=" ".join(w.capitalize() for w in words) or "Untitled",
        pitch=request.strip()[:200] or "Survive the arena.",
        camera="side-view" if side else "top-down",
        controls="Arrows/WASD move; Space attacks",
        session_seconds=45,
        player="A small hero with 5 HP and a short-range attack",
        world="One screen-sized arena with a tiled floor",
        look="Readable pixel art, dark floor, bright characters",
        entities=[
            Entity(
                id="hero",
                role="player",
                look="a small knight",
                behaviour="moves, attacks",
                width=16,
                height=16,
                animations=["idle", "walk"],
            ),
            Entity(
                id="slime",
                role="enemy",
                look="a green slime",
                behaviour="spawns at the edges and chases the hero",
                width=16,
                height=16,
                animations=["idle", "walk"],
            ),
            Entity(
                id="gem",
                role="pickup",
                look="a small blue gem",
                behaviour="dropped by defeated enemies; collect for points",
                width=8,
                height=8,
                animations=[],
            ),
            Entity(
                id="floor",
                role="tile",
                look="stone floor tile",
                behaviour="ground",
                width=16,
                height=16,
                animations=[],
            ),
        ],
        rules=[
            "Enemies spawn at the screen edges and chase the hero",
            "Space defeats nearby enemies; each drops a gem",
            "Survive until the timer ends",
        ],
        progression=[],
        difficulty=["Spawns get faster every 15 seconds"],
        win="Survive 45 seconds",
        lose="HP reaches 0",
        hud=["HP", "gems", "time left"],
        checks=[
            Check(
                id="spawns",
                description="enemies appear early",
                event="enemy_spawn",
                min_count=3,
                by_second=20,
            ),
            Check(
                id="combat",
                description="enemies can be defeated",
                event="enemy_defeat",
                min_count=2,
                by_second=0,
            ),
            Check(
                id="gems",
                description="gems can be collected",
                event="gem_collect",
                min_count=1,
                by_second=0,
            ),
            Check(
                id="win",
                description="the session can be won",
                event="win",
                min_count=1,
                by_second=0,
            ),
        ],
    )


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none)"


def design_markdown(d: GameDesign) -> str:
    entities = "\n".join(
        f"| `{e.id}` | {e.role} | {e.width}x{e.height} | {e.look} | {e.behaviour} | "
        f"{', '.join(e.animations) or '-'} |"
        for e in d.entities
    )
    checks = "\n".join(
        f"| `{c.event}` | {c.min_count} | {f'by {c.by_second:g} s' if c.by_second else 'any time'} "
        f"| {c.description} |"
        for c in d.checks
    )
    return (
        f"# {d.title}\n\n{d.pitch}\n\n"
        f"**Camera:** {d.camera}  \n**Controls:** {d.controls}  \n"
        f"**Session:** {d.session_seconds} s  \n**Player:** {d.player}  \n"
        f"**World:** {d.world}  \n**Look:** {d.look}\n\n"
        f"**Win:** {d.win}  \n**Lose:** {d.lose}\n\n"
        f"## Rules\n{_bullets(d.rules)}\n\n## Progression\n{_bullets(d.progression)}\n\n"
        f"## Difficulty\n{_bullets(d.difficulty)}\n\n## HUD\n{_bullets(d.hud)}\n\n"
        "## Entities\n| id | role | size | look | behaviour | animations |\n|---|---|---|---|---|---|\n"
        f"{entities}\n\n"
        "## Checks (the playtest must see these events)\n"
        f"| event | at least | when | rule |\n|---|---|---|---|\n{checks}\n"
    )
