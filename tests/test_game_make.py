"""Prompt-driven games: static checks, playtest, art rule (library first, else Muse), repair."""

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import ArtPick, ArtPlan, keyword_plan, make_art, resolve_plan
from spriteforge.game.codegen import CodeFile, GameCode, mock_code, static_check
from spriteforge.game.design import Check, Entity, GameDesign, fallback_design
from spriteforge.game.pipeline import make_game
from spriteforge.library import add_pack
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider

ASSETS = {"hero", "slime", "gem", "floor"}


def code(js: str) -> GameCode:
    return GameCode(files=[CodeFile(path="game.js", content=js)], notes="")


# --- static checks ----------------------------------------------------------------------------


def test_static_check_accepts_the_fixture_and_rejects_unsafe_code():
    assert static_check(mock_code(), ASSETS) == []
    start = "\nsf.start({scenes: [], autoplay: () => ({})});"
    bad = {
        "fetch('x')": "fetch()",
        "const u = 'https://evil.example/x.js';": "URLs",
        "import x from './x.js';": "ES module import/export",
        "new Phaser.Game({});": "new Phaser.Game",
        "let t = Date.now();": "wall-clock time",
        "eval('1');": "eval()",
        "sf.sprite(this, 1, 2, 'dragon');": "unknown asset id 'dragon'",
    }
    for snippet, problem in bad.items():
        problems = static_check(code(snippet + start), ASSETS)
        assert any(problem in p for p in problems), (snippet, problems)
    # Comments are not code; textures the game makes itself are not library ids.
    ok = "// see https://docs.phaser.io\nthis.add.image(0, 0, 'glow'); g.generateTexture('glow', 4, 4);"
    assert static_check(code(ok + start), ASSETS) == []
    assert any("sf.start" in p for p in static_check(code("let a = 1;"), ASSETS))
    assert any("syntax error" in p for p in static_check(code("function ( {" + start), ASSETS))


# --- the art rule: library first, otherwise Muse Image ---------------------------------------


