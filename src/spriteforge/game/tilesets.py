"""Tile sets: named tiles cut from installed tileset sheets, with seasonal variants and parallax.

One JSON per tile set in `library/tilesets/` (see its README): which pack and sheet, the camera
it is drawn for, its tile size, and named tiles (`floor`, `wall`, `ground_top`, ...) at grid
cells. A tile set is offered only when its pack is installed. Tiles are referred to as
`<tileset>/<tile>` (e.g. `pixel-crawler-dungeon/floor`).
"""

import json
from pathlib import Path

from PIL import Image

from spriteforge.library.store import library_root, search

TILESETS_DIR = Path(__file__).parents[1] / "library" / "tilesets"


def load_tilesets(camera: str = "") -> list[dict]:
    """Installed tile sets, optionally only those drawn for `camera` (top-down/side-view)."""
    out = []
    for path in sorted(TILESETS_DIR.glob("*.json")):
        ts = json.loads(path.read_text(encoding="utf-8"))
        if (library_root() / ts["pack"]).exists() and (not camera or ts["camera"] == camera):
            out.append(ts)
    return out


def tile_ids(tilesets: list[dict]) -> dict[str, tuple[dict, str]]:
    return {f"{ts['id']}/{name}": (ts, name) for ts in tilesets for name in ts["tiles"]}


def pick_variant(ts: dict, text_words: set[str]) -> str:
    return next(
        (v for v, spec in ts["variants"].items() if text_words & set(spec["words"])), "normal"
    )


def _asset_file(asset_id: str) -> Path:
    return library_root() / next(a["file"] for a in search() if a["id"] == asset_id)


def tile_image(ts: dict, name: str, variant: str = "normal") -> Image.Image:
    """One tile at the tile set's native size."""
    spec = ts["tiles"][name]
    size = ts["tile_size"]
    sheet = Image.open(_asset_file(spec["asset"])).convert("RGBA")
    cx, cy = spec["cell"]
    if spec.get("seasonal"):
        cy += ts["variants"].get(variant, ts["variants"]["normal"])["row_offset"]
    tile = sheet.crop((cx * size, cy * size, cx * size + size, cy * size + size))
    if spec.get("flip_y"):
        tile = tile.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return tile


def backdrop_layers(ts: dict, variant: str = "normal") -> list[tuple[str, Image.Image, float]]:
    """Parallax layers back to front as (name, image, scroll factor); [] without a backdrop."""
    spec = ts.get("backdrop")
    if not spec:
        return []
    files = {a["id"]: a["file"] for a in search()}
    tag = ts["variants"].get(variant, {}).get("backdrop", "normal-bg")
    layers = []
    for i, (layer, factor) in enumerate(zip(spec["layers"], spec["factors"], strict=True)):
        asset = spec["asset_pattern"].format(variant=tag, layer=layer)
        if asset in files:
            image = Image.open(library_root() / files[asset]).convert("RGBA")
            layers.append((f"backdrop_{i}", image, factor))
    return layers
