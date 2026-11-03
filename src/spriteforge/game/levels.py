"""Levels as data (L2.3, platformers first): the Level Designer writes each level, code checks it.

A `LevelSpec` is a map (rows of characters, top row first) and a legend that says what each
character is: tiles (solid, one_way, hazard) and things (breakable blocks, crates and what they
hold, pickups and the ability they give, patrolling enemies and how they are beaten,
checkpoints, the exit, the start). The game loads levels through the SDK (`sf.level`); the
Programmer writes the mechanics once.

Code, not the model, decides what holds (`evaluate`):
* `fit_level` repairs what it can: ids and names, unknown characters, ragged rows, a missing or
  second start or exit, things floating in the air (dropped onto the floor below), events the
  design does not check, ability names close to the plan's.
* `reach.solve` proves the level can be played with the plan's movement: every floor, the exit,
  and every lock with its key before it. Unreachable floors are lowered when that makes them
  reachable (the L2.1 slab above the jump).
* `check_level` lists what remains: the exit out of reach (and why), unreachable floors and
  pickups, a level that does not use its planned new idea, a stretch between checkpoints with
  no challenge, a level easier than the one before (code's measure), unknown challenges.
"""

from __future__ import annotations

import itertools
import math
import re
from collections import deque
from typing import Literal

from pydantic import BaseModel, Field

from spriteforge.agents.models import Contract

from .design import GameDesign
from .plan import TILE, PlanRecord
from .reach import Grid, Motion, Solution, Thing, World, gap_limits, key, solve

Kind = Literal["solid", "one_way", "hazard", "breakable", "crate", "pickup", "enemy",
               "checkpoint", "exit", "start"]  # fmt: skip
TILE_KINDS = ("solid", "one_way", "hazard")
# Events the SDK emits for every level game; a level lists only the game's own events.
SDK_EVENTS = ("level_start", "level_complete", "checkpoint", "respawn", "jump", "dash", "win")
STANDS = ("crate", "enemy", "checkpoint", "exit", "start")  # things that rest on a floor
EMPTY = " ."
MIN_COLS, MAX_COLS, MIN_ROWS, MAX_ROWS = 24, 200, 8, 18
PATROL = 3  # an enemy walks at most this many tiles each way from where it is placed
WEIGHTS = {"gap": 1.0, "wide gap": 1.0, "climb": 1.0, "dash": 1.5, "hazard": 1.0,
           "enemy": 2.0, "lock": 2.0}  # fmt: skip


class LegendEntry(Contract):
    char: str = Field(description="one character used in the rows (not ' ' or '.')")
    kind: Kind
    entity: str = Field(description="the design entity id it shows (the player's id for start)")
    needs: str = Field(description="ability that opens, breaks or beats it; '' = no ability needed")
    opened_by: Literal["touch", "dash", "never"] = Field(
        description="how the player opens a breakable or crate or beats an enemy: touch, dash "
        "into it, or never (an enemy to avoid); 'never' for tiles and the other things"
    )
    holds: str = Field(description="crate: the entity id it drops when opened; '' otherwise")
    gives: str = Field(
        description="pickup, or what a crate drops: the ability it gives; '' (coins and others)"
    )


class ChallengeAt(Contract):
    name: str = Field(description="a challenge from the plan's vocabulary")
    column: int = Field(description="the column where it starts (0 = left edge)")


class LevelSpec(Contract):
    n: int
    name: str
    legend: list[LegendEntry]
    rows: list[str] = Field(
        description="the map, top row first, every row the same length; ' ' or '.' is empty"
    )
    challenges: list[ChallengeAt]
    events: list[str] = Field(description="the design's check events this level makes happen")
    exclusive: list[str] = Field(
        description="abilities that replace each other (taking one drops the others); [] if "
        "they stack"
    )
    notes: str = Field(description="how the level teaches its idea and where its challenges are")


class LevelRecord(BaseModel):
    """What the run keeps per level: the fitted spec and what code made of it."""

    spec: LevelSpec
    solved: bool
    route: list[dict] = Field(default_factory=list)  # the bot's steps (reach.Step.data)
    problems: list[str] = Field(default_factory=list)  # what code could not repair
    notes: list[str] = Field(default_factory=list)  # what code repaired
    features: list[list] = Field(default_factory=list)  # [column, kind] along the route
    difficulty: float = 0.0  # code's measure (never below the level before)
    retried: bool = False  # the Level Designer was asked once more with the problems
    by: str = ""


# --- the player and the plan's movement -------------------------------------------------------


def player_body(design: GameDesign, manifest: dict | None = None) -> tuple[float, float]:
    """The player's body in tiles: from the build's art (its physics body) when there is one,
    otherwise the design's size."""
    player = next(e for e in design.entities if e.role == "player")
    if manifest:
        scale = manifest.get("scale") or 1
        sprite = manifest.get("sprites", {}).get(player.id)
        if sprite:
            w, h = (sprite.get("body") or [0, 0, *sprite["frame"]])[2:4]
            return round(w / (TILE * scale), 3), round(h / (TILE * scale), 3)
    return round(min(player.width, 15) / TILE, 3), round(player.height / TILE, 3)


def motion_for(plan: PlanRecord, body: tuple[float, float], fps: int = 30) -> Motion:
    m, k = plan.plan.movement, plan.kinematics
    gravity = (k.gravity / k.tile) if k else 60.0
    return Motion(speed=m.move_speed, gravity=gravity, jump=m.jump_height if k else 0.0,
                  dash=m.dash_distance, cooldown=m.dash_cooldown, coyote=m.coyote_time,
                  body_w=body[0], body_h=body[1], fps=fps)  # fmt: skip


