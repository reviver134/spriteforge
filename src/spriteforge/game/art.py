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

import io
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np
from PIL import Image, ImageDraw
from pydantic import Field

from spriteforge.agents.models import Contract
from spriteforge.library.store import contact_sheet, library_root, load_packs, search
from spriteforge.pixel.grid import detect_cells, sample_cells
from spriteforge.pixel.palette import extract_palette, quantize
from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import mode_resize, prepare_source
from spriteforge.providers.base import ImageRequest

from .actors import COLORS, Actor, adapt, canon, face_right, load_actors, recolor, words
from .design import Entity, GameDesign
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


class ArtPick(Contract):
    entity: str = Field(description="entity id from the design")
    source: Source
    asset: str = Field(
        description="actor key, object id, '<tileset>/<tile>' or tileset id for backdrop; "
        "'' for muse"
    )
    prompt: str = Field(description="muse only: what to draw (subject, colours, materials)")
    reference: str = Field(description="muse only: AI-allowed library asset id; '' = automatic")
    recolor: str = Field(description="actor only: colour word to hue-shift it to, or ''")
    reason: str


class ArtPlan(Contract):
    variant: str = Field(description="tile set variant (normal, autumn, winter, ...)")
    picks: list[ArtPick]


@dataclass
class Filled:
    png: bytes
    source: (
        str  # "library <id>" | "tileset <id>" | "muse" | "muse (locked, from vN)" | "placeholder"
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


def library_context(camera: str) -> str:
    """What the Art Director may pick from, for the game's camera."""
    view = "side" if camera == "side-view" else "topdown"
    tags = CAMERA_TAGS[camera]
    actors = [a for a in load_actors() if a.side() == view]
    lines = ["# Actors (animated; use source=actor, asset=key)"]
    lines += [
        f"* `{a.key}` {'hero' if a.category == 'character' else 'enemy'}: "
        + ", ".join(f"{v} x{len(f)}" for v, f in sorted(a.anims.items()))
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
        lines += [f"  * `{ts['id']}/{n}`: {t['what']}" for n, t in ts["tiles"].items()]
    lines.append("\n# Whole objects (source=object; pickups, props, projectiles)")
    objects = [
        o
        for o in whole_objects().values()
        if not (set().union(*CAMERA_TAGS.values()) - tags) & set(o["tags"])
    ]
    lines += [
        f"* `{o['id']}` {o['category']}: {o['name']} ({o['cell'][0]}x{o['cell'][1]}"
        + (f", {o['frames']} frames" if o["frames"] > 1 else "")
        + ("" if o.get("ai_allowed", True) else ", no AI")
        + ")"
        for o in sorted(objects, key=lambda o: o["id"])
    ] or ["(none)"]
    refs = sorted({a["pack"] for a in reference_assets().values()})
    lines.append(
        "\n# Muse style references\nAny asset id from these packs: "
        + (", ".join(f"`{r}`" for r in refs) or "none (Muse draws from text only)")
    )
    return "\n".join(lines)


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
        "creature. Pick the tile set variant that matches the game's world.\n\n"
        f"Game: {design.title}. {design.pitch}\nCamera: {design.camera}. Look: {design.look}\n\n"
        f"Entities:\n{entities}\n\nLibrary:\n{library}"
    )


# --- deterministic plan: the offline Art Director, and the default for forgotten entities ----


def actor_index() -> dict[str, Actor]:
    return {a.key: a for a in load_actors()}


def actor_sheet(actor: Actor) -> str:
    """The library asset id of an actor's idle (else first) sheet, usable as a reference."""
    name = set(actor.name.split())
    sheets = [a for a in search() if a["pack"] == actor.pack and name <= set(a["name"].split())]
    idle = [a for a in sheets if "idle" in a["name"].split()]
    return (idle or sheets or [{"id": ""}])[0]["id"]


def keyword_pick(e: Entity, design: GameDesign, used: set[str]) -> ArtPick:
    view = "side" if design.camera == "side-view" else "topdown"
    want = entity_words(e) - {"top", "down", "side", "view"}

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
        if e.role == "player":
            heroes = [
                a
                for a in actors
                if a.category == "character"
                and {"idle", "walk", "run"} & a.anims.keys()
                and "body" not in a.tokens
            ]
            if heroes:
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
    # Library first: a Muse request for something the library has by name uses the library.
    for e in design.entities:
        p = out[e.id]
        if p.source != "muse":
            continue
        taken = {q.asset for q in out.values() if q.source == "actor"}
        lib = keyword_pick(e, design, taken)
        if lib.source != "muse" and lib.reason != GENERIC_HERO:
            out[e.id] = lib
            warnings.append(
                f"{e.id}: the library has {lib.asset} ({lib.reason}); "
                "used it instead of Muse (library first)"
            )
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
        if p.reference and p.reference not in refs:
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


def actor_art(e: Entity, actor: Actor, size, topdown: bool, faces: str, hue: str) -> Filled:
    wanted = e.animations or ["idle", "walk"]
    if "idle" not in wanted:
        wanted = ["idle", *wanted]
    strip, counts, body = adapt(
        face_right(actor.anims, faces), {a: 1 for a in wanted}, size, topdown
    )
    reasons = [f"{sum(counts.values())} frames from {actor.key}"]
    if hue in COLORS:
        strip = recolor(strip, COLORS[hue])
        reasons.append(f"recoloured {hue}")
    anims, start = {}, 0
    for name, n in counts.items():
        anims[name] = [start, n]
        start += n
    meta = sprite_meta(e, size, anims, body=list(body))
    return Filled(png(strip), f"library {actor.key}", reasons, meta)


def object_art(e: Entity, asset: dict, size) -> Filled:
    frames = [fit_sprite(f, size, anchor_for(e.role)) for f in asset_frames(asset)][:8]
    strip = Image.new("RGBA", (size[0] * len(frames), size[1]))
    for i, f in enumerate(frames):
        strip.paste(f, (i * size[0], 0))
    anims = {a: [0, len(frames)] for a in (e.animations or ["idle"])}
    anims.setdefault("idle", [0, len(frames)])
    return Filled(
        png(strip),
        f"library {asset['id']}",
        [f"{len(frames)} frame(s)"],
        sprite_meta(e, size, anims),
    )


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
    request_size: tuple[int, int]
    look: str = ""  # the design's art direction, for text-only requests


def look_palette(text: str) -> list[str]:
    """Hex codes and colour words named in the design's look, in order (<= 16)."""
    found = [m.upper() for m in re.findall(r"#[0-9a-fA-F]{6}\b", text)]
    found += [COLOR_HEX[w] for w in re.findall(r"[a-z]+", text.lower()) if w in COLOR_HEX]
    return list(dict.fromkeys(found))[:16]


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
) -> ArtSpec:
    sprite = e.role not in {"tile", "background"}
    anchor = {
        "tile": "fills the whole grid edge to edge (full bleed) and repeats without seams",
        "background": "fills the whole image edge to edge (full bleed)",
        "pickup": "centred in the grid",
        "projectile": "centred in the grid",
    }.get(e.role, "feet (or base) at the bottom centre of the grid")
    palette = (
        extract_palette(reference, 16).colors
        if reference is not None
        else look_palette(design.look)
    )
    return ArtSpec(
        role=e.role,
        view=VIEWS[design.camera],
        grid=grid,
        scale_note=scale_note(e, grid, hero_px),
        anchor=anchor,
        facing="pointing right" if e.role == "projectile" else "facing right" if sprite else "",
        light=LIGHT[design.camera],
        outline="a 1 px dark outline around the subject" if sprite else "no outline",
        palette=palette,
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
        shape = f"A game background scene, no characters. {cells}; the grid covers the whole image."
    else:
        shape = (
            f"One single game sprite, whole object visible. {cells}; the sprite fills that "
            "grid, centred in the image, on a flat pure magenta (#FF00FF) background with "
            "nothing else in the image."
        )
    details = [
        f"View: {spec.view}.",
        f"Size: {spec.scale_note}.",
        f"Placement: {spec.anchor}.",
        f"Facing: {spec.facing}." if spec.facing else "",
        f"Lighting: {spec.light}.",
        f"Outline: {spec.outline}.",
        f"Palette: use only these colours: {', '.join(spec.palette)}." if spec.palette else "",
    ]
    style = (
        "Match the reference image's pixel-art style exactly: outline weight, shading, palette "
        "and level of detail. The reference is a style example only; do not copy its subject."
        if has_ref
        else f"Crisp retro pixel art, hard pixel edges. Look: {spec.look}."
    )
    return (
        f"Pixel art for a 2D game. Draw: {pick.prompt.strip().rstrip('.')}. Draw it once, in "
        f"one state only. {shape} {' '.join(d for d in details if d)} {style} No text, no "
        "lettering, no frame, no drop shadow."
    )


def muse_frame(spec: ArtSpec, raw: bytes, grid_confidence: float = 0.6):
    """Raw Muse image -> a frame of `spec.grid` size, and how it was made. Big-pixel art is
    read back one cell per pixel ("native"); anything else is fitted ("fit")."""
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    size, sprite = spec.grid, spec.role not in {"tile", "background"}
    info: dict = {"raw_size": list(image.size), "mode": "fit"}
    if sprite:
        image = prepare_source(image, ProcessingOptions(detect_grid=False), ProcessingReport())
        if not image.getbbox():
            return Image.new("RGBA", size), {**info, "subject": None}
    info["subject"] = list(image.size)
    grid = detect_cells(image)
    if grid:
        info.update(cell=round(grid.pitch, 1), confidence=round(grid.confidence, 2))
    small = sample_cells(image, grid) if grid and grid.confidence >= grid_confidence else None
    if small is not None and sprite and (bbox := small.getbbox()):
        small = small.crop(bbox)
        info["logical"] = list(small.size)
        native = small.width <= size[0] and small.height <= size[1]
        # Muse often draws more cells than asked: shrink the clean cells, not the raw image.
        info["mode"] = "native" if native else "native-resized"
        return fit_sprite(small, size, anchor_for(spec.role), grow=not native), info
    elif small is not None and not sprite:
        info["logical"] = list(small.size)
        info["mode"] = "native" if small.size == tuple(size) else "native-resized"
        image = small
    if spec.role == "tile":
        return fit_tile(image, size), info
    if spec.role == "background":
        return mode_resize(image, size).convert("RGBA"), info
    return fit_sprite(image, size, anchor_for(spec.role)), info


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


def check_frame(spec: ArtSpec, frame: Image.Image, info: dict) -> list[str]:
    """What is wrong with a processed Muse frame, as sentences for the retry prompt."""
    w, h = spec.grid
    if not frame.getchannel("A").getbbox():
        return ["no subject: nothing usable was drawn on the magenta background"]
    problems = []
    if spec.role == "tile" and not seams_match(frame):
        problems.append(
            "the tile does not repeat: its left/right or top/bottom edges do not line up"
        )
    if spec.role in {"tile", "background"}:
        return problems
    sw, sh = info.get("subject") or frame.getchannel("A").getbbox()[2:]
    ratio = (sw / sh) / (w / h)
    if not 1 / 1.35 <= ratio <= 1.35:
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
) -> ArtResult:
    """Resolve the plan and produce every entity's art. `previous(entity)` returns an earlier
    version's Muse record ({"prompt", "reference", "size", "raw", "png", "version"})."""
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
    calls = 0
    hero_px = next(e.height for e in design.entities if e.role == "player") * scale
    order = sorted(entities.values(), key=lambda e: PRIORITY.index(e.role))
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
            filled[e.id] = object_art(e, objects[p.asset], size)
            used_packs.add(objects[p.asset]["pack"])
        elif p.source == "tile":
            ts, name = tile_ids(list(tilesets.values()))[p.asset]
            tile = tile_image(ts, name, variant)
            if tile.size != size:
                tile = tile.resize(size, Image.Resampling.NEAREST)
            filled[e.id] = single_frame(e, tile, f"tileset {p.asset} ({variant})", [p.reason])
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
            muse = Muse(generate, previous, max_calls, options, warnings, hero_px, calls)
            filled[e.id] = muse.art(e, design, p, screen if e.role == "background" else size)
            calls = muse.calls
    credits = [
        f"{packs[pid].title} by {packs[pid].author} - {packs[pid].license}"
        + (f" ({packs[pid].source_url})" if packs[pid].source_url else "")
        for pid in sorted(used_packs)
        if pid in packs
    ]
    if any(f.source.startswith("muse") for f in filled.values()):
        credits.append("Other art generated with Muse Image for this game.")
    return ArtResult(screen, scale, background, filled, picks, warnings, credits)


