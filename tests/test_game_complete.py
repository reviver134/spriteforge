"""L2.6: the whole game: a three-level game with progress, level select and victory; every level
tested with real keys on every build; G5 reviews the whole game, promotes its record, writes
lessons to the shared memory as generated knowledge and exports the game, its record and its
tests; promoted lessons reach the next game's plan."""

import io
import json
import zipfile

import pytest
from playwright.sync_api import sync_playwright
from test_game_db import conformance
from test_game_flow import Page
from test_levels import GATED, WORDS, answer, check, levels_of, read, slime_design, slime_plan, submit

from spriteforge.config import load_config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan
from spriteforge.game.build import build_game
from spriteforge.game.codegen import MOCK_PLATFORMER
from spriteforge.game.export import export_zip
from spriteforge.game.gamedb import open_db
from spriteforge.game.lessons import (
    ACTOR,
    LessonRecord,
    fallback_lessons,
    lesson_entries,
    past_lessons,
    run_facts,
)
from spriteforge.game.levels import levels_script, player_body
from spriteforge.game.menus import fallback_menu, fit_menu, menus_script
from spriteforge.game.pipeline import Models, produce_art
from spriteforge.game.playtest import playtest_levels
from spriteforge.game.plan import plan_prompt
from spriteforge.game.qa import QATest, level_tests, merge_suite, run_qa, with_level_tests
from spriteforge.game.record import serve
from spriteforge.game.structure import make_structure
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.memory.okf.validate import validate
from spriteforge.game.bundle import Decision
from spriteforge.stages.workflow import start_run, workflow_step

# --- a three-level game in the browser ----------------------------------------------------------


@pytest.fixture
def models(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump(), "mock": True})
    return Models(cfg, Budget(store, run.id, cfg.budget), True)


def three_levels(tmp_path, models):
    design, plan = slime_design(), slime_plan()
    records = []
    for n in (1, 2, 3):
        records.append(check(GATED, n=n, previous=records[-1] if records else None,
                             events=["crate_open", "level_complete"]))  # fmt: skip
    art = produce_art(models, design, keyword_plan(design), models.cfg)
    manifest = art.manifest()
    structure = make_structure(design, plan, True)
    spec, _ = fit_menu(fallback_menu(design, structure), structure, design)
    script = menus_script(spec, structure, design)
    build = build_game(tmp_path / "build", title="t", controls="c",
                       files={"game.js": MOCK_PLATFORMER.read_text(encoding="utf-8")}, assets=manifest,
                       images=art.files(), credits=[],
                       levels=levels_script(records, design, plan, player_body(design, manifest)),
                       menus=script)  # fmt: skip
    return build, records, script


def test_three_levels_play_through_to_victory_with_progress_and_level_select(tmp_path, models):
    build, records, script = three_levels(tmp_path, models)
    assert [lv["n"] for lv in script["levels"]] == [1, 2, 3]
    # Regression: every level played on its own by the bot.
    report = playtest_levels(build, [], levels_of(records))
    assert [lv["cleared"] for lv in report["levels"]] == [True, True, True], report["levels"]
    # Every level tested with real keys, each test starting in its level.
    tests = level_tests(script)
    assert [(t.name, t.level) for t in tests] == [(f"level {n} starts and plays", n) for n in (1, 2, 3)]
    results = run_qa(build, tests)
    assert all(r["passed"] for r in results), [(r["name"], r["failure"]) for r in results]
    assert [r["state"].get("level") for r in results] == [1, 2, 3]
    with serve(build) as url, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            # The bot plays the whole game in one page: results between levels, then victory.
            p = Page(browser, url, "autoplay=1&headless=1&god=1&reset=1")
            for _ in range(240):
                p.run(1)
                if "game_complete" in p.events():
                    break
            p.run(1.2)
            events = p.events()
            assert events.count("level_complete") == 3 and events.count("game_complete") == 1
            assert p.menu() == "victory"
            assert p.js("window.sf.progress.unlocked") == 3
            # A human then picks level 3 from Level select (it is unlocked).
            h = p.goto("headless=1")
            h.press("Enter")
            assert h.js("window.sf.menu.options")[:3] == ["Start", "Continue", "Level select"]
            h.press("ArrowDown")
            h.press("ArrowDown")
            h.press("Enter")
            assert h.menu() == "level_select"
            h.press("3")
            h.run(0.5)
            assert h.js("window.sf.level.n") == 3 and h.js("window.sf.state.level") == 3
        finally:
            browser.close()