def plan_abilities(plan: PlanRecord) -> dict[str, str]:
    """Ability keys -> names: the plan's abilities plus move/jump/dash from the movement."""
    m = plan.plan.movement
    out = {key(a.name): a.name for a in plan.plan.abilities}
    for name, on in (("move", True), ("jump", m.jump_height > 0), ("dash", m.dash_distance > 0)):
        if on:
            out.setdefault(name, name)
    return out


def start_abilities(plan: PlanRecord) -> set[str]:
    """What the player holds when a level starts: movement, and abilities 'from start'."""
    words = {"start", "beginning", "begin", "default", "always"}
    held = {k for k in plan_abilities(plan) if k in {"move", "jump", "dash"}}
    return held | {key(a.name) for a in plan.plan.abilities
                   if words & set(key(a.unlocked_by).split())}  # fmt: skip


def match_ability(name: str, known: dict[str, str]) -> str | None:
    """The plan's name for an ability ('' stays ''); a loose match when exactly one fits."""
    if not name.strip():
        return ""
    k = key(name)
    if k in known:
        return known[k]
    near = [v for kk, v in known.items() if k in kk or kk in k]
    return near[0] if len(near) == 1 else None


# --- repairs -----------------------------------------------------------------------------------


def fit_level(spec: LevelSpec, n: int, design: GameDesign, plan: PlanRecord) -> tuple[LevelSpec, list[str]]:
    """Repairs code can make; returns the fitted spec and notes on what it changed."""
    notes = []
    ids = {e.id for e in design.entities}
    player = next(e.id for e in design.entities if e.role == "player")
    known = plan_abilities(plan)
    legend, chars = [], set()
    for e in spec.legend:
        ch = e.char[:1]
        if not ch or ch in EMPTY or ch in chars:
            notes.append(f"legend entry {e.char!r} ({e.kind}) dropped: empty or repeated character")
            continue
        entity = player if e.kind == "start" else match_entity(e.entity, e.kind, design)
        if entity is None and e.kind == "checkpoint":  # works without art: the SDK draws a flag
            notes.append(f"legend {ch!r}: the design has no checkpoint entity ({e.entity!r}); "
                         "the SDK draws a flag")  # fmt: skip
            entity = ""
        if entity is None:
            notes.append(f"legend {ch!r}: unknown entity {e.entity!r}; its cells are left empty")
            continue
        if entity and entity != e.entity.strip() and e.kind != "start":
            notes.append(f"legend {ch!r}: entity {e.entity!r} read as {entity!r}")
        fixed = {"char": ch, "entity": entity}
        for field in ("needs", "gives"):
            name = match_ability(getattr(e, field), known)
            if name is None:
                notes.append(f"legend {ch!r}: {field} {getattr(e, field)!r} is not an ability of "
                             f"the plan ({', '.join(known.values())}); dropped")  # fmt: skip
                name = ""
            elif name != getattr(e, field):
                notes.append(f"legend {ch!r}: {field} {getattr(e, field)!r} read as {name!r}")
            fixed[field] = name
        if e.kind in {"crate", "breakable"} and e.opened_by == "never":
            fixed["opened_by"] = "touch"
            notes.append(f"legend {ch!r}: a {e.kind} must open; opened_by set to touch")
        elif e.kind not in {"crate", "breakable", "enemy"} and e.opened_by != "never":
            fixed["opened_by"] = "never"
        if e.kind != "crate" and e.holds:
            fixed["holds"] = ""
        elif e.kind == "crate" and e.holds and e.holds not in ids:
            notes.append(f"legend {ch!r}: the crate holds unknown entity {e.holds!r}; it holds nothing")
            fixed |= {"holds": "", "gives": ""}
        if e.kind not in {"pickup", "crate"} and (fixed.get("gives") or e.gives):
            fixed["gives"] = ""
        legend.append(e.model_copy(update=fixed))
        chars.add(ch)
    rows = [r.rstrip("\n") for r in spec.rows][:MAX_ROWS]
    if len(spec.rows) > MAX_ROWS:
        notes.append(f"{len(spec.rows)} rows cut to {MAX_ROWS}")
    width = min(MAX_COLS, max((len(r) for r in rows), default=0))
    unknown = set()
    fixed_rows = []
    for r in rows:
        row = "".join(ch if ch in chars else " " for ch in r[:width]).ljust(width)
        unknown |= {ch for ch in r if ch not in chars and ch not in EMPTY}
        fixed_rows.append(row)
    if unknown:
        notes.append(f"characters not in the legend left empty: {''.join(sorted(unknown))!r}")
    if len({len(r) for r in spec.rows}) > 1:
        notes.append(f"rows of different lengths padded to {width} columns")
    rows = fixed_rows
    kinds = {e.char: e.kind for e in legend}
    rows, moved = settle(rows, kinds)
    notes += moved
    rows, legend = one_of(rows, legend, kinds, "start", player, notes)
    kinds = {e.char: e.kind for e in legend}
    rows, legend = one_of(rows, legend, kinds, "exit", exit_entity(design), notes)
    # A checkpoint never wears the look of another thing in the level: the SDK draws its own
    # flag. Live 2026-11-03: checkpoints drawn as the crate looked like crates that never open
    # ("some boxes are unbreakable").
    others = {e.entity for e in legend if e.kind not in {"checkpoint", "start"} and e.entity}
    for i, e in enumerate(legend):
        if e.kind == "checkpoint" and e.entity in others:
            notes.append(f"legend {e.char!r}: a checkpoint drawn as {e.entity!r} would look like "
                         f"the {next(x.kind for x in legend if x.entity == e.entity and x.kind != 'checkpoint')}; "
                         "the SDK draws a flag")  # fmt: skip
            legend[i] = e.model_copy(update={"entity": ""})
    checks = {c.event for c in design.checks}
    events = [e for e in dict.fromkeys(spec.events) if e in checks]
    if dropped := [e for e in spec.events if e not in checks]:
        notes.append(f"events the design does not check dropped: {', '.join(dropped)}")
    exclusive = [x for x in (match_ability(a, known) for a in spec.exclusive) if x]
    challenges = [c.model_copy(update={"column": max(0, min(width - 1, c.column))})
                  for c in spec.challenges]  # fmt: skip
    if spec.n != n:
        notes.append(f"level number {spec.n} set to {n}")
    return spec.model_copy(update={"n": n, "name": spec.name.strip() or f"Level {n}",
                                   "legend": legend, "rows": rows, "events": events,
                                   "exclusive": exclusive, "challenges": challenges}), notes  # fmt: skip


