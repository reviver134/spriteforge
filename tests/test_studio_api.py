"""Phase 1 of the studio (docs/ui-plan.md): projects, one worker per run, the agents of a run,
their threads and chat messages."""

import time

import pytest

from spriteforge.app.agents_view import AGENTS, actor
from spriteforge.app.chat import ChatAnswer, answer_question, classify
from spriteforge.config import BudgetConfig, init_workspace
from spriteforge.core.acting import acting
from spriteforge.core.cost import Budget
from spriteforge.core.state import Artifact, Event, Store


def wait_for(client, run_id, done, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = client.get(f"/api/runs/{run_id}/agents").json()
        if done(state):
            return state
        time.sleep(0.05)
    raise AssertionError(f"timed out: {state['status']} ({state['error']}), gate {state['gate']}")


def by_name(state):
    return {a["name"]: a for a in state["agents"]}


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("Why is the jump so short?", "question"),
        ("what does the boss do", "question"),
        ("make the bat purple", "change"),
        ("Can you make the bat purple?", "change"),
        ("The slime should be green.", "change"),
        ("is level 2 harder than level 1?", "question"),
    ],
)
def test_classify(text, intent):
    assert classify(text) == intent


@pytest.mark.parametrize(
    ("agent", "kind", "payload", "expected"),
    [
        ("Director", "game.progress", {"message": "build v1: the Programmer is writing"}, "Programmer"),
        ("Director", "game.progress", {"message": "attempt 2: failed at playtest"}, "Programmer"),
        ("Director", "game.progress", {"message": "build v2: art (library first, …)"}, "Pixel Artist"),
        ("Director", "game.progress", {"message": "build v1: the menus (v1)"}, "Menu Designer"),
        ("Director", "game.progress", {"message": "build v1: recording the autoplay"}, "QA Playtester"),
        ("Director", "game.progress", {"message": "starting"}, "Director"),
        ("Level/Tile Designer", "game.progress", {"message": "writing level 1"}, "Level/Tile Designer"),
        ("meta", "cost.reserved", {"agent": "Sound Director"}, "Sound Director"),
        ("meta", "cost.reserved", {}, None),
        ("Librarian", "memory.retrieved", {}, None),
        ("Human", "gate.responded", {}, None),
    ],
)  # fmt: skip
def test_actor_names_the_agent_at_work(agent, kind, payload, expected):
    event = Event(id=1, run_id="r", timestamp="t", agent=agent, type=kind, payload=payload)
    assert actor(event) == expected


