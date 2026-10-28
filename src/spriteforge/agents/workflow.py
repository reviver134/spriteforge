"""Offline agent-runtime acceptance workflow, resumed entirely from local artifacts and gates."""

import asyncio
from pathlib import Path

from pydantic import BaseModel, Field

from spriteforge.config import BudgetConfig
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates, new_gate
from spriteforge.core.state import Store
from spriteforge.memory.okf.git import write_lock
from spriteforge.memory.okf.seed import seed
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.providers.base import ChatResult
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider

from .assets import AssetResult, Assets, GenerateAsset
from .director import Director
from .models import Changes, RoleOutput, StageNode, StagePlan
from .roles import Role, load_roles
from .runtime import AgentRuntime, AgentTask, Tool

WORKFLOW = "agent-runtime-mock-v1"


class Flow(BaseModel):
    roles: dict[str, Role]
    plan: StagePlan | None = None
    outputs: dict[str, str] = Field(default_factory=dict)
    completed: list[str] = Field(default_factory=list)
    handled: list[str] = Field(default_factory=list)
    changes: Changes | None = None
    change_artifact: str | None = None
    rework: StagePlan | None = None
    rework_done: list[str] = Field(default_factory=list)
    revisions: dict[str, int] = Field(default_factory=dict)
    source_gate: str | None = None


def start_run(store: Store, goal: str, budget: BudgetConfig, roles_directory: Path | None = None):
    # Snapshot user edits for this run; subsequent runs reload the Markdown files.
    roles = load_roles(roles_directory)
    required = {
        "Director",
        "Game Designer",
        "Art Director",
        "Pixel Artist",
        "Level/Tile Designer",
        "Programmer",
        "QA Playtester",
        "Librarian",
    }
    if required - roles.keys():
        raise ValueError("Missing required roles")
    run = store.create_run(
        {
            "workflow": WORKFLOW,
            "goal": goal,
            "budget": budget.model_dump(),
            "roles": {name: role.model_dump() for name, role in roles.items()},
        }
    )
    Gates(store)
    return run


def mock_plan() -> StagePlan:
    definitions = [
        ("brief", None, "G1 Brief"),
        ("design", "Game Designer", None),
        ("art", "Art Director", None),
        ("concept", None, "G2 Concept preview"),
        ("assets", "Pixel Artist", None),
        ("contact", None, "G3 Asset contact sheet"),
        ("level", "Level/Tile Designer", None),
        ("code", "Programmer", None),
        ("qa", "QA Playtester", None),
        ("playtest", None, "G4 Playtest"),
        ("memory", "Librarian", None),
        ("final", None, "G5 Final review"),
    ]
    return StagePlan(
        stages=[
            StageNode(
                id=ident,
                role=role,
                gate=gate,
                goal=gate or f"Produce mock {role} artifact",
                depends_on=[definitions[i - 1][0]] if i else [],
            )
            for i, (ident, role, gate) in enumerate(definitions)
        ]
    )


def mock_changes(text: str, records: dict[str, AssetResult]) -> Changes:
    """Scripted offline Director behavior; unrecognized feedback stays conservative."""
    lowered = text.casefold()
    named = [ident for ident in records if ident.casefold() in lowered]
    if named:
        target, regen = named, True
    elif "style" in lowered or "palette" in lowered:
        target, regen = "style", False
    elif "level" in lowered or "room" in lowered:
        target, regen = "level", False
    else:
        target, regen = "mechanic", False
    return Changes(
        requests=[
            {
                "target": target,
                "instruction": text,
                "severity": "minor",
                "scope": "Only the listed targets",
                "needs_regen": regen,
            }
        ]
    )


def mock_replan(changes: Changes) -> StagePlan:
    stages = []
    for i, change in enumerate(changes.requests):
        role = (
            "Pixel Artist"
            if change.needs_regen
            else {
                "style": "Art Director",
                "level": "Level/Tile Designer",
                "mechanic": "Game Designer",
            }.get(str(change.target), "Art Director")
        )
        stages.append(
            StageNode(
                id=f"revision-{i}",
                role=role,
                goal=change.instruction,
                depends_on=[f"revision-{i - 1}"] if i else [],
            )
        )
    return StagePlan(stages=stages)