def settle(rows: list[str], kinds: dict[str, str]) -> tuple[list[str], list[str]]:
    """Things that rest on a floor (crates, enemies, checkpoints, the exit, the start) drop
    onto the first floor below them."""
    grid = [list(r) for r in rows]
    notes = []
    solid = lambda ch: kinds.get(ch) in {"solid", "one_way", "breakable", "crate"}
    for r in range(len(grid) - 2, -1, -1):
        for c, ch in enumerate(grid[r]):
            if kinds.get(ch) not in STANDS or solid(grid[r + 1][c]):
                continue
            to = r
            while to + 1 < len(grid) and grid[to + 1][c] in EMPTY:
                to += 1
            if to + 1 < len(grid) and solid(grid[to + 1][c]) and to != r:
                grid[r][c], grid[to][c] = " ", ch
                notes.append(f"{kinds[ch]} at column {c} dropped from row {r} onto the floor at row {to + 1}")
    return ["".join(g) for g in grid], notes


KIND_WORDS = {"checkpoint": r"checkpoint|flag|banner|bell|torch", "exit": r"door|exit|portal|goal|gate"}


def match_entity(name: str, kind: str, design: GameDesign) -> str | None:
    """The design's entity id for `name`: exact, else the one id that contains it (or it
    contains), else the one that shares a word, else the one entity that looks like the kind
    (a flag for a checkpoint, a door for an exit); None when that is not exactly one."""
    ids = [e.id for e in design.entities]
    name = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    if name in ids:
        return name
    words = set(name.split("_")) - {""}
    for found in ([i for i in ids if name and (name in i or i in name)],
                  [i for i in ids if words & set(i.split("_"))],
                  [e.id for e in design.entities if kind in KIND_WORDS
                   and re.search(KIND_WORDS[kind], f"{e.id} {e.look}", re.IGNORECASE)]):  # fmt: skip
        if len(found) == 1:
            return found[0]
    return None


def exit_entity(design: GameDesign) -> str:
    """What a placed exit shows: a door-like prop, else any prop, else the player."""
    props = [e for e in design.entities if e.role == "prop"]
    words = r"door|exit|portal|goal|gate|flag"
    door = next((e.id for e in props if re.search(words, f"{e.id} {e.look}", re.IGNORECASE)), None)
    return door or (props[0].id if props else next(e.id for e in design.entities if e.role == "player"))


def one_of(rows, legend, kinds, kind: str, entity: str, notes: list[str]):
    """Exactly one start (the leftmost is kept) and one exit (the rightmost); a missing one is
    placed on the first floor from that side."""
    at = [(c, r) for r, row in enumerate(rows) for c, ch in enumerate(row) if kinds.get(ch) == kind]
    grid = [list(r) for r in rows]
    if len(at) > 1:
        keep = min(at) if kind == "start" else max(at)
        for c, r in at:
            if (c, r) != keep:
                grid[r][c] = " "
        notes.append(f"{len(at) - 1} extra {kind}(s) removed")
    elif not at:
        char = next((e.char for e in legend if e.kind == kind), None)
        if char is None:
            char = next(ch for ch in ("@", "S", "s", "&") if ch not in kinds) if kind == "start" else \
                next(ch for ch in ("E", "X", "x", "%") if ch not in kinds)  # fmt: skip
            legend = [*legend, LegendEntry(char=char, kind=kind, entity=entity, needs="",
                                           opened_by="never", holds="", gives="")]  # fmt: skip
        floor = lambda ch: kinds.get(ch) in {"solid", "one_way"}
        cols = range(len(grid[0])) if kind == "start" else range(len(grid[0]) - 1, -1, -1)
        for c in cols:
            spot = next((r for r in range(len(grid) - 1) if grid[r][c] in EMPTY and floor(grid[r + 1][c])), None)
            if spot is not None:
                grid[spot][c] = char
                notes.append(f"no {kind}: placed at column {c}, row {spot}")
                break
    return ["".join(g) for g in grid], legend


# --- the grid code checks ----------------------------------------------------------------------


def parse(spec: LevelSpec, design: GameDesign) -> Grid:
    legend = {e.char: e for e in spec.legend}
    sizes = {e.id: e for e in design.entities}
    rows, cols = len(spec.rows), len(spec.rows[0]) if spec.rows else 0
    tiles, things, start, done = {}, [], (0, 0), set()
    for r, row in enumerate(spec.rows):
        for c, ch in enumerate(row):
            e = legend.get(ch)
            if e is None or (c, r) in done:
                continue
            if e.kind in TILE_KINDS:
                tiles[(c, r)] = e.kind
                continue
            if e.kind == "start":
                start = (c, r)
                continue
            cells = group(spec.rows, c, r) if e.kind == "breakable" else [(c, r)]
            done.update(cells)
            ent = sizes.get(e.entity)
            tall = max(1, math.ceil((ent.height if ent else TILE) / TILE - 0.25)) if e.kind == "enemy" else 1
            things.append(Thing(f"t{len(things) + 1}", e.kind, e.entity, tuple(cells), e.needs,
                                e.opened_by, e.holds, e.gives, tall=tall))  # fmt: skip
    grid = Grid(cols, rows, tiles, things, start)
    for t in grid.things:
        if t.kind == "enemy":
            t.patrol = patrol(grid, t)
    return grid


