"""The game plan: the human's requirements and the concrete numbers every later agent uses.

The Game Designer writes it after the research and before the design (L2.2): the human's
requirements, numbered and kept word for word; movement with numbers; abilities, mechanics
(what each is for) and a challenge vocabulary; per level a teaching goal, a new idea, its
challenges, the abilities it needs and how hard it is; what the player sees and hears when
something happens; an animation list per entity; risks. The design is written from it.

Code, not the model, decides what holds:
* `kinematics`: jump physics derived from the movement numbers (gravity, take-off speed, the
  widest gap a jump clears, how high a platform can be). Later checks (level reachability,
  L2.3) use these numbers.
* `fit_plan` repairs what it can (requirement ids R1.., level numbers 1.., difficulty 1-10,
  no negative numbers); `check_plan` lists what it cannot: a requirement that is not the
  human's own words or that no part of the plan covers, inconsistent movement numbers, a
  level without a teaching goal or a new idea, a level easier than the one before.
"""

import math
import re

from pydantic import BaseModel, Field

from spriteforge.agents.models import Contract

TILE = 16
MAX_LEVELS = 8
PART_KINDS = ("movement", "style", "ability", "mechanic", "challenge", "level", "feel", "animation")


class Requirement(Contract):
    id: str = Field(description="R1, R2, ... in order")
    text: str = Field(description="the human's own words, copied exactly (a sentence or a clause)")


class Movement(Contract):
    move_speed: float = Field(description="tiles per second")
    jump_height: float = Field(description="the highest jump, in tiles; 0 when there is no jump")
    air_time: float = Field(
        description="seconds from take-off to landing at the same height; 0 without a jump"
    )
    dash_distance: float = Field(description="tiles; 0 without a dash")
    dash_cooldown: float = Field(description="seconds; 0 without a dash")
    coyote_time: float = Field(
        description="seconds a jump still works after walking off a ledge; 0 without a jump"
    )


class Ability(Contract):
    name: str
    does: str
    numbers: str = Field(description="its numbers: duration, cooldown, range ...; '' if none")
    unlocked_by: str = Field(description="'start' or what gives it (a pickup, a crate ...)")


class Mechanic(Contract):
    name: str
    what: str = Field(description="what happens, concretely")
    purpose: str = Field(description="what it is for in play")


class Challenge(Contract):
    name: str
    tests: str = Field(description="what it asks of the player")
    needs: list[str] = Field(description="ability names it needs; [] for none")


class LevelPlan(Contract):
    n: int
    name: str
    teaching_goal: str = Field(description="what the player learns here")
    new_idea: str = Field(description="the one idea this level adds")
    challenges: list[str] = Field(description="challenge names from the vocabulary")
    abilities: list[str] = Field(description="ability names the level needs")
    difficulty: int = Field(description="1 (gentle) to 10 (hardest), never below the level before")


class Feel(Contract):
    event: str = Field(description="what happens: kill, transform, break, pick up, hurt ...")
    see: str = Field(description="what the player sees (particles, flash, squash, shake ...)")
    hear: str = Field(description="what the player hears; '' for silence")


class AnimationRow(Contract):
    name: str = Field(description="e.g. idle, hop, jump, land, dash, transform, break")
    frames: int
    shows: str = Field(description="what the frames show, e.g. squash, stretch, airborne, land")
    loop: bool


class EntityAnimations(Contract):
    entity: str = Field(description="what it animates: the player, an enemy, a crate ...")
    rows: list[AnimationRow]


class Coverage(Contract):
    requirement: str = Field(description="a requirement id, e.g. R1")
    parts: list[str] = Field(
        description="plan parts that fulfil it: 'movement', 'style', 'ability:<name>', "
        "'mechanic:<name>', 'challenge:<name>', 'level:<n>', 'feel:<event>', "
        "'animation:<entity>'"
    )
    how: str


class GamePlan(Contract):
    requirements: list[Requirement]
    style: str = Field(description="how the game looks and feels overall")
    movement: Movement
    abilities: list[Ability]
    mechanics: list[Mechanic]
    challenges: list[Challenge] = Field(description="the challenge vocabulary: obstacle kinds")
    levels: list[LevelPlan]
    feel: list[Feel]
    animations: list[EntityAnimations]
    coverage: list[Coverage]
    risks: list[str]


