"""Can a platformer level be played with the player's real movement? (L2.3)

Code proves it by searching the level the way the SDK's player moves: the same gravity, take-off
speed, run speed and dash as `sflevel.js`, stepped at Phaser's fixed 60 Hz physics with the
bot's inputs changing once per playtest frame. Units are tiles and seconds; rows count down.

* `fly` follows one planned move (a jump, a walk off a ledge, a dash) through the grid and says
  where the player lands, or None when the move hits a wall, a ceiling, a hazard, an enemy's
  patrol or falls out of the level. The model jumps a little lower than the SDK (CLEARANCE vs
  the SDK's 6 px), and a landing needs a quarter tile of the body over the platform.
* `World`: the grid with some things opened (crates dissolved, walls broken, enemies beaten);
  its `runs` are the floors the player can stand and walk on, and `link` finds a move between
  two runs.
* `solve`: a search over what the player holds (abilities from pickups; some replace each
  other) and what is opened, greedily opening whatever it can reach and come back from. It
  returns the route the bot follows in the playtest, or why the exit cannot be reached.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field, replace
from functools import lru_cache

DT = 1 / 60  # Phaser Arcade's fixed physics step
CLEARANCE_SDK = 6 / 16  # the SDK's jump peaks this far (tiles) above the plan's jump height
CLEARANCE = 4 / 16  # the model's jump: 2 px under the SDK's, a safety margin
LAND = 0.25  # tiles of the body that must be over a platform for a landing
EDGE = 0.15  # tiles of the body left on the ledge at an edge take-off
MAX_FALL = 40.0  # tiles/s; the SDK caps falls at this too (no tunnelling through tiles)
DASH_STEPS = 9  # physics steps a dash lasts (0.15 s)
MAX_AIR = 3.0  # s; longer flights are not considered
LOCKS = ("crate", "breakable", "enemy", "gate")  # closed: in the way; a gate opens by its lever
EPS = 1e-9


def take_off_speed(gravity: float, height: float) -> float:
    """Take-off speed whose jump peaks exactly at `height` under semi-implicit Euler at DT
    (Arcade adds gravity to the velocity, then moves): v = g dt/2 + sqrt((g dt/2)^2 + 2 g h)."""
    half = gravity * DT / 2
    return half + math.sqrt(half * half + 2 * gravity * height)


def key(text: str) -> str:
    """Ability names compared loosely (case, spaces, dashes and underscores do not matter)."""
    return " ".join("".join(ch if ch.isalnum() else " " for ch in str(text).casefold()).split())


@dataclass(frozen=True)
class Motion:
    """The player's movement in tiles and seconds, and its body."""

    speed: float  # run speed
    gravity: float  # 8 h / T^2 from the plan
    jump: float  # the plan's jump height (0 = no jump)
    dash: float = 0.0  # dash distance (0 = no dash)
    cooldown: float = 0.0
    coyote: float = 0.0
    body_w: float = 0.75
    body_h: float = 0.75
    fps: int = 30  # the bot decides once per frame; physics steps twice per frame at 30 fps

    @property
    def v0(self) -> float:
        return take_off_speed(self.gravity, self.jump + CLEARANCE) if self.jump > 0 else 0.0

    @property
    def tall(self) -> int:
        return max(1, math.ceil(self.body_h - 1e-6))

    @property
    def sub(self) -> int:
        return max(1, round(1 / (self.fps * DT)))

    @property
    def dash_speed(self) -> float:
        return self.dash / (DASH_STEPS * DT)

    @property
    def max_rise(self) -> int:
        return int(self.jump + CLEARANCE + EPS) if self.jump > 0 else 0

    def rise_time(self, rise: float, up: bool) -> float | None:
        """Seconds after take-off when the feet are `rise` tiles up, rising (up) or
        falling; None when the jump never gets that high."""
        if self.v0 <= 0:
            return None
        v = self.v0 - self.gravity * DT / 2  # the stepped jump follows this parabola exactly
        disc = v * v - 2 * self.gravity * rise
        if disc < 0:
            return None
        return (v - math.sqrt(disc) if up else v + math.sqrt(disc)) / self.gravity

    def sdk(self, tile_px: int = 16) -> dict:
        """The numbers the SDK's controller uses (tiles; the SDK multiplies by its tile size)."""
        return {
            "speed": round(self.speed, 4),
            "gravity": round(self.gravity, 4),
            "jump": round(self.jump + CLEARANCE_SDK, 4) if self.jump > 0 else 0,
            "dash": round(self.dash, 4),
            "dash_steps": DASH_STEPS,
            "cooldown": round(self.cooldown, 4),
            "coyote": round(self.coyote, 4),
            "max_fall": MAX_FALL,
        }


