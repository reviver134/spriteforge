"""G3 asset production: one checkpointed task per step, explicit targeted revisions."""

import json

from spriteforge.agents.models import ChangeRequest, Changes
from spriteforge.core.cost import BudgetExceeded
from spriteforge.core.gates import Gates, new_gate

from .asset_context import ProductionContext, make_manifest
from .asset_models import Manifest, ProductionPlan, dependents
from .asset_render import generate_task
from .asset_review import review_set
from .asset_store import ProductionStore


def enable_production(store, run_id):
    """Opt in to G3 without changing any prior gate answer or approved art."""
    run = store.get_run(run_id)
    if run.config.get("workflow") != "intake-concept-v1":
        raise ValueError("Asset production requires an intake/concept run")
    if run.status in {"aborted", "budget_exhausted"}:
        raise ValueError("This run cannot start production")
    if run.config.get("asset_production") and run.status != "completed":
        return
    names = {a.name for a in store.artifacts(run_id)}
    if (
        run.status == "completed"
        and not {"brief.json", "style.json", "anchor.png", "approval.json"} <= names
    ):
        raise ValueError("Production needs an approved G2 style and anchor")
    config = {
        **run.config,
        "asset_production": True,
        "preview_production_enabled": run.config.get("gameplay_preview", False),
    }
    with store.connect() as db:
        db.execute(
            "UPDATE runs SET config=?,status=? WHERE id=?",
            (json.dumps(config), "running" if run.status == "completed" else run.status, run_id),
        )
        store._event(db, run_id, "Human", "production.enabled", {})


def selected_targets(store, run_id, feedback):
    ctx = ProductionStore(store, run_id)
    manifest = Manifest.model_validate(ctx.read("production/manifest.json"))
    value = feedback.form.get("targets", "")
    if not isinstance(value, str):
        raise ValueError("Asset targets must be comma-separated ids")  # noqa: TRY004 -- UI validation
    selected = {s.strip() for s in value.split(",") if s.strip()}
    return selected, dependents(manifest.tasks(), selected)


def production_step(store, run_id):
    ctx = ProductionContext(store, run_id)
    try:
        return _step(ctx)
    except BudgetExceeded as exc:
        message = f"Asset production stopped: {exc}. Accepted assets and attempts are saved; no more provider calls will be made."
        with store.connect() as db:
            db.execute("UPDATE runs SET status='budget_exhausted' WHERE id=?", (run_id,))
            store._event(
                db, run_id, "Director", "production.budget_exhausted", {"message": message}
            )
        return False


def _step(ctx):
    ctx.restore_files()
    gates = Gates(ctx.store)
    history = gates.all(ctx.run_id)
    last = history[-1]
    manifest = make_manifest(ctx)
    tasks = manifest.tasks()
    if last.stage == "assets" and last.status == "approved":
        ctx.save(
            "production/approval.json",
            {"gate": last.id, "assets": {k: r.version for k, r in ctx.records().items()}},
            "Director",
        )
        ctx.store.advance(ctx.run_id, ctx.run.cursor, "G3 approved; assets locked", final=True)
        return False
    if last.stage == "assets" and last.status == "changes_requested":
        selected, affected = selected_targets(ctx.store, ctx.run_id, last.response)
        changes = Changes(
            requests=[
                ChangeRequest(
                    target=sorted(affected),
                    instruction=last.response.text,
                    severity="minor",
                    scope="Selected: "
                    + ", ".join(sorted(selected))
                    + ". Regenerate these assets and their dependent frames/references: "
                    + ", ".join(sorted(affected)),
                    needs_regen=True,
                )
            ]
        )
        name = f"production/changes/{last.id}.json"
        ctx.save(name, changes.model_dump(), "Director")
        gates.open(
            ctx.run_id,
            new_gate(
                "asset_revise",
                "Confirm asset changes and dependent frames",
                [ctx.artifacts[name].id],
                kind="confirm_changes",
                parent_id=last.id,
                changes=changes,
            ),
        )
        return False
    confirmations = [g for g in history if g.stage == "asset_revise" and g.status == "approved"]
    cycle = len(confirmations)
    plan_name = f"production/cycles/{cycle}/plan.json"
    if plan_name in ctx.artifacts:
        plan = ProductionPlan.model_validate(ctx.read(plan_name))
    elif cycle:
        previous = ProductionPlan.model_validate(
            ctx.read(f"production/cycles/{cycle - 1}/plan.json")
        )
        plan = ProductionPlan(versions=dict(previous.versions), instructions={})
        for change in confirmations[-1].changes.requests:
            if not isinstance(change.target, list):
                raise ValueError("G3 revisions must name specific asset ids")  # noqa: TRY004
            for target in change.target:
                plan.versions[target] += 1
                plan.instructions[target] = change.instruction
        ctx.save(plan_name, plan.model_dump(), "Director")
    else:
        records = ctx.records()
        plan = ProductionPlan(
            versions={t.id: records[t.id].version if t.id in records else 1 for t in tasks},
            instructions={},
        )
        ctx.save(plan_name, plan.model_dump(), "Director")
    records = ctx.records()
    for task in tasks:
        if task.id not in records or records[task.id].version < plan.versions[task.id]:
            generate_task(ctx, task, plan)
            return True
    names = review_set(ctx, manifest, cycle)
    gates.open(
        ctx.run_id,
        new_gate(
            "assets",
            "G3 Asset contact sheet",
            [ctx.artifacts[n].id for n in names],
            form_schema={
                "type": "object",
                "properties": {
                    "targets": {
                        "type": "string",
                        "title": "Asset ids to change (comma-separated)",
                        "default": "",
                        "suggestions": [s.id for s in manifest.assets],
                    }
                },
            },
        ),
    )
    return False
