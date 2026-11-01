"""Library sheets grouped into animated actors, and fitting their frames to a sprite size.

`load_actors` groups `<creature> <verb> [direction]` sheets from shippable packs (e.g. Pixel
Crawler's "orc warrior run"), `adapt` turns chosen animations into one strip on a shared feet
line, `recolor` hue-shifts a creature. Used by the game art step; nothing here calls a model.
"""

import colorsys
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from spriteforge.library.store import library_root, search

# Animation verbs found in library file names -> canonical verb.
VERBS = {
    "idle": "idle",
    "walk": "walk",
    "run": "run",
    "flight": "fly",
    "fly": "fly",
    "jump": "jump",
    "fall": "fall",
    "attack": "attack",
    "slice": "attack",
    "death": "death",
    "hit": "hurt",
    "hurt": "hurt",
    "shield": "shield",
    "pierce": "pierce",
    "crush": "crush",
    "collect": "collect",
    "carry": "carry",
    "fishing": "fishing",
}
NOISE = {"take", "side", "up", "down", "npcs", "crew", "sheet", "base"}
# What each wanted animation may be built from, in preference order.
ANIM_SOURCES = {
    "idle": ["idle", "fly", "walk", "run"],
    "run": ["run", "walk", "fly", "idle"],
    "walk": ["walk", "run", "fly", "idle"],
    "move": ["run", "walk", "fly", "idle"],
    "jump": ["jump", "idle"],
    "fall": ["fall", "jump", "idle"],
    "hurt": ["hurt", "death", "idle"],
    "attack": ["attack", "pierce", "idle"],
}
SYNONYMS = {
    "undead": "skeleton",
    "skeletons": "skeleton",
    "bones": "skeleton",
    "bony": "skeleton",
    "goblins": "goblin",
    "orcs": "orc",
    "mushrooms": "mushroom",
    "fungus": "mushroom",
    "fungi": "mushroom",
    "shroom": "mushroom",
    "shrooms": "mushroom",
    "eyes": "eye",
    "eyeball": "eye",
    "eyeballs": "eye",
    "beholder": "eye",
    "knights": "knight",
    "paladin": "knight",
    "soldier": "warrior",
    "warriors": "warrior",
    "mage": "wizzard",
    "wizard": "wizzard",
    "wizards": "wizzard",
    "sorcerer": "wizzard",
    "shamans": "shaman",
    "thief": "rogue",
    "rogues": "rogue",
    "assassin": "rogue",
    "bat": "fly",
    "bats": "fly",
    "flying": "fly",
    "flyer": "fly",
    "flyers": "fly",
    "farmer": "peasant",
    "farmers": "peasant",
    "farming": "peasant",
    "farm": "peasant",
    "villager": "citizen",
    "villagers": "citizen",
    "builder": "peasant",
    "settler": "peasant",
}
COLORS = {
    "red": 0,
    "crimson": 350,
    "orange": 28,
    "yellow": 52,
    "golden": 45,
    "gold": 45,
    "green": 120,
    "lime": 95,
    "teal": 175,
    "cyan": 185,
    "blue": 220,
    "skyblue": 200,
    "purple": 275,
    "violet": 270,
    "pink": 320,
    "magenta": 300,
}


STOP = {
    "a",
    "an",
    "the",
    "of",
    "in",
    "and",
    "with",
    "full",
    "f",
    "b",
    "to",
    "is",
    "are",
    "who",
    "that",
    "where",
    "on",
    "at",
    "by",
    "for",
    "game",
    "my",
    "his",
    "her",
    "its",
}


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", text.lower()) if w not in STOP]


def canon(tokens) -> set[str]:
    return {SYNONYMS.get(t, t) for t in tokens}


# --- actors: library sheets grouped into creatures with animations --------------------------


@dataclass
class Actor:
    key: str
    pack: str
    name: str
    category: str
    tags: set[str]
    anims: dict[str, list[Image.Image]] = field(default_factory=dict)
    faces: str = "right"

    @property
    def tokens(self) -> set[str]:
        """Words that identify the creature (its name), not the pack it came from."""
        return canon(words(self.name))

    def side(self) -> str:
        return "side" if "side-view" in self.tags else "topdown"


def sheet_frames(path: Path, cell: tuple[int, int]) -> list[Image.Image]:
    sheet = Image.open(path).convert("RGBA")
    w, h = cell
    out = []
    for y in range(0, sheet.height - h + 1, h):
        for x in range(0, sheet.width - w + 1, w):
            frame = sheet.crop((x, y, x + w, y + h))
            if frame.getchannel("A").getbbox():
                out.append(frame)
    return out


