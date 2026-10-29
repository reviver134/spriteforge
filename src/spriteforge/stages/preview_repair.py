"""Repair a defective unapproved draft without repainting or altering its history."""

from spriteforge.core.gates import Gates
from spriteforge.memory.okf.git import write_lock

from .preview_assets import PreviewContext
from .preview_models import PreviewPlan


def reprocess_draft(
    store,
    run_id,
    targets,
    reason,
    layout_instruction="",
    *,
    generate_targets=(),
    canvas_overrides=None,
):
    with write_lock(store.path.parent / f".{run_id}.runtime.lock"):
        ctx = PreviewContext(store, run_id)
        gate = Gates(store).pending(run_id)
        if not gate or gate.stage != "gameplay":
            raise ValueError("Repairs require an unapproved gameplay review")
        report = next(
            a
            for a in ctx.artifacts.values()
            if a.id in gate.artifacts_to_show and a.name.endswith("/readability.json")
        )
        previous = ctx.read(report.name)["version"]
        plan = PreviewPlan.model_validate(ctx.read(f"preview/v{previous}/plan.json"))
        targets = list(dict.fromkeys([*targets, *generate_targets]))
        if set(targets) - plan.versions.keys():
            raise ValueError("Repair targets must name existing assets")
        if generate_targets and not reason.startswith("User requested"):
            raise ValueError(
                "New generation during draft repair requires a user-requested revision"
            )
        plan.version += 1
        plan.recolor = False
        plan.reprocess_targets = [ident for ident in targets if ident not in generate_targets]
        plan.canvas_overrides.update(canvas_overrides or {})
        plan.instructions = {ident: reason for ident in targets}
        plan.layout_instruction = layout_instruction
        for ident in targets:
            plan.versions[ident] += 1
        ctx.save(f"preview/v{plan.version}/plan.json", plan.model_dump(), "QA Playtester")
        ctx.save(
            f"preview/v{plan.version}/repair.json",
            {
                "reason": reason,
                "targets": targets,
                "previous_gate": gate.id,
                "operation": "user-requested targeted revision"
                if generate_targets
                else "reprocess saved source; no image calls",
                "generate_targets": list(generate_targets),
            },
            "QA Playtester",
        )
        Gates(store).supersede_preview(run_id, gate.id, reason)
