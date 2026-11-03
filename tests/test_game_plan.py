"""L2.2: research with web search, the game plan, and the fixes found in the L2.1 live runs."""

import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_game_db import conformance

from spriteforge.app.server import create_app
from spriteforge.config import load_config
from spriteforge.core.cost import Budget, ledger
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.game.art import keyword_plan
from spriteforge.game.codegen import requirements
from spriteforge.game.design import Entity, fallback_design
from spriteforge.game.gamedb import open_db
from spriteforge.game.pipeline import Models, plan_new_art, write_plan, write_research
from spriteforge.game.plan import (
    Coverage,
    GamePlan,
    LevelPlan,
    Movement,
    check_plan,
    derive_kinematics,
    fallback_plan,
    fit_plan,
    make_record,
)
from spriteforge.game.research import Reference, Research, fallback_research, fit_research
from spriteforge.game.revision import Revision
from spriteforge.memory.okf.validate import validate
from spriteforge.providers.base import SearchResult, WebSource
from spriteforge.providers.mock import MockLLMProvider, MockSearchProvider
from spriteforge.providers.muse_search import MuseSearchProvider, read_response
from spriteforge.stages.workflow import start_run, workflow_step

WORDS = ("A side-view pixel platformer with 3 levels. The hero is a small slime that can move, "
         "jump and dash, but it cannot kill enemies.")  # fmt: skip


@pytest.fixture
def budget(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    cfg = load_config()
    run = store.create_run({"workflow": "t", "budget": cfg.budget.model_dump()})
    return Budget(store, run.id, cfg.budget)


def calls(budget) -> list:
    with budget.store.connect() as db:
        return [c for c in ledger(db, budget.run_id).values() if c.status == "succeeded"]


# --- the probe's findings: the Responses API path, citations, searches, cost --------------------

# The shape tools/probe_web_search.py recorded (2026-11-02), trimmed.
RESPONSE = {
    "status": "completed",
    "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": "I'll research.", "annotations": []}]},
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "search", "query": "Celeste level design teaches dash",
                    "sources": [{"type": "url", "url": "https://www.example.org/celeste/"},
                                {"type": "url", "url": "https://wiki.example.org/Prologue"}]}},
        {"type": "web_search_call", "status": "completed",
         "action": {"type": "open_page", "url": "https://blog.example.org/mario-1-1"}},
        {"type": "message", "content": [{"type": "output_text", "text": '{"answer": 1}',
                                         "annotations": [{"type": "url_citation", "url": "https://cite.example.org/x",
                                                          "title": "Cited", "start_index": 0, "end_index": 5}]}]},
    ],
    "usage": {"input_tokens": 10000, "input_tokens_details": {"cached_tokens": 0},
              "output_tokens": 2000, "output_tokens_details": {"reasoning_tokens": 1000},
              "total_tokens": 12000},
}  # fmt: skip


def test_responses_answer_is_read_like_the_probe_saw_it():
    found = read_response(RESPONSE)
    assert found.content == '{"answer": 1}'  # the last message holds the structured answer
    assert found.text.startswith("I'll research.")
    assert found.searches == 2 and found.queries == ["Celeste level design teaches dash"]
    assert [s.url for s in found.sources] == [
        "https://www.example.org/celeste/", "https://wiki.example.org/Prologue",
        "https://blog.example.org/mario-1-1", "https://cite.example.org/x"]  # fmt: skip
    assert [c.title for c in found.cited] == ["Cited"]


def test_search_request_contract_and_search_cost(budget, monkeypatch):
    cfg = load_config()
    assert cfg.research.path == "/v1/responses" and cfg.research.max_searches == 8
    monkeypatch.setenv("META_API_KEY", "fake-test-key")
    seen = []

    def handler(request):
        seen.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=RESPONSE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        models = Models(cfg, budget, mock=False)
        provider = MuseSearchProvider(cfg.llm, cfg.research, budget, client)
        models.search = provider
        models.web_research("Game Designer", "study platformers", Research)
    path, body = seen[0]
    assert path == "/v1/responses"
    assert body["tools"] == [{"type": "web_search", "search_context_size": "medium"}]
    assert body["max_tool_calls"] == 8 and body["include"] == ["web_search_call.action.sources"]
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"]
    assert body["reasoning"] == {"effort": "medium"} and body["store"] is False
    assert "Game Designer" in body["instructions"]
    (call,) = calls(budget)
    assert call.web_searches == 2 and call.input_tokens == 10000 and call.reasoning_tokens == 1000
    tokens = (10000 * 1.25 + 2000 * 4.25) / 1_000_000
    assert call.estimated_cost == pytest.approx(tokens + 2 * 0.0025)


# --- research ----------------------------------------------------------------------------------


