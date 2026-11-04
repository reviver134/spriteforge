"""Each agent's plan at G2 (entry gate, steps, exit gate) and its report after a build."""

import json

from test_game_stage import to_design_gate

from spriteforge.app.agents_view import summarize, thread
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.game import team_reports as team
from spriteforge.game.design import GameDesign
from spriteforge.stages.workflow import workflow_step


def design() -> GameDesign:
    return GameDesign.model_validate({
        "title": "Slime Arena", "pitch": "Survive.", "camera": "top-down", "endless": False,
        "controls": "Arrows move", "session_seconds": 60, "player": "A knight", "world": "A cave",
        "look": "Dark green cave", "rules": ["Touching a slime hurts"], "progression": [],
        "difficulty": ["More slimes"], "win": "Survive 60 s", "lose": "No hearts", "hud": ["Hearts"],
        "entities": [
            {"id": "knight", "role": "player", "look": "Blue knight", "behaviour": "Moves",
             "width": 16, "height": 16, "animations": ["walk"]},
            {"id": "slime", "role": "enemy", "look": "Green slime", "behaviour": "Hops",
             "width": 16, "height": 16, "animations": []},
        ],
        "checks": [{"id": "spawn", "description": "Slimes appear", "event": "enemy_spawn",
                    "min_count": 1, "by_second": 10}],
    })  # fmt: skip


def test_fit_keeps_the_build_agents_in_order_and_fills_what_is_missing():
    agents = team.build_agents(design(), None, levels=False, menus=True, sounds=False, review=True)
    assert agents == ["Art Director", "Pixel Artist", "Menu Designer", "Programmer",
                      "QA Playtester", "Art Reviewer"]  # fmt: skip
    fallback = team.default_tasks(design(), None, agents)
    answer = team.TaskList(agents=[
        team.AgentPlan(agent="programmer", starts_when=[], tasks=["Write it " + "x" * 300],
                       done_when=["It runs"]),
        team.AgentPlan(agent="Sound Director", starts_when=["a"], tasks=["b"], done_when=["c"]),
    ])  # fmt: skip
    fitted = team.fit_tasks(answer, fallback)
    assert [p.agent for p in fitted.agents] == agents  # no Sound Director: sounds are off
    coder = next(p for p in fitted.agents if p.agent == "Programmer")
    assert coder.done_when == ["It runs"] and len(coder.tasks[0]) <= team.POINT
    assert coder.starts_when == fallback.agents[3].starts_when  # empty: the plain plan's
    assert all(p.tasks and p.starts_when and p.done_when for p in fitted.agents)


def test_exit_reports_are_checked_by_code_from_the_build_report():
    report = {
        "title": "Slime Arena", "passed": False, "play": "/play", "problems": ["Check spawn failed"],
        "art": [{"entity": "knight", "source": "muse"}, {"entity": "slime", "source": "library"}],
        "warnings": [], "checks": [{"id": "spawn", "ok": False}],
        "attempts": [{"attempt": 1}, {"attempt": 2}], "qa": {"passed": 3, "failed": 1},
    }  # fmt: skip
    reports = {r.agent: r for r in team.exit_reports(report, design())}
    assert set(reports) == {"Art Director", "Pixel Artist", "Programmer", "QA Playtester"}
    assert "1 drawn by Muse Image" in reports["Pixel Artist"].did[0]
    assert dict(reports["Programmer"].checks)["All checks of the design pass (0/1)"] is False
    text = team.team_markdown(list(reports.values()), 1, "Slime Arena")
    assert "## Programmer" in text and "⚠️" in text and "✅" in text


def test_every_agent_shows_its_plan_and_asks_for_g2_then_reports_after_the_build(tmp_path):
    store, ident = to_design_gate(tmp_path)
    gate = Gates(store).pending(ident)
    assert gate.stage == "design" and {"Game Designer", "Pixel Artist", "Programmer"} <= set(gate.agents)
    names = {a.name: a for a in store.artifacts(ident)}
    tasks = json.loads(names["game/tasks/v1.json"].data)
    assert [p["agent"] for p in tasks["agents"]] == gate.agents[1:]
    assert "Done when (exit gate)" in names["game/tasks/v1.md"].data.decode()

    items = thread(store, ident, "Pixel Artist")
    kinds = [i["kind"] for i in items]
    assert kinds.index("report") < kinds.index("gate")  # the plan, then the approval it asks for
    plan = next(i["report"] for i in items if i["kind"] == "report")
    assert plan["stage"] == "entry" and plan["sections"][0]["title"].startswith("Starts")
    status = {a.name: a.status for a in summarize(store, ident, busy=False)}
    assert status["Pixel Artist"] == "needs_you" and status["Programmer"] == "needs_you"
    designer = [i["report"]["stage"] for i in thread(store, ident, "Game Designer") if i["kind"] == "report"]
    assert designer == ["exit"]  # the Game Designer reports what it wrote

    ScriptedHuman([Feedback(action="submit", text="")]).answer(Gates(store), ident)
    workflow_step(store, ident)  # build v1 -> G3 (menus) or G4
    gate = Gates(store).pending(ident)
    assert gate.stage in {"menus", "playtest"} and "Programmer" in gate.agents
    reports = [i["report"] for i in thread(store, ident, "Programmer") if i["kind"] == "report"]
    assert [r["stage"] for r in reports] == ["entry", "exit"]
    assert reports[1]["sections"][1]["title"] == "Exit gate (checked by code)"
    assert any(a.name == "game/v1/team-report.md" for a in store.artifacts(ident))