@dataclass
class MuseOptions:
    sizes: list[tuple[int, int]] = field(default_factory=lambda: [SQUARE])  # `image.sizes`
    retries: int = 1  # `game.art_retry`: redraws of a frame that fails `check_frame`
    grid_confidence: float = 0.6  # `game.native_grid_confidence`


@dataclass
class Muse:
    """One entity's Muse Image art under the build's call cap (`calls` counts across)."""

    generate: Generate | None
    previous: Callable[[str], dict | None]
    max_calls: int
    options: MuseOptions
    warnings: list[str]
    hero_px: int
    calls: int = 0

    def art(self, e: Entity, design: GameDesign, p: ArtPick, size) -> Filled:
        old = self.previous(e.id)
        key = {"prompt": p.prompt, "reference": p.reference, "size": list(size)}
        if old and all(old.get(k) == v for k, v in key.items()):
            reasons = [p.reason or "Muse art", "unchanged request: reused, no image call"]
            return single_frame(
                e,
                Image.open(io.BytesIO(old["png"])).convert("RGBA"),
                f"muse (locked, from v{old['version']})",
                reasons,
                {**key, "raw": old["raw"], "reused_from": old["version"]},
            )
        if self.generate is None or self.calls >= self.max_calls:
            why = (
                "no image provider"
                if self.generate is None
                else f"image-call cap ({self.max_calls}) reached"
            )
            return self.greybox(e, size, why)
        ref = reference_image(p.reference) if p.reference else None
        spec = art_spec(e, design, tuple(size), self.hero_px, self.options.sizes, ref)
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
        request = ImageRequest(prompt=prompt, size=spec.request_size, references=refs)
        best, problems, history, made = None, [], [], 0
        for attempt in range(1 + self.options.retries):
            if attempt:
                if self.calls >= self.max_calls:
                    self.warnings.append(
                        f"{e.id}: {'; '.join(problems)}; no retry, image-call cap "
                        f"({self.max_calls}) reached"
                    )
                    break
                request = request.model_copy(
                    update={
                        "prompt": "The last attempt was wrong: " + "; ".join(problems)
                        + ". Fix that. " + prompt,
                        "reasoning_strength": "high",
                    }
                )  # fmt: skip
            self.calls += 1
            made += 1
            try:
                raw = self.generate(request, bool(refs))
                frame, info = muse_frame(spec, raw, self.options.grid_confidence)
            except Exception as exc:  # noqa: BLE001 -- one failed image must not sink the build
                detail = str(exc).strip().splitlines()[0][:200] if str(exc).strip() else ""
                self.warnings.append(f"{e.id}: Muse Image failed ({type(exc).__name__}: "
                                     f"{detail})")  # fmt: skip
                break
            problems = check_frame(spec, frame, info)
            history.append(problems)
            if frame.getchannel("A").getbbox():
                best = (raw, frame, info, request, problems)
            if not problems:
                break
        if best is None:
            return self.greybox(e, size, "no usable Muse image")
        raw, frame, info, request, problems = best  # the kept frame's own problems
        if problems:
            self.warnings.append(f"{e.id}: accepted with problems: {'; '.join(problems)}")
        frame = limit_palette(frame)
        cells = "x".join(map(str, info["logical"])) + " cells, " if "logical" in info else ""
        readback = f"{info['mode']} ({cells}from {info['raw_size'][0]}x{info['raw_size'][1]})"
        reasons = [
            p.reason or "Muse art",
            f"style example: {p.reference}"
            if ref is not None
            else "text only (no AI-allowed style example)",
            readback,
        ]
        if len(history) > 1:
            reasons.append(f"retried at high reasoning strength: {'; '.join(history[0])}")
        if edit_of:
            reasons.append(f"edited from v{edit_of} per feedback")
        record = {
            **key,
            "raw": raw,
            "muse_prompt": request.prompt,
            "edit_of": edit_of,
            "request": {
                "size": list(request.size),
                "reasoning_strength": request.reasoning_strength,
                "calls": made,
            },
            "spec": spec.model_dump(mode="json"),
            "frame": info,
            "problems": problems,
        }
        return single_frame(e, frame, "muse", reasons, record)

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