def research(*refs: tuple[str, str]) -> Research:
    base = fallback_research("", True)
    return base.model_copy(update={"references": [
        Reference(title=t, url=u, learn="teach the dash where a fall is safe") for t, u in refs]})  # fmt: skip


def test_reference_urls_are_verified_against_the_pages_searched():
    found = SearchResult(sources=[WebSource(url="https://www.example.org/celeste/")], searches=3)
    answer = research(("Celeste", "http://example.org/celeste"), ("Made up", "https://invented.example/x"),
                      ("Bad", "javascript:alert(1)"), ("Celeste", "https://dup.example"))  # fmt: skip
    record = fit_research(answer, found, "test")
    assert [r.title for r in record.research.references] == ["Celeste", "Made up", "Bad"]
    assert record.verified == [True, False, False]  # www., scheme and trailing slash ignored
    assert record.research.references[2].url == ""
    assert any("not among the pages" in n for n in record.notes)
    offline = fit_research(fallback_research("# Common pitfalls\n* floaty jumps\n", True), None, "offline")
    assert offline.research.references == [] and offline.research.pitfalls == ["floaty jumps"]


def test_research_one_call_or_a_structuring_second_call(budget):
    cfg = load_config()
    good = research(("Celeste", "https://www.example.org/celeste/"))
    found = SearchResult(content=good.model_dump_json(), text="notes", searches=4,
                         sources=[WebSource(url="https://example.org/celeste")])  # fmt: skip
    search = MockSearchProvider(budget, [found])
    record = write_research(Models(cfg, budget, mock=True, search=search), WORDS,
                            "Platformer", "", True)  # fmt: skip
    assert record.verified == [True] and record.searches == 4 and "web search" in record.by
    assert "never copy" in search.requests[0].prompt and search.requests[0].max_searches == 8
    # The searched answer does not fit the schema: one structured call (no search) fixes it.
    search = MockSearchProvider(budget, [found.model_copy(update={"content": "Here is what I found"})])
    llm = MockLLMProvider(budget, [good.model_dump()])
    record = write_research(Models(cfg, budget, mock=True, provider=llm, search=search), WORDS,
                            "Platformer", "", True)  # fmt: skip
    assert "second call" in record.by and record.verified == [True]
    # Offline: the genre notes, no references, no search.
    record = write_research(Models(cfg, budget, mock=True), WORDS, "Platformer", "", True)
    assert record.searches == 0 and record.research.references == [] and "offline" in record.by


# --- the plan: kinematics, repairs, checks -------------------------------------------------------


def test_kinematics_from_the_movement_numbers():
    m = Movement(move_speed=6, jump_height=3, air_time=0.7, dash_distance=3, dash_cooldown=1,
                 coyote_time=0.1)  # fmt: skip
    k = derive_kinematics(m)
    assert k.gravity == pytest.approx(8 * 48 / 0.49, abs=0.1)
    assert k.jump_velocity == pytest.approx(4 * 48 / 0.7, abs=0.1)
    assert (k.jump_distance, k.max_rise, k.max_gap, k.max_gap_with_dash) == (67.2, 3, 4, 7)
    assert k.coyote == pytest.approx(9.6)
    assert derive_kinematics(m.model_copy(update={"jump_height": 0, "air_time": 0})) is None


def good_plan() -> GamePlan:
    plan = fallback_plan([WORDS], side_view=True)
    assert len(plan.levels) == 3  # "3 levels" in the human's words
    return plan


def test_offline_plan_holds_and_requirements_are_the_humans_words():
    plan = good_plan()
    assert [r.text for r in plan.requirements] == [
        "A side-view pixel platformer with 3 levels.",
        "The hero is a small slime that can move, jump and dash, but it cannot kill enemies."]  # fmt: skip
    assert check_plan(plan, WORDS) == []
    record = make_record(plan, WORDS)
    assert record.kinematics.max_gap == 4 and not record.problems


def test_plan_checks_find_what_code_cannot_repair():
    plan = good_plan()
    bad = plan.model_copy(update={
        "requirements": [*plan.requirements, plan.requirements[0].model_copy(
            update={"id": "R3", "text": "A platformer that has three levels."})],  # paraphrased
        "coverage": [*plan.coverage, Coverage(requirement="R1", parts=["mechanic:teleport"], how="x")],
        "movement": plan.movement.model_copy(update={"jump_height": 1, "air_time": 2}),  # floaty
        "levels": [plan.levels[0],
                   plan.levels[1].model_copy(update={"teaching_goal": "", "challenges": ["lava"]}),
                   plan.levels[2].model_copy(update={"new_idea": plan.levels[0].new_idea,
                                                     "difficulty": 1})],
    })  # fmt: skip
    problems = "\n".join(check_plan(bad, WORDS))
    assert "R3 is not the human's own words" in problems
    assert "R3 is not covered" in problems
    assert "'mechanic:teleport' is not a part of the plan" in problems
    assert "floaty" in problems
    assert "level 2 (Level 2) has no teaching goal" in problems
    assert "challenge 'lava' is not in the vocabulary" in problems
    assert "level 3 (Level 3) repeats an earlier level's new idea" in problems
    assert "level 3 (difficulty 1) is easier than level 2" in problems
    jump = plan.movement.model_copy(update={"air_time": 0})
    assert any("both jump_height and air_time" in p
               for p in check_plan(plan.model_copy(update={"movement": jump}), WORDS))  # fmt: skip


