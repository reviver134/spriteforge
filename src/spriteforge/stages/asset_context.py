"""Role proposals and selected skill/prompt context for production."""

from pathlib import Path

from spriteforge.agents.roles import load_roles
from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget
from spriteforge.memory.okf.seed import seed
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.pixel.schemas import Style
from spriteforge.providers.base import ChatRequest, Message, chat_json
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider
from spriteforge.skills.catalog import SkillCatalog

from .asset_models import AssetSpec, FrameState, Manifest, ManifestProposal, ProposedAsset
from .asset_store import ProductionStore
from .intake import GameBrief


class ProductionContext(ProductionStore):
    def __init__(self, store, run_id):
        super().__init__(store, run_id)
        self.run = store.get_run(run_id)
        self.cfg = Config.model_validate(self.run.config["settings"])
        self.budget = Budget(store, run_id, BudgetConfig.model_validate(self.run.config["budget"]))
        self.style = Style.model_validate(self.read("style.json"))
        self.brief = GameBrief.model_validate(self.read("brief.json"))
        from spriteforge.core.gates import Gates

        notes = [
            g.response.text
            for g in Gates(store).all(run_id)
            if g.stage in {"genre", "brief", "followups"} and g.response and g.response.text.strip()
        ]
        self.brief.notes = list(dict.fromkeys([*self.brief.notes, *notes]))

    def image_provider(self):
        return (
            MockImageProvider(self.budget)
            if self.run.config["mock"]
            else MuseImageProvider(self.cfg.image, self.budget)
        )

    def ask(self, name, role, prompt, schema, fallback, images=()):
        if name in self.artifacts:
            return schema.model_validate(self.read(name))
        provider = (
            MockLLMProvider(self.budget, [fallback.model_dump()])
            if self.run.config["mock"]
            else MuseSparkProvider(self.cfg.llm, self.budget)
        )
        try:
            result = chat_json(
                provider,
                ChatRequest(
                    messages=[
                        Message(
                            role="system",
                            content=str(load_roles()[role].model_dump())
                            + "\nHuman brief notes override untouched genre defaults. Return only the requested JSON.",
                        ),
                        Message(role="user", content=prompt, images=list(images)),
                    ]
                ),
                schema,
            )
            self.save(name, result.model_dump(), role)
            return result
        finally:
            if hasattr(provider, "close"):
                provider.close()

    def prompt_context(self, task):
        name = f"production/context/{task.spec.type}-{'frame' if task.base else 'base'}.json"
        if name not in self.artifacts:
            memory = KnowledgeStore(self.store.path.parent / "memory", self.store, self.run_id)
            seed(memory)
            snippets = memory.retrieve(
                "prompt-patterns chunky pixel " + task.spec.type, token_budget=1800
            )
            patterns = [
                memory.show(s.id).body for s in snippets if s.id.startswith("prompt-patterns/")
            ]
            catalog = SkillCatalog(self.store.path.parent)
            role = load_roles()["Pixel Artist"]
            summaries = catalog.summaries(role)
            wanted = ["pixel-art-prompting"] + (["sprite-animation-frames"] if task.base else [])
            skills = []
            for summary in summaries:
                if summary["name"] in wanted:
                    skills.append(catalog.load(role, summary["name"]))
                    self.store.append_event(
                        self.run_id, "Pixel Artist", "skill.loaded", {"name": summary["name"]}
                    )
            self.save(name, {"patterns": patterns, "skills": skills})
        return str(self.read(name))

    def prompt(self, task, instruction):
        template = (
            "anim_frame" if task.base else ("item" if task.spec.type == "fx" else task.spec.type)
        )
        body = (Path(__file__).parents[1] / "prompts" / f"{template}.txt").read_text(
            encoding="utf-8"
        )
        return (
            body
            + "\nAsset: "
            + task.model_dump_json()
            + "\nApproved style rules: "
            + self.style.model_dump_json()
            + "\nGame brief: "
            + self.brief.model_dump_json()
            + "\nRetrieved guidance: "
            + self.prompt_context(task)
            + "\nConfirmed revision: "
            + (instruction or "Initial production")
        )