def test_level_tests_join_the_suite_and_old_tests_start_in_level_one():
    script = {"levels": [{"n": 1, "name": "A", "goal": ""}, {"n": 2, "name": "B", "goal": ""}]}
    assert level_tests({"levels": script["levels"][:1]}) == []  # one level: nothing to add
    old = QATest.model_validate({"name": "t", "purpose": "p", "god": True, "steps": [
        {"do": "expect_no_errors", "buttons": [], "seconds": 0, "index": 0, "event": "",
         "min_count": 0, "within_seconds": 0, "path": "", "op": "", "value": "", "label": ""}]})
    assert (old.start, old.level) == ("play", 1)
    mine = old.model_copy(update={"name": "level 2 starts and plays"})  # the designer's own wins
    tests = with_level_tests([old, mine], script)
    assert [t.name for t in tests] == ["t", "level 2 starts and plays", "level 1 starts and plays"]
    suite = merge_suite(None, tests, 1)
    assert len(suite.tests) == 3 and suite.versions == [1, 1, 1]
    title = QATest.model_validate({**old.model_dump(), "start": "title", "level": 3})
    assert title.level == 1  # a test from the title starts where the human picks
    with pytest.raises(ValueError):
        QATest.model_validate({**old.model_dump(), "level": 9})


# --- lessons and the export ---------------------------------------------------------------------


def report(v, **kw):
    return {"version": v, "title": "Slime", "passed": True, "attempts": [{"attempt": 1, "passed": True}],
            "checks": [], "levels": [], "art": [], "qa": None, **kw}  # fmt: skip


def test_facts_and_offline_lessons_come_from_what_happened():
    reports = [
        report(1, attempts=[{"attempt": 1, "passed": False, "stage": "playtest",
                             "summary": "never emitted wall_break"}, {"attempt": 2, "passed": True}],
               levels=[{"n": 3, "cleared": False, "ended": "stuck", "problems": ["gap too wide"]}],
               qa={"tests": [{"name": "level 3 starts and plays", "passed": False,
                              "failure": "x stayed at 24"}]}),
        report(2, art_review={"issues": [{"entity": "acid", "severity": "blocking",
                                          "problem": "reads as a coin", "status": "fixed"}]}),
    ]  # fmt: skip
    decisions = [Decision("G1 brief", "approved", "", "genre"),
                 Decision("G4 playtest v1", "changes requested", "the acid looks like a coin",
                          "art redrawn: acid", 2, True)]  # fmt: skip
    facts = run_facts(reports, decisions, "genres/side-scrolling-platformer",
                      {"chat": 9, "image": 4, "estimated_cost_usd": 0.4})  # fmt: skip
    text = "\n".join(facts)
    assert "build v1: 2 attempts; failed at playtest" in text
    assert "the bot did not clear level 3 (stuck)" in text and "QA test 'level 3 starts and plays'" in text
    assert "art review (blocking) acid: reads as a coin" in text
    assert 'human at G4 playtest v1 (changes requested): "the acid looks like a coin"' in text
    lessons = fallback_lessons(facts).lessons
    assert {lesson.topic for lesson in lessons} == {"code", "levels", "qa", "art", "process"}
    qa = next(lesson for lesson in lessons if lesson.topic == "qa")
    assert "level 3 starts and plays" in qa.evidence  # a QA test of a level is about QA
    clean = fallback_lessons(run_facts([report(1)], [], "genres/x", {})).lessons
    assert [lesson.title for lesson in clean] == ["A clean run"]


