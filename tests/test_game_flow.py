"""L2.5: the SDK's flow in a browser (title, menus, pause, results, game over, victory, saved
progress), the bot playtest with menus, the QA steps for menus, and G3 in a gated run."""

import io
import json

import pytest
from PIL import Image
from playwright.sync_api import sync_playwright
from test_game_db import conformance
from test_levels import GATED, check, levels_of, slime_design, slime_plan

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.build import build_game
from spriteforge.game.codegen import MOCK_PLATFORMER, mock_code
from spriteforge.game.design import fallback_design
from spriteforge.game.gamedb import open_db
from spriteforge.game.menus import fallback_menu, fit_menu, menus_script
from spriteforge.game.levels import levels_script, player_body
from spriteforge.game.pipeline import Models, menu_shots, produce_art
from spriteforge.game.playtest import playtest, playtest_levels
from spriteforge.game.qa import QATest, blank_step, run_qa
from spriteforge.game.record import READY, serve, wait_until
from spriteforge.game.structure import make_structure
from spriteforge.memory.okf.validate import validate
from spriteforge.stages.workflow import start_run, workflow_step

s = blank_step


def arena_build(tmp_path):
    design = fallback_design("survive the arena")
    structure = make_structure(design, None, False)
    spec, _ = fit_menu(fallback_menu(design, structure), structure, design)
    art = make_art(design, keyword_plan(design), generate=None)
    script = menus_script(spec, structure, design)
    build = build_game(tmp_path / "build", title=design.title, controls=design.controls,
                       files=mock_code().as_dict(), assets=art.manifest(), images=art.files(),
                       credits=[], menus=script)  # fmt: skip
    return build, design, script


class Page:
    """One page of a build with real key presses while the harness steps frames."""

    def __init__(self, browser, url, query):
        self.page, self.url = browser.new_page(viewport={"width": 320, "height": 180}), url
        self.goto(query)

    def goto(self, query):
        """The build again in the same page (its saved progress is kept unless reset=1)."""
        self.page.goto(f"{self.url}/index.html?{query}")
        assert wait_until(self.page, READY)
        return self

    def run(self, seconds):
        self.page.evaluate(f"window.sfRun({seconds}, 30, true)")

    def press(self, key):
        self.page.keyboard.down(key)
        self.run(0.1)
        self.page.keyboard.up(key)
        self.run(1 / 30)

    def js(self, expression):
        return self.page.evaluate(expression)

    def menu(self):
        return (self.js("window.sf.menu") or {}).get("screen")

    def events(self):
        return [e["name"] for e in self.js("window.sf.events")]


@pytest.fixture
def browser():
    with sync_playwright() as pw:
        chromium = pw.chromium.launch(headless=True)
        yield chromium
        chromium.close()


def test_the_flow_with_real_keys(tmp_path, browser):
    build, _, _ = arena_build(tmp_path)
    with serve(build) as url:
        p = Page(browser, url, "headless=1&reset=1")
        assert p.menu() == "title" and p.js("window.sf.flow.state") == "title"
        p.press("Enter")
        assert p.menu() == "main"
        p.press("Enter")  # Start
        p.run(1)
        assert p.menu() is None and "level_start" in p.events()
        # Escape pauses: the clock stands still and the game does not move.
        p.press("Escape")
        assert p.menu() == "pause"
        before = p.js("[window.sf.clock.now, window.sf.state.x]")
        p.page.keyboard.down("ArrowRight")
        p.run(1)
        p.page.keyboard.up("ArrowRight")
        assert p.js("[window.sf.clock.now, window.sf.state.x]") == before
        p.press("Escape")  # Escape on pause resumes
        assert p.menu() is None and p.js("window.sf.clock.now") > before[0]
        # Idle until losing: game over, then Retry restarts the level.
        p.run(60)
        assert p.menu() == "game_over"
        p.press("Enter")
        p.run(0.5)
        events = p.events()
        assert events.index("lose") < events.index("level_failed") < events.index("restart")
        assert ["pause", "resume"] == [e for e in events if e in ("pause", "resume")]
        assert p.js("window.sf.errors") == []
        # Quit to title from the pause menu: arrows move the highlight, the title comes back.
        p.press("Escape")
        for _ in range(3):
            p.press("ArrowDown")
        assert p.js("window.sf.menu.options[window.sf.menu.index]") == "Quit to title"
        p.press("Enter")
        assert p.menu() == "title"


