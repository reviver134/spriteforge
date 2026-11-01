"""Library descriptions: measured looks for every image, Muse Spark vision only for packs that
allow AI use; and the art step checking library picks against the design's colours."""

import json
import re
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw

from spriteforge.config import load_config
from spriteforge.core.cost import Budget, BudgetExceeded
from spriteforge.core.state import Store
from spriteforge.game.actors import recolor
from spriteforge.game.art import (
    ArtPick,
    ArtPlan,
    art_spec,
    auto_reference,
    library_context,
    make_art,
    resolve_plan,
)
from spriteforge.game.design import Entity, fallback_design
from spriteforge.game.tilesets import load_tilesets
from spriteforge.library import add_pack, search
from spriteforge.library.describe import describe_library
from spriteforge.library.looks import colour_gap, colour_name, measure
from spriteforge.library.store import DEFAULT_ROOT
from spriteforge.mcp.library import search_assets
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockLLMProvider

SIZES = [(1024, 1024), (1792, 1024)]


def sprite(path: Path, frames: int, size: int, color, cracks=None) -> None:
    img = Image.new("RGBA", (frames * size, size))
    draw = ImageDraw.Draw(img)
    for n in range(frames):
        draw.rectangle((n * size + 2, 2, n * size + size - 3, size - 1), fill=color)
        if cracks:
            draw.line((n * size + 3, 5, n * size + size - 4, size - 5), fill=cracks)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


@pytest.fixture
def library(tmp_path, monkeypatch) -> Path:
    """One pack that allows AI use and one that forbids it."""
    lib, open_src, closed_src = tmp_path / "library", tmp_path / "open", tmp_path / "closed"
    sprite(open_src / "Blob" / "Idle.png", 2, 16, (200, 50, 50, 255))
    sprite(open_src / "Hero" / "Idle.png", 2, 16, (30, 150, 150, 255))
    sprite(open_src / "Floor.png", 1, 16, (143, 123, 98, 255), cracks=(100, 85, 66, 255))
    sprite(closed_src / "Mobs" / "Goblin" / "Run.png", 4, 16, (40, 160, 40, 255))
    common = {"cell": "auto", "name_depth": 2, "tags": ["top-down"], "license": "CC0",
              "license_verified": True, "root": lib}  # fmt: skip
    add_pack(open_src, "open-pack", category="prop", ai_allowed=True,
             categorize={"Blob/": "enemy", "Hero/": "character", "Floor": "tileset"},
             **common)  # fmt: skip
    add_pack(closed_src, "closed-pack", category="enemy", ai_allowed=False, **common)
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    return lib


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


class Describer(MockLLMProvider):
    """Answers every contact sheet: image n is `names[n]` (else 'thing n'); records requests.
    `fail` maps a call number (1-based) to the exception that call raises."""

    def __init__(self, budget, names=None, fail=None):
        super().__init__(budget, [])
        self.names, self.fail, self.requests = names or {}, fail or {}, []

    def chat(self, request):
        with self.budget.attempt("mock", "describer"):
            self.requests.append(request)
            if exc := self.fail.get(len(self.requests)):
                raise exc
            listed = re.findall(r'^(\d+)\. "([^"]+)"', request.messages[-1].content, re.MULTILINE)
            images = [
                {"n": int(n), "what": self.names.get(name, f"thing {n}"),
                 "description": f"a described {self.names.get(name, name)}", "view": "top-down"}
                for n, name in listed
            ]  # fmt: skip
            return ChatResult(content=json.dumps({"images": images}))


def entries(lib: Path, pack: str) -> dict:
    return json.loads((lib / "descriptions" / f"{pack}.json").read_text(encoding="utf-8"))["assets"]


# --- measuring -----------------------------------------------------------------------------


def test_colour_names_brightness_and_detail():
    names = {(143, 123, 98): "tan", (81, 100, 103): "grey-teal", (58, 58, 61): "dark grey",
             (30, 150, 150): "teal", (200, 50, 50): "red", (122, 122, 46): "olive",
             (20, 20, 24): "black", (240, 240, 240): "white", (65, 133, 202): "blue"}  # fmt: skip
    assert {rgb: colour_name(rgb) for rgb in names} == names
    flat = measure(Image.new("RGBA", (16, 16), (81, 100, 103, 255)))
    assert flat.text == "grey-teal; dark (0.38); flat, no detail" and flat.fill == 1
    tile = Image.new("RGBA", (16, 16), (143, 123, 98, 255))
    ImageDraw.Draw(tile).rectangle((0, 0, 15, 4), fill=(90, 75, 60, 255))
    look = measure(tile)
    assert [c.name for c in look.colours] == ["tan", "brown"] and look.detail > 0.12
    assert colour_gap("#8F7B62", "#3A3A3D") > 25 > colour_gap("#516467", "#808088")


# --- describing ----------------------------------------------------------------------------


