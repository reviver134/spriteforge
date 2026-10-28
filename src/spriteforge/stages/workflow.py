"""G1/G2 workflow. Durable artifacts are checkpoints; accepted art is never regenerated."""

import json
from uuid import uuid4

from spriteforge.agents.models import ChangeRequest, Changes
from spriteforge.config import BudgetConfig, Config
from spriteforge.core.cost import Budget
from spriteforge.core.gates import Gates, new_gate
from spriteforge.core.security import redact
from spriteforge.core.state import Artifact
from spriteforge.memory.okf.git import write_lock
from spriteforge.memory.okf.seed import seed
from spriteforge.memory.okf.store import KnowledgeStore
from spriteforge.pixel.schemas import Style
from spriteforge.providers.base import ImageRequest
from spriteforge.providers.mock import MockImageProvider, MockLLMProvider
from spriteforge.providers.muse_image import MuseImageProvider
from spriteforge.providers.muse_spark import MuseSparkProvider

from .concept import preview
from .intake import (
    GameBrief,
    Options,
    Questions,
    brief_form,
    default_options,
    field,
    followups,
    propose,
)

WORKFLOW = "intake-concept-v1"


def start_run(store, cfg, mock=True, assets=False):
    return store.create_run(
        {
            "workflow": WORKFLOW,
            "mock": mock,
            "asset_production": assets,
            "settings": cfg.model_dump(mode="json"),
            "budget": cfg.budget.model_dump(),
        }
    )


def workflow_step(store, run_id):
    with write_lock(store.path.parent / f".{run_id}.runtime.lock"):
        return _step(store, run_id)


def advance_until_gate(store, run_id):
    while workflow_step(store, run_id):
        pass


