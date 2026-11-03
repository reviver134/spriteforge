"""L2.8: the QA Playtester's code checks: written per game, checked before they run, run inside
the game's page, a broken check fixed once and never blamed on the game, failures sent to the
Programmer, exported with the QA tests."""

import shutil

import pytest
from playwright.sync_api import sync_playwright
from test_game_flow import arena_build
from test_levels import GATED, built, check, slime_design, slime_plan

from spriteforge.config import load_config
from spriteforge.game import qa_runtime
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.codegen import mock_code
from spriteforge.game.design import fallback_design
from spriteforge.game.pipeline import CheckWriter, build_and_test
from spriteforge.game.qa import export_tests
from spriteforge.game.qa_checks import CodeCheck, CodeChecks, check_problems, fallback_checks, usable
from spriteforge.game.qa_runtime import run_check, serve


def code_check(name, code, god=True):
    return CodeCheck(name=name, purpose=f"{name} (test)", god=god, start="play", level=1, code=code)


def run(build, checks, wall_ms=None, monkeypatch=None):
    if wall_ms and monkeypatch:
        monkeypatch.setattr(qa_runtime, "CHECK_WALL_MS", wall_ms)
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            return {c.name: run_check(browser.new_page(), url, c.model_dump(), 30) for c in checks}
        finally:
            browser.close()


# --- before a check runs ----------------------------------------------------------------------


def test_check_code_is_checked_before_it_runs():
    bad = {
        "network": "fetch('x'); qa.step(1);",
        "fakes an outcome": "const e = qa.level().list[0]; sf.level.open(e);",
        "fakes an event": "sf.event('win');",
        "timers": "setTimeout(() => {}, 10);",
        "storage": "localStorage.setItem('a', 1);",
        "changes the SDK": "sf.state = {};",
    }
    for why, code in bad.items():
        assert check_problems(code_check(why, code)), why
    good = code_check("fine", "const s = qa.scene();\nqa.hold('right', 1);\nqa.expect(s, 'no scene');")
    assert check_problems(good) == []
    if shutil.which("node"):  # node --check parses the check as the body of a function
        assert any("syntax" in p.lower() for p in check_problems(code_check("typo", "qa.step(1;")))
    kept, notes = usable(CodeChecks(checks=[good, code_check("net", bad["network"])]))
    assert [c.name for c in kept] == ["fine"] and notes == ["check 'net' uses network calls"]
    assert len(fallback_checks(fallback_design("an arena")).checks) == 2


# --- running in the game -------------------------------------------------------------------------


def test_a_check_passes_fails_breaks_or_runs_out_of_time(tmp_path, monkeypatch):
    build, _, _ = arena_build(tmp_path)
    results = run(build, [
        code_check("moves", "const x0 = qa.state().x;\nqa.hold('right', 1);\n"
                            "qa.expect(qa.state().x > x0, 'x stayed ' + x0);"),
        code_check("fails", "qa.step(0.5);\nqa.expect(false, 'the hero has no hat');"),
        code_check("broken", "qa.step(0.2);\nnoSuchThing.go();"),
        code_check("forever", "while (true) {}"),
        code_check("too long", "qa.step(200);"),
    ], wall_ms=4000, monkeypatch=monkeypatch)  # fmt: skip
    assert results["moves"]["passed"] and results["moves"]["kind"] == "check"
    fails = results["fails"]
    assert not fails["passed"] and not fails["broken"] and fails["failure"] == "the hero has no hat"
    assert results["broken"]["broken"] and "noSuchThing is not defined" in results["broken"]["failure"]
    assert results["forever"]["broken"]  # stopped by the browser, the QA Playtester's fault
    assert not results["too long"]["passed"] and "ran past" in results["too long"]["failure"]
    assert all(r["screenshots"] for r in results.values() if not r["broken"])


CHEAT = """
class Level extends Phaser.Scene {
  constructor() { super('level'); }
  create() {
    this.L = sf.level.build(this, {touch: (t, how) => {
      if (t.kind === 'pickup') sf.level.take(t); else if (t.kind !== 'enemy') sf.level.open(t);
    }});
    this.fired = false;
  }
  update() {
    const inp = sf.input(this);
    if (CHEATS && inp.action2 && !this.fired) {  // "fireAssist": the nearest enemy dies, hit or not
      this.fired = true;
      const e = this.L.list.find(t => t.kind === 'enemy' && !t.open);
      if (e) sf.level.open(e);
    }
  }
}
sf.start({scenes: [Level]});
"""
# What a QA Playtester writes for a game whose design says enemies are beaten up close.
FAR_AWAY = code_check("enemies are not beaten from afar", """
const L = qa.level(), T = L.tile;
const e = L.list.find(t => t.kind === 'enemy' && !t.open);
qa.expect(e, 'no enemy in the level');
const tiles = Math.abs(e.sprite.x - L.player.x) / T;
qa.expect(tiles > 6, 'the player starts next to the enemy');
qa.hold('x', 0.5);
qa.step(0.5);
qa.expect(!e.open, 'enemy ' + e.entity + ' was beaten from ' + Math.round(tiles) + ' tiles away, untouched');
""")