def load_actors() -> list[Actor]:
    """Group `<creature> <verb> [direction]` sheets; side-facing sheets win over up/down."""
    root, actors = library_root(), {}
    index_file = root / "index.json"
    packs = (
        json.loads(index_file.read_text(encoding="utf-8"))["packs"] if index_file.exists() else {}
    )
    for a in search(usage="ship"):
        if a["category"] not in {"character", "enemy"}:
            continue
        tokens = words(a["name"])
        verbs = [VERBS[t] for t in tokens if t in VERBS]
        if not verbs or a["frames"] < 1:
            continue
        direction = next((t for t in tokens if t in {"side", "up", "down"}), "side")
        name = " ".join(t for t in tokens if t not in VERBS and t not in NOISE).strip()
        key = f"{a['pack']}/{name}"
        actor = actors.setdefault(
            key,
            Actor(
                key,
                a["pack"],
                name,
                a["category"],
                set(a["tags"]),
                faces=packs.get(a["pack"], {}).get("faces", "right"),
            ),
        )
        verb = verbs[0]
        if verb in actor.anims and direction != "side":
            continue
        actor.anims[verb] = sheet_frames(root / a["file"], tuple(a["cell"]))
    return [a for a in actors.values() if a.anims]


# --- fitting frames to a sprite size ---------------------------------------------------------