def group(rows: list[str], c: int, r: int) -> list[tuple[int, int]]:
    """Connected cells with the same character (one breakable wall)."""
    ch, seen, queue = rows[r][c], {(c, r)}, deque([(c, r)])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= ny < len(rows) and 0 <= nx < len(rows[ny]) and (nx, ny) not in seen \
                    and rows[ny][nx] == ch:  # fmt: skip
                seen.add((nx, ny))
                queue.append((nx, ny))
    return sorted(seen)


def patrol(grid: Grid, t: Thing) -> tuple[int, int]:
    """The columns an enemy walks between: along its floor, at most PATROL tiles each way,
    stopping at walls, edges, hazards and other things."""
    c, r = t.col, t.row
    taken = {cell for o in grid.things if o is not t for cell in o.cells}
    floor = lambda x: grid.tiles.get((x, r + 1)) in {"solid", "one_way"} or any(
        o.kind == "breakable" and (x, r + 1) in o.cells for o in grid.things)
    free = lambda x: 0 <= x < grid.cols and (x, r) not in grid.tiles and (x, r) not in taken
    a = b = c
    while a - 1 >= c - PATROL and free(a - 1) and floor(a - 1):
        a -= 1
    while b + 1 <= c + PATROL and free(b + 1) and floor(b + 1):
        b += 1
    return a, b


# --- route features and difficulty ---------------------------------------------------------------


def features(grid: Grid, sol: Solution, m: Motion) -> list[list]:
    """[column, kind] for what the route asks of the player: gaps, wide gaps, climbs, dashes,
    hazards and enemies jumped over, locks opened; plus every enemy in the level."""
    base = World(grid, frozenset(), m)
    out = []
    cell = grid.start
    for step in sol.route:
        if step.move is not None and step.cell is not None:
            a, b = cell, step.cell
            side = step.move.side
            off = math.floor(step.move.x0 - side * m.body_w / 2)  # the take-off column
            lo, hi = sorted((off, b[0]))
            pits = [c for c in range(lo + 1, hi) if (c, a[1] + 1) not in base.support
                    and (c, b[1] + 1) not in base.support]  # fmt: skip
            if pits:
                out.append([lo, "gap"])
                if len(pits) >= max(2, m_gap(m)):
                    out.append([lo, "wide gap"])
            if a[1] - b[1] >= 2:
                out.append([lo, "climb"])
            if step.move.dash >= 0:
                out.append([lo, "dash"])
            if any((c, r) in base.hazard for c in range(lo + 1, hi) for r in range(min(a[1], b[1]) - 1, max(a[1], b[1]) + 2)):
                out.append([lo, "hazard"])
        if step.do in {"touch", "dash"} and step.thing:
            t = grid.thing(step.thing)
            if t.needs:
                out.append([t.col, "lock"])
        if step.cell is not None:
            cell = step.cell
    out += [[t.col, "enemy"] for t in grid.things if t.kind == "enemy"]
    return sorted(out)


def m_gap(m: Motion) -> int:
    air = m.rise_time(0, up=False) or 0
    return max(1, math.ceil(m.speed * air) - 1)


def difficulty(feats: list[list], sol: Solution) -> float:
    """Code's measure: weighted challenges along the route, plus a little per jump."""
    jumps = sum(1 for s in sol.route if s.move is not None)
    return round(sum(WEIGHTS.get(k, 0) for _, k in feats) + jumps / 10, 2)


# --- checks ------------------------------------------------------------------------------------


def floors_out_of_reach(grid: Grid, sol: Solution, m: Motion) -> list[tuple[int, int, int]]:
    """Floors (on tiles) the player can never stand on: (c0, c1, row)."""
    base = World(grid, frozenset(), m)
    out = []
    for run in base.runs:
        cells = [(c, run.row) for c in range(run.c0, run.c1 + 1)]
        on_tiles = all(grid.tiles.get((c, run.row + 1)) in {"solid", "one_way"} for c, _ in cells)
        if on_tiles and not any(c in sol.reached for c in cells):
            out.append((run.c0, run.c1, run.row))
    return out


def unreachable_things(grid: Grid, sol: Solution, m: Motion) -> list[Thing]:
    out = []
    reach = sol.reached
    air = m.rise_time(0, up=False) or 0
    half = math.ceil(m.speed * air / 2) + 1  # a coin in the arc of a jump from a floor nearby
    for t in grid.things:
        if t.kind == "checkpoint":
            c, r = t.cells[0]
            if not any((x, y) in reach for x in (c - 1, c, c + 1) for y in range(r, r + m.tall)):
                out.append(t)
        elif t.kind == "pickup":
            c, r = t.cells[0]
            if not any((x, y) in reach for x in range(c - half, c + half + 1)
                       for y in range(r, r + m.tall + m.max_rise + 1)):  # fmt: skip
                out.append(t)
        elif t.kind == "crate":
            c, r = t.cells[0]
            if not any((x, y) in reach for x in (c - 1, c + 1) for y in range(r, r + m.tall)):
                out.append(t)
    return out


def describe(t: Thing) -> str:
    return f"the {t.kind} ({t.entity}) at column {t.col}, row {t.row}"


def asks_dash(challenge, name: str) -> bool:
    """A challenge asks for a dash when its name, its test or its needs say so."""
    words = f"{name} {challenge.tests} {' '.join(challenge.needs)}" if challenge else name
    return "dash" in key(words).split()


