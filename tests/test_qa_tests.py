"""M10.4: QA tests written from the design, played with real key presses, exported."""

import ast
import json
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan, make_art
from spriteforge.game.build import build_game
from spriteforge.game.codegen import CodePatch, mock_code
from spriteforge.game.design import fallback_design
from spriteforge.game.pipeline import make_game
from spriteforge.game.qa import (
    QASuite,
    QATest,
    QATests,
    blank_step,
    export_tests,
    fallback_qa,
    merge_suite,
    qa_failures,
    run_qa,
)
from spriteforge.game.revision import dropped_tests
from spriteforge.providers.mock import MockLLMProvider

s = blank_step
MENU_GAME = """
class Menu extends Phaser.Scene {
  constructor() { super('menu'); }
  create() {
    const [w, h] = sf.screen();
    this.hero = sf.sprite(this, w / 2, h / 2, 'hero');
    this.at = 1; this.paused = false; this.picked = -1;
  }
  update() {
    if (this.paused) return;
    const inp = sf.input(this);
    this.hero.setVelocity(inp.x * 60, inp.y * 60);
    if (this.at && sf.clock.now >= this.at) {
      this.at = 0; this.paused = true; this.physics.world.pause();
      sf.choose(this, 'LEVEL UP', ['Damage', 'Speed', 'HP'], i => {
        this.picked = i; this.paused = false; this.physics.world.resume();
      });
    }
    sf.state = {hp: 3, x: Math.round(this.hero.x), y: Math.round(this.hero.y),
                time: sf.clock.now, picked: this.picked};
  }
}
sf.start({scenes: [Menu], autoplay: () => ({})});
"""


def build(tmp_path: Path, code: str | None = None) -> Path:
    design = fallback_design("arena")
    art = make_art(design, keyword_plan(design), generate=None)
    files = {"game.js": code} if code else mock_code().as_dict()
    return build_game(tmp_path / "build", title="t", controls="-", files=files,
                      assets=art.manifest(), images=art.files(), credits=[])  # fmt: skip


def qa_test(name, *steps, god=True) -> QATest:
    return QATest(name=name, purpose="p", god=god, steps=list(steps))


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t", "budget": load_config().budget.model_dump()})
    return Budget(store, run.id, load_config().budget)


def test_schema_rejects_unknown_step_key_and_limits():
    ok = {"buttons": [], "seconds": 0, "index": 0, "event": "", "min_count": 0,
          "within_seconds": 0, "path": "", "op": "", "value": "", "label": ""}  # fmt: skip
    step = lambda **kw: {**ok, **kw}
    one = lambda *steps: {
        "tests": [{"name": "t", "purpose": "p", "god": True, "steps": list(steps)}]
    }
    QATests.model_validate(one(step(do="hold", buttons=["right", "space"], seconds=2)))
    for bad in (
        one(step(do="teleport")),  # an unknown step type
        one(step(do="press", buttons=["f5"])),  # a key outside the whitelist
        one(step(do="hold", buttons=["q"], seconds=1)),
        one(step(do="wait", seconds=200)),  # 200 s
        one(*[step(do="wait", seconds=1)] * 50),  # 50 steps
        one(*[step(do="wait", seconds=60)] * 3),  # 180 game seconds in one test
        one(step(do="expect_event", event="Bad Name!", min_count=1)),
        one(step(do="expect_state", path="__proto__.x;", op="==", value="1")),
        {
            "tests": [
                {
                    "name": f"t{n}",
                    "purpose": "p",
                    "god": True,
                    "steps": [step(do="expect_no_errors")],
                }
                for n in range(13)
            ]
        },
    ):
        with pytest.raises(ValidationError):
            QATests.model_validate(bad)
    # Fields a step does not use are cleared.
    cleaned = QATests.model_validate(one(step(do="wait", seconds=2, buttons=["up"], label="x")))
    assert cleaned.tests[0].steps[0].buttons == [] and cleaned.tests[0].steps[0].label == ""


def test_runner_passes_and_fails_on_mock_game(tmp_path):
    site = build(tmp_path)
    passing = qa_test("moves and spawns", s(do="hold", buttons=["right"], seconds=2),
                   s(do="expect_no_errors"), s(do="expect_state", path="x", op=">", value="@start"),
                   s(do="expect_event", event="enemy_spawn", min_count=1, within_seconds=0))  # fmt: skip
    failing = qa_test("never", s(do="wait", seconds=1), s(do="expect_no_errors"),
                   s(do="expect_event", event="never_emitted", min_count=1, within_seconds=2),
                   s(do="screenshot", label="unreached"))  # fmt: skip
    good, bad = run_qa(site, [passing, failing])
    assert good["passed"] and good["failed_step"] is None and good["state"]["x"] > 160
    assert not bad["passed"] and bad["failed_step"] == 3
    assert "event 'never_emitted' happened 0 times in 2 s" in bad["failure"]
    assert [shot["label"] for shot in bad["screenshots"]] == ["failed at step 3"]
    assert bad["screenshots"][0]["png"][:4] == b"\x89PNG"
    assert [st["ok"] for st in bad["steps"]] == [True, True, False]
    text = qa_failures([good, bad])
    assert "QA test 'never' (p) failed at step 3 of 3 (expect event never_emitted >= 1" in text


def test_lose_path_without_god(tmp_path):
    site = build(tmp_path)
    lose = s(do="expect_event", event="lose", min_count=1, within_seconds=60)
    idle, unkillable = run_qa(site, [qa_test("lose idle", lose, god=False), qa_test("god", lose)])
    assert idle["passed"] and idle["events"]["lose"] == 1 and idle["game_seconds"] < 60
    assert not unkillable["passed"]  # god mode: the player never dies


