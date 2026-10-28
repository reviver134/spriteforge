import json

import numpy as np
import pytest
from PIL import Image

from spriteforge.pixel.atlas import Atlas, pack
from spriteforge.pixel.preview import comparison, contact_sheet, upscale


def test_atlas_roundtrip_padding_and_animation_keys(tmp_path):
    sprites = {
        "hero/idle/0": Image.new("RGBA", (16, 16), "red"),
        "hero/idle/1": Image.new("RGBA", (16, 16), (0, 255, 0, 128)),
        "floor": Image.new("RGBA", (8, 8), "blue"),
    }
    animations = {"hero/idle": ["hero/idle/0", "hero/idle/1"]}
    atlas = pack(sprites, tmp_path, animations=animations)
    saved = Atlas.model_validate_json((tmp_path / "atlas.json").read_text())
    assert saved == atlas
    assert saved.meta.animations == animations
    sheet = Image.open(tmp_path / "atlas.png")
    occupied = np.zeros((sheet.height, sheet.width), dtype=bool)
    for key, frame in saved.frames.items():
        r = frame.frame
        assert not occupied[r.y : r.y + r.h, r.x : r.x + r.w].any()
        occupied[r.y : r.y + r.h, r.x : r.x + r.w] = True
        restored = sheet.crop((r.x, r.y, r.x + r.w, r.y + r.h))
        assert np.array_equal(np.array(restored), np.array(sprites[key]))
        assert sheet.getpixel((r.x - 1, r.y)) == (0, 0, 0, 0)
        assert not frame.rotated and not frame.trimmed
    assert sheet.size == (saved.meta.size.w, saved.meta.size.h)
    png_bytes, json_bytes = (
        (tmp_path / "atlas.png").read_bytes(),
        (tmp_path / "atlas.json").read_bytes(),
    )
    pack(dict(reversed(list(sprites.items()))), tmp_path, animations=animations)
    assert (tmp_path / "atlas.png").read_bytes() == png_bytes
    assert (tmp_path / "atlas.json").read_bytes() == json_bytes
    assert set(json.loads(json_bytes)["frames"]) == set(sprites)


def test_atlas_invalid_and_overflow(tmp_path):
    image = Image.new("RGBA", (8, 8), "red")
    with pytest.raises(ValueError):
        pack({}, tmp_path)
    with pytest.raises(ValueError, match="reserved"):
        pack({"__BASE": image}, tmp_path)
    with pytest.raises(ValueError, match="reference"):
        pack({"idle/0": image}, tmp_path, animations={"walk": ["missing"]})
    with pytest.raises(ValueError, match="exceeds"):
        pack({"sprite": image}, tmp_path, max_size=8)
    with pytest.raises(ValueError, match="fit"):
        pack({str(i): image for i in range(8)}, tmp_path, max_size=16)


def test_nearest_preview_preserves_pixels_and_alpha():
    image = Image.new("RGBA", (2, 2))
    image.putpixel((1, 0), (255, 0, 0, 255))
    large = upscale(image)
    assert large.size == (16, 16)
    assert np.array_equal(np.array(large), np.repeat(np.repeat(np.array(image), 8, 0), 8, 1))
    with pytest.raises(ValueError):
        upscale(image, 0)


def test_contact_sheet_and_comparison():
    image = Image.new("RGBA", (16, 16), "red")
    sheet = contact_sheet({"A": image, "B": image}, columns=2)
    assert sheet.width > 2 * 16 * 8
    assert sheet.height >= 16 * 8 + 24
    compared = comparison(image.resize((128, 128), Image.Resampling.NEAREST), image)
    assert compared.size == sheet.size
    with pytest.raises(ValueError):
        contact_sheet({})