def test_lessons_go_to_the_memory_generated_and_only_promoted_ones_reach_the_plan(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"workflow": "t"})
    memory = KnowledgeStore(tmp_path / "memory", store, run.id)
    facts = ["genre genres/side-scrolling-platformer; 1 build(s)",
             "build v1: the bot did not clear level 2 (stuck)"]  # fmt: skip
    record = LessonRecord(lessons=fallback_lessons(facts).lessons, facts=facts, by="mock",
                          game="Slime Shift", genre="genres/side-scrolling-platformer", build=1)  # fmt: skip
    entries = lesson_entries(record, run.id)
    memory.write_batch(entries, "lessons", actor=ACTOR)
    assert validate(memory, external=False).errors == []
    found = {e.id: e for e in memory.scan()[0] if e.id.startswith("lessons/")}
    assert set(found) == {f"lessons/slime-shift-{run.id[:8]}-1"}
    lesson = found[f"lessons/slime-shift-{run.id[:8]}-1"]
    assert lesson.metadata.type == "Lesson" and lesson.metadata.trust_tier == "unverified"
    assert lesson.metadata.generated.by == ACTOR and "side-scrolling-platformer" in lesson.metadata.tags
    assert "spriteforge memory promote" in lesson.body
    # Generated lessons wait for a human: no agent reads them until they are promoted.
    assert past_lessons(memory, "genres/side-scrolling-platformer", "a slime platformer") == ""
    memory.promote(lesson.id, "human:owner")
    text = past_lessons(memory, "genres/side-scrolling-platformer", "a slime platformer")
    assert lesson.title in text
    assert past_lessons(memory, "genres/top-down-dungeon-crawler", "a dungeon") == ""  # other genre
    assert lesson.title in plan_prompt("a platformer", "", text)


