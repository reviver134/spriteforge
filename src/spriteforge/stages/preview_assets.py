"""Muse building blocks through the shared pixel pipeline; one call per targeted version."""

import io

from PIL import Image

from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates
from spriteforge.pixel.schemas import Style
from spriteforge.pixel.validate import validate
from spriteforge.providers.base import ImageRequest

from .asset_context import ProductionContext
from .asset_models import AssetRecord
from .asset_render import deterministic_repair, pixelspec, process
from .asset_store import ProductionStore, digest
from .concept import center_crop, png
from .intake import GameBrief, Options


class PreviewContext(ProductionContext):
    def __init__(self, store, run_id):
        ProductionStore.__init__(self, store, run_id)
        self.run = store.get_run(run_id)
        self.cfg = Config.model_validate(self.run.config["settings"])
        cap = BudgetConfig.model_validate(self.run.config["budget"])
        cap.max_image_calls = min(cap.max_image_calls, self.cfg.preview.max_image_calls)
        cap.per_request = False  # the preview's cap is for the whole stage
        self.budget = Budget(store, run_id, cap)
        self.brief = GameBrief.model_validate(self.read("brief.json"))
        history = Gates(store).all(run_id)
        self.brief.notes = list(
            dict.fromkeys(
                [
                    *self.brief.notes,
                    *[
                        g.response.text
                        for g in history
                        if g.stage in {"genre", "brief", "followups"}
                        and g.response
                        and g.response.text.strip()
                    ],
                ]
            )
        )
        if self.brief.perspective not in {"top-down", "side-scroll", "isometric"}:
            raise ValueError(
                "Gameplay previews support top-down, side-scroll or isometric perspective"
            )
        options = Options.model_validate(self.read("options.json"))
        gate = next(g for g in history if g.stage == "styles")
        chosen = gate.response.form.get("style") or options.options[0].name
        self.option = next(o for o in options.options if o.name == chosen)
        self.style = Style(palette=self.option.colors, max_colors=len(self.option.colors))
        self.use_mood = (
            gate.response.form.get("mood_board", "yes" if self.cfg.preview.mood_board else "no")
            == "yes"
        )

    def restore_files(self):
        for name, artifact in list(self.artifacts.items()):
            if name.startswith(("preview/", "mood/")):
                self.save(name, artifact.data, mime=artifact.mime)

    def image(self, prefix, prompt, refs=(), size=(1024, 1024)):
        raw_name, request_name = prefix + "/raw.png", prefix + "/request.json"
        if raw_name in self.artifacts:
            return self.data(raw_name)
        if request_name in self.artifacts:
            raise RuntimeError(
                "Image request was interrupted. Saved reservations are retained; automatic replay is disabled."
            )
        # Reserve checking occurs in the provider before dispatch. The request checkpoint
        # prevents an ambiguous interrupted network call from being replayed on resume.
        self.save(
            request_name,
            {
                "prompt": prompt,
                "size": list(size),
                "model": self.cfg.image.model,
                "references": [{"path": r, "sha256": digest(self.data(r))} for r in refs],
            },
        )
        provider = self.image_provider()
        try:
            request = ImageRequest(
                prompt=prompt, size=size, references=[self.data(r) for r in refs]
            )
            result = provider.edit(request) if refs else provider.generate(request)
            self.save(raw_name, result.data, mime="image/png")
            self.save(prefix + "/response.json", result.metadata)
            return result.data
        finally:
            if hasattr(provider, "close"):
                provider.close()


def mood_board(ctx):
    name = "mood/v1/landscape.png"
    if name not in ctx.artifacts:
        raw = ctx.image(
            "mood/v1",
            "Mood board, not final art. A simple low-detail landscape study of "
            + ctx.brief.environment
            + ". Broad shapes, sparse detail, chunky pixels. "
            "No text, lettering, captions, logos or HUD. Mood: "
            + ctx.brief.mood
            + ". Perspective: "
            + ctx.brief.perspective
            + ". Human brief notes: "
            + str(ctx.brief.notes)
            + ". Palette and style: "
            + ctx.option.model_dump_json(),
            size=(1536, 1024),
        )
        source = Image.open(io.BytesIO(raw)).convert("RGB")
        cropped = center_crop(source)
        ctx.save(name, png(cropped), "Art Director", "image/png")
        ctx.save(
            "mood/v1/crop.json",
            {
                "source": source.size,
                "cropped": cropped.size,
                "requested": [1536, 1024],
                "method": "center-crop; no stretching",
            },
            "Art Director",
        )
    return name


