"""Gate 2 contracts. Layouts refer only to the building blocks in this run."""

from typing import Literal

from pydantic import Field, field_validator, model_validator

from spriteforge.agents.models import Contract
from spriteforge.pixel.schemas import Style

from .asset_models import AssetSpec, Manifest, Size


class Placement(Contract):
    asset: str
    x: int = Field(ge=0, lt=320)
    y: int = Field(ge=0, lt=180)


class HUD(Contract):
    hearts: int = Field(ge=1, le=5)
    pickup_count: int = Field(ge=0, le=99)
    weapon: str = Field(min_length=1, max_length=16, pattern=r"^[A-Za-z0-9 -]+$")


class RoomLayout(Contract):
    perspective: Literal["top-down", "side-scroll", "isometric"]
    environment: str
    width: Literal[20]
    height: Literal[11]
    tile_size: Literal[16]
    tiles: list[list[str]] = Field(min_length=11, max_length=11)
    entities: list[Placement] = Field(min_length=6, max_length=20)
    hud: HUD

    @model_validator(mode="after")
    def grid(self):
        if any(len(row) != 20 for row in self.tiles):
            raise ValueError("Room must have exactly 20 columns and 11 rows")
        return self

    def validate_for(self, brief, manifest):
        if self.perspective != brief.perspective or self.environment != brief.environment:
            raise ValueError("Layout must preserve the brief's perspective and environment exactly")
        specs = {s.id: s for s in manifest.assets}
        tile_ids = {s.id for s in manifest.assets if s.type == "tile"}
        if any(cell not in tile_ids | {"empty"} for row in self.tiles for cell in row):
            raise ValueError("Layout references an unknown tile")
        if tile_ids - {cell for row in self.tiles for cell in row}:
            raise ValueError(
                "Layout must show every floor and wall variant, with walls representing the environment"
            )
        required = set(specs) - tile_ids
        if {e.asset for e in self.entities} != required:
            raise ValueError("Room must display every character, prop and pickup")
        if sum(e.asset == "hero" for e in self.entities) != 1:
            raise ValueError("Room needs exactly one hero")
        for entity in self.entities:
            width, height = specs[entity.asset].size
            if not width // 2 <= entity.x <= 320 - width // 2 or not height + 16 <= entity.y <= 176:
                raise ValueError("Entity canvas overlaps the HUD or leaves the screen")
            if self.perspective == "top-down" and self.tiles[(entity.y - 1) // 16][
                entity.x // 16
            ].startswith("wall"):
                raise ValueError("Entity feet must not be inside a wall")
        return self


class PreviewPlan(Contract):
    version: int = Field(ge=1)
    versions: dict[str, int]
    instructions: dict[str, str]
    style: Style
    layout_instruction: str = ""
    recolor: bool = False
    reprocess_targets: list[str] = Field(default_factory=list)
    canvas_overrides: dict[str, Size] = Field(default_factory=dict)


class PreviewChange(Contract):
    targets: list[str] = Field(min_length=1, max_length=14)
    instruction: str = Field(min_length=1)
    palette: list[str] = Field(max_length=16)

    @field_validator("palette")
    @classmethod
    def colors(cls, value):
        return Style(palette=value).palette if value else []


class BlockDescription(Contract):
    id: str
    description: str = Field(min_length=1, max_length=1000)


class BlockDescriptions(Contract):
    assets: list[BlockDescription] = Field(min_length=12, max_length=12)


def block_manifest(brief, descriptions=None):
    """Fixed bounded scope; the designer supplies brief-specific descriptions."""
    blocks = [
        ("hero", "character", brief.character),
        ("enemy_1", "character", "First distinct enemy"),
        ("enemy_2", "character", "Second distinct enemy"),
        *[
            (f"floor_{i}", "tile", f"Subtle seamless floor material variation {i}")
            for i in range(1, 4)
        ],
        *[(f"wall_{i}", "tile", f"Seamless wall material variation {i}") for i in range(1, 4)],
        ("prop_1", "item", "Environment prop, a small obstacle"),
        ("prop_2", "item", "Environment prop, a landmark"),
        ("pickup", "item", "A bright readable key pickup"),
    ]
    chosen = {a.id: a.description for a in descriptions.assets} if descriptions else {}
    if descriptions and (set(chosen) != {b[0] for b in blocks} or len(chosen) != 12):
        raise ValueError("Designer must describe exactly the requested 12 building blocks")
    return Manifest(
        assets=[
            AssetSpec(
                id=ident,
                type=kind,
                size=(48, 48) if kind == "character" else (16, 16),
                frames={"idle": 1},
                desc=chosen.get(ident, desc) + "; " + brief.environment,
                depends_on=[],
                refs=["anchor"],
            )
            for ident, kind, desc in blocks
        ]
    )


def example_layout(brief):
    side = brief.perspective == "side-scroll"
    tiles = [
        [
            (
                "wall_" + str(1 + x % 3)
                if (y == 10 if side else x in (0, 19) or y in (0, 10))
                else "empty"
                if side and y < 9
                else "floor_" + str(1 + (x + y) % 3)
            )
            for x in range(20)
        ]
        for y in range(11)
    ]
    positions = (
        [(80, 144), (176, 144), (256, 144), (40, 144), (216, 144), (128, 128)]
        if side
        else [(112, 112), (224, 96), (208, 144), (56, 96), (264, 128), (144, 144)]
    )
    return RoomLayout(
        perspective=brief.perspective,
        environment=brief.environment,
        width=20,
        height=11,
        tile_size=16,
        tiles=tiles,
        entities=[
            Placement(asset=a, x=x, y=y)
            for a, (x, y) in zip(
                ["hero", "enemy_1", "enemy_2", "prop_1", "prop_2", "pickup"], positions, strict=True
            )
        ],
        hud=HUD(hearts=3, pickup_count=0, weapon="SWORD"),
    )
