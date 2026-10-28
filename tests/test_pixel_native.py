import numpy as np
import pytest
from PIL import Image, ImageFilter

from spriteforge.pixel import AssetSpec, ProcessingOptions, ProcessingReport, Style, postprocess
from spriteforge.pixel.palette import (
    background_tinted_dark_colors,
    merge_near_duplicates,
    palette_rgb,
    palette_usage,
    rgb_to_lab,
)
from spriteforge.pixel.schemas import Palette


def logical_sprite(width=34, height=27):
    y, x = np.mgrid[:height, :width]
    pixels = np.zeros((height, width, 4), dtype=np.uint8)
    pixels[..., :3] = np.where(((x + y) % 2)[..., None], [30, 180, 50], [90, 225, 110])
    pixels[..., 3] = 255
    pixels[[0, -1], :, :3] = [8, 12, 20]
    pixels[:, [0, -1], :3] = [8, 12, 20]
    return Image.fromarray(pixels)


def enlarge(logical, factor=8, blur=0):
    big = logical.resize(
        (logical.width * factor, logical.height * factor), Image.Resampling.NEAREST
    )
    raw = Image.new("RGBA", (big.width + 48, big.height + 48), (195, 30, 170, 255))
    raw.paste(big, (24, 24))
    return raw.filter(ImageFilter.GaussianBlur(blur)) if blur else raw


STYLE = Style(palette=["#080C14", "#1EB432", "#5AE16E"], min_cluster_size=0)


def test_blurred_34_by_27_native_is_pixel_identical_on_48_canvas():
    logical = logical_sprite()
    report = ProcessingReport()
    output = postprocess(
        enlarge(logical, blur=0.35),
        AssetSpec(size=(48, 48)),
        STYLE,
        options=ProcessingOptions(),
        report=report,
    )
    expected = Image.new("RGBA", (48, 48))
    expected.paste(logical, (7, 10))
    assert report.grid_size == 8
    assert report.logical_subject_size == (34, 27)
    assert report.scale_mode == "native" and report.scale_factor == 1
    assert np.array_equal(np.array(output), np.array(expected))


def test_bottom_baseline_and_explicit_fit():
    raw = enlarge(logical_sprite())
    report = ProcessingReport()
    output = postprocess(
        raw,
        AssetSpec(size=(48, 48)),
        STYLE,
        options=ProcessingOptions(baseline="bottom"),
        report=report,
    )
    assert output.getbbox() == (7, 20, 41, 47)
    fit_report = ProcessingReport()
    fitted = postprocess(
        raw,
        AssetSpec(size=(48, 48)),
        STYLE,
        options=ProcessingOptions(scale_mode="fit"),
        report=fit_report,
    )
    assert fit_report.scale_mode == "fit"
    assert fitted.getbbox()[2] - fitted.getbbox()[0] == 46


@pytest.mark.parametrize(
    "logical_size,target,mode",
    [
        ((32, 24), 24, "native-integer-downscale"),
        ((34, 27), 32, "native-mode-downscale"),
    ],
)
def test_native_overflow_warns_and_downscales(logical_size, target, mode):
    report = ProcessingReport()
    output = postprocess(
        enlarge(logical_sprite(*logical_size)),
        AssetSpec(size=(target, target)),
        STYLE,
        options=ProcessingOptions(),
        report=report,
    )
    assert report.logical_subject_size == logical_size
    assert report.scale_mode == mode
    assert any(
        f"{logical_size[0]}x{logical_size[1]}" in w and "--size" in w for w in report.warnings
    )
    assert output.getbbox()[2] < target and output.getbbox()[3] < target
    if mode == "native-integer-downscale":
        assert report.scale_factor == 0.5
        assert output.getbbox()[2] - output.getbbox()[0] == logical_size[0] // 2


def test_native_without_grid_falls_back_to_fit():
    report = ProcessingReport()
    raw = Image.new("RGBA", (40, 40))
    raw.paste((30, 180, 50, 255), (10, 10, 30, 30))
    postprocess(
        raw, AssetSpec(), STYLE, options=ProcessingOptions(scale_mode="native"), report=report
    )
    assert report.logical_subject_size is None
    assert report.scale_mode == "fit"
    assert any("No confident source grid" in w for w in report.warnings)


def test_low_confidence_grid_uses_unsnapped_fit(monkeypatch):
    def weak_detection(image, report):
        report.grid_confidence = 0.2
        return 8, (0, 0)

    monkeypatch.setattr("spriteforge.pixel.source.detect_grid", weak_detection)
    raw = enlarge(logical_sprite())
    report = ProcessingReport()
    postprocess(
        raw,
        AssetSpec(size=(48, 48)),
        STYLE,
        options=ProcessingOptions(),
        report=report,
    )
    assert report.logical_subject_size is None
    assert report.scale_mode == "fit"
    assert any("too low" in w for w in report.warnings)


def test_palette_merge_default_and_configurable_threshold():
    palette = Palette(colors=["#303030", "#343434", "#00FF00"])
    delta = np.linalg.norm(np.diff(rgb_to_lab(palette_rgb(palette.colors[:2])), axis=0))
    assert 0 < delta < 5
    assert merge_near_duplicates(palette).colors == ["#303030", "#00FF00"]
    assert merge_near_duplicates(palette, threshold=delta / 2) == palette


def test_palette_usage_counts_excludes_transparent_and_warns_unused():
    image = Image.new("RGBA", (4, 4), (255, 0, 0, 255))
    image.putpixel((0, 0), (0, 0, 255, 0))
    palette = Palette(colors=["#FF0000", "#0000FF"])
    assert palette_usage(image, palette) == {"#FF0000": 15, "#0000FF": 0}
    report = ProcessingReport()
    raw = Image.new("RGBA", (12, 12))
    raw.paste((255, 0, 0, 255), (2, 2, 10, 10))
    output = postprocess(
        raw, AssetSpec(), Style(palette=palette.colors), options=ProcessingOptions(), report=report
    )
    assert report.palette_usage["#0000FF"] == 0
    assert sum(report.palette_usage.values()) == np.count_nonzero(np.array(output)[..., 3])
    assert any("Unused palette color: #0000FF" == w for w in report.warnings)


def test_background_tinted_dark_color_warning_distinguishes_neutrals_and_green():
    palette = Palette(colors=["#410249", "#171717", "#103519", "#FFFFFF"])
    assert background_tinted_dark_colors(palette, "#CD21B3") == ["#410249"]
    assert background_tinted_dark_colors(palette, None) == []
    assert background_tinted_dark_colors(palette, "#888888") == []
    report = ProcessingReport()
    raw = Image.new("RGBA", (32, 32), (205, 33, 179, 255))
    raw.paste((16, 53, 25, 255), (8, 8, 24, 24))
    postprocess(
        raw, AssetSpec(), Style(palette=palette.colors), options=ProcessingOptions(), report=report
    )
    assert report.tinted_dark_colors == ["#410249"]
    assert any("tinted toward background" in w for w in report.warnings)
