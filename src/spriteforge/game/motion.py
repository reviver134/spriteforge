"""Motion design (L2.4): how each thing moves and reacts, as data the art and the SDK follow.

The Art Director writes an `AnimationBrief` from the design and the game plan (its animation
list and feel), once per design version: per entity the animation rows with what each frame
shows (a hop: squash, stretch, airborne, land), when the game plays the row, its timing (fps,
loop or once); per form ability how the player looks in it (and a tint for when its row fails);
and per event an effect (particles, flash, shake, squash) that the SDK plays with `sf.fx`.

Models propose; code enforces:
* `fit_brief` repairs what it can (entity ids and ability names matched loosely, frame counts
  and timing within limits, break/die rows once and ending in "gone", tiles keep only break
  rows, forms get a tint, missing rows and effects from the vocabulary) and `check_brief` says
  what it cannot.
* `fallback_brief` is the offline Art Director: the plan's animations and feel, else the
  design's animation names, with poses from a vocabulary (a slime hops, a crate breaks).
* After Muse draws the rows, `row_problems` measures the frames (a moving row whose frames do
  not differ would slide; frames that change size or leave the ground line; a loop that jumps;
  a break row that does not end empty) and `finish_rows` repairs what code can: a break row's
  last frame is emptied, a still moving row gets squash-and-stretch frames made from its
  first frame, a missing break row is a crumble of the art itself.
"""

import io
import math
import re
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel, Field

from spriteforge.agents.models import Contract

from .design import Entity, GameDesign, snake
from .plan import PlanRecord, key

MAX_FRAMES = 4  # frames per row (one sheet row)
MAX_ROWS = 6  # rows per sprite sheet
PLAYS = ("idle", "move", "jump", "fall", "land", "dash", "hurt", "attack", "break", "die",
         "form", "pickup", "other")  # fmt: skip
MOMENTS = ("jump", "land", "dash", "break", "kill", "pickup", "transform", "hurt", "checkpoint",
           "exit", "game")  # fmt: skip
ONCE = {"jump", "fall", "land", "hurt", "attack", "break", "die"}  # played once, never looped
GONE = {"break", "die"}  # rows that end with nothing left
MOVING = {"move", "form", "dash"}  # rows whose frames must differ (else the sprite slides)
ORDER = ["idle", "move", "jump", "form", "dash", "fall", "land", "hurt", "attack", "break", "die",
         "pickup", "other"]  # fmt: skip
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")
BLOB = re.compile(r"slime|blob|jelly|ooze|goo\b|gel\b|pudding", re.IGNORECASE)


class Pose(Contract):
    shows: str = Field(description="what this frame shows, concretely (shape, pose)")
    airborne: bool = Field(description="the subject is off the ground in this frame")


class MotionRow(Contract):
    entity: str = Field(description="the design's entity id")
    name: str = Field(description="row name, snake_case: idle, hop, jump, dash, break, die, "
                                  "acid_form ...")  # fmt: skip
    plays: Literal[PLAYS] = Field(
        description="when the game plays it: idle, move (walking, hopping, flying), jump, fall, "
        "land, dash, hurt, attack, break (a thing breaks), die (an enemy is beaten), form (the "
        "player in a transformed form, every move), pickup (an item's idle shine), other"
    )
    form: str = Field(description="plays=form: the ability whose form this row shows; else ''")
    frames: list[Pose] = Field(description=f"1-{MAX_FRAMES} frames, in order")
    fps: int = Field(description="frames per second, 4-16")
    loop: bool


class FormLook(Contract):
    ability: str = Field(description="the plan's ability name, e.g. 'Acid Form'")
    looks: str = Field(description="how the player looks in this form: colours, material")
    tint: str = Field(description="#RRGGBB: the SDK tints the player this colour when the "
                                  "form's row fails its checks")  # fmt: skip
    particles: str = Field(description="#RRGGBB of what the form gives off (drips, sparks), "
                                       "or ''")  # fmt: skip


class Effect(Contract):
    name: str = Field(description="the plan's feel event it makes visible: kill, transform ...")
    on: Literal[MOMENTS] = Field(
        description="when the SDK plays it: jump, land, dash, break (a crate or wall opens), "
        "kill (an enemy is beaten), pickup, transform (the player changes form), hurt (sent "
        "back), checkpoint, exit; 'game' = only when the game's code calls sf.fx(name)"
    )
    particles: int = Field(description="0-30 particles")
    colors: list[str] = Field(description="#RRGGBB particle colours")
    speed: float = Field(description="particle speed, pixels per second on a 320x180 screen")
    flash: str = Field(description="#RRGGBB the subject flashes, or ''")
    shake: float = Field(description="screen shake 0-1 (0 none, 1 strong)")
    squash: float = Field(description="-0.4..0.4: the player squashes flat (+) or stretches "
                                      "tall (-) for a moment; 0 none")  # fmt: skip
    seconds: float = Field(description="how long it lasts, 0.1-1 s")


class AnimationBrief(Contract):
    rows: list[MotionRow]
    forms: list[FormLook]
    effects: list[Effect]


class MotionRecord(BaseModel):
    """What the run keeps (`game/design/vN-motion.json`): the brief and what code made of it."""

    brief: AnimationBrief
    problems: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)  # the design's ids it was written for

    def rows(self, entity: str) -> list[MotionRow]:
        return [r for r in self.brief.rows if r.entity == entity]


# --- vocabulary ----------------------------------------------------------------------------------

