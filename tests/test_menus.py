"""L2.5: menus as data: the validator, code's repairs, the theme in the art's colours, the
offline Menu Designer, the title art and the Programmer's contract."""

import io

import pytest
from PIL import Image
from test_levels import slime_design, slime_plan

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.codegen import FLOW_API, programmer_prompt
from spriteforge.game.design import fallback_design
from spriteforge.game.menus import (
    MenuItem,
    MenuSpec,
    Screen,
    Theme,
    contrast,
    fallback_menu,
    fit_menu,
    fit_theme,
    menu_problems,
    menu_prompt,
    menus_script,
    needed_screens,
    offline_revision,
)
from spriteforge.game.pipeline import Models, game_assets, menu_pack, revise_menus, write_menus
from spriteforge.game.structure import GameStructure, PlannedLevel, make_structure
from spriteforge.game.title_art import make_title_art, prompts
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider


def arena():
    design = fallback_design("survive the arena")
    return design, make_structure(design, None, False)


def platformer():
    design = slime_design()
    return design, make_structure(design, slime_plan(), True)


def spec_for(design, structure) -> MenuSpec:
    spec, _ = fit_menu(fallback_menu(design, structure), structure, design)
    return spec


def screen(spec, sid) -> Screen:
    return next(s for s in spec.screens if s.id == sid)


# --- the structure ----------------------------------------------------------------------------------


def test_the_structure_has_the_plans_levels_or_one_level():
    design, one = arena()
    assert [lv.name for lv in one.levels] == [design.title] and one.lose == design.lose
    pdesign, three = platformer()
    assert len(three.levels) == 3 and three.levels[0].n == 1 and three.win.startswith("Finish level 3")
    assert any("unlocked" in s for s in three.saved) and three.lose == ""
    with pytest.raises(ValueError, match="unique"):
        GameStructure(levels=[PlannedLevel(n=1, name="A", goal=""), PlannedLevel(n=2, name="a", goal="")],
                      carries=[], saved=[], win="w", lose="")  # fmt: skip
    with pytest.raises(ValueError, match="1 to 8"):
        GameStructure(levels=[PlannedLevel(n=i, name=f"L{i}", goal="") for i in range(1, 10)],
                      carries=[], saved=[], win="w", lose="")  # fmt: skip


# --- the validator ------------------------------------------------------------------------------------


def test_the_offline_menus_hold_for_any_design():
    for design, structure in (arena(), platformer()):
        spec = spec_for(design, structure)
        assert menu_problems(spec, structure) == []
        assert [s.id for s in spec.screens] == needed_screens(structure)
    _, three = platformer()
    assert {"results", "level_select"} <= set(needed_screens(three)) and "game_over" not in needed_screens(three)
    _, one = arena()
    assert "game_over" in needed_screens(one) and "level_select" not in needed_screens(one)


def test_the_validator_finds_what_breaks_the_rules():
    design, structure = arena()
    good = spec_for(design, structure)
    # A screen nothing leads to: the main menu without its settings item.
    main = screen(good, "main").model_copy(update={"items": [MenuItem(label="Start", action="start")]})
    unreachable = good.model_copy(update={"screens": [main if s.id == "main" else s for s in good.screens]})
    problems = menu_problems(unreachable, structure)
    assert "the controls screen cannot be reached from the title" in problems
    assert "main: the settings item is missing" in problems
    # Code repairs it: the missing items come back and the controls get an item that opens them.
    fitted, notes = fit_menu(unreachable, structure, design)
    assert menu_problems(fitted, structure) == [] and "main: the settings item was missing: added" in notes
    # An unknown action, and one not allowed there.
    odd = screen(good, "pause").model_copy(update={"items": [
        *screen(good, "pause").items, MenuItem(label="Dance", action="dance"),
        MenuItem(label="Next", action="next_level")]})  # fmt: skip
    problems = menu_problems(good.model_copy(update={"screens": [odd if s.id == "pause" else s
                                                                 for s in good.screens]}), structure)  # fmt: skip
    assert "pause: item 'Dance' has an unknown action 'dance'" in problems
    assert "pause: item 'Next' (next_level) is not allowed there" in problems
    # A label too long for the panel, and text that cannot be read on it.
    long = screen(good, "main").model_copy(update={"items": [
        MenuItem(label="Start a brand new adventure in the slime caves today", action="start"),
        *screen(good, "main").items[1:]]})  # fmt: skip
    dark = good.theme.model_copy(update={"text": "#202230"})
    problems = menu_problems(good.model_copy(update={"theme": dark, "screens": [
        long if s.id == "main" else s for s in good.screens]}), structure)  # fmt: skip
    assert any(p.startswith("main: label 'Start a brand new") and "fit the panel" in p for p in problems)
    assert any(p.startswith("theme text #202230 on the panel") for p in problems)
    # A screen this game does not have.
    extra = good.model_copy(update={"screens": [*good.screens, Screen(id="shop", title="", subtitle="", items=[])]})
    assert "unknown screen 'shop'" in menu_problems(extra, structure)