def mock_manifest(brief, style):
    """Small, explicit offline fixture. Live manifests come from the two designer roles."""
    specs = [
        ("hero", "character", style.character_canvas, {"idle": 1, "walk": 2}, brief.character),
        ("enemy", "character", style.character_canvas, {"idle": 1, "walk": 2}, "A readable enemy"),
        ("ground", "tile", style.tile_canvas, {"idle": 1}, "Repeatable ground material"),
        ("wall", "tile", style.tile_canvas, {"idle": 1}, "Repeatable wall material"),
        ("reward", "item", (16, 16), {"idle": 1}, "A reward pickup"),
        ("health", "ui", (16, 16), {"idle": 1}, "A heart icon"),
        ("impact", "fx", (16, 16), {"idle": 1, "burst": 2}, "An impact spark"),
    ]
    return ManifestProposal(
        assets=[
            ProposedAsset(
                id=i,
                type=t,
                size=s,
                frames=[FrameState(state=k, count=v) for k, v in frames.items()],
                desc=desc + "; " + brief.environment,
                depends_on=[],
                refs=["anchor"],
            )
            for i, t, s, frames, desc in specs
        ]
    )


def make_manifest(ctx):
    name = "production/manifest.json"
    if name in ctx.artifacts:
        return Manifest.model_validate(ctx.read(name))
    memory = KnowledgeStore(ctx.store.path.parent / "memory", ctx.store, ctx.run_id)
    seed(memory)
    entry = memory.show(ctx.brief.genre)
    prompt = (
        "Produce a complete but small AssetSpec manifest for this brief using the genre asset checklist. "
        "Human notes override default descriptions. Include actors, terrain, items, UI and effects as needed. "
        "Idle frame zero is the base for every asset; animation states list total frame counts, including any additional idle frames. "
        "refs are other manifest asset ids or anchor, never file paths. Dependencies must be acyclic. "
        "At most 64 base assets and 256 total frames; fit the available image-call cap. "
        "No sprite sheets: each generated output is one frame.\nBrief: "
        + ctx.brief.model_dump_json()
        + "\nGenre checklist and mechanics:\n"
        + entry.body
        + "\nStyle: "
        + ctx.style.model_dump_json()
        + f"\nImage cap: {ctx.budget.config.max_image_calls}"
    )
    design = ctx.ask(
        "production/game-design-manifest.json",
        "Game Designer",
        prompt,
        ManifestProposal,
        mock_manifest(ctx.brief, ctx.style),
    )
    proposal = ctx.ask(
        "production/level-design-manifest.json",
        "Level/Tile Designer",
        prompt
        + "\nReview the Game Designer's proposal, correct dependencies and add the necessary tiles. Return the complete merged manifest:\n"
        + design.model_dump_json(),
        ManifestProposal,
        design,
    )
    assets = []
    for item in proposal.assets:
        if len({f.state for f in item.frames}) != len(item.frames):
            raise ValueError("Duplicate animation states")
        values = item.model_dump()
        values["frames"] = {f.state: f.count for f in item.frames}
        if item.type == "character":
            values["size"] = ctx.style.character_canvas
        elif item.type == "tile":
            values["size"] = ctx.style.tile_canvas
        assets.append(AssetSpec.model_validate(values))
    manifest = Manifest(assets=assets)
    if "preview/locked-assets.json" in ctx.artifacts:
        locked = Manifest.model_validate(ctx.read(ctx.read("approval.json")["manifest"]))
        # Keep accepted base specs; new animation states may extend them later.
        proposals = {a.id: a for a in manifest.assets}
        for asset in locked.assets:
            if asset.id in proposals:
                asset.frames = proposals[asset.id].frames
            proposals[asset.id] = asset
        manifest = Manifest(assets=list(proposals.values()))
    order = {t.id: i for i, t in enumerate(manifest.tasks())}
    manifest.assets.sort(key=lambda a: order[a.id])
    ctx.save(name, manifest.model_dump(), "Level/Tile Designer")
    ctx.save(
        "production/tasks.json", [t.model_dump() for t in manifest.tasks()], "Level/Tile Designer"
    )
    return manifest
