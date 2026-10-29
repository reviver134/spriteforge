"""Gate 2 gameplay preview. All image spending and final locking stay behind gates."""

from pydantic import model_validator

from spriteforge.core.cost import BudgetExceeded
from spriteforge.core.gates import Gates, new_gate

from .asset_models import Manifest, ProductionPlan
from .preview_assets import PreviewContext, asset_record, build_block, mood_board
from .preview_models import BlockDescription, BlockDescriptions, PreviewPlan, block_manifest
from .preview_review import request_change, revised_plan, room_layout


def gameplay_step(store, run_id):
    ctx = PreviewContext(store, run_id)
    try:
        return _step(ctx)
    except BudgetExceeded as exc:
        message = (
            f"Gameplay preview stopped at its call/cost cap: {exc}. Saved assets are preserved."
        )
        with store.connect() as db:
            db.execute("UPDATE runs SET status='budget_exhausted' WHERE id=?", (run_id,))
            store._event(db, run_id, "Director", "preview.budget_exhausted", {"message": message})
        return False


def open_gate(ctx, stage, title, names, **kwargs):
    Gates(ctx.store).open(
        ctx.run_id, new_gate(stage, title, [ctx.artifacts[n].id for n in names], **kwargs)
    )


def manifest_for(ctx):
    name = "preview/manifest.json"
    if name not in ctx.artifacts:
        fixture = block_manifest(ctx.brief)

        class ValidDescriptions(BlockDescriptions):
            @model_validator(mode="after")
            def required_ids(self):
                block_manifest(ctx.brief, self)
                return self

        descriptions = ctx.ask(
            "preview/descriptions-v2.json",
            "Game Designer",
            "Describe each of these exact 12 building blocks for this brief, preserving ids. "
            "Return each id and a concise visual description only. Choose two distinct enemies, three "
            "floor variants, three wall variants, two props and a key appropriate to this environment "
            "and perspective. NO baked-in UI or text. Brief: "
            + ctx.brief.model_dump_json()
            + ". Blocks: "
            + fixture.model_dump_json(),
            ValidDescriptions,
            BlockDescriptions(
                assets=[BlockDescription(id=a.id, description=a.desc) for a in fixture.assets]
            ),
        )
        manifest = block_manifest(ctx.brief, descriptions)
        ctx.save(name, manifest.model_dump(), "Game Designer")
    return Manifest.model_validate(ctx.read(name))


def approve_preview(ctx, gate, plan, manifest):
    from spriteforge.core.usage import usage_report

    records = [asset_record(ctx, ident, version) for ident, version in plan.versions.items()]
    if any(r.status != "accepted" for r in records):
        raise ValueError("Fix failing asset validators before approving this preview")
    report = ctx.read(f"preview/v{plan.version}/readability.json")
    # Include colors actually used in rendered assets and the code-drawn HUD.
    style = plan.style.model_copy(
        update={"palette": report["palette"], "max_colors": len(report["palette"])}
    )
    ctx.save("style.json", style.model_dump(), "Art Director")
    hero = next(r for r in records if r.id == "hero")
    ctx.save("anchor.png", ctx.data(hero.artifact), "Art Director", "image/png")
    locking = ProductionPlan(versions=plan.versions, instructions=plan.instructions)
    for record in records:
        ctx.lock(record, locking)
    ctx.save("preview/locked-assets.json", {r.id: r.model_dump() for r in records}, "Director")
    ctx.save(
        "preview/run-report.json", usage_report(ctx.store, ctx.run_id).model_dump(), "Director"
    )
    ctx.save(
        "approval.json",
        {
            "gate": gate.id,
            "version": plan.version,
            "screen": [320, 180],
            "style_anchor": "anchor.png",
            "style": "style.json",
            "manifest": f"preview/v{plan.version}/manifest.json",
            "registry": "preview/locked-assets.json",
            "preview": f"preview/v{plan.version}/gameplay.png",
        },
        "Director",
    )
    # G2 stops here. A later explicit --assets resume can use these exact accepted bases.
    ctx.store.advance(
        ctx.run_id,
        ctx.run.cursor,
        "G2 approved; gameplay preview and building blocks locked",
        final=True,
    )