def test_cost_ledger_records_the_acting_agent(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({})
    budget = Budget(store, run.id, BudgetConfig())
    with acting("Art Director"):
        budget.reserve("meta", "muse-spark")
    budget.reserve("meta", "muse-spark")
    agents = [e.payload.get("agent") for e in store.events(run.id) if e.type == "cost.reserved"]
    assert agents == ["Art Director", None]


def test_a_question_carries_only_that_agents_work(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = store.create_run({"mock": True})

    def save(name, text, agent, mime="text/markdown"):
        artifact = Artifact(id=name, run_id=run.id, name=name, mime=mime, data=text.encode())
        store.save_artifact(artifact, agent, "artifact.created")

    save("game/plan/v1.md", "jump height 2 tiles", "Game Designer")
    save("game/plan/v2.md", "jump height 3 tiles", "Game Designer")
    save("game/plan/v2.json", '{"jump": 3}', "Game Designer", "application/json")
    save("game/sounds/v1.md", "boing", "Sound Director")
    store.append_event(run.id, "Human", "chat.user", {"to": "Game Designer", "text": "hi"})
    store.append_event(run.id, "Game Designer", "chat.agent", {"text": "hello"})

    class Models:
        def ask(self, role, prompt, schema, fallback, task):
            self.seen = (role, prompt, task)
            return ChatAnswer(answer="Three tiles.")

    models = Models()
    reply = answer_question(store, run.id, "Game Designer", "How high is the jump?", models)
    role, prompt, task = models.seen
    assert (reply, role, task) == ("Three tiles.", "Game Designer", "chat")
    assert "jump height 3 tiles" in prompt and "game/plan/v2.md" in prompt
    assert "2 tiles" not in prompt and '{"jump"' not in prompt and "boing" not in prompt
    assert "Human: hi\nGame Designer: hello" in prompt
    assert prompt.endswith("How high is the jump?")


def test_live_project_without_a_key_offers_mock(studio):
    _, client = studio
    response = client.post("/api/projects", json={"text": "a cave game"})
    assert response.status_code == 409 and "offline (mock)" in response.json()["detail"]


def test_two_projects_have_their_own_workers(studio):
    _store, client = studio
    ids = [
        client.post("/api/projects", json={"text": idea, "mock": True}).json()["id"]
        for idea in ("a slime in a cave", "a bat in a belfry")
    ]
    for ident in ids:
        state = wait_for(client, ident, lambda s: s["gate"])
        assert state["gate"]["stage"] == "genre"
    projects = client.get("/api/projects").json()["projects"]
    # Newest first; the memory corpus's audit run is a tool, not a project.
    assert [p["idea"] for p in projects] == ["a bat in a belfry", "a slime in a cave"]
    assert all(p["gate"]["stage"] == "genre" and p["mock"] for p in projects)


def test_project_from_idea_to_final_through_agent_threads(studio):
    _store, client = studio
    created = client.post("/api/projects", json={"text": "a slime in a cave", "mock": True})
    assert created.status_code == 201
    run_id = created.json()["id"]
    state = wait_for(client, run_id, lambda s: s["gate"])
    agents = by_name(state)
    assert [a["name"] for a in state["agents"]] == AGENTS
    assert agents["Director"]["status"] == "needs_you" and agents["Director"]["needs_you"] == 1
    assert agents["Game Designer"]["status"] == "done" and agents["Game Designer"]["calls"] == 1
    assert agents["Programmer"]["status"] == "waiting"

    director = client.get(f"/api/runs/{run_id}/agents/Director/thread").json()["items"]
    assert [i["kind"] for i in director] == ["run", "chat", "gate"]
    assert director[1]["text"] == "a slime in a cave" and director[2]["gate"]["stage"] == "genre"
    # The first gate is the Director's alone.
    designer = client.get(f"/api/runs/{run_id}/agents/Game Designer/thread").json()["items"]
    assert "gate" not in [i["kind"] for i in designer]

    question = client.post(f"/api/runs/{run_id}/agents/Game Designer/messages",
                           json={"text": "Why this genre?"}).json()["items"]  # fmt: skip
    assert [(i["role"], i["intent"]) for i in question] == [
        ("user", "question"),
        ("agent", "question"),
    ]
    assert "Offline mock" in question[1]["text"]
    change = client.post(f"/api/runs/{run_id}/agents/Level%2FTile Designer/messages",
                         json={"text": "make level 2 shorter"}).json()["items"]  # fmt: skip
    # The Level Designer has not run: the change waits for its first run.
    assert change[0]["status"] == "queued" and change[1]["agent"] == "Level/Tile Designer"
    assert "first run" in change[1]["text"]
    after = question[-1]["id"]
    tail = client.get(f"/api/runs/{run_id}/agents/Level/Tile Designer/thread?after={after}")
    assert next(i["text"] for i in tail.json()["items"]) == "make level 2 shorter"
    assert client.get(f"/api/runs/{run_id}/agents/Nobody/thread").status_code == 404

    seen = {}
    while True:
        state = wait_for(client, run_id, lambda s: s["status"] == "completed" or (
            s["gate"] and s["gate"]["id"] not in seen), timeout=180)  # fmt: skip
        if state["status"] == "completed":
            break
        gate = state["gate"]
        seen[gate["id"]] = {a["name"] for a in state["agents"] if a["status"] == "needs_you"}
        url = f"/api/runs/{run_id}/gates/{gate['id']}?wait=false"
        response = client.post(url, json={"action": "approve"})
        if response.status_code == 409:
            response = client.post(url, json={"action": "submit"})
        assert response.status_code == 200, response.text
    waits = list(seen.values())
    assert {"Director", "Game Designer"} in waits
    assert {"Director", "Menu Designer"} in waits
    assert {"Director", "Programmer", "QA Playtester"} in waits
    assert {"Director", "Librarian"} in waits

    agents = by_name(state)
    # Nobody is left working or waiting on the human; an agent the game did not need (the
    # Level/Tile Designer of a one-screen game) only chatted, which is not work: "waiting".
    assert all(a["status"] in {"done", "waiting"} for a in agents.values())
    assert agents["Programmer"]["status"] == "done" and agents["Librarian"]["status"] == "done"
    assert agents["Pixel Artist"]["images"] > 0 and agents["Pixel Artist"]["cost"] > 0
    assert agents["Programmer"]["calls"] >= 1 and agents["Sound Director"]["calls"] >= 1
    programmer = client.get(f"/api/runs/{run_id}/agents/Programmer/thread").json()["items"]
    assert any(i["kind"] == "gate" and i["gate"]["stage"] == "playtest" for i in programmer)
    assert any(i["kind"] == "progress" and "Programmer" in i["text"] for i in programmer)
    project = client.get(f"/api/runs/{run_id}/project").json()
    assert project["status"] == "completed" and project["thumbnail"] and project["cost"] > 0
    assert project["title"] != "a slime in a cave"  # the design's title
    assert client.post(f"/api/runs/{run_id}/resume").status_code == 409
    level = client.get(f"/api/runs/{run_id}/agents/Level/Tile Designer/thread").json()["items"]
    asked = next(i for i in level if i["kind"] == "chat" and i["role"] == "user")
    assert asked["status"] == "applied"


def test_two_runs_open_a_new_memory_corpus_at_once(tmp_path):
    """Two first projects in a new workspace both open (and `git init`) the memory corpus."""
    import threading

    from spriteforge.memory.okf.seed import seed
    from spriteforge.memory.okf.store import KnowledgeStore

    for n in range(3):
        root = init_workspace(tmp_path / str(n))
        store = Store(root / "state.sqlite3")
        errors = []

        def open_and_seed(run_id, root=root, store=store, errors=errors):
            try:
                seed(KnowledgeStore(root / "memory", store, run_id))
            except Exception as exc:  # noqa: BLE001 -- collected for the assertion
                errors.append(exc)

        threads = [
            threading.Thread(target=open_and_seed, args=(store.create_run({}).id,))
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