P = lambda shows, air=False: Pose(shows=shows, airborne=air)
POSE_BOOK = {
    "idle": [P("resting"), P("breathing in, a little taller"), P("resting"),
             P("breathing out, a little lower")],
    "hop": [P("squashed low and wide, gathering to spring"), P("stretched tall and thin, "
            "springing up"), P("round, in the air above the ground", True),
            P("squashed wide, landing")],
    "walk": [P("left foot forward"), P("feet passing under the body"), P("right foot forward"),
             P("feet passing under the body")],
    "fly": [P("wings up", True), P("wings level", True), P("wings down", True),
            P("wings level", True)],
    "jump": [P("stretched tall, taking off", True), P("round, at the top of the jump", True)],
    "fall": [P("falling, a little stretched", True), P("falling, bracing to land", True)],
    "land": [P("squashed flat on landing"), P("springing back to shape")],
    "dash": [P("stretched long and low, leaning forward"),
             P("stretched long, a smear of speed behind")],
    "hurt": [P("recoiling from a hit, eyes shut"), P("flinching, squashed")],
    "attack": [P("winding up"), P("striking"), P("follow-through"), P("recovering")],
    "break": [P("cracks appear across it"), P("splitting into pieces"),
              P("pieces flying apart and falling"), P("gone: only a few tiny bits left")],
    "die": [P("hit, squashed flat"), P("bursting apart"), P("small bits scattering"),
            P("gone: only a few tiny bits left")],
    "pickup": [P("still"), P("a small glint"), P("a bright glint"), P("a small glint")],
}  # fmt: skip
NAME_PLAYS = {
    "idle": "idle", "stand": "idle", "rest": "idle", "breathe": "idle",
    "walk": "move", "run": "move", "move": "move", "hop": "move", "crawl": "move", "fly": "move",
    "chase": "move", "patrol": "move", "waddle": "move", "roll": "move", "slither": "move",
    "jump": "jump", "fall": "fall", "land": "land", "dash": "dash",
    "hurt": "hurt", "hit": "hurt", "damage": "hurt", "attack": "attack", "shoot": "attack",
    "break": "break", "crumble": "break", "shatter": "break", "dissolve": "break", "open": "break",
    "destroy": "break", "smash": "break",
    "die": "die", "death": "die", "defeat": "die", "pop": "die", "splat": "die",
    "spin": "pickup", "glint": "pickup", "shine": "pickup", "sparkle": "pickup", "bob": "pickup",
}  # fmt: skip
FPS = {"idle": 4, "move": 10, "form": 10, "jump": 8, "fall": 8, "land": 12, "dash": 14,
       "hurt": 10, "attack": 12, "break": 12, "die": 12, "pickup": 8, "other": 8}  # fmt: skip
MOMENT_WORDS = [
    ("kill", r"kill|defeat|beat|stomp|enemy|splat"),
    ("transform", r"transform|form|morph|change"),
    ("break", r"break|crumble|dissolve|smash|shatter|destroy|crate|wall"),
    ("pickup", r"pick|collect|coin|gem|item|power"),
    ("hurt", r"hurt|damage|hit|respawn|die|death|lose"),
    ("jump", r"jump"), ("land", r"land"), ("dash", r"dash"),
    ("checkpoint", r"checkpoint|flag|save"), ("exit", r"exit|finish|goal|door|complete|win"),
]  # fmt: skip
FX_FIELDS = ("particles", "colors", "speed", "flash", "shake", "squash", "seconds")
DEFAULT_FX = {on: dict(zip(FX_FIELDS, values, strict=True)) for on, values in {
    "jump": (4, ["#E8E8F0"], 40, "", 0.0, -0.25, 0.2),
    "land": (4, ["#C8C0B0"], 30, "", 0.0, 0.3, 0.2),
    "dash": (6, ["#E8E8F0"], 30, "", 0.0, -0.2, 0.25),
    "break": (12, ["#8C6A48", "#5A4632"], 80, "", 0.3, 0.0, 0.5),
    "kill": (12, ["#F0F0F0", "#F0D040"], 90, "#FFFFFF", 0.2, 0.0, 0.4),
    "pickup": (8, ["#F0D040", "#FFFFFF"], 50, "", 0.0, 0.0, 0.35),
    "transform": (14, ["#FFFFFF"], 60, "#FFFFFF", 0.1, 0.2, 0.4),
    "hurt": (6, ["#F05050"], 60, "#FF4040", 0.4, 0.2, 0.3),
}.items()}  # fmt: skip
# The library actor animation (an `actors.ANIM_SOURCES` key) each row kind is made from.
ACTOR_SOURCES = {"idle": "idle", "move": "move", "jump": "jump", "fall": "fall", "land": "idle",
                 "dash": "move", "hurt": "hurt", "attack": "attack", "die": "death",
                 "break": "death", "form": "move", "pickup": "idle", "other": "idle"}  # fmt: skip


def plays_of(name: str) -> str:
    words = snake(name).split("_")
    return next((NAME_PLAYS[w] for w in words if w in NAME_PLAYS), "other")


def is_blob(e: Entity) -> bool:
    return bool(BLOB.search(f"{e.id} {e.look}"))


def book_poses(plays: str, e: Entity, name: str = "") -> list[Pose]:
    if plays == "move":
        word = snake(name)
        kind = ("hop" if is_blob(e) or "hop" in word else "fly" if "fly" in word else "walk")
        return POSE_BOOK[kind]
    if plays == "form":
        return POSE_BOOK["hop" if is_blob(e) else "walk"]
    return POSE_BOOK.get(plays) or [P(f"{name.replace('_', ' ')}, pose {i + 1}") for i in range(4)]


def sentences_of(text: str, n: int) -> list[str]:
    """`n` poses from a plan row's 'shows' ("squash, stretch, airborne, land")."""
    parts = [p.strip() for p in re.split(r",|;|\bthen\b|->|→", text) if p.strip()]
    if len(parts) >= n:
        return parts[:n]
    return parts + [parts[-1] if parts else "resting"] * (n - len(parts))


AIR_WORDS = re.compile(r"airborne|in the air|mid-?air|off the ground|apex|peak|top of", re.IGNORECASE)