def workflow_step(store: Store, run_id: str) -> bool:
    with write_lock(store.path.parent / f".{run_id}.runtime.lock"):
        return asyncio.run(_step(store, run_id))


async def _step(store: Store, run_id: str) -> bool:
    run = store.get_run(run_id)
    if run.config.get("workflow") != WORKFLOW:
        raise ValueError("Unknown runtime workflow")
    if run.status in {"completed", "aborted"}:
        return False
    gates = Gates(store)
    if gates.pending(run_id):
        return False
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS agent_flows (run_id TEXT PRIMARY KEY, body TEXT)")
        row = db.execute("SELECT body FROM agent_flows WHERE run_id=?", (run_id,)).fetchone()
    flow = (
        Flow.model_validate_json(row[0])
        if row
        else Flow(roles={n: Role.model_validate(r) for n, r in run.config["roles"].items()})
    )
    budget = Budget(store, run_id, BudgetConfig.model_validate(run.config["budget"]))
    assets = Assets(store, run_id, MockImageProvider(budget))
    memory = KnowledgeStore(store.path.parent / "memory", store, run_id)
    seed(memory)

    def runtime(role: Role, responses: list, tools=None) -> AgentRuntime:
        if role.model != "mock":
            raise ValueError(
                "The offline workflow requires model: mock; inject a provider in AgentRuntime"
            )
        return AgentRuntime(store, run_id, memory, MockLLMProvider(budget, responses), tools)

    def director(responses):
        return Director(
            runtime(flow.roles["Director"], responses), flow.roles["Director"], flow.roles
        )

    def save(kind="workflow.progress", final=False):
        with store.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO agent_flows VALUES(?,?)", (run_id, flow.model_dump_json())
            )
            db.execute(
                "UPDATE runs SET cursor=cursor+1,status=? WHERE id=?",
                ("completed" if final else "running", run_id),
            )
            store._event(db, run_id, "Director", kind, {"completed": flow.completed})

    if flow.plan is None:
        flow.plan, ident = await director([mock_plan().model_dump()]).plan(
            AgentTask(id="initial-plan", goal=run.config["goal"])
        )
        flow.outputs["plan"] = ident
        save("director.planned")
        return True

    async def execute(stage: StageNode, task_id: str, inputs: list[str], rework=False):
        role = flow.roles[stage.role]
        tools, responses = {}, []
        expected_assets = set(assets.records())
        if stage.role == "Pixel Artist":
            allowed = set(flow.revisions) if rework else {"hero", "tile"}
            expected_assets.update(allowed)
            for index, ident in enumerate(sorted(allowed)):
                responses.append(
                    ChatResult(
                        tool_calls=[
                            {
                                "id": f"image-{index}",
                                "name": "generate_asset",
                                "arguments": {
                                    "asset_id": ident,
                                    "instruction": stage.goal + " " + ident,
                                },
                            }
                        ]
                    )
                )
            tools["generate_asset"] = Tool(
                GenerateAsset,
                AssetResult,
                lambda request: assets.generate(
                    request, allowed=allowed, revisions=flow.revisions if rework else None
                ),
            )
        responses.append(
            RoleOutput(
                summary=stage.goal + " (offline mock)", asset_ids=sorted(expected_assets)
            ).model_dump()
        )
        result = await Director(
            runtime(role, responses, tools), flow.roles["Director"], flow.roles
        ).dispatch(stage, AgentTask(id=task_id, goal=stage.goal, inputs=inputs), RoleOutput)
        return result.id

    # Handle answered gates before dispatching another stage. All interpretation and
    # re-planning results are persisted artifacts, and replay has deterministic task ids.
    answered = next(
        (g for g in gates.all(run_id) if g.status != "pending" and g.id not in flow.handled), None
    )
    if answered:
        if answered.status == "aborted":
            return False
        feedback_artifact = next(
            a.id for a in store.artifacts(run_id) if a.name == f"feedback/{answered.id}.json"
        )
        if answered.status == "changes_requested":
            changes = mock_changes(answered.response.text, assets.records())
            flow.changes, flow.change_artifact = await director([changes.model_dump()]).feedback(
                AgentTask(
                    id=f"interpret-{answered.id}",
                    goal=answered.response.text,
                    inputs=[feedback_artifact],
                ),
                set(assets.records()),
            )
            flow.source_gate = answered.id
            flow.handled.append(answered.id)
            save("director.changes_proposed")
            gates.open(
                run_id,
                new_gate(
                    answered.stage,
                    "Confirm parsed change requests",
                    [flow.change_artifact],
                    kind="confirm_changes",
                    parent_id=answered.id,
                    changes=flow.changes,
                ),
            )
            return True
        if answered.kind == "confirm_changes":
            flow.rework, plan_id = await director([mock_replan(flow.changes).model_dump()]).replan(
                AgentTask(
                    id=f"replan-{answered.id}",
                    goal="Re-plan only the confirmed changes",
                    inputs=[flow.change_artifact, feedback_artifact],
                ),
                flow.changes,
            )
            flow.outputs[f"replan-{answered.id}"] = plan_id
            flow.rework_done = []
            flow.revisions = {
                ident: assets.records()[ident].version + 1
                for change in flow.changes.requests
                if change.needs_regen
                for ident in change.target
            }
            flow.handled.append(answered.id)
            save("director.replanned")
            return True
        flow.completed.append(answered.stage)
        flow.outputs[f"gate-{answered.stage}"] = feedback_artifact
        flow.handled.append(answered.id)
        save()
        return True

    if flow.rework:
        stage = Director.ready(flow.rework, set(flow.rework_done))
        if stage:
            if stage.gate:
                raise ValueError("Revision plan must route roles only")
            ident = await execute(
                stage, f"rework-{flow.source_gate}-{stage.id}", [flow.change_artifact], rework=True
            )
            flow.outputs[f"rework-{flow.source_gate}-{stage.id}"] = ident
            flow.rework_done.append(stage.id)
            save()
            return True
        original = next(g for g in gates.all(run_id) if g.id == flow.source_gate)
        flow.rework = None
        flow.changes = None
        save("changes.applied")
        images = [r.artifact_id for r in assets.records().values()]
        sheet = assets.contact_sheet()
        gates.open(
            run_id, new_gate(original.stage, original.title, images + ([sheet] if sheet else []))
        )
        return True

    stage = Director.ready(flow.plan, set(flow.completed))
    if stage is None:
        save("run.completed", final=True)
        return False
    if stage.gate:
        previous = [g for g in gates.all(run_id) if g.stage == stage.id]
        # Crash recovery: a proposal saved immediately before opening its confirmation.
        if flow.source_gate and flow.changes and not flow.rework and previous:
            gates.open(
                run_id,
                new_gate(
                    stage.id,
                    "Confirm parsed change requests",
                    [flow.change_artifact],
                    kind="confirm_changes",
                    parent_id=flow.source_gate,
                    changes=flow.changes,
                ),
            )
        else:
            images = [r.artifact_id for r in assets.records().values()]
            sheet = assets.contact_sheet()
            kwargs = {}
            if stage.id == "brief":
                kwargs["form_schema"] = {
                    "type": "object",
                    "properties": {"brief": {"type": "string", "title": "Game brief"}},
                }
            gates.open(
                run_id,
                new_gate(
                    stage.id,
                    stage.gate,
                    images + ([sheet] if sheet else []) + list(flow.outputs.values())[-2:],
                    **kwargs,
                ),
            )
        return True
    # Include approved feedback and upstream role outputs explicitly, including
    # artifacts across intervening gates and confirmed revision artifacts.
    inputs = list(dict.fromkeys(flow.outputs.values()))
    flow.outputs[stage.id] = await execute(stage, f"stage-{stage.id}", inputs)
    flow.completed.append(stage.id)
    save()
    return True


def advance_until_gate(store: Store, run_id: str) -> None:
    while workflow_step(store, run_id):
        if store.get_run(run_id).status == "paused":
            break
