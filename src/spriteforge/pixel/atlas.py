"""Deterministic shelf packing; frame keys are preserved verbatim for Phaser."""

import json
import math
from collections.abc import Mapping, Sequence
from pathlib import Path

from PIL import Image
from pydantic import Field

from .schemas import Schema


class Rect(Schema):
    x: int
    y: int
    w: int
    h: int


class Size(Schema):
    w: int
    h: int


class AtlasFrame(Schema):
    frame: Rect
    rotated: bool = False
    trimmed: bool = False
    spriteSourceSize: Rect
    sourceSize: Size


class AtlasMeta(Schema):
    app: str = "SpriteForge"
    version: str = "1"
    image: str = "atlas.png"
    format: str = "RGBA8888"
    size: Size
    scale: str = "1"
    # Phaser ignores this metadata; use it to create scene.anims frame sequences.
    animations: dict[str, list[str]] = Field(default_factory=dict)


class Atlas(Schema):
    frames: dict[str, AtlasFrame]
    meta: AtlasMeta


def pack(
    sprites: Mapping[str, Image.Image],
    output_dir: Path,
    *,
    animations: Mapping[str, Sequence[str]] | None = None,
    padding: int = 1,
    max_size: int = 2048,
) -> Atlas:
    if not sprites:
        raise ValueError("Cannot pack an empty atlas")
    if {"__BASE", "hasOwnProperty"} & sprites.keys():
        raise ValueError("Frame name is reserved by the Phaser JSON-hash parser")
    if padding < 0 or max_size < 1 or any(not key for key in sprites):
        raise ValueError("Invalid padding, maximum size, or empty frame key")
    animation_map = {name: list(keys) for name, keys in (animations or {}).items()}
    for name, keys in animation_map.items():
        if not name or not keys or any(key not in sprites for key in keys):
            raise ValueError(f"Animation {name!r} must reference existing atlas frames")
    ordered = sorted(sprites, key=lambda key: (-sprites[key].height, -sprites[key].width, key))
    largest = max(img.width + 2 * padding for img in sprites.values())
    if largest > max_size or any(img.height + 2 * padding > max_size for img in sprites.values()):
        raise ValueError("A sprite exceeds max_size including padding")
    area = sum((img.width + 2 * padding) * (img.height + 2 * padding) for img in sprites.values())
    width = min(max_size, max(largest, math.ceil(math.sqrt(area))))
    while True:
        x = y = row_height = 0
        placed = {}
        for key in ordered:
            img = sprites[key]
            w, h = img.width + 2 * padding, img.height + 2 * padding
            if x + w > width:
                x, y, row_height = 0, y + row_height, 0
            placed[key] = (x + padding, y + padding)
            x += w
            row_height = max(row_height, h)
        height = y + row_height
        if height <= max_size:
            break
        if width == max_size:
            raise ValueError("Sprites do not fit within max_size")
        width = min(max_size, width * 2)
    image = Image.new("RGBA", (width, height))
    frames = {}
    for key in sorted(sprites):
        img = sprites[key].convert("RGBA")
        x, y = placed[key]
        image.paste(img, (x, y))
        frames[key] = AtlasFrame(
            frame=Rect(x=x, y=y, w=img.width, h=img.height),
            spriteSourceSize=Rect(x=0, y=0, w=img.width, h=img.height),
            sourceSize=Size(w=img.width, h=img.height),
        )
    atlas = Atlas(
        frames=frames, meta=AtlasMeta(size=Size(w=width, h=height), animations=animation_map)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    image.save(output_dir / "atlas.png")
    (output_dir / "atlas.json").write_text(
        json.dumps(atlas.model_dump(), indent=2) + "\n", encoding="utf-8"
    )
    return atlas
