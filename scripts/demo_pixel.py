"""Produce a deliberately bad raw sprite and deterministic corrected PNGs; no model calls."""

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from spriteforge.pixel import AssetSpec, Style, postprocess, validate
from spriteforge.pixel.atlas import pack
from spriteforge.pixel.palette import extract_palette, load_palette, save_palette
from spriteforge.pixel.preview import comparison, contact_sheet, upscale


def bad_sprite() -> Image.Image:
    height = width = 384
    y, x = np.mgrid[:height, :width]
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    # Near-magenta gradient that stays within the configured key tolerance.
    pixels[..., 0] = 248 - x // 30
    pixels[..., 1] = 3 + y // 50
    pixels[..., 2] = 249 - y // 35
    pixels[..., 3] = 255
    raw = Image.fromarray(pixels)
    draw = ImageDraw.Draw(raw)
    draw.rounded_rectangle((100, 105, 284, 290), radius=65, fill="#21BC65")
    draw.rectangle((100, 200, 284, 290), fill="#21BC65")
    draw.ellipse((132, 151, 157, 184), fill="#142332")
    draw.ellipse((224, 151, 249, 184), fill="#142332")
    draw.arc((161, 187, 221, 231), 0, 180, fill="#142332", width=8)
    draw.rectangle((145, 125, 181, 140), fill="#9AEF9D")
    draw.rectangle((118, 307, 120, 309), fill="#61B387")  # stray speck
    return raw.filter(ImageFilter.GaussianBlur(2.4))


def main(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    style = Style(
        palette=[
            c
            for c in load_palette("dungeon").colors
            if c in {"#171725", "#173F35", "#256B43", "#47AD59", "#9CDD74", "#FFFFFF"}
        ],
        outline=True,
        outline_color="#171725",
        chroma_tolerance=55,
        min_cluster_size=3,
    )
    spec = AssetSpec(id="demo_slime", size=(32, 32), min_fill_ratio=0.2)
    before = bad_sprite()
    after = postprocess(before, spec, style)
    failures = validate(after, spec, style)
    if failures:
        raise RuntimeError([failure.model_dump() for failure in failures])
    before.save(output / "before.png")
    after.save(output / "after.png")
    upscale(after).save(output / "after-8x.png")
    comparison(before, after).save(output / "comparison.png")
    contact_sheet({"Slime / idle / 0": after}).save(output / "contact-sheet.png")
    pack({"slime/idle/0": after}, output, animations={"slime/idle": ["slime/idle/0"]})
    save_palette(extract_palette(after), output / "extracted-palette.json")
    (output / "style.json").write_text(style.model_dump_json(indent=2), encoding="utf-8")
    print(f"PASS: {spec.size}, binary alpha, palette compliant. Outputs: {output.resolve()}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("workspace/pixel-demo"))
    main(parser.parse_args().output)