# --- the grid ----------------------------------------------------------------------------------


Cell = tuple[int, int]


@dataclass
class Thing:
    """Something placed in a level (not a tile): crates, breakable walls, pickups, enemies,
    checkpoints, the exit and the player's start."""

    id: str
    kind: str
    entity: str
    cells: tuple[Cell, ...]
    needs: str = ""
    opened_by: str = "never"
    holds: str = ""
    gives: str = ""
    patrol: tuple[int, int] | None = None  # enemy: the columns it walks between
    tall: int = 1  # enemy: rows it spans

    @property
    def col(self) -> int:
        return min(c for c, _ in self.cells)

    @property
    def row(self) -> int:
        return max(r for _, r in self.cells)

    def zone(self) -> set[Cell]:
        """Cells an enemy may be in while it patrols (hazard while it lives)."""
        if self.kind != "enemy":
            return set(self.cells)
        a, b = self.patrol or (self.col, self.col)
        return {(c, self.row - k) for c in range(a, b + 1) for k in range(self.tall)}


@dataclass
class Grid:
    cols: int
    rows: int
    tiles: dict[Cell, str]  # "solid" | "one_way" | "hazard"
    things: list[Thing]
    start: Cell

    def thing(self, ident: str) -> Thing:
        return next(t for t in self.things if t.id == ident)


@dataclass
class Run:
    """A floor: cells (c0..c1, row) the player can stand on and walk between."""

    i: int
    row: int
    c0: int
    c1: int

    def has(self, cell: Cell) -> bool:
        return cell[1] == self.row and self.c0 <= cell[0] <= self.c1


@dataclass(frozen=True)
class Move:
    """One planned move in the air, as the bot plays it. `x0` is where the body's centre is
    when it starts (a jump from standing; for a fall, just past the ledge)."""

    kind: str  # "jump" | "fall"
    x0: float
    vx: float  # horizontal speed in the air (after `wait`)
    side: int  # -1 left, 1 right, 0 straight up
    wait: float = 0.0  # seconds with no horizontal speed first (rise beside a wall)
    dash: float = -1.0  # seconds after the start the dash is pressed; -1: none
    after: float = 0.0  # horizontal speed after the dash

    def data(self) -> dict:
        return {"do": self.kind, "x0": round(self.x0, 4), "vx": round(self.vx, 4),
                "side": self.side, "wait": round(self.wait, 4), "dash": round(self.dash, 4),
                "after": round(self.after, 4)}  # fmt: skip


@dataclass
class Flight:
    cell: Cell  # where it lands (the standing cell)
    x: float  # the body's centre at landing
    time: float
    swept: frozenset[Cell]
    box: tuple[int, int, int, int]  # c0, r0, c1, r1 of everything the flight came near