def is_form_ability(name: str, does: str) -> bool:
    """An ability that changes how the player looks: its name says form ('Acid Form', 'Metal
    Transformation'), or what it does says the player transforms, turns into or becomes
    something. A bare 'form' in what it does is not enough (live 2026-11-02: 'Dissolve: touch
    dissolves wooden crates to spawn coins or a form' got a form row that pushed out the dash)."""
    return bool(re.search(r"\bform\b|transform|morph", name, re.IGNORECASE)
                or re.search(r"transforms?\b|turns? into|becomes?\b|morphs?\b", does, re.IGNORECASE))  # fmt: skip


MATERIAL_HEX = {
    "acid": "#7CE04A", "poison": "#7CE04A", "toxic": "#7CE04A", "slime": "#7CE04A",
    "metal": "#A8B0BC", "steel": "#A8B0BC", "iron": "#A8B0BC", "silver": "#C8CCD4",
    "fire": "#F07828", "flame": "#F07828", "lava": "#E05028", "magma": "#E05028",
    "ice": "#9CDCF4", "frost": "#9CDCF4", "water": "#4A9CE0", "stone": "#8C8070",
    "rock": "#8C8070", "gold": "#E0B030", "shadow": "#503C78", "ghost": "#C8D0F0",
    "electric": "#F0E050", "lightning": "#F0E050", "wind": "#D0F0E8", "earth": "#8C6A48",
}  # fmt: skip


MATERIAL_LOOK = {
    "acid": "bright green, glossy, dripping acid", "poison": "sickly green, dripping",
    "metal": "grey metal with a bright shine", "steel": "grey steel with a bright shine",
    "iron": "dark grey iron with a dull shine", "fire": "orange and red, flickering flames",
    "lava": "glowing orange-red with dark crust", "ice": "pale blue ice, glinting",
    "stone": "grey stone, rough", "gold": "shiny gold", "electric": "yellow, crackling sparks",
    "water": "clear blue water, wobbling", "shadow": "dark purple, smoky",
}  # fmt: skip


def material_look(text: str) -> str:
    return next((MATERIAL_LOOK[w] for w in re.findall(r"[a-z]+", text.lower()) if w in MATERIAL_LOOK), "")


def hex_of(text: str, fallback: str = "#FFFFFF") -> str:
    """The first colour `text` names, else the first material it names (acid is green, metal
    grey), else `fallback`."""
    from .art import look_palette  # art imports this module: import at call time

    colours = look_palette(text)
    if colours:
        return colours[0]
    return next((MATERIAL_HEX[w] for w in re.findall(r"[a-z]+", text.lower()) if w in MATERIAL_HEX),
                fallback)  # fmt: skip


# --- the offline Art Director ----------------------------------------------------------------------


def entity_for(name: str, design: GameDesign) -> str | None:
    """The design's entity for a plan row's 'entity' ('the player', 'enemy', 'crate'...)."""
    from .levels import match_entity

    ids = {e.id for e in design.entities}
    n = snake(name)
    if n in ids:
        return n
    if n in {"player", "hero", "the_player"}:
        return next(e.id for e in design.entities if e.role == "player")
    found = match_entity(name, "", design)
    if found:
        return found
    roles = [e.id for e in design.entities if e.role == n.removesuffix("s")]
    return roles[0] if len(roles) == 1 else None


def default_rows(e: Entity, breaks: str = "") -> list[MotionRow]:
    """Rows from the design's animation names and the vocabulary; `breaks`: 'break' or 'die'
    when the levels open this entity."""
    if e.role == "background":
        return []
    names = list(dict.fromkeys(["idle", *e.animations]))
    if e.role in {"player", "enemy"} and not any(plays_of(n) == "move" for n in names):
        names.insert(1, "hop" if is_blob(e) else "walk")  # what moves needs a move row
    if e.role == "tile":
        names = [n for n in names if plays_of(n) == "break"]
    if e.role == "pickup" and not any(plays_of(n) == "pickup" for n in names):
        names = names[:1]
    if breaks and not any(plays_of(n) in GONE for n in names):
        names.append(breaks)
    rows = []
    for name in names:
        plays = plays_of(name)
        if e.role == "pickup" and name == "idle":
            plays = "pickup"
        poses = book_poses(plays, e, name)
        if plays == "idle" and e.role in {"tile", "prop"}:
            poses = poses[:1]
        rows.append(MotionRow(entity=e.id, name=snake(name), plays=plays, form="",
                              frames=list(poses[:MAX_FRAMES]), fps=FPS[plays],
                              loop=plays not in ONCE))  # fmt: skip
    return rows


def form_rows(design: GameDesign, plan: PlanRecord | None) -> tuple[list[MotionRow], list[FormLook]]:
    player = next(e for e in design.entities if e.role == "player")
    rows, forms = [], []
    for a in plan.plan.abilities if plan else []:
        if not is_form_ability(a.name, a.does):
            continue
        looks = material_look(a.name) or material_look(a.does) or f"changed by {a.name}: {a.does}"
        name = snake(a.name)
        poses = [P(f"{p.shows}, {looks}", p.airborne) for p in book_poses("form", player)]
        rows.append(MotionRow(entity=player.id, name=name, plays="form", form=a.name,
                              frames=poses, fps=FPS["form"], loop=True))  # fmt: skip
        tint = hex_of(f"{a.name} {a.does}", "#C0C0C8")
        forms.append(FormLook(ability=a.name, looks=looks, tint=tint, particles=tint))
    return rows, forms


