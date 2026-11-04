"""G4/G5 feedback: the Director's plan is approved by the human before any agent acts on it."""

import json

from spriteforge.config import load_config
from spriteforge.core.gates import Feedback, Gates, ScriptedHuman
from spriteforge.core.state import Store
from spriteforge.stages.rewind import current_round
from spriteforge.stages.workflow import start_run, workflow_step


def say(store, ident, text="", step=True, **kwargs):
    ScriptedHuman([Feedback(action="submit", text=text, **kwargs)]).answer(Gates(store), ident)
    if step:
        workflow_step(store, ident)


def names(store, ident):
    return {a.name for a in store.artifacts(ident)}


def read(store, ident, name):
    return next(a for a in store.artifacts(ident) if a.name == name).data


def to_playtest(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, load_config(), mock=True, goal="survive a slime arena, collect gems")
    workflow_step(store, run.id)
    for form in ({"genre": "genres/top-down-dungeon-crawler"}, {"environment": "A slime cave"}, {}):
        ScriptedHuman([Feedback(action="approve", form=form)]).answer(Gates(store), run.id)
        workflow_step(store, run.id)
    say(store, run.id)  # G2: approve design v1 -> build v1
    while (gate := Gates(store).pending(run.id)).stage == "menus":
        say(store, run.id)
    assert gate.stage == "playtest"
    return store, run.id


def test_playtest_feedback_waits_for_the_plan_to_be_approved(tmp_path):
    store, ident = to_playtest(tmp_path)
    playtest = Gates(store).pending(ident)
    say(store, ident, "make the slime bright red")

    gate = Gates(store).pending(ident)
    assert gate.stage == "confirm" and gate.parent_id == playtest.id
    assert gate.title.startswith("G4 Confirm the plan") and gate.empty_feedback_approves
    assert not any(n.startswith("game/v2/") for n in names(store, ident))  # nothing built yet
    n = len(Gates(store).all(ident)) - 1  # the playtest gate's number
    plan = read(store, ident, f"game/feedback/gate-{n}-plan.md").decode()
    assert "redraw `slime`" in plan and "> make the slime bright red" in plan
    assert current_round(store, ident) is None  # an open gate: nothing to stop

    # A correction: the Director plans again with it (a new round key), and asks again.
    say(store, ident, "the hero too")
    again = Gates(store).pending(ident)
    assert again.stage == "confirm" and again.parent_id == playtest.id
    assert not any(n.startswith("game/v2/") for n in names(store, ident))
    revision = json.loads(read(store, ident, f"game/feedback/gate-{n}.1-revision.json"))
    assert [c["entity"] for c in revision["art_changes"]] == ["hero", "slime"]
    assert "redraw `hero`" in read(store, ident, f"game/feedback/gate-{n}.1-plan.md").decode()

    # Approving the plan is what starts the work, as the playtest gate's round.
    say(store, ident, step=False)
    assert current_round(store, ident)[0].id == playtest.id
    workflow_step(store, ident)
    assert Gates(store).pending(ident).stage == "playtest"
    report = json.loads(read(store, ident, "game/v2/report.json"))
    assert sorted(c["entity"] for c in report["revision"]["art_changes"]) == ["hero", "slime"]


def test_story_only_feedback_is_confirmed_too(tmp_path):
    store, ident = to_playtest(tmp_path)
    say(store, ident, "add a story about the slime king")
    assert Gates(store).pending(ident).stage == "confirm"
    assert not any(n.startswith("game/story/") for n in names(store, ident))
    say(store, ident)
    assert Gates(store).pending(ident).stage == "playtest"
    assert any(n.startswith("game/story/") for n in names(store, ident))
