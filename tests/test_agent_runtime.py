import asyncio
import json
import shutil
import time

import pytest
from pydantic import ValidationError

from spriteforge.agents.assets import AssetResult, Assets, GenerateAsset
from spriteforge.agents.models import RoleOutput, StagePlan
from spriteforge.agents.roles import load_roles
from spriteforge.agents.runtime import AgentRuntime, AgentTask, Tool
from spriteforge.agents.workflow import advance_until_gate, mock_plan, start_run, workflow_step
from spriteforge.config import BudgetConfig
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Feedback, Gates
from spriteforge.core.state import Store
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider


@pytest.fixture
def agent_context(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    run = start_run(store, "top-down dungeon crawler", BudgetConfig())
    budget = Budget(store, run.id, BudgetConfig())
    memory = KnowledgeStore(tmp_path / "memory", store, run.id)
    return store, run, budget, memory


def test_runtime_tools_schema_retry_memory_and_replay(agent_context):
    store, run, budget, memory = agent_context
    calls = []
    role = load_roles()["Pixel Artist"]
    role.allowed_skills = ["pixels"]
    role.required_skills = ["pixels"]
    provider = MockLLMProvider(
        budget,
        [
            ChatResult(
                tool_calls=[
                    {
                        "id": "1",
                        "name": "generate_asset",
                        "arguments": {"asset_id": "hero", "instruction": "round"},
                    }
                ]
            ),
            {"wrong": "schema"},
            {"summary": "Done", "asset_ids": ["hero"]},
        ],
    )

    def tool(arguments):
        calls.append(arguments)
        return AssetResult(asset_id="hero", artifact_id="image", version=1)

    runtime = AgentRuntime(
        store,
        run.id,
        memory,
        provider,
        {"generate_asset": Tool(GenerateAsset, AssetResult, tool)},
        {"pixels": "Use readable silhouettes"},
    )
    task = AgentTask(id="test", goal="hero", memory_budget=80)
    output = asyncio.run(runtime.run(role, task, RoleOutput))
    assert RoleOutput.model_validate_json(output.data).asset_ids == ["hero"]
    assert len(calls) == 1
    events = store.events(run.id)
    assert next(e for e in events if e.type == "agent.memory").payload == {
        "ids": [],
        "scopes": ["project"],
    }
    assert len([e for e in events if e.type == "agent.step"]) == 3
    assert asyncio.run(runtime.run(role, task, RoleOutput)) == output
    assert store.events(run.id) == events


@pytest.mark.parametrize("case", ["tool", "schema", "steps", "timeout"])
def test_runtime_limits_and_denials(agent_context, case):
    store, run, budget, memory = agent_context
    role = load_roles()["Director"]
    responses = [ChatResult(tool_calls=[{"id": "1", "name": "forbidden", "arguments": {}}])]
    expected = PermissionError
    if case == "schema":
        responses, expected = [{"bad": True}] * 3, ValidationError
    elif case == "steps":
        role.max_steps = 1
        responses, expected = [{"bad": True}], RuntimeError
    provider = MockLLMProvider(budget, responses)
    if case == "timeout":

        async def slow(request):
            await asyncio.sleep(1)

        provider.chat, expected = slow, TimeoutError
    runtime = AgentRuntime(store, run.id, memory, provider)
    before = time.monotonic()
    with pytest.raises(expected):
        asyncio.run(
            runtime.run(
                role, AgentTask(goal="test"), RoleOutput, timeout=0.02 if case == "timeout" else 5
            )
        )
    assert time.monotonic() - before < 3
    assert not store.artifacts(run.id)
    assert store.events(run.id)[-1].type == "agent.failed"


def test_dag_rejects_cycles_missing_dependencies_and_duplicate_ids():
    for stages in [
        [{"id": "a", "depends_on": ["b"]}, {"id": "b", "depends_on": ["a"]}],
        [{"id": "a", "depends_on": ["unknown"]}],
        [{"id": "a"}, {"id": "a"}],
    ]:
        with pytest.raises(ValidationError):
            StagePlan(stages=[{**s, "role": "Director", "goal": "Test"} for s in stages])


def test_roles_are_snapshotted_per_run(tmp_path):
    from pathlib import Path

    import spriteforge.agents.roles as roles_module

    directory = tmp_path / "roles"
    shutil.copytree(Path(roles_module.__file__).with_name("roles"), directory)
    store = Store(tmp_path / "state.sqlite3")
    first = start_run(store, "test", BudgetConfig(), directory)
    path = directory / "director.md"
    path.write_text(path.read_text() + "\nChanged next run.\n")
    second = start_run(store, "test", BudgetConfig(), directory)
    assert "Changed next run" not in first.config["roles"]["Director"]["prompt"]
    assert "Changed next run" in second.config["roles"]["Director"]["prompt"]


def test_scripted_gates_restart_targeted_changes(agent_context, scripted_human):
    store, run, budget, _ = agent_context
    human = scripted_human(
        [
            Feedback(action="approve", form={"brief": "a dungeon"}),
            Feedback(action="approve"),
            Feedback(action="request_changes", tags=["art"], text="make the hero rounder"),
            Feedback(action="approve"),  # parsed requests confirmation
            Feedback(action="approve"),  # revised contact sheet
            Feedback(action="approve"),
            Feedback(action="approve"),
        ]
    )
    advance_until_gate(store, run.id)
    assert Gates(store).pending(run.id).title == "G1 Brief"
    for _ in range(2):
        human.answer(Gates(store), run.id)
        advance_until_gate(store, run.id)
    assert Gates(store).pending(run.id).title == "G3 Asset contact sheet"
    assets = Assets(store, run.id, MockImageProvider(budget))
    before = assets.records()
    image_bytes = {a.id: a.data for a in store.artifacts(run.id) if a.mime == "image/png"}
    human.answer(Gates(store), run.id)
    advance_until_gate(store, run.id)
    confirmation = Gates(store).pending(run.id)
    assert confirmation.kind == "confirm_changes"
    assert confirmation.changes.requests[0].target == ["hero"]
    assert confirmation.changes.requests[0].needs_regen
    assert assets.records() == before
    # A fresh Store, runtime, and provider see the exact same paused request.
    restored = Store(store.path)
    events = restored.events(run.id)
    assert not workflow_step(restored, run.id)
    assert restored.get_run(run.id).status == "paused"
    assert Gates(restored).pending(run.id) == confirmation
    assert restored.events(run.id) == events
    human.answer(Gates(restored), run.id)
    advance_until_gate(restored, run.id)
    after = assets.records()
    assert after["tile"] == before["tile"]
    assert after["hero"].version == 2
    assert after["hero"].artifact_id != before["hero"].artifact_id
    assert all(
        next(a.data for a in restored.artifacts(run.id) if a.id == ident) == data
        for ident, data in image_bytes.items()
    )
    while restored.get_run(run.id).status != "completed":
        human.answer(Gates(restored), run.id)
        advance_until_gate(restored, run.id)
    assert (
        len(
            [e for e in restored.events(run.id) if e.type == "cost.reserved" and e.payload["image"]]
        )
        == 3
    )
    # Every role this workflow has a stage for (the game pipeline's Art Reviewer has none).
    stage_roles = {"Director"} | {s.role for s in mock_plan().stages if s.role}
    assert {e.agent for e in restored.events(run.id) if e.type == "agent.completed"} == (
        stage_roles
    )
    assert stage_roles <= set(load_roles())
    count = len(restored.events(run.id))
    assert not workflow_step(restored, run.id)
    assert len(restored.events(run.id)) == count


def test_gate_abort_duplicate_and_invalid_form(agent_context):
    store, run, _, _ = agent_context
    advance_until_gate(store, run.id)
    gates = Gates(store)
    gate = gates.pending(run.id)
    with pytest.raises(ValueError):
        gates.respond(run.id, gate.id, Feedback(action="approve", form={"unknown": 4}))
    gates.respond(run.id, gate.id, Feedback(action="abort"))
    with pytest.raises(ValueError):
        gates.respond(run.id, gate.id, Feedback(action="approve"))
    assert not workflow_step(store, run.id)
    assert store.get_run(run.id).status == "aborted"


def test_librarian_data_is_redacted_before_provider(agent_context, monkeypatch):
    store, run, _, memory = agent_context
    monkeypatch.setenv("META_API_KEY", "fake-api-secret")

    class CheckingProvider:
        def chat(self, request):
            assert "fake-api-secret" not in request.model_dump_json()
            return ChatResult(content=json.dumps({"summary": "Safe"}))

    runtime = AgentRuntime(store, run.id, memory, CheckingProvider())
    asyncio.run(
        runtime.run(load_roles()["Librarian"], AgentTask(goal="fake-api-secret"), RoleOutput)
    )


def test_synchronous_provider_timeout_returns_promptly(agent_context):
    store, run, _, memory = agent_context

    class SlowProvider:
        def chat(self, request):
            time.sleep(0.3)
            return ChatResult(content='{"summary":"Late"}')

    runtime = AgentRuntime(store, run.id, memory, SlowProvider())
    before = time.monotonic()
    with pytest.raises(TimeoutError):
        asyncio.run(
            runtime.run(load_roles()["Director"], AgentTask(goal="test"), RoleOutput, timeout=0.03)
        )
    assert time.monotonic() - before < 0.25
    assert not store.artifacts(run.id)
