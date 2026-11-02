"""The title art (L2.5): a background picture behind the title and main screens and an emblem
over the game's title, drawn by Muse Image in the style of the game's locked art (two calls),
cleaned and checked by the pixel tools. Muse never draws text: the SDK draws every word.

Style references are only art Muse drew for this game (library art, whatever its licence,
is never sent). Accepted title art is locked: a later build reuses it unless the menus'
descriptions of it change. Offline, when the calls fail or a picture fails its checks, code
draws it from the game's own art: a panel in the theme's colours with the ground tile along
the bottom, and the player sprite enlarged as the emblem.
"""

import io
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import key_background, mode_resize
from spriteforge.providers.base import ImageRequest

from .art import ArtResult, limit_palette, png
from .design import GameDesign
from .menus import MenuSpec, mix

BACKGROUND_SIZE, EMBLEM_SIZE = (1792, 1024), (1024, 1024)
EMBLEM_BOX = 56  # px at scale 1: the emblem's longest side


@dataclass
class TitleArt:
    title: bytes  # PNG at the screen's size
    emblem: bytes  # PNG with transparency
    source: str  # "muse", "muse (locked, from vN)", "code-drawn: <why>"
    prompts: dict = field(default_factory=dict)  # {"title": ..., "emblem": ...}
    notes: list[str] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # Muse's pictures before cleaning
    version: int = 0  # the build that made it (0: not built yet)

    def record(self) -> dict:
        return {"source": self.source, "prompts": self.prompts, "notes": self.notes,
                "version": self.version}


def prompts(spec: MenuSpec, design: GameDesign) -> dict:
    style = (f"Match the pixel size, outlines, shading and palette of the reference sprites "
             f"exactly. Art direction: {design.look}.")  # fmt: skip
    return {
        "title": (f"Pixel art title-screen background for the 2D {design.camera} game "
                  f"'{design.title}': {spec.title_art}. Wide landscape scene, chunky readable "
                  f"pixels, a calm darker area across the upper middle where a title will be "
                  f"placed. {style} No text, no letters, no numbers, no logo, no UI, no border."),
        "emblem": (f"A single pixel art emblem for the game '{design.title}': {spec.emblem}. "
                   f"One centred subject, bold outline, on a plain flat magenta background "
                   f"(#FF00FF) that touches nothing. {style} No text, no letters, no numbers."),
    }  # fmt: skip