def check_level(spec: LevelSpec, grid: Grid, sol: Solution, m: Motion, plan: PlanRecord,
                design: GameDesign, previous: LevelRecord | None, design_held: set[str],
                feats: list[list], score: float) -> list[str]:  # fmt: skip
    """What is wrong with a fitted level; [] when it holds."""
    problems = []
    if not MIN_COLS <= grid.cols or not MIN_ROWS <= grid.rows:
        problems.append(f"the map is {grid.cols}x{grid.rows}; make it at least {MIN_COLS} columns "
                        f"and {MIN_ROWS} rows")  # fmt: skip
    if not any(t.kind == "exit" for t in grid.things):
        problems.append("the level has no exit")
    if not sol.solved:
        problems += [p[:1].upper() + p[1:] for p in sol.problems]
        problems.append(f"keep floors within {m.max_rise} tiles up and gaps within the jump "
                        "(see the movement numbers)")  # fmt: skip
    for c0, c1, r in floors_out_of_reach(grid, sol, m):
        below = [y for (x, y) in sol.reached if c0 - 3 <= x <= c1 + 3 and y > r]
        how = f" ({min(below) - r} tiles above the nearest floor the player reaches; a jump " \
              f"reaches {m.max_rise})" if below else ""  # fmt: skip
        problems.append(f"the floor at columns {c0}-{c1}, row {r} cannot be reached{how}")
    for t in unreachable_things(grid, sol, m):
        problems.append(f"{describe(t)} cannot be reached")
    lp = plan.plan.levels[spec.n - 1] if spec.n <= len(plan.plan.levels) else None
    if lp:
        earlier = {key(a) for x in plan.plan.levels[: spec.n - 1] for a in x.abilities}
        new = [a for a in lp.abilities if key(a) not in earlier and key(a) not in {"move", "jump"}]
        for a in new:
            needs = [t for t in grid.things if key(t.needs) == key(a)]
            gives = [t for t in grid.things if key(t.gives) == key(a)]
            if not needs and not gives:
                problems.append(f"the level never uses {a}, part of its new idea ({lp.new_idea}): "
                                "place something that needs it or gives it")  # fmt: skip
            elif gives and needs and key(a) not in design_held and sol.solved:
                without = Grid(grid.cols, grid.rows, grid.tiles,
                               [t if key(t.gives) != key(a) else
                                Thing(t.id, t.kind, t.entity, t.cells, t.needs, t.opened_by,
                                      t.holds, "", t.patrol, t.tall) for t in grid.things],
                               grid.start)  # fmt: skip
                if solve(without, m, design_held, set(spec.exclusive), limit=150).solved:
                    problems.append(f"the exit can be reached without {a}, the level's new idea: "
                                    "the player gets past what needs it (over it, around it or "
                                    "through a gap); make it block the way to the exit")  # fmt: skip
        vocab = {key(c.name) for c in plan.plan.challenges}
        placed = {key(c.name) for c in spec.challenges}
        for c in spec.challenges:
            if key(c.name) not in vocab:
                problems.append(f"challenge {c.name!r} is not in the plan's vocabulary")
        for name in lp.challenges:
            if key(name) not in placed:
                problems.append(f"the plan's challenge {name!r} for this level is not placed")
        by_name = {key(c.name): c for c in plan.plan.challenges}
        dashes = [n for n in lp.challenges if n and asks_dash(by_name.get(key(n)), n)]
        if dashes and m.dash > 0 and sol.solved and not any(s.move and s.move.dash >= 0 for s in sol.route):
            jump, dash = gap_limits(m)
            gaps = f" (a jump crosses gaps up to {jump} tiles; a dash gap is {jump + 1}-{dash} tiles)"
            problems.append(f"the plan's challenge {dashes[0]!r} asks for a dash, but the way to the "
                            f"exit never needs one{gaps}")  # fmt: skip
    if sol.solved:
        exit_ = next(t for t in grid.things if t.kind == "exit")
        marks = sorted({grid.start[0], *(t.col for t in grid.things if t.kind == "checkpoint"), exit_.col})
        for a, b in itertools.pairwise(marks):
            if b - a >= 6 and not any(a <= c < b for c, _ in feats):
                problems.append(f"columns {a}-{b} (between checkpoints) have no challenge: no gap, "
                                "climb, hazard, enemy or lock on the way")  # fmt: skip
    if previous is not None and sol.solved and previous.solved and score < previous.difficulty:
        problems.append(f"level {spec.n} measures easier than level {spec.n - 1} ({score:g} < "
                        f"{previous.difficulty:g} by code's count of gaps, climbs, dashes, hazards, "
                        "enemies and locks): make it harder")  # fmt: skip
    return problems


# --- lowering floors out of reach ---------------------------------------------------------------


def lower(rows: list[str], kinds: dict[str, str], c0: int, c1: int, r: int, by: int) -> list[str] | None:
    """Move the slab under floor (c0..c1, r) and what stands on it `by` rows down, when the
    cells below are empty; None when it cannot move."""
    slab = r + 1
    tile = lambda ch: kinds.get(ch) in {"solid", "one_way"}
    if slab >= len(rows) or not all(tile(rows[slab][c]) for c in range(c0, c1 + 1)):
        return None
    a, b = c0, c1
    while a - 1 >= 0 and tile(rows[slab][a - 1]):
        a -= 1
    while b + 1 < len(rows[slab]) and tile(rows[slab][b + 1]):
        b += 1
    grid = [list(x) for x in rows]
    moving = [(c, y) for c in range(a, b + 1) for y in range(max(0, slab - 3), slab + 1)
              if grid[y][c] not in EMPTY and (y == slab or kinds.get(grid[y][c]) not in TILE_KINDS)]  # fmt: skip
    if not moving or slab + by >= len(grid) - 1:
        return None
    chars = {cell: grid[cell[1]][cell[0]] for cell in moving}
    for c, y in moving:
        grid[y][c] = " "
    for (c, y), ch in chars.items():
        if grid[y + by][c] not in EMPTY:
            return None
        grid[y + by][c] = ch
    return ["".join(g) for g in grid]