class World:
    """The grid with the things in `opened` gone (opened crates and walls, beaten enemies)."""

    def __init__(self, grid: Grid, opened: frozenset[str], m: Motion):
        self.grid, self.opened, self.m = grid, opened, m
        self.cols, self.rows = grid.cols, grid.rows
        self.solid = {c for c, k in grid.tiles.items() if k == "solid"}
        self.oneway = {c for c, k in grid.tiles.items() if k == "one_way"}
        self.hazard = {c for c, k in grid.tiles.items() if k == "hazard"}
        self.support = self.solid | self.oneway
        self.owner: dict[Cell, str] = {}  # cells of things still closed -> thing id
        for t in grid.things:
            if t.kind not in LOCKS or t.id in opened:
                continue
            cells = t.zone()
            if t.kind == "enemy":
                self.hazard |= cells
            else:
                self.solid |= cells
                if t.kind in ("breakable", "gate"):  # stood on; a crate dissolves on touch
                    self.support |= cells
            self.owner.update(dict.fromkeys(cells, t.id))
        self.runs = self.find_runs()
        self.run_at = {(c, r.row): r for r in self.runs for c in range(r.c0, r.c1 + 1)}
        self.links: dict[int, list[Link]] | None = None

    def blocked(self, c: int, r: int) -> bool:
        return c < 0 or c >= self.cols or (c, r) in self.solid or (c, r) in self.hazard

    def standable(self, c: int, r: int) -> bool:
        if not (0 <= c < self.cols and 0 <= r < self.rows - 1) or (c, r + 1) not in self.support:
            return False
        return not any(self.blocked(c, r - k) for k in range(self.m.tall))

    def find_runs(self) -> list[Run]:
        out = []
        for r in range(self.rows):
            c = 0
            while c < self.cols:
                if self.standable(c, r):
                    c0 = c
                    while c + 1 < self.cols and self.standable(c + 1, r):
                        c += 1
                    out.append(Run(len(out), r, c0, c))
                c += 1
        return out

    def state(self, box: tuple[int, int, int, int]) -> tuple:
        """Which closed things lie in a box: a flight through it depends on them."""
        c0, r0, c1, r1 = box
        return tuple(sorted({o for (c, r), o in self.owner.items()
                             if c0 <= c <= c1 and r0 <= r <= r1}))  # fmt: skip


# --- flights -----------------------------------------------------------------------------------


def body_cells(x: float, y: float, w: float, h: float):
    for c in range(math.floor(x - w / 2 + EPS), math.floor(x + w / 2 - EPS) + 1):
        for r in range(math.floor(y - h + EPS), math.floor(y - EPS) + 1):
            yield c, r


def fly(world: World, row: int, move: Move) -> Flight | None:
    """Follow `move` from standing on row `row` (feet at y = row + 1)."""
    m = world.m
    w, h, g = m.body_w, m.body_h, m.gravity
    x, y = move.x0, row + 1.0
    vy = -m.v0 if move.kind == "jump" else 0.0
    dashing, dashed = 0, False
    swept: set[Cell] = set()
    lo_x, hi_x, lo_y, hi_y = x, x, y, y

    def box() -> tuple[int, int, int, int]:
        return (math.floor(lo_x - w / 2) - 1, math.floor(lo_y - h) - 1,
                math.floor(hi_x + w / 2) + 1, math.floor(hi_y) + 1)  # fmt: skip

    for frame in range(int(MAX_AIR * m.fps)):
        t = frame / m.fps
        vx = 0.0 if t < move.wait - EPS else move.vx
        if move.dash >= 0 and not dashed and t >= move.dash - EPS:
            dashed, dashing, vy = True, DASH_STEPS, 0.0
        if dashed and not dashing:
            vx = move.after
        for _ in range(m.sub):
            if dashing:
                sx, vy = move.side * m.dash_speed, 0.0
            else:
                sx, vy = vx, min(vy + g * DT, MAX_FALL)
            py = y
            x += sx * DT
            y += vy * DT
            if dashing:
                dashing -= 1
            lo_x, hi_x, lo_y, hi_y = min(lo_x, x), max(hi_x, x), min(lo_y, y), max(hi_y, y)
            if x - w / 2 < 0 or x + w / 2 > world.cols or y - h > world.rows:
                return None  # the side of the level, or a pit
            if vy > 0:
                for k in range(math.ceil(py - EPS), math.floor(y + EPS) + 1):
                    if k > py - EPS:  # the feet crossed the top of row k, coming down
                        landed = land(world, x, k, w)
                        if landed is not None:
                            cell = (landed, k - 1)
                            if any(world.blocked(c, r) for c, r in body_cells(x, k, w, h)):
                                return None
                            swept.update(body_cells(x, k, w, h))
                            return Flight(cell, x, frame / m.fps, frozenset(swept), box())
                        if landed is None and touches(world, x, k, w):
                            return None  # only the edge of a platform: too close to call
            for c, r in body_cells(x, y, w, h):
                if world.blocked(c, r):
                    return None
                swept.add((c, r))
    return None