def references(art: ArtResult) -> list[bytes]:
    """Up to two of the game's own Muse sprites (the player first), enlarged for Muse."""
    muse = [(eid, f) for eid, f in art.filled.items() if f.source.startswith("muse")]
    muse.sort(key=lambda kv: kv[1].meta.get("role") != "player")
    out = []
    for _, f in muse[:2]:
        img = Image.open(io.BytesIO(f.png)).convert("RGBA")
        frame = f.meta.get("frame") if f.kind == "sprite" else None
        if frame:
            img = img.crop((0, 0, int(frame[0]), int(frame[1])))
        k = max(1, 256 // max(img.size))
        out.append(png(img.resize((img.width * k, img.height * k), Image.NEAREST)))
    return out


def clean_background(raw: bytes, screen: tuple[int, int]) -> tuple[Image.Image, list[str]]:
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    w, h = screen
    scale = max(w / img.width, h / img.height)
    size = (max(w, round(img.width * scale)), max(h, round(img.height * scale)))
    img = mode_resize(img, size)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = limit_palette(img.crop((left, top, left + w, top + h)), 32)
    problems = []
    luma = np.asarray(img.convert("L"), dtype=float) / 255
    if luma.std() < 0.03:
        problems.append("the background is one flat colour")
    return img, problems


def clean_emblem(raw: bytes, box: int) -> tuple[Image.Image, list[str]]:
    img = key_background(Image.open(io.BytesIO(raw)), ProcessingOptions(), ProcessingReport())
    alpha = np.asarray(img)[..., 3]
    problems = []
    if not alpha.any():
        return img, ["the emblem came back empty after removing its background"]
    if (alpha > 0).mean() > 0.9:  # nothing was keyed: the subject is on a picture, not a plain ground
        problems.append("the emblem kept its background (no plain ground to remove)")
    ys, xs = np.nonzero(alpha)
    img = img.crop((int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1))
    k = box / max(img.size)
    img = mode_resize(img, (max(1, round(img.width * k)), max(1, round(img.height * k))))
    img = limit_palette(img, 16)
    filled = float((np.asarray(img)[..., 3] > 0).mean())
    if filled < 0.08:
        problems.append("the emblem is mostly empty")
    return img, problems


def code_drawn(spec: MenuSpec, art: ArtResult, why: str) -> TitleArt:
    """The title art drawn by code from the game's art and the theme's colours."""
    w, h = art.screen
    top, bottom = spec.theme.panel, mix(spec.theme.panel, spec.theme.border, 0.35)
    rows = [np.array([int(mix(top, bottom, y / max(1, h - 1))[i:i + 2], 16) for i in (1, 3, 5)] + [255],
                     dtype=np.uint8) for y in range(h)]  # fmt: skip
    canvas = Image.fromarray(np.repeat(np.stack(rows)[:, None, :], w, axis=1))
    tile = next((f for f in art.filled.values() if f.meta.get("role") == "tile" and f.kind == "image"), None)
    if tile:
        t = Image.open(io.BytesIO(tile.png)).convert("RGBA")
        ground = h - t.height
        for x in range(0, w, t.width):
            canvas.alpha_composite(t, (x, ground))
    player = next((f for f in art.filled.values() if f.meta.get("role") == "player"), None)
    if player:
        img = Image.open(io.BytesIO(player.png)).convert("RGBA")
        frame = player.meta.get("frame") if player.kind == "sprite" else None
        if frame:
            img = img.crop((0, 0, int(frame[0]), int(frame[1])))
        k = max(1, (EMBLEM_BOX * art.scale) // max(img.size))
        emblem = img.resize((img.width * k, img.height * k), Image.NEAREST)
    else:
        emblem = Image.new("RGBA", (8, 8), (0, 0, 0, 0))
    return TitleArt(png(canvas), png(emblem), f"code-drawn: {why}")


Generate = Callable[[ImageRequest, bool], bytes]


def make_title_art(spec: MenuSpec, design: GameDesign, art: ArtResult,
                   generate: Generate | None, cache=None,
                   previous: TitleArt | None = None) -> TitleArt:  # fmt: skip
    """Muse's title art for `spec`, or the locked `previous` when its descriptions are the
    same, or code's when there is no image model (`generate` None) or the pictures fail."""
    asked = prompts(spec, design)
    if previous is not None and previous.prompts == asked and previous.source.startswith("muse"):
        return previous
    if generate is None:
        offline = code_drawn(spec, art, "offline (no image model)")
        offline.prompts = asked  # what an image model would be asked (a later build may draw it)
        return offline
    refs = references(art)
    raw, notes = {}, []
    for part, size in (("title", BACKGROUND_SIZE), ("emblem", EMBLEM_SIZE)):
        request = ImageRequest(prompt=asked[part], size=size, references=refs)
        edit = bool(refs)
        try:
            hit = cache.get(request, edit) if cache else None
            raw[part] = hit if hit is not None else generate(request, edit)
            if hit is None and cache:
                cache.put(request, edit, raw[part])
        except Exception as exc:  # noqa: BLE001 -- the title art falls back to code's
            notes.append(f"Muse could not draw the {part} art ({type(exc).__name__}: {str(exc)[:160]})")
    drawn = code_drawn(spec, art, "the Muse pictures failed their checks")
    title, emblem = None, None
    if "title" in raw:
        img, problems = clean_background(raw["title"], art.screen)
        notes += [f"title background: {p}" for p in problems]
        title = None if problems else png(img)
    if "emblem" in raw:
        img, problems = clean_emblem(raw["emblem"], EMBLEM_BOX * art.scale)
        notes += [f"emblem: {p}" for p in problems]
        emblem = None if problems else png(img)
    if title is None and emblem is None:
        return TitleArt(drawn.title, drawn.emblem, drawn.source, asked, notes, raw)
    source = "muse" if title and emblem else "muse (" + ("background" if title else "emblem") + "; the other code-drawn)"
    return TitleArt(title or drawn.title, emblem or drawn.emblem, source, asked, notes, raw)


def manifest_images(t: TitleArt) -> tuple[dict, dict[str, bytes]]:
    """assets.json image entries (role "menu": the SDK's, not the Programmer's) and files."""
    out, files = {}, {}
    for ident, data in (("sf_title_art", t.title), ("sf_emblem", t.emblem)):
        w, h = Image.open(io.BytesIO(data)).size
        out[ident] = {"file": f"assets/{ident}.png", "role": "menu", "size": [w, h],
                      "source": t.source}  # fmt: skip
        files[f"assets/{ident}.png"] = data
    return out, files