# Event words that name what opens on the route: an event about beating an enemy needs the
# route to beat one (live 2026-11-02: level 3 listed kill_enemy, its route jumped over every
# enemy, and three repair rounds asked the Programmer for a kill the bot never makes).
EVENT_KINDS = (
    (re.compile(r"kill|defeat|beat|stomp|slay"), {"enemy"}),
    (re.compile(r"break|smash|shatter|crumble|wall"), {"breakable"}),
    (re.compile(r"dissolve|crate"), {"crate"}),
)


def events_off_route(spec: LevelSpec, grid, sol) -> list[str]:
    """The level's events that name something the route never opens (a kill without an enemy
    beaten, a break without a wall broken). Events code cannot read stay."""
    kind_of = {t.id: t.kind for t in grid.things}
    opened = {kind_of.get(st.get("thing")) for st in (s.data() for s in sol.route)
              if st["do"] in {"touch", "dash"}}  # fmt: skip
    out = []
    for event in spec.events:
        for words, kinds in EVENT_KINDS:
            if words.search(event):
                if not (kinds & opened):
                    out.append(event)
                break
    return out


REACH_AHEAD = 12  # columns past the furthest point the player reaches that clear_way looks at


def clear_way(spec: LevelSpec, design: GameDesign, m, held, sol, kinds: dict[str, str]):
    """When the exit cannot be reached, one change that gets the player further, looking just
    past the furthest point: an enemy nothing beats is removed; else the next gap in the floor
    is narrowed from its far side, a column at a time; else an enemy that can be beaten is
    removed (it is part of the level's idea, so it goes last). Returns (spec, grid, solution,
    note) or None. Live 2026-11-03: an enemy nothing beats on the only path and a gap that
    landed in an enemy's patrol were built, and the bot could not finish either level."""
    far_c, far_r = max(sol.reached, key=lambda c: (c[0], -c[1]), default=(0, 0))
    rows, exclusive = spec.rows, set(spec.exclusive)

    def attempt(new_rows: list[str]):
        trial = spec.model_copy(update={"rows": new_rows})
        grid = parse(trial, design)
        found = solve(grid, m, held, exclusive)
        further = found.solved or max((c for c, _ in found.reached), default=-1) > far_c
        return (trial, grid, found) if further else None

    never = {e.char for e in spec.legend if e.opened_by == "never"}
    enemies = [(c, r, ch) for r, row in enumerate(rows) for c, ch in enumerate(row)
               if kinds.get(ch) == "enemy" and far_c < c <= far_c + REACH_AHEAD]  # fmt: skip

    def remove(which):
        for c, r, ch in sorted(which):
            if done := attempt([row[:c] + " " + row[c + 1:] if y == r else row
                                for y, row in enumerate(rows)]):  # fmt: skip
                return (*done, f"an enemy ('{ch}') at column {c} blocked the way past column "
                               f"{far_c}: removed")  # fmt: skip
        return None

    return (remove([e for e in enemies if e[2] in never]) or narrow_gap(rows, far_c, far_r, kinds, attempt)
            or remove([e for e in enemies if e[2] not in never]))  # fmt: skip


def narrow_gap(rows: list[str], far_c: int, far_r: int, kinds: dict[str, str], attempt):
    """The first gap in the floor past column far_c, filled from its far side until `attempt`
    gets the player further."""
    floor = far_r + 1
    if floor >= len(rows) or kinds.get(rows[floor][far_c]) != "solid":
        return None
    ground, line = rows[floor][far_c], rows[floor]
    a = far_c + 1
    while a < len(line) and kinds.get(line[a]) in ("solid", "one_way"):
        a += 1
    b = a
    while b < len(line) and line[b] in EMPTY:
        b += 1
    if a >= len(line) or b >= len(line) or a > far_c + REACH_AHEAD:
        return None  # no gap ahead, or it runs off the level
    depth = [y for y in range(floor, len(rows)) if rows[y][far_c] == ground]
    for k in range(1, b - a + 1):
        cols = range(b - k, b)
        new = ["".join(ground if (y in depth and x in cols and ch in EMPTY) else ch
                       for x, ch in enumerate(row)) for y, row in enumerate(rows)]  # fmt: skip
        if done := attempt(new):
            what = f"narrowed to columns {a}-{b - k - 1}" if k < b - a else "filled in"
            return (*done, f"the gap at columns {a}-{b - 1} could not be crossed: {what}")
    return None


def evaluate(spec: LevelSpec, n: int, design: GameDesign, plan: PlanRecord,
             previous: LevelRecord | None, body: tuple[float, float], fps: int = 30,
             retried: bool = False, notes: list[str] = (), by: str = "") -> LevelRecord:  # fmt: skip
    """Fit, solve, lower floors out of reach, then check."""
    fitted, repairs = fit_level(spec, n, design, plan)
    m = motion_for(plan, body, fps)
    held = start_abilities(plan)
    grid = parse(fitted, design)
    sol = solve(grid, m, held, set(fitted.exclusive))
    kinds = {e.char: e.kind for e in fitted.legend}
    # Code repairs what it can, one change at a time: a floor out of reach is lowered; when the
    # exit still cannot be reached, the way past the furthest point is cleared (clear_way).
    for _ in range(8):
        better = None
        for c0, c1, r in floors_out_of_reach(grid, sol, m):
            for down in (1, 2, 3):
                rows = lower(fitted.rows, kinds, c0, c1, r, down)
                if rows is None:
                    continue
                trial = fitted.model_copy(update={"rows": rows})
                g2 = parse(trial, design)
                s2 = solve(g2, m, held, set(fitted.exclusive))
                if (c0, r + down) in s2.reached and (s2.solved or not sol.solved):
                    better = (trial, g2, s2, (f"the floor at columns {c0}-{c1}, row {r} was out of "
                              f"reach: lowered {down} row(s)"))  # fmt: skip
                    break
            if better:
                break
        if better is None and not sol.solved:
            better = clear_way(fitted, design, m, held, sol, kinds)
        if better is None:
            break
        fitted, grid, sol, note = better
        repairs.append(note)
    if sol.solved and (unseen := events_off_route(fitted, grid, sol)):
        fitted = fitted.model_copy(update={"events": [e for e in fitted.events if e not in unseen]})
        repairs.append(f"events the route never makes happen dropped: {', '.join(unseen)} (the bot "
                       "plays the route, so the playtest could not see them)")  # fmt: skip
    feats = features(grid, sol, m) if sol.solved else []
    score = difficulty(feats, sol) if sol.solved else 0.0
    problems = check_level(fitted, grid, sol, m, plan, design, previous, held, feats, score)
    return LevelRecord(spec=fitted, solved=sol.solved, route=[s.data() for s in sol.route],
                       problems=problems, notes=[*notes, *repairs], features=feats,
                       difficulty=score, retried=retried, by=by)  # fmt: skip


