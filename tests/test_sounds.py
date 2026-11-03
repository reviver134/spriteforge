"""L2.7: the Sound Director's sounds as data, fitted by code, synthesised by the SDK, heard in
the bot's playtest, edited in the build's sound editor and revised from G4 words."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright
from test_game_flow import answer, arena_build, read, shown, submit
from test_levels import GATED, built, check, levels_of, slime_design, slime_plan

from spriteforge.app.server import create_app
from spriteforge.config import load_config
from spriteforge.core.gates import Gates
from spriteforge.core.state import Store
from spriteforge.game.build import build_game, install_sounds
from spriteforge.game.design import fallback_design
from spriteforge.game.playtest import playtest_levels
from spriteforge.game.record import serve
from spriteforge.game.revision import fallback_revision
from spriteforge.game.sounds import (
    PRESETS,
    CueIdea,
    SoundBrief,
    SoundSet,
    edited_record,
    fallback_brief,
    keep_edits,
    make_record,
    offline_revision,
    refit,
    sound_problems,
    sounds_script,
)
from spriteforge.stages.workflow import start_run, workflow_step


def slime_sounds(levels=True, menus=True):
    design = slime_design()
    return design, make_record(fallback_brief(design, levels, menus), design, "test", levels, menus)


def cue(record, name):
    return next(c for c in record.sounds.cues if c.name == name)


# --- code: the offline Sound Director, fit and checks ---------------------------------------------


def test_a_slime_splashes_squishes_and_bubbles_and_each_thing_breaks_its_own_way():
    _, record = slime_sounds()
    by_moment = {}
    for c in record.sounds.cues:
        for m in c.on:
            by_moment.setdefault(m, []).append(c)
    assert by_moment["land"][0].preset == "splash" and by_moment["jump"][0].preset == "squish"
    move = by_moment["move"][0]
    assert move.preset == "bubble_loop" and move.loop and 0.08 <= move.every <= 1
    breaks = {c.name: c for c in by_moment["break"]}
    assert breaks["crate_crate"].preset == "crate_break" and "crate" in breaks["crate_crate"].match
    assert breaks["wall_wall"].preset == "wall_break" and "wall" in breaks["wall_wall"].match
    assert by_moment["pickup"][0].preset == "coin" and by_moment["kill"][0].preset == "enemy_pop"
    assert {"checkpoint", "exit", "win", "lose", "menu_select", "menu_move", "pause"} <= set(by_moment)
    assert record.problems == [] and record.custom == {}
    # A top-down game without menus: no jump, land, checkpoint or menu sounds.
    arena = fallback_design("survive the arena")
    moments = {m for c in fallback_brief(arena).cues for m in c.on}
    assert "move" in moments and not moments & {"jump", "land", "checkpoint", "menu_select"}


def test_fit_repairs_presets_moments_and_tweaks_and_fills_silent_moments():
    design = slime_design()
    brief = SoundBrief(style="wet", cues=[
        CueIdea(name="Laser Shot", on=["shoot"], what="pew", preset="lazer", pitch=5, length=0.1, volume=3),
        CueIdea(name="slime walk", on=["walking"], what="bubbles", preset="bubble_loop", pitch=1, length=1, volume=0.5),
        CueIdea(name="slime walk", on=["Collect"], what="ding", preset="coin", pitch=1, length=1, volume=1),
        CueIdea(name="nothing", on=[], what="", preset="coin", pitch=1, length=1, volume=1),
    ])  # fmt: skip
    record = make_record(brief, design, "test", levels=True, menus=False)
    laser = cue(record, "laser_shot")
    assert laser.preset == "laser" and laser.on == ["shoot"] and laser.volume == 1.0
    assert laser.layers[0].freq == pytest.approx(PRESETS["laser"][1][0]["freq"] * 2)  # pitch clamped to 2
    walk = cue(record, "slime_walk")
    assert walk.on == ["move"] and walk.loop
    assert cue(record, "slime_walk_2").on == ["pickup"]  # a duplicate name made unique
    assert record.custom == {"shoot": "pew"}  # the game's own event: the Programmer emits it
    assert any("'shoot' is the game's own event" in n for n in record.notes)
    assert any("preset 'lazer' is not in the library: laser" in n for n in record.notes)
    assert any("plays on no moment: dropped" in n for n in record.notes)
    # Every moment the slime game has but the brief left silent got a sound.
    added = {m for c in record.sounds.cues for m in c.on}
    assert {"jump", "land", "break", "kill", "hurt", "checkpoint", "exit", "win"} <= added
    assert not any(m.startswith("menu") for m in added)  # no menus, no menu sounds


def test_human_edits_are_clamped_and_unknown_moments_are_flagged():
    _, record = slime_sounds()
    data = sounds_script(record)
    data["cues"][0]["layers"][0]["freq"] = 99999
    data["cues"][0]["volume"] = -2
    data["cues"].append({"name": "Boing!", "on": ["bounce_pad"], "layers": [{"wave": "kazoo"}]})
    edited = edited_record(SoundSet.model_validate(data), record, 3,
                           set(record.sounds.cues[0].on) | {"jump", "land"})  # fmt: skip
    first = edited.sounds.cues[0]
    assert first.layers[0].freq == 12000 and first.volume == 0
    assert edited.sounds.cues[-1].name == "boing" and edited.sounds.cues[-1].layers[0].wave == "square"
    assert edited.custom["bounce_pad"] == "" and edited.by.startswith("human") and edited.build == 3
    assert any("'bounce_pad' is not a moment the game emits yet" in n for n in edited.notes)
    assert any("silent" in p for p in edited.problems)  # volume 0 is allowed but reported
    assert sound_problems(record.sounds) == []


def test_a_revision_changes_only_what_it_names_and_keeps_the_humans_edits():
    design, record = slime_sounds()
    data = sounds_script(record)
    jump = next(c for c in data["cues"] if "jump" in c["on"])
    jump["layers"][0]["freq_end"] = 900  # shaped by hand in the editor
    edited = edited_record(SoundSet.model_validate(data), record, 1, {"jump"})
    brief = offline_revision(edited, "the splash is too loud")
    revised = keep_edits(edited, make_record(brief, design, "test", True, True))
    splash = cue(revised, "slime_splash_land")
    assert splash.volume == pytest.approx(cue(edited, "slime_splash_land").volume * 0.5)
    assert cue(revised, jump["name"]).layers[0].freq_end == 900  # the hand edit survived
    assert cue(revised, "slime_bubble_move").volume == cue(edited, "slime_bubble_move").volume
    # "no menu sounds": the named cues go, the rest stay.
    quiet = make_record(offline_revision(edited, "no menu sounds please"), design, "t", True, False)
    assert not any(c.name.startswith("menu") for c in quiet.sounds.cues)
    # The offline Director sends sound words to the Sound Director, and nothing else.
    r = fallback_revision(design, "the jump sound is too loud")
    assert r.sound_only and r.sound_changes and not r.code_instructions


def test_refit_adds_a_sound_for_a_new_moment_and_keeps_the_rest():
    design, record = slime_sounds()
    assert refit(record, design, True, True) is record  # nothing new: the same record
    without = make_record(SoundBrief(style="s", cues=[]), design.model_copy(update={"checks": design.checks}),
                          "t", True, False)  # fmt: skip
    again = refit(without, design, True, True)  # menus now: menu sounds are added
    assert again is not without and "added" in again.why
    assert {"menu_select", "menu_move", "pause"} <= {m for c in again.sounds.cues for m in c.on}


# --- the build ------------------------------------------------------------------------------------


def test_the_build_loads_the_sounds_after_the_sdk_and_ships_the_editor(tmp_path):
    build, design, _ = arena_build(tmp_path)
    record = make_record(fallback_brief(design, False, True), design, "t", False, True)
    install_sounds(build, sounds_script(record, design.title))
    assert {"sounds.js", "sounds.json", "sfsound.js", "sounds.html", "sfsound-editor.js"} <= {
        p.name for p in build.iterdir()}  # fmt: skip
    again = build_game(tmp_path / "b2", title="t", controls="c", files={"game.js": "sf.start({scenes: []});"},
                       assets={"screen": [320, 180], "scale": 1, "sprites": {}, "images": {}},
                       images={}, credits=[], menus={"x": 1}, sounds=sounds_script(record))  # fmt: skip
    html = (again / "index.html").read_text(encoding="utf-8")
    order = [html.index(s) for s in ('src="sf.js"', 'src="sounds.js"', 'src="sfsound.js"',
                                     'src="sfflow.js"', 'src="game.js"')]  # fmt: skip
    assert order == sorted(order) and 'href="sounds.html"' in html and "M mutes" in html
    loaded = json.loads((again / "sounds.json").read_text(encoding="utf-8"))
    assert set(loaded["presets"]) == set(PRESETS) and "move" in loaded["moments"]


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        chromium = pw.chromium.launch(headless=True)
        yield chromium
        chromium.close()


def test_the_bot_playtest_hears_the_slime_and_every_break(tmp_path):
    design, plan = slime_design(), slime_plan()
    records = [check(GATED, n=1, events=["crate_open", "enemy_defeat", "wall_break", "level_complete"])]
    build = built(tmp_path, design, plan, records)
    _, sounds = slime_sounds(levels=True, menus=False)
    install_sounds(build, sounds_script(sounds, design.title))
    html = (build / "index.html").read_text(encoding="utf-8")
    (build / "index.html").write_text(html.replace('<script src="levels.js">', '<script src="sounds.js">'
                                                   '</script><script src="sfsound.js"></script><script src="levels.js">'),
                                      encoding="utf-8")  # fmt: skip
    report = playtest_levels(build, [], levels_of(records))
    assert report["levels"][0]["cleared"] and report["errors"] == [], report["levels"][0]
    heard = report["sounds"]
    for name in ("slime_squish_jump", "slime_splash_land", "slime_bubble_move", "crate_crate",
                 "wall_wall", "dash", "level_complete"):  # fmt: skip
        assert heard.get(name, 0) >= 1, (name, heard)
    assert heard["slime_bubble_move"] >= 3  # a loop: it repeats while the slime moves
    assert heard["slime_squish_jump"] == report["events"]["jump"]  # event + effect: played once
    # The design's crate_open plays the crate's own break sound (once), not a second one.
    assert "crate_open" not in heard and heard["crate_crate"] == report["events"]["crate_open"]


def test_every_preset_is_audible_and_the_editor_edits_and_downloads(tmp_path, browser):
    build, design, _ = arena_build(tmp_path)
    record = make_record(fallback_brief(design, False, True), design, "t", False, True)
    install_sounds(build, sounds_script(record, design.title))
    with serve(build) as url:
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{url}/sounds.html")
        levels = page.evaluate("""() => Promise.all(Object.entries(window.SF_SOUNDS.presets).map(
            ([name, p]) => window.SFSynth.render({layers: p.layers, volume: 1}).then(r => [name, r])))""")
        for name, r in levels:
            assert r["peak"] > 0.05 and r["rms"] > 0.003 and r["seconds"] < 3, (name, r)
        assert page.locator("section.cue").count() == len(record.sounds.cues)
        assert page.locator("#save").is_hidden()  # a folder: no run to save to, download instead
        first = page.locator("section.cue").first
        first.get_by_role("button", name="Higher").click()
        assert "dirty" in (first.get_attribute("class") or "")
        payload = page.evaluate("window.sfEditor.payload()")
        assert payload["cues"][0]["layers"][0]["freq"] > record.sounds.cues[0].layers[0].freq
        with page.expect_download() as download:
            page.click("#download")
        text = Path(download.value.path()).read_text(encoding="utf-8")
        assert text.startswith("window.SF_SOUNDS = ") and '"presets"' in text
        assert errors == []


def test_m_and_the_settings_menu_turn_the_sound_off(tmp_path, browser):
    from test_game_flow import Page

    build, design, _ = arena_build(tmp_path)
    record = make_record(fallback_brief(design, False, True), design, "t", False, True)
    install_sounds(build, sounds_script(record, design.title))
    html = (build / "index.html").read_text(encoding="utf-8")
    (build / "index.html").write_text(html.replace('<script src="menus.js">', '<script src="sounds.js">'
                                                   '</script><script src="sfsound.js"></script><script src="menus.js">'),
                                      encoding="utf-8")  # fmt: skip
    with serve(build) as url:
        p = Page(browser, url, "headless=1&reset=1")
        assert p.js("window.sf.settings.sound") is True
        p.press("Enter")  # title -> main: the select sound plays (counted while silent)
        p.press("ArrowDown")
        counts = p.js("window.sf.soundCount")
        assert counts.get("menu_select", 0) >= 1 and counts.get("menu_move", 0) >= 1
        p.press("m")
        assert p.js("window.sf.settings.sound") is False
        assert p.js("window.sf.progress.settings.sound") is False  # saved with the settings
        p.goto("headless=1")
        assert p.js("window.sf.settings.sound") is False  # kept across a reload
        # The settings screen's Sound item turns it back on.
        p.press("Enter")
        options = p.js("window.sf.menu.options")
        p.press(str(options.index("Settings") + 1))
        assert p.menu() == "settings"
        sound = p.js("window.sf.menu.actions").index("sound")
        p.press(str(sound + 1))
        assert p.js("window.sf.settings.sound") is True and p.menu() == "settings"
        assert p.js("window.sf.errors") == []


# --- a gated run: v1 at G2, the editor's saves, G4 sound feedback without a rebuild ---------------


def gated_run(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.workspace = tmp_path
    ident = start_run(store, cfg, mock=True, goal="a slime in an arena, collect gems").id
    workflow_step(store, ident)
    answer(store, ident, form={"genre": "genres/top-down-dungeon-crawler"})
    answer(store, ident, form={"environment": "A slime cave"})
    answer(store, ident)
    submit(store, ident)  # approve the design: sounds v1, build v1, then G3 (menus)
    submit(store, ident)  # approve the menus: G4
    return store, ident


def test_sounds_in_a_gated_run_editor_saves_and_g4_feedback(tmp_path):
    store, ident = gated_run(tmp_path)
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and "game/sounds/v1.json" in shown(store, ident, gate)
    report = read(store, ident, "game/v1/report.json")
    assert report["sounds_version"] == 1 and report["sounds"]["cues"]
    assert any(c["heard"] for c in report["sounds"]["cues"])  # the bot's playtest heard sounds
    build = tmp_path / "runs" / ident / "game" / "v1" / "build"
    assert (build / "sounds.html").is_file() and 'src="sfsound.js"' in (build / "index.html").read_text()

    # The sound editor saves to the run: a new version by the human, played by build v1 at once.
    script = json.loads((build / "sounds.json").read_text(encoding="utf-8"))
    script["cues"][0]["volume"] = 0.25
    script["cues"][0]["layers"][0]["freq"] = 50000  # clamped
    with TestClient(create_app(store=store)) as client:
        assert client.get(f"/api/runs/{ident}").json()["play"]["sounds"] is True
        page = client.get(f"/api/runs/{ident}/play/v1/sounds.html")
        assert page.status_code == 200 and "sfsound-editor.js" in page.text
        saved = client.post(f"/api/runs/{ident}/sounds/v1", json={"sounds": script}).json()
        assert saved["version"] == 2 and saved["playing"] is True
        bad = client.post(f"/api/runs/{ident}/sounds/v1", json={"sounds": {"cues": "nope"}})
        assert bad.status_code == 422
        assert client.post(f"/api/runs/{ident}/sounds/v9", json={"sounds": script}).status_code == 404
    v2 = read(store, ident, "game/sounds/v2.json")
    assert v2["by"].startswith("human") and v2["build"] == 1
    assert v2["sounds"]["cues"][0]["volume"] == 0.25 and v2["sounds"]["cues"][0]["layers"][0]["freq"] == 12000
    on_disk = json.loads((build / "sounds.json").read_text(encoding="utf-8"))
    assert on_disk["cues"][0]["volume"] == 0.25

    # G4 words about a sound: the Sound Director revises it; the same build plays it (no rebuild).
    name = script["cues"][0]["name"].replace("_", " ")
    submit(store, ident, f"the {name} sound is too loud")
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and gate.title.startswith("G4 Playtest v1")
    assert "game/v2/report.json" not in {a.name for a in store.artifacts(ident)}
    v3 = read(store, ident, "game/sounds/v3.json")
    assert v3["sounds"]["cues"][0]["volume"] == pytest.approx(0.125)
    assert v3["sounds"]["cues"][0]["layers"][0]["freq"] == 12000  # the human's shape kept
    assert "game/sounds/v3.json" in shown(store, ident, gate)
    assert json.loads((build / "sounds.json").read_text(encoding="utf-8"))["cues"][0]["volume"] == pytest.approx(0.125)

    # A rebuild for other feedback plays the latest sounds.
    submit(store, ident, "make the enemies faster")
    report2 = read(store, ident, "game/v2/report.json")
    assert report2["sounds_version"] == 3
