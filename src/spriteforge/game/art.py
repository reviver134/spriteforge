"""Art for a generated game: library first, otherwise Muse Image, decided per design entity.

The Art Director (Muse Spark) proposes an `ArtPlan`, one pick per entity: a library actor
(animated character), a whole library object, a tile from an installed tile set, a parallax
backdrop, or `muse`. Code enforces the rule, the model only proposes:

* a library pick must exist, ship under its licence and match the game's camera; any entity
  without a valid library pick (none named, wrong camera, not shippable, unknown id) is sent
  to **Muse Image** automatically, with a prompt from the design and an AI-allowed library
  asset as style reference (never an `ai_allowed: false` pack; text-only if none exists);
* there is no "placeholder" choice: greybox art appears only when the per-build image-call cap
  is reached or Muse fails, and each one is reported as a warning;
* every Muse request renders an `ArtSpec` built by code: view, exact pixel grid, size against
  the hero, anchor, facing, light, outline and palette, sent at the verified image size closest
  to the subject's aspect ratio;
* Muse output is keyed and read back cell by cell when it was drawn on a big-pixel grid
  (`pixel.grid`), else cropped and mode-filtered to the entity's size; then snapped to its own
  16 main colours. `check_frame` catches wrong aspect, fill, empty images and seams; one retry
  at high reasoning strength fixes them, or the frame is kept with a warning. The library
  reference steers Muse's style. Unchanged requests reuse earlier Muse art (locked) and
  changed ones become edits of the previous image.

Nothing here talks to a model directly: callers pass `generate(request, edit) -> png bytes`.
"""

import colorsys
import io
import math
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import Field

from spriteforge.agents.models import Contract
from spriteforge.library.looks import (
    FLAT,
    LUMA,
    asset_image,
    colour_gap,
    detail_word,
    measure,
    rgb_of,
)
from spriteforge.library.store import (
    contact_sheet,
    library_root,
    load_packs,
    search,
    tile_descriptions,
)
from spriteforge.pixel.grid import detect_cells, sample_cells
from spriteforge.pixel.palette import extract_palette, quantize
from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import mode_resize, prepare_source
from spriteforge.providers.base import ImageRequest

from .actors import (
    COLORS,
    NOISE,
    VERBS,
    Actor,
    adapt,
    canon,
    dominant_hue,
    face_right,
    load_actors,
    recolor,
    words,
)
from .design import Entity, GameDesign
from .image_cache import ImageCache
from .tilesets import backdrop_layers, load_tilesets, pick_variant, tile_ids, tile_image

MAX_OBJECT_PX = 128
SQUARE = (1024, 1024)  # the size every config verifies; others come from `image.sizes`
PERSPECTIVE_TAGS = {"side": {"side-view", "side", "platformer"}, "topdown": {"top-down"}}
CAMERA_TAGS = {"side-view": PERSPECTIVE_TAGS["side"], "top-down": PERSPECTIVE_TAGS["topdown"]}
# Muse calls go to what the player needs to read the game first.
PRIORITY = ["player", "enemy", "pickup", "tile", "prop", "projectile", "background"]
Source = Literal["actor", "object", "tile", "backdrop", "muse"]
# A hero chosen only because it exists (no name match): Muse may draw the described one instead.
GENERIC_HERO = "library hero for this camera"
# Pick reasons that mark an explicit redraw (G4 feedback, an art-review fix): such a Muse pick
# stays Muse even when the library has the creature by name.
REVIEW_REASON = "art review:"
MUSE_CREDIT = "Other art generated with Muse Image for this game."
EXPLICIT_REDRAW = ("redrawn per feedback", REVIEW_REASON)


class ArtPick(Contract):
    entity: str = Field(description="entity id from the design")
    source: Source
    asset: str = Field(
        description="actor key, object id, '<tileset>/<tile>' or tileset id for backdrop; "
        "'' for muse"
    )
    prompt: str = Field(description="muse only: what to draw (subject, colours, materials)")
    reference: str = Field(description="muse only: AI-allowed library asset id; '' = automatic")
    recolor: str = Field(
        description="library art only (actor, object or tile): the colour to recolour it to, "
        "e.g. 'teal' or 'dark grey', when the library art fits but its colour does not; or ''"
    )
    reason: str


class ArtPlan(Contract):
    variant: str = Field(description="tile set variant (normal, autumn, winter, ...)")
    picks: list[ArtPick]


@dataclass
class Filled:
    png: bytes
    source: (
        str  # "library <id>" | "tileset <id>" | "muse" | "muse (cached)" | "placeholder"
        # | "muse (locked, from vN)"
    )
    reasons: list[str]
    meta: dict  # assets.json entry (minus file)
    kind: str = "sprite"  # sprite | image
    record: dict = field(default_factory=dict)  # Muse request + raw bytes, for locking/edits
    extra: dict[str, tuple[bytes, dict]] = field(default_factory=dict)  # backdrop layers


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# --- what the library offers -----------------------------------------------------------------


def whole_objects() -> dict[str, dict]:
    """Shippable library assets that are one object: a sprite or a single-row strip."""
    out = {}
    for a in search(usage="ship-with-credit"):
        if a["category"] in {"character", "enemy", "tileset", "other", "background"}:
            continue
        cw, ch = a["cell"]
        if a["size"][1] != ch or max(cw, ch) > MAX_OBJECT_PX or a["frames"] > 12:
            continue
        if not a.get("transparent_background", True):
            continue
        out[a["id"]] = a
    return out


def reference_assets() -> dict[str, dict]:
    """Assets Muse may see as a style example: AI-allowed packs that may ship."""
    return {a["id"]: a for a in search(usage="ship-with-credit", ai=True)}


def asset_frames(asset: dict) -> list[Image.Image]:
    sheet = Image.open(library_root() / asset["file"]).convert("RGBA")
    w, h = asset["cell"]
    frames = [sheet.crop((x, 0, x + w, h)) for x in range(0, sheet.width - w + 1, w)]
    return [f for f in frames if f.getchannel("A").getbbox()] or [sheet]