def _step(ctx):
    ctx.restore_files()
    history = Gates(ctx.store).all(ctx.run_id)
    last = history[-1]
    if ctx.use_mood and "preview/reference.json" not in ctx.artifacts:
        name = mood_board(ctx)
        if last.stage == "styles":
            open_gate(ctx, "mood_board", "G2 Optional mood board, not final art", [name])
            return False
        if last.stage == "mood_board" and last.status == "approved":
            ctx.save(
                "preview/reference.json",
                {"artifact": name, "approved_gate": last.id},
                "Art Director",
            )
    manifest = manifest_for(ctx)
    versions = [
        int(n.split("/")[1][1:])
        for n in ctx.artifacts
        if n.startswith("preview/v") and n.endswith("/plan.json")
    ]
    version = max(versions, default=1)
    if last.stage == "preview_revise" and last.status == "approved":
        parent = next(g for g in history if g.id == last.parent_id)
        reviewed = next(
            a
            for a in ctx.artifacts.values()
            if a.id in parent.artifacts_to_show and a.name.endswith("/readability.json")
        )
        version = ctx.read(reviewed.name)["version"] + 1
    name = f"preview/v{version}/plan.json"
    if name in ctx.artifacts:
        plan = PreviewPlan.model_validate(ctx.read(name))
    else:
        plan = (
            revised_plan(
                ctx, PreviewPlan.model_validate(ctx.read(f"preview/v{version - 1}/plan.json")), last
            )
            if version > 1
            else PreviewPlan(
                version=1,
                versions={a.id: 1 for a in manifest.assets},
                instructions={},
                style=ctx.style,
            )
        )
        ctx.save(name, plan.model_dump(), "Director")
    ctx.style = plan.style
    for spec in manifest.assets:
        if spec.id in plan.canvas_overrides:
            if spec.type in {"character", "tile"}:
                raise ValueError("Character and tile canvases remain fixed")
            spec.size = plan.canvas_overrides[spec.id]
    ctx.save(f"preview/v{plan.version}/manifest.json", manifest.model_dump(), "Game Designer")
    if last.stage == "gameplay":
        if last.status == "changes_requested":
            request_change(ctx, last, manifest, plan)
            return False
        if last.status == "approved":
            approve_preview(ctx, last, plan, manifest)
            return False
    if not ctx.use_mood and "preview/reference.json" not in ctx.artifacts:
        hero = build_block(ctx, manifest.tasks()[0], plan)
        if last.stage == "sprite_anchor" and last.status == "approved":
            ctx.save(
                "preview/reference.json",
                {"artifact": hero.artifact, "approved_gate": last.id},
                "Art Director",
            )
        else:
            open_gate(
                ctx,
                "sprite_anchor",
                "G2 Approve the first sprite as style reference",
                [hero.artifact, hero.provenance],
            )
            return False
    for task in manifest.tasks():
        if f"preview/assets/{task.id}/v{plan.versions[task.id]}/record.json" not in ctx.artifacts:
            build_block(ctx, task, plan)
            return True
    layout = room_layout(ctx, manifest, plan)
    from .preview_render import render_preview

    names = render_preview(ctx, plan, layout)
    if ctx.use_mood:
        names.append("mood/v1/landscape.png")
    open_gate(
        ctx,
        "gameplay",
        f"G2 Gameplay preview v{version}",
        names,
        form_schema={
            "type": "object",
            "properties": {
                "targets": {
                    "type": "string",
                    "title": "Change targets (assets, layout or palette)",
                    "default": "",
                    "suggestions": [a.id for a in manifest.assets] + ["layout", "palette"],
                },
                "palette": {
                    "type": "string",
                    "title": "New palette (comma-separated hex colors, for palette changes)",
                    "default": "",
                },
            },
        },
    )
    return False
