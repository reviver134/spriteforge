"""Asset library MCP service, and the characters/ recipes + tile sets it serves to agents."""

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import anyio
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client
from PIL import Image, ImageDraw

from spriteforge.game.actors import load_actors
from spriteforge.game.tilesets import backdrop_layers, load_tilesets, pick_variant, tile_image
from spriteforge.library import add_pack, search

ROOT = Path(__file__).parents[1]


def strip(path: Path, frames: int, size: int, color) -> None:
    img = Image.new("RGBA", (frames * size, size))
    for n in range(frames):
        ImageDraw.Draw(img).rectangle((n * size + 4, 4, n * size + size - 5, size - 1), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def fixture_library(tmp_path: Path) -> Path:
    """A side-view pack ingested from a folder (not a zip), so categorize sees POSIX paths."""
    src = tmp_path / "My Pack"
    strip(src / "Mobs" / "Goblin" / "Run.png", 4, 32, (40, 160, 40, 255))
    strip(src / "Mobs" / "Goblin" / "Idle.png", 2, 32, (40, 160, 40, 255))
    strip(src / "Hero" / "Knight" / "Run.png", 4, 32, (160, 160, 200, 255))
    strip(src / "Hero" / "Knight" / "Idle.png", 2, 32, (160, 160, 200, 255))
    strip(src / "Items" / "Key.png", 1, 16, (230, 200, 40, 255))
    lib = tmp_path / "library"
    add_pack(src, "test-side", category="prop", categorize={"Mobs/": "enemy", "Hero/": "character"},
             cell="auto", name_depth=2, tags=["side-view"], license="CC0", license_verified=True,
             ai_allowed=False, root=lib)  # fmt: skip
    return lib


def test_folder_ingest_categorizes_with_posix_paths(tmp_path, monkeypatch):
    lib = fixture_library(tmp_path)
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    categories = {a["name"]: a["category"] for a in search()}
    assert categories == {"goblin idle": "enemy", "goblin run": "enemy",
                          "knight idle": "character", "knight run": "character",
                          "items key": "prop"}  # fmt: skip


def test_library_mcp_stdio_session(tmp_path):
    lib = fixture_library(tmp_path)

    async def exercise():
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "spriteforge", "library", "mcp"],
            env={**get_default_environment(), "SPRITEFORGE_LIBRARY": str(lib)},
        )
        async with (
            stdio_client(server) as (read, write),
            ClientSession(read, write, read_timeout_seconds=timedelta(seconds=30)) as session,
        ):
            await session.initialize()
            tools = await session.list_tools()
            assert {t.name for t in tools.tools} == {
                "library_packs", "library_search", "library_actors", "library_tiles",
            }  # fmt: skip
            assert all(t.annotations.readOnlyHint for t in tools.tools)

            async def call(name, args=None):
                result = await session.call_tool(name, args or {})
                assert not result.isError, result
                # Lists arrive as structured {"result": [...]}; a plain dict only as JSON text.
                if result.structuredContent is None:
                    return json.loads(result.content[0].text)
                return result.structuredContent["result"]

            [pack] = await call("library_packs")
            assert pack["id"] == "test-side" and pack["camera"] == ["side-view"]
            assert pack["usage"] == "ship" and pack["ai_allowed"] is False
            assert pack["contents"] == {"character": 2, "enemy": 2, "prop": 1}

            # Synonyms from the brief vocabulary: "goblins" finds the goblin sheets.
            hits = await call("library_search", {"query": "goblins", "camera": "side-view"})
            assert {h["id"] for h in hits} == {"test-side/goblin-run", "test-side/goblin-idle"}
            assert all("sha256" not in h and h["ai_allowed"] is False for h in hits)
            assert await call("library_search", {"query": "goblin", "camera": "top-down"}) == []

            actors = await call("library_actors", {"camera": "side"})
            by_key = {a["key"]: a for a in actors}
            assert by_key["test-side/goblin"]["animations"] == {"idle": 2, "run": 4}
            assert by_key["test-side/goblin"]["camera"] == "side-view"
            assert by_key["test-side/knight"]["role"] == "hero"

            tiles = await call("library_tiles", {"camera": "side-view"})
            assert tiles["camera"] == "side-view" and tiles["tilesets"] == []
            assert "test-side/items-key" in {o["id"] for o in tiles["whole_objects"]}
            assert tiles["muse_reference_packs"] == []  # the only pack forbids AI use

            bad = await session.call_tool("library_tiles", {"camera": "isometric"})
            assert bad.isError and "camera must be one of" in bad.content[0].text

    anyio.run(exercise)


# --- the real characters/ folder (git-ignored art, so skipped where it is absent) ------------

CHARACTERS = ROOT / "characters"


def load_recipes():
    spec = importlib.util.spec_from_file_location(
        "ingest_library", ROOT / "tools" / "ingest_library.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(not CHARACTERS.is_dir(), reason="characters/ art folder not present")
def test_characters_recipes_feed_tilesets_and_actors(tmp_path_factory, monkeypatch):
    # Short folder name: pack/category/slug paths are long and Windows caps paths at 260.
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(tmp_path_factory.mktemp("l")))
    recipes = load_recipes()
    for pack_id, spec in recipes.PACKS.items():
        if recipes.source_path(spec).exists():
            recipes.ingest(pack_id)
    ids = {a["id"] for a in search()}

    # Every tile set tile and backdrop layer points at a real library asset.
    tilesets = load_tilesets()
    assert {ts["id"] for ts in tilesets} == {"gandalf-seasons", "pixel-crawler-dungeon"}
    for ts in tilesets:
        for name, spec in ts["tiles"].items():
            assert spec["asset"] in ids, (ts["id"], name)
            for variant in ts["variants"]:
                assert tile_image(ts, name, variant).getchannel("A").getbbox(), (ts["id"], name)
        if "backdrop" in ts:
            for variant in ts["variants"]:
                assert len(backdrop_layers(ts, variant)) == len(ts["backdrop"]["layers"])
    seasons = next(ts for ts in tilesets if ts["id"] == "gandalf-seasons")
    assert pick_variant(seasons, {"grass", "forest"}) == "normal"
    assert pick_variant(seasons, {"snow", "forest"}) == "winter"
    assert [ts["id"] for ts in load_tilesets("top-down")] == ["pixel-crawler-dungeon"]

    actors = {a.key: a for a in load_actors()}
    assert actors["luizmelo-monsters/flying eye"].side() == "side"
    assert {"idle", "run", "death"} <= set(actors["anokolisa-pixel-crawler/orc warrior"].anims)
    assert actors["anokolisa-pixel-crawler/body"].side() == "topdown"