def plan_rows(design: GameDesign, plan: PlanRecord | None) -> list[MotionRow]:
    """The plan's animation list as rows (poses from its 'shows')."""
    out = []
    for group in plan.plan.animations if plan else []:
        eid = entity_for(group.entity, design)
        if eid is None:
            continue
        e = next(x for x in design.entities if x.id == eid)
        for r in group.rows:
            plays = plays_of(r.name)
            n = max(1, min(MAX_FRAMES, r.frames))
            shows = sentences_of(r.shows, n)
            book = book_poses(plays, e, r.name)
            poses = [P(s, bool(AIR_WORDS.search(s)) or (book[i % len(book)].airborne and plays != "move"))
                     for i, s in enumerate(shows)]  # fmt: skip
            out.append(MotionRow(entity=eid, name=snake(r.name), plays=plays, form="",
                                 frames=poses, fps=FPS[plays], loop=r.loop and plays not in ONCE))  # fmt: skip
    return out


def effect(name: str, on: str, colors: list[str] | None = None) -> Effect:
    values = dict(DEFAULT_FX.get(on, DEFAULT_FX["pickup"]))
    if colors:
        values["colors"] = colors
    return Effect(name=name, on=on, **values)


def moment_of(text: str) -> str:
    return next((m for m, pattern in MOMENT_WORDS if re.search(pattern, text, re.IGNORECASE)), "game")


def plan_effects(plan: PlanRecord | None) -> list[Effect]:
    out = []
    from .art import look_palette

    for f in plan.plan.feel if plan else []:
        on = moment_of(f.event)
        if on == "game":
            on = moment_of(f.see)
        out.append(effect(snake(f.event), on, look_palette(f.see)[:3] or None))
    return out


def fallback_brief(design: GameDesign, plan: PlanRecord | None = None,
                   breaks: dict[str, str] | None = None) -> AnimationBrief:  # fmt: skip
    """The offline Art Director: the plan's rows (else the design's animation names), a form row
    per form ability, effects from the plan's feel; `fit_brief` adds what is still missing."""
    rows = plan_rows(design, plan)
    extra, forms = form_rows(design, plan)
    have = {r.entity for r in rows}
    for e in design.entities:
        if e.id not in have:
            rows += default_rows(e, (breaks or {}).get(e.id, ""))
    return AnimationBrief(rows=rows + extra, forms=forms, effects=plan_effects(plan))


# --- code: repairs and checks -----------------------------------------------------------------------


def fit_brief(brief: AnimationBrief, design: GameDesign, plan: PlanRecord | None = None,
              breaks: dict[str, str] | None = None) -> tuple[AnimationBrief, list[str]]:  # fmt: skip
    """Repairs code can make (see the module doc); returns the brief and notes on changes.
    `breaks`: entity -> 'break' or 'die' for what the levels open (crates, walls, enemies)."""
    from .levels import match_ability, plan_abilities

    notes, breaks = [], breaks or {}
    entities = {e.id: e for e in design.entities}
    abilities = plan_abilities(plan) if plan else {}
    forms_by_key = {}
    for f in brief.forms:
        name = match_ability(f.ability, abilities) if abilities else f.ability
        if not name:
            notes.append(f"form {f.ability!r} is not one of the plan's abilities: dropped")
            continue
        tint = f.tint if HEX.match(f.tint or "") else hex_of(f.looks, "#C0C0C8")
        if tint != f.tint:
            notes.append(f"form {name}: tint {f.tint!r} is not #RRGGBB: {tint}")
        particles = f.particles if HEX.match(f.particles or "") else ""
        forms_by_key.setdefault(key(name), f.model_copy(update={"ability": name, "tint": tint.upper(),
                                                               "particles": particles.upper()}))  # fmt: skip
    rows: dict[str, list[MotionRow]] = {}
    player = next(e.id for e in design.entities if e.role == "player")
    for r in brief.rows:
        eid = entity_for(r.entity, design)
        if eid is None:
            notes.append(f"row {r.name!r} of unknown entity {r.entity!r}: dropped")
            continue
        e = entities[eid]
        name = snake(r.name)
        plays = r.plays
        form = ""
        if plays == "form" or (r.form.strip() and eid == player):
            form = match_ability(r.form or r.name, abilities) if abilities else r.form
            if not form or eid != player:
                notes.append(f"{eid}: form row {name!r} names no ability of the player: dropped")
                continue
            plays = "form"
        if e.role == "background" or (e.role == "tile" and plays != "break"):
            notes.append(f"{eid}: a {e.role} keeps only break rows: {name!r} dropped")
            continue
        if any(x.name == name for x in rows.get(eid, [])):
            notes.append(f"{eid}: second row {name!r} dropped")
            continue
        frames = [f for f in r.frames if f.shows.strip()][:MAX_FRAMES]
        if not frames:
            frames = list(book_poses(plays, e, name)[:MAX_FRAMES])
            notes.append(f"{eid}.{name}: no frames: poses from the vocabulary")
        if len(r.frames) > MAX_FRAMES:
            notes.append(f"{eid}.{name}: {len(r.frames)} frames trimmed to {MAX_FRAMES}")
        if plays in GONE and not re.search(r"gone|empty|nothing|vanish|disappear", frames[-1].shows, re.IGNORECASE):
            gone = P("gone: only a few tiny bits left")
            frames = (frames + [gone]) if len(frames) < MAX_FRAMES else frames[:-1] + [gone]
            notes.append(f"{eid}.{name}: a {plays} row ends with nothing left (last frame: gone)")
        fps = max(2, min(20, r.fps))
        loop = r.loop and plays not in ONCE
        if loop != r.loop:
            notes.append(f"{eid}.{name}: a {plays} row plays once")
        rows.setdefault(eid, []).append(r.model_copy(update={
            "entity": eid, "name": name, "plays": plays, "form": form, "frames": frames,
            "fps": fps, "loop": loop}))  # fmt: skip
    # What the game needs, added from the vocabulary when the brief forgot it.
    for e in design.entities:
        own = rows.setdefault(e.id, [])
        kinds = {r.plays for r in own}
        if e.role != "background" and e.role != "tile" and "idle" not in kinds and "pickup" not in kinds:
            first = default_rows(e)[:1]
            own[:0] = first
            notes.append(f"{e.id}: no idle row: added from the vocabulary")
        wanted = breaks.get(e.id)
        if wanted and not kinds & GONE:
            own += [r for r in default_rows(e, wanted) if r.plays in GONE]
            notes.append(f"{e.id}: the levels {'beat' if wanted == 'die' else 'break'} it: "
                         f"a {wanted} row added")  # fmt: skip
        if e.role == "enemy" and "move" not in kinds:
            own.insert(1, next(r for r in default_rows(e) if r.plays == "move"))
            notes.append(f"{e.id}: no move row (it patrols): added from the vocabulary")
        if e.id == player and design.camera == "side-view" and plan and plan.kinematics:
            needs = ("move", "jump") + (("dash",) if plan.plan.movement.dash_distance > 0 else ())
            for need in needs:
                if need not in kinds:
                    own.append(MotionRow(entity=e.id, name="hop" if need == "move" and is_blob(e) else need,
                                         plays=need, form="", frames=list(book_poses(need, e, need)),
                                         fps=FPS[need], loop=need not in ONCE))  # fmt: skip
                    notes.append(f"{e.id}: no {need} row: added from the vocabulary")
    for a in plan.plan.abilities if plan else []:
        if not is_form_ability(a.name, a.does):
            continue
        k = key(a.name)
        if k not in forms_by_key:
            tint = hex_of(f"{a.name} {a.does}", "#C0C0C8")
            looks = material_look(a.name) or material_look(a.does) or f"changed by {a.name}: {a.does}"
            forms_by_key[k] = FormLook(ability=a.name, looks=looks, tint=tint, particles=tint)
            notes.append(f"form {a.name}: no look given: tinted {tint}")
        if not any(r.plays == "form" and key(r.form) == k for r in rows[player]):
            extra, _ = form_rows(design, plan)
            rows[player] += [r for r in extra if key(r.form) == k]
            notes.append(f"{player}: no row for {a.name}: added from the vocabulary")
    ordered = []
    for e in design.entities:
        own = sorted(rows.get(e.id, []), key=lambda r: ORDER.index(r.plays))
        if len(own) > MAX_ROWS:
            notes.append(f"{e.id}: {len(own)} rows, the sheet holds {MAX_ROWS}: "
                         + ", ".join(r.name for r in own[MAX_ROWS:]) + " dropped")
            own = own[:MAX_ROWS]
        ordered += own
    effects, seen = [], set()
    for x in brief.effects:
        name = snake(x.name)
        if name in seen:
            continue
        seen.add(name)
        colors = [c.upper() for c in x.colors if HEX.match(c)][:4] or DEFAULT_FX.get(x.on, DEFAULT_FX["pickup"])["colors"]
        effects.append(x.model_copy(update={
            "name": name, "colors": colors, "particles": max(0, min(30, x.particles)),
            "speed": max(0.0, min(300.0, x.speed)), "flash": x.flash.upper() if HEX.match(x.flash or "") else "",
            "shake": max(0.0, min(1.0, x.shake)), "squash": max(-0.4, min(0.4, x.squash)),
            "seconds": max(0.1, min(1.0, x.seconds))}))  # fmt: skip
    moments = {x.on for x in effects}
    needed = ["break", "kill", "pickup", "hurt"]
    if design.camera == "side-view":
        needed = ["jump", "land", *needed] + (["dash"] if plan and plan.plan.movement.dash_distance else [])
    if forms_by_key:
        needed.append("transform")
    for on in needed:
        if on not in moments:
            effects.append(effect(on, on))
            notes.append(f"no effect for {on}: the default added")
    return AnimationBrief(rows=ordered, forms=list(forms_by_key.values()), effects=effects), notes