def touches(world: World, x: float, k: int, w: float) -> bool:
    return any((c, k) in world.support
               for c in range(math.floor(x - w / 2 + EPS), math.floor(x + w / 2 - EPS) + 1))  # fmt: skip


def land(world: World, x: float, k: int, w: float) -> int | None:
    """The column the body lands on when its feet reach the top of row k: the support with
    the most overlap, if it is at least LAND; None otherwise."""
    best, col = 0.0, None
    for c in range(math.floor(x - w / 2 + EPS), math.floor(x + w / 2 - EPS) + 1):
        if (c, k) in world.support:
            overlap = min(x + w / 2, c + 1) - max(x - w / 2, c)
            if overlap > best:
                best, col = overlap, c
    if col is None or best < min(LAND, w) - EPS:
        return None
    return col if world.standable(col, k - 1) else None


class Flights:
    """`fly` with a memory: a flight is reused in another world when the closed things near
    it are the same."""

    def __init__(self):
        self.seen: dict[tuple, list[tuple[tuple, Flight | None]]] = {}

    def __call__(self, world: World, row: int, move: Move) -> Flight | None:
        for state, result in self.seen.get((row, move), []):
            if world.state(state[0]) == state[1]:
                return result
        result = fly(world, row, move)
        span = result.box if result else wide_box(world, row, move)
        self.seen.setdefault((row, move), []).append(((span, world.state(span)), result))
        return result


def wide_box(world: World, row: int, move: Move) -> tuple[int, int, int, int]:
    """Everything a failed flight could have come near."""
    m = world.m
    reach = m.speed * MAX_AIR + m.dash + 2
    lo = move.x0 - reach if move.side <= 0 else move.x0 - 2
    hi = move.x0 + reach if move.side >= 0 else move.x0 + 2
    return (math.floor(lo), row - m.max_rise - m.tall - 2, math.ceil(hi), world.rows)


# --- links between floors ----------------------------------------------------------------------


@dataclass
class Link:
    src: int
    dst: int
    move: Move
    flight: Flight


def moves(m: Motion, s: Run, t: Run, a: int, b: int):
    """Moves worth trying from cell a of run s to cell b of run t, simplest first."""
    side = (b > a) - (b < a)
    rise = s.row - t.row
    end = (side > 0 and a == s.c1) or (side < 0 and a == s.c0)
    xa, xb = a + 0.5, b + 0.5
    starts = [xa] + ([a + 0.5 + side * (0.5 + m.body_w / 2 - EDGE)] if end else [])
    ends = [xb, b + 0.5 - side * 0.3] if side else [xb]
    if m.v0:
        down = m.rise_time(rise, up=False)
        if down:
            for x0 in starts:
                for x1 in ends:
                    vx = (x1 - x0) / down
                    if abs(vx) <= m.speed + EPS:
                        yield Move("jump", x0, vx, side)
            # Rise first, then move: over the top of a wall beside the take-off, or over
            # spikes or an enemy right next to it.
            for height in ((rise + 0.3, rise + 0.05) if rise > 0 else (1.05,)):
                up = m.rise_time(height, up=True) if side else None
                if up is None:
                    continue
                wait = math.ceil(up * m.fps - EPS) / m.fps
                if wait < down:
                    vx = (xb - xa) / (down - wait)
                    if abs(vx) > m.speed:
                        vx = side * m.speed
                    yield Move("jump", xa, vx, side, wait=wait)
    if rise < 0 and side and end:
        x0 = (s.c1 + 1 if side > 0 else s.c0) + side * (m.body_w / 2 + 0.02)
        fall = math.sqrt(2 * -rise / m.gravity)
        vx = (xb - x0) / fall
        if abs(vx) < 1:
            vx = side * 1.0
        if abs(vx) <= m.speed + EPS:
            yield Move("fall", x0, vx, side)
    if m.dash > 0 and side:
        x0 = starts[-1]
        if m.v0:
            apex = math.ceil(m.v0 / m.gravity * m.fps) / m.fps
            for at in (apex, 0.0):
                for after in (1.0, 0.5, 0.0):
                    yield Move("jump", x0, side * m.speed, side, dash=at, after=side * m.speed * after)
        else:
            yield Move("fall", x0, side * m.speed, side, dash=0.0, after=side * m.speed)


