import json
import zipfile

import pytest
from PIL import Image

from spriteforge.library import add_pack, asset_path, load_packs, search, set_license
from spriteforge.library.store import name_from_file


def make_zip(tmp_path, names, size=(32, 32)):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as z:
        for name in names:
            img = Image.new("RGBA", size, (0, 0, 0, 0))
            img.putpixel((3, 3), (200, 50, 50, 255))
            path = tmp_path / "src.png"
            img.save(path)
            z.write(path, name)
        z.writestr("__MACOSX/._junk.png", b"junk")
    return archive


def test_name_from_file_strips_category_and_numbers():
    assert name_from_file("armour04chainmail", "armour") == ("chainmail", "chainmail")
    assert name_from_file("01_Hero Idle", "") == ("hero-idle", "hero idle")


def test_ingest_catalog_and_license_gate(tmp_path):
    root = tmp_path / "library"
    archive = make_zip(tmp_path, ["a/armour01robe.png", "armour02robe.png", "sheet.png"])
    pack = add_pack(archive, "test-pack", category="equipment", subcategory="armour", root=root)
    assert [a.id for a in pack.assets] == ["test-pack/robe", "test-pack/robe-2", "test-pack/sheet"]
    assert (root / "test-pack" / "preview.png").is_file()
    entry = pack.assets[0]
    assert entry.size == (32, 32) and entry.transparent_background and entry.opaque_bbox
    assert asset_path("test-pack/robe", root).is_file()

    # Unknown license: searchable as reference, never offered for shipping.
    assert pack.usage == "reference-only"
    assert len(search(category="equipment", root=root)) == 3
    assert search(usage="ship", root=root) == []

    set_license("test-pack", "CC0", source_url="https://example.com/pack", root=root)
    assert load_packs(root)[0].usage == "ship"
    assert len(search(usage="ship", tag="armour", root=root)) == 3
    index = json.loads((root / "index.json").read_text())
    assert index["packs"]["test-pack"]["usage"] == "ship"


def test_sheet_cells_and_frames(tmp_path):
    root = tmp_path / "library"
    archive = make_zip(tmp_path, ["walk.png"], size=(64, 16))
    pack = add_pack(archive, "sheets", category="character", cell=(16, 16), root=root)
    assert pack.assets[0].cell == (16, 16) and pack.assets[0].frames == 4
    assert search(size=(16, 16), root=root)[0]["id"] == "sheets/walk"


def test_cc_by_needs_credit_and_bad_input_fails(tmp_path):
    root = tmp_path / "library"
    add_pack(make_zip(tmp_path, ["x.png"]), "by", license="CC-BY-4.0", license_verified=True,
             root=root)  # fmt: skip
    assert load_packs(root)[0].usage == "ship-with-credit"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="No images"):
        add_pack(empty, "empty", root=root)


def test_mixed_pack_categories_meta_and_private_packs(tmp_path):
    root = tmp_path / "library"
    archive = make_zip(tmp_path, ["Texture/TX Player.png", "Texture/TX Tileset Grass.png",
                                  "Texture/Extra/TX Props.png", "Other/skip.png"])  # fmt: skip
    with zipfile.ZipFile(archive, "a") as z:
        z.writestr("LICENSE.txt", "CC0")
    pack = add_pack(
        archive, "mixed", category="prop", include="texture/", strip_prefix="TX ",
        categorize={"Player": "character", "Tileset": "tileset"}, redistributable=False,
        license="FREE-USE", license_verified=True, root=root,
    )  # fmt: skip
    cats = {a.id: a.category for a in pack.assets}
    assert cats == {"mixed/player": "character", "mixed/grass": "tileset",
                    "mixed/props-extra": "prop"}  # fmt: skip
    assert (root / "mixed" / "meta" / "LICENSE.txt").read_text() == "CC0"
    assert pack.usage == "ship"
    assert "/mixed/" in (root / ".gitignore").read_text()
