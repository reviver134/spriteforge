import numpy as np
from PIL import Image, ImageDraw

from spriteforge.pixel import AssetSpec, Style, postprocess
from spriteforge.pixel.postprocess import boundary_already_dark


def test_no_outline_gets_one_and_existing_outline_stays_unchanged():
    style = Style(palette=["#000000", "#00FF00"], outline=True)
    raw = Image.new("RGBA", (8, 8), "lime")
    assert not boundary_already_dark(raw, style)
    result = postprocess(raw, AssetSpec(), style)
    assert (0, 0, 0, 255) in result.getdata()
    ImageDraw.Draw(raw).rectangle((0, 0, 7, 7), outline="black", width=1)
    assert boundary_already_dark(raw, style)
    with_guard = postprocess(raw, AssetSpec(), style)
    without = postprocess(raw, AssetSpec(), style.model_copy(update={"outline": False}))
    assert np.array_equal(np.array(with_guard), np.array(without))


def test_guard_requires_more_than_ninety_percent_and_dark_palette():
    style = Style(palette=["#000000", "#00FF00"])
    image = Image.new("RGBA", (6, 6), "black")  # 20 boundary pixels
    image.putpixel((0, 0), (0, 255, 0, 255))
    image.putpixel((0, 1), (0, 255, 0, 255))
    assert not boundary_already_dark(image, style)  # exactly 90%, not greater
    image.putpixel((0, 1), (0, 0, 0, 255))
    assert boundary_already_dark(image, style)
    assert not boundary_already_dark(image, Style(palette=["#00FF00"]))


def test_character_and_tile_defaults():
    style = Style(palette=["#000000"])
    assert style.character_canvas == AssetSpec(type="character").size == (48, 48)
    assert style.character_logical_limit == (40, 40)
    assert style.tile_canvas == AssetSpec(type="tile").size == (16, 16)
    assert AssetSpec(type="character", size=(64, 64)).size == (64, 64)
