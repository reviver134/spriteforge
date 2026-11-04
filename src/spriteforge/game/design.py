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
    endless: bool = Field(
        description="True when a run never ends (an endless runner, endless survival): the "
        "game has no levels with exits; its code spawns the world forever and speeds up"
    )
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

    @model_validator(mode="before")
    @classmethod
    def older_designs(cls, data):
        """Designs saved before `endless` existed were never endless."""
        if isinstance(data, dict) and "endless" not in data:
            data = {**data, "endless": False}
        return data

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


def design_prompt(request: str, plan: str = "") -> str:
    """`plan`: the game plan (L2.2) the design is written from: its numbers, abilities,
    mechanics, levels, feel and animations."""
    return (
        "Write the game design document for this request. Keep every rule, number and mechanic "
        "the human asked for; fill gaps with the simplest choice that fits; invent nothing that "
        "contradicts the request. This document is given to a programmer who writes the whole "
        "game in Phaser 3, and to an art team who finds or draws a sprite for each entity.\n"
        "* entities: everything the player sees (player, each enemy type, pickups, projectiles, "
        "props, floor/wall/ground tiles, background). Sizes are pixels on a 320x180 screen.\n"
        "* checks: one per important rule, as an event the code will emit with sf.event(name) "
        "the moment it happens, plus 'win'. A bot that cannot die plays the full session, so "
        "every check must be reachable by simple play (never 'lose': the bot cannot lose). Use "
        "by_second for rules about timing (e.g. first spawn within 5 s, difficulty rises by "
        "120 s).\n"
        "* endless: true for a run that never ends (an endless runner, endless survival). Such "
        "a game has no levels: its code spawns the track or waves forever and speeds up, its "
        "autoplay policy dodges what comes, and it emits 'win' when the run reaches its goal "
        "(a distance or score, or session_seconds survived) so the playtest sees it.\n"
        "* A side-view platformer that is not endless, with a game plan, plays its levels as "
        "data: the SDK builds each "
        "level, moves the player with the plan's numbers and emits level_start, level_complete, "
        "checkpoint, respawn, jump, dash and win (after the last level); use these names for "
        "those rules. A bot plays each level on its own, so per-level rules are checked per "
        "level; it never fails on purpose, so respawn (falling or getting hurt) is never a "
        "check. Its levels may use levers and gates for puzzles (the Level Designer decides "
        "where): list a lever (prop) and a gate (prop, one cell of bars or a door the levels "
        "stack) when the game suits them, so they get art.\n"
        + (
            "* The game plan below is approved groundwork: keep its numbers (movement, jump, "
            "dash), abilities, mechanics, levels, feel and animations; the player's stats and "
            "the progression come from it.\n"
            f"\nGame plan:\n{plan.strip()}\n"
            if plan.strip()
            else ""
        )
        + f"\nRequest:\n{request.strip()}"
    )


# "endless" alone is not enough: a platformer can have an endless pit or endless waves. It
# counts next to the run itself (an endless rush, an endless side-scrolling track), and any
# auto-run counts (the level bot walks a route; it cannot play a player who runs by itself).
# Auto-scrolling levels stay levels.
ENDLESS = re.compile(
    r"\b(?:endless|infinite|never[- ]ending)\s+(?:side-?scroll\w*\s+)?"
    r"(?:run\w*|rush|track|road|path|course|mode|survival|sprint|chase|journey|scroll\w*)\b"
    r"|\bauto-?run\w*",
    re.IGNORECASE,
)


def is_endless(design: "GameDesign") -> bool:
    """The Game Designer's flag, or what the design says about itself: an endless runner
    written before the flag existed (or with it left false) must not be built as levels with
    exits, whose bot cannot play a world that scrolls by itself (live 2026-11-03: a unicorn
    runner failed four playtests that way)."""
    said = " ".join([design.title, design.pitch, design.world, design.win, *design.rules])
    return design.endless or bool(ENDLESS.search(said))


def revision_prompt(design: "GameDesign", feedback: str, plan: str = "") -> str:
    """`plan`: the game plan revised for the same feedback (the design follows it)."""
    return (
        "Revise this game design document. Change only what the feedback asks for and keep "
        "everything else identical, including entity ids and check event names that still "
        f"apply.\n\nFeedback:\n{feedback.strip()}\n\n"
        + (f"The game plan, revised for this feedback (keep its numbers):\n{plan.strip()}\n\n"
           if plan.strip() else "")
        + f"Current design:\n{design.model_dump_json()}"
    )


def fallback_design(request: str, platformer: bool = False) -> GameDesign:
    """Offline stand-in for the Game Designer (mock runs): a small arena game whose events the
    bundled mock game emits, titled after the request; with `platformer` (a planned side-view
    game: levels as data), a platformer whose events the mock platformer emits."""
    words = re.findall(r"[A-Za-z]+", request)[:4]
    if platformer:
        return fallback_platformer(" ".join(w.capitalize() for w in words) or "Untitled", request)
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


def fallback_platformer(title: str, request: str) -> GameDesign:
    """The offline platformer: a hero that runs and jumps through levels of floors, gaps,
    spikes and slimes to a door (the mock platformer game plays it)."""
    E = Entity
    return GameDesign(
        title=title,
        pitch=request.strip()[:200] or "Jump across the levels to the door.",
        camera="side-view",
        controls="Arrows/WASD move; Space jumps",
        session_seconds=120,
        player="A small hero that runs and jumps (the plan's numbers)",
        world="Side-view levels of floors, gaps, spikes and slimes, each ending at a door",
        look="Readable pixel art, bright characters on a calm sky",
        entities=[
            E(id="hero", role="player", look="a small knight", behaviour="runs and jumps",
              width=12, height=14, animations=["idle", "walk", "jump"]),
            E(id="slime", role="enemy", look="a green slime", behaviour="patrols its floor; "
              "touching it sends the hero back to the checkpoint", width=14, height=10,
              animations=["idle", "walk"]),
            E(id="gem", role="pickup", look="a small blue gem", behaviour="collect for points",
              width=8, height=8, animations=[]),
            E(id="floor", role="tile", look="grass-topped stone", behaviour="ground and platforms",
              width=16, height=16, animations=[]),
            E(id="spikes", role="tile", look="grey metal spikes", behaviour="touching them sends "
              "the hero back to the checkpoint", width=16, height=16, animations=[]),
            E(id="flag", role="prop", look="a small blue flag", behaviour="checkpoint",
              width=12, height=16, animations=[]),
            E(id="door", role="prop", look="a wooden door", behaviour="the level's exit",
              width=16, height=24, animations=[]),
        ],
        rules=["Run and jump from floor to floor", ("Spikes and slimes send the hero back to "
               "the last checkpoint"), "Gems add to the score", "The door ends the level"],
        progression=["Levels in order; the last door wins the game"],
        difficulty=["Each level adds wider gaps, higher floors, spikes and slimes"],
        win="Reach the door of the last level",
        lose="",
        hud=["gems", "level"],
        checks=[
            Check(id="jumps", description="the hero jumps", event="jump", min_count=1, by_second=0),
            Check(id="gems", description="gems can be collected", event="gem_collect",
                  min_count=1, by_second=0),
            Check(id="levels", description="levels can be finished", event="level_complete",
                  min_count=1, by_second=0),
            Check(id="win", description="the game can be won", event="win", min_count=1,
                  by_second=0),
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