def test_fit_plan_renumbers_requirements_and_levels():
    plan = good_plan()
    odd = plan.model_copy(update={
        "requirements": [r.model_copy(update={"id": str(i)}) for i, r in enumerate(plan.requirements, 1)],
        "coverage": [c.model_copy(update={"requirement": c.requirement[1:]}) for c in plan.coverage]
        + [Coverage(requirement="R9", parts=["movement"], how="x")],
        "levels": [lv.model_copy(update={"n": lv.n * 10, "difficulty": 20 if lv.n == 3 else lv.difficulty})
                   for lv in reversed(plan.levels)],
    })  # fmt: skip
    fitted, notes = fit_plan(odd)
    assert [r.id for r in fitted.requirements] == ["R1", "R2"]
    assert {c.requirement for c in fitted.coverage} == {"R1", "R2"}
    assert [lv.n for lv in fitted.levels] == [1, 2, 3] and fitted.levels[2].difficulty == 10
    assert any("R9" in n for n in notes) and check_plan(fitted, WORDS) == []


def test_plan_problems_go_back_to_the_game_designer_once(budget):
    cfg = load_config()
    plan = good_plan()
    bad = plan.model_copy(update={"levels": [LevelPlan(n=1, name="One", teaching_goal="", new_idea="",
                                                       challenges=[], abilities=[], difficulty=1)]})  # fmt: skip
    llm = MockLLMProvider(budget, [bad.model_dump(), plan.model_dump()])
    record = write_plan(Models(cfg, budget, mock=True, provider=llm), WORDS, [WORDS], "", True)
    assert record.retried and record.problems == [] and len(record.plan.levels) == 3
    llm = MockLLMProvider(budget, [bad.model_dump(), bad.model_dump()])
    record = write_plan(Models(cfg, budget, mock=True, provider=llm), WORDS, [WORDS], "", True)
    assert record.retried and any("no teaching goal" in p for p in record.problems)


# --- the gated run: G1 research question, G2 shows research, plan and design -----------------


def answer(store, ident, **kwargs):
    ScriptedHuman([Feedback(action="approve", **kwargs)]).answer(Gates(store), ident)
    workflow_step(store, ident)
    through_menus(store, ident)


def through_menus(store, ident):
    """G3 (the menus, L2.5) opens before the first G4: approve it (tested in test_game_flow)."""
    gate = Gates(store).pending(ident)
    if gate is not None and gate.stage == "menus":
        ScriptedHuman([Feedback(action="submit", text="")]).answer(Gates(store), ident)
        workflow_step(store, ident)


def submit(store, ident, text=""):
    ScriptedHuman([Feedback(action="submit", text=text)]).answer(Gates(store), ident)
    workflow_step(store, ident)
    through_menus(store, ident)


def names_of(store, ident, ids) -> list[str]:
    by_id = {a.id: a.name for a in store.artifacts(ident)}
    return [by_id[i] for i in ids]


def read(store, ident, name):
    return json.loads(next(a for a in store.artifacts(ident) if a.name == name).data)


def gated(tmp_path, research=None):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, load_config(), mock=True, goal=WORDS)
    workflow_step(store, run.id)
    answer(store, run.id, form={"genre": "genres/side-scrolling-platformer"})
    form = {"perspective": "side-scroll"} | ({"research": research} if research else {})
    answer(store, run.id, form=form)
    answer(store, run.id)  # follow-up questions
    return store, run.id