def test_settings_and_progress_survive_a_reload_and_reset_clears_them(tmp_path, browser):
    build, _, _ = arena_build(tmp_path)
    with serve(build) as url:
        p = Page(browser, url, "headless=1&reset=1")
        p.press("Enter")
        p.press("ArrowDown")  # Settings
        p.press("Enter")
        assert p.menu() == "settings"
        p.press("Enter")  # Difficulty: normal -> hard
        p.press("ArrowDown")
        p.press("Enter")  # Fewer flashes: on
        assert p.js("window.sf.settings") == {"difficulty": "hard", "multiplier": 1.35, "fewerFlashes": True}
        p.press("Escape")
        assert p.menu() == "main"
        p.goto("headless=1")
        assert p.js("window.sf.settings.difficulty") == "hard" and p.js("window.sf.state.difficulty") == "hard"
        p.goto("headless=1&reset=1")
        assert p.js("window.sf.settings.difficulty") == "normal"


def test_a_win_opens_victory_and_play_again_restarts(tmp_path, browser):
    build, _, _ = arena_build(tmp_path)
    with serve(build) as url:
        p = Page(browser, url, "headless=1&play=1&god=1&reset=1")
        assert p.menu() is None
        p.run(47)
        counts = p.js("window.sf.counts")  # (sf.events keeps the first 400 only)
        assert {"win", "level_complete", "game_complete"} <= set(counts) and p.menu() == "victory"
        assert p.js("window.sf.progress.completed") is True and p.js("window.sf.paused") is True
        p.press("Enter")  # Play again
        p.run(0.5)
        assert "restart" in p.js("window.sf.counts") and p.js("window.sf.state.time") < 2


def test_the_bot_skips_the_title_and_plays_as_before(tmp_path):
    build, design, _ = arena_build(tmp_path)
    report = playtest(build, design.checks, design.session_seconds)
    assert report["passed"], report
    assert "title" not in report["events"] and report["events"]["level_start"] == 1


def test_every_screen_is_shot_and_labels_fit(tmp_path):
    build, _, script = arena_build(tmp_path)
    shots, errors = menu_shots(build, script)
    assert errors == [] and set(shots) == set(script["screens"])
    assert all(Image.open(io.BytesIO(png)).size == (320, 180) for png in shots.values())
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        p = Page(browser, url, "headless=1&reset=1")
        for sid in script["screens"]:
            p.js(f"window.sf.flow.preview({json.dumps(sid)})")
            panel = p.js("window.sf.flow.panel")
            widths = p.js("window.sf.flow && sf.game.scene.getScene('sf-menu').parts"
                          ".filter(o => o.type === 'Text').map(o => [o.x - o.displayOriginX, o.width])")  # fmt: skip
            inside = [x >= panel["x"] - 1 and x + w <= panel["x"] + panel["w"] + 1 for x, w in widths
                      if x >= panel["x"] - 1]  # fmt: skip
            assert all(inside), (sid, widths, panel)