def test_the_export_holds_the_build_the_record_and_the_tests(tmp_path):
    build, db = tmp_path / "build", tmp_path / "db"
    (build / "tests").mkdir(parents=True)
    (build / "index.html").write_text("<html>", encoding="utf-8")
    (build / "tests" / "test_game_qa.py").write_text("# tests", encoding="utf-8")
    (db / ".git").mkdir(parents=True)
    (db / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (db / "index.md").write_text("# Game record", encoding="utf-8")
    data = export_zip(build, db, "Slime Shift!", 3, "# Lessons")
    assert data == export_zip(build, db, "Slime Shift!", 3, "# Lessons")  # the same bytes again
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert sorted(names) == ["slime-shift/README.txt", "slime-shift/build/index.html",
                             "slime-shift/build/tests/test_game_qa.py", "slime-shift/db/index.md",
                             "slime-shift/lessons.md"]  # fmt: skip


# --- G5 in a gated run ---------------------------------------------------------------------------


def shown(store, ident, gate):
    names = {a.id: a.name for a in store.artifacts(ident)}
    return [names[i] for i in gate.artifacts_to_show]


def test_g5_reviews_the_whole_game_then_promotes_it_writes_lessons_and_exports(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    cfg.workspace = tmp_path
    ident = start_run(store, cfg, mock=True, goal=WORDS).id
    workflow_step(store, ident)
    answer(store, ident, form={"genre": "genres/side-scrolling-platformer"})
    answer(store, ident, form={"perspective": "side-scroll"})
    answer(store, ident)
    submit(store, ident)  # the design: levels, menus, build v1, G3 (approved), G4
    assert Gates(store).pending(ident).stage == "playtest"
    suite = read(store, ident, "game/v1/qa-tests.json")
    levels = {t["name"]: t["level"] for t in suite["tests"] if t["name"].startswith("level ")}
    assert levels == {f"level {n} starts and plays": n for n in (1, 2, 3)}
    qa = {t["name"]: t for t in read(store, ident, "game/v1/qa.json")["tests"]}
    assert all(qa[name]["passed"] for name in levels), [qa[n]["failure"] for n in levels]
    names = {a.name for a in store.artifacts(ident)}
    assert {f"game/v1/levels/shot-{n}.png" for n in (1, 2, 3)} <= names  # one shot per level

    # G4 approval no longer ends the run: G5 shows the whole game.
    submit(store, ident)
    gate = Gates(store).pending(ident)
    assert gate.stage == "final" and gate.title.startswith("G5 Final review (build v1")
    assert "3/3 levels cleared by the bot" in gate.title
    seen = shown(store, ident, gate)
    assert seen[:2] == ["game/final/v1-review.json", "game/final/v1-lessons.json"]
    assert {"game/v1/levels/level-2.png", "game/v1/levels/shot-2.png", "game/v1/report.json"} <= set(seen)
    summary = read(store, ident, "game/final/v1-review.json")
    assert [lv["cleared"] for lv in summary["levels"]] == [True, True, True]
    assert summary["levels"][1]["qa"]["passed"] >= 1 and summary["levels"][1]["qa"]["failed"] == 0
    assert summary["qa"]["by_kind"]["menus and flow"]["passed"] >= 1
    assert summary["cost"]["chat"] >= 1 and summary["record"]["concepts"] > 20
    lessons = read(store, ident, "game/final/v1-lessons.json")
    assert 1 <= len(lessons["lessons"]) <= 6 and lessons["facts"][0].startswith("genre genres/side")
    assert store.get_run(ident).status != "completed"
    db = open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)
    tiers = {e.id: e.metadata.trust_tier for e in db.scan()[0]}
    assert tiers["builds/v1"] != "human-reviewed"  # G5 promotes, not G4

    # G5 feedback is G4 feedback: a new build, then G4 again, then G5 for it.
    submit(store, ident, "make the slime bright red")
    gate = Gates(store).pending(ident)
    assert gate.stage == "playtest" and gate.title.startswith("G4 Playtest v2")
    assert [c["entity"] for c in read(store, ident, "game/v2/report.json")["revision"]["art_changes"]] == ["slime"]
    submit(store, ident)
    assert Gates(store).pending(ident).title.startswith("G5 Final review (build v2")

    # Approve: the run is finished, the record human-reviewed, the lessons in the memory.
    submit(store, ident)
    assert store.get_run(ident).status == "completed"
    approval = read(store, ident, "approval.json")
    assert approval["version"] == 2 and approval["export"] == "game/v2/export.zip"
    assert approval["lessons"] and approval["lessons_error"] == ""
    stable = [e for e in db.scan()[0] if e.metadata.status == "stable"]
    assert {e.metadata.trust_tier for e in stable} == {"human-reviewed"}
    decisions = [e for e in stable if e.id.startswith("decisions/")]
    assert any("G5 final review (build v1)" in d.title and "changes requested" in d.body for d in decisions)
    assert any("G5 final review (build v2)" in d.title and "the game is finished" in d.body for d in decisions)
    assert conformance(db) == [] and validate(db, external=False).errors == []
    memory = KnowledgeStore(tmp_path / "memory", store, ident)
    written = {e.id: e for e in memory.scan()[0] if e.id in approval["lessons"]}
    assert len(written) == len(approval["lessons"])
    assert {e.metadata.trust_tier for e in written.values()} == {"unverified"}
    assert validate(memory, external=False).errors == []
    archive = next(a for a in store.artifacts(ident) if a.name == "game/v2/export.zip")
    files = zipfile.ZipFile(io.BytesIO(archive.data)).namelist()
    top = files[0].split("/")[0]
    assert {f"{top}/build/index.html", f"{top}/build/tests/test_game_qa.py", f"{top}/db/index.md",
            f"{top}/db/builds/v2.md", f"{top}/lessons.md"} <= set(files)  # fmt: skip
    assert not any("/.git/" in f for f in files)
    exported = json.loads(zipfile.ZipFile(io.BytesIO(archive.data)).read(f"{top}/build/levels.json"))
    assert len(exported["levels"]) == 3