def test_fit_repairs_what_code_can():
    design, structure = platformer()
    messy = MenuSpec(
        title="Jump To The Door: The Extremely Long Subtitle Edition Of The Game",
        subtitle="Hop!",
        theme=Theme(panel="#101010", border="gold", text="#151515", highlight="#F4D35E", dim="#333333",
                    style="wavy"),
        screens=[
            Screen(id="title", title="", subtitle="", items=[MenuItem(label="Press Start", action="press_start")]),
            Screen(id="main", title="", subtitle="", items=[
                MenuItem(label="Play", action="play"), MenuItem(label="Options", action="options"),
                MenuItem(label="Play again", action="start"), MenuItem(label="Shop", action="shop")]),
            Screen(id="gameover", title="Oops", subtitle="", items=[]),
        ],
        credits=["me"], title_art="", emblem="",
    )  # fmt: skip
    spec, notes = fit_menu(messy, structure, design)
    assert menu_problems(spec, structure) == []
    main = screen(spec, "main")
    assert [i.action for i in main.items][:2] == ["start", "settings"]  # synonyms mapped
    assert "main: item 'Play again' (start) dropped: repeated" in notes
    assert "main: item 'Shop' dropped: unknown action 'shop'" in notes
    assert "screen 'gameover' dropped: this game does not need it" in notes  # a platformer: no losing
    assert "screen pause was missing: added with its plain items" in notes
    assert len(spec.title) < len(messy.title) and spec.theme.style == "framed"
    assert contrast(spec.theme.text, spec.theme.panel) >= 4.5
    assert spec.title_art and spec.emblem  # empty descriptions come from the design


def test_the_theme_takes_the_art_colours_and_stays_readable():
    theme = Theme(panel="#FFFFFF", border="#FFFFFF", text="#FFFFFF", highlight="#CC2222", dim="#FFFFFF",
                  style="double")  # fmt: skip
    colours = [("#2A6E3F", 0.5), ("#C9E4A0", 0.3), ("#D04030", 0.1), ("#1B1E2B", 0.1)]
    fitted, notes = fit_theme(theme, colours)
    assert fitted.highlight == "#D04030" or contrast(fitted.highlight, fitted.panel) >= 4.5
    assert contrast(fitted.text, fitted.panel) >= 4.5 and contrast(fitted.highlight, fitted.panel) >= 4.5
    assert fitted.panel != "#FFFFFF" and "theme colours taken from the locked art's measured colours" in notes


# --- the Menu Designer ------------------------------------------------------------------------------


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    return Budget(store, run.id, cfg.budget), cfg


def test_problems_go_back_to_the_menu_designer_once(budget):
    b, cfg = budget
    design, structure = arena()
    good = spec_for(design, structure)
    bad = good.model_copy(update={"theme": good.theme.model_copy(update={"panel": "not a colour"}),
                                  "screens": [s for s in good.screens if s.id != "settings"]})  # fmt: skip
    # Code repairs the missing screen and the colour, so the first answer has no problems left.
    provider = MockLLMProvider(b, [bad.model_dump()])
    record = write_menus(Models(cfg, b, True, provider), design, structure)
    assert record.problems == [] and not record.retried
    assert any("screen settings was missing" in n for n in record.notes)
    prompt = menu_prompt(design, structure, "## the record")
    assert "game_over: actions retry, title; must have retry, title" in prompt and "the record" in prompt


def test_the_offline_menu_designer_takes_a_named_colour(budget):
    b, cfg = budget
    design, structure = arena()
    record = write_menus(Models(cfg, b, True), design, structure)
    red = revise_menus(Models(cfg, b, True), record, "make the menus red", design, structure)
    assert red.spec.theme.highlight != record.spec.theme.highlight
    assert offline_revision(record.spec, "fine as it is") == record.spec


# --- the title art -------------------------------------------------------------------------------------


def art_for(design):
    return make_art(design, keyword_plan(design), generate=None)


def test_the_title_art_is_code_drawn_offline():
    design, structure = arena()
    spec, art = spec_for(design, structure), art_for(design)
    title = make_title_art(spec, design, art, None)
    assert title.source == "code-drawn: offline (no image model)"
    assert Image.open(io.BytesIO(title.title)).size == tuple(art.screen)
    assert Image.open(io.BytesIO(title.emblem)).getbbox() is not None
    assert "No text, no letters" in title.prompts["title"] and title.prompts == prompts(spec, design)


def test_muse_draws_the_title_art_once_and_it_stays_locked(budget):
    b, cfg = budget
    design, structure = arena()
    spec, art = spec_for(design, structure), art_for(design)
    images = MockImageProvider(b)
    calls = []
    generate = lambda request, edit: (calls.append(request), images.generate(request).data)[1]
    first = make_title_art(spec, design, art, generate)
    assert len(calls) == 2 and first.source.startswith("muse") and set(first.raw) == {"title", "emblem"}
    again = make_title_art(spec, design, art, generate, previous=first)
    assert again is first and len(calls) == 2  # locked: no call
    changed = spec.model_copy(update={"emblem": "a golden gem"})
    make_title_art(changed, design, art, generate, previous=first)
    assert len(calls) == 4  # its description changed: drawn again
    # Library art is never sent to Muse as a style reference.
    assert all(not r.references for r in calls)


def test_a_build_carries_the_menus_and_the_programmer_is_told(budget):
    b, cfg = budget
    design, structure = arena()
    record = write_menus(Models(cfg, b, True), design, structure)
    art = art_for(design)
    pack = menu_pack(Models(cfg, b, True), record, design, art, cfg)
    manifest, files = game_assets(art, pack)
    assert manifest["images"]["sf_title_art"]["role"] == "menu" and "assets/sf_emblem.png" in files
    assert pack.script["art"] == {"title": "sf_title_art", "emblem": "sf_emblem"}
    assert pack.script["screens"]["pause"]["items"][0]["action"] == "resume"
    prompt = programmer_prompt(design, manifest)
    assert FLOW_API in prompt and "sf.settings.multiplier" in prompt
    assert "sf_title_art" not in prompt  # the title art is the SDK's, not the Programmer's
    assert FLOW_API not in programmer_prompt(design, art.manifest())
    script = menus_script(record.spec, structure, design, ["Pixel Crawler - Free Pack 2.11 (CC0)"])
    assert "Pixel Crawler - Free Pack 2.11" in script["credits"]
