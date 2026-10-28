import numpy as np
import pytest
from PIL import Image

from spriteforge.pixel.palette import (
    extract_palette,
    lab_to_rgb,
    load_palette,
    merge_near_duplicates,
    quantize,
    rgb_to_lab,
    save_palette,
)
from spriteforge.pixel.schemas import Palette


def test_lab_known_values_and_roundtrip():
    rgb = np.array([[0, 0, 0], [255, 255, 255], [255, 0, 0], [12, 84, 193]])
    lab = rgb_to_lab(rgb)
    assert np.allclose(lab[0], [0, 0, 0], atol=0.001)
    assert np.allclose(lab[1], [100, 0, 0], atol=0.001)
    assert np.allclose(lab[2], [53.24, 80.09, 67.20], atol=0.02)
    assert np.max(np.abs(lab_to_rgb(lab).astype(int) - rgb)) <= 1


def test_quantize_uses_lab_distance_not_rgb():
    # Gray 120 is closer to black in RGB, but closer to white in perceptual L*.
    result = quantize(np.array([[[120, 120, 120]]]), ["#000000", "#FFFFFF"])
    assert result.tolist() == [[[255, 255, 255]]]


def test_extract_kmeans_is_deterministic_ignores_transparency():
    image = Image.new("RGBA", (12, 12), "red")
    image.paste((0, 0, 255, 255), (0, 0, 6, 12))
    image.putpixel((0, 0), (0, 255, 0, 0))
    palette = extract_palette(image, count=2)
    assert set(palette.colors) == {"#FF0000", "#0000FF"}
    assert palette == extract_palette(image, count=2)
    assert len(extract_palette(image, count=1).colors) == 1
    with pytest.raises(ValueError, match="transparent"):
        extract_palette(Image.new("RGBA", (8, 8)))
    with pytest.raises(ValueError):
        extract_palette(image, count=0)


def test_merge_near_duplicate_colors():
    palette = Palette(colors=["#FF0000", "#FE0101", "#0000FF"])
    assert merge_near_duplicates(palette).colors == ["#FF0000", "#0000FF"]
    assert len(merge_near_duplicates(palette, threshold=0).colors) == 3


def test_palette_files_and_known_palette(tmp_path):
    palette = load_palette("dungeon")
    assert len(palette.colors) == 16
    path = tmp_path / "palettes" / "saved.json"
    save_palette(palette, path)
    assert load_palette(path) == palette
    assert Palette(colors=["#abcdef", "#ABCDEF"]).colors == ["#ABCDEF"]
