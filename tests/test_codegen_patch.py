"""M10.2 Spark token savings: repairs as edits, a stable prompt prefix, a filtered library."""

import os
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import library_context
from spriteforge.game.codegen import (
    SDK_API,
    CodeEdit,
    CodeFile,
    CodePatch,
    GameCode,
    PatchError,
    apply_patch,
    change_prompt,
    code_context,
    mock_code,
    programmer_prompt,
    repair_prompt,
)
from spriteforge.game.design import Entity, GameDesign, fallback_design
from spriteforge.game.pipeline import make_game
from spriteforge.library import add_pack
from spriteforge.library.store import DEFAULT_ROOT
from spriteforge.providers.mock import MockLLMProvider

GAME = "let speed = 60;\nfunction tick() { return speed; }\nsf.start({scenes: [], autoplay: () => ({})});"


def game(js: str = GAME) -> GameCode:
    return GameCode(files=[CodeFile(path="game.js", content=js)], notes="first notes")


def edit(old, new, path="game.js") -> CodeEdit:
    return CodeEdit(path=path, old=old, new=new)


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


def test_apply_patch_edits_in_order_and_adds_files():
    patched = apply_patch(
        game(),
        CodePatch(
            edits=[
                edit("let speed = 60;", "let speed = 90;"),
                edit("return speed;", "return speed * 2;"),
                edit("", "const ENEMIES = [];", "enemies.js"),  # a new file, whole
            ],
            notes="",
        ),
    )
    files = patched.as_dict()
    assert "let speed = 90;" in files["game.js"] and "speed * 2" in files["game.js"]
    assert files["enemies.js"] == "const ENEMIES = [];"
    assert patched.notes == "first notes"  # empty notes keep the previous ones
    rewritten = apply_patch(game(), CodePatch(edits=[edit("", "sf.start({});")], notes="new"))
    assert rewritten.as_dict() == {"game.js": "sf.start({});"} and rewritten.notes == "new"


def test_apply_patch_rejects_ambiguous_or_missing_text():
    bad = CodePatch(
        edits=[
            edit("speed", "pace"),  # twice in game.js
            edit("let lives = 3;", "let lives = 5;"),  # not there
            edit("x", "y", "missing.js"),
        ],
        notes="",
    )
    with pytest.raises(PatchError) as err:
        apply_patch(game(), bad)
    assert len(err.value.problems) == 3
    assert "occurs 2 times" in err.value.problems[0]
    assert "occurs 0 times" in err.value.problems[1]
    assert "no file missing.js" in err.value.problems[2]


def test_patch_applies_and_falls_back(tmp_path, budget):
    """A repair answer whose edit is ambiguous makes that round ask for complete files."""
    design = fallback_design("arena")
    crashing = (
        "class S extends Phaser.Scene { constructor() { super('s'); } update() { null.x; } }\n"
        "sf.start({scenes: [S], autoplay: () => ({})});"
    )
    scripted = MockLLMProvider(
        budget,
        [
            design.model_dump(),
            {"variant": "normal", "picks": []},  # forgotten entities get keyword picks
            game(crashing).model_dump(),
            CodePatch(edits=[edit("(", "[")], notes="").model_dump(),  # "(" is everywhere
            mock_code().model_dump(),  # the full-file answer
        ],
    )
    seen = []
    chat = scripted.chat

    def spy(request):
        seen.append((request.json_schema["title"], request.messages[-1].content))
        return chat(request)

    scripted.chat = spy
    cfg = load_config()
    cfg.game.review = False  # the script answers the Programmer only
    cfg.game.qa = False
    cfg.game.motion = False  # (motion design: test_motion)
    cfg.game.menus = False  # (menus: test_menus)
    cfg.game.sounds = False  # (sounds: test_sounds)
    result = make_game("arena", tmp_path / "g", cfg, mock=True, provider=scripted,
                       record=False, log=lambda m: None)  # fmt: skip
    assert [title for title, _ in seen] == ["GameDesign", "ArtPlan", "GameCode", "CodePatch",
                                            "GameCode"]  # fmt: skip
    assert "Answer with edits" in seen[3][1] and "Runtime error" in seen[3][1]
    assert "could not be applied" in seen[4][1] and "Return ALL files complete" in seen[4][1]
    assert "occurs" in seen[4][1]
    attempts = result.report["attempts"]
    assert [a["passed"] for a in attempts] == [False, True]
    assert attempts[0]["repair"] == "full files"