def test_qa_steps_for_menus_pass_and_fail_as_they_should(tmp_path):
    build, design, script = arena_build(tmp_path)
    tests = [
        QATest(name="menus by label and action", god=True, start="title", purpose="p",
               steps=[s(do="expect_menu", menu="title"), s(do="press", buttons=["enter"]),
                      s(do="select", menu="Settings"), s(do="expect_menu", menu="settings"),
                      s(do="select", menu="back"), s(do="select", menu="start"),
                      s(do="expect_event", event="level_start", min_count=1, within_seconds=1)]),
        QATest(name="a missing item", god=True, start="title", purpose="p",
               steps=[s(do="press", buttons=["enter"]), s(do="select", menu="Shop")]),
        QATest(name="the wrong screen", god=True, start="play", purpose="p",
               steps=[s(do="expect_menu", menu="pause")]),
        QATest(name="a reload keeps progress", god=True, start="title", purpose="p",
               steps=[s(do="press", buttons=["enter"]), s(do="select", menu="settings"),
                      s(do="select", menu="difficulty"), s(do="reload"),
                      s(do="expect_menu", menu="title"),
                      s(do="expect_state", path="difficulty", op="==", value="hard")]),
    ]  # fmt: skip
    results = {r["name"]: r for r in run_qa(build, tests)}
    assert results["menus by label and action"]["passed"], results["menus by label and action"]
    assert results["a missing item"]["failure"].startswith("the main menu has no item 'Shop' (items: Start")
    assert results["the wrong screen"]["failure"].startswith("the pause menu did not open within 3 s")
    assert results["a reload keeps progress"]["passed"], results["a reload keeps progress"]


def test_old_qa_tests_still_load_and_start_in_play():
    old = {"name": "t", "purpose": "p", "god": True,
           "steps": [{"do": "wait", "buttons": [], "seconds": 1, "index": 0, "event": "", "min_count": 0,
                      "within_seconds": 0, "path": "", "op": "", "value": "", "label": ""}]}  # fmt: skip
    test = QATest.model_validate(old)
    assert test.start == "play" and test.steps[0].menu == ""


# --- levels: results between levels, continue and level select -------------------------------------


