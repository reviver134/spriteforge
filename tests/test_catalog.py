"""OKF catalog mirrors packs, tile sets and actors so agents can find them through memory."""

import zipfile

from PIL import Image, ImageDraw

from spriteforge.library import add_pack
from spriteforge.memory.okf.catalog import sync_catalog
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.memory.okf.validate import validate


def test_catalog_sync_is_searchable_idempotent_and_deprecates(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    archive = tmp_path / "m.zip"
    strip = Image.new("RGBA", (192, 48))
    ImageDraw.Draw(strip).rectangle((10, 10, 30, 47), fill=(40, 160, 40, 255))
    strip.save(tmp_path / "f.png")
    with zipfile.ZipFile(archive, "w") as z:
        z.write(tmp_path / "f.png", "M/Goblin/Run.png")
        z.write(tmp_path / "f.png", "M/Goblin/Idle.png")
    add_pack(archive, "test-monsters", category="enemy", cell="auto", name_depth=2,
             tags=["side-view"], license="CC0", license_verified=True, ai_allowed=False,
             root=lib)  # fmt: skip
    store = KnowledgeStore(tmp_path / "ws" / "memory")
    first = sync_catalog(store)
    assert first["updated"] == first["total"] == 4  # guide, library, pack, actor
    assert sync_catalog(store)["updated"] == 0
    assert validate(store, external=False).errors == []
    assert "catalog/index.md" in (store.root / "index.md").read_text()
    hits = [s.id for s in store.retrieve("goblin enemy side view", token_budget=800)]
    assert hits[0] == "catalog/actors/test-monsters-goblin"
    pack = store.show("catalog/packs/test-monsters")
    assert "forbidden" in pack.body and "no AI" in pack.metadata.description
    actor = store.show("catalog/actors/test-monsters-goblin")
    assert "side-view" in actor.body and "source=actor" in actor.body
    assert "kit" not in store.show("catalog/guide").body
    # Pack removed from the library: its entries are deprecated, not silently left current.
    (lib / "test-monsters" / "pack.json").unlink()
    result = sync_catalog(store)
    assert result["deprecated"] == 2
    assert store.show("catalog/packs/test-monsters").metadata.status == "deprecated"