def reachable_pair(m: Motion, s: Run, t: Run) -> bool:
    """A cheap test before trying moves: the height and the distance can be covered."""
    rise = s.row - t.row
    if rise > m.max_rise:
        return False
    if t.c0 > s.c1:
        gap = t.c0 - s.c1 - 1
    elif t.c1 < s.c0:
        gap = s.c0 - t.c1 - 1
    else:
        return True
    air = m.rise_time(rise, up=False) if m.v0 else None
    if air is None:
        air = math.sqrt(2 * max(0, -rise) / m.gravity) if rise < 0 else 0.0
    return gap <= m.speed * air + m.dash + 1


def candidates(s: Run, t: Run) -> list[tuple[int, int]]:
    if t.c0 > s.c1:
        froms, tos = range(s.c1, max(s.c0, s.c1 - 3) - 1, -1), range(t.c0, min(t.c1, t.c0 + 2) + 1)
    elif t.c1 < s.c0:
        froms, tos = range(s.c0, min(s.c1, s.c0 + 3) + 1), range(t.c1, max(t.c0, t.c1 - 2) - 1, -1)
    else:
        froms = [c for c in (t.c0 - 1, t.c1 + 1, t.c0 - 2, t.c1 + 2, t.c0 - 3, t.c1 + 3, t.c0, t.c1)
                 if s.c0 <= c <= s.c1]  # fmt: skip
        tos = [c for c in (t.c0, t.c1, t.c0 + 1, t.c1 - 1) if t.c0 <= c <= t.c1]
        # A ledge over a wider floor: off its own ends, down beside it (the way back from a
        # lever on an upper floor; before, a floating platform was a one-way trip up).
        froms += [s.c1, s.c0]
        tos += [c for c in (s.c1 + 1, s.c0 - 1, s.c1 + 2, s.c0 - 2) if t.c0 <= c <= t.c1]
    pairs = []
    for a in dict.fromkeys(froms):
        for b in dict.fromkeys(tos):
            pairs.append((a, b))
    return pairs


def link(world: World, flights: Flights, s: Run, t: Run) -> Link | None:
    if not reachable_pair(world.m, s, t):
        return None
    for a, b in candidates(s, t):
        for move in moves(world.m, s, t, a, b):
            f = flights(world, s.row, move)
            if f is not None and t.has(f.cell):
                return Link(s.i, t.i, move, f)
    return None


def links(world: World, flights: Flights) -> dict[int, list[Link]]:
    if world.links is None:
        world.links = {r.i: [] for r in world.runs}
        for s in world.runs:
            for t in world.runs:
                if s.i != t.i and (found := link(world, flights, s, t)):
                    world.links[s.i].append(found)
    return world.links


# --- the search --------------------------------------------------------------------------------


@dataclass
class Action:
    do: str  # "touch" | "dash" | "take" | "exit"
    thing: str
    at: Cell  # where the player stands to do it
    hop: Move | None = None  # take/exit by jumping in place (a pickup in the air)


@dataclass
class Step:
    """One step of the route the bot plays."""

    do: str  # walk | jump | fall | touch | dash | collect | exit
    x: float = 0.0  # walk: where to; touch/dash: where to stand
    move: Move | None = None
    cell: Cell | None = None  # where the player stands after it
    thing: str = ""
    drop: bool = False  # collect: the thing a crate dropped

    def data(self) -> dict:
        out = {"do": self.do}
        if self.move:
            out |= self.move.data() | {"do": self.do}
        else:
            out["x"] = round(self.x, 4)
        if self.cell:
            out["cell"] = list(self.cell)
        if self.thing:
            out["thing"] = self.thing
        if self.drop:
            out["drop"] = True
        return out


@dataclass
class Solution:
    solved: bool
    route: list[Step]
    reached: set[Cell]  # every standing cell any explored state could reach
    actions: list[Action]
    problems: list[str] = field(default_factory=list)  # why the exit could not be reached
    states: int = 0


@dataclass(frozen=True)
class State:
    cell: Cell
    held: frozenset[str]
    opened: frozenset[str]
    taken: frozenset[str]