# --- what the SDK loads ------------------------------------------------------------------------


def level_data(record: LevelRecord, design: GameDesign) -> dict:
    """One level as the SDK loads it (`levels.js`): the map, its tiles' entities, the things
    with their patrols, the start and the bot's route. Units are tiles."""
    spec = record.spec
    grid = parse(spec, design)
    tiles = {e.char: {"kind": e.kind, "entity": e.entity} for e in spec.legend if e.kind in TILE_KINDS}
    rows = ["".join(ch if ch in tiles else " " for ch in row) for row in spec.rows]
    things = [{"id": t.id, "kind": t.kind, "entity": t.entity, "col": t.col, "row": t.row,
               "cells": [list(c) for c in t.cells], "needs": t.needs, "opened_by": t.opened_by,
               "holds": t.holds, "gives": t.gives,
               **({"patrol": list(t.patrol), "tall": t.tall} if t.patrol else {})}
              for t in grid.things]  # fmt: skip
    player = next((e.entity for e in spec.legend if e.kind == "start"),
                  next(e.id for e in design.entities if e.role == "player"))  # fmt: skip
    return {"n": spec.n, "name": spec.name, "cols": grid.cols, "rows": grid.rows, "map": rows,
            "tiles": tiles, "things": things, "start": list(grid.start), "player": player,
            "route": record.route, "exclusive": spec.exclusive}  # fmt: skip


def levels_script(records: list[LevelRecord], design: GameDesign, plan: PlanRecord,
                  body: tuple[float, float]) -> dict:  # fmt: skip
    """`window.SF_LEVELS`: the movement the SDK's player uses and every level."""
    m = motion_for(plan, body)
    names = plan_abilities(plan)
    held = sorted(names[k] for k in start_abilities(plan) if k in names)
    return {"movement": m.sdk(), "body": list(body), "abilities": held,
            "levels": [level_data(r, design) for r in records]}  # fmt: skip


# --- prompts -----------------------------------------------------------------------------------


EXAMPLE = """Example of the format (a different game, 32 columns, 8 rows):
legend: '#' solid ground_tile, '=' one_way ground_tile, '^' hazard spikes, '@' start hero,
'c' crate box (holds 'coin', gives ''), 'k' crate box (holds 'key_orb', gives 'Key Power'),
'B' breakable stone_block (needs 'Key Power', opened_by 'touch'), 'e' enemy beetle (needs '',
opened_by 'never'), 'o' pickup coin, 'F' checkpoint flag, 'D' exit door
rows:
"                                "
"                                "
"              ===        o      "
"                               B"
"     o                  e      B"
"@  c   k  #   F   ^^      ===  D"
"########  ###########  #########"
"########  ###########  #########"
"""

GUIDE = """* rows: the map, top row first, every row the same length. ' ' or '.' is empty. The
  bottom rows are ground; a gap in the ground is a pit (falling in sends the player back to the
  last checkpoint). The screen shows about 20 columns and 11 rows and scrolls with the player.
* legend: one character per kind of cell. kind solid (ground, platforms, walls), one_way (a
  platform the player jumps up through), hazard (spikes: touching sends the player back),
  breakable (blocks that open: needs = the ability, opened_by = touch or dash), crate (opened by
  touch or dash; holds = the entity it drops, gives = the ability that drop gives, '' for coins),
  pickup (gives an ability, or '' for coins and such), enemy (patrols its floor up to 3 tiles each
  way; needs and opened_by say how it is beaten, opened_by 'never' for one to avoid), checkpoint,
  exit (exactly one, at the right end), start (exactly one, at the left end, entity = the player).
  entity is a design entity id. A thing that rests on a floor sits on the row above it.
* Code checks every move with the movement numbers below: floors more than the jump's reach up,
  or gaps wider than a jump (or a dash once it is there), cannot be crossed. Keep the main path
  comfortable (one tile less than the limits) except where the jump is the challenge. Every
  floor, pickup and crate must be reachable.
* Teach the level's new idea where failing costs little, then test it. A thing that needs an
  ability must have that ability's crate or pickup reachable before it, and when a pickup gives
  the level's new ability, the way to the exit must need it (put the lock in the way). Put a
  checkpoint between challenges; every stretch between checkpoints has a challenge (a gap, a
  climb, a hazard, an enemy or a lock). Each level is harder than the one before.
* challenges: each challenge of the plan for this level, with the column where it starts.
* events: the design's check events this level makes happen through the game's own mechanics
  (names exactly as in the design; not the SDK's level_start, level_complete, checkpoint,
  respawn, jump, dash or win, which are checked over all levels).
* A challenge that asks for a dash needs a gap wider than a jump (see the movement numbers).
* exclusive: abilities that replace each other (the plan's mechanics say so), else [].
The level is original: research teaches patterns, never copy a game's levels."""