@pytest.mark.parametrize("cheats", [True, False])
def test_a_check_catches_an_enemy_beaten_from_afar(tmp_path, cheats):
    design, plan = slime_design(), slime_plan()
    records = [check(GATED, n=1, events=["level_complete"])]
    build = built(tmp_path, design, plan, records, code=CHEAT.replace("CHEATS", str(cheats).lower()))
    result = run(build, [FAR_AWAY])[FAR_AWAY.name]
    assert result["passed"] is not cheats and not result["broken"], result["failure"]
    if cheats:
        assert "was beaten from" in result["failure"] and "tiles away, untouched" in result["failure"]


# --- in the build loop -----------------------------------------------------------------------------


class Scripted:
    """Models for build_and_test: the QA Playtester's answers in order; prompts kept."""

    name = "scripted"

    def __init__(self, answers):
        self.answers, self.prompts = list(answers), []

    def ask(self, role, prompt, schema, fallback, task=None, images=()):
        assert role == "QA Playtester" and schema is CodeChecks
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_checks_are_written_from_the_code_fixed_once_and_failures_reach_the_programmer(tmp_path):
    design = fallback_design("survive the arena")
    art = make_art(design, keyword_plan(design), generate=None)
    passing = code_check("moves right", "const x0 = qa.state().x;\nqa.hold('right', 1);\n"
                                        "qa.expect(qa.state().x > x0, 'x stayed');")
    broken = code_check("reads the score", "qa.step(0.2);\nqa.expect(scene.score >= 0, 'no score');")
    fixed = code_check("reads the score", "qa.step(0.2);\nqa.expect(qa.state().time > 0, 'time stands still');")
    failing = code_check("never loses hp", "qa.step(1);\nqa.expect(qa.state().hp > 999, 'hp is ' + qa.state().hp);")
    forbidden = code_check("cheats", "sf.event('win');")
    models = Scripted([CodeChecks(checks=[passing, broken, failing, forbidden]),
                       CodeChecks(checks=[passing, fixed, failing])])  # fmt: skip
    cfg = load_config()
    cfg.game.max_repairs = 0
    writer = CheckWriter(models, design)
    outcome = build_and_test(models, design, art, mock_code(), cfg, tmp_path, lambda m: None,
                             None, checks=writer)  # fmt: skip
    assert "The game's code:" in models.prompts[0] and "game.js" in models.prompts[0]
    assert "failed by themselves" in models.prompts[1] and "scene is not defined" in models.prompts[1]
    assert [c.name for c in outcome.checks] == ["moves right", "reads the score", "never loses hp"]
    assert any("check 'cheats' uses calling the SDK" in n for n in outcome.check_notes)
    results = {r["name"]: r for r in outcome.qa}
    assert results["moves right"]["passed"] and results["reads the score"]["passed"]
    assert not results["never loses hp"]["passed"] and not results["never loses hp"]["broken"]
    summary = outcome.attempts[0]["summary"]
    assert "QA code checks failed" in summary and "check 'never loses hp'" in summary
    assert outcome.report["qa"] == {**outcome.report["qa"], "passed": 2, "failed": 1, "broken": 0}


def test_the_export_runs_checks_in_the_page_and_keeps_them_data_in_python():
    text = export_tests([FAR_AWAY])
    compile(text, "test_game_qa.py", "exec")
    assert "def run_check" in text and "run = run_check if \"code\" in qa else run_test" in text
    assert "sf.level.open" not in text.split("DATA_JSON = ")[0]  # the check is only in the data


def test_a_failed_repair_call_keeps_the_best_attempt(tmp_path, monkeypatch):
    """Live 2026-11-03: a repair call timed out and the whole build was lost, with a good first
    attempt. Now the repairs end and the best attempt so far is kept."""
    from spriteforge.game import pipeline
    from spriteforge.providers.http import ProviderError

    def timeout(*args, **kwargs):
        raise ProviderError("The read operation timed out")

    monkeypatch.setattr(pipeline, "edit_code", timeout)
    design = fallback_design("survive the arena")
    art = make_art(design, keyword_plan(design), generate=None)
    failing = code_check("never loses hp", "qa.step(1);\nqa.expect(qa.state().hp > 999, 'hp is ' + qa.state().hp);")
    models = Scripted([CodeChecks(checks=[failing])])
    cfg = load_config()
    cfg.game.max_repairs = 2
    outcome = build_and_test(models, design, art, mock_code(), cfg, tmp_path, lambda m: None, None,
                             checks=CheckWriter(models, design))  # fmt: skip
    assert len(outcome.attempts) == 1 and outcome.attempts[0]["kept"]
    assert outcome.attempts[0]["repair"] == "failed: The read operation timed out"
    assert outcome.build is not None and (outcome.build / "index.html").is_file()