def check_brief(brief: AnimationBrief, design: GameDesign, plan: PlanRecord | None = None) -> list[str]:
    """What a fitted brief still lacks (shown with it; nothing here blocks the build)."""
    problems = []
    player = next(e for e in design.entities if e.role == "player")
    own = [r for r in brief.rows if r.entity == player.id]
    move = [r for r in own if r.plays == "move"]
    if not move:
        problems.append(f"{player.id} has no move row: it would slide")
    elif all(len(r.frames) < 2 for r in move):
        problems.append(f"{player.id}'s move row has one frame: it would slide")
    for r in brief.rows:
        if r.plays in MOVING and len(r.frames) >= 2 and len({key(f.shows) for f in r.frames}) == 1:
            problems.append(f"{r.entity}.{r.name}: every frame shows the same pose")
    return problems


def make_motion(brief: AnimationBrief, design: GameDesign, plan: PlanRecord | None = None,
                breaks: dict[str, str] | None = None, notes: list[str] = ()) -> MotionRecord:  # fmt: skip
    fitted, repairs = fit_brief(brief, design, plan, breaks)
    return MotionRecord(brief=fitted, problems=check_brief(fitted, design, plan),
                        notes=[*notes, *repairs], entities=[e.id for e in design.entities])  # fmt: skip


def keep_rows(old: MotionRecord, new: AnimationBrief, design: GameDesign) -> AnimationBrief:
    """After a design change every entity that still exists keeps its rows (its locked art is
    not redrawn); only new entities take the new brief's rows."""
    kept = {r.entity for r in old.brief.rows}
    ids = {e.id for e in design.entities}
    rows = [r for r in old.brief.rows if r.entity in ids]
    rows += [r for r in new.rows if r.entity not in kept]
    forms = {key(f.ability): f for f in new.forms} | {key(f.ability): f for f in old.brief.forms}
    effects = {x.name: x for x in new.effects} | {x.name: x for x in old.brief.effects}
    return AnimationBrief(rows=rows, forms=list(forms.values()), effects=list(effects.values()))