def level_brief(n: int, design: GameDesign, plan: PlanRecord) -> str:
    p, k, mv = plan.plan, plan.kinematics, plan.plan.movement
    lp = p.levels[n - 1] if n <= len(p.levels) else None
    m = motion_for(plan, player_body(design))
    gap, dash = gap_limits(m) if k else (0, 0)
    jump = (f"a jump reaches floors up to {m.max_rise} tiles higher and clears gaps up to {gap} "
            "tiles" + (f"; a gap of {gap + 1}-{dash} tiles needs a dash in the air" if dash else "")
            + " (code's model of the real movement)" if k else "no jump")  # fmt: skip
    ents = "\n".join(f"- `{e.id}` ({e.role}, {e.width}x{e.height} px): {e.look}; {e.behaviour}"
                     for e in design.entities)  # fmt: skip
    this = (f"Level {n} of {len(p.levels)}: {lp.name} (difficulty {lp.difficulty}/10). Teaches: "
            f"{lp.teaching_goal}. New idea: {lp.new_idea}. Challenges: {', '.join(lp.challenges) or '-'}. "
            f"Needs: {', '.join(lp.abilities) or '-'}." if lp else f"Level {n}.")  # fmt: skip
    return (
        f"{this}\n\nMovement (tiles of 16 px): run {mv.move_speed:g} tiles/s; {jump}.\n"
        "Abilities: " + ("; ".join(f"{a.name}: {a.does} (from {a.unlocked_by})" for a in p.abilities) or "-")
        + "\nMechanics: " + ("; ".join(f"{x.name}: {x.what}" for x in p.mechanics) or "-")
        + "\nChallenge vocabulary: " + ("; ".join(f"{c.name}: {c.tests}" for c in p.challenges) or "-")
        + "\nDesign check events (at least that many over all levels): "
        + ", ".join(f"{c.event} ({c.min_count})" for c in design.checks)
        + f"\nEntities:\n{ents}"
    )  # fmt: skip


def earlier_text(records: list[LevelRecord]) -> str:
    return "\n\n".join(
        f"Level {r.spec.n} {r.spec.name} (code's difficulty {r.difficulty:g}):\n"
        + "\n".join(f"'{e.char}' {e.kind} {e.entity}" + (f" needs {e.needs}" if e.needs else "")
                    + (f" holds {e.holds}" if e.holds else "") + (f" gives {e.gives}" if e.gives else "")
                    for e in r.spec.legend)
        + "\n" + "\n".join(f'"{row}"' for row in r.spec.rows) for r in records[-2:])  # fmt: skip


def level_prompt(n: int, design: GameDesign, plan: PlanRecord, earlier: list[LevelRecord],
                 context: str = "") -> str:  # fmt: skip
    return (
        f"Design level {n} of this platformer as data. Code checks it with the player's real "
        "movement, a bot plays it along the route code finds, and the Programmer writes the "
        "mechanics once for every level.\n" + GUIDE + "\n\n" + EXAMPLE + "\n"
        + level_brief(n, design, plan)
        + (f"\n\nEarlier levels (this one must be harder):\n{earlier_text(earlier)}" if earlier else "")
        + (f"\n\nThe game's record (OKF bundle):\n{context.strip()}" if context.strip() else "")
    )  # fmt: skip


def level_retry_prompt(prompt: str, problems: list[str]) -> str:
    return (prompt + "\n\nYour previous level had these problems (found by code with the real "
            "movement); answer the whole level again with them fixed:\n"
            + "\n".join(f"- {p}" for p in problems))  # fmt: skip


def level_revision_prompt(record: LevelRecord, said: str, design: GameDesign, plan: PlanRecord,
                          earlier: list[LevelRecord]) -> str:  # fmt: skip
    return (
        f"Revise level {record.spec.n} for the human's feedback. Change only what the feedback "
        "asks for and keep everything else that works; the other levels stay as they are.\n"
        + GUIDE + "\n\n" + level_brief(record.spec.n, design, plan)
        + (f"\n\nEarlier levels:\n{earlier_text(earlier)}" if earlier else "")
        + f"\n\nThe human said:\n{said.strip()}\n\nCurrent level:\n{record.spec.model_dump_json()}"
    )  # fmt: skip


# --- shown at G4 and in the bundle --------------------------------------------------------------


def level_markdown(record: LevelRecord) -> str:
    s = record.spec
    legend = "\n".join(
        f"| `{e.char}` | {e.kind} | `{e.entity}` | {e.needs or '-'} | {e.opened_by} | "
        f"{e.holds or '-'} | {e.gives or '-'} |" for e in s.legend)  # fmt: skip
    route = ", ".join(f"{st['do']}" + (f" {st['thing']}" if st.get("thing") else "")
                      for st in record.route if st["do"] != "walk")  # fmt: skip
    return (
        f"# Level {s.n}: {s.name}\n\n{s.notes}\n\n"
        f"**Code's check:** {'the exit can be reached' if record.solved else 'NOT SOLVABLE'}; "
        f"difficulty {record.difficulty:g}; {len(s.rows[0]) if s.rows else 0}x{len(s.rows)} tiles\n\n"
        "| char | kind | entity | needs | opened by | holds | gives |\n|---|---|---|---|---|---|---|\n"
        f"{legend}\n\n```\n" + "\n".join(s.rows) + "\n```\n\n"
        "**Challenges:** " + (", ".join(f"{c.name} (column {c.column})" for c in s.challenges) or "-")
        + f"\n\n**Events:** {', '.join(s.events) or '-'}"
        + f"\n\n**Bot route:** {route or '-'}"
        + ("\n\n## Problems code found\n" + "\n".join(f"* {p}" for p in record.problems)
           if record.problems else "")
        + "".join(f"\n> repaired: {n}" for n in record.notes) + "\n"
    )  # fmt: skip