def downscale(img: Image.Image, k: int) -> Image.Image:
    """Integer pixel-art downscale: keep the most opaque-covered colour of each k x k block."""
    if k == 1:
        return img
    a = np.asarray(img)
    h, w = a.shape[0] // k * k, a.shape[1] // k * k
    blocks = (
        a[:h, :w]
        .reshape(h // k, k, w // k, k, 4)
        .transpose(0, 2, 1, 3, 4)
        .reshape(h // k, w // k, k * k, 4)
    )
    out = np.zeros((h // k, w // k, 4), dtype=np.uint8)
    for y in range(out.shape[0]):
        for x in range(out.shape[1]):
            px = blocks[y, x]
            solid = px[px[:, 3] > 127]
            if len(solid) * 2 >= len(px):
                vals, counts = np.unique(solid, axis=0, return_counts=True)
                out[y, x] = vals[counts.argmax()]
    return Image.fromarray(out, "RGBA")


def face_right(anims: dict[str, list[Image.Image]], faces: str) -> dict[str, list[Image.Image]]:
    """Games draw every sprite facing right and flip it when it moves left."""
    if faces != "left":
        return anims
    mirror = Image.Transpose.FLIP_LEFT_RIGHT
    return {k: [f.transpose(mirror) for f in v] for k, v in anims.items()}


def common_canvas(anims: dict[str, list[Image.Image]]) -> dict[str, list[Image.Image]]:
    """Packs draw animations on different canvases (Pixel Crawler: idle 32 px, run 64 px).
    Put every animation on one canvas, its content bottom-centred, so boxes are comparable."""
    boxes = {}
    for name, frames in anims.items():
        found = [b for f in frames if (b := f.getchannel("A").getbbox())]
        if found:
            boxes[name] = (
                min(b[0] for b in found),
                min(b[1] for b in found),
                max(b[2] for b in found),
                max(b[3] for b in found),
            )
    if not boxes or len({f.size for v in anims.values() for f in v}) == 1:
        return anims
    cw = max(b[2] - b[0] for b in boxes.values()) + 2
    ch = max(b[3] - b[1] for b in boxes.values()) + 2
    out = {}
    for name, frames in anims.items():
        x0, y0, x1, y1 = boxes.get(name, (0, 0, *frames[0].size))
        placed = []
        for f in frames:
            cell = Image.new("RGBA", (cw, ch))
            part = f.crop((x0, y0, x1, y1))
            cell.paste(part, ((cw - part.width) // 2, ch - 1 - part.height), part)
            placed.append(cell)
        out[name] = placed
    return out


def adapt(anims: dict[str, list[Image.Image]], slot_frames: dict[str, int], size, topdown: bool):
    """Return (strip, frame counts, body) for a sprite of `size` from source animations.

    Every frame is placed relative to one anchor (union of the idle/move frames' opaque
    boxes), feet on the bottom row, so the creature doesn't jitter between animations.
    """
    w, h = size
    chosen: dict[str, list[Image.Image]] = {}
    for anim in slot_frames:
        src = next((v for v in ANIM_SOURCES.get(anim, [anim]) if v in anims), None)
        frames = anims[src] if src else next(iter(anims.values()))
        if anim == "jump" and src == "jump" and "fall" not in anims and len(frames) > 1:
            frames = frames[: len(frames) // 2]
        if anim == "fall" and src == "jump" and len(frames) > 1:
            frames = frames[len(frames) // 2 :]
        if anim == "hurt" and src == "death":
            frames = frames[:2]
        if len(frames) > 8:  # keep animations short: evenly spaced subset
            frames = [frames[round(i * (len(frames) - 1) / 7)] for i in range(8)]
        chosen[anim] = frames
    chosen = common_canvas(chosen)
    anchor_frames = [f for a in ("idle", "move", "run", "walk") if a in chosen for f in chosen[a]]
    boxes = [f.getchannel("A").getbbox() for f in anchor_frames or chosen[next(iter(chosen))]]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    k = max(1, -(-(y1 - y0) // int(h * 0.9)), -(-(x1 - x0) // w))  # integer shrink to fit
    up = 1
    if k == 1 and (y1 - y0) * 2 <= h * 0.8 and (x1 - x0) * 2 <= w:
        up = 2  # tiny source in a big slot (16 px art in a 32 px build)
    cx = (x0 + x1) / 2
    counts, cells = {}, []
    for anim, frames in chosen.items():
        counts[anim] = len(frames)
        for f in frames:
            f = downscale(f, k) if k > 1 else f
            if up > 1:
                f = f.resize((f.width * up, f.height * up), Image.Resampling.NEAREST)
            s = up / k
            cell = Image.new("RGBA", (w, h))
            cell.paste(f, (round(w / 2 - cx * s), round(h - y1 * s)), f)
            cells.append(cell)
    strip = Image.new("RGBA", (w * len(cells), h))
    for i, c in enumerate(cells):
        strip.paste(c, (i * w, 0))
    bx0, by0, bx1, by1 = cells[0].getchannel("A").getbbox() or (0, 0, w, h)
    bw = max(4, int((bx1 - bx0) * 0.6))
    if topdown:  # feet only, so actors can overlap wall faces like classic top-down games
        bh = max(4, min(10, (by1 - by0) // 3))
        body = ((w - bw) // 2, h - bh - 1, bw, bh)
    else:
        bh = max(6, int((by1 - by0) * 0.85))
        body = ((w - bw) // 2, h - bh, bw, bh)
    return strip, counts, body


def _hsv(strip: Image.Image) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(hsv, alpha, mask of saturated drawn pixels) of an image, all 0-1."""
    a = np.asarray(strip.convert("RGBA")).astype(float) / 255
    rgb, alpha = a[..., :3], a[..., 3]
    hsv = np.array([colorsys.rgb_to_hsv(*p) for p in rgb.reshape(-1, 3)]).reshape(rgb.shape)
    mask = (alpha > 0.5) & (hsv[..., 1] > 0.25) & (hsv[..., 2] > 0.15)
    return hsv, alpha, mask


def dominant_hue(strip: Image.Image) -> float | None:
    """The main hue of the saturated pixels, 0-1 (None: no saturated pixels)."""
    hsv, _, mask = _hsv(strip)
    if not mask.any():
        return None
    hist = np.histogram(hsv[..., 0][mask], bins=36, range=(0, 1))[0]
    return (hist.argmax() + 0.5) / 36


def recolor(strip: Image.Image, hue_deg: float, value: float | None = None) -> Image.Image:
    """Rotate the dominant hue of saturated pixels to `hue_deg`, keeping their shading. With
    `value` (0-1, the target colour's HSV value) those pixels' mean brightness is lifted to it
    when it is lower: a hue turn alone left a dark olive cloak dark teal (a near-black hero).
    It is never lowered: a "dark red" orc went darker than its dark floor (both live,
    2026-11-01)."""
    hsv, alpha, mask = _hsv(strip)
    if not mask.any():
        return strip
    hist = np.histogram(hsv[..., 0][mask], bins=36, range=(0, 1))[0]
    dominant = (hist.argmax() + 0.5) / 36
    near = mask & (np.abs(((hsv[..., 0] - dominant + 0.5) % 1) - 0.5) < 0.12)
    hsv[..., 0] = np.where(near, (hsv[..., 0] - dominant + hue_deg / 360) % 1, hsv[..., 0])
    v = hsv[..., 2]
    mean = float(v[near].mean())
    if value is not None and value > mean:
        moved = 1 - (1 - v) * (1 - value) / max(1 - mean, 1e-6)
        hsv[..., 2] = np.where(near, np.clip(moved, 0, 1), v)
    out = np.array([colorsys.hsv_to_rgb(*p) for p in hsv.reshape(-1, 3)]).reshape(hsv.shape)
    result = np.dstack([out, alpha]) * 255
    return Image.fromarray(result.round().astype(np.uint8), "RGBA")