def _step(store, run_id):
    run = store.get_run(run_id)
    gates = Gates(store)
    if run.status in {"completed", "aborted", "failed", "budget_exhausted"} or gates.pending(
        run_id
    ):
        return False
    if run.config.get("asset_production") and any(
        a.name == "approval.json" for a in store.artifacts(run_id)
    ):
        from .production import production_step

        return production_step(store, run_id)
    cfg = Config.model_validate(run.config["settings"])
    budget = Budget(store, run_id, BudgetConfig.model_validate(run.config["budget"]))
    root = store.path.parent / "runs" / run_id
    root.mkdir(parents=True, exist_ok=True)
    artifacts = {a.name: a for a in store.artifacts(run_id)}

    def save(name, data, role="Director", mime="application/json"):
        if name not in artifacts:
            raw = data if isinstance(data, bytes) else json.dumps(redact(data)).encode()
            artifact = Artifact(id=uuid4().hex, run_id=run_id, name=name, mime=mime, data=raw)
            store.save_artifact(artifact, role, "artifact.created")
            artifacts[name] = artifact
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_bytes() != artifacts[name].data:
            raise ValueError(f"Locked artifact differs on disk: {name}")
        if not target.exists():
            target.write_bytes(artifacts[name].data)
        return artifacts[name].id

    def read(name):
        return json.loads(artifacts[name].data)

    def open_gate(stage, title, names=(), **kwargs):
        gates.open(run_id, new_gate(stage, title, [artifacts[n].id for n in names], **kwargs))

    def ask(role, prompt, schema, fallback, name):
        if name not in artifacts:
            llm = (
                MockLLMProvider(budget, [fallback.model_dump()])
                if run.config["mock"]
                else MuseSparkProvider(cfg.llm, budget)
            )
            try:
                result = propose(llm, role, prompt, schema)
                save(name, result.model_dump(), role)
            finally:
                if hasattr(llm, "close"):
                    llm.close()
        return schema.model_validate(read(name))

    history = gates.all(run_id)
    intake_notes = [
        g.response.text
        for g in history
        if g.stage in {"genre", "brief", "followups"} and g.response and g.response.text.strip()
    ]
    last = history[-1] if history else None
    memory = KnowledgeStore(store.path.parent / "memory", store, run_id)
    seed(memory)
    if last is None:
        entries, _ = memory.scan()
        genres = [e for e in entries if e.id.startswith("genres/") and not e.private]
        memory.retrieve("genre game design", token_budget=1500)
        save("genres.json", {e.id: e.title for e in genres}, "Game Designer")
        open_gate(
            "genre",
            "G1 Choose a genre",
            form_schema={
                "type": "object",
                "properties": {
                    "genre": {
                        "type": "string",
                        "title": "Genre",
                        "enum": [e.id for e in genres],
                        "default": "genres/top-down-dungeon-crawler",
                    }
                },
            },
        )
        return False
    if last.stage == "genre":
        ident = last.response.form.get("genre") or "genres/top-down-dungeon-crawler"
        entry = memory.show(ident)
        save("genre.json", {"id": ident, "body": entry.body}, "Game Designer")
        open_gate("brief", "G1 Game brief", form_schema=brief_form(entry))
        return False
    if last.stage == "brief":
        entry = memory.show(read("genre.json")["id"])
        properties = brief_form(entry)["properties"]
        values = {
            k: last.response.form.get(k) or v.get("default", "") for k, v in properties.items()
        }
        answers = {k: values.pop(k) for k in list(values) if k.startswith("seed_")}
        brief = GameBrief(genre=entry.id, answers=answers, notes=intake_notes, **values)
        save("brief-draft.json", brief.model_dump(), "Game Designer")
        # Separate context also repairs legacy drafts without overwriting saved artifacts.
        save("brief-notes.json", {"notes": intake_notes}, "Game Designer")
        questions = ask(
            "Game Designer",
            "Ask 2-4 specific follow-up questions based on this brief and its answers: "
            + brief.model_dump_json()
            + "\nGenre:\n"
            + entry.body,
            Questions,
            followups(brief),
            "followups.json",
        )
        open_gate(
            "followups",
            "G1 Follow-up questions",
            form_schema={
                "type": "object",
                "properties": {
                    f"followup_{i}": field(q, "Use the simplest genre-appropriate default")
                    for i, q in enumerate(questions.questions)
                },
            },
        )
        return False
    if last.stage == "followups":
        brief = GameBrief.model_validate(read("brief-draft.json"))
        brief.notes = intake_notes
        brief.answers.update(last.response.form)
        save("brief.json", brief.model_dump(), "Game Designer")
        snippets = memory.retrieve(
            "art-styles " + brief.genre + " " + brief.mood, token_budget=2500, relative_score=0
        )
        entries = [memory.show(s.id) for s in snippets if s.id.startswith("art-styles/")][:3]
        if len(entries) < 2:
            entries = [
                memory.show(e.id)
                for e in memory.scan()[0]
                if e.id.startswith("art-styles/") and not e.private
            ][:3]
        options = ask(
            "Art Director",
            "Propose 2-3 palette/style options with hex color swatches for "
            + brief.model_dump_json()
            + "\nRetrieved art styles:\n"
            + "\n".join(e.body for e in entries),
            Options,
            default_options(brief, entries),
            "options.json",
        )
        open_gate(
            "styles",
            "G2 Choose a concept style",
            ["options.json"],
            form_schema={
                "type": "object",
                "properties": {
                    "style": {
                        "type": "string",
                        "title": "Style",
                        "enum": [o.name for o in options.options],
                    }
                },
            },
        )
        return False
    if last.stage == "concept" and last.status == "changes_requested":
        changes = Changes(
            requests=[
                ChangeRequest(
                    target="style",
                    instruction=last.response.text,
                    severity="minor",
                    scope="Concept scene only",
                    needs_regen=True,
                )
            ]
        )
        name = f"changes/{last.id}.json"
        save(name, changes.model_dump())
        open_gate(
            "revise",
            "Confirm concept change requests",
            [name],
            kind="confirm_changes",
            parent_id=last.id,
            changes=changes,
        )
        return False
    if last.stage == "concept" and last.status == "approved":
        version = len([g for g in history if g.stage == "concept"])
        meta = read(f"concept/v{version}.json")
        palette = [last.response.form.get(f"color_{i}") or c for i, c in enumerate(meta["palette"])]
        style = Style(palette=palette, max_colors=len(palette))
        save("style.json", style.model_dump(), "Art Director")
        save("anchor.png", artifacts[f"concept/v{version}.png"].data, "Art Director", "image/png")
        save(
            "approval.json",
            {
                "gate": last.id,
                "version": version,
                "screen": [320, 180],
                "style_anchor": "anchor.png",
                "style": "style.json",
            },
        )
        production = run.config.get("asset_production", False)
        store.advance(
            run_id, run.cursor, "G2 approved; style and anchor locked", final=not production
        )
        return production
    if last.stage in {"styles", "revise"}:
        version = len([g for g in history if g.stage == "concept"]) + 1
        brief = GameBrief.model_validate(read("brief.json"))
        # Legacy servers saved briefs without free-text notes; recover them from gates.
        brief.notes = list(dict.fromkeys([*brief.notes, *intake_notes]))
        options = Options.model_validate(read("options.json"))
        style_gate = next(g for g in history if g.stage == "styles")
        selected = style_gate.response.form.get("style") or options.options[0].name
        option = next(o for o in options.options if o.name == selected)
        name = f"concept/v{version}.png"
        if name not in artifacts:
            provider = (
                MockImageProvider(budget)
                if run.config["mock"]
                else MuseImageProvider(cfg.image, budget)
            )
            prompt = (
                "CONCEPT key-art scene, not final game assets. Landscape 16:9 composition: environment, "
                "one hero, 2-3 enemies and a HUD mock all in one frame. Chunky readable pixel art. "
                "Human notes override untouched template defaults. "
                + brief.model_dump_json()
                + "\nStyle: "
                + option.model_dump_json()
            )
            references = []
            if version > 1:
                references = [artifacts[f"concept/v{version - 1}.png"].data]
                prompt = (
                    "Edit the previous CONCEPT only as instructed by these confirmed ChangeRequests: "
                    + last.changes.model_dump_json()
                )
            try:
                req = ImageRequest(prompt=prompt, references=references)
                result = provider.edit(req) if references else provider.generate(req)
                save(name, result.data, "Art Director", "image/png")
            finally:
                if hasattr(provider, "close"):
                    provider.close()
        native, enlarged, palette = preview(artifacts[name].data, brief.screen)
        save(f"concept/v{version}-native.png", native, "Art Director", "image/png")
        save(f"concept/v{version}-pixel.png", enlarged, "Art Director", "image/png")
        save(
            f"concept/v{version}.json",
            {
                "version": version,
                "palette": palette,
                "label": "CONCEPT — not final art",
                "screen": brief.screen,
            },
        )
        open_gate(
            "concept",
            f"G2 CONCEPT v{version} — not final art",
            [name, f"concept/v{version}-pixel.png"],
            form_schema={
                "type": "object",
                "properties": {
                    f"color_{i}": {**field(f"Palette swatch {i + 1}", c), "format": "color"}
                    for i, c in enumerate(palette)
                },
            },
        )
        return False
    raise ValueError("Unsupported intake gate transition")