def strip(path: Path, frames: int, size: int, color) -> None:
    img = Image.new("RGBA", (frames * size, size))
    for n in range(frames):
        ImageDraw.Draw(img).rectangle((n * size + 4, 4, n * size + size - 5, size - 1), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Top-down pack: a knight hero, a goblin enemy and a key; plus a no-AI pack."""
    lib = tmp_path / "library"
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    src = tmp_path / "pack"
    strip(src / "Mobs" / "Goblin" / "Run.png", 4, 32, (40, 160, 40, 255))
    strip(src / "Mobs" / "Goblin" / "Idle.png", 2, 32, (40, 160, 40, 255))
    strip(src / "Hero" / "Knight" / "Run.png", 4, 32, (160, 160, 200, 255))
    strip(src / "Hero" / "Knight" / "Idle.png", 2, 32, (160, 160, 200, 255))
    strip(src / "Items" / "Key.png", 1, 16, (230, 200, 40, 255))
    add_pack(
        src,
        "td",
        category="prop",
        categorize={"Mobs/": "enemy", "Hero/": "character"},
        cell="auto",
        name_depth=2,
        tags=["top-down"],
        license="CC0",
        license_verified=True,
        root=lib,
    )
    noai = tmp_path / "noai"
    strip(noai / "Coin.png", 1, 16, (250, 220, 0, 255))
    add_pack(
        noai,
        "noai",
        category="item",
        tags=["top-down"],
        license="CC0",
        license_verified=True,
        ai_allowed=False,
        root=lib,
    )
    return lib


def design_with(*entities: Entity) -> GameDesign:
    d = fallback_design("arena")
    d.entities = [d.entities[0], *entities]
    return d


def ent(eid, role, look, size=16, anims=None) -> Entity:
    return Entity(
        id=eid, role=role, look=look, behaviour="-", width=size, height=size, animations=anims or []
    )


class CountingImages(MockImageProvider):
    def __init__(self, budget):
        super().__init__(budget)
        self.prompts = []

    def generate(self, request):
        self.prompts.append(request.prompt)
        return super().generate(request)


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


def generator(provider):
    return lambda request, edit: (provider.edit if edit else provider.generate)(request).data


def test_library_first_then_muse_and_cap(library, budget):
    d = design_with(
        ent("goblin", "enemy", "a green goblin", 24, ["idle", "run"]),
        ent("bat", "enemy", "a purple bat", 16, ["fly"]),
        ent("key", "pickup", "a golden key", 12),
        ent("chest", "prop", "a wooden chest"),
    )
    plan = keyword_plan(d)
    picks = {p.entity: p for p in plan.picks}
    assert (picks["hero"].source, picks["hero"].asset) == ("actor", "td/knight")
    assert (picks["goblin"].source, picks["goblin"].asset) == ("actor", "td/goblin")
    assert (picks["key"].source, picks["key"].asset) == ("object", "td/items-key")
    assert picks["bat"].source == picks["chest"].source == "muse"  # nothing like them exists

    images = CountingImages(budget)
    art = make_art(d, plan, generate=generator(images), max_calls=1)
    assert art.filled["hero"].source == "library td/knight"
    assert art.filled["goblin"].meta["anims"] == {"idle": [0, 2], "run": [2, 4]}
    # One image call allowed: enemies come before props, so the bat gets Muse, the chest
    # becomes a reported placeholder.
    assert art.filled["bat"].source == "muse" and len(images.prompts) == 1
    assert "purple bat" in images.prompts[0]
    assert art.filled["chest"].source == "placeholder"
    assert any("chest" in w and "cap" in w for w in art.warnings)
    manifest = art.manifest()
    assert set(manifest["sprites"]) >= {"hero", "goblin", "bat", "key", "chest"}
    assert all(Path(p).name.endswith(".png") for p in art.files())


def pick(entity, source, asset="", reference="", prompt="") -> ArtPick:
    return ArtPick(
        entity=entity,
        source=source,
        asset=asset,
        prompt=prompt,
        reference=reference,
        recolor="",
        reason="",
    )


def test_invalid_picks_fall_back_to_library_then_muse(library):
    d = design_with(
        ent("goblin", "enemy", "a goblin"),
        ent("dragon", "enemy", "a red dragon"),
        ent("coin", "pickup", "a gold coin"),
        ent("orb", "pickup", "a glowing orb"),
    )
    bad = ArtPlan(
        variant="normal",
        picks=[
            pick("goblin", "actor", "td/dragon"),  # unknown actor, but the library has a goblin
            pick("dragon", "actor", "td/dragon"),  # unknown actor and no dragon anywhere
            pick("coin", "muse"),  # Muse asked for, but the library has a coin
            pick("orb", "muse", reference="noai/coin"),  # reference from a no-AI pack
            pick("ghost", "muse", prompt="x"),  # not in the design
        ],
    )
    picks, warnings = resolve_plan(bad, d)
    assert (picks["goblin"].source, picks["goblin"].asset) == ("actor", "td/goblin")
    assert picks["dragon"].source == "muse" and picks["dragon"].prompt == "a red dragon"
    assert any("td/dragon" in w for w in warnings)
    # Library first: shippable library art is used even from a no-AI pack (it is not sent to
    # Muse); as a Muse style reference that pack is refused.
    assert (picks["coin"].source, picks["coin"].asset) == ("object", "noai/coin")
    assert picks["orb"].source == "muse" and picks["orb"].reference.startswith("td/")
    assert any("forbids AI use" in w for w in warnings)
    assert any("ghost" in w for w in warnings) and "ghost" not in picks
    assert picks["hero"].source == "actor"  # forgotten entity: keyword pick
    # Objects match on what the entity is (its id), not on colour words in its description.
    red = design_with(ent("heart", "pickup", "a heart with a key hole"))
    assert {q.entity: q.source for q in keyword_plan(red).picks}["heart"] == "muse"


def test_unchanged_muse_request_is_locked(library, budget):
    d = design_with(ent("bat", "enemy", "a purple bat"))
    plan = keyword_plan(d)
    images = CountingImages(budget)
    first = make_art(d, plan, generate=generator(images))
    record = {**first.filled["bat"].record, "png": first.filled["bat"].png, "version": 1}
    again = make_art(
        d, plan, generate=generator(images), previous=lambda e: record if e == "bat" else None
    )
    assert len(images.prompts) == 1 and again.filled["bat"].source == "muse (locked, from v1)"


# --- pipeline: playtest + repair loop ---------------------------------------------------------


def test_make_game_offline_end_to_end(tmp_path):
    out = tmp_path / "game"
    result = make_game("survive the slime arena", out, load_config(), mock=True, log=lambda m: None)
    assert result.report["passed"], result.report
    assert (out / "build" / "index.html").is_file() and (out / "poster.png").is_file()
    playtest = json.loads((out / "playtest.json").read_text())
    assert playtest["ended"] == "win" and playtest["game_seconds"] >= 44
    assert {c["event"]: c["ok"] for c in playtest["checks"]} == {
        "enemy_spawn": True,
        "enemy_defeat": True,
        "gem_collect": True,
        "win": True,
    }
    # Empty library (tests): every entity is drawn by (mock) Muse Image, none is greybox.
    assert {a["source"] for a in result.report["art"]} == {"muse"}
    assert result.report["model_calls"]["image"] == 4
    # Each Muse request is recorded with its size and art spec.
    art = {e["entity"]: e for e in json.loads((out / "art.json").read_text())["entities"]}
    assert art["gem"]["request"]["size"] == [1024, 1024]
    assert art["gem"]["spec"]["grid"] == [8, 8] and "8×8 grid" in art["gem"]["muse_prompt"]
    assert art["floor"]["spec"]["anchor"].startswith("fills the whole grid")
    assert art["slime"]["frame"]["mode"] in {"native", "native-resized", "fit"}


def test_repair_loop_feeds_failures_back(tmp_path, budget):
    design = fallback_design("arena")
    unsafe = code("fetch('x');\nsf.start({scenes: [], autoplay: () => ({})});")
    crashing = code(  # boots, then throws every frame
        "class S extends Phaser.Scene { constructor() { super('s'); } update() { null.x; } }\n"
        "sf.start({scenes: [S], autoplay: () => ({})});"
    )
    scripted = MockLLMProvider(
        budget,
        [
            design.model_dump(),
            keyword_plan(design).model_dump(),
            unsafe.model_dump(),
            crashing.model_dump(),
            mock_code().model_dump(),
        ],
    )
    seen = []
    chat = scripted.chat

    def spy(request):
        seen.append(request.messages[-1].content)
        return chat(request)

    scripted.chat = spy
    result = make_game(
        "arena",
        tmp_path / "g",
        load_config(),
        mock=True,
        provider=scripted,
        record=False,
        log=lambda m: None,
    )
    attempts = result.report["attempts"]
    assert [a["stage"] for a in attempts] == ["static", "playtest", "playtest"]
    assert [a["passed"] for a in attempts] == [False, False, True]
    assert "uses fetch()" in seen[3]  # the static failure went back to the Programmer
    assert "Runtime error" in seen[4] and "null" in seen[4]


def test_playtest_reports_missing_events(tmp_path, budget):
    design = fallback_design("arena")
    design.checks.append(
        Check(
            id="boss", description="a boss appears", event="boss_spawn", min_count=1, by_second=30
        )
    )
    scripted = MockLLMProvider(
        budget,
        [
            design.model_dump(),
            keyword_plan(design).model_dump(),
            mock_code().model_dump(),
            mock_code().model_dump(),
        ],
    )
    cfg = load_config()
    cfg.game.max_repairs = 1
    result = make_game(
        "arena", tmp_path / "g", cfg, mock=True, provider=scripted, record=False, log=lambda m: None
    )
    report = json.loads((tmp_path / "g" / "playtest.json").read_text())
    assert not result.report["passed"]
    assert {c["event"]: c["ok"] for c in report["checks"]}["boss_spawn"] is False
    assert "event 'boss_spawn' happened 0 times" in result.report["attempts"][0]["summary"]
    assert len(result.report["attempts"]) == 1  # the same files came back: no pointless rerun


def test_spark_schemas_are_strict():
    """Meta's strict json_schema mode needs every property required and no extra keys."""

    def problems(node, path="$"):
        out = []
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                if node.get("additionalProperties") is not False:
                    out.append(f"{path}: additionalProperties")
                if missing := set(node["properties"]) - set(node.get("required", [])):
                    out.append(f"{path}: optional {sorted(missing)}")
            for k, v in node.items():
                out += problems(v, f"{path}/{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                out += problems(v, f"{path}[{i}]")
        return out

    for schema in (GameDesign, ArtPlan, GameCode):
        assert problems(schema.model_json_schema()) == [], schema.__name__


def test_design_normalises_model_output():
    d = fallback_design("x").model_dump()
    d["entities"][1]["id"] = "Big Slime"
    d["entities"][1]["width"] = 999
    d["checks"][0]["event"] = "Enemy Spawn"
    d["checks"][0]["by_second"] = 9999
    design = GameDesign.model_validate(d)
    assert design.entities[1].id == "big_slime" and design.entities[1].width == 128
    assert design.checks[0].event == "enemy_spawn"
    assert design.checks[0].by_second == 0  # a deadline at the session end means "any time"
    d["entities"][1]["role"] = "player"
    with pytest.raises(ValueError, match="exactly one"):
        GameDesign.model_validate(d)