def breaks_of(levels: list) -> dict[str, str]:
    """Entity -> 'break' or 'die' for what the levels open (crates, breakables, enemies)."""
    out = {}
    for r in levels:
        for e in r.spec.legend:
            if e.entity and e.kind in {"crate", "breakable"}:
                out.setdefault(e.entity, "break")
            elif e.entity and e.kind == "enemy" and e.opened_by != "never":
                out.setdefault(e.entity, "die")
    return out


def breaks_from_design(design: GameDesign) -> dict[str, str]:
    """Without levels: enemies die; props and tiles whose behaviour breaks them break."""
    out = {}
    for e in design.entities:
        if e.role == "enemy":
            out[e.id] = "die"
        elif e.role in {"prop", "tile"} and re.search(r"break|crumble|dissolve|smash|destroy|shatter",
                                                      e.behaviour, re.IGNORECASE):  # fmt: skip
            out[e.id] = "break"
    return out


# --- prompts --------------------------------------------------------------------------------------


def brief_prompt(design: GameDesign, plan: PlanRecord | None, breaks: dict[str, str]) -> str:
    entities = "\n".join(
        f"* `{e.id}` ({e.role}, {e.width}x{e.height}): {e.look}. Does: {e.behaviour}. "
        f"Design animations: {', '.join(e.animations) or '-'}"
        + (f". The levels {'beat' if breaks[e.id] == 'die' else 'break'} it." if e.id in breaks else "")
        for e in design.entities if e.role != "background"
    )  # fmt: skip
    p = plan.plan if plan else None
    planned = "\n".join(f"* {a.entity}: " + "; ".join(
        f"{r.name} ({r.frames} frames, {'loop' if r.loop else 'once'}: {r.shows})" for r in a.rows)
        for a in (p.animations if p else [])) or "- (none)"  # fmt: skip
    feel = "\n".join(f"* {f.event}: {f.see}" for f in (p.feel if p else [])) or "- (none)"
    abilities = "\n".join(f"* {a.name}: {a.does}" for a in (p.abilities if p else [])) or "- (none)"
    return f"""Design the motion of this game's art: how each thing moves and reacts, so an image
model can draw every frame and the game engine can play it. Things must move like what they are:
a slime hops and squashes (it never slides), a crate breaks apart, a wall crumbles, an enemy is
beaten and gone, a transformed player looks transformed for as long as the form lasts.

* rows: per entity (its design id), the animation rows, each with 1-{MAX_FRAMES} frames saying
  concretely what the frame shows (a hop: squashed low and wide; stretched tall springing up;
  round in the air; squashed wide landing) and whether the subject is off the ground in it;
  `plays` says when the game shows the row; fps 4-16; loop for idle and movement, once for
  jump, land, hurt, break and die. At most {MAX_ROWS} rows per entity; idle first.
* Every moving row's frames must differ visibly (a row of one repeated pose makes the sprite
  slide). Frames keep the subject's size and stand on the same ground line, except airborne
  frames. A break or die row ends with the thing gone (the last frame: only tiny bits left).
* Tiles have only a break row (when the levels break them). Pickups have one shine row.
* forms: for each ability that changes how the player looks (a form or transformation): how
  it looks (colours, material, drips or shine) and a `plays: form` row of the player moving
  in that look (the game shows it for as long as the form lasts), plus a #RRGGBB tint and
  particle colour the engine uses if the row cannot be drawn.
* effects: one per feel event below: when the engine plays it (`on`), particles, colours,
  flash, screen shake and the player's squash (+ flat, - tall). Jump stretches, landing
  squashes.
No text, no story, no names beyond the design's.

Game: {design.title}. {design.pitch}
Camera: {design.camera}. Look: {design.look}

Entities:
{entities}

The game plan's animations:
{planned}

The game plan's feel:
{feel}

Abilities:
{abilities}"""


def motion_markdown(record: MotionRecord) -> str:
    b = record.brief
    rows = "\n".join(
        f"| `{r.entity}` | {r.name} | {r.plays}{f' ({r.form})' if r.form else ''} | "
        + "; ".join(f"{i + 1}. {f.shows}{' (in the air)' if f.airborne else ''}" for i, f in enumerate(r.frames))
        + f" | {r.fps} | {'loop' if r.loop else 'once'} |" for r in b.rows)  # fmt: skip
    forms = "\n".join(f"* **{f.ability}**: {f.looks} (tint {f.tint}"
                      + (f", particles {f.particles})" if f.particles else ")") for f in b.forms)  # fmt: skip
    fx = "\n".join(
        f"| {x.name} | {x.on} | {x.particles} | {', '.join(x.colors)} | {x.flash or '-'} | "
        f"{x.shake:g} | {x.squash:g} | {x.seconds:g} s |" for x in b.effects)  # fmt: skip
    return (
        "# Motion design\n\n## Rows\n| entity | row | plays | frames | fps | |\n|---|---|---|---|---|---|\n"
        f"{rows}\n\n## Forms\n{forms or '- (none)'}\n\n## Effects (`sf.fx`)\n"
        "| effect | on | particles | colours | flash | shake | squash | lasts |\n"
        f"|---|---|---|---|---|---|---|---|\n{fx}\n"
        + ("\n## Problems code found\n" + "\n".join(f"* {p}" for p in record.problems) + "\n"
           if record.problems else "")
        + "".join(f"\n> repaired: {n}" for n in record.notes) + "\n"
    )  # fmt: skip


# --- frames: checks and repairs by code ---------------------------------------------------------

STILL = 0.04  # share of the subject's pixels that must change between frames of a moving row
SIZE_RANGE = (0.5, 2.0)  # opaque area against the row's median
EMPTY = 0.1  # a gone frame keeps at most this share of the first frame's pixels


class RowCheck(BaseModel):
    entity: str
    row: str
    plays: str
    frames: int
    problems: list[str] = Field(default_factory=list)
    repaired: list[str] = Field(default_factory=list)
    form_fallback: bool = False  # a form row that failed: the SDK tints instead


def mask(frame: Image.Image) -> np.ndarray:
    return np.asarray(frame.convert("RGBA"))[..., 3] > 0


