"""Phase 3 of the studio (docs/ui-plan.md): a change asked in an agent's chat is filed under that
agent's Revision slot and applied by the gated pipeline; QA's Suggest a check."""

import json

from spriteforge.game.design import GameDesign
from spriteforge.game.qa_checks import check_prompt
from spriteforge.game.codegen import GameCode
from spriteforge.game.revision import ArtChange, Revision, merge
from spriteforge.stages.chat_changes import as_revision, named_entities, slot_for

from test_studio_api import by_name, studio, wait_for  # noqa: F401 -- the fixture

DESIGN = {
    "title": "Cave", "pitch": "p", "camera": "side-view", "endless": False, "controls": "arrows",
    "session_seconds": 60, "player": "p", "world": "w", "look": "l", "rules": ["r"],
    "progression": [], "difficulty": [], "win": "w", "lose": "l", "hud": [],
    "checks": [{"id": "c", "description": "d", "event": "hit", "min_count": 1, "by_second": 0}],
    "entities": [
        {"id": "hero", "role": "player", "look": "a knight", "behaviour": "b", "width": 16,
         "height": 16, "animations": []},
        {"id": "cave_bat", "role": "enemy", "look": "a grey bat", "behaviour": "b", "width": 16,
         "height": 16, "animations": []},
    ],
}  # fmt: skip


def design() -> GameDesign:
    return GameDesign.model_validate(DESIGN)


def change(slot, text, agent="x", id=1):
    return {"id": id, "agent": agent, "slot": slot, "text": text}


def test_slots_follow_the_agent():
    assert slot_for("Pixel Artist", "make the bat purple") == "art_changes"
    assert slot_for("Game Designer", "add a story about a lost prince") == "story_changes"
    assert slot_for("Game Designer", "enemies should be faster") == "design_changes"
    assert slot_for("QA Playtester", "check that the bat cannot fly through walls") == "add_qa_checks"
    assert slot_for("QA Playtester", "drop the restart test") == "drop_qa_tests"
    assert slot_for("Programmer", "make the jump higher") == "code_instructions"
    assert slot_for("Director", "more enemies") == "feedback"


def test_changes_become_one_revision_without_the_director():
    d = design()
    assert named_entities(d, "make the cave bats purple") == ["cave_bat"]
    rev, routed = as_revision([
        change("art_changes", "make the cave bat purple"),
        change("art_changes", "make everything darker"),  # names no entity: the Director routes it
        change("level_changes", "make level 2 shorter"),
        change("level_changes", "fewer gaps"),  # no number: every level
        change("code_instructions", "jump higher"),
        change("add_qa_checks", "check that the bat never leaves the screen"),
        change("drop_qa_tests", "ignore the restart test"),
        change("feedback", "more enemies"),
    ], d, ["restarts after losing", "moves"], levels=3)  # fmt: skip
    assert [(c.entity, c.prompt) for c in rev.art_changes] == [
        ("cave_bat", "a grey bat; make the cave bat purple")
    ]
    assert [(c.level, c.change) for c in rev.level_changes] == [
        (2, "make level 2 shorter"), (1, "fewer gaps"), (2, "fewer gaps"), (3, "fewer gaps")
    ]
    assert rev.code_instructions == "jump higher"
    assert rev.add_qa_checks == ["check that the bat never leaves the screen"]
    assert rev.drop_qa_tests == ["restarts after losing"]
    assert routed == "make everything darker\nmore enemies"


def test_merge_keeps_both_sides():
    a = Revision(design_changes="", art_changes=[ArtChange(entity="hero", prompt="red")],
                 code_instructions="faster", drop_qa_tests=[], story_changes="", level_changes=[])
    b = Revision(design_changes="", art_changes=[ArtChange(entity="hero", prompt="blue")],
                 code_instructions="higher jump", drop_qa_tests=[], story_changes="",
                 level_changes=[], add_qa_checks=["check x"])  # fmt: skip
    m = merge(a, b)
    assert m.code_instructions == "faster\nhigher jump" and m.add_qa_checks == ["check x"]
    assert [c.prompt for c in m.art_changes] == ["blue"]
    # Saved revisions from before suggested checks load.
    old = a.model_dump()
    del old["add_qa_checks"]
    assert Revision.model_validate(old).add_qa_checks == []