@pytest.fixture
def models(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    return Models(cfg, Budget(store, run.id, cfg.budget), True)


def test_levels_show_results_unlock_and_continue(tmp_path, models):
    design, plan = slime_design(), slime_plan()
    first = check(GATED, n=1, events=["crate_open", "level_complete"])
    records = [first, check(GATED, n=2, previous=first, events=["crate_open", "level_complete"])]
    art = produce_art(models, design, keyword_plan(design), models.cfg)
    manifest = art.manifest()
    structure = make_structure(design, plan, True)
    structure = structure.model_copy(update={"levels": structure.levels[:2]})
    spec, _ = fit_menu(fallback_menu(design, structure), structure, design)
    build = build_game(tmp_path / "build", title="t", controls="c",
                       files={"game.js": MOCK_PLATFORMER.read_text(encoding="utf-8")}, assets=manifest,
                       images=art.files(), credits=[],
                       levels=levels_script(records, design, plan, player_body(design, manifest)),
                       menus=menus_script(spec, structure, design))  # fmt: skip
    # The playtest plays one level at a time, unchanged by the flow.
    report = playtest_levels(build, [], levels_of(records))
    assert all(lv["cleared"] for lv in report["levels"]), report["levels"]
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        # The bot plays level 1, the results open, it picks Next level itself.
        p = Page(browser, url, "autoplay=1&headless=1&god=1&reset=1")
        for _ in range(60):
            p.run(1)
            if "level_start" in p.events() and p.js("window.sf.flow.current") == 2:
                break
        events = p.events()
        assert events.count("level_complete") >= 1 and "menu_open" in events
        assert p.js("window.sf.progress.unlocked") == 2
        # A human now sees Continue and level 2 unlocked in Level select (the same browser).
        h = p.goto("headless=1")
        h.press("Enter")
        assert h.js("window.sf.menu.options")[:3] == ["Start", "Continue", "Level select"]
        h.press("ArrowDown")
        h.press("ArrowDown")
        h.press("Enter")
        assert h.menu() == "level_select"
        assert h.js("window.sf.menu.options")[:2] == [f"{lv.n}. {lv.name}" for lv in structure.levels]
        h.press("2")
        h.run(0.5)
        assert h.js("window.sf.level.n") == 2 and h.js("window.sf.state.level") == 2


# --- G3 in a gated run ------------------------------------------------------------------------------


def submit(store, ident, text="", **kwargs):
    ScriptedHuman([Feedback(action="submit", text=text, **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)


def answer(store, ident, **kwargs):
    ScriptedHuman([Feedback(action="approve", **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)


def read(store, ident, name):
    return json.loads(next(a for a in store.artifacts(ident) if a.name == name).data)


def shown(store, ident, gate):
    names = {a.id: a.name for a in store.artifacts(ident)}
    return [names[i] for i in gate.artifacts_to_show]


def test_g3_shows_the_menus_and_feedback_rebuilds_with_the_same_code(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.workspace = tmp_path
    ident = start_run(store, cfg, mock=True, goal="survive a slime arena, collect gems").id
    workflow_step(store, ident)
    answer(store, ident, form={"genre": "genres/top-down-dungeon-crawler"})
    answer(store, ident, form={"environment": "A slime cave"})
    answer(store, ident)
    gate = Gates(store).pending(ident)
    assert gate.stage == "design" and "game/design/v1-structure.json" in shown(store, ident, gate)
    submit(store, ident)  # approve the design: menus v1, QA with menus, build v1, then G3
    gate = Gates(store).pending(ident)
    assert gate.stage == "menus" and gate.title.startswith("G3 Menus v1 (build v1")
    names = shown(store, ident, gate)
    assert names[:3] == ["game/v1/menus.png", "game/v1/menus/title-art.png", "game/v1/menus/emblem.png"]
    assert "game/menus/v1.json" in names
    report = read(store, ident, "game/v1/report.json")
    assert report["menus_version"] == 1 and report["menus"]["errors"] == []
    assert "game/v1/menus/pause.png" in report["menus"]["screens"]
    qa = read(store, ident, "game/v1/qa.json")
    assert {"title to play, pause and resume", "difficulty setting"} <= {t["name"] for t in qa["tests"]}
    assert all(t["passed"] for t in qa["tests"]), [t for t in qa["tests"] if not t["passed"]]

    # Menu feedback: the Menu Designer revises the menus; same code, same art, G3 again.
    submit(store, ident, "make the menus red")
    gate = Gates(store).pending(ident)
    assert gate.stage == "menus" and gate.title.startswith("G3 Menus v2 (build v2")
    v1, v2 = read(store, ident, "game/menus/v1.json"), read(store, ident, "game/menus/v2.json")
    assert v2["spec"]["theme"]["highlight"] != v1["spec"]["theme"]["highlight"]
    assert read(store, ident, "game/v2/code.json") == read(store, ident, "game/v1/code.json")
    report2 = read(store, ident, "game/v2/report.json")
    assert report2["menus_version"] == 2 and report2["revision"]["menu_changes"] == "make the menus red"
    assert {a["source"] for a in report2["art"]} <= {"muse (locked, from v1)"}

    # Approve the menus: G4 for the same build, the menus human-reviewed in the record.
    submit(store, ident)
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and gate.title.startswith("G4 Playtest v2")
    assert "game/v2/menus.png" in shown(store, ident, gate)
    db = open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)
    entries = {e.id: e for e in db.scan()[0]}
    assert entries["menus/overview"].metadata.trust_tier == "human-reviewed"
    assert entries["design/structure"].metadata.trust_tier == "human-reviewed"
    assert entries["builds/v2"].metadata.trust_tier != "human-reviewed"  # not playtested yet
    assert (db.root / "references" / "menus" / "pause.png").is_file() and (db.root / "references" / "menus.json").is_file()
    decisions = [e for i, e in entries.items() if i.startswith("decisions/") and e.metadata.type == "Decision"]
    assert any("G3 menus" in d.title for d in decisions)
    assert conformance(db) == [] and validate(db, external=False).errors == []

    # G4 feedback about the menus goes to the Menu Designer, not the Programmer.
    submit(store, ident, "the title screen should be blue")
    report3 = read(store, ident, "game/v3/report.json")
    assert report3["revision"]["menu_changes"] and report3["menus_version"] == 3
    assert read(store, ident, "game/v3/code.json") == read(store, ident, "game/v1/code.json")
    assert Gates(store).pending(ident).stage == "playtest"  # G3 is passed once