def test_menu_choice_and_restart_with_real_keys(tmp_path):
    menu = build(tmp_path / "menu", MENU_GAME)
    pick = qa_test("level-up menu", s(do="expect_event", event="menu", min_count=1, within_seconds=3),
                s(do="choose", index=1), s(do="expect_event", event="choice", min_count=1),
                s(do="expect_state", path="picked", op="==", value="1"))  # fmt: skip
    [picked] = run_qa(menu, [pick])
    assert picked["passed"], picked["failure"]
    mock = build(tmp_path / "mock")  # it has no menu
    [missing] = run_qa(mock, [qa_test("no menu", s(do="choose", index=0))])
    assert not missing["passed"] and missing["failure"].startswith("no menu (sf.choose) opened")
    # The offline tests (lose, restart with Enter, HUD clock, ...) all pass on the mock game.
    results = run_qa(mock, fallback_qa(fallback_design("arena")).tests)
    assert all(r["passed"] for r in results), [(r["name"], r["failure"]) for r in results]
    assert {"loses when idle", "restarts after losing"} <= {r["name"] for r in results}


def test_export_compiles_and_escapes(tmp_path):
    payload = '"; import os; os.system("x") #'
    tests = [qa_test(payload, s(do="hold", buttons=["right"], seconds=1), s(do="expect_no_errors")),
             qa_test("lose idle", s(do="expect_event", event="lose", min_count=1, within_seconds=60),
                  god=False)]  # fmt: skip
    site = build(tmp_path)
    path = site / "tests" / "test_game_qa.py"
    path.parent.mkdir()
    path.write_text(export_tests(tests), encoding="utf-8")
    py_compile.compile(str(path), doraise=True)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    holders = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "os.system" in n.value]  # fmt: skip
    assert len(holders) == 1 and payload in json.loads(holders[0].value)["tests"][0]["name"]
    assert not any(isinstance(n, ast.Import) and any(a.name == "os" for a in n.names)
                   for n in ast.walk(tree))  # fmt: skip
    run = subprocess.run([sys.executable, "-m", "pytest", str(path.parent), "-q",
                          "-p", "no:cacheprovider", "--rootdir", str(path.parent)],
                         capture_output=True, text=True, timeout=300, check=False)  # fmt: skip
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    assert "2 passed" in run.stdout


def test_failures_reach_repair_prompt(tmp_path, budget):
    design = fallback_design("arena")
    qa = QATests(tests=[qa_test("never happens", s(do="expect_event", event="never_emitted",
                                                min_count=1, within_seconds=1))])  # fmt: skip
    scripted = MockLLMProvider(budget, [
        design.model_dump(), qa.model_dump(), {"variant": "normal", "picks": []},
        mock_code().model_dump(), CodePatch(edits=[], notes="").model_dump(),
    ])  # fmt: skip
    seen, chat = [], scripted.chat
    scripted.chat = lambda request: (seen.append(request), chat(request))[1]
    cfg = load_config()
    cfg.game.review = cfg.game.motion = cfg.game.menus = cfg.game.sounds = False
    result = make_game("arena", tmp_path / "g", cfg, mock=True, provider=scripted, record=False,
                       log=lambda m: None)  # fmt: skip
    titles = [r.json_schema["title"] for r in seen]
    assert titles == ["GameDesign", "QATests", "ArtPlan", "GameCode", "CodePatch"]
    repair = seen[-1].messages[-1].content
    assert "QA test 'never happens'" in repair and "failed at step 1 of 1" in repair
    assert "press real keys" in repair
    attempt = result.report["attempts"][0]
    assert attempt["stage"] == "qa" and attempt["qa"] == {"passed": 0, "failed": 1}
    assert result.report["passed"] is True  # the bot playtest passed; QA is reported apart
    assert result.report["qa"]["failed"] == 1 and (tmp_path / "g" / "qa.json").is_file()
    assert (tmp_path / "g" / "build" / "tests" / "test_game_qa.py").is_file()


def test_dropped_tests_and_suite_merge():
    names = ["moves with the arrow keys", "restarts after losing", "loses when idle"]
    assert dropped_tests("ignore the restart test", names) == ["restarts after losing"]
    assert dropped_tests("remove the idle test and the restart test", names) == [
        "loses when idle", "restarts after losing"]  # fmt: skip
    assert dropped_tests("the restart test is fine, drop the idle test", names) == [
        "loses when idle"
    ]
    assert dropped_tests("make the slime red", names) == []
    v1 = QATests(tests=[qa_test(n, s(do="expect_no_errors")) for n in names])
    suite1 = merge_suite(None, v1.tests, 1)
    assert suite1.versions == [1, 1, 1]
    suite2 = merge_suite(suite1, None, 2, ["Restarts after losing"])
    assert [t.name for t in suite2.tests] == [names[0], names[2]]
    assert suite2.dropped == ["Restarts after losing"] and suite2.since(names[0]) == 1
    new = QATests(tests=[qa_test("loses when idle", s(do="wait", seconds=1)),
                         qa_test("menu works", s(do="expect_no_errors"))])  # fmt: skip
    suite3 = merge_suite(suite2, new.tests, 3)
    assert [(t.name, v) for t, v in zip(suite3.tests, suite3.versions, strict=True)] == [
        (names[0], 1), ("loses when idle", 3), ("menu works", 3)]  # fmt: skip
    assert QASuite.model_validate(suite3.model_dump()) == suite3
