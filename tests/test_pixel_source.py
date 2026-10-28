import json
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image, ImageDraw

from spriteforge.pixel import (
    AssetSpec,
    ProcessingOptions,
    ProcessingReport,
    Style,
    postprocess,
    validate,
)
from spriteforge.pixel.source import (
    components,
    detect_grid,
    estimate_background,
    isolate_subject,
    key_background,
    mode_resize,
    snap_grid,
)


def test_border_median_ignores_corner_watermark_and_uses_lab():
    image = Image.new("RGBA", (100, 80), (190, 40, 150, 255))
    ImageDraw.Draw(image).rectangle((30, 20, 70, 60), fill="green")
    image.putpixel((99, 79), (240, 200, 235, 255))
    assert estimate_background(image).tolist() == [190, 40, 150]
    report = ProcessingReport()
    keyed = key_background(image, ProcessingOptions(), report)
    assert report.background == "#BE2896"
    assert keyed.getpixel((0, 0))[3] == 0
    assert keyed.getpixel((40, 40))[3] == 255
    assert estimate_background(Image.new("RGBA", (8, 8))) is None


def test_despill_removes_only_background_like_edge_contamination():
    image = Image.new("RGBA", (40, 40), (200, 25, 180, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, 31, 31), fill=(130, 40, 120, 255))  # contaminated rim
    draw.rectangle((10, 10, 29, 29), fill=(30, 180, 55, 255))
    image.putpixel((20, 20), (130, 40, 120, 255))  # legitimate internal purple accent
    report = ProcessingReport()
    result = key_background(
        image, ProcessingOptions(background_tolerance=5, despill_tolerance=50), report
    )
    assert report.despilled_pixels > 0
    assert result.getpixel((8, 8))[3] == 0
    assert result.getpixel((15, 15))[3] == 255
    assert result.getpixel((20, 20)) == image.getpixel((20, 20))


def test_key_tolerance_uses_lab_not_rgb():
    image = Image.new("RGBA", (40, 40), (150, 150, 150, 255))
    image.putpixel((15, 15), (175, 175, 175, 255))  # RGB distance 43; LAB distance about 9
    options = ProcessingOptions(background_tolerance=10, despill_tolerance=0)
    assert key_background(image, options, ProcessingReport()).getpixel((15, 15))[3] == 0
    assert (
        key_background(
            image, options.model_copy(update={"background_tolerance": 8}), ProcessingReport()
        ).getpixel((15, 15))[3]
        == 255
    )


def test_components_scanlines_merge_branches_and_keep_four_connectivity():
    mask = np.zeros((8, 8), dtype=bool)
    mask[1, 1:3] = True
    mask[1, 5:7] = True
    mask[2, 1:7] = True  # merges both previous runs
    mask[3, 7] = True  # diagonal: separate
    labels, groups = components(mask)
    assert [area for _, area, _ in groups] == [10, 1]
    assert labels[1, 1] == labels[1, 6]
    assert labels[3, 7] != labels[2, 6]
    assert components(np.zeros((4, 4), dtype=bool))[1] == []


def test_isolation_default_and_area_ratio_keep_detached():
    image = Image.new("RGBA", (40, 40))
    image.paste((0, 200, 0, 255), (5, 5, 15, 15))
    image.paste((0, 200, 0, 255), (20, 5, 25, 10))
    image.putpixel((35, 35), (255, 255, 255, 255))
    report = ProcessingReport()
    result = isolate_subject(image, ProcessingOptions(), report)
    assert result.getbbox() == (5, 5, 15, 15)
    assert [d.area for d in report.dropped] == [25, 1]
    assert report.dropped[0].area_ratio == 0.25
    report = ProcessingReport()
    result = isolate_subject(
        image, ProcessingOptions(keep_detached=True, detached_min_area_ratio=0.25), report
    )
    assert len(components(np.array(result)[..., 3] > 0)[1]) == 2
    assert [d.area for d in report.dropped] == [1]
    assert image.getpixel((35, 35))[3] == 255  # input remains untouched


