import numpy as np
import pytest
from PIL import Image

from spriteforge.pixel import AssetSpec, Style, validate, validate_animation
from spriteforge.pixel.validate import seam_scores


@pytest.fixture
def pixel_context():
    image = Image.new("RGBA", (8, 8))
    image.paste((255, 0, 0, 255), (2, 2, 6, 6))
    return (
        image,
        AssetSpec(size=(8, 8)),
        Style(palette=["#FF0000", "#000000", "#00FF00"], max_colors=2),
    )


def codes(image, spec, style):
    return {failure.code for failure in validate(image, spec, style)}


@pytest.mark.parametrize(
    "code", ["dimensions", "palette", "alpha", "max_colors", "clipped", "fill_ratio"]
)
def test_sprite_check_passing_and_failing(pixel_context, code):
    image, spec, style = pixel_context
    assert code not in codes(image, spec, style)
    assert not validate(image, spec, style)
    if code == "dimensions":
        image = image.resize((16, 16), Image.Resampling.NEAREST)
    elif code == "palette":
        image.putpixel((2, 2), (0, 0, 255, 255))
    elif code == "alpha":
        image.putpixel((2, 2), (255, 0, 0, 127))
    elif code == "max_colors":
        image.putpixel((2, 2), (0, 0, 0, 255))
        image.putpixel((3, 2), (0, 255, 0, 255))
    elif code == "clipped":
        image.putpixel((0, 2), (255, 0, 0, 255))
    elif code == "fill_ratio":
        image = Image.new("RGBA", (8, 8))
    failures = validate(image, spec, style)
    assert code in {failure.code for failure in failures}
    assert all(failure.message and failure.fix_hint for failure in failures)


@pytest.mark.parametrize("code", ["tile_edges", "tile_wrap"])
@pytest.mark.parametrize("size", [(8, 8), (9, 7)])
def test_tile_check_passing_and_failing(code, size):
    spec = AssetSpec(type="tile", size=size, seam_threshold=0.01)
    style = Style(palette=["#FF0000", "#000000"])
    image = Image.new("RGBA", size, "red")
    assert not validate(image, spec, style)
    assert seam_scores(image) == (0, 0)
    image.putpixel((size[0] - 1, 2), (0, 0, 0, 255))
    assert code in codes(image, spec, style)
    assert seam_scores(image) == (1, 1)


def test_tile_seam_threshold_transparency_and_single_pixel():
    style = Style(palette=["#000000", "#010101"])
    spec = AssetSpec(type="tile", size=(4, 4), seam_threshold=0.01)
    image = Image.new("RGBA", (4, 4), "black")
    image.putpixel((0, 0), (1, 1, 1, 255))
    assert not validate(image, spec, style)
    image.putpixel((0, 0), (0, 0, 0, 0))
    assert {"tile_edges", "tile_wrap"} <= codes(image, spec, style)
    transparent = Image.new("RGBA", (4, 4))
    transparent.putpixel((0, 0), (255, 255, 255, 0))
    assert seam_scores(transparent) == (0, 0)
    assert seam_scores(Image.new("RGBA", (1, 1), "black")) == (0, 0)


def test_transparent_rgb_ignored_and_explicit_edge_override(pixel_context):
    image, spec, style = pixel_context
    image.putpixel((0, 0), (123, 222, 33, 0))
    assert not validate(image, spec, style)
    image.putpixel((0, 0), (255, 0, 0, 255))
    assert not validate(image, spec.model_copy(update={"allow_edge_touch": True}), style)


@pytest.mark.parametrize("code", ["animation_size", "animation_palette", "animation_empty"])
def test_animation_check_passing_and_failing(pixel_context, code):
    image, spec, style = pixel_context
    second = image.copy()
    second.paste((0, 255, 0, 255), (2, 2, 6, 6))
    frames = {"idle/0": image, "idle/1": second}
    assert not validate_animation(frames, spec, style)  # subsets of common palette are valid
    if code == "animation_size":
        frames["idle/1"] = second.resize((9, 9), Image.Resampling.NEAREST)
    elif code == "animation_palette":
        second.putpixel((2, 2), (0, 0, 255, 255))
    else:
        frames = {}
    failures = validate_animation(frames, spec, style)
    assert code in {failure.code for failure in failures}
    assert all(failure.message and failure.fix_hint for failure in failures)


def test_animation_runs_individual_frame_checks(pixel_context):
    image, spec, style = pixel_context
    pixels = np.array(image)
    pixels[2, 2, 3] = 128
    failures = validate_animation({"run/0": Image.fromarray(pixels)}, spec, style)
    assert any(f.code == "alpha" and f.message.startswith("run/0:") for f in failures)
