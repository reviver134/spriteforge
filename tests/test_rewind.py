"""Phase 3b of the studio (docs/ui-plan.md): interrupt and rewind, and the cost policy (caps
stop agent loops, never work the user asks for)."""

import json
import threading

import pytest
from test_chat_changes import answer_until
from test_studio_api import by_name, wait_for

from spriteforge.config import BudgetConfig, init_workspace
from spriteforge.core.acting import acting
from spriteforge.core.cost import Budget, BudgetExceeded, Cancelled, for_the_user
from spriteforge.core.state import Store
from spriteforge.game import pipeline
from spriteforge.game.loops import Progress, within
from spriteforge.stages import game as game_stage
from spriteforge.stages.rewind import request_stop


@pytest.fixture
def run(tmp_path):
    store = Store(init_workspace(tmp_path) / "state.sqlite3")
    return store, store.create_run({})


def test_a_stop_request_refuses_new_calls_but_not_the_users(run):
    store, r = run
    budget = Budget(store, r.id, BudgetConfig())
    budget.reserve("meta", "spark")
    request_stop(store, r.id, [])
    with pytest.raises(Cancelled):
        budget.reserve("meta", "spark")
    try:  # the broad handlers that keep a build going never swallow it
        budget.reserve("meta", "spark")
    except Exception:  # noqa: BLE001
        pytest.fail("Cancelled was caught by `except Exception`")
    except Cancelled:
        pass
    with for_the_user():  # an answer to the human's question still starts
        budget.reserve("meta", "spark")


def test_each_agent_has_its_own_allowance_from_the_last_human_action(run):
    store, r = run
    # $0.02 each: two images at budget.image_cost $0.01.
    budget = Budget(store, r.id, BudgetConfig(per_request=True, max_agent_cost=0.02,
                                              max_image_calls=0))  # fmt: skip
    with acting("Pixel Artist"):
        budget.reserve("meta", "image", image=True)  # the per-run call caps do not apply
        budget.reserve("meta", "image", image=True)
        with pytest.raises(BudgetExceeded, match=r"the Pixel Artist used up its \$0.02 allowance"):
            budget.reserve("meta", "image", image=True)
    with acting("Art Reviewer"):  # another agent's allowance is its own
        budget.reserve("meta", "image", image=True)
    store.append_event(r.id, "Human", "gate.responded", {"id": "g1"})  # a fresh allowance
    with acting("Pixel Artist"):
        budget.reserve("meta", "image", image=True)
    whole_run = Budget(store, r.id, BudgetConfig(max_image_calls=2, per_request=False))
    with pytest.raises(BudgetExceeded):
        whole_run.reserve("meta", "image", image=True)
    with for_the_user():
        whole_run.reserve("meta", "image", image=True)


def test_loops_stop_when_tries_stop_improving():
    tries = Progress(patience=2)
    assert tries.add(-3) and tries.add(-1)  # fewer problems: better
    assert not tries.add(-1) and not tries.stuck  # one try without improvement
    assert not tries.add(-2) and tries.stuck  # two in a row: stop
    assert within(5, None) and within(0, 1) and not within(1, 1)


def artifacts(store, run_id):
    return {a.name: a for a in store.artifacts(run_id)}


def report(store, run_id, version):
    return json.loads(artifacts(store, run_id)[f"game/v{version}/report.json"].data)


def file_change(store, run_id, agent, slot, text) -> int:
    """A chat change as the studio files it (for a stop triggered from inside the worker)."""
    store.append_event(run_id, "Human", "chat.user", {"to": agent, "text": text,
                                                      "intent": "change", "slot": slot,
                                                      "status": "queued"})  # fmt: skip
    return max(e.id for e in store.events(run_id))


def request_changes(client, run_id, state, seen):
    gate = state["gate"]
    seen.add(gate["id"])
    response = client.post(f"/api/runs/{run_id}/gates/{gate['id']}?wait=false",
                           json={"action": "request_changes", "text": ""})  # fmt: skip
    assert response.status_code == 200, response.text