@pytest.mark.parametrize("block", [2, 4, 8, 23])
def test_source_grid_detection_and_mode_snap(block):
    y, x = np.mgrid[:16, :16]
    logical = np.zeros((16, 16, 4), dtype=np.uint8)
    logical[..., :3] = np.where(((x + y) % 2)[..., None], [30, 180, 50], [15, 20, 30])
    logical[..., 3] = 255
    image = Image.fromarray(logical).resize((16 * block, 16 * block), Image.Resampling.NEAREST)
    detected, offset = detect_grid(image)
    assert detected == block
    assert offset == (0, 0)
    assert np.array_equal(np.array(snap_grid(image, detected, offset)), logical)


def test_grid_falls_back_for_solid_smooth_and_non_grid_sources():
    assert detect_grid(Image.new("RGBA", (64, 64), "green"))[0] is None
    noise = np.random.default_rng(1).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    assert detect_grid(Image.fromarray(noise))[0] is None
    gradient = np.zeros((64, 64, 3), dtype=np.uint8)
    gradient[..., 1] = np.arange(64)
    assert detect_grid(Image.fromarray(gradient))[0] is None


def test_mode_downscale_selects_source_color_not_average():
    image = Image.new("RGBA", (4, 4), "green")
    image.putpixel((0, 0), (255, 0, 0, 255))
    assert mode_resize(image, (1, 1)).getpixel((0, 0)) == (0, 128, 0, 255)
    transparent = Image.new("RGBA", (4, 4))
    transparent.putpixel((0, 0), (255, 0, 0, 255))
    assert mode_resize(transparent, (1, 1)).getpixel((0, 0)) == (0, 0, 0, 0)
    assert set(mode_resize(image, (8, 8)).getdata()) == set(image.getdata())
    partial = Image.new("RGBA", (4, 4), (0, 128, 0, 127))
    assert mode_resize(partial, (1, 1)).getpixel((0, 0))[3] == 127  # alpha snap happens later


def test_hardened_pipeline_clean_outline_and_preserves_eye_features():
    raw = Image.new("RGBA", (96, 80), (195, 30, 170, 255))
    draw = ImageDraw.Draw(raw)
    draw.rectangle((25, 20, 70, 65), fill=(50, 190, 60, 255))
    draw.rectangle((34, 31, 41, 40), fill=(8, 12, 20, 255))
    draw.rectangle((54, 31, 61, 40), fill=(8, 12, 20, 255))
    draw.rectangle((80, 70, 83, 73), fill="white")  # watermark
    draw.rectangle((14, 65, 17, 68), fill="green")  # droplet
    style = Style(palette=["#32BE3C", "#080C14"], outline=True)
    report = ProcessingReport()
    output = postprocess(raw, AssetSpec(), style, options=ProcessingOptions(), report=report)
    assert not validate(output, AssetSpec(), style)
    assert len(report.dropped) == 2
    pixels = np.array(output)
    assert len(components(pixels[..., 3] > 0)[1]) == 1
    assert np.all(pixels[pixels[..., 3] == 0] == 0)
    dark = np.all(pixels[..., :3] == [8, 12, 20], axis=-1) & (pixels[..., 3] > 0)
    assert len(components(dark)[1]) == 3  # continuous dark outline plus two eyes


def test_process_image_script_outputs_and_logs(tmp_path):
    raw = Image.new("RGB", (96, 80), (195, 30, 170))
    draw = ImageDraw.Draw(raw)
    draw.rectangle((25, 20, 70, 65), fill=(50, 190, 60))
    draw.rectangle((80, 70, 83, 73), fill="white")
    path = tmp_path / "input.png"
    raw.save(path)
    output = tmp_path / "out"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/process_image.py",
            str(path),
            "--size",
            "32",
            "--output",
            str(output),
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    assert "Dropped component" in result.stdout
    assert "Detected block size" in result.stdout
    assert "Failures: []" in result.stdout
    assert {"before.png", "after.png", "after_8x.png", "comparison.png"} <= {
        p.name for p in output.iterdir()
    }
    assert Image.open(output / "after.png").size == (32, 32)
    assert Image.open(output / "after_8x.png").size == (256, 256)
    assert json.loads((output / "processing.json").read_text())["dropped"]
