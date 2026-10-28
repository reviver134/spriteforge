"""Free local pixel tools. Read art inputs; write only new tool-output drafts."""

import sys
from pathlib import Path
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from PIL import Image
from pydantic import Field, model_validator

from spriteforge.pixel.atlas import pack
from spriteforge.pixel.palette import extract_palette
from spriteforge.pixel.postprocess import postprocess
from spriteforge.pixel.preview import comparison, contact_sheet, upscale
from spriteforge.pixel.schemas import (
    AssetSpec,
    ProcessingOptions,
    ProcessingReport,
    Schema,
    Style,
    hex_color,
)
from spriteforge.pixel.validate import validate


class Grid(Schema):
    rows: list[str] = Field(min_length=1, max_length=256)
    legend: dict[str, str] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_grid(self):
        width = len(self.rows[0])
        if not 1 <= width <= 256 or any(len(row) != width for row in self.rows):
            raise ValueError("Grid rows must have equal widths from 1 to 256")
        if any(len(key) != 1 or key == "." for key in self.legend):
            raise ValueError("Legend keys are single characters; dot means transparent")
        self.legend = {key: hex_color(color) for key, color in self.legend.items()}
        if set("".join(self.rows)) - self.legend.keys() - {"."}:
            raise ValueError("Every non-dot character needs a legend color")
        return self


class Frames(Schema):
    frames: dict[str, str] = Field(min_length=1, max_length=128)
    animations: dict[str, list[str]] = Field(default_factory=dict)


class Output(Schema):
    files: dict[str, str]
    report: dict = Field(default_factory=dict)


def create_server(workspace: Path):
    root = workspace.resolve()
    server = FastMCP("SpriteForge free pixel tools", log_level="ERROR")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)
    write = ToolAnnotations(readOnlyHint=False, destructiveHint=False, openWorldHint=False)

    def image(path: str, max_side: int = 4096):
        target = (root / path).resolve()
        relative = target.relative_to(root)
        if (
            not relative.parts
            or relative.parts[0] not in {"runs", "tool-output"}
            or any(p.startswith(".") for p in relative.parts)
        ):
            raise ValueError("Inputs must be inside workspace/runs or workspace/tool-output")
        if target.stat().st_size > 20 * 1024 * 1024:
            raise ValueError("Image exceeds 20 MB")
        with Image.open(target) as source:
            if source.format != "PNG" or max(source.size) > max_side:
                raise ValueError(f"Use PNG inputs no larger than {max_side} pixels per side")
            return source.convert("RGBA")

    def destination():
        parent = root / "tool-output"
        if parent.is_symlink() or parent.resolve() != root / "tool-output":
            raise ValueError("Output directory cannot redirect outside workspace")
        folder = parent / uuid4().hex
        folder.mkdir(parents=True, exist_ok=False)
        return folder

    def output(folder, report=None):
        return Output(
            files={p.name: p.relative_to(root).as_posix() for p in folder.iterdir()},
            report=report or {},
        )

    @server.tool(annotations=read)
    def inspect_sprite(path: str) -> dict:
        """Inspect a PNG's size and LAB palette. No model call or file writes."""
        source = image(path)
        colors = extract_palette(source).colors if source.getbbox() else []
        return {"size": list(source.size), "palette": colors, "bbox": source.getbbox()}

    @server.tool(annotations=write)
    def draw_grid(grid: Grid) -> Output:
        """Draw exact pixels from character rows and #RRGGBB legend; dot is transparent. Writes a NEW draft PNG and nearest-neighbor preview only."""
        source = Image.new("RGBA", (len(grid.rows[0]), len(grid.rows)))
        for y, row in enumerate(grid.rows):
            for x, char in enumerate(row):
                if char != ".":
                    color = grid.legend[char]
                    source.putpixel(
                        (x, y), tuple(int(color[i : i + 2], 16) for i in (1, 3, 5)) + (255,)
                    )
        folder = destination()
        source.save(folder / "sprite.png")
        upscale(source).save(folder / "preview.png")
        return output(folder)

    @server.tool(annotations=write)
    def process_sprite(
        path: str, spec: AssetSpec, style: Style, options: ProcessingOptions | None = None
    ) -> Output:
        """Process a sprite using explicit approved style/spec, validate it, and save NEW draft and comparison PNGs. Accepted inputs are never overwritten."""
        if max(spec.size) > 256:
            raise ValueError("Draft sprite canvases must be <=256x256")
        source = image(path)
        report = ProcessingReport()
        result = postprocess(
            source, spec, style, options=options or ProcessingOptions(), report=report
        )
        failures = [f.model_dump() for f in validate(result, spec, style)]
        folder = destination()
        result.save(folder / "sprite.png")
        comparison(source, result).save(folder / "comparison.png")
        (folder / "processing.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        return output(folder, {"processing": report.model_dump(), "failures": failures})

    @server.tool(annotations=write)
    def export_atlas(request: Frames) -> Output:
        """Pack PNG frame paths into a NEW Phaser 3 atlas and contact sheet; animation names map to frame keys. Maximum 128 frames, 256x256 each."""
        sprites = {key: image(path, max_side=256) for key, path in request.frames.items()}
        folder = destination()
        atlas = pack(sprites, folder, animations=request.animations)
        contact_sheet(sprites, factor=2).save(folder / "contact-sheet.png")
        return output(folder, {"frames": len(atlas.frames), "animations": atlas.meta.animations})

    return server


if __name__ == "__main__":
    create_server(Path(sys.argv[1])).run(transport="stdio")
