"""Grouped sheets, animation previews, and set-level visual review."""

import io
import math

from PIL import Image, ImageDraw

from spriteforge.pixel.validate import validate_animation

from .asset_models import SetIssue, SetReview
from .asset_render import pixelspec
from .concept import png


def contact_sheet(ctx, records, failed_ids=()):
    groups = [
        (kind, [r for r in records if r.type == kind])
        for kind in ("character", "tile", "item", "ui", "fx")
    ]
    cell_w, cell_h, columns = 260, 250, 4
    height = sum(30 + math.ceil(len(group) / columns) * cell_h for _, group in groups if group)
    sheet = Image.new("RGB", (columns * cell_w, max(1, height)), "#151822")
    draw = ImageDraw.Draw(sheet)
    y = 0
    for kind, group in groups:
        if not group:
            continue
        draw.text((8, y + 8), kind.upper(), fill="white")
        y += 30
        for index, record in enumerate(group):
            x, row_y = index % columns * cell_w, y + index // columns * cell_h
            sprite = Image.open(io.BytesIO(ctx.data(record.artifact))).convert("RGBA")
            scale = max(1, min(192 // sprite.width, 192 // sprite.height))
            enlarged = sprite.resize(
                (sprite.width * scale, sprite.height * scale), Image.Resampling.NEAREST
            )
            sheet.paste(enlarged, (x + (cell_w - enlarged.width) // 2, row_y + 4), enlarged)
            passed = record.status == "accepted" and record.id not in failed_ids
            badge = (
                "PASS"
                if passed
                else (
                    "FAIL / PLACEHOLDER" if record.status == "placeholder" else "FAIL / SET REVIEW"
                )
            )
            draw.text((x + 5, row_y + 200), record.id, fill="white")
            draw.text(
                (x + 5, row_y + 216),
                f"{badge}  attempts {record.attempts}/3  v{record.version}",
                fill="#9ae6b4" if passed else "#ffb4b4",
            )
        y += math.ceil(len(group) / columns) * cell_h
    return png(sheet)


def animation_gif(ctx, records):
    frames = []
    for record in sorted(records, key=lambda r: r.index):
        image = Image.open(io.BytesIO(ctx.data(record.artifact))).convert("RGBA")
        canvas = Image.new("RGB", image.size, "#303447")
        canvas.paste(image, mask=image.getchannel("A"))
        frames.append(canvas.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST))
    output = io.BytesIO()
    frames[0].save(
        output,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=180,
        loop=0,
        disposal=2,
    )
    return output.getvalue()


def review_set(ctx, manifest, cycle):
    prefix = f"production/cycles/{cycle}"
    records = list(ctx.records().values())
    sheet_name = prefix + "/contact-sheet.png"
    animation_names, hard_issues = [], []
    for spec in manifest.assets:
        for state in spec.frames:
            group = [r for r in records if r.parent == spec.id and r.state == state]
            if len(group) < 2:
                continue
            name = f"{prefix}/animations/{spec.id}-{state}.gif"
            ctx.save(name, animation_gif(ctx, group), "Pixel Artist", "image/gif")
            animation_names.append(name)
            task = next(t for t in manifest.tasks() if t.spec.id == spec.id)
            failures = validate_animation(
                {r.id: Image.open(io.BytesIO(ctx.data(r.artifact))) for r in group},
                pixelspec(task, ctx.style),
                ctx.style,
            )
            hard_issues += [
                SetIssue(asset_ids=[r.id for r in group], issue=f.message, fix=f.fix_hint)
                for f in failures
            ]
    # Bounded batches keep even a large manifest readable to the vision model.
    reviews = []
    for index in range(0, len(records), 12):
        group = records[index : index + 12]
        review = ctx.ask(
            f"{prefix}/set-review-{index // 12}.json",
            "Art Director",
            "Review this asset set for consistent palette, lighting, pixel density, camera, character identity, scale and animation baselines. "
            "The second image is the approved anchor. Report specific asset_ids for every inconsistency and an actionable fix. "
            "Do not approve placeholder sprites. Asset ids: " + ", ".join(r.id for r in group),
            SetReview,
            SetReview(style_match=5, issues=[]),
            [contact_sheet(ctx, group), ctx.data("anchor.png")],
        )
        known = {r.id for r in group}
        if any(set(issue.asset_ids) - known or not issue.asset_ids for issue in review.issues):
            raise ValueError("Set critic referenced unknown or empty asset ids")
        if review.style_match < 3 and not review.issues:
            review.issues.append(
                SetIssue(
                    asset_ids=sorted(known),
                    issue="Set style score below 3/5",
                    fix="Align palette, scale, camera and lighting with the anchor",
                )
            )
        reviews.append(review)
    issues = hard_issues + [issue for review in reviews for issue in review.issues]
    issues += [
        SetIssue(
            asset_ids=[r.id],
            issue="Placeholder: " + "; ".join(r.issues),
            fix="Request a targeted revision or explicitly accept this placeholder at G3",
        )
        for r in records
        if r.status == "placeholder"
    ]
    failed_ids = {ident for issue in issues for ident in issue.asset_ids}
    ctx.save(sheet_name, contact_sheet(ctx, records, failed_ids), "Art Director", "image/png")
    result = SetReview(style_match=min(r.style_match for r in reviews), issues=issues)
    ctx.save(prefix + "/consistency.json", result.model_dump(), "Art Director")
    ctx.save(prefix + "/registry.json", {r.id: r.model_dump() for r in records}, "Librarian")
    return [sheet_name, *animation_names, prefix + "/consistency.json"]