def test_prompts_share_stable_prefix():
    design = fallback_design("arena")
    manifest = {
        "screen": [320, 180],
        "scale": 1,
        "background": "#000",
        "sprites": {"hero": {"role": "player", "frame": [16, 16], "anims": {"idle": [0, 4]}}},
        "images": {},
    }
    prompts = [
        programmer_prompt(design, manifest),
        repair_prompt(design, game(), "Runtime error: x is null", manifest),
        change_prompt(design, game(), "enemies should be faster", manifest),
    ]
    shared = os.path.commonprefix(prompts)
    stable = SDK_API + design.model_dump_json(indent=1)
    assert len(shared) >= 0.9 * len(stable)
    assert shared.startswith(code_context(design, manifest).rstrip())
    assert SDK_API in shared and design.model_dump_json(indent=1) in shared
    # The changing parts come after it.
    assert all(part not in shared for part in ("Runtime error", "enemies should be faster"))


# --- the Art Director's library list ------------------------------------------------------


def strip(path: Path, frames: int, size: int = 32, color=(120, 120, 120, 255)) -> None:
    img = Image.new("RGBA", (frames * size, size))
    for n in range(frames):
        ImageDraw.Draw(img).rectangle((n * size + 4, 4, n * size + size - 5, size - 1), fill=color)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


def survival_design() -> GameDesign:
    """The shape of the M10.1 live survival design (workspace/m10-1-sheets)."""
    d = fallback_design("survival")

    def ent(eid, role, look, anims=()):
        return Entity(id=eid, role=role, look=look, behaviour="-", width=16, height=16,
                      animations=list(anims))  # fmt: skip

    d.entities = [
        ent("survivor", "player", "hooded survivor in a green cloak with a lantern, top-down",
            ["idle", "walk", "hurt", "death"]),
        ent("corner_slime", "enemy", "green round slime with glossy eyes", ["walk"]),
        ent("cave_bat", "enemy", "purple bat with wide wings and yellow eyes", ["fly"]),
        ent("crypt_skeleton", "enemy", "white skeleton warrior with a rusty sword", ["walk"]),
        ent("monster_gem", "pickup", "small bright cyan diamond gem", ["spin"]),
        ent("heart", "pickup", "red pixel heart with a white shine", ["pulse"]),
        ent("corner_portal", "prop", "dark purple swirling portal in a stone arch"),
        ent("floor_grass", "tile", "dark green grass floor tile"),
        ent("wall_stone", "tile", "grey stone block wall with moss"),
        ent("dark_woods_bg", "background", "dark forest at night"),
    ]  # fmt: skip
    return d


def test_library_context_filtered(tmp_path, monkeypatch):
    lib = tmp_path / "library"
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(lib))
    src = tmp_path / "pack"
    for actor, anims in {
        "Mobs/Skeleton": ["Idle", "Run", "Death"],
        "Mobs/Orc": ["Idle", "Run"],
        "Hero/Knight": ["Idle", "Run", "Death"],
        "Hero/Rogue": ["Idle", "Run", "Death"],
        "Hero/Wizzard": ["Idle", "Run", "Death"],
        "Hero/Citizen Tavern": ["Idle", "Walk"],
        "Hero/Body": ["Idle", "Walk", "Fishing", "Carry"],
    }.items():
        for anim in anims:
            strip(src / actor / f"{anim}.png", 4)
    strip(src / "Items" / "Heart.png", 1, 16)
    strip(src / "Items" / "Frying Pan.png", 1, 16)
    add_pack(src, "td", category="prop", categorize={"Mobs/": "enemy", "Hero/": "character"},
             cell="auto", name_depth=2, tags=["top-down"], license="CC0",
             license_verified=True, root=lib)  # fmt: skip
    design = survival_design()
    full, filtered = library_context("top-down"), library_context("top-down", design)
    assert "td/skeleton" in filtered and "td/knight" in filtered
    assert "td/items-heart" in filtered  # an object named like a pickup
    for absent in ("td/orc", "citizen", "tavern", "fishing", "td/body", "frying"):
        assert absent in full and absent not in filtered, absent
    assert len(filtered) / 4 < 600