class Kinematics(BaseModel):
    """Jump physics derived from the plan's movement numbers (pixels, seconds)."""

    tile: int = TILE
    speed: float  # px/s
    jump_height: float  # px
    air_time: float  # s
    gravity: float  # px/s^2: a jump of h px lasting T s needs g = 8h / T^2
    jump_velocity: float  # px/s at take-off: v = 4h / T
    jump_distance: float  # px covered in the air at full speed (take-off to landing)
    max_rise: int  # tiles: the highest platform top a jump reaches
    max_gap: int  # tiles: the widest gap a running jump clears
    dash: float  # px
    max_gap_with_dash: int  # tiles, with one dash in the air
    coyote: float  # px of ledge forgiveness at full speed


class PlanRecord(BaseModel):
    """What the run keeps (`game/plan/vK.json`): the plan and what code made of it."""

    plan: GamePlan
    kinematics: Kinematics | None  # None without a jump
    problems: list[str] = Field(default_factory=list)  # what code could not repair
    notes: list[str] = Field(default_factory=list)  # what code repaired
    retried: bool = False  # the Game Designer was asked once more with the problems


# --- code: kinematics, repairs, checks ---------------------------------------------------------


def derive_kinematics(m: Movement, tile: int = TILE) -> Kinematics | None:
    if m.jump_height <= 0 or m.air_time <= 0:
        return None
    h, t, speed = m.jump_height * tile, m.air_time, m.move_speed * tile
    distance, dash = speed * t, m.dash_distance * tile
    gap = lambda px: max(0, math.ceil(px / tile) - 1)  # a gap must be narrower than the jump
    return Kinematics(
        tile=tile,
        speed=round(speed, 2),
        jump_height=round(h, 2),
        air_time=t,
        gravity=round(8 * h / t**2, 1),
        jump_velocity=round(4 * h / t, 1),
        jump_distance=round(distance, 1),
        max_rise=int(h // tile),
        max_gap=gap(distance),
        dash=round(dash, 1),
        max_gap_with_dash=gap(distance + dash),
        coyote=round(speed * m.coyote_time, 1),
    )


def key(text: str) -> str:
    """Names compared loosely: case, spaces, dashes and underscores do not matter."""
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


def plain(text: str) -> str:
    """Words compared for 'word for word': case, quotes, dashes and spacing do not matter."""
    text = str(text).casefold().translate(str.maketrans("‘’“”–—", "''\"\"--"))
    return re.sub(r"\s+", " ", text).strip(" \t\"'.,;:!")


def ability_names(plan: GamePlan) -> set[str]:
    """Abilities a level or challenge may name: the plan's, plus move/jump/dash when the
    movement has them."""
    m = plan.movement
    base = {"move", "walk", "run"} | ({"jump"} if m.jump_height > 0 else set())
    base |= {"dash"} if m.dash_distance > 0 else set()
    return {key(a.name) for a in plan.abilities} | base


def fit_plan(plan: GamePlan) -> tuple[GamePlan, list[str]]:
    """Repairs code can make: requirement ids R1.. (coverage follows), levels in order and
    numbered 1.., difficulty within 1-10, no negative movement numbers, trimmed lists."""
    notes = []
    ids = {}
    requirements = []
    for i, r in enumerate((r for r in plan.requirements if r.text.strip()), 1):
        ids.setdefault(r.id.strip().upper(), f"R{i}")
        ids.setdefault(r.id.strip(), f"R{i}")
        if r.id.strip() != f"R{i}":
            notes.append(f"requirement {r.id!r} renumbered R{i}")
        requirements.append(r.model_copy(update={"id": f"R{i}", "text": r.text.strip()}))
    coverage = []
    for c in plan.coverage:
        rid = ids.get(c.requirement.strip()) or ids.get(c.requirement.strip().upper())
        if rid is None:
            notes.append(f"coverage of unknown requirement {c.requirement!r} dropped")
            continue
        coverage.append(c.model_copy(update={"requirement": rid}))
    levels = sorted(plan.levels, key=lambda lv: lv.n)
    if [lv.n for lv in levels] != list(range(1, len(levels) + 1)):
        notes.append("levels renumbered 1.." + str(len(levels)))
    levels = [lv.model_copy(update={"n": i, "difficulty": max(1, min(10, lv.difficulty))})
              for i, lv in enumerate(levels, 1)]  # fmt: skip
    m = plan.movement
    movement = m.model_copy(update={k: max(0.0, v) for k, v in m.model_dump().items()})
    if movement != m:
        notes.append("negative movement numbers set to 0")
    return plan.model_copy(update={"requirements": requirements, "coverage": coverage,
                                   "levels": levels, "movement": movement,
                                   "risks": [r for r in plan.risks if r.strip()]}), notes  # fmt: skip


def part_known(part: str, plan: GamePlan) -> bool:
    kind, _, name = part.partition(":")
    kind, name = kind.strip().casefold(), key(name)
    if kind in {"movement", "style"}:
        return not name
    names = {
        "ability": ability_names(plan),
        "mechanic": {key(x.name) for x in plan.mechanics},
        "challenge": {key(x.name) for x in plan.challenges},
        "level": {str(lv.n) for lv in plan.levels},
        "feel": {key(f.event) for f in plan.feel},
        "animation": {key(a.entity) for a in plan.animations},
    }.get(kind)
    return names is not None and name in names


def check_plan(plan: GamePlan, words: str) -> list[str]:
    """What is wrong with a (fitted) plan; [] when it holds. `words`: everything the human
    said (the requirements must be copied from it)."""
    problems = []
    said = plain(words)
    if not plan.requirements:
        problems.append("no requirements: number every distinct thing the human asked for")
    for r in plan.requirements:
        if plain(r.text) not in said:
            problems.append(f"{r.id} is not the human's own words (copy them exactly): {r.text!r}")
    covered = {c.requirement for c in plan.coverage if c.parts}
    for r in plan.requirements:
        if r.id not in covered:
            problems.append(f"{r.id} is not covered by any part of the plan")
    for c in plan.coverage:
        for part in c.parts:
            if not part_known(part, plan):
                problems.append(f"coverage of {c.requirement}: {part!r} is not a part of the plan")
    m = plan.movement
    if m.move_speed <= 0:
        problems.append("movement: move_speed must be above 0 tiles per second")
    if (m.jump_height > 0) != (m.air_time > 0):
        problems.append("movement: a jump needs both jump_height and air_time (or neither)")
    if k := derive_kinematics(m):
        if not 0.5 <= m.jump_height <= 10:
            problems.append(f"movement: jump_height {m.jump_height:g} tiles is outside 0.5-10")
        if not 0.2 <= m.air_time <= 2:
            problems.append(f"movement: air_time {m.air_time:g} s is outside 0.2-2 s")
        if not 200 <= k.gravity <= 5000:
            problems.append(
                f"movement: a {m.jump_height:g}-tile jump in {m.air_time:g} s needs gravity "
                f"{k.gravity:g} px/s^2 (outside 200-5000): it would feel "
                + ("floaty" if k.gravity < 200 else "like a twitch")
            )
        if m.coyote_time > 0.3:
            problems.append(f"movement: coyote_time {m.coyote_time:g} s is above 0.3 s")
    if m.dash_distance > 0 and m.dash_cooldown <= 0:
        problems.append("movement: a dash needs a cooldown above 0 s")
    abilities = ability_names(plan)
    challenges = {key(c.name) for c in plan.challenges}
    for c in plan.challenges:
        for need in c.needs:
            if key(need) not in abilities:
                problems.append(f"challenge {c.name!r} needs unknown ability {need!r}")
    if not 1 <= len(plan.levels) <= MAX_LEVELS:
        problems.append(f"{len(plan.levels)} levels: plan 1 to {MAX_LEVELS}")
    ideas = set()
    for lv in plan.levels:
        where = f"level {lv.n} ({lv.name})"
        if not lv.teaching_goal.strip():
            problems.append(f"{where} has no teaching goal")
        if not lv.new_idea.strip():
            problems.append(f"{where} has no new idea")
        elif key(lv.new_idea) in ideas:
            problems.append(f"{where} repeats an earlier level's new idea")
        ideas.add(key(lv.new_idea))
        for name in lv.challenges:
            if key(name) not in challenges:
                problems.append(f"{where}: challenge {name!r} is not in the vocabulary")
        for name in lv.abilities:
            if key(name) not in abilities:
                problems.append(f"{where}: unknown ability {name!r}")
        if len(plan.levels) > 1 and not lv.challenges:
            problems.append(f"{where} has no challenge")
    for a, b in zip(plan.levels, plan.levels[1:], strict=False):
        if b.difficulty < a.difficulty:
            problems.append(f"level {b.n} (difficulty {b.difficulty}) is easier than level "
                            f"{a.n} ({a.difficulty})")  # fmt: skip
    return problems


def make_record(plan: GamePlan, words: str, retried: bool = False,
                notes: list[str] = ()) -> PlanRecord:  # fmt: skip
    fitted, repairs = fit_plan(plan)
    return PlanRecord(plan=fitted, kinematics=derive_kinematics(fitted.movement),
                      problems=check_plan(fitted, words), notes=[*notes, *repairs],
                      retried=retried)  # fmt: skip


# --- prompts -------------------------------------------------------------------------------------


GUIDE = """* requirements: every distinct thing the human asked for, numbered R1, R2, ... in order,
  each copied word for word from the human's words (a sentence or a clause; never paraphrase).
* movement: numbers in tiles (16 px) and seconds. Code derives the jump physics from them (a
  jump of h tiles with T seconds of air time needs gravity 8h/T^2), so keep them consistent
  and close to what players of the genre expect; 0 for what the game does not have.
* abilities (move, jump and dash are implied by the movement numbers), mechanics with what
  each is for, and the challenge vocabulary: obstacle kinds and what each asks of the player.
* levels: as many as the request asks for (1 for a single arena or session). Each has a
  teaching goal, the one new idea it adds (never the same twice), its challenges from the
  vocabulary, the abilities it needs and a difficulty 1-10 that never drops. Teach each idea
  where failing costs little before testing it; combine abilities in later levels.
* feel: what the player sees and hears when something happens (kill, transform, break, pick
  up, hurt ...).
* animations: per animated thing, its rows with frame counts, what the frames show (a hop:
  squash, stretch, airborne, land) and loop or once; breaking things get a break row.
* coverage: for each requirement, the plan parts that fulfil it, written 'movement', 'style',
  'ability:<name>', 'mechanic:<name>', 'challenge:<name>', 'level:<n>', 'feel:<event>' or
  'animation:<entity>'. Every requirement must be covered.
* risks: what could make the game unfun or unbuildable.
Keep every number the human gave. The game is original: research teaches patterns, never copy
a game's levels, characters or art. No story, names or lore unless the human asked for a story."""


def plan_prompt(request: str, research: str = "", lessons: str = "") -> str:
    """`lessons`: human-reviewed lessons from earlier games of the genre (L2.6)."""
    return (
        "Write the game plan for this request: the concrete numbers and ideas the design, the "
        "levels, the art and the code will be built from, not adjectives.\n" + GUIDE
        + (f"\n\nResearch on similar games:\n{research.strip()}" if research.strip() else "")
        + ("\n\nLessons from earlier games of this genre (reviewed by a human; apply them where "
           f"they fit this request):\n{lessons.strip()}" if lessons.strip() else "")
        + f"\n\nThe human's words:\n{request.strip()}"
    )  # fmt: skip


def plan_revision_prompt(plan: GamePlan, changes: str, words: str, said: str = "") -> str:
    """`changes`: what to change (at G4, the Director's design changes); `said`: the human's
    own words at this gate, which new requirements are copied from (default: `changes`)."""
    said = said.strip() or changes.strip()
    asked = f"\n\nThe human said:\n{said}" + (
        f"\n\nWhat to change in the plan:\n{changes.strip()}" if changes.strip() != said else "")
    return (
        "Revise this game plan for the human's feedback. Keep the existing requirements word for "
        "word; when the human asks for something new, add it as a new requirement copied word "
        "for word from what the human said (never from a summary of it). Change only what the "
        "feedback asks for and keep every requirement covered.\n" + GUIDE + asked
        + f"\n\nThe human's words so far:\n{words.strip()}"
        + f"\n\nCurrent plan:\n{plan.model_dump_json()}"
    )  # fmt: skip


def retry_prompt(prompt: str, problems: list[str]) -> str:
    return (
        prompt + "\n\nYour previous plan had these problems; answer the whole plan again with "
        "them fixed:\n" + "\n".join(f"- {p}" for p in problems)
    )


def plan_brief(record: PlanRecord) -> str:
    """The plan as the design prompt reads it, with the kinematics code derived."""
    p, k, m = record.plan, record.kinematics, record.plan.movement
    jump = (
        f"jump {m.jump_height:g} tiles, {m.air_time:g} s in the air (gravity {k.gravity:g} "
        f"px/s^2, take-off {k.jump_velocity:g} px/s; a jump clears gaps up to {k.max_gap} tiles "
        f"and reaches platforms {k.max_rise} tiles up), coyote time {m.coyote_time:g} s"
        if k
        else "no jump"
    )
    dash = (f"dash {m.dash_distance:g} tiles, cooldown {m.dash_cooldown:g} s"
            + (f" (with a dash in the air: gaps up to {k.max_gap_with_dash} tiles)" if k else "")
            if m.dash_distance else "no dash")  # fmt: skip
    lines = [
        "Requirements: " + "; ".join(f"{r.id} {r.text}" for r in p.requirements),
        f"Style: {p.style}",
        f"Movement: {m.move_speed:g} tiles/s; {jump}; {dash}",
        "Abilities: " + "; ".join(f"{a.name} ({a.does}; {a.numbers or '-'}; from {a.unlocked_by})"
                                  for a in p.abilities),
        "Mechanics: " + "; ".join(f"{x.name}: {x.what} (for {x.purpose})" for x in p.mechanics),
        "Challenges: " + "; ".join(f"{c.name}: {c.tests}" for c in p.challenges),
        *(f"Level {lv.n} {lv.name} (difficulty {lv.difficulty}): teaches {lv.teaching_goal}; "
          f"new idea {lv.new_idea}; challenges {', '.join(lv.challenges) or '-'}; needs "
          f"{', '.join(lv.abilities) or '-'}" for lv in p.levels),
        "Feel: " + "; ".join(f"{f.event}: {f.see}" + (f", {f.hear}" if f.hear else "")
                             for f in p.feel),
        "Animations: " + "; ".join(f"{a.entity}: " + ", ".join(
            f"{r.name} {r.frames}f ({r.shows})" for r in a.rows) for a in p.animations),
    ]  # fmt: skip
    return "\n".join(lines)


# --- offline stand-in ----------------------------------------------------------------------------


NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", text) if len(s.strip()) > 2]


def fallback_plan(notes: list[str], side_view: bool) -> GamePlan:
    """Offline Game Designer: the human's sentences as requirements, plain movement numbers,
    as many levels as the words ask for (1 otherwise), one challenge per level."""
    said = [s for n in notes for s in sentences(n)]
    count = re.search(r"\b(\d|one|two|three|four|five|six|seven|eight)\s+levels?\b",
                      " ".join(notes), re.IGNORECASE)  # fmt: skip
    n = int(count[1]) if count and count[1].isdigit() else NUMBERS.get((count[1] if count else "").lower(), 1)
    n = max(1, min(MAX_LEVELS, n))
    movement = Movement(move_speed=6, jump_height=3 if side_view else 0,
                        air_time=0.7 if side_view else 0, dash_distance=0, dash_cooldown=0,
                        coyote_time=0.1 if side_view else 0)  # fmt: skip
    hazard = Challenge(name="hazard", tests="avoid what hurts while moving", needs=["move"])
    levels = [
        LevelPlan(n=i, name=f"Level {i}", teaching_goal="the controls" if i == 1 else "the hazards together",
                  new_idea="the core loop" if i == 1 else f"harder mix {i}",
                  challenges=["hazard"], abilities=["move"], difficulty=min(10, 2 * i))
        for i in range(1, n + 1)
    ]  # fmt: skip
    requirements = [Requirement(id=f"R{i}", text=s) for i, s in enumerate(said, 1)]
    return GamePlan(
        requirements=requirements,
        style="readable pixel art",
        movement=movement,
        abilities=[],
        mechanics=[Mechanic(name="core loop", what="what the human asked for",
                            purpose="the game's main activity")],
        challenges=[hazard],
        levels=levels,
        feel=[Feel(event="hurt", see="the player flashes", hear="")],
        animations=[EntityAnimations(entity="player", rows=[
            AnimationRow(name="idle", frames=2, shows="breathing", loop=True),
            AnimationRow(name="walk", frames=4, shows="a step cycle", loop=True)])],
        coverage=[Coverage(requirement=r.id, parts=["level:1", "mechanic:core loop"],
                           how="the design follows it") for r in requirements],
        risks=["the offline stand-in plans no detail"],
    )


# --- shown at G2 -----------------------------------------------------------------------------------


def plan_markdown(record: PlanRecord) -> str:
    p, k, m = record.plan, record.kinematics, record.plan.movement
    covers = {}
    for c in p.coverage:
        covers.setdefault(c.requirement, []).append(f"{', '.join(c.parts)} ({c.how})")
    reqs = "\n".join(f"{r.id}. \"{r.text}\": " + ("; ".join(covers.get(r.id, [])) or "NOT COVERED")
                     for r in p.requirements)  # fmt: skip
    kin = (
        f"| gravity | {k.gravity:g} px/s² |\n| take-off speed | {k.jump_velocity:g} px/s |\n"
        f"| distance in the air | {k.jump_distance:g} px |\n| highest platform | {k.max_rise} tiles |\n"
        f"| widest gap | {k.max_gap} tiles ({k.max_gap_with_dash} with a dash) |\n"
        f"| coyote time | {k.coyote:g} px |"
        if k else "| jump | none |"
    )  # fmt: skip
    levels = "\n".join(
        f"| {lv.n} | {lv.name} | {lv.teaching_goal} | {lv.new_idea} | {', '.join(lv.challenges) or '-'} "
        f"| {', '.join(lv.abilities) or '-'} | {lv.difficulty} |" for lv in p.levels)  # fmt: skip
    anims = "\n".join(f"* **{a.entity}**: " + "; ".join(
        f"{r.name} ({r.frames} frames, {'loop' if r.loop else 'once'}: {r.shows})" for r in a.rows)
        for a in p.animations)  # fmt: skip
    return (
        f"# Game plan\n\n## Requirements (the human's words)\n{reqs}\n\n**Style:** {p.style}\n\n"
        f"## Movement\n{m.move_speed:g} tiles/s; jump {m.jump_height:g} tiles in {m.air_time:g} s; "
        f"dash {m.dash_distance:g} tiles (cooldown {m.dash_cooldown:g} s); coyote time "
        f"{m.coyote_time:g} s\n\n| kinematics (code) | |\n|---|---|\n{kin}\n\n"
        "## Abilities\n" + ("\n".join(f"* **{a.name}**: {a.does} {a.numbers} (from {a.unlocked_by})"
                                      for a in p.abilities) or "- (none)")
        + "\n\n## Mechanics\n" + "\n".join(f"* **{x.name}**: {x.what} _For:_ {x.purpose}" for x in p.mechanics)
        + "\n\n## Challenges\n" + "\n".join(f"* **{c.name}**: {c.tests}" for c in p.challenges)
        + "\n\n## Levels\n| n | name | teaches | new idea | challenges | needs | difficulty |\n"
        "|---|---|---|---|---|---|---|\n" + levels
        + "\n\n## Feel\n" + "\n".join(f"* **{f.event}**: {f.see}" + (f"; {f.hear}" if f.hear else "")
                                      for f in p.feel)
        + f"\n\n## Animations\n{anims or '- (none)'}\n\n## Risks\n"
        + ("\n".join(f"* {r}" for r in p.risks) or "- (none)")
        + ("\n\n## Problems code found\n" + "\n".join(f"* {x}" for x in record.problems)
           if record.problems else "")
        + "".join(f"\n> repaired: {n}" for n in record.notes) + "\n"
    )  # fmt: skip