def change(a: Image.Image, b: Image.Image) -> float:
    """Share of the two frames' drawn pixels that differ (shape or colour)."""
    x, y = np.asarray(a.convert("RGBA")).astype(int), np.asarray(b.convert("RGBA")).astype(int)
    if x.shape != y.shape:
        return 1.0
    drawn = (x[..., 3] > 0) | (y[..., 3] > 0)
    if not drawn.any():
        return 0.0
    differ = ((x[..., 3] > 0) != (y[..., 3] > 0)) | (np.abs(x[..., :3] - y[..., :3]).sum(-1) > 24)
    return float((differ & drawn).sum() / drawn.sum())


def ground(frame: Image.Image) -> int | None:
    rows = np.where(mask(frame).any(1))[0]
    return int(rows[-1]) if len(rows) else None


def row_problems(frames: list[Image.Image], row: MotionRow, role: str) -> list[str]:
    """What is wrong with one drawn row, as sentences (for the retry prompt and the report)."""
    if not frames:
        return ["no frames"]
    areas = [int(mask(f).sum()) for f in frames]
    where = f"the {row.name} row"
    if not areas[0]:
        return [f"{where}: its first frame is empty"]
    problems = []
    n = len(frames)
    if row.plays in MOVING and n >= 2:
        moves = [change(frames[i], frames[i + 1]) for i in range(n - 1)]
        if max(moves) < STILL:
            problems.append(f"{where}: its {n} frames are the same picture, so the sprite would "
                            "slide; draw " + "; ".join(f.shows for f in row.frames))  # fmt: skip
    body = [a for a, p in zip(areas, range(n), strict=True)
            if row.plays not in GONE or p < n - 1]  # fmt: skip
    median = float(np.median(body)) if body else 0
    if median and row.plays not in GONE:
        odd = [i + 1 for i, a in enumerate(areas) if not SIZE_RANGE[0] <= a / median <= SIZE_RANGE[1]]
        if odd:
            problems.append(f"{where}: frame(s) {', '.join(map(str, odd))} change the subject's size")
    if role in {"player", "enemy", "prop"} and row.plays not in GONE:
        feet = [ground(f) for f, p in zip(frames, row.frames + row.frames[-1:] * n, strict=False)
                if not p.airborne]  # fmt: skip
        feet = [g for g in feet if g is not None]
        if feet and max(feet) - min(feet) > 1:
            problems.append(f"{where}: the frames do not stand on one ground line")
    if row.loop and n >= 3 and row.plays in MOVING:
        steps = [change(frames[i], frames[i + 1]) for i in range(n - 1)]
        seam = change(frames[-1], frames[0])
        if seam > max(0.25, 1.5 * float(np.median(steps))):
            problems.append(f"{where}: the last frame does not lead back to the first (the loop jumps)")
    if row.plays in GONE and areas[-1] > EMPTY * areas[0]:
        problems.append(f"{where}: it does not end empty")
    return problems


