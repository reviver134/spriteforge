"""Pixel contracts shared by processing, checks, and saved palettes."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Dimension = Annotated[int, Field(ge=1, le=4096)]


def hex_color(value: str) -> str:
    if (
        len(value) != 7
        or not value.startswith("#")
        or any(c not in "0123456789abcdefABCDEF" for c in value[1:])
    ):
        raise ValueError("Colors must use #RRGGBB")
    return value.upper()


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Palette(Schema):
    name: str = "custom"
    colors: list[str] = Field(min_length=1, max_length=256)

    @field_validator("colors")
    @classmethod
    def normalize(cls, colors):
        return list(dict.fromkeys(hex_color(c) for c in colors))


class Style(Schema):
    palette: list[str] = Field(min_length=1, max_length=256)
    background: str = "#FF00FF"
    chroma_tolerance: float = Field(default=45, ge=0, le=442)
    alpha_threshold: int = Field(default=128, ge=1, le=255)
    min_cluster_size: int = Field(default=2, ge=0)
    outline: bool = False
    outline_color: str | None = None
    max_colors: int = Field(default=16, ge=1, le=256)

    @field_validator("palette")
    @classmethod
    def normalize(cls, colors):
        return list(dict.fromkeys(hex_color(c) for c in colors))

    @field_validator("background", "outline_color")
    @classmethod
    def normalize_color(cls, color):
        return hex_color(color) if color is not None else None

    @model_validator(mode="after")
    def outline_is_in_palette(self):
        if self.outline_color and self.outline_color not in self.palette:
            raise ValueError("outline_color must belong to the palette")
        return self


class AssetSpec(Schema):
    id: str = "sprite"
    type: Literal["sprite", "character", "tile", "item", "ui"] = "sprite"
    size: tuple[Dimension, Dimension] = (32, 32)
    padding: int | None = Field(default=None, ge=0)
    allow_edge_touch: bool | None = None
    max_colors: int | None = Field(default=None, ge=1, le=256)
    min_fill_ratio: float = Field(default=0.05, ge=0, le=1)
    seam_threshold: float = Field(default=0.05, ge=0, le=1)

    @property
    def margin(self) -> int:
        return self.padding if self.padding is not None else (0 if self.type == "tile" else 1)

    @property
    def edges_allowed(self) -> bool:
        return self.allow_edge_touch if self.allow_edge_touch is not None else self.type == "tile"

    @model_validator(mode="after")
    def usable_canvas(self):
        if min(self.size) <= 2 * self.margin:
            raise ValueError("padding leaves no usable canvas")
        return self


class Failure(Schema):
    code: str
    message: str
    fix_hint: str