def test_only_ai_allowed_art_reaches_the_model(library, budget):
    names = {"blob idle": "red slime", "hero idle": "teal hero", "floor": "tan stone tile"}
    describer = Describer(budget, names)
    counts = describe_library(library, describer, model="spark-test")
    assert counts == {"model": 3, "measured": 1, "kept": 0, "failed": 0, "sheets": 1}
    [request] = describer.requests
    assert len(request.messages[-1].images) == 1  # one contact sheet
    prompt = request.messages[-1].content
    assert all(f'"{n}"' in prompt for n in names) and "goblin" not in prompt
    sheet = Image.open(__import__("io").BytesIO(request.messages[-1].images[0]))
    assert sheet.size == (3 * 128, 128 + 18)  # 3 numbered cells, nothing from closed-pack
    described = entries(library, "open-pack")
    blob = described["open-pack/blob-idle"]
    assert blob["what"] == "red slime" and blob["by"] == "spark-test"
    assert blob["status"] == "generated" and blob["look"]["colours"][0]["name"] == "red"
    goblin = entries(library, "closed-pack")["closed-pack/goblin-run"]
    assert goblin["by"] == "measured" and goblin["text"].startswith("goblin run (enemy); green")
    # The index carries the descriptions: search(), the MCP tools and the catalog read them.
    found = {a["id"]: a for a in search()}
    assert found["open-pack/blob-idle"]["description"] == "a described red slime"
    assert found["closed-pack/goblin-run"]["described_by"] == "measured"
    assert [a["id"] for a in search_assets("slime")] == ["open-pack/blob-idle"]


def test_rerun_keeps_reviewed_text_and_describes_changed_images(library, budget):
    describe_library(library, Describer(budget), model="spark-test")
    path = library / "descriptions" / "open-pack.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["assets"]["open-pack/hero-idle"].update(text="my words", status="reviewed")
    data["assets"]["open-pack/floor"]["sha256"] = "0" * 64  # as if the image changed
    path.write_text(json.dumps(data), encoding="utf-8")
    again = Describer(budget)
    counts = describe_library(library, again, model="spark-test")
    assert counts == {"model": 1, "measured": 1, "kept": 2, "failed": 0, "sheets": 1}
    assert re.findall(
        r'^\d+\. "([^"]+)"', again.requests[0].messages[-1].content, re.MULTILINE
    ) == ["floor"]
    now = entries(library, "open-pack")
    assert now["open-pack/hero-idle"]["text"] == "my words"
    assert now["open-pack/hero-idle"]["status"] == "reviewed"
    assert now["open-pack/floor"]["sha256"] != "0" * 64


def test_failed_sheets_keep_measured_entries(library, budget):
    describer = Describer(budget, fail={1: RuntimeError("HTTP 500"), 2: BudgetExceeded("cap")})
    logged = []
    counts = describe_library(library, describer, model="x", per_sheet=1, log=logged.append)
    assert counts == {"model": 0, "measured": 1, "kept": 0, "failed": 3, "sheets": 2}
    assert len(describer.requests) == 2  # out of budget: no third sheet
    assert all(e["by"] == "measured" for e in entries(library, "open-pack").values())
    assert any("HTTP 500" in line for line in logged)


def test_offline_describe_is_measured_only(library):
    counts = describe_library(library, None)
    assert counts["model"] == 0 and counts["measured"] == 4
    assert all(a["described_by"] == "measured" for a in search())


# --- the art step uses what the art looks like (the installed library) -----------------------

needs_dungeon = pytest.mark.skipif(
    not any(ts["id"] == "pixel-crawler-dungeon" for ts in load_tilesets("top-down")),
    reason="the Pixel Crawler pack is not installed",
)


@pytest.fixture
def installed(monkeypatch):
    """The installed library (read only), instead of the empty one tests get by default."""
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(DEFAULT_ROOT))


def tile_plan(look: str, tile: str) -> tuple:
    design = fallback_design("arena")
    floor = next(e for e in design.entities if e.id == "floor")
    floor.look = look
    pick = ArtPick(entity="floor", source="tile", asset=tile, prompt="", reference="",
                   recolor="", reason="test pick")  # fmt: skip
    return design, ArtPlan(variant="normal", picks=[pick])


@needs_dungeon
def test_library_tile_far_from_the_named_colour_is_recoloured(installed):
    design, plan = tile_plan("Dark grey stone floor tile with subtle cracks",
                             "pixel-crawler-dungeon/floor")  # fmt: skip
    picks, warnings = resolve_plan(plan, design)
    assert picks["floor"].source == "tile" and picks["floor"].recolor == "dark grey"
    assert any("is tan (#8F7B62); the design asks for dark grey" in w for w in warnings)
    floor = make_art(design, plan, generate=None).filled["floor"]
    look = measure(Image.open(__import__("io").BytesIO(floor.png)))
    assert colour_gap(look.mean, "#3A3A3D") < 10 and look.detail >= 0.03  # cracks kept
    assert "recoloured dark grey (#3A3A3D)" in floor.reasons
    # A look that names no colour, or a near one, keeps the library tile as it is.
    design, plan = tile_plan("stone floor tile", "pixel-crawler-dungeon/floor")
    assert resolve_plan(plan, design)[0]["floor"].recolor == ""
    # A colour named only for a detail is an accent, not the tile's colour.
    design, plan = tile_plan("Higher dark stone wall block with red moss edge highlight",
                             "pixel-crawler-dungeon/wall_face")  # fmt: skip
    assert resolve_plan(plan, design)[0]["floor"].recolor == ""


