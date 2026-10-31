"""Asset library records: every third-party file carries its pack's source and license."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Licenses that allow shipping the art inside a generated game without extra steps.
SHIPPABLE = {"CC0", "CC0-1.0", "PUBLIC-DOMAIN", "OWN"}
# Free-to-use custom licenses: fine inside a built game, but the raw files may not be
# redistributed (kept out of git via `redistributable: false`).
FREE_USE = {"FREE-USE"}
# Licenses that allow shipping only with attribution (credits file is written into the build).
ATTRIBUTION = {"CC-BY-3.0", "CC-BY-4.0", "OGA-BY-3.0", "MIT"}

Category = Literal[
    "character",
    "enemy",
    "tileset",
    "prop",
    "item",
    "equipment",
    "ui",
    "effect",
    "background",
    "other",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AssetEntry(Strict):
    id: str  # "<pack>/<slug>"
    file: str  # path relative to the library root
    category: Category
    subcategory: str = ""
    name: str
    tags: list[str] = Field(default_factory=list)
    size: tuple[int, int]
    # Grid cell when the file is a sheet; equals size for a single sprite.
    cell: tuple[int, int]
    frames: int = 1
    colors: int
    opaque_bbox: tuple[int, int, int, int] | None
    transparent_background: bool
    sha256: str


class Pack(Strict):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str
    author: str = "unknown"
    source_url: str = ""
    license: str = "UNKNOWN"
    license_url: str = ""
    license_verified: bool = False
    notes: str = ""
    original_archive: str = ""
    # False: raw files may not be shared (public repo, zip of the library); builds still may.
    redistributable: bool = True
    # False: the license forbids AI use (e.g. "no AI training"); never send to Muse as a
    # reference or training input. Builds may still include the art.
    ai_allowed: bool = True
    # Which way side-facing sprites in this pack look. Kits expect right-facing art and flip
    # it when moving left, so "left" packs are mirrored when they are adapted into a slot.
    faces: Literal["right", "left"] = "right"
    assets: list[AssetEntry] = Field(default_factory=list)

    @property
    def usage(self) -> Literal["ship", "ship-with-credit", "reference-only"]:
        """What a build may do with this pack. Unverified packs are never shipped."""
        if not self.license_verified:
            return "reference-only"
        if self.license.upper() in SHIPPABLE | FREE_USE:
            return "ship"
        if self.license.upper() in ATTRIBUTION:
            return "ship-with-credit"
        return "reference-only"
