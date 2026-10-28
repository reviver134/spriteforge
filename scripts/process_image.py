"""Clean an existing model image without making any model calls."""

import argparse
from pathlib import Path

from PIL import Image

from spriteforge.pixel import AssetSpec, Style, postprocess, validate
from spriteforge.pixel.palette import extract_palette, load_palette
from spriteforge.pixel.preview import comparison, upscale
from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import isolate_subject, key_background


def process(
    input_path: Path,
    size: int,
    palette: str,
    keep_detached: bool,
    output: Path,
    min_area_ratio: float = 0.02,
) -> int:
    raw = Image.open(input_path).convert("RGBA")
    options = ProcessingOptions(keep_detached=keep_detached, detached_min_area_ratio=min_area_ratio)
    # Auto palette must see the isolated, despilled subject, never the magenta field
    # or detached watermark. Palette extraction otherwise wastes most of its colors.
    if palette == "auto":
        clean = isolate_subject(
            key_background(raw, options, ProcessingReport()), options, ProcessingReport()
        )
        colors = extract_palette(clean, count=16, merge_threshold=0).colors
    else:
        colors = load_palette(palette).colors
    style = Style(palette=colors, max_colors=len(colors), outline=True)
    spec = AssetSpec(id=input_path.stem, size=(size, size))
    report = ProcessingReport()
    after = postprocess(raw, spec, style, options=options, report=report)
    failures = validate(after, spec, style)
    output.mkdir(parents=True, exist_ok=True)
    raw.save(output / "before.png")
    after.save(output / "after.png")
    upscale(after).save(output / "after_8x.png")
    comparison(raw, after).save(output / "comparison.png")
    (output / "processing.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    (output / "style.json").write_text(style.model_dump_json(indent=2), encoding="utf-8")
    print(f"Source: {raw.width}x{raw.height}; background: {report.background or 'transparent'}")
    print(f"Detected block size: {report.grid_size or 'none'}; grid offset: {report.grid_offset}")
    print(f"Despilled pixels: {report.despilled_pixels}")
    for dropped in report.dropped:
        print(
            f"Dropped component: area={dropped.area}, bbox={dropped.bbox}, ratio={dropped.area_ratio:.6f}"
        )
    if failures:
        for failure in failures:
            print(f"FAIL {failure.code}: {failure.message} Fix: {failure.fix_hint}")
    else:
        print("Failures: []")
    print(f"Comparison: {(output / 'comparison.png').resolve()}")
    return int(bool(failures))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--size", type=int, default=32)
    parser.add_argument(
        "--palette", default="auto", help='Palette JSON path or "auto" (16 LAB colors)'
    )
    parser.add_argument("--keep-detached", action="store_true")
    parser.add_argument("--min-area-ratio", type=float, default=0.02)
    parser.add_argument("--output", type=Path, default=Path("workspace/pixel-demo/real/out"))
    args = parser.parse_args()
    raise SystemExit(
        process(
            args.input,
            args.size,
            args.palette,
            args.keep_detached,
            args.output,
            args.min_area_ratio,
        )
    )
