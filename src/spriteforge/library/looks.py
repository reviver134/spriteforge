"""What a library image looks like, measured from its pixels (free; no model sees it).

`measure` names the main colours and gives brightness (mean Rec. 709 luma of the drawn
pixels, 0-1) and detail (the spread between the 10th and 90th percentile of that luma). The
library descriptions carry it for every asset, the Art Director's list shows it, and the art
step compares library picks with the colours a design names (`colour_gap`).
"""

import colorsys
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel

LUMA = np.array([0.2126, 0.7152, 0.0722])
FLAT = 0.03  # detail under this: one flat colour (e.g. a wall top with no bricks)


class Colour(BaseModel):
    name: str
    hex: str
    share: float  # of the drawn pixels


class Look(BaseModel):
    colours: list[Colour]  # main colours, largest first
    brightness: float
    detail: float
    fill: float  # drawn share of the image
    mean: str  # mean colour of the drawn pixels
    text: str  # "tan and brown; mid (0.49); low-contrast detail"


def hex_of(rgb) -> str:
    return "#" + "".join(f"{int(np.rint(c)):02X}" for c in rgb[:3])


def rgb_of(hex_code: str) -> tuple[int, int, int]:
    return tuple(int(hex_code[i : i + 2], 16) for i in (1, 3, 5))


def colour_name(rgb) -> str:
    """A plain colour word for an RGB colour: 'tan', 'dark grey', 'grey-teal', 'olive' ..."""
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb[:3]))
    if v < 0.13:
        return "black"
    if s < 0.13 or (s < 0.2 and v < 0.3):
        return (
            "dark grey" if v < 0.4 else "grey" if v < 0.68 else "light grey" if v < 0.9 else "white"
        )
    deg = h * 360
    if deg < 15 or deg >= 345:
        base = "red"
    elif deg < 42:
        base = "tan" if s < 0.45 and v >= 0.45 else "brown" if v < 0.62 else "orange"
    elif deg < 70:
        base = "olive" if v < 0.55 else "cream" if s < 0.4 else "yellow"
    elif deg < 160:
        base = "green"
    elif deg < 195:
        base = "cyan" if v > 0.75 and s > 0.45 else "teal"
    elif deg < 255:
        base = "blue"
    elif deg < 290:
        base = "purple"
    else:
        base = "pink" if v > 0.75 and s < 0.55 else "magenta"
    earthy = base in {"tan", "brown", "olive", "cream"}
    if s < 0.3 and not earthy:
        base = f"grey-{base}"
    if v < 0.38 and base not in {"brown", "olive"}:
        return f"dark {base}"
    if v > 0.88 and s < 0.5 and not earthy:
        return f"light {base}"
    return base


def brightness_word(value: float) -> str:
    for limit, word in ((0.2, "very dark"), (0.38, "dark"), (0.6, "mid"), (0.8, "light")):
        if value < limit:
            return word
    return "very light"


def detail_word(value: float) -> str:
    if value < FLAT:
        return "flat, no detail"
    return "low-contrast detail" if value < 0.12 else "high-contrast detail"


def measure(image: Image.Image) -> Look:
    a = np.asarray(image.convert("RGBA"))
    drawn = a[..., 3] > 127
    rgb = a[drawn][:, :3]
    if not len(rgb):
        return Look(colours=[], brightness=0, detail=0, fill=0, mean="#000000", text="empty")
    luma = rgb.astype(float) @ LUMA / 255
    colours, counts = np.unique(rgb, axis=0, return_counts=True)
    groups: dict[str, list] = {}
    for colour, count in zip(colours, counts, strict=True):
        groups.setdefault(colour_name(colour), []).append((colour.astype(float), int(count)))
    total = int(counts.sum())
    named = []
    for name, items in groups.items():
        n = sum(k for _, k in items)
        mean = sum(c * k for c, k in items) / n
        named.append(Colour(name=name, hex=hex_of(mean), share=round(n / total, 3)))
    named.sort(key=lambda c: (-c.share, c.name))
    main = [c for c in named if c.share >= 0.08][:4] or named[:1]
    words = [c.name for c in main]
    listed = ", ".join(words[:-1]) + " and " + words[-1] if len(words) > 1 else words[0]
    brightness = round(float(luma.mean()), 3)
    detail = round(float(np.percentile(luma, 90) - np.percentile(luma, 10)), 3)
    return Look(
        colours=main,
        brightness=brightness,
        detail=detail,
        fill=round(float(drawn.mean()), 3),
        mean=hex_of(rgb.astype(float).mean(axis=0)),
        text=f"{listed}; {brightness_word(brightness)} ({brightness:.2f}); {detail_word(detail)}",
    )


def lab(hex_code: str) -> np.ndarray:
    """CIELAB (D65) of an sRGB colour."""
    rgb = np.array(rgb_of(hex_code)) / 255
    linear = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    xyz = (
        np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
        @ linear
        / np.array([0.95047, 1.0, 1.08883])
    )
    f = np.where(xyz > (6 / 29) ** 3, np.cbrt(xyz), xyz / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def colour_gap(a: str, b: str) -> float:
    """How far apart two colours look (CIE76 delta E; about 25 and up: clearly different)."""
    return float(np.linalg.norm(lab(a) - lab(b)))


def asset_image(asset: dict, root: Path) -> Image.Image:
    """What an asset shows: its first drawn frame for an animation sheet, else the image."""
    image = Image.open(root / asset["file"]).convert("RGBA")
    w, h = asset["cell"]
    if asset.get("frames", 1) <= 1 or (w, h) == image.size:
        return image
    for y in range(0, image.height - h + 1, h):
        for x in range(0, image.width - w + 1, w):
            frame = image.crop((x, y, x + w, y + h))
            if frame.getchannel("A").getbbox():
                return frame
    return image.crop((0, 0, w, h))
