"""The offline Level Designer (mock runs and tests): a level made by code from the plan.

Left to right: a start flat with a pickup, then sections (a gap, a climb, spikes, an enemy in a
trough, a platform with a pickup), a checkpoint after every two, and a flat with the exit.
Gaps and climbs stay one tile inside the plan's limits; each level has one section more than
the one before, so it is harder by code's measure. `variant` turns the section order (an
offline answer to feedback changes the level).
"""

import re

from .design import GameDesign
from .levels import ChallengeAt, LegendEntry, LevelSpec
from .plan import PlanRecord

H, FLOOR = 10, 7  # rows; the row the player stands on (ground in rows 8 and 9)
LEVEL_EVENTS = ("jump", "level_complete")


def entity(design: GameDesign, roles: tuple[str, ...], words: str = "", avoid: str = "") -> str | None:
    """The first entity of `roles` whose id or look has one of `words` (any when ''), and
    none of `avoid`."""
    for e in design.entities:
        text = f"{e.id} {e.look}".lower()
        if e.role not in roles or (avoid and re.search(avoid, text)):
            continue
        if not words or re.search(words, text):
            return e.id
    return None


def fallback_level(n: int, design: GameDesign, plan: PlanRecord, variant: int = 0) -> LevelSpec:
    k = plan.kinematics
    rise = max(1, min((k.max_rise if k else 1) - 1, 1 + n // 2))
    gap = max(1, min((k.max_gap if k else 1) - 1, n))
    player = next(e.id for e in design.entities if e.role == "player")
    hazard_words = r"spike|thorn|lava|hazard|acid pool"
    tile = entity(design, ("tile",), avoid=hazard_words) or player
    spikes = entity(design, ("tile", "prop"), hazard_words)
    enemy = entity(design, ("enemy",))
    pickup = entity(design, ("pickup",), avoid=r"key|potion|form|power")
    flag = entity(design, ("prop", "pickup"), r"flag|checkpoint|banner|torch")
    door = entity(design, ("prop",), r"door|exit|portal|goal|gate") or entity(design, ("prop",)) \
        or pickup or tile  # fmt: skip
    legend = [
        LegendEntry(char="#", kind="solid", entity=tile, needs="", opened_by="never", holds="", gives=""),
        LegendEntry(char="=", kind="one_way", entity=tile, needs="", opened_by="never", holds="", gives=""),
        LegendEntry(char="@", kind="start", entity=player, needs="", opened_by="never", holds="", gives=""),
        LegendEntry(char="D", kind="exit", entity=door, needs="", opened_by="never", holds="", gives=""),
    ]  # fmt: skip
    extra = [("^", "hazard", spikes, "never"), ("e", "enemy", enemy, "never"),
             ("o", "pickup", pickup, "never"), ("F", "checkpoint", flag, "never")]  # fmt: skip
    legend += [LegendEntry(char=c, kind=kind, entity=ent, needs="", opened_by=ob, holds="", gives="")
               for c, kind, ent, ob in extra if ent]  # fmt: skip
    kinds = ["gap", "climb", "spikes", "enemy", "platform"]
    kinds = [x for x in kinds if (x != "spikes" or spikes) and (x != "enemy" or enemy)]
    turn = (n - 1 + variant) % len(kinds)
    kinds = kinds[turn:] + kinds[:turn]
    cols: list[dict[int, str]] = []

    def ground(width: int, top: int = FLOOR + 1, on: dict | None = None, at: int = 0) -> None:
        """`width` columns of ground from row `top` down; `on` goes in the column `at`."""
        for j in range(width):
            col = {r: "#" for r in range(top, H)}
            col.update((on or {}) if j == at else {})
            cols.append(col)

    ground(1)
    ground(1, on={FLOOR: "@"})
    ground(1, on={FLOOR: "o"} if pickup else None)
    ground(2)
    lp = plan.plan.levels[n - 1] if n <= len(plan.plan.levels) else None
    names = (lp.challenges if lp else []) or [c.name for c in plan.plan.challenges][:1]
    challenges = []
    for i, kind in enumerate(kinds[: 2 + n]):
        start = len(cols)
        if kind == "gap":
            cols += [{} for _ in range(gap)]
        elif kind == "climb":
            ground(4, top=FLOOR + 1 - rise, on={FLOOR - rise: "o"} if pickup else None, at=1)
        elif kind == "spikes":
            ground(1, on={FLOOR: "^"})
            ground(1, on={FLOOR: "^"})
        elif kind == "enemy":
            ground(1, top=FLOOR + 2)
            ground(1, top=FLOOR + 2, on={FLOOR + 1: "e"})
            ground(1, top=FLOOR + 2)
        elif kind == "platform":
            height = FLOOR - min(rise + 1, 3)
            for j in range(4):
                ground(1, on={height + 1: "=", **({height: "o"} if pickup and j == 1 else {})})
        if names:
            challenges.append(ChallengeAt(name=names[i % len(names)], column=start))
        ground(3, on={FLOOR: "F"} if flag and i % 2 == 1 else None, at=1)
    ground(2)
    ground(1, on={FLOOR: "D"})
    ground(2)
    rows = ["".join(col.get(r, " ") for col in cols) for r in range(H)]
    checks = {c.event for c in design.checks}
    events = [e for e in LEVEL_EVENTS if e in checks]
    if pickup and f"{pickup}_collect" in checks:
        events.append(f"{pickup}_collect")
    if n == len(plan.plan.levels) and "win" in checks:
        events.append("win")
    for name in names:  # every planned challenge is placed
        if name not in {c.name for c in challenges}:
            challenges.append(ChallengeAt(name=name, column=5))
    return LevelSpec(n=n, name=lp.name if lp else f"Level {n}", legend=legend, rows=rows,
                     challenges=challenges, events=events, exclusive=[],
                     notes=f"Offline level: {', '.join(kinds[: 2 + n])}, a checkpoint after every "
                           "two sections, the exit at the right.")  # fmt: skip