def asset_record(ctx, ident, version):
    return AssetRecord.model_validate(ctx.read(f"preview/assets/{ident}/v{version}/record.json"))


def build_block(ctx, task, plan):
    version = plan.versions[task.id]
    prefix = f"preview/assets/{task.id}/v{version}"
    if prefix + "/record.json" in ctx.artifacts:
        return asset_record(ctx, task.id, version)
    ctx.style = plan.style
    previous = f"preview/assets/{task.id}/v{version - 1}"
    reprocess = plan.recolor or task.id in plan.reprocess_targets
    if reprocess:
        raw = ctx.data(previous + "/raw.png")
        ctx.save(prefix + "/raw.png", raw, mime="image/png")
        ctx.save(
            prefix + "/request.json", {"operation": "reprocess", "source": previous + "/raw.png"}
        )
    else:
        refs = []
        if version > 1:
            refs.append(previous + "/sprite.png")
        if "preview/reference.json" in ctx.artifacts:
            refs.append(ctx.read("preview/reference.json")["artifact"])
        prompt = ctx.prompt(task, plan.instructions.get(task.id, ""))
        prompt += (
            "\nGATE 2 NATIVE GRID: deliver ONE asset only, not a scene or sprite sheet. "
            "Use a uniform square logical pixel grid with crisp flat color clusters and no subpixel detail. "
            "For characters: 48x48 logical canvas, full subject 32-40 pixels tall, generous flat magenta margin, "
            "keep eyes and silhouette readable at 1x. For props and key: 16x16 logical canvas on flat magenta. "
            "For tiles: fill the entire square with an opaque seamless 16x16 material, no margin or background, "
            "low contrast floor detail. No letters or HUD. Use the reference for STYLE ONLY, draw only the requested object. "
            "Camera must be " + ctx.brief.perspective + "."
        )
        raw = ctx.image(prefix, prompt, list(dict.fromkeys(refs)))
    image, failures, report = process(raw, task, plan.style, scale_mode="native")
    repaired = False
    if failures and task.spec.type == "tile":
        image = deterministic_repair(image, task, plan.style)
        failures = validate(image, pixelspec(task, plan.style), plan.style)
        repaired = True
    # Report rendered, logical height rather than source-model pixels or canvas height.
    bbox = image.getbbox()
    height = bbox[3] - bbox[1] if bbox else 0
    if task.id == "hero" and height < 24:
        report.warnings.append(
            f"Hero logical pixel height is {height}; below the 24-pixel readability threshold"
        )
    ctx.save(prefix + "/sprite.png", png(image), mime="image/png")
    ctx.save(
        prefix + "/readability-4x.png",
        png(image.resize((image.width * 4, image.height * 4), Image.Resampling.NEAREST)),
        mime="image/png",
    )
    ctx.save(
        prefix + "/validation.json",
        {
            "failures": [f.model_dump() for f in failures],
            "processing": report.model_dump(),
            "logical_height": height,
            "native_grid_requested": True,
        },
    )
    record = AssetRecord(
        id=task.id,
        version=version,
        type=task.spec.type,
        parent=task.spec.id,
        state="idle",
        index=0,
        artifact=prefix + "/sprite.png",
        status="placeholder" if failures else "accepted",
        attempts=0 if reprocess else 1,
        deterministic_fix=repaired,
        scores=None,
        issues=[f.message for f in failures],
        provenance=prefix + "/validation.json",
    )
    ctx.save(prefix + "/record.json", record.model_dump())
    return record