def reference_image(asset_id: str) -> Image.Image:
    """Style example for Muse: the asset, cropped to content (<= 512 px), upscaled to >= 256."""
    asset = reference_assets()[asset_id]
    image = Image.open(library_root() / asset["file"]).convert("RGBA")
    if bbox := image.getchannel("A").getbbox():
        image = image.crop(bbox)
    image = image.crop((0, 0, min(image.width, 512), min(image.height, 512)))
    k = max(1, 256 // max(image.size))
    if k > 1:
        image = image.resize((image.width * k, image.height * k), Image.Resampling.NEAREST)
    return image


def entity_words(e: Entity) -> set[str]:
    found = words(e.id.replace("_", " ") + " " + e.look)
    return set(found) | canon(found)


# Words in an entity id that say what kind of thing it is, not which thing ("floor_tile").
GENERIC_ID_WORDS = {"tile", "tiles", "sprite", "pickup", "prop", "projectile", "object", "big",
                    "small", "player", "enemy", "corner", "dire"}  # fmt: skip


def name_words(e: Entity) -> set[str]:
    """What the entity *is*, from its id only ('bone_arrow' -> bone, arrow). Descriptions carry
    colours and materials ('red', 'stone') that would match unrelated library objects."""
    found = [w for w in words(e.id.replace("_", " ")) if w not in GENERIC_ID_WORDS]
    return set(found) | canon(found)


CAMERA_WORDS = {"top", "down", "side", "view"}
MAX_HERO_OPTIONS = 3


def hero_candidates(actors: list[Actor]) -> list[Actor]:
    """Library characters that can be the hero: they stand and move (not modular bodies)."""
    return [
        a
        for a in actors
        if a.category == "character"
        and {"idle", "walk", "run"} & a.anims.keys()
        and "body" not in a.tokens
    ]


def library_context(camera: str, design: GameDesign | None = None) -> str:
    """What the Art Director may pick from, for the game's camera.

    With a design, the list is filtered to keep the prompt small: actors named like the
    design's player or enemies, up to 3 hero options, every tile set for the camera, and
    objects named like what a pickup, prop or projectile is (`name_words`, as the keyword pick
    matches). The
    rest of the library stays reachable through the `library_search` MCP tool, and a plan
    may still pick it: `resolve_plan` checks picks against the whole library."""
    view = "side" if camera == "side-view" else "topdown"
    tags = CAMERA_TAGS[camera]
    actors = [a for a in load_actors() if a.side() == view]
    object_words: set[str] | None = None
    if design is not None:
        cast = [e for e in design.entities if e.role in {"player", "enemy"}]
        want = set().union(*(entity_words(e) for e in cast)) - CAMERA_WORDS
        player = set().union(*(entity_words(e) for e in cast if e.role == "player"))
        heroes = sorted(
            hero_candidates(actors),
            key=lambda a: (len(a.tokens & player), len(a.anims), a.key),
            reverse=True,
        )[:MAX_HERO_OPTIONS]
        actors = [a for a in actors if a in heroes or a.tokens & want]
        things = [e for e in design.entities if e.role in {"pickup", "prop", "projectile"}]
        object_words = set().union(*(name_words(e) for e in things))
    notes = {a["id"]: a for a in search()}
    tile_notes = tile_descriptions()
    lines = ["# Actors (animated; use source=actor, asset=key)"]
    lines += [
        f"* `{a.key}` {'hero' if a.category == 'character' else 'enemy'}: "
        + ", ".join(f"{v} x{len(f)}" for v, f in sorted(a.anims.items()))
        + look_note(notes.get(actor_sheet(a)), actor_frame(a))
        for a in actors
    ] or ["(none for this camera)"]
    lines.append("\n# Tiles (source=tile, asset=<tileset>/<tile>)")
    if not load_tilesets(camera):
        lines.append("(none for this camera)")
    for ts in load_tilesets(camera):
        lines.append(
            f"* tile set `{ts['id']}` ({ts['tile_size']} px; variants: "
            + ", ".join(
                f"{v} [{', '.join(s['words']) or 'default'}]" for v, s in ts["variants"].items()
            )
            + (
                "; has a parallax backdrop: source=backdrop, asset=" + ts["id"]
                if ts.get("backdrop")
                else ""
            )
            + ")"
        )
        lines += [
            f"  * `{ts['id']}/{n}`: {t['what']}"
            + look_note(tile_notes.get(f"{ts['id']}/{n}"), tile_image(ts, n), detail=True)
            for n, t in ts["tiles"].items()
        ]
    lines.append("\n# Whole objects (source=object; pickups, props, projectiles)")
    objects = [
        o
        for o in whole_objects().values()
        if not (set().union(*CAMERA_TAGS.values()) - tags) & set(o["tags"])
        and (object_words is None or object_words & set(re.findall(r"[a-z]+", o["name"])))
    ]
    lines += [
        f"* `{o['id']}` {o['category']}: {o['name']} ({o['cell'][0]}x{o['cell'][1]}"
        + (f", {o['frames']} frames" if o["frames"] > 1 else "")
        + ("" if o.get("ai_allowed", True) else ", no AI")
        + ")"
        + look_note(notes.get(o["id"]), asset_image(o, library_root()))
        for o in sorted(objects, key=lambda o: o["id"])
    ] or ["(none named like this game's entities)" if object_words is not None else "(none)"]
    refs = sorted({a["pack"] for a in reference_assets().values()})
    lines.append(
        "\n# Muse style references (field `reference`)\n"
        + (
            "An actor key or object id listed above from one of these packs: "
            + ", ".join(f"`{r}`" for r in refs)
            + ". Tile ids are not references; '' lets the harness pick one."
            if refs
            else "None (Muse draws from text only); use ''."
        )
    )
    return "\n".join(lines)


def look_note(entry: dict | None, image: Image.Image, detail: bool = False) -> str:
    """' — <what it is>; <colours>; brightness <0-1>[, <detail>]' for a library line, so the
    Art Director picks by what the art looks like, not by its file name (a "stone floor" tile
    it called dark grey was tan, live 2026-11-01). `what` comes from the library description
    (`spriteforge library describe`); colours, brightness and detail are measured here. The
    full sentences stay in the MCP tools and `catalog/`, to keep this list short (M10.2)."""
    look = measure(image)
    parts = [entry["what"]] if entry and entry.get("described_by", "measured") != "measured" else []
    parts.append(", ".join(c.name for c in look.colours) or "empty")
    parts.append(
        f"brightness {look.brightness:.2f}" + (f", {detail_word(look.detail)}" if detail else "")
    )
    return " — " + "; ".join(parts)


def actor_frame(actor: Actor) -> Image.Image:
    frames = actor.anims.get("idle") or next(iter(actor.anims.values()))
    return frames[0]


def art_prompt(design: GameDesign, library: str) -> str:
    entities = "\n".join(
        f"* `{e.id}` ({e.role}, {e.width}x{e.height}, animations: {', '.join(e.animations) or '-'}): "
        f"{e.look}"
        for e in design.entities
    )
    return (
        "Choose art for every entity of this game. The local library comes first: whenever it "
        "has the same kind of creature or object for the game's camera, use it even if details "
        "differ (a skeleton actor for a 'skeleton archer', the wizzard actor for a mage, a "
        "library gem for a crystal). Sizes do not matter; the harness fits the frames. Only "
        "when nothing of that kind exists, use source=muse with a concrete prompt (subject, "
        "colours, materials, pose) so the image model draws it. Never use an unrelated "
        "creature. Pick the tile set variant that matches the game's world. Each library "
        "line says what the art shows and how it looks (colours, brightness, detail measured "
        "from its pixels): when only the colour differs from the entity's look, use it and "
        "set `recolor` (e.g. 'teal', 'dark grey'); when the shape or detail differs (a flat "
        "tile for a brick wall), use Muse.\n\n"
        f"Game: {design.title}. {design.pitch}\nCamera: {design.camera}. Look: {design.look}\n\n"
        f"Entities:\n{entities}\n\nLibrary:\n{library}"
    )


# --- deterministic plan: the offline Art Director, and the default for forgotten entities ----


def actor_index() -> dict[str, Actor]:
    return {a.key: a for a in load_actors()}


def creature_name(sheet_name: str) -> str:
    """The creature a sheet belongs to, as `load_actors` groups them ('npcs rogue idle' ->
    'rogue')."""
    return " ".join(t for t in words(sheet_name) if t not in VERBS and t not in NOISE).strip()


def actor_sheet(actor: Actor) -> str:
    """The library asset id of an actor's idle (else first) sheet, side-facing first: what it
    looks like and a style reference. Only the actor's own sheets count (a name match also
    took the orc rogue's sheet for the rogue hero)."""
    sheets = [
        a
        for a in search()
        if a["pack"] == actor.pack
        and a["category"] in {"character", "enemy"}
        and creature_name(a["name"]) == actor.name
    ]
    idle = [a for a in sheets if "idle" in words(a["name"])]
    pool = sorted(idle or sheets, key=lambda a: "side" not in words(a["name"]))
    return (pool or [{"id": ""}])[0]["id"]


def keyword_pick(e: Entity, design: GameDesign, used: set[str]) -> ArtPick:
    view = "side" if design.camera == "side-view" else "topdown"
    want = entity_words(e) - CAMERA_WORDS

    def pick(source, asset, reason):
        return ArtPick(
            entity=e.id,
            source=source,
            asset=asset,
            prompt="",
            reference="",
            recolor="",
            reason=reason,
        )

    if e.role in {"player", "enemy"}:
        actors = [a for a in load_actors() if a.side() == view and a.key not in used]
        named = [a for a in actors if a.tokens & want]
        if named:
            best = max(
                named,
                key=lambda a: (
                    len(a.tokens & want),
                    a.category == "character" if e.role == "player" else a.category == "enemy",
                    -len(a.key),
                ),
            )
            return pick("actor", best.key, f"library actor named like {sorted(best.tokens & want)}")
        if e.role == "player" and (heroes := hero_candidates(actors)):
            best = max(heroes, key=lambda a: (len(a.anims), a.key))
            return pick("actor", best.key, GENERIC_HERO)
    if e.role == "tile":
        named = name_words(e)
        for ident, (ts, name) in tile_ids(load_tilesets(design.camera)).items():
            if named & set(words(name.replace("_", " "))):
                return pick("tile", ident, f"tile set tile '{name}'")
    if e.role == "background":
        for ts in load_tilesets(design.camera):
            if ts.get("backdrop"):
                return pick("backdrop", ts["id"], "tile set parallax backdrop")
    if e.role in {"pickup", "prop", "projectile"}:
        named = name_words(e)
        other = set().union(*CAMERA_TAGS.values()) - CAMERA_TAGS[design.camera]
        for ident, o in sorted(whole_objects().items()):
            if not other & set(o["tags"]) and named & set(re.findall(r"[a-z]+", o["name"])):
                return pick("object", ident, f"library object '{o['name']}'")
    return ArtPick(
        entity=e.id,
        source="muse",
        asset="",
        prompt=e.look,
        reference="",
        recolor="",
        reason="nothing in the library fits",
    )


def keyword_plan(design: GameDesign) -> ArtPlan:
    used: set[str] = set()
    picks = []
    for e in design.entities:
        p = keyword_pick(e, design, used)
        if p.source == "actor":
            used.add(p.asset)
        picks.append(p)
    text = set(words(design.world + " " + design.look + " " + design.pitch))
    variants = [pick_variant(ts, text) for ts in load_tilesets(design.camera)]
    return ArtPlan(variant=next((v for v in variants if v != "normal"), "normal"), picks=picks)


# --- validation: what the plan may actually use --------------------------------------------


def auto_reference(e: Entity, design: GameDesign, chosen_packs: list[str]) -> str:
    """An AI-allowed style example: same kind of art from packs already in the game first."""
    refs = reference_assets()
    pixel = sorted((i, a) for i, a in refs.items() if a.get("colors", 0) <= 64)
    if not refs:
        return ""
    if e.role == "background":
        return next((i for i, a in sorted(refs.items()) if backdrop_reference(a)), "")
    wanted = {
        "tile": {"tileset"},
        "pickup": {"item", "equipment", "prop"},
        "prop": {"prop", "item"},
        "projectile": {"effect", "item"},
        "background": {"background", "tileset"},
    }.get(e.role, {"character", "enemy"})
    tags = CAMERA_TAGS[design.camera]

    def first(pred, pool=pixel) -> str:
        return next((i for i, a in pool if pred(a)), "")

    for pack in chosen_packs:
        if hit := first(lambda a, p=pack: a["pack"] == p and a["category"] in wanted):
            return hit
    return (
        first(lambda a: a["category"] in wanted and tags & set(a["tags"]))
        or first(lambda a: a["category"] in wanted)
        or first(lambda a: tags & set(a["tags"]))
        or first(lambda a: True)
        or first(lambda a: True, sorted(refs.items()))
    )


def backdrop_reference(asset: dict) -> bool:
    """A style example a background may have: a real backdrop, not a tile sheet or a mockup
    scene. Muse copied both into backgrounds (an arcade room; stone blocks, live 2026-11-01)."""
    return asset["category"] == "background" and "mockups" not in asset["tags"]


# Words in an entity's look that ask for drawn detail a flat library tile does not have.
DETAIL_WORDS = {"brick", "bricks", "crack", "cracks", "cracked", "moss", "mossy", "cobble",
                "cobbles", "cobblestone", "plank", "planks", "stones", "pattern", "patterned",
                "rune", "runes", "carved", "grass", "grassy", "pebbles", "rocky", "textured",
                "grain", "veins", "tufts", "stripe", "stripes", "striped", "rust", "rusty",
                "rivet", "rivets", "riveted", "panel", "panels", "plated", "grime", "grimy",
                "scratched", "dented", "ornate", "engraved", "mosaic", "ridged", "studded",
                "bolted", "weathered", "chipped"}  # fmt: skip
FAR = 25  # colour_gap above which library art is clearly not the colour the design names


def main_part(text: str) -> str:
    """What a look says the thing itself is, before its details: colours after "with" or a
    comma are accents ("dark stone wall block with red moss edge" -> "dark stone wall block";
    reading the red moss as the wall's colour turned a dark wall bright red, live 2026-11-01)."""
    return re.split(r"\bwith\b|[,;.(]", text, maxsplit=1)[0]


def far_colour(text: str, mean: str) -> str | None:
    """The first colour the main part of `text` names, when every colour it names there is far
    from `mean`."""
    named = look_colours(main_part(text))
    if not named or min(colour_gap(code, mean) for _, code in named) <= FAR:
        return None
    return named[0][0]


def hue_mismatch(text: str, image: Image.Image) -> str | None:
    """The first hued colour the main part of `text` names, when the art's main hue is more
    than 45° off."""
    hued = [phrase for phrase, _ in look_colours(main_part(text)) if phrase.split()[-1] in COLORS]
    art = dominant_hue(image) if hued else None
    if art is None:
        return None
    gap = abs((COLORS[hued[0].split()[-1]] / 360 - art + 0.5) % 1 - 0.5) * 360
    return hued[0] if gap > 45 else None


def resolve_plan(plan: ArtPlan, design: GameDesign) -> tuple[dict[str, ArtPick], list[str]]:
    """One valid pick per entity. Invalid library picks become Muse requests."""
    entities = {e.id: e for e in design.entities}
    view = "side" if design.camera == "side-view" else "topdown"
    actors, objects = actor_index(), whole_objects()
    tiles = tile_ids(load_tilesets(design.camera))
    backdrops = {ts["id"] for ts in load_tilesets(design.camera) if ts.get("backdrop")}
    norm = lambda s: re.sub(r"[\s_-]+", " ", s.lower()).strip()
    actor_keys = {norm(k): k for k in actors}
    warnings, out = [], {}

    def to_muse(p: ArtPick, why: str) -> ArtPick:
        warnings.append(f"{p.entity}: {why}; asking Muse Image instead")
        return p.model_copy(update={"source": "muse", "asset": "", "reason": why})

    for p in plan.picks:
        e = entities.get(p.entity)
        if e is None:
            warnings.append(f"art: no entity {p.entity!r} in the design")
            continue
        if p.source == "actor":
            key = actor_keys.get(norm(p.asset))
            if key is None:
                p = to_muse(p, f"actor {p.asset!r} is not in the library")
            elif actors[key].side() != view:
                p = to_muse(p, f"actor {key} is drawn for the other camera")
            else:
                p = p.model_copy(update={"asset": key})
        elif p.source == "object" and p.asset not in objects:
            known = any(a["id"] == p.asset for a in search())
            p = to_muse(
                p,
                f"{p.asset!r} "
                + ("is not a shippable single object" if known else "is not in the library"),
            )
        elif p.source == "tile" and p.asset not in tiles:
            p = to_muse(p, f"tile {p.asset!r} is not in an installed {design.camera} tile set")
        elif p.source == "backdrop" and p.asset not in backdrops:
            p = to_muse(p, f"no {design.camera} tile set {p.asset!r} with a backdrop")
        out[e.id] = p
    for e in design.entities:  # entities the plan forgot get the keyword pick
        if e.id not in out:
            out[e.id] = keyword_pick(e, design, {p.asset for p in out.values()})
            warnings.append(f"{e.id}: not in the art plan; {out[e.id].reason}")
    # Library first: a Muse request for something the library has by name uses the library,
    # unless it is an explicit redraw (feedback or an art-review fix of that entity).
    for e in design.entities:
        p = out[e.id]
        if p.source != "muse" or p.reason.startswith(EXPLICIT_REDRAW):
            continue
        taken = {q.asset for q in out.values() if q.source == "actor"}
        lib = keyword_pick(e, design, taken)
        if lib.source == "tile" and measure(tile_image(*tiles[lib.asset])).detail < FLAT:
            # A flat tile is no stand-in for one Muse would draw (a recolour fixes only its
            # colour): the flat wall top undid the Art Director's choice of Muse for a rusty
            # metal wall with hazard stripes, live 2026-11-01.
            continue
        if lib.source != "muse" and lib.reason != GENERIC_HERO:
            out[e.id] = lib
            warnings.append(
                f"{e.id}: the library has {lib.asset} ({lib.reason}); "
                "used it instead of Muse (library first)"
            )
    # Library art must look like what the design asks for (models propose, code enforces): a
    # flat tile is not used for a look that asks for bricks or moss, a tile far from the colour
    # the look names is recoloured, an actor or object of another hue is hue-shifted.
    for e in design.entities:
        p = out[e.id]
        if p.source == "tile":
            look = measure(tile_image(*tiles[p.asset]))
            asks = sorted(DETAIL_WORDS & set(words(e.look)))
            if look.detail < FLAT and asks:
                out[e.id] = to_muse(p, f"library tile {p.asset} is flat ({look.text}) but the "
                                       f"design asks for {', '.join(asks)}")  # fmt: skip
            elif not p.recolor and (target := far_colour(e.look, look.mean)):
                out[e.id] = p.model_copy(update={"recolor": target})
                warnings.append(f"{e.id}: library tile {p.asset} is {look.colours[0].name} "
                                f"({look.mean}); the design asks for {target}: recoloured")  # fmt: skip
        elif p.source in {"actor", "object"} and not p.recolor:
            image = actor_frame(actors[p.asset]) if p.source == "actor" else (
                asset_frames(objects[p.asset])[0])  # fmt: skip
            if target := hue_mismatch(e.look, image):
                out[e.id] = p.model_copy(update={"recolor": target})
                warnings.append(f"{e.id}: library {p.source} {p.asset} has another main colour; "
                                f"the design asks for {target}: recoloured")  # fmt: skip
    refs = reference_assets()
    chosen_packs = list(
        dict.fromkeys(p.asset.split("/", 1)[0] for p in out.values() if p.source == "actor")
    ) + [tiles[p.asset][0]["pack"] for p in out.values() if p.source == "tile"]
    for eid, p in out.items():
        if p.source != "muse":
            continue
        update = {"prompt": p.prompt.strip() or entities[eid].look}
        if norm(p.reference) in actor_keys:  # an actor key: use that actor's own idle sheet
            sheet = actor_sheet(actors[actor_keys[norm(p.reference)]])
            p = p.model_copy(update={"reference": sheet})
        if (
            p.reference in refs
            and entities[eid].role == "background"
            and not backdrop_reference(refs[p.reference])
        ):
            warnings.append(f"{eid}: a background takes a style example only from a backdrop, "
                            f"not {p.reference!r}; drawn from text")  # fmt: skip
            update["reference"] = ""
        elif p.reference and p.reference not in refs:
            blocked = any(a["id"] == p.reference for a in search())
            warnings.append(
                f"{eid}: reference {p.reference!r} "
                + ("forbids AI use" if blocked else "is not in the library")
                + "; picked another style example"
            )
            update["reference"] = ""
        if not (update.get("reference", p.reference)):
            update["reference"] = auto_reference(entities[eid], design, chosen_packs)
        out[eid] = p.model_copy(update=update)
    return out, warnings


# --- turning images into game art -----------------------------------------------------------


def fit_sprite(
    image: Image.Image, size: tuple[int, int], anchor: str = "bottom", grow: bool = True
) -> Image.Image:
    """Crop to content and shrink (mode filter, aspect kept) into a w x h cell."""
    w, h = size
    if bbox := image.getchannel("A").getbbox():
        image = image.crop(bbox)
    k = min(w / image.width, h / image.height)
    # grow only by whole pixels (never for art already at its native grid); shrink by mode
    k = (int(k) if grow else 1) if k >= 1 else k
    target = (max(1, round(image.width * k)), max(1, round(image.height * k)))
    small = mode_resize(image, target) if target != image.size else image
    cell = Image.new("RGBA", (w, h))
    x = (w - small.width) // 2
    y = {"bottom": h - small.height, "top": 0}.get(anchor, (h - small.height) // 2)
    cell.paste(small, (x, y), small)
    return cell


def fit_tile(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Whole image -> opaque tile; holes take the dominant colour so the tile repeats."""
    tile = mode_resize(image.convert("RGBA"), size)
    rgba = np.array(tile)
    opaque = rgba[..., 3] > 0
    if not opaque.any():
        rgba[...] = (90, 90, 90, 255)
    elif not opaque.all():
        colors, counts = np.unique(rgba[opaque][:, :3], axis=0, return_counts=True)
        rgba[~opaque] = (*colors[counts.argmax()], 255)
    rgba[..., 3] = 255
    return Image.fromarray(rgba)


def limit_palette(image: Image.Image, colors: int = 16) -> Image.Image:
    """Snap a sprite to its own `colors` dominant colours: crisp pixel art that keeps the hues
    the design asked for. (Locking to another asset's palette turned a cyan gem grey when the
    style reference was a bonfire, so the reference only guides Muse's style.)"""
    palette = extract_palette(image, colors).colors
    rgba = np.array(image.convert("RGBA"))
    mask = rgba[..., 3] > 0
    if mask.any():
        rgba[mask, :3] = quantize(rgba[mask, :3], palette)
        rgba[..., 3] = np.where(mask, 255, 0)
    return Image.fromarray(rgba)


def anchor_for(role: str) -> str:
    return "bottom" if role in {"player", "enemy", "prop"} else "center"


def sprite_meta(e: Entity, frame: tuple[int, int], anims: dict[str, list[int]], **extra) -> dict:
    return {"role": e.role, "frame": list(frame), "anims": anims, **extra}


def single_frame(e: Entity, image: Image.Image, source: str, reasons, record=None) -> Filled:
    """A one-frame sprite (Muse or a still object): every animation shows that frame."""
    if e.role in {"tile", "background"}:
        meta = {"role": e.role, "size": list(image.size)}
        return Filled(png(image), source, reasons, meta, "image", record or {})
    anims = {a: [0, 1] for a in (e.animations or ["idle"])}
    anims.setdefault("idle", [0, 1])
    return Filled(
        png(image), source, reasons, sprite_meta(e, image.size, anims), "sprite", record or {}
    )


def recolour_tile(tile: Image.Image, target: str) -> Image.Image:
    """The tile's light-and-dark pattern in `target`'s colour: its mean brightness moves to the
    target's and every pixel keeps its difference from that mean (squeezed only where it
    would pass black or white), so cracks and grain keep their contrast."""
    a = np.asarray(tile.convert("RGBA")).astype(float)
    luma = a[..., :3] @ LUMA / 255
    drawn = a[..., 3] > 127
    mean = float(luma[drawn].mean() if drawn.any() else luma.mean())
    colour = np.array(rgb_of(target)) / 255
    lt = max(float(colour @ LUMA), 1e-3)
    offset = luma - mean
    low, high = float(offset.min()), float(offset.max())
    squeeze = min(1.0, lt / -low if low < 0 else 1.0, (1 - lt) / high if high > 0 else 1.0)
    new = lt + offset * squeeze
    rgb = np.clip(colour[None, None, :] * (new / lt)[..., None], 0, 1) * 255
    return Image.fromarray(np.dstack([rgb, a[..., 3]]).round().astype(np.uint8), "RGBA")


def recolour_sprite(strip: Image.Image, phrase: str) -> Image.Image | None:
    """Hue-shift a sprite to the colour `phrase` names, lifting its brightness to that colour's
    when it is darker; None when the phrase names no hue (grey, black, white)."""
    word = next((w for w in reversed(phrase.lower().split()) if w in COLORS), None)
    if word is None:
        return None
    code = (look_palette(phrase) or [None])[0]
    value = colorsys.rgb_to_hsv(*(np.array(rgb_of(code)) / 255))[2] if code else None
    return recolor(strip, COLORS[word], value)


def actor_art(e: Entity, actor: Actor, size, topdown: bool, faces: str, hue: str) -> Filled:
    wanted = e.animations or ["idle", "walk"]
    if "idle" not in wanted:
        wanted = ["idle", *wanted]
    strip, counts, body = adapt(
        face_right(actor.anims, faces), {a: 1 for a in wanted}, size, topdown
    )
    reasons = [f"{sum(counts.values())} frames from {actor.key}"]
    if hue and (shifted := recolour_sprite(strip, hue)) is not None:
        strip = shifted
        reasons.append(f"recoloured {hue}")
    anims, start = {}, 0
    for name, n in counts.items():
        anims[name] = [start, n]
        start += n
    meta = sprite_meta(e, size, anims, body=list(body))
    return Filled(png(strip), f"library {actor.key}", reasons, meta)


def object_art(e: Entity, asset: dict, size, hue: str = "") -> Filled:
    frames = [fit_sprite(f, size, anchor_for(e.role)) for f in asset_frames(asset)][:8]
    strip = Image.new("RGBA", (size[0] * len(frames), size[1]))
    for i, f in enumerate(frames):
        strip.paste(f, (i * size[0], 0))
    reasons = [f"{len(frames)} frame(s)"]
    if hue and (shifted := recolour_sprite(strip, hue)) is not None:
        strip = shifted
        reasons.append(f"recoloured {hue}")
    anims = {a: [0, len(frames)] for a in (e.animations or ["idle"])}
    anims.setdefault("idle", [0, len(frames)])
    return Filled(png(strip), f"library {asset['id']}", reasons, sprite_meta(e, size, anims))


def placeholder(e: Entity, size) -> Image.Image:
    """Greybox stand-in (only when the image cap is reached or Muse fails)."""
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    color = {
        "player": (90, 150, 230),
        "enemy": (210, 80, 80),
        "pickup": (240, 200, 60),
        "projectile": (250, 250, 250),
        "prop": (150, 120, 90),
        "tile": (70, 70, 84),
        "background": (30, 34, 48),
    }[e.role]
    if e.role in {"tile", "background"}:
        d.rectangle(
            (0, 0, w - 1, h - 1), fill=(*color, 255), outline=(*[c + 20 for c in color], 255)
        )
    else:
        d.rounded_rectangle(
            (1, 1, w - 2, h - 2),
            radius=max(1, min(w, h) // 4),
            fill=(*color, 255),
            outline=(20, 20, 28, 255),
        )
    return img


# --- the art spec: what every Muse request states, built by code -----------------------------

VIEWS = {
    "top-down": "top-down 3/4 view: the camera is tilted about 45°, we see the top and the "
    "front of the subject",
    "side-view": "flat side view (profile), the camera level with the subject",
}
# A background asked for "the top and the front of the subject" came back as stone blocks.
BACKGROUND_VIEWS = {
    "top-down": "seen straight down from above, flat: no walls, blocks, platforms, buildings "
    "or perspective",
    "side-view": "a distant backdrop behind the level, flat side view",
}
# Side-view prompts must not say "top": Muse then tilts the camera.
LIGHT = {"top-down": "light from the top-left", "side-view": "light from the upper left"}
# Hex values for colour words in the design's look, when no style example gives a palette.
COLOR_HEX = {
    "red": "#C83232", "crimson": "#A01E32", "orange": "#E07828", "yellow": "#F0D040",
    "gold": "#E0B030", "golden": "#E0B030", "green": "#3C9C3C", "lime": "#96D23C",
    "teal": "#1E9696", "cyan": "#32C8DC", "blue": "#3264D2", "navy": "#1E2864",
    "purple": "#8040C0", "violet": "#9650D2", "pink": "#E070B0", "magenta": "#C83CA0",
    "brown": "#7C5030", "tan": "#C8A078", "black": "#141418", "white": "#F0F0F0",
    "grey": "#808088", "gray": "#808088", "silver": "#BEBEC8", "bone": "#E6DCC0",
}  # fmt: skip


class ArtSpec(Contract):
    role: str
    view: str
    grid: tuple[int, int]
    scale_note: str
    anchor: str
    facing: str
    light: str
    outline: str
    palette: list[str] = Field(max_length=16)
    # "look": the colours the request names (the reference gives technique only);
    # "reference": the style example's colours (the request names none).
    palette_from: str = ""
    request_size: tuple[int, int]
    look: str = ""  # the design's art direction, for text-only requests


# Words that cancel the colour words after them in the same clause ("remove tan stone").
NEGATIONS = {"no", "not", "without", "remove", "removing", "instead", "never"}


def shade(hex_code: str, word: str) -> str:
    """`hex_code` darkened after "dark"/"deep", lightened after "light"/"pale"."""
    rgb = [int(hex_code[i : i + 2], 16) for i in (1, 3, 5)]
    if word in {"dark", "darker", "deep"}:
        rgb = [round(c * 0.45) for c in rgb]
    elif word in {"light", "lighter", "pale"}:
        rgb = [round(c + (255 - c) * 0.5) for c in rgb]
    return "#" + "".join(f"{c:02X}" for c in rgb)


SHADES = {"dark", "darker", "deep", "light", "lighter", "pale"}


def look_colours(text: str) -> list[tuple[str, str]]:
    """(phrase, hex) for the hex codes and colour words named in `text`, in order (<= 16):
    ("dark grey", "#3A3A3D"). A colour after a negation in its clause ("remove tan stone") is
    left out."""
    found = [(m.upper(), m.upper()) for m in re.findall(r"#[0-9a-fA-F]{6}\b", text)]
    for clause in re.split(r"[,;.:]", text.lower()):
        words_, negated = re.findall(r"[a-z]+", clause), False
        for i, w in enumerate(words_):
            negated = negated or w in NEGATIONS
            if w in COLOR_HEX and not negated:
                before = words_[i - 1] if i else ""
                phrase = f"{before} {w}" if before in SHADES else w
                found.append((phrase, shade(COLOR_HEX[w], before)))
    out: dict[str, str] = {}
    for phrase, code in found:
        out.setdefault(code, phrase)
    return [(phrase, code) for code, phrase in out.items()][:16]


def look_palette(text: str) -> list[str]:
    """Hex codes of the colours named in `text` ("dark purple" is a dark purple)."""
    return [code for _, code in look_colours(text)]


def request_size(grid: tuple[int, int], sizes: list[tuple[int, int]]) -> tuple[int, int]:
    """The verified Muse size whose aspect ratio is closest to the grid's."""
    want = math.log(grid[0] / grid[1])
    return min((tuple(s) for s in sizes), key=lambda s: (abs(math.log(s[0] / s[1]) - want), s))


def scale_note(e: Entity, grid: tuple[int, int], hero_px: int) -> str:
    hero = f"the hero is {hero_px} px tall"
    if e.role == "player":
        return f"this is the hero, {hero_px} px tall"
    if e.role == "tile":
        return f"{hero}; one tile is {grid[1]} px"
    if e.role == "background":
        return f"{hero}; the scene is {grid[1]} px tall"
    ratio = grid[1] / hero_px
    words_ = {0.25: "a quarter", 0.5: "half", 0.75: "three quarters", 1: "the same height as"}
    near = min(words_, key=lambda r: abs(r - ratio))
    if abs(near - ratio) < 0.08:
        compared = f"{words_[near]} the hero" if near != 1 else "the same height as the hero"
    else:
        compared = f"{ratio:.1f}× the hero's height"
    return f"{hero}; this is {grid[1]} px, {compared}"


def art_spec(
    e: Entity,
    design: GameDesign,
    grid: tuple[int, int],
    hero_px: int,
    sizes: list[tuple[int, int]],
    reference: Image.Image | None = None,
    text: str = "",
) -> ArtSpec:
    """`text` is what is asked for (the pick's prompt, else the entity's look). Colours it
    names win over the style example's: the example's own palette turned a red slime orange
    and a purple bat brown (live, 2026-11-01). The example then gives technique only."""
    sprite = e.role not in {"tile", "background"}
    anchor = {
        "tile": "fills the whole grid edge to edge (full bleed) and repeats without seams",
        "background": "fills the whole image edge to edge (full bleed)",
        "pickup": "centred in the grid",
        "projectile": "centred in the grid",
    }.get(e.role, "feet (or base) at the bottom centre of the grid")
    if own := look_palette(text or e.look):
        palette, palette_from = own, "look"
    elif reference is not None:
        palette, palette_from = extract_palette(reference, 16).colors, "reference"
    else:
        palette, palette_from = look_palette(design.look), "look"
    return ArtSpec(
        role=e.role,
        view=(BACKGROUND_VIEWS if e.role == "background" else VIEWS)[design.camera],
        grid=grid,
        scale_note=scale_note(e, grid, hero_px),
        anchor=anchor,
        facing="pointing right" if e.role == "projectile" else "facing right" if sprite else "",
        light=LIGHT[design.camera],
        outline="a 1 px dark outline around the subject" if sprite else "no outline",
        palette=palette,
        palette_from=palette_from,
        request_size=request_size(grid, sizes),
        look=design.look,
    )


def muse_prompt(spec: ArtSpec, pick: ArtPick, has_ref: bool = False) -> str:
    w, h = spec.grid
    # Probe 2026-10-31: Muse draws clean big pixels but picks its own count (a 16x16 knight
    # came back 21x27 cells); naming the block size as a fraction of the image did not help.
    cells = (
        f"Drawn on a {w}×{h} grid of big square pixels, one solid colour per cell, no "
        "anti-aliasing, no gradients inside a cell"
    )
    if spec.role == "tile":
        shape = f"One seamless square game tile. {cells}; the grid covers the whole image."
    elif spec.role == "background":
        # "A game background scene" with no style example came back with lightning bolts,
        # explosions and green blobs (the game's bolts and slimes), live 2026-11-01.
        shape = (
            "A game background: only the empty ground, sky or void itself, with no characters, "
            f"creatures, items, projectiles or effects. {cells}; the grid covers the whole image."
        )
    else:
        shape = (
            f"One single game sprite, whole object visible. {cells}; the sprite fills that "
            "grid, centred in the image, on a flat pure magenta (#FF00FF) background with "
            "nothing else in the image."
        )
    return (
        f"Pixel art for a 2D game. Draw: {pick.prompt.strip().rstrip('.')}. Draw it once, in "
        f"one state only. {shape} "
        + (TINY_STYLE + " " if tiny(spec.grid) and spec.role not in {"tile", "background"} else "")
        + f"{spec_details(spec, has_ref)} No text, no lettering, no frame, no drop shadow."
    )


def spec_details(spec: ArtSpec, has_ref: bool) -> str:
    details = [
        f"View: {spec.view}.",
        f"Size: {spec.scale_note}.",
        f"Placement: {spec.anchor}.",
        f"Facing: {spec.facing}." if spec.facing else "",
        f"Lighting: {spec.light}.",
        f"Outline: {spec.outline}.",
        palette_line(spec),
        "Copy only the reference image's pixel-art technique: outline weight, shading and "
        "level of detail. It is from another game: do not draw its subject, objects or layout"
        + (", and do not use its colours." if spec.palette_from == "look" else ".")
        if has_ref
        else f"Crisp retro pixel art, hard pixel edges. Look: {spec.look}.",
    ]
    return " ".join(d for d in details if d)


def palette_line(spec: ArtSpec) -> str:
    if not spec.palette:
        return ""
    colours = ", ".join(spec.palette)
    if spec.palette_from == "reference":
        return f"Palette: use only these colours: {colours}."
    return f"Colours: {colours}, as described, with darker and lighter shades of them."


def sheet_prompt(
    spec: ArtSpec, pick: ArtPick, cols: int, rows: int, frames: list[str], has_ref: bool = False
) -> str:
    """Several small frames of one subject in one image: a sprite sheet read like a slide
    show, left to right, top to bottom. Each frame gets a slot of the image, so Muse draws it
    with fewer, bigger pixels than a sprite that fills the whole image."""
    w, h = spec.grid
    listed = " ".join(f"{i + 1}. {f.strip().rstrip('.')}." for i, f in enumerate(frames))
    return (
        f"Pixel art sprite sheet for a 2D game: {len(frames)} frames of the same subject, "
        f"{pick.prompt.strip().rstrip('.')}. Lay them out as {cols} columns by {rows} rows of "
        "equal cells on one flat pure magenta (#FF00FF) background, no grid lines, no borders. "
        "Each frame sits whole inside its own cell, centred, with magenta space around it; "
        "the subject has the same size, colours and design in every frame, standing on the "
        f"same baseline. Each frame is a tiny sprite drawn on a {w}×{h} grid of big square "
        "pixels, one solid colour per cell, no anti-aliasing, no gradients inside a cell. "
        f"Frames, left to right, top to bottom: {listed} "
        + (TINY_STYLE + " " if tiny(spec.grid) else "")
        + f"{spec_details(spec, has_ref)} No text, no numbers, no lettering, no drop shadow."
    )


def read_sprite(
    image: Image.Image, grid_confidence: float, pitch: float | None = None
) -> tuple[Image.Image | None, dict]:
    """Key a raw sprite image; return its big pixels one per pixel when drawn on a grid
    (info mode "native"), else the keyed subject itself (mode "fit"); None when empty."""
    info: dict = {"raw_size": list(image.size), "mode": "fit"}
    image = prepare_source(image, ProcessingOptions(detect_grid=False), ProcessingReport())
    if not image.getbbox():
        return None, {**info, "subject": None}
    info["subject"] = list(image.size)
    grid = detect_cells(image, pitch=pitch)
    if grid:
        info.update(cell=round(grid.pitch, 1), confidence=round(grid.confidence, 2))
    if grid and grid.confidence >= grid_confidence:
        small = sample_cells(image, grid)
        if bbox := small.getbbox():
            small = small.crop(bbox)
            info.update(mode="native", logical=list(small.size))
            return small, info
    return image, info


def muse_frame(spec: ArtSpec, raw: bytes, grid_confidence: float = 0.6, adapt: bool = False):
    """Raw Muse image -> a frame of `spec.grid` size, and how it was made. Big-pixel art is
    read back one cell per pixel ("native"); anything else is fitted ("fit")."""
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    size = spec.grid
    if spec.role not in {"tile", "background"}:
        source, info = read_sprite(image, grid_confidence)
        if source is None:
            return Image.new("RGBA", size), info
        if adapt:
            size = art_size(size, source.size)
        native = source.width <= size[0] and source.height <= size[1]
        if info["mode"] == "native" and not native:
            # Muse often draws more cells than asked: shrink the clean cells, not the raw image.
            info["mode"] = "native-resized"
        grow = info["mode"] == "fit"
        return fit_sprite(source, size, anchor_for(spec.role), grow=grow), info
    info: dict = {"raw_size": list(image.size), "mode": "fit", "subject": list(image.size)}
    grid = detect_cells(image)
    if grid:
        info.update(cell=round(grid.pitch, 1), confidence=round(grid.confidence, 2))
    if grid and grid.confidence >= grid_confidence:
        cells = sample_cells(image, grid)
        if cells.width * 2 >= size[0] and cells.height * 2 >= size[1]:
            image = cells
            info["logical"] = list(image.size)
            info["mode"] = "native" if image.size == tuple(size) else "native-resized"
        else:
            # Live 2026-11-01: a void's faint grid lines, 106 px apart, read as an 18x11 grid
            # for a 320x180 scene; blown up, the lines vanished into a flat black image.
            info["note"] = (
                f"a {cells.width}x{cells.height} grid is too coarse for {size[0]}x{size[1]} "
                "(drawn lines, not pixels); fitted instead"
            )
    if spec.role == "tile":
        return fit_tile(image, size), info
    return mode_resize(image, size).convert("RGBA"), info


# --- sprite sheets: several small frames in one Muse image ----------------------------------

# Poses per animation, one per frame (4 frames; fewer frames take the first ones).
POSES = {
    "idle": ["standing still", "breathing in, a little taller", "standing still",
             "breathing out, a little lower"],
    "walk": ["left foot forward", "feet passing under the body", "right foot forward",
             "feet passing under the body"],
    "fly": ["wings up", "wings level", "wings down", "wings level"],
    "attack": ["winding up", "striking", "follow-through", "recovering"],
    "hurt": ["recoiling from a hit", "flashing", "recoiling", "recovering"],
    # Not "fading away": Muse paints it translucent, which keys to a pink smear.
    "death": ["hit, recoiling", "falling", "lying on the ground", "lying flat, defeated"],
}  # fmt: skip
POSE_ALIASES = {"run": "walk", "move": "walk", "chase": "walk", "hop": "walk", "jump": "walk",
                "shoot": "attack", "cast": "attack", "hit": "hurt", "die": "death"}  # fmt: skip
OBJECT_POSES = {
    "pickup": ["still", "a small glint", "a bright glint", "a small glint"],
    "projectile": ["in flight", "in flight, trail flickers", "in flight", "in flight, trail flickers"],
}  # fmt: skip


# Sprites this small (longest side, px) read best as one bold shape: one idle row of nearly
# identical poses (the other animations show it), and no sparkles or effects.
TINY = 10
TINY_POSES = ["still", "still, its highlight one pixel higher", "still",
              "still, its highlight one pixel lower"]  # fmt: skip
TINY_STYLE = (
    "These are tiny icons: one bold, simple silhouette with at most 4 colours and a dark "
    "outline; no sparkles, glow, particles, trails or effects outside the shape."
)


def tiny(grid: tuple[int, int]) -> bool:
    return max(grid) <= TINY


def main_row(rows: list[tuple[str, list[str]]]) -> int:
    """The movement row (walk, run, fly, ...) if there is one, else idle (row 0)."""
    moving = {"walk", "fly"}
    return next(
        (i for i, (name, _) in enumerate(rows) if POSE_ALIASES.get(name, name) in moving), 0
    )


def sheet_rows(e: Entity, frames: int) -> list[tuple[str, list[str]]]:
    """One row per animation (idle first, at most 4), `frames` poses each."""
    if tiny((e.width, e.height)):
        return [("idle", [f"idle: {p}" for p in TINY_POSES[:frames]])]
    names = list(dict.fromkeys(["idle", *e.animations]))[:4]
    rows = []
    for name in names:
        poses = (
            OBJECT_POSES.get(e.role, ["still"] * 4)
            if name == "idle" and e.role in {"pickup", "projectile", "prop"}
            else POSES.get(POSE_ALIASES.get(name, name))
        ) or [f"{name.replace('_', ' ')}, pose {i + 1}" for i in range(4)]
        rows.append((name, [f"{name.replace('_', ' ')}: {p}" for p in poses[:frames]]))
    return rows


def art_size(box: tuple[int, int], extent: tuple[int, int]) -> tuple[int, int]:
    """A frame with the drawn art's aspect ratio and about the design box's area, when the two
    shapes differ by more than 15% (a bat with spread wings in a square box); else the box."""
    a = min(3.0, max(1 / 3, extent[0] / extent[1]))
    w, h = box
    if abs(math.log(a / (w / h))) <= math.log(1.15):
        return tuple(box)
    nh = math.sqrt(w * h / a)
    return (min(128, max(4, round(nh * a))), min(128, max(4, round(nh))))


def muse_sheet(
    spec: ArtSpec,
    raw: bytes,
    cols: int,
    rows: int,
    grid_confidence: float = 0.6,
    adapt: bool = False,
    main_row: int = 0,
) -> tuple[list[Image.Image], list[dict]]:
    """Slice a sheet into `cols` x `rows` equal cells (row by row) and read each frame. The
    frames of a row (one animation) share one scale; empty cells repeat the first frame with
    something in it."""
    sheet = Image.open(io.BytesIO(raw)).convert("RGBA")
    cw, ch = sheet.width / cols, sheet.height / rows
    cells = [
        sheet.crop((round(c * cw), round(r * ch), round((c + 1) * cw), round((r + 1) * ch)))
        for r in range(rows)
        for c in range(cols)
    ]
    read = [read_sprite(cell, grid_confidence) for cell in cells]
    # One sheet, one pixel size: a frame read at another pitch (often half, doubling its
    # cells) is read again at the sheet's median pitch.
    pitches = [i["cell"] for s, i in read if s is not None and i["mode"] == "native"]
    if pitches:
        shared = float(np.median(pitches))
        read = [
            read_sprite(cell, grid_confidence, shared)
            if src is not None and abs(info.get("cell", shared) - shared) > 0.2 * shared
            else (src, info)
            for cell, (src, info) in zip(cells, read, strict=True)
        ]
    size, anchor = spec.grid, anchor_for(spec.role)
    if all(src is None for src, _ in read):
        return [Image.new("RGBA", size) for _ in read], [info for _, info in read]
    if adapt:  # the frame takes the main row's shape (what the player sees most)
        main = read[main_row * cols : (main_row + 1) * cols]
        drawn = [s for s, _ in main if s is not None] or [s for s, _ in read if s is not None]
        size = art_size(size, (max(s.width for s in drawn), max(s.height for s in drawn)))
    # One shrink per row (an animation), so frames do not pulse in size; a wide death row
    # does not shrink the idle row. Frames without a grid are fitted on their own.
    shrink = []
    for r in range(rows):
        row = read[r * cols : (r + 1) * cols]
        native = [src for src, info in row if src is not None and info["mode"] == "native"]
        shrink.append(min((min(size[0] / s.width, size[1] / s.height) for s in native), default=1))
    frames, infos = [], []
    for n, (src, info) in enumerate(read):
        k = shrink[n // cols]
        if src is None:
            frames.append(None)
        elif info["mode"] == "native":
            if k < 1:
                src = mode_resize(
                    src, (max(1, round(src.width * k)), max(1, round(src.height * k)))
                )
                info["mode"] = "native-resized"
            frames.append(fit_sprite(src, size, anchor, grow=False))
        else:
            frames.append(fit_sprite(src, size, anchor))
        infos.append(info)
    first = next(f for f in frames if f is not None)
    return [f if f is not None else first.copy() for f in frames], infos


def seams_match(tile: Image.Image) -> bool:
    """Opposite edges differ no more than neighbouring columns/rows inside the tile do.
    (`pixel.validate.seam_scores` takes the maximum difference, which flags every texture.)"""
    rgb = np.asarray(tile.convert("RGB"), dtype=float)
    for axis in (0, 1):
        inner = np.abs(np.diff(rgb, axis=axis)).mean()
        first, last = (rgb[0], rgb[-1]) if axis == 0 else (rgb[:, 0], rgb[:, -1])
        if np.abs(first - last).mean() > 2 * inner + 8:
            return False
    return True


def make_tileable(tile: Image.Image) -> Image.Image:
    """Opposite edges made to line up: each edge band is blended into the tile shifted by
    half, whose edges were neighbours inside the tile. The middle stays as drawn."""
    rgb = np.asarray(tile.convert("RGB"), dtype=float)
    for axis in (0, 1):
        n = rgb.shape[axis]
        edge = np.minimum(np.arange(n), np.arange(n)[::-1])  # distance to the nearest edge
        weight = np.clip(1 - edge / max(1, n // 4), 0, 1)
        shape = [1, 1, 1]
        shape[axis] = n
        weight = weight.reshape(shape)
        rgb = (1 - weight) * rgb + weight * np.roll(rgb, n // 2, axis=axis)
    return Image.fromarray(np.round(rgb).astype(np.uint8)).convert("RGBA")


SEAM_PROBLEM = "the tile does not repeat: its left/right or top/bottom edges do not line up"


def check_frame(spec: ArtSpec, frame: Image.Image, info: dict, aspect: bool = True) -> list[str]:
    """What is wrong with a processed Muse frame, as sentences for the retry prompt. With
    `aspect` off (the frame took the art's shape) the subject's aspect is not checked."""
    w, h = frame.size if not aspect else spec.grid
    if not frame.getchannel("A").getbbox():
        return ["no subject: nothing usable was drawn on the magenta background"]
    problems = []
    if spec.role == "tile" and not seams_match(frame):
        problems.append(SEAM_PROBLEM)
    if spec.role in {"tile", "background"}:
        return problems
    sw, sh = info.get("subject") or frame.getchannel("A").getbbox()[2:]
    ratio = (sw / sh) / (w / h)
    if aspect and not 1 / 1.35 <= ratio <= 1.35:
        problems.append(
            f"the subject is {'too wide' if ratio > 1 else 'too tall'} for its {w}×{h} grid "
            f"(aspect {sw / sh:.2f}, wanted about {w / h:.2f})"
        )
    fill = float((np.asarray(frame)[..., 3] > 0).mean())
    if fill < 0.2:
        problems.append(f"the subject fills only {fill:.0%} of its {w}×{h} grid; draw it bigger")
    elif fill > 0.98:
        problems.append(
            "the subject fills the whole grid like a solid block; draw just the object on "
            "the magenta background"
        )
    return problems


Generate = Callable[[ImageRequest, bool], bytes]


@dataclass
class ArtResult:
    screen: tuple[int, int]
    scale: int
    background: str
    filled: dict[str, Filled]
    picks: dict[str, ArtPick]
    warnings: list[str]
    credits: list[str]

    def manifest(self) -> dict:
        """assets.json: what the SDK loads and the Programmer may reference."""
        sprites, images = {}, {}
        for eid, f in self.filled.items():
            entry = {"file": f"assets/{eid}.png", **f.meta, "source": f.source}
            (sprites if f.kind == "sprite" else images)[eid] = entry
            for lid, (_, meta) in f.extra.items():
                images[lid] = {"file": f"assets/{lid}.png", **meta, "source": f.source}
        return {
            "screen": list(self.screen),
            "scale": self.scale,
            "background": self.background,
            "sprites": sprites,
            "images": images,
        }

    def files(self) -> dict[str, bytes]:
        out = {f"assets/{eid}.png": f.png for eid, f in self.filled.items()}
        for f in self.filled.values():
            out.update({f"assets/{lid}.png": data for lid, (data, _) in f.extra.items()})
        return out


def make_art(
    design: GameDesign,
    plan: ArtPlan,
    *,
    generate: Generate | None,
    max_calls: int = 8,
    previous: Callable[[str], dict | None] = lambda eid: None,
    options: "MuseOptions | None" = None,
    cache: ImageCache | None = None,
) -> ArtResult:
    """Resolve the plan and produce every entity's art. `previous(entity)` returns an earlier
    version's Muse record ({"prompt", "reference", "size", "raw", "png", "version"}).

    Library picks and locked art resolve first. The remaining Muse jobs get their first call
    slot in PRIORITY order *before* any is sent (so the cap is never exceeded and the same
    entities become greybox under a cap), then run `options.workers` at a time. `generate`
    must be safe to call from several threads. Results and warnings are assembled in entity
    order, so the outcome does not depend on which call finishes first."""
    options = options or MuseOptions()
    picks, warnings = resolve_plan(plan, design)
    tilesets = {ts["id"]: ts for ts in load_tilesets(design.camera)}
    used_sets = {
        p.asset.split("/", 1)[0] for p in picks.values() if p.source in {"tile", "backdrop"}
    }
    scale = 2 if any(tilesets[t]["tile_size"] == 32 for t in used_sets if t in tilesets) else 1
    screen = (320 * scale, 180 * scale)
    variant = (
        plan.variant
        if any(plan.variant in tilesets[t]["variants"] for t in used_sets)
        else "normal"
    )
    background = next((tilesets[t]["background"] for t in used_sets), "#0b0c14")
    entities = {e.id: e for e in design.entities}
    actors, objects = actor_index(), whole_objects()
    packs = {p.id: p for p in load_packs()}
    used_packs: set[str] = set()
    filled: dict[str, Filled] = {}
    hero_px = next(e.height for e in design.entities if e.role == "player") * scale
    order = sorted(entities.values(), key=lambda e: PRIORITY.index(e.role))
    slots = CallSlots(max_calls)
    muses: dict[str, Muse] = {}
    jobs: list[tuple[Muse, MuseJob]] = []
    for e in order:
        p = picks[e.id]
        size = (e.width * scale, e.height * scale)
        if p.source == "actor":
            actor = actors[p.asset]
            filled[e.id] = actor_art(
                e, actor, size, design.camera == "top-down", packs[actor.pack].faces, p.recolor
            )
            used_packs.add(actor.pack)
        elif p.source == "object":
            filled[e.id] = object_art(e, objects[p.asset], size, p.recolor)
            used_packs.add(objects[p.asset]["pack"])
        elif p.source == "tile":
            ts, name = tile_ids(list(tilesets.values()))[p.asset]
            tile = tile_image(ts, name, variant)
            reasons = [p.reason]
            if p.recolor and (target := look_palette(p.recolor)):
                tile = recolour_tile(tile, target[0])
                reasons.append(f"recoloured {p.recolor} ({target[0]})")
            if tile.size != size:
                tile = tile.resize(size, Image.Resampling.NEAREST)
            filled[e.id] = single_frame(e, tile, f"tileset {p.asset} ({variant})", reasons)
            used_packs.add(ts["pack"])
        elif p.source == "backdrop":
            ts = tilesets[p.asset]
            layers = {}
            for lid, image, factor in backdrop_layers(ts, variant):
                if image.height > screen[1]:
                    image = image.crop((0, image.height - screen[1], image.width, image.height))
                layers[f"{e.id}_{lid.split('_')[-1]}"] = (
                    png(image),
                    {"role": "background", "size": list(image.size), "parallax": factor},
                )
            first = next(iter(layers.values()))[0] if layers else png(placeholder(e, screen))
            filled[e.id] = Filled(
                first,
                f"tileset {p.asset} backdrop ({variant})",
                [f"{len(layers)} parallax layers {list(layers)}"],
                {"role": "background", "size": list(screen)},
                "image",
                extra=layers,
            )
            used_packs.add(ts["pack"])
        else:
            muse = muses[e.id] = Muse(generate, previous, slots, options, hero_px, cache)
            box = screen if e.role == "background" else size
            if (locked := muse.locked(e, p, box)) is not None:
                filled[e.id] = locked
            elif generate is None:
                filled[e.id] = muse.greybox(e, box, "no image provider")
            else:
                job = muse.job(e, design, p, box)
                if muse.is_cached(job.request):
                    jobs.append((muse, job))  # the cache answers it: no call slot needed
                elif slots.take():
                    jobs.append((muse, replace(job, slot=True)))
                else:
                    filled[e.id] = muse.greybox(e, box, f"image-call cap ({max_calls}) reached")
    filled.update(run_jobs(jobs, options.workers))
    filled = {e.id: filled[e.id] for e in order}
    for e in order:
        warnings += muses[e.id].warnings if e.id in muses else []
    credits = [
        f"{packs[pid].title} by {packs[pid].author} - {packs[pid].license}"
        + (f" ({packs[pid].source_url})" if packs[pid].source_url else "")
        for pid in sorted(used_packs)
        if pid in packs
    ]
    if any(f.source.startswith("muse") for f in filled.values()):
        credits.append(MUSE_CREDIT)
    return ArtResult(screen, scale, background, filled, picks, warnings, credits)


def run_jobs(jobs: list[tuple["Muse", "MuseJob"]], workers: int) -> dict[str, Filled]:
    """Draw every job, `workers` at a time. A job that raises becomes a reported greybox and
    never cancels the others."""

    def one(muse: Muse, job: MuseJob) -> Filled:
        try:
            return muse.draw(job)
        except Exception as exc:  # noqa: BLE001 -- one failed job must not sink the build
            return muse.greybox(job.e, job.size, f"Muse job failed ({type(exc).__name__})")

    if workers <= 1 or len(jobs) <= 1:
        return {job.e.id: one(muse, job) for muse, job in jobs}
    with ThreadPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
        futures = {job.e.id: pool.submit(one, muse, job) for muse, job in jobs}
    return {eid: f.result() for eid, f in futures.items()}


@dataclass
class MuseOptions:
    sizes: list[tuple[int, int]] = field(default_factory=lambda: [SQUARE])  # `image.sizes`
    retries: int = 1  # `game.art_retry`: redraws of a frame that fails `check_frame`
    grid_confidence: float = 0.6  # `game.native_grid_confidence`
    # `game.sheet_frames`: sprites are drawn as a sheet, this many frames per animation row
    # (each frame gets a slot of the image, so Muse uses fewer, bigger pixels); 1 = one image
    sheet_frames: int = 4
    # `game.adapt_size`: a sprite's frame takes the drawn art's shape (about the design's area)
    adapt_size: bool = True
    workers: int = 4  # `game.image_workers`: Muse calls in flight at once
    reasoning: Literal["low", "high"] = "low"  # first attempt's strength (review fixes: high)


class CallSlots:
    """The build's image-call cap, shared by parallel Muse jobs. Each job's first call is
    reserved in PRIORITY order before dispatch; retries take what is left."""

    def __init__(self, cap: int):
        self.cap, self.used, self._lock = cap, 0, threading.Lock()

    def take(self) -> bool:
        with self._lock:
            if self.used >= self.cap:
                return False
            self.used += 1
            return True

    def give_back(self) -> None:
        with self._lock:
            self.used -= 1


@dataclass
class MuseJob:
    """One entity's Muse request, prepared in the main thread before dispatch."""

    e: Entity
    p: ArtPick
    size: tuple[int, int]
    spec: ArtSpec
    rows: list[tuple[str, list[str]]]
    prompt: str  # the spec prompt; a retry puts the problems in front of it
    request: ImageRequest
    key: dict  # what decides whether a later version may reuse (lock) this art
    has_ref: bool
    edit_of: int | None
    slot: bool = False  # a call slot was reserved for the first attempt


@dataclass
class Muse:
    """One entity's Muse Image art under the build's shared call cap (`slots`). Warnings are
    the entity's own; `make_art` merges them in entity order."""

    generate: Generate | None
    previous: Callable[[str], dict | None]
    slots: CallSlots
    options: MuseOptions
    hero_px: int
    cache: ImageCache | None = None
    warnings: list[str] = field(default_factory=list)

    def rows(self, e: Entity) -> list[tuple[str, list[str]]]:
        n = self.options.sheet_frames
        return sheet_rows(e, n) if n > 1 and e.role not in {"tile", "background"} else []

    def request_key(self, e: Entity, p: ArtPick, size) -> dict:
        rows = self.rows(e)
        return {
            "prompt": p.prompt,
            "reference": p.reference,
            "size": list(size),
            "sheet": [self.options.sheet_frames, len(rows)] if rows else None,
        }

    def locked(self, e: Entity, p: ArtPick, size) -> Filled | None:
        """An unchanged request reuses the previous version's art (no call)."""
        old = self.previous(e.id)
        key = self.request_key(e, p, size)
        if not (old and all(old.get(k) == v for k, v in key.items())):
            return None
        reasons = [p.reason or "Muse art", "unchanged request: reused, no image call"]
        record = {**key, "raw": old["raw"], "reused_from": old["version"],
                  "frame_size": old.get("frame_size")}  # fmt: skip
        source = f"muse (locked, from v{old['version']})"
        if old.get("anims"):
            meta = sprite_meta(e, tuple(old.get("frame_size") or size), old["anims"])
            return Filled(old["png"], source, reasons, meta, "sprite",
                          {**record, "anims": old["anims"]})  # fmt: skip
        image = Image.open(io.BytesIO(old["png"])).convert("RGBA")
        return single_frame(e, image, source, reasons, record)

    def job(self, e: Entity, design: GameDesign, p: ArtPick, size) -> MuseJob:
        old = self.previous(e.id)
        n, rows = self.options.sheet_frames, self.rows(e)
        ref = reference_image(p.reference) if p.reference else None
        spec = art_spec(e, design, tuple(size), self.hero_px, self.options.sizes, ref,
                        p.prompt.strip() or e.look)  # fmt: skip
        if rows:
            layout = (size[0] * n, size[1] * len(rows))
            spec = spec.model_copy(
                update={"request_size": request_size(layout, self.options.sizes)}
            )
            poses = [pose for _, row in rows for pose in row]
            prompt = sheet_prompt(spec, p, n, len(rows), poses, ref is not None)
        else:
            prompt = muse_prompt(spec, p, ref is not None)
        refs = [png(ref)] if ref is not None else []
        edit_of = None
        if old and old.get("raw"):  # feedback changed this entity: edit the previous image
            refs = [old["raw"], *refs]
            prompt = (
                "Revise the FIRST image (the current sprite) as described; keep its framing and "
                + ("the second image's style. " if ref is not None else "its style. ")
                + prompt
            )
            edit_of = old["version"]
        request = ImageRequest(prompt=prompt, size=spec.request_size, references=refs,
                               reasoning_strength=self.options.reasoning)  # fmt: skip
        return MuseJob(e, p, tuple(size), spec, rows, prompt, request,
                       self.request_key(e, p, size), ref is not None, edit_of)  # fmt: skip

    def is_cached(self, request: ImageRequest) -> bool:
        return self.cache is not None and self.cache.path(request, False).is_file()

    def fetch(self, job: MuseJob, request: ImageRequest, first: bool) -> tuple[bytes | None, bool]:
        """(raw, from the cache), or (None, False) when the cap leaves no call for it. Raises
        when the call fails."""
        edit = bool(request.references)
        if self.cache and (hit := self.cache.get(request, edit)) is not None:
            if first and job.slot:
                self.slots.give_back()  # reserved, but the cache answered
            return hit, True
        if not (first and job.slot) and not self.slots.take():
            return None, False
        raw = self.generate(request, edit)
        if self.cache:
            self.cache.put(request, edit, raw)
        return raw, False

    def draw(self, job: MuseJob) -> Filled:
        e, p, size, spec, rows = job.e, job.p, job.size, job.spec, job.rows
        n = self.options.sheet_frames
        request = job.request
        best, problems, history, made, failed = None, [], [], 0, False
        for attempt in range(1 + self.options.retries):
            if attempt and not failed:  # a failed call (e.g. HTTP 500) is sent again as it was
                request = request.model_copy(
                    update={
                        "prompt": "The last attempt was wrong: " + "; ".join(problems)
                        + ". Fix that. " + job.prompt,
                        "reasoning_strength": "high",
                    }
                )  # fmt: skip
            try:
                raw, cached = self.fetch(job, request, attempt == 0)
                if raw is None:
                    self.warnings.append(
                        f"{e.id}: {'; '.join(problems)}; no retry, image-call cap "
                        f"({self.slots.cap}) reached"
                    )
                    break
                made += 0 if cached else 1
                conf, adapt = self.options.grid_confidence, self.options.adapt_size
                if rows:
                    frames, infos = muse_sheet(spec, raw, n, len(rows), conf, adapt, main_row(rows))
                else:
                    frame, info = muse_frame(spec, raw, conf, adapt)
                    frames, infos = [frame], [info]
            except Exception as exc:  # noqa: BLE001 -- one failed image must not sink the build
                made += 1
                detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
                self.warnings.append(f"{e.id}: Muse Image failed ({type(exc).__name__}: "
                                     f"{detail})")  # fmt: skip
                failed, problems = True, ["the image request failed"]
                continue
            failed = False
            problems = check_frame(spec, frames[0], infos[0], aspect=not adapt)
            empty = [i + 1 for i, info in enumerate(infos) if info.get("subject") is None]
            if empty and len(empty) < len(frames):
                problems.append(
                    f"frames {', '.join(map(str, empty))} of the {n}x{len(rows)} sheet are "
                    "empty; draw every frame inside its own cell"
                )
            history.append(problems)
            if frames[0].getchannel("A").getbbox():
                best = (raw, frames, infos, request, problems, cached)
            if not problems:
                break
        if best is None:
            return self.greybox(e, size, "no usable Muse image")
        raw, frames, infos, request, problems, cached = best  # the kept frames' own problems
        blended = spec.role == "tile" and SEAM_PROBLEM in problems
        if blended:  # models propose, code enforces: a tile that does not repeat is blended
            frames = [make_tileable(frames[0]), *frames[1:]]
            problems = [p for p in problems if p != SEAM_PROBLEM]
        if problems:
            self.warnings.append(f"{e.id}: accepted with problems: {'; '.join(problems)}")
        info = infos[0]
        cells = "x".join(map(str, info["logical"])) + " cells, " if "logical" in info else ""
        readback = f"{info['mode']} ({cells}from {info['raw_size'][0]}x{info['raw_size'][1]})"
        if rows:
            modes = [i["mode"] for i in infos]
            readback = (
                f"{len(frames)}-frame sheet ({n}x{len(rows)}), "
                + ", ".join(f"{modes.count(m)} {m}" for m in dict.fromkeys(modes))
                + f"; frame 1 {readback}"
            )
        reasons = [
            p.reason or "Muse art",
            f"style example: {p.reference}"
            if job.has_ref
            else "text only (no AI-allowed style example)",
            readback,
        ]
        if cached:
            reasons.append("identical earlier request: image from the cache, no call")
        if len(history) > 1:
            reasons.append(f"retried at high reasoning strength: {'; '.join(history[0])}")
        if blended:
            reasons.append("edges blended by code: the drawn tile did not repeat")
        if job.edit_of:
            reasons.append(f"edited from v{job.edit_of} per feedback")
        record = {
            **job.key,
            "raw": raw,
            "muse_prompt": request.prompt,
            "edit_of": job.edit_of,
            "request": {
                "size": list(request.size),
                "reasoning_strength": request.reasoning_strength,
                "calls": made,
                "cached": cached,
            },
            "spec": spec.model_dump(mode="json"),
            "frame": info,
            "problems": problems,
        }
        source = "muse (cached)" if cached else "muse"
        fw, fh = frames[0].size  # the design's box, or the art's shape (`adapt_size`)
        record["frame_size"] = [fw, fh]
        if (fw, fh) != tuple(size):
            reasons.append(f"frame {fw}x{fh} follows the art (design box {size[0]}x{size[1]})")
        if not rows:
            return single_frame(e, limit_palette(frames[0]), source, reasons, record)
        # One palette for the whole strip, so frames keep identical colours.
        strip = Image.new("RGBA", (fw * len(frames), fh))
        for i, f in enumerate(frames):
            strip.paste(f, (i * fw, 0))
        strip = limit_palette(strip)
        anims = {name: [i * n, n] for i, (name, _) in enumerate(rows)}
        for name in e.animations:  # beyond four rows: show the idle row
            anims.setdefault(name, [0, n])
        record.update(anims=anims, frames_read=[i["mode"] for i in infos])
        return Filled(png(strip), source, reasons, sprite_meta(e, (fw, fh), anims),
                      "sprite", record)  # fmt: skip

    def greybox(self, e: Entity, size, why: str) -> Filled:
        self.warnings.append(f"{e.id}: {why}; greybox placeholder used")
        return single_frame(e, placeholder(e, size), "placeholder", [why])


def art_sheet(filled: dict[str, Filled], scale: int = 3) -> bytes | None:
    """Contact sheet of every entity's first frame, for the gate and the report."""
    if not filled:
        return None
    images = []
    for f in filled.values():
        img = Image.open(io.BytesIO(f.png)).convert("RGBA")
        if f.kind == "sprite":
            w, h = f.meta["frame"]
            img = img.crop((0, 0, w, h))
        images.append(img)
    return png(contact_sheet(images, thumb=32 * scale))