def test_go_back_during_art_redoes_the_round_with_both_changes(studio, monkeypatch):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    answer_until(client, run_id, "playtest", seen)
    entity = report(store, run_id, 1)["art"][0]["entity"]
    say = lambda agent, text: client.post(f"/api/runs/{run_id}/agents/{agent}/messages",
                                          json={"text": text}).json()["items"]  # fmt: skip
    say("Pixel Artist", f"make the {entity.replace('_', ' ')} purple")

    # The art of build v2 waits until the test has asked for a change and pressed Go back.
    entered, release, original = threading.Event(), threading.Event(), game_stage.produce_art

    def produce_art(*args, **kwargs):
        entered.set()
        assert release.wait(60)
        return original(*args, **kwargs)

    monkeypatch.setattr(game_stage, "produce_art", produce_art)
    request_changes(client, run_id, client.get(f"/api/runs/{run_id}/agents").json(), seen)
    assert entered.wait(60)
    busy = client.get(f"/api/runs/{run_id}/agents").json()
    assert busy["busy"] and not busy["gate"]
    reply = say("Game Designer", "make the enemies faster")[-1]
    card = reply["rewind"]
    assert card["state"] == "open" and card["build"] == 2
    assert reply["text"].startswith("This stops the ") and "goes back to the Game Designer" in reply["text"]
    stop = client.post(f"/api/runs/{run_id}/stop", json={"change": card["change"]})
    assert stop.status_code == 200 and stop.json()["stopping"]
    monkeypatch.setattr(game_stage, "produce_art", original)
    release.set()

    state = answer_until(client, run_id, "playtest", seen)
    names = artifacts(store, run_id)
    assert "game/v2/report.json" not in names  # stopped: superseded, its number never reused
    assert report(store, run_id, 3)["design_version"] == 2
    revision = json.loads(names["game/v3/revision.json"].data)
    assert [c["entity"] for c in revision["art_changes"]] == [entity]
    assert "make the enemies faster" in revision["design_changes"]
    assert json.loads(names["game/v3/base.json"].data) == {"base": 1}
    events = [e.type for e in store.events(run_id)]
    assert events.count("round.superseded") == 1 and events.count("run.stop_cleared") == 1
    assert state["queued"] == []
    designer = client.get(f"/api/runs/{run_id}/agents/Game Designer/thread").json()["items"]
    assert next(i for i in designer if "rewind" in i)["rewind"]["state"] == "went_back"
    director = client.get(f"/api/runs/{run_id}/agents/Director/thread").json()["items"]
    assert any(i["kind"] == "run" and i["type"] == "round.rewound" for i in director)
    assert by_name(state)["Programmer"]["status"] == "needs_you"


def test_stops_during_code_and_during_a_repair_round_are_not_swallowed(studio, monkeypatch):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    state = answer_until(client, run_id, "playtest", seen)

    # 1. During code generation: the Programmer's call is refused, the round is redone.
    original = pipeline.edit_code
    calls = []

    def stop_in_code(models, code, prompt_for, log):
        if not calls:
            calls.append("stopped")
            request_stop(store, run_id, [file_change(store, run_id, "Programmer",
                                                     "code_instructions", "jump higher")])  # fmt: skip
        return original(models, code, prompt_for, log)

    monkeypatch.setattr(pipeline, "edit_code", stop_in_code)
    say = client.post(f"/api/runs/{run_id}/agents/Programmer/messages", json={"text": "make it faster"})
    assert say.status_code == 201
    request_changes(client, run_id, state, seen)
    state = answer_until(client, run_id, "playtest", seen)
    revision = json.loads(artifacts(store, run_id)["game/v3/revision.json"].data)
    assert revision["code_instructions"].split("\n") == ["make it faster", "jump higher"]
    assert "game/v2/report.json" not in artifacts(store, run_id)

    # 2. During a repair round: the first attempt fails, the stop comes with the repair call.
    play, repairs = pipeline.play, []

    def failing_play(*args, **kwargs):
        result = play(*args, **kwargs)
        return {**result, "passed": False, "errors": [*result.get("errors", []), "boom"]}

    def stop_in_repair(models, code, prompt_for, log):
        if "Runtime error: boom" in prompt_for(None) and not repairs:
            repairs.append("stopped")
            request_stop(store, run_id, [file_change(store, run_id, "Programmer",
                                                     "code_instructions", "slower enemies")])  # fmt: skip
        return original(models, code, prompt_for, log)

    monkeypatch.setattr(pipeline, "play", failing_play)
    monkeypatch.setattr(pipeline, "edit_code", stop_in_repair)
    client.post(f"/api/runs/{run_id}/agents/Programmer/messages", json={"text": "bigger jump"})
    request_changes(client, run_id, state, seen)
    state = answer_until(client, run_id, "playtest", seen)
    assert repairs == ["stopped"]
    names = artifacts(store, run_id)
    assert "game/v4/report.json" not in names and "game/v5/report.json" in names
    assert json.loads(names["game/v5/revision.json"].data)["code_instructions"].split("\n") == [
        "bigger jump", "slower enemies"]
    assert store.get_run(run_id).status != "failed"


