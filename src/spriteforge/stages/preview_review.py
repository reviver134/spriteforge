"""Targeted, confirmed changes and validated Level/Tile Designer layouts."""

import re

from pydantic import model_validator

from spriteforge.agents.models import ChangeRequest, Changes
from spriteforge.core.gates import Gates, new_gate
from spriteforge.pixel.schemas import Style

from .preview_models import PreviewChange, RoomLayout, example_layout


def validate_change(change, manifest):
    known = {a.id for a in manifest.assets} | {"layout", "palette"}
    if set(change.targets) - known:
        raise ValueError("Change targets must name existing assets, layout or palette")
    if "palette" in change.targets and set(change.targets) != {"palette"}:
        raise ValueError("Request palette changes separately from asset or layout changes")
    if ("palette" in change.targets) != bool(change.palette):
        raise ValueError("Palette changes need 4-16 explicit palette colors")
    if change.palette and not 4 <= len(change.palette) <= 16:
        raise ValueError("Palette changes need 4-16 explicit palette colors")
    return change


def request_change(ctx, gate, manifest, plan):
    prefix = f"preview/changes/{gate.id}"
    known = [a.id for a in manifest.assets]
    target_text = gate.response.form.get("targets", "").strip()
    palette_text = gate.response.form.get("palette", "").strip()
    targets = [s.strip() for s in target_text.split(",") if s.strip()]
    colors = [s.strip() for s in palette_text.split(",") if s.strip()]
    if targets:
        change = validate_change(
            PreviewChange(targets=targets, instruction=gate.response.text, palette=colors), manifest
        )
        ctx.save(prefix + ".json", change.model_dump(), "Director")
    else:
        text = gate.response.text.lower()
        guessed = [ident for ident in known if re.search(r"\b" + re.escape(ident) + r"\b", text)]
        if not guessed:
            guessed = ["palette"] if colors else ["layout"]
        fallback = PreviewChange(targets=guessed, instruction=gate.response.text, palette=colors)

        class ValidChange(PreviewChange):
            @model_validator(mode="after")
            def known_targets(self):
                return validate_change(self, manifest)

        change = ctx.ask(
            prefix + ".json",
            "Director",
            "Interpret this preview feedback as minimal targeted changes. Assets: "
            + ", ".join(known)
            + ". 'hero rounder' targets ONLY hero; 'add more walls' targets layout. Palette changes target ONLY palette "
            "and require a complete 4-16 hex-color palette. Asset and layout requests have an empty palette array. "
            "Do not regenerate unaffected assets. Current palette: "
            + str(plan.style.palette)
            + ". Feedback: "
            + gate.response.text,
            ValidChange,
            fallback,
        )
    changes = Changes(
        requests=[
            ChangeRequest(
                target="style"
                if target == "palette"
                else "level"
                if target == "layout"
                else [target],
                instruction=change.instruction,
                severity="minor",
                scope="Reprocess existing sources without image calls"
                if target == "palette"
                else "Change the room layout without image calls"
                if target == "layout"
                else "Regenerate only " + target,
                needs_regen=target not in {"layout", "palette"},
            )
            for target in change.targets
        ]
    )
    ctx.save(prefix + "-requests.json", changes.model_dump(), "Director")
    Gates(ctx.store).open(
        ctx.run_id,
        new_gate(
            "preview_revise",
            "Confirm gameplay preview changes",
            [ctx.artifacts[prefix + "-requests.json"].id, ctx.artifacts[prefix + ".json"].id],
            kind="confirm_changes",
            parent_id=gate.id,
            changes=changes,
        ),
    )


def revised_plan(ctx, previous, gate):
    change = PreviewChange.model_validate(ctx.read(f"preview/changes/{gate.parent_id}.json"))
    plan = previous.model_copy(deep=True)
    plan.version += 1
    plan.instructions, plan.layout_instruction = {}, ""
    plan.reprocess_targets = []
    plan.recolor = "palette" in change.targets
    if plan.recolor:
        plan.style = Style(palette=change.palette, max_colors=len(change.palette))
        for ident in plan.versions:
            plan.versions[ident] += 1
            plan.instructions[ident] = change.instruction
    else:
        for target in change.targets:
            if target == "layout":
                plan.layout_instruction = change.instruction
            else:
                plan.versions[target] += 1
                plan.instructions[target] = change.instruction
    return plan


def room_layout(ctx, manifest, plan):
    name = f"preview/v{plan.version}/layout.json"
    if name in ctx.artifacts:
        return RoomLayout.model_validate(ctx.read(name)).validate_for(ctx.brief, manifest)
    previous = f"preview/v{plan.version - 1}/layout.json"
    if plan.version > 1 and not plan.layout_instruction:
        layout = RoomLayout.model_validate(ctx.read(previous)).validate_for(ctx.brief, manifest)
        ctx.save(name, layout.model_dump(), "Level/Tile Designer")
        return layout

    class ValidRoom(RoomLayout):
        @model_validator(mode="after")
        def brief_and_assets(self):
            return self.validate_for(ctx.brief, manifest)

    fallback = example_layout(ctx.brief)
    if plan.layout_instruction and ctx.run.config["mock"]:
        fallback = RoomLayout.model_validate(ctx.read(previous))
        fallback.tiles[3][5] = "wall_2"
    return ctx.ask(
        name,
        "Level/Tile Designer",
        "Design a readable room using only these assets: "
        + manifest.model_dump_json()
        + ". Return a 20x11 grid of 16x16 tile IDs (or 'empty'), for a 320x180 screen. "
        "Use the exact brief perspective and environment strings. Entity x,y are SCREEN PIXELS, center-bottom "
        "of the full sprite canvas. Character canvases are 48x48; use the manifest dimensions for props and pickup. Keep full entities below the "
        "16-pixel top HUD and inside the screen: character x=24..296,y=64..176. "
        "Exactly one hero; display both enemies, both props, and key. Space characters apart. "
        "Use EVERY floor and wall tile variant at least once. Make the environment visible with tile-built "
        "structures, not an empty backdrop: for a city, arrange wall tiles into broken building facades "
        "with varied heights and gaps, behind the actors. The walls may be scenery in side-scroll views. "
        "Top-down: leave floor beneath entity feet, make connected traversable floor and walls. "
        "Side-scroll: empty sky, floor/platform tiles BELOW standing characters, not a top-down room. "
        "Isometric: grid projected with screen x=88+(column-row)*8,y=36+(column+row)*4; "
        "place entities on that diamond. HUD hearts, key counter and short ASCII weapon label. "
        "Brief: "
        + ctx.brief.model_dump_json()
        + (
            "\nPrevious room: "
            + str(ctx.read(previous))
            + "\nRequested change: "
            + plan.layout_instruction
            if plan.layout_instruction
            else ""
        ),
        ValidRoom,
        fallback,
    )
