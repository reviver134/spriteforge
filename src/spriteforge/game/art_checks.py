"""Free, deterministic checks on a build's art, run before the vision review (M10.3).

They measure what a player needs to read the game at its real size:

* contrast: a sprite's luminance against the floor tile it stands on, the larger of its body's
  and its outline ring's difference (a dark outline keeps a mid-tone sprite readable);
* hue clash: an enemy and a pickup with the same dominant hue (threat and reward look alike);
* size: the drawn subject against the design's size and against the hero;
* placeholders still present (greybox from the image-call cap or a failed Muse call);
* silhouette: a sparse shape (little of its bounding box filled) or a subject much smaller than
  its frame.

Findings are inputs for the reviewer and, offline, the whole review (`fallback_review`). The
thresholds were checked against the art of the M10.1 and M10.2 live builds.
"""

import colorsys
import io
import re
from typing import Literal

import numpy as np
from PIL import Image
from pydantic import BaseModel

from .art import ArtResult, Filled
from .design import Entity, GameDesign

Check = Literal["contrast", "hue", "size", "placeholder", "silhouette"]
Severity = Literal["blocking", "major", "minor"]

MIN_CONTRAST = 0.12  # luminance difference (0-1) between a sprite and its floor
HUE_CLASH = 20  # degrees: an enemy and a pickup this close in dominant hue look alike
SATURATED = 0.35  # a pixel counts for the hue when its saturation and value are above this
HUE_SHARE = 0.3  # ... and the sprite has a dominant hue when this share of it is saturated
SIZE_RANGE = (0.5, 1.6)  # drawn long side / design long side outside this range is flagged
HERO_RATIO = 2.0  # size against the hero off by this factor (either way) is flagged
MIN_FILL = 0.2  # opaque share of the subject's bounding box
MIN_EXTENT = 0.5  # the subject's long side against its frame's
FLOOR_WORDS = {"floor", "ground", "grass", "dirt", "sand", "path", "road", "earth"}


class Finding(BaseModel):
    entity: str
    check: Check
    detail: str
    severity: Severity  # what the offline review uses; the vision reviewer decides live


def first_frame(f: Filled) -> Image.Image:
    image = Image.open(io.BytesIO(f.png)).convert("RGBA")
    if f.kind == "sprite":
        w, h = f.meta["frame"]
        image = image.crop((0, 0, w, h))
    return image


def opaque(image: Image.Image) -> np.ndarray:
    """RGB rows of the visible pixels."""
    rgba = np.asarray(image.convert("RGBA"))
    return rgba[rgba[..., 3] > 127][:, :3].astype(float)


def luminance(rgb: np.ndarray) -> float:
    """Mean Rec. 709 luma of RGB rows, 0-1."""
    if not len(rgb):
        return 0.0
    return float((rgb @ np.array([0.2126, 0.7152, 0.0722])).mean() / 255)


def edge(image: Image.Image) -> np.ndarray:
    """RGB rows of the visible pixels that touch transparency: the silhouette's outline."""
    rgba = np.asarray(image.convert("RGBA"))
    solid = np.pad(rgba[..., 3] > 127, 1)
    inner = solid[:-2, 1:-1] & solid[2:, 1:-1] & solid[1:-1, :-2] & solid[1:-1, 2:]
    ring = solid[1:-1, 1:-1] & ~inner
    return rgba[ring][:, :3].astype(float)


def contrast(image: Image.Image, floor_lum: float) -> float:
    return max(abs(luminance(opaque(image)) - floor_lum), abs(luminance(edge(image)) - floor_lum))


def dominant_hue(image: Image.Image) -> float | None:
    """The hue (degrees) most of a sprite's saturated pixels share; None for grey sprites."""
    rgb = opaque(image) / 255
    if not len(rgb):
        return None
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in rgb])
    strong = hsv[(hsv[:, 1] >= SATURATED) & (hsv[:, 2] >= SATURATED)]
    if len(strong) < HUE_SHARE * len(hsv):
        return None
    degrees = strong[:, 0] * 360
    counts, edges = np.histogram(degrees, bins=24, range=(0, 360))
    peak = (edges[counts.argmax()] + 7.5) % 360
    near = degrees[np.abs((degrees - peak + 180) % 360 - 180) <= 15]
    angle = np.angle(np.exp(1j * np.radians(near)).mean())
    return float(np.degrees(angle) % 360)


