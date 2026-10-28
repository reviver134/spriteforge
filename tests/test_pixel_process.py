import numpy as np
import pytest
from PIL import Image, ImageDraw
from pydantic import ValidationError

from spriteforge.pixel import AssetSpec, Style, postprocess, validate
from spriteforge.pixel.postprocess import remove_clusters


def test_pipeline_clean_deterministic_and_does_not_mutate():
    raw = Image.new("RGBA", (128, 128), (248, 8, 249, 255))
    draw = ImageDraw.Draw(raw)
    draw.rectangle((30, 40, 97, 87), fill=(32, 198, 41, 255))
    original = raw.tobytes()
    style = Style(palette=["#000000", "#00FF00"], outline=True, min_cluster_size=1)
    spec = AssetSpec(size=(32, 32))
    output = postprocess(raw, spec, style)
    assert not validate(output, spec, style)
    assert output.tobytes() == postprocess(raw, spec, style).tobytes()
    assert raw.tobytes() == original
    assert output.getpixel((0, 0)) == (0, 0, 0, 0)
    assert output.getpixel((16, 16)) == (0, 255, 0, 255)
    assert set(np.unique(np.array(output)[..., 3])) == {0, 255}


def test_trim_fit_preserves_aspect_and_padding():
    raw = Image.new("RGB", (120, 120), "magenta")
    ImageDraw.Draw(raw).rectangle((10, 20, 109, 69), fill="red")
    style = Style(palette=["#FF0000"], min_cluster_size=0)
    image = postprocess(raw, AssetSpec(size=(32, 32)), style)
    left, top, right, bottom = image.getbbox()
    assert abs((right - left) / (bottom - top) - 2) < 0.15
    assert left >= 1 and top >= 1 and right < 32 and bottom < 32


def test_only_nearest_and_box_filters(monkeypatch):
    original = Image.Image.resize
    filters = []

    def resize(self, size, resample=None, *args, **kwargs):
        filters.append(resample)
        return original(self, size, resample, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "resize", resize)
    postprocess(Image.new("RGB", (128, 128), "red"), AssetSpec(), Style(palette=["#FF0000"]))
    assert Image.Resampling.BOX in filters
    assert set(filters) <= {Image.Resampling.BOX, Image.Resampling.NEAREST}


def test_alpha_threshold_and_entirely_keyed_input():
    style = Style(palette=["#FF0000"], min_cluster_size=0)
    spec = AssetSpec(size=(8, 8))
    low = postprocess(Image.new("RGBA", (8, 8), (255, 0, 0, 127)), spec, style)
    high = postprocess(Image.new("RGBA", (8, 8), (255, 0, 0, 128)), spec, style)
    assert low.getbbox() is None
    assert high.getbbox() is not None
    keyed = postprocess(Image.new("RGB", (16, 16), "magenta"), spec, style)
    assert keyed.getbbox() is None
    assert "fill_ratio" in {f.code for f in validate(keyed, spec, style)}


def test_cluster_cleanup_four_connectivity_and_minimum_boundary():
    alpha = np.zeros((8, 8), dtype=np.uint8)
    alpha[1:3, 1:3] = 255
    alpha[4, 4:6] = 255
    alpha[6, 6] = 255
    alpha[7, 7] = 255  # diagonal, not connected
    cleaned = remove_clusters(alpha, 2)
    assert np.count_nonzero(cleaned) == 6
    assert np.count_nonzero(alpha) == 8
    assert np.array_equal(remove_clusters(alpha, 1), alpha)
    assert not remove_clusters(alpha, 5).any()


def test_outline_exactly_one_pixel_and_on_palette():
    raw = Image.new("RGBA", (4, 4), "red")
    spec = AssetSpec(size=(12, 12))
    without = postprocess(raw, spec, Style(palette=["#FF0000", "#000000"], min_cluster_size=0))
    outlined = postprocess(raw, spec, Style(palette=["#FF0000", "#000000"], outline=True))
    assert outlined.getbbox() == without.getbbox()
    pixels = np.array(outlined)
    red = np.all(pixels[..., :3] == [255, 0, 0], axis=-1) & (pixels[..., 3] > 0)
    black = np.all(pixels[..., :3] == [0, 0, 0], axis=-1) & (pixels[..., 3] > 0)
    assert black.any()
    for y, x in np.argwhere(black):
        assert any(
            0 <= yy < 12 and 0 <= xx < 12 and red[yy, xx]
            for yy, xx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1))
        )
    assert not validate(outlined, spec, Style(palette=["#FF0000", "#000000"]))


def test_tile_uses_full_canvas_without_padding():
    spec = AssetSpec(type="tile", size=(16, 16), min_fill_ratio=1)
    style = Style(palette=["#FF0000"])
    output = postprocess(Image.new("RGB", (96, 96), "red"), spec, style)
    assert output.getbbox() == (0, 0, 16, 16)
    assert not validate(output, spec, style)


def test_pixel_schema_rejects_invalid_contracts():
    for args in (
        {"palette": []},
        {"palette": ["red"]},
        {"palette": ["#+00000"]},
        {"palette": ["#FF0000"], "outline_color": "#000000"},
    ):
        with pytest.raises(ValidationError):
            Style(**args)
    with pytest.raises(ValidationError):
        AssetSpec(size=(4, 4), padding=2)
    with pytest.raises(ValidationError):
        AssetSpec(size=(0, 32))
    with pytest.raises(ValueError, match="outline"):
        postprocess(
            Image.new("RGB", (8, 8), "red"),
            AssetSpec(size=(3, 3)),
            Style(palette=["#FF0000"], outline=True),
        )


def test_custom_chroma_key_tolerance_boundary():
    raw = Image.new("RGBA", (8, 8), (0, 0, 255, 255))
    raw.putpixel((2, 2), (0, 0, 250, 255))
    raw.putpixel((3, 3), (0, 0, 249, 255))
    style = Style(palette=["#0000FF"], background="#0000FF", chroma_tolerance=5, min_cluster_size=0)
    result = postprocess(raw, AssetSpec(size=(8, 8)), style)
    # Distance 5 is removed; distance 6 survives (quantized back to blue).
    assert result.getbbox() is not None
    assert result.getpixel((4, 4)) == (0, 0, 255, 255)
    assert (
        postprocess(
            raw, AssetSpec(size=(8, 8)), style.model_copy(update={"chroma_tolerance": 6})
        ).getbbox()
        is None
    )


def test_pipeline_removes_stray_clusters_after_resizing():
    raw = Image.new("RGBA", (16, 16))
    raw.paste((255, 0, 0, 255), (4, 4, 11, 11))
    raw.putpixel((0, 0), (255, 0, 0, 255))
    raw.putpixel((15, 15), (255, 0, 0, 255))
    spec = AssetSpec(type="tile", size=(16, 16))
    style = Style(palette=["#FF0000"], min_cluster_size=2)
    result = postprocess(raw, spec, style)
    assert result.getbbox() == (4, 4, 11, 11)
    assert postprocess(raw, spec, style.model_copy(update={"min_cluster_size": 0})).getbbox() == (
        0,
        0,
        16,
        16,
    )