def test_suggested_checks_are_in_the_qa_prompt():
    code = GameCode.model_validate({"files": [{"path": "game.js", "content": "x"}], "notes": ""})
    prompt = check_prompt(design(), code, "", ["check that the bat never leaves the screen"])
    assert "write one check for each" in prompt and "* check that the bat never" in prompt
    assert "write one check for each" not in check_prompt(design(), code)


def answer_until(client, run_id, stage, seen):
    """Approve every gate until one of `stage` opens; returns that state."""
    while True:
        state = wait_for(client, run_id, lambda s: s["gate"] and s["gate"]["id"] not in seen,
                         timeout=180)  # fmt: skip
        gate = state["gate"]
        if gate["stage"] == stage:
            return state
        seen.add(gate["id"])
        url = f"/api/runs/{run_id}/gates/{gate['id']}?wait=false"
        response = client.post(url, json={"action": "approve"})
        if response.status_code == 409:
            response = client.post(url, json={"action": "submit"})
        assert response.status_code == 200, response.text


def test_chat_changes_at_the_playtest_build_the_next_version(studio):
    store, client = studio
    run_id = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True}).json()["id"]
    seen = set()
    state = answer_until(client, run_id, "playtest", seen)
    assert state["queued"] == []
    report = json.loads(next(a for a in store.artifacts(run_id)
                             if a.name == "game/v1/report.json").data)  # fmt: skip
    entity = report["art"][0]["entity"]
    say = lambda agent, text: client.post(f"/api/runs/{run_id}/agents/{agent}/messages",
                                          json={"text": text}).json()["items"]  # fmt: skip

    unplaced = say("Pixel Artist", "make it shinier")
    assert unplaced[0]["status"] == "unplaced" and "Which one" in unplaced[1]["text"]
    art = say("Pixel Artist", f"make the {entity.replace('_', ' ')} purple")
    assert art[0]["status"] == "queued" and "Request changes" in art[1]["text"]
    check = say("QA Playtester", "check that the hero never falls through the floor")
    assert check[0]["intent"] == "change" and check[0]["status"] == "queued"
    state = client.get(f"/api/runs/{run_id}/agents").json()
    assert [c["agent"] for c in state["queued"]] == ["Pixel Artist", "QA Playtester"]
    calls = by_name(state)["Director"]["calls"]

    # Request changes with no text: the queued changes are the whole revision.
    gate = state["gate"]
    seen.add(gate["id"])
    response = client.post(f"/api/runs/{run_id}/gates/{gate['id']}?wait=false",
                           json={"action": "request_changes", "text": ""})  # fmt: skip
    assert response.status_code == 200, response.text
    state = answer_until(client, run_id, "playtest", seen)
    names = {a.name: a for a in store.artifacts(run_id)}
    revision = json.loads(names["game/v2/revision.json"].data)
    assert [c["entity"] for c in revision["art_changes"]] == [entity]
    assert revision["add_qa_checks"] == ["check that the hero never falls through the floor"]
    assert revision["code_instructions"] == ""
    assert by_name(state)["Director"]["calls"] == calls  # nobody had to route it
    report = json.loads(names["game/v2/report.json"].data)
    assert "checks you suggested" in report["qa"]["note"]
    assert state["queued"] == []
    thread = client.get(f"/api/runs/{run_id}/agents/Pixel Artist/thread").json()["items"]
    mine = [i for i in thread if i["kind"] == "chat" and i["role"] == "user"]
    assert [i.get("status") for i in mine] == ["unplaced", "applied"]