def test_g2_shows_research_then_plan_then_design(tmp_path):
    store, ident = gated(tmp_path)
    gate = Gates(store).pending(ident)
    assert gate.stage == "design" and "Read the research, the game plan" in gate.title
    assert names_of(store, ident, gate.artifacts_to_show) == [
        "game/research.json", "game/plan/v1.json", "game/design/v1.json",
        "game/design/v1-structure.json"]  # fmt: skip
    plan = read(store, ident, "game/plan/v1.json")
    assert plan["problems"] == [] and len(plan["plan"]["levels"]) == 3
    assert plan["kinematics"]["max_gap"] >= 1
    db = open_db(tmp_path / "runs" / ident / "game" / "db", store, ident)
    ids = {e.id for e in db.scan()[0]}
    assert {"research/overview", "plan/overview", "plan/movement", "plan/mechanics", "plan/feel",
            "plan/levels/1", "plan/levels/3"} <= ids  # fmt: skip
    movement = next(e for e in db.scan()[0] if e.id == "plan/movement")
    assert "# Schema" in movement.body and "`max_gap`" in movement.body
    assert validate(db, external=False).errors == [] and conformance(db) == []
    # G2 feedback revises the plan, then the design from it.
    submit(store, ident, "Make the levels longer")
    gate = Gates(store).pending(ident)
    assert names_of(store, ident, gate.artifacts_to_show) == [
        "game/research.json", "game/plan/v2.json", "game/design/v2.json",
        "game/design/v2-structure.json"]  # fmt: skip
    assert sum(a.name == "game/research.json" for a in store.artifacts(ident)) == 1


def test_no_research_at_g1_means_no_research(tmp_path):
    store, ident = gated(tmp_path, research="No research")
    names = {a.name for a in store.artifacts(ident)}
    assert "game/research.json" not in names and "game/plan/v1.json" in names
    assert read(store, ident, "brief.json")["research"] == "No research"
    db = tmp_path / "runs" / ident / "game" / "db"
    assert not (db / "research").exists() and (db / "plan" / "overview.md").is_file()


# --- the fixes from the L2.1 live runs -------------------------------------------------------------


def test_design_change_keeps_locked_art_and_plans_only_new_entities(budget):
    cfg = load_config()
    design = fallback_design("slime platformer")
    previous = keyword_plan(design)
    models = Models(cfg, budget, mock=True)
    same = plan_new_art(models, design, previous)
    assert same.picks == previous.picks and calls(budget) == []  # no Art Director call
    bigger = design.model_copy(update={"entities": [*design.entities[1:], Entity(
        id="crate", role="prop", look="a wooden crate", behaviour="dissolves into coins",
        width=16, height=16, animations=["break"])]})  # the hero is gone, a crate is new  # fmt: skip
    plan = plan_new_art(models, bigger, previous)
    assert [p.entity for p in plan.picks] == ["slime", "gem", "floor", "crate"]
    assert plan.picks[:3] == previous.picks[1:] and len(calls(budget)) == 1


def test_g4_design_change_revises_the_plan_and_redraws_no_art(tmp_path, monkeypatch):
    from spriteforge.stages import game as stage

    def director(design, feedback, tests=()):
        return Revision(design_changes=feedback, art_changes=[], code_instructions="",
                        drop_qa_tests=[])  # fmt: skip

    monkeypatch.setattr(stage, "fallback_revision", director)
    store, ident = gated(tmp_path)
    submit(store, ident)  # approve the design: build v1
    with TestClient(create_app(store=store)) as client:
        detail = client.get(f"/api/runs/{ident}").json()
    assert detail["play"] == {"version": 1, "url": f"/api/runs/{ident}/play/v1/index.html", "sounds": True}
    submit(store, ident, "make the jump much higher")
    report = read(store, ident, "game/v2/report.json")
    assert report["design_version"] == 2 and report["revision"]["design_changes"]
    assert "game/plan/v2.json" in {a.name for a in store.artifacts(ident)}
    assert all(a["source"].startswith(("muse (locked, from v1)", "library", "tileset"))
               for a in report["art"]), report["art"]  # fmt: skip
    assert read(store, ident, "game/v2/art-plan-new.json")["picks"] == \
        read(store, ident, "game/v1/art-plan.json")["picks"]  # fmt: skip


def test_programmer_must_let_an_idle_player_lose():
    text = requirements(fallback_design("arena"))
    assert "A player who presses nothing can lose" in text


def test_g4_plan_revision_copies_requirements_from_the_humans_words():
    """Live 2026-11-02: given only the Director's summary at G4, the Game Designer copied the
    summary as a requirement (not the human's words); the prompt now carries both."""
    from spriteforge.game.plan import plan_revision_prompt

    said = "The slime's jump is too low: make the jump higher, about 4 tiles."
    changes = "Increase slime jump height to about 4 tiles (up from 3.2 tiles)."
    prompt = plan_revision_prompt(good_plan(), changes, WORDS + "\n- " + said, said)
    assert f"The human said:\n{said}" in prompt
    assert f"What to change in the plan:\n{changes}" in prompt
    assert "never from a summary" in prompt
    g2 = plan_revision_prompt(good_plan(), said, WORDS)  # at G2 the feedback is the human's words
    assert f"The human said:\n{said}" in g2 and "What to change in the plan" not in g2