def test_the_same_failure_twice_stops_the_repairs_and_opens_the_gate(studio, monkeypatch):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    state = answer_until(client, run_id, "playtest", seen)
    play = pipeline.play

    def failing_play(*args, **kwargs):
        result = play(*args, **kwargs)
        return {**result, "passed": False, "errors": ["boom"]}

    def changed(models, code, prompt_for, log):  # a repair that changes the code each time
        files = [f.model_copy(update={"content": f.content + "\n// tried"}) for f in code.files]
        return code.model_copy(update={"files": files}), "edits"

    monkeypatch.setattr(pipeline, "play", failing_play)
    monkeypatch.setattr(pipeline, "edit_code", changed)
    client.post(f"/api/runs/{run_id}/agents/Programmer/messages", json={"text": "make it faster"})
    request_changes(client, run_id, state, seen)
    state = answer_until(client, run_id, "playtest", seen)
    attempts = report(store, run_id, 2)["attempts"]
    assert len(attempts) == 2 and "the same failure each time" in attempts[-1]["stopped"]
    assert "the same failure each time" in state["gate"]["title"]


def test_a_used_up_allowance_pauses_the_run_until_continue(studio, monkeypatch):
    store, client = studio
    from spriteforge.app import workers

    real = workers.step_for
    once = []

    def step_for(store_, run_id):
        if not once:
            once.append(1)

            def broke(*_):
                raise BudgetExceeded("Call budget exceeded for meta/muse-spark")

            return broke
        return real(store_, run_id)

    monkeypatch.setattr(workers, "step_for", step_for)
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    state = wait_for(client, run_id, lambda s: s["status"] == "budget_exhausted")
    assert not state["busy"]
    assert client.post(f"/api/runs/{run_id}/resume").json()["started"]
    state = wait_for(client, run_id, lambda s: s["gate"])
    assert state["status"] != "budget_exhausted"
    types = [e.type for e in store.events(run_id)]
    assert types.index("run.paused") < types.index("allowance.granted")


def test_go_back_during_the_first_build_revises_the_design_without_reopening_g2(studio, monkeypatch):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    state = answer_until(client, run_id, "design", seen)
    entered, release, original = threading.Event(), threading.Event(), game_stage.produce_art

    def produce_art(*args, **kwargs):
        entered.set()
        assert release.wait(60)
        return original(*args, **kwargs)

    monkeypatch.setattr(game_stage, "produce_art", produce_art)
    gate = state["gate"]
    seen.add(gate["id"])
    url = f"/api/runs/{run_id}/gates/{gate['id']}?wait=false"
    response = client.post(url, json={"action": "approve"})
    if response.status_code == 409:
        response = client.post(url, json={"action": "submit"})
    assert response.status_code == 200, response.text
    assert entered.wait(60)
    reply = client.post(f"/api/runs/{run_id}/agents/Game Designer/messages",
                        json={"text": "make the enemies faster"}).json()["items"][-1]  # fmt: skip
    assert reply["rewind"]["build"] == 1
    client.post(f"/api/runs/{run_id}/stop", json={"change": reply["rewind"]["change"]})
    monkeypatch.setattr(game_stage, "produce_art", original)
    release.set()

    state = wait_for(client, run_id, lambda s: s["gate"] and s["gate"]["id"] not in seen,
                     timeout=180)  # fmt: skip
    assert state["gate"]["stage"] in {"menus", "playtest"}  # G2 is not reopened
    names = artifacts(store, run_id)
    assert "game/v1/report.json" not in names and "game/v2/report.json" in names
    assert report(store, run_id, 2)["design_version"] == 2
    assert json.loads(names["game/v2/base.json"].data) == {"base": None}
    assert "game/v1/art/" not in "".join(names)  # the stopped build saved no art


def test_repairs_go_past_the_old_limit_while_they_improve(studio, monkeypatch):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    state = answer_until(client, run_id, "playtest", seen)
    play, plays = pipeline.play, []

    def improving_play(*args, **kwargs):  # 4 errors, then 3, 2, 1, then it passes
        plays.append(1)
        result = play(*args, **kwargs)
        left = 5 - len(plays)
        return {**result, "passed": left <= 0, "errors": [f"error {i}" for i in range(left)]}

    def changed(models, code, prompt_for, log):
        files = [f.model_copy(update={"content": f.content + "\n// tried"}) for f in code.files]
        return code.model_copy(update={"files": files}), "edits"

    monkeypatch.setattr(pipeline, "play", improving_play)
    monkeypatch.setattr(pipeline, "edit_code", changed)
    client.post(f"/api/runs/{run_id}/agents/Programmer/messages", json={"text": "make it faster"})
    request_changes(client, run_id, state, seen)
    answer_until(client, run_id, "playtest", seen)
    attempts = report(store, run_id, 2)["attempts"]
    assert len(attempts) >= 5 and attempts[-1]["passed"]  # 4+ repairs (the old limit was 3)
    assert not any(a.get("stopped") for a in attempts)