@pytest.mark.skipif(
    not (DEFAULT_ROOT / "anokolisa-pixel-crawler").is_dir(),
    reason="the installed (not redistributable) library is not present",
)
def test_library_context_filtered_on_the_installed_library(monkeypatch):
    monkeypatch.setenv("SPRITEFORGE_LIBRARY", str(DEFAULT_ROOT))
    text = library_context("top-down", survival_design())
    assert "pixel-crawler/skeleton`" in text and "pixel-crawler/knight`" in text
    assert "citizen" not in text and "fishing" not in text and "tavern" not in text
    assert "pixel-crawler-dungeon/floor" in text  # every tile set for the camera
    assert len(text) / 4 < 600


def test_a_repair_that_makes_the_game_worse_is_not_kept(tmp_path, budget):
    """Live 2026-11-02: the last repair broke what the attempt before it had working, and the
    harness kept the last one. The best attempt is kept, and its build is the one on disk."""
    from spriteforge.game.pipeline import attempt_rank

    design = fallback_design("arena")
    almost = mock_code().files[0].content.replace("sf.event('gem_collect');", "")
    assert almost != mock_code().files[0].content
    scripted = MockLLMProvider(
        budget,
        [
            design.model_dump(),
            {"variant": "normal", "picks": []},
            game(almost).model_dump(),  # boots and plays; never emits gem_collect
            CodePatch(edits=[edit("sf.start(", "eval('1'); sf.start(")], notes="").model_dump(),
        ],
    )
    cfg = load_config()
    cfg.game.review = cfg.game.qa = cfg.game.motion = cfg.game.menus = cfg.game.sounds = False
    cfg.game.max_repairs = 1
    result = make_game("arena", tmp_path / "g", cfg, mock=True, provider=scripted,
                       record=False, log=lambda m: None)  # fmt: skip
    attempts = result.report["attempts"]
    assert [a["stage"] for a in attempts] == ["playtest", "static"]
    assert attempts[0].get("kept") and not attempts[1].get("kept")
    built = (tmp_path / "g" / "build" / "game.js").read_text(encoding="utf-8")
    assert "eval(" not in built and "gem_collect" not in built
    playtest = (tmp_path / "g" / "playtest.json").read_text(encoding="utf-8")
    assert '"stage": "playtest"' in playtest  # the kept attempt's report, not the static one
    # The order: passing everything > booting > the playtest > checks > QA > errors > later.
    static = {"stage": "static", "passed": False}
    booted = {"stage": "playtest", "passed": False, "ended": "timeout", "errors": [],
              "checks": [{"ok": True}, {"ok": False}]}  # fmt: skip
    no_boot = {**booted, "ended": "no-boot", "checks": [{"ok": False}, {"ok": False}]}
    played = {**booted, "passed": True, "checks": [{"ok": True}, {"ok": True}]}
    assert attempt_rank(booted, [], 0) > attempt_rank(static, [], 3)
    assert attempt_rank(booted, [], 0) > attempt_rank(no_boot, [], 3)
    assert attempt_rank(played, [{"passed": False}], 0) > attempt_rank(booted, [{"passed": True}], 3)
    assert attempt_rank(played, [{"passed": True}], 0) > attempt_rank(played, [{"passed": False}], 3)
    assert attempt_rank(booted, [], 2) > attempt_rank(booted, [], 1)  # a tie: the later one