@needs_dungeon
def test_flat_library_tile_is_not_used_for_a_detailed_look(installed):
    design, plan = tile_plan("Brown-grey brick wall block with moss top",
                             "pixel-crawler-dungeon/wall")  # fmt: skip
    picks, warnings = resolve_plan(plan, design)
    assert picks["floor"].source == "muse" and picks["floor"].prompt
    assert any("is flat" in w and "brick, moss" in w for w in warnings)
    design, plan = tile_plan("plain wall top", "pixel-crawler-dungeon/wall")
    assert resolve_plan(plan, design)[0]["floor"].source == "tile"
    # Library first does not swap a Muse request back to a flat tile named like the entity.
    design = fallback_design("arena")
    design.entities.append(Entity(id="arena_wall", role="tile", behaviour="-", width=16,
                                  height=16, animations=[],
                                  look="Rusty brown metal wall block with hazard stripes"))  # fmt: skip
    pick = ArtPick(entity="arena_wall", source="muse", asset="", prompt="rusty metal wall",
                   reference="", recolor="", reason="the library walls are flat")  # fmt: skip
    picks, warnings = resolve_plan(ArtPlan(variant="normal", picks=[pick]), design)
    assert picks["arena_wall"].source == "muse"
    assert not any(w.startswith("arena_wall:") for w in warnings)


def test_recolour_moves_brightness_to_the_named_colour():
    import colorsys

    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[1:7, 1:7] = (90, 88, 30, 255)  # dark olive cloak
    rgba[3:5, 3:5] = (60, 58, 20, 255)  # its shadow
    strip = Image.fromarray(rgba)
    hue_only, lifted = recolor(strip, 175), recolor(strip, 175, value=0.59)

    def cloak(img):
        hsv = [
            colorsys.rgb_to_hsv(*(np.array(px[:3]) / 255))
            for px in np.asarray(img)[1:7, 1:7].reshape(-1, 4)
        ]
        return np.mean([v for *_, v in hsv]), np.mean([h for h, *_ in hsv]) * 360

    assert abs(cloak(hue_only)[0] - cloak(strip)[0]) < 0.01  # a hue turn keeps it dark
    darker = recolor(strip, 0, value=0.2)  # "dark red": never darker than the art was
    assert abs(cloak(darker)[0] - cloak(strip)[0]) < 0.01
    value, hue = cloak(lifted)
    assert abs(value - 0.59) < 0.03 and abs(hue - 175) < 6
    px = np.asarray(lifted)
    assert px[3, 3, :3].sum() < px[1, 1, :3].sum()  # the shadow stays darker


def test_background_view_and_style_example(installed):
    design = fallback_design("arena")
    void = Entity(id="void", role="background", look="very dark blue-black void with mist",
                  behaviour="-", width=128, height=128, animations=[])  # fmt: skip
    spec = art_spec(void, design, (320, 180), 16, SIZES)
    assert "straight down" in spec.view and "front of the subject" not in spec.view
    assert auto_reference(void, design, ["anokolisa-pixel-crawler"]) == ""
    design.entities.append(void)
    sheet = next((a["id"] for a in search(ai=True) if a["category"] == "tileset"), "")
    if not sheet:
        pytest.skip("no AI-allowed tile sheet installed")
    pick = ArtPick(entity="void", source="muse", asset="", prompt="a dark void",
                   reference=sheet, recolor="", reason="test")  # fmt: skip
    picks, warnings = resolve_plan(ArtPlan(variant="normal", picks=[pick]), design)
    assert picks["void"].reference == ""
    assert any("void: a background takes a style example only from a backdrop" in w
               for w in warnings)  # fmt: skip


@needs_dungeon
def test_art_director_sees_what_library_art_looks_like(installed):
    text = library_context("top-down", fallback_design("arena"))
    line = lambda ident: next(t for t in text.splitlines() if f"`{ident}`:" in t)
    floor, wall = line("pixel-crawler-dungeon/floor"), line("pixel-crawler-dungeon/wall")
    assert floor.startswith("  * `pixel-crawler-dungeon/floor`: stone floor — ")
    assert floor.endswith("; tan; brightness 0.49, low-contrast detail")
    assert wall.endswith("; grey-teal; brightness 0.38, flat, no detail")