def squash_frames(frame: Image.Image, n: int) -> list[Image.Image]:
    """A still moving row made to move by code: squash, rest, stretch, rest (bottom anchored)."""
    w, h = frame.size
    box = frame.getchannel("A").getbbox()
    if not box:
        return [frame.copy() for _ in range(n)]
    subject = frame.crop(box)
    sw, sh = subject.size
    out = []
    for kx, ky in [(1.15, 0.82), (1.0, 1.0), (0.88, 1.12), (1.0, 1.0)][:n] + [(1.0, 1.0)] * max(0, n - 4):
        tw, th = max(1, min(w, round(sw * kx))), max(1, min(h, round(sh * ky)))
        part = subject.resize((tw, th), Image.Resampling.NEAREST)
        cell = Image.new("RGBA", (w, h))
        cell.alpha_composite(part, ((w - tw) // 2, h - th if box[3] >= h - 1 else max(0, box[3] - th)))
        out.append(cell)
    return out


def crumble_frames(frame: Image.Image, n: int = 4) -> list[Image.Image]:
    """A break row made by code from the art itself: it cracks, falls apart in chunks that drop
    and thin out, and the last frame is empty."""
    rgba = np.asarray(frame.convert("RGBA"))
    h, w = rgba.shape[:2]
    chunk = max(2, min(w, h) // 4)
    rng = np.random.default_rng(7)
    out = [frame.copy()]
    cracked = rgba.copy()
    for c in range(chunk, w, chunk):
        cracked[:, c - 1 : c, :3] = (cracked[:, c - 1 : c, :3] * 0.45).astype(np.uint8)
    for r in range(chunk, h, chunk):
        cracked[r - 1 : r, :, :3] = (cracked[r - 1 : r, :, :3] * 0.45).astype(np.uint8)
    out[0] = Image.fromarray(cracked)
    pieces = [(x, y) for y in range(0, h, chunk) for x in range(0, w, chunk)]
    drift = {p: (rng.integers(-2, 3), rng.integers(0, 3)) for p in pieces}
    for step in range(1, max(1, n - 1)):
        cell = np.zeros_like(rgba)
        keep = 1 - step / max(1, n - 1)
        for i, (x, y) in enumerate(pieces):
            if rng.random() > keep + 0.25:
                continue
            dx, dy = drift[(x, y)]
            nx, ny = x + dx * step * 2, y + (dy + step) * step * 2
            part = cracked[y : y + chunk, x : x + chunk]
            ph, pw = part.shape[:2]
            if 0 <= nx and nx + pw <= w and 0 <= ny and ny + ph <= h:
                cell[ny : ny + ph, nx : nx + pw] = np.where(part[..., 3:] > 0, part, cell[ny : ny + ph, nx : nx + pw])
        out.append(Image.fromarray(cell))
    out = out[: n - 1] + [Image.new("RGBA", (w, h))]
    return out[:n] if n > 1 else [Image.new("RGBA", (w, h))]


def finish_rows(frames: list[Image.Image], rows: list[MotionRow], role: str, counts: list[int]) -> tuple[list[Image.Image], list[RowCheck]]:
    """Check every row of a drawn strip (rows laid out one after another, `counts` frames each)
    and repair what code can. Returns the frames (same layout) and a check per row."""
    out, checks, at = list(frames), [], 0
    for row, n in zip(rows, counts, strict=True):
        part = out[at : at + n]
        found = row_problems(part, row, role)
        check = RowCheck(entity=row.entity, row=row.name, plays=row.plays, frames=n, problems=found)
        if any("same picture" in p for p in found) and row.plays in MOVING:
            part = squash_frames(part[0], n)
            check.repaired.append("squash and stretch made by code from its first frame")
        if row.plays in GONE and any("does not end empty" in p for p in found):
            part = part[:-1] + [Image.new("RGBA", part[-1].size)]
            check.repaired.append("the last frame emptied by code")
        if row.plays == "form" and any("empty" in p or "size" in p for p in found):
            check.form_fallback = True
            check.repaired.append("the SDK shows the form as a tint with particles")
        out[at : at + n] = part
        checks.append(check)
        at += n
    return out, checks


# --- previews: the rows for the reviewer and the human --------------------------------------------

PREVIEW_ZOOM = 4
LABEL_H = 12


def _frames(png_bytes: bytes, frame: tuple[int, int]) -> list[Image.Image]:
    strip = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    w, h = frame
    return [strip.crop((i * w, 0, (i + 1) * w, h)) for i in range(max(1, strip.width // w))]


def strips(art: dict[str, tuple[bytes, dict]]) -> list[tuple[str, str, list[Image.Image], dict]]:
    """(entity, row, frames, row meta) for every sprite row with more than one frame.
    `art`: entity -> (strip png, sprite meta)."""
    out = []
    for eid, (data, meta) in art.items():
        if "frame" not in meta:
            continue
        frames = _frames(data, tuple(meta["frame"]))
        for name, (start, count) in meta.get("anims", {}).items():
            info = (meta.get("rows") or {}).get(name)
            if info is None or count < 2:
                continue
            out.append((eid, name, frames[start : start + count], info))
    return out


def filmstrips(art: dict[str, tuple[bytes, dict]], zoom: int = PREVIEW_ZOOM) -> bytes | None:
    """Every animated row as a labelled filmstrip (frames left to right) at `zoom`x on grey."""
    rows = strips(art)
    if not rows:
        return None
    cell_w = max(f.width for _, _, fr, _ in rows for f in fr) * zoom + 8
    cell_h = max(f.height for _, _, fr, _ in rows for f in fr) * zoom + 8
    cols = max(len(fr) for _, _, fr, _ in rows)
    sheet = Image.new("RGBA", (cols * cell_w + 8, len(rows) * (cell_h + LABEL_H) + 8), (92, 96, 112, 255))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=10)
    for r, (eid, name, frames, info) in enumerate(rows):
        y = 4 + r * (cell_h + LABEL_H)
        draw.text((6, y), f"{eid}.{name} ({info.get('plays', '')}, {info.get('fps', 8)} fps, "
                          f"{'loop' if info.get('loop') else 'once'})", fill=(255, 255, 255, 255), font=font)  # fmt: skip
        for c, f in enumerate(frames):
            big = f.resize((f.width * zoom, f.height * zoom), Image.Resampling.NEAREST)
            draw.rectangle((4 + c * cell_w, y + LABEL_H, 4 + (c + 1) * cell_w - 4, y + LABEL_H + cell_h - 4),
                           outline=(70, 74, 88, 255))  # fmt: skip
            sheet.alpha_composite(big, (8 + c * cell_w, y + LABEL_H + cell_h - 6 - big.height))
    out = io.BytesIO()
    sheet.save(out, format="PNG")
    return out.getvalue()


def motion_gif(art: dict[str, tuple[bytes, dict]], zoom: int = 3, seconds: float = 2.0) -> bytes | None:
    """Every animated row playing at its own fps (once rows play, then show empty until the
    loop restarts), side by side, as an animated GIF for G4 (a browser plays it)."""
    rows = strips(art)
    if not rows:
        return None
    cw = max(f.width for _, _, fr, _ in rows for f in fr) * zoom + 10
    ch = max(f.height for _, _, fr, _ in rows for f in fr) * zoom + 10 + LABEL_H
    cols = min(6, len(rows))
    lines = math.ceil(len(rows) / cols)
    tick = 1 / 24
    font = ImageFont.load_default(size=9)
    pictures = []
    for t in range(round(seconds / tick)):
        canvas = Image.new("RGB", (cols * cw, lines * ch), (92, 96, 112))
        draw = ImageDraw.Draw(canvas)
        for i, (eid, name, frames, info) in enumerate(rows):
            x, y = (i % cols) * cw, (i // cols) * ch
            draw.text((x + 4, y + 2), f"{eid}.{name}"[:22], fill=(255, 255, 255), font=font)
            k = int(t * tick * info.get("fps", 8))
            f = frames[k % len(frames)] if info.get("loop") else (frames[k] if k < len(frames) else None)
            if f is None:
                continue
            big = f.resize((f.width * zoom, f.height * zoom), Image.Resampling.NEAREST)
            lift = 0
            if info.get("lift"):
                lift = info["lift"][(k % len(frames)) if info.get("loop") else min(k, len(frames) - 1)] * zoom
            canvas.paste(big, (x + (cw - big.width) // 2, y + ch - 5 - big.height - lift), big)
        pictures.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=128))
    out = io.BytesIO()
    pictures[0].save(out, format="GIF", save_all=True, append_images=pictures[1:],
                     duration=round(tick * 1000), loop=0, disposal=2)  # fmt: skip
    return out.getvalue()