def hue_distance(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def subject_box(image: Image.Image) -> tuple[int, int, int, int] | None:
    alpha = image.getchannel("A").point(lambda v: 255 if v > 127 else 0)
    return alpha.getbbox()


def floor_of(design: GameDesign, art: ArtResult) -> str | None:
    """The tile sprites stand on: a tile named like a floor, else the first tile, else the
    background."""
    tiles = [e for e in design.entities if e.role == "tile" and e.id in art.filled]
    named = [
        e for e in tiles if FLOOR_WORDS & set(re.findall(r"[a-z]+", f"{e.id} {e.look}".lower()))
    ]
    pick = (named or tiles or [e for e in design.entities if e.role == "background"] or [None])[0]
    return pick.id if pick and pick.id in art.filled else None


def sprites(design: GameDesign, art: ArtResult) -> list[Entity]:
    return [
        e for e in design.entities if e.role not in {"tile", "background"} and e.id in art.filled
    ]


def precheck(design: GameDesign, art: ArtResult) -> list[Finding]:
    out: list[Finding] = []
    frames = {eid: first_frame(f) for eid, f in art.filled.items()}
    for eid, f in art.filled.items():
        if f.source == "placeholder":
            out.append(Finding(entity=eid, check="placeholder", severity="blocking",
                               detail="greybox placeholder, not real art"))  # fmt: skip
    floor = floor_of(design, art)
    floor_lum = luminance(opaque(frames[floor])) if floor else None
    hero = next((e for e in design.entities if e.role == "player" and e.id in frames), None)
    hero_box = subject_box(frames[hero.id]) if hero else None
    for e in sprites(design, art):
        if art.filled[e.id].source == "placeholder":
            continue
        image = frames[e.id]
        box = subject_box(image)
        if box is None:
            out.append(Finding(entity=e.id, check="silhouette", severity="blocking",
                               detail="the frame is empty"))  # fmt: skip
            continue
        if floor_lum is not None and (diff := contrast(image, floor_lum)) < MIN_CONTRAST:
            out.append(Finding(
                entity=e.id, check="contrast", severity="major",
                detail=f"luminance {luminance(opaque(image)):.2f} (outline "
                f"{luminance(edge(image)):.2f}) on a floor of {floor_lum:.2f} ({floor}): "
                f"difference {diff:.2f}, below {MIN_CONTRAST}",
            ))  # fmt: skip
        bw, bh = box[2] - box[0], box[3] - box[1]
        fill = len(opaque(image.crop(box))) / (bw * bh)
        extent = max(bw / image.width, bh / image.height)
        if fill < MIN_FILL:
            out.append(Finding(entity=e.id, check="silhouette", severity="minor",
                               detail=f"sparse shape: {fill:.0%} of its bounding box is filled"))  # fmt: skip
        if extent < MIN_EXTENT:
            out.append(Finding(entity=e.id, check="silhouette", severity="major",
                               detail=f"the subject fills only {extent:.0%} of its "
                               f"{image.width}x{image.height} frame"))  # fmt: skip
        long_side, design_long = max(bw, bh), max(e.width, e.height) * art.scale
        ratio = long_side / design_long
        if not SIZE_RANGE[0] <= ratio <= SIZE_RANGE[1]:
            out.append(Finding(entity=e.id, check="size", severity="major",
                               detail=f"drawn {long_side} px long, {ratio:.1f}x its design "
                               f"size ({design_long} px)"))  # fmt: skip
        if hero and hero_box and e.id != hero.id:
            hero_long = max(hero_box[2] - hero_box[0], hero_box[3] - hero_box[1])
            expected = max(e.width, e.height) / max(hero.width, hero.height)
            actual = long_side / hero_long
            if not 1 / HERO_RATIO < actual / expected < HERO_RATIO:
                out.append(Finding(entity=e.id, check="size", severity="major",
                                   detail=f"{actual:.1f}x the hero's size; the design says "
                                   f"{expected:.1f}x"))  # fmt: skip
    roles = {e.id: e.role for e in design.entities}
    hues = {e.id: dominant_hue(frames[e.id]) for e in sprites(design, art)
            if art.filled[e.id].source != "placeholder"}  # fmt: skip
    for enemy in [k for k, h in hues.items() if roles[k] == "enemy" and h is not None]:
        for pickup in [k for k, h in hues.items() if roles[k] == "pickup" and h is not None]:
            gap = hue_distance(hues[enemy], hues[pickup])
            if gap <= HUE_CLASH:
                out.append(Finding(
                    entity=pickup, check="hue", severity="major",
                    detail=f"same dominant hue as the enemy {enemy} ({hues[pickup]:.0f} vs "
                    f"{hues[enemy]:.0f} deg hue): a reward that looks like a threat",
                ))  # fmt: skip
    return out
