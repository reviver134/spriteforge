"""Deterministic processing, bounded repairs, and honest placeholder output."""

import io
import json

import numpy as np
from PIL import Image, ImageDraw

from spriteforge.pixel.palette import quantize
from spriteforge.pixel.postprocess import postprocess
from spriteforge.pixel.schemas import AssetSpec as PixelSpec
from spriteforge.pixel.schemas import ProcessingOptions, ProcessingReport
from spriteforge.pixel.source import estimate_background, key_background, mode_resize
from spriteforge.pixel.terrain import repair_seams
from spriteforge.pixel.validate import validate
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.http import ProviderError

from .asset_models import AssetRecord, Critique, ObjectReading
from .asset_store import digest
from .concept import png


def pixelspec(task, style):
    padding = None
    if task.spec.type == "character":
        padding = max(
            1, max((task.spec.size[i] - style.character_logical_limit[i] + 1) // 2 for i in (0, 1))
        )
    return PixelSpec(
        id=task.id,
        type="sprite" if task.spec.type == "fx" else task.spec.type,
        size=task.spec.size,
        padding=padding,
    )


def process(raw, task, style, *, scale_mode="native"):
    source = Image.open(io.BytesIO(raw)).convert("RGBA")
    spec = pixelspec(task, style)
    report = ProcessingReport()
    if task.spec.type == "tile":
        # A terrain tile fills its canvas. Subject isolation would punch holes in it.
        background = estimate_background(source)
        chroma = np.array([int(style.background[i : i + 2], 16) for i in (1, 3, 5)])
        if background is not None and np.linalg.norm(background.astype(float) - chroma) < 45:
            # Muse sometimes returns a swatch on magenta despite the full-bleed prompt.
            # Crop that presentation border before quantization; never map it to red.
            source = key_background(source, ProcessingOptions(), report)
            bounds = source.getbbox()
            if bounds:
                source = source.crop(bounds)
                report.warnings.append("Cropped chroma presentation border from terrain source")
        rgb = mode_resize(source, spec.size).convert("RGB")
        result = Image.fromarray(quantize(np.asarray(rgb), style.palette)).convert("RGBA")
        report.scale_mode = "tile-mode-downscale"
        report.logical_subject_size = spec.size
        report.palette_usage = {
            c: sum(1 for p in result.getdata() if "#" + "".join(f"{v:02X}" for v in p[:3]) == c)
            for c in style.palette
        }
        report.palette_size = sum(count > 0 for count in report.palette_usage.values())
    else:
        result = postprocess(
            source,
            spec,
            style,
            options=ProcessingOptions(
                scale_mode=scale_mode,
                baseline="bottom" if task.spec.type == "character" else "center",
            ),
            report=report,
        )
    return result, validate(result, spec, style), report


def deterministic_repair(image, task, style):
    result = image.copy().convert("RGBA")
    if task.spec.type == "tile":
        result = repair_seams(result, style.palette)
    else:
        result = postprocess(result, pixelspec(task, style), style)
    return result


def placeholder(task, style):
    result = Image.new("RGBA", task.spec.size)
    draw = ImageDraw.Draw(result)
    margin = pixelspec(task, style).margin
    box = (margin, margin, result.width - margin - 1, result.height - margin - 1)
    draw.rectangle(box, fill=style.palette[0])
    draw.line((box[0], box[1], box[2], box[3]), fill=style.palette[-1], width=2)
    draw.line((box[0], box[3], box[2], box[1]), fill=style.palette[-1], width=2)
    return deterministic_repair(result, task, style)


def critique(ctx, prefix, image, task):
    enlarged = png(image.resize((image.width * 8, image.height * 8), Image.Resampling.NEAREST))
    ctx.save(prefix + "/readability-8x.png", enlarged, mime="image/png")
    reading = ctx.ask(
        prefix + "/reading.json",
        "QA Playtester",
        "Name this object from the image alone. Judge whether its silhouette is recognizable. Do not infer a desired answer from filenames. Report confidence 1-5.",
        ObjectReading,
        ObjectReading(name="mock object", confidence=5),
        [enlarged],
    )
    references = [png(image), enlarged, ctx.data("anchor.png")]
    if task.base:
        references.append(ctx.data(ctx.records()[task.base].artifact))
    result = ctx.ask(
        prefix + "/critic.json",
        "Art Director",
        "Critique the first image at its native game resolution, then its 8x nearest-neighbor preview, against the third image (approved style anchor). "
        "Score style_match/readability 1-5, check matches_desc, list specific issues and a precise edit fix. "
        "If the independent object reading is wrong or ambiguous, flag readability. "
        "If a fourth image is present, it is the accepted idle base: compare identity, proportions and foot baseline against it. "
        "For animations preserve identity, silhouette proportions and baseline. Description: "
        + task.spec.desc
        + f"; state={task.state}, frame={task.index}. Blind reading: "
        + reading.model_dump_json()
        + "\nRequired camera: "
        + ctx.brief.perspective
        + "\nApproved style: "
        + ctx.style.model_dump_json(),
        Critique,
        Critique(style_match=5, readability=5, matches_desc=True, issues=[], fix=""),
        references,
    )
    if reading.confidence < 3:
        result = result.model_copy(
            update={
                "readability": min(result.readability, reading.confidence),
                "issues": [*result.issues, "Object could not be named confidently at 8x"],
            }
        )
    return result


def generate_task(ctx, task, plan):
    version = plan.versions[task.id]
    prefix = f"production/assets/{task.id}/v{version}"
    finished = prefix + "/record.json"
    if finished in ctx.artifacts:
        record = AssetRecord.model_validate(ctx.read(finished))
        ctx.lock(record, plan)
        return record
    records = ctx.records()
    previous = records.get(task.id)
    if previous and previous.version >= version:
        return previous
    if previous and task.id not in plan.instructions:
        raise PermissionError("Accepted asset is locked")
    refs = ([records[task.base].artifact] if task.base else []) + ["anchor.png"]
    refs += [records[r].artifact for r in task.spec.refs if r != "anchor" and r != task.base]
    refs = list(dict.fromkeys(refs))
    if previous:
        refs.append(previous.artifact)
    prompt = ctx.prompt(task, plan.instructions.get(task.id, ""))
    attempt_logs, issues, scores = [], [], None
    last_image = None
    accepted, fixed = False, False
    for attempt in range(1, 4):
        attempt_prefix = f"{prefix}/attempt-{attempt}"
        request_name = attempt_prefix + "/request.json"
        raw_name = attempt_prefix + "/raw.png"
        result_name = attempt_prefix + "/result.json"
        if result_name in ctx.artifacts:
            log = ctx.read(result_name)
            attempt_logs.append(log)
            issues = log["issues"]
            if attempt_prefix + "/processed.png" in ctx.artifacts:
                last_image = Image.open(
                    io.BytesIO(ctx.data(attempt_prefix + "/processed.png"))
                ).convert("RGBA")
            scores = Critique.model_validate(log["scores"]) if log["scores"] else None
            if log["passed"]:
                accepted = True
                break
            continue
        repair_refs = list(refs)
        if attempt > 1:
            previous_raw = f"{prefix}/attempt-{attempt - 1}/raw.png"
            if previous_raw in ctx.artifacts:
                repair_refs.append(previous_raw)
        repair_refs = list(dict.fromkeys(repair_refs))[:10]
        instruction = prompt
        if issues:
            instruction += (
                "\nREPAIR ONLY these specific issues (last reference is the previous attempt when present): "
                + "; ".join(issues)
            )
            if task.spec.type == "tile":
                instruction += (
                    " Repair opposite edges and half-offset joins; preserve the central material."
                )
        strength = "low" if attempt == 1 else "high"
        metadata = {
            "prompt": instruction,
            "references": [{"path": r, "sha256": digest(ctx.data(r))} for r in repair_refs],
            "reasoning_strength": strength,
            "size": [1024, 1024],
            "model": ctx.cfg.image.model,
            "base_url": str(ctx.cfg.image.base_url),
            "endpoint": ctx.cfg.image.edit_path,
        }
        cache_key = digest(
            json.dumps(
                {**metadata, "references": [r["sha256"] for r in metadata["references"]]},
                sort_keys=True,
            ).encode()
        )
        cache_name = f"production/cache/{cache_key}.png"
        interrupted = (
            request_name in ctx.artifacts
            and raw_name not in ctx.artifacts
            and cache_name not in ctx.artifacts
            and attempt_prefix + "/retryable.json" not in ctx.artifacts
        )
        ctx.save(request_name, metadata)
        try:
            if interrupted:
                raise ProviderError(
                    "Previous request was interrupted; its reservation is retained and it will not be blindly replayed"
                )
            if raw_name not in ctx.artifacts:
                if cache_name not in ctx.artifacts:
                    provider = ctx.image_provider()
                    try:
                        raw = provider.edit(
                            ImageRequest(
                                prompt=instruction,
                                size=(1024, 1024),
                                references=[ctx.data(r) for r in repair_refs],
                                reasoning_strength=strength,
                            )
                        ).data
                        ctx.save(cache_name, raw, mime="image/png")
                    finally:
                        if hasattr(provider, "close"):
                            provider.close()
                else:
                    ctx.store.append_event(
                        ctx.run_id,
                        "Pixel Artist",
                        "asset.cache_hit",
                        {"id": task.id, "hash": cache_key},
                    )
                ctx.save(raw_name, ctx.data(cache_name), mime="image/png")
            image, failures, report = process(ctx.data(raw_name), task, ctx.style)
            if task.spec.type == "tile" and any(
                f.code in {"tile_edges", "tile_wrap"} for f in failures
            ):
                ctx.save(attempt_prefix + "/before-seam-repair.png", png(image), mime="image/png")
                image = deterministic_repair(image, task, ctx.style)
                failures = validate(image, pixelspec(task, ctx.style), ctx.style)
                fixed = not any(f.code in {"tile_edges", "tile_wrap"} for f in failures)
                ctx.store.append_event(
                    ctx.run_id,
                    "Pixel Artist",
                    "asset.seam_repair",
                    {"id": task.id, "passed": fixed, "attempt": attempt},
                )
            last_image = image
            ctx.save(attempt_prefix + "/processed.png", png(image), mime="image/png")
            ctx.save(
                attempt_prefix + "/validation.json",
                {"failures": [f.model_dump() for f in failures], "processing": report.model_dump()},
            )
            issues = [f.message + " " + f.fix_hint for f in failures]
            scores = None if failures else critique(ctx, attempt_prefix, image, task)
            if scores and not scores.passed:
                issues += scores.issues + [
                    scores.fix or "Improve description match, readability and style"
                ]
            accepted = not failures and scores is not None and scores.passed
        except ProviderError as exc:
            # A known failed dispatch consumes neither an image attempt nor cap.
            # Stop this workflow invocation; explicit resume retries the same slot.
            if not interrupted:
                ctx.save(attempt_prefix + "/retryable.json", {"retryable": True})
            ctx.store.append_event(
                ctx.run_id,
                "Pixel Artist",
                "asset.provider_failed",
                {"id": task.id, "attempt": attempt, "message": str(exc)},
            )
            raise
        log = {
            "attempt": attempt,
            "request": request_name,
            "cache_key": cache_key,
            "passed": accepted,
            "issues": issues,
            "router_decisions": [
                e.payload
                for e in ctx.store.events(ctx.run_id)
                if e.type == "router.decision"
                and (
                    prefix in e.payload.get("task_id", "")
                    or e.payload.get("task_id")
                    == "context:production/context/"
                    + task.spec.type
                    + ("-frame.json" if task.base else "-base.json")
                )
            ],
            "scores": scores.model_dump() if scores else None,
        }
        ctx.save(result_name, log)
        attempt_logs.append(log)
        ctx.store.append_event(
            ctx.run_id, "Pixel Artist", "asset.attempt", {"id": task.id, "version": version, **log}
        )
        if accepted:
            break
    if not accepted and last_image is not None:
        candidate = deterministic_repair(last_image, task, ctx.style)
        failures = validate(candidate, pixelspec(task, ctx.style), ctx.style)
        ctx.save(prefix + "/deterministic.png", png(candidate), mime="image/png")
        ctx.save(
            prefix + "/deterministic-validation.json",
            {"failures": [f.model_dump() for f in failures]},
        )
        scores = None if failures else critique(ctx, prefix + "/deterministic", candidate, task)
        accepted = not failures and scores is not None and scores.passed
        if accepted:
            last_image, fixed, issues = candidate, True, []
        else:
            issues = [f.message for f in failures] + (
                scores.issues + [scores.fix] if scores else issues
            )
    final_image = last_image if accepted else placeholder(task, ctx.style)
    artifact = prefix + "/sprite.png"
    ctx.save(artifact, png(final_image), mime="image/png")
    provenance = prefix + "/provenance.json"
    ctx.save(
        provenance,
        {
            "asset": task.model_dump(),
            "version": version,
            "attempts": attempt_logs,
            "deterministic_fix": fixed,
            "placeholder": not accepted,
            "scores": scores.model_dump() if scores else None,
            "issues": issues,
        },
    )
    record = AssetRecord(
        id=task.id,
        version=version,
        type=task.spec.type,
        parent=task.spec.id,
        state=task.state,
        index=task.index,
        artifact=artifact,
        status="accepted" if accepted else "placeholder",
        attempts=len(attempt_logs),
        deterministic_fix=fixed,
        scores=scores,
        issues=issues,
        provenance=provenance,
        locked=accepted,
    )
    ctx.save(finished, record.model_dump())
    ctx.lock(record, plan)
    return record