class Search:
    def __init__(self, grid: Grid, m: Motion, held: set[str], exclusive: set[str]):
        self.grid, self.m = grid, m
        self.start_held = frozenset(key(a) for a in held)
        self.exclusive = {key(a) for a in exclusive}
        self.flights = Flights()
        self.worlds: dict[frozenset[str], World] = {}

    def world(self, opened: frozenset[str]) -> World:
        if opened not in self.worlds:
            self.worlds[opened] = World(self.grid, opened, self.m)
        return self.worlds[opened]

    def reach(self, world: World, cell: Cell) -> dict[int, tuple[int, Link] | None]:
        """Runs reachable from the run at `cell`: run -> (previous run, link) (BFS tree)."""
        first = world.run_at.get(cell)
        if first is None:
            return {}
        graph = links(world, self.flights)
        tree: dict[int, tuple[int, Link] | None] = {first.i: None}
        queue = deque([first.i])
        while queue:
            i = queue.popleft()
            for ln in graph[i]:
                if ln.dst not in tree:
                    tree[ln.dst] = (i, ln)
                    queue.append(ln.dst)
        return tree

    def stands(self, world: World, tree, cell: Cell) -> bool:
        run = world.run_at.get(cell)
        return run is not None and run.i in tree

    # where the player stands to deal with a thing

    def touch_cells(self, world: World, t: Thing) -> list[Cell]:
        tall = self.m.tall
        if t.kind == "enemy":
            a, b = t.patrol or (t.col, t.col)
            spots = [(a - 1, t.row), (b + 1, t.row)]
        else:
            own = set(t.cells)
            spots = [(c + d, r2) for c, r in t.cells for d in (-1, 1)
                     for r2 in range(r, r + tall)
                     if (c + d, r2) not in own and (c + d, r2 + 1) not in own]  # fmt: skip
        return [s for s in dict.fromkeys(spots) if world.standable(*s)]

    def visit(self, world: World, tree, t: Thing) -> tuple[Cell, Move | None] | None:
        """Where the player gets a pickup or reaches the exit: standing in its cell, or a jump
        in place under it."""
        tall = self.m.tall
        for c, r in t.cells:
            for r2 in range(r, r + tall):
                if self.stands(world, tree, (c, r2)):
                    return (c, r2), None
        for c, r in t.cells:
            for dc in (0, -1, 1):
                for r2 in range(r + tall, min(world.rows - 1, r + tall + self.m.max_rise + 1)):
                    spot = (c + dc, r2)
                    if not self.stands(world, tree, spot):
                        continue
                    hop = Move("jump", spot[0] + 0.5 - dc * 0.25, 0.0, 0)
                    f = self.flights(world, r2, hop)
                    if f is not None and (c, r) in f.swept and f.cell == spot:
                        return spot, hop
        return None

    def available(self, state: State) -> list[Thing]:
        """Pickups that give an ability and can be taken now (placed ones, and what opened
        crates dropped)."""
        out = []
        for t in self.grid.things:
            if t.kind in ("pickup", "lever") and t.gives and t.id not in state.taken:
                out.append(t)
            if t.kind == "crate" and t.gives and t.id in state.opened and t.id + ">" not in state.taken:
                out.append(Thing(t.id + ">", "pickup", t.holds, t.cells, gives=t.gives))
        return out

    def gates(self, t: Thing) -> frozenset[str]:
        """The gates a lever opens when it is pulled (anywhere in the level)."""
        if t.kind != "lever":
            return frozenset()
        return frozenset(g.id for g in self.grid.things
                         if g.kind == "gate" and key(g.needs) == key(t.gives))  # fmt: skip

    def can_open(self, t: Thing, held: frozenset[str]) -> bool:
        return (t.kind in LOCKS and t.opened_by in {"touch", "dash"}
                and (not t.needs or key(t.needs) in held))  # fmt: skip

    def closure(self, state: State) -> tuple[State, list[Action]]:
        """Open everything reachable that the player can open and come back from."""
        acts = []
        while True:
            world = self.world(state.opened)
            tree = self.reach(world, state.cell)
            options = []
            for t in self.grid.things:
                if t.id in state.opened or not self.can_open(t, state.held):
                    continue
                spots = [s for s in self.touch_cells(world, t) if self.stands(world, tree, s)]
                if spots:
                    spot = min(spots, key=lambda s: abs(s[0] - state.cell[0]) + abs(s[1] - state.cell[1]))
                    options.append((abs(spot[0] - state.cell[0]), t, spot))
            for _, t, spot in sorted(options, key=lambda o: (o[0], o[1].id)):
                opened = state.opened | {t.id}
                after = self.world(opened)
                back = self.reach(after, spot)
                home = after.run_at.get(state.cell)
                if home is not None and home.i in back:  # the player can come back
                    acts.append(Action("dash" if t.opened_by == "dash" else "touch", t.id, spot))
                    state = State(spot, state.held, opened, state.taken)
                    break
            else:
                return state, acts

    def branches(self, state: State):
        world = self.world(state.opened)
        tree = self.reach(world, state.cell)
        for p in self.available(state):
            spot = self.visit(world, tree, p)
            if spot:
                gives = key(p.gives)
                held = (state.held - self.exclusive if gives in self.exclusive else state.held) | {gives}
                yield (State(spot[0], frozenset(held), state.opened | self.gates(p),
                             state.taken | {p.id}), Action("take", p.id, spot[0], spot[1]))  # fmt: skip
        for t in self.grid.things:
            if t.id in state.opened or not self.can_open(t, state.held):
                continue
            for spot in self.touch_cells(world, t):
                if self.stands(world, tree, spot):
                    yield (State(spot, state.held, state.opened | {t.id}, state.taken),
                           Action("dash" if t.opened_by == "dash" else "touch", t.id, spot))
                    break

    def run(self, limit: int = 400) -> Solution:
        exit_ = next((t for t in self.grid.things if t.kind == "exit"), None)
        start = State(self.grid.start, self.start_held, frozenset(), frozenset())
        if exit_ is None or self.world(frozenset()).run_at.get(self.grid.start) is None:
            why = "the level has no exit" if exit_ is None else "the start is not on a floor"
            return Solution(False, [], set(), [], [why])
        nodes: list[tuple[State, int, list[Action]]] = [(start, -1, [])]
        seen = {start}
        queue = deque([0])
        reached: set[Cell] = set()
        frontier: dict[str, Thing] = {}
        while queue and len(nodes) <= limit:
            i = queue.popleft()
            state, acts = self.closure(nodes[i][0])
            world = self.world(state.opened)
            tree = self.reach(world, state.cell)
            reached |= {(c, world.runs[r].row) for r in tree
                        for c in range(world.runs[r].c0, world.runs[r].c1 + 1)}  # fmt: skip
            for t in self.grid.things:
                if t.kind in LOCKS and t.id not in state.opened and any(
                        self.stands(world, tree, s) for s in self.touch_cells(world, t)):  # fmt: skip
                    frontier[t.id] = t
            goal = self.visit(world, tree, exit_)
            if goal:
                actions = self.path(i, nodes) + acts + [Action("exit", exit_.id, goal[0], goal[1])]
                route = self.route(actions)
                return Solution(True, route, reached, actions, states=len(nodes))
            for nxt, act in self.branches(state):
                if nxt not in seen:
                    seen.add(nxt)
                    nodes.append((nxt, i, acts + [act]))
                    queue.append(len(nodes) - 1)
        held = set().union(*(s.held for s, _, _ in nodes)) | self.start_held
        return Solution(False, [], reached, [], self.why(reached, frontier, held, exit_),
                        states=len(nodes))  # fmt: skip

    def path(self, i: int, nodes) -> list[Action]:
        out = []
        while i > 0:
            _, parent, acts = nodes[i]
            out = acts + out
            i = parent
        return out

    def why(self, reached: set[Cell], frontier: dict[str, Thing], held: set[str],
            exit_: Thing) -> list[str]:  # fmt: skip
        out = []
        for t in frontier.values():
            if t.kind == "gate":
                levers = [x for x in self.grid.things if x.kind == "lever" and key(x.gives) == key(t.needs)]
                out.append(f"a gate ({t.entity or 'gate'}) at column {t.col} stays shut: "
                           + (f"its lever at column {levers[0].col}, row {levers[0].row} cannot be "
                              "reached before it" if levers else "no lever opens it"))  # fmt: skip
                continue
            if t.opened_by == "never":  # live 2026-11-03: this was not said, and the retry failed
                if t.kind in ("enemy", "breakable"):
                    what = "an enemy" if t.kind == "enemy" else "a breakable wall"
                    out.append(f"{what} ({t.entity}) at column {t.col} blocks the way and nothing "
                               "beats it (opened_by 'never'): give it something that beats it, or "
                               "leave room to jump over it")  # fmt: skip
                continue
            if t.needs and key(t.needs) not in held:
                what = {"enemy": "an enemy", "breakable": "a breakable wall", "crate": "a crate"}[t.kind]
                out.append(f"{what} ({t.entity}) at column {t.col} blocks the way and needs "
                           f"{t.needs}, but no {t.needs} can be picked up before it")  # fmt: skip
        far = max(reached, key=lambda c: c[0], default=self.grid.start)
        out.append(f"the exit at column {exit_.col}, row {exit_.row} cannot be reached: the player "
                   f"gets no further than column {far[0]}, row {far[1]}")  # fmt: skip
        return out

    # the route the bot plays

    def route(self, actions: list[Action]) -> list[Step]:
        steps: list[Step] = []
        opened: frozenset[str] = frozenset()
        cell = self.grid.start
        for act in actions:
            world = self.world(opened)
            tree = self.reach(world, cell)
            steps += self.walk(world, tree, cell, act.at)
            cell = act.at
            if act.hop:
                steps.append(Step("jump", move=act.hop, cell=act.at))
            if act.do in {"touch", "dash"}:
                t = self.grid.thing(act.thing)
                steps.append(Step(act.do, x=act.at[0] + 0.5, cell=act.at, thing=act.thing))
                opened = opened | {act.thing}
                if t.kind == "crate" and t.holds and not t.gives:
                    spot = t.cells[0]
                    after = self.world(opened)
                    if after.standable(*spot) and after.run_at.get(spot) is after.run_at.get(cell):
                        steps.append(Step("collect", x=spot[0] + 0.5, cell=spot, thing=t.id, drop=True))
                        cell = spot
            elif act.do == "take":
                ident, drop = act.thing.rstrip(">"), act.thing.endswith(">")
                steps.append(Step("collect", x=act.at[0] + 0.5, cell=act.at, thing=ident, drop=drop))
                if not drop:  # a lever opens its gates
                    opened = opened | self.gates(self.grid.thing(ident))
            elif act.do == "exit":
                steps.append(Step("exit", x=act.at[0] + 0.5, cell=act.at, thing=act.thing))
        return steps

    def walk(self, world: World, tree, cell: Cell, to: Cell) -> list[Step]:
        here, there = world.run_at[cell], world.run_at[to]
        chain = []
        i = there.i
        while i != here.i:
            parent, ln = tree[i]
            chain.append(ln)
            i = parent
        steps = []
        for ln in reversed(chain):
            steps.append(Step(ln.move.kind, move=ln.move, cell=ln.flight.cell))
        if not steps or steps[-1].cell != to:
            steps.append(Step("walk", x=to[0] + 0.5, cell=to))
        return steps


@lru_cache(maxsize=64)
def gap_limits(m: Motion) -> tuple[int, int]:
    """The widest gap the model crosses with a jump, and with a jump and a dash (0 without a
    dash). Edge take-offs make these one wider than the plan's whole-tile estimate at times
    (live 2026-11-02: the plan said 3, a jump from the very edge crossed 4)."""

    def widest(motion: Motion) -> int:
        best = 0
        for gap in range(1, 24):
            cols = gap + 8
            tiles = {(c, 2): "solid" for c in range(cols) if not 3 <= c < 3 + gap}
            grid = Grid(cols, 3, tiles, [Thing("x", "exit", "", ((cols - 2, 1),))], (1, 1))
            if not solve(grid, motion).solved:
                break
            best = gap
        return best

    jump = widest(replace(m, dash=0.0))
    return jump, (widest(m) if m.dash > 0 else 0)


def solve(grid: Grid, m: Motion, held: set[str] = (), exclusive: set[str] = (),
          limit: int = 400) -> Solution:  # fmt: skip
    return Search(grid, m, set(held), set(exclusive)).run(limit)
