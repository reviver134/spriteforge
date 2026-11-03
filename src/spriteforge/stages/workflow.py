"""G1/G2 workflow. Durable artifacts are checkpoints; accepted art is never regenerated."""

import json
import logging
import re
from uuid import uuid4

from spriteforge.agents.models import ChangeRequest, Changes
from spriteforge.config import BudgetConfig, Config
from spriteforge.core.acting import acting
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

from .intake import (
    BriefDraft,
    GameBrief,
    GenrePick,
    Options,
    Questions,
    brief_form,
    brief_prompt,
    custom_genre_body,
    default_draft,
    default_options,
    field,
    fit_draft,
    followups,
    genre_form,
    genre_prompt,
    guess_genre,
    match_genre,
    propose,
)

WORKFLOW = "intake-concept-v1"


GENRE_NOTE = (
    "Any genre works: pick a suggestion or type your own. The agents write the game's code "
    "from your answers, using the art library first and Muse Image for anything it lacks."
)


def brief_defaults(artifacts, genre, words=""):
    """Brief defaults saved at the genre step; runs from before drafting use the genre's.
    `words`: what the human wrote (a story is suggested only when they ask for one)."""
    if "brief-defaults.json" in artifacts:
        draft = BriefDraft.model_validate_json(artifacts["brief-defaults.json"].data)
    else:
        draft = default_draft(genre["body"])
    return fit_draft(draft, words)


def start_run(store, cfg, mock=True, assets=False, gameplay_preview=True, legacy=False, goal=""):
    if not gameplay_preview and not legacy:
        raise ValueError("Deprecated concept preview requires explicit --legacy")
    run = store.create_run(
        {
            "workflow": WORKFLOW,
            "mock": mock,
            "asset_production": assets,
            "gameplay_preview": gameplay_preview,
            "legacy_preview": legacy,
            "goal": goal,
            "settings": cfg.model_dump(mode="json"),
            "budget": cfg.budget.model_dump(),
        }
    )
    if legacy:
        logging.getLogger(__name__).warning("DEPRECATED: legacy PIL concept preview enabled")
        store.append_event(run.id, "Director", "preview.deprecated", {"legacy": True})
    return run


def workflow_step(store, run_id):
    with write_lock(store.path.parent / f".{run_id}.runtime.lock"):
        return _step(store, run_id)


def advance_until_gate(store, run_id):
    while workflow_step(store, run_id):
        pass


def _step(store, run_id):
    run = store.get_run(run_id)
    if not run.config.get("gameplay_preview") and not run.config.get("legacy_preview"):
        raise ValueError("Deprecated concept preview requires explicit --legacy")
    gates = Gates(store)
    if run.status in {"completed", "aborted", "failed", "budget_exhausted"} or gates.pending(
        run_id
    ):
        return False
    history = gates.all(run_id)
    if history and history[-1].stage in {"design", "menus", "playtest", "final"}:
        from .game import game_step

        return game_step(store, run_id)
    if (
        run.config.get("asset_production")
        and (not run.config.get("gameplay_preview") or run.config.get("preview_production_enabled"))
        and any(a.name == "approval.json" for a in store.artifacts(run_id))
    ):
        from .production import production_step

        return production_step(store, run_id)
    history = gates.all(run_id)
    if (
        run.config.get("gameplay_preview")
        and history
        and history[-1].stage
        in {"styles", "mood_board", "sprite_anchor", "gameplay", "preview_revise"}
    ):
        from .gameplay import gameplay_step

        return gameplay_step(store, run_id)
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
                else MuseSparkProvider(cfg.llm, budget, reasoning=cfg.llm.reasoning.get("intake"))
            )
            try:
                with acting(role):
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
    if run.config.get("goal"):
        intake_notes.insert(0, run.config["goal"])
    last = history[-1] if history else None
    memory = KnowledgeStore(store.path.parent / "memory", store, run_id)
    seed(memory)
    goal = run.config.get("goal", "")
    genres = [e for e in memory.scan()[0] if e.id.startswith("genres/") and not e.private]
    if last is None:
        memory.retrieve("genre game design " + goal, token_budget=1500)
        save("genres.json", {e.id: e.title for e in genres}, "Game Designer")
        fallback = guess_genre(goal, genres)
        if not goal.strip():
            save("genre-pick.json", fallback.model_dump(), "Game Designer")
        pick = ask(
            "Game Designer", genre_prompt(goal, genres), GenrePick, fallback, "genre-pick.json"
        )
        open_gate(
            "genre",
            "G1 Choose a genre",
            ["genre-pick.json"],
            form_schema=genre_form(pick, genres, GENRE_NOTE),
        )
        return False
    if last.stage == "genre":
        pick = (
            GenrePick.model_validate(read("genre-pick.json"))
            if "genre-pick.json" in artifacts
            else guess_genre(goal, genres)  # runs whose genre gate opened before suggestions
        )
        text = (last.response.form.get("genre") or pick.genre).strip()
        entry = match_genre(text, genres)
        if entry:
            genre = {"id": entry.id, "title": entry.title, "body": entry.body}
        else:
            if text.lower() != pick.genre.lower():
                pick = ask(
                    "Game Designer",
                    genre_prompt(f"{goal}\nThe human chose the genre: {text}", genres),
                    GenrePick,
                    pick.model_copy(update={"genre": text}),
                    "genre-custom.json",
                )
            slug = "-".join(re.findall(r"[a-z0-9]+", text.lower()))[:48].strip("-") or "game"
            genre = {
                "id": f"custom/{slug}",
                "title": text,
                "body": custom_genre_body(pick, genres),
            }
        save("genre.json", genre, "Game Designer")
        draft = default_draft(genre["body"])
        if not goal.strip():
            save("brief-defaults.json", draft.model_dump(), "Game Designer")
        draft = ask(
            "Game Designer",
            brief_prompt(goal, genre["title"], genre["body"]),
            BriefDraft,
            draft,
            "brief-defaults.json",
        )
        draft = fit_draft(draft, "\n".join(intake_notes))
        open_gate("brief", "G1 Game brief", form_schema=brief_form(genre["body"], draft))
        return False
    if last.stage == "brief":
        genre = read("genre.json")
        draft = brief_defaults(artifacts, genre, "\n".join(intake_notes))
        properties = brief_form(genre["body"], draft)["properties"]
        values = {
            k: last.response.form.get(k) or v.get("default", "") for k, v in properties.items()
        }
        answers = {k: values.pop(k) for k in list(values) if k.startswith("seed_")}
        brief = GameBrief(genre=genre["id"], answers=answers, notes=intake_notes, **values)
        save("brief-draft.json", brief.model_dump(), "Game Designer")
        # Separate context also repairs legacy drafts without overwriting saved artifacts.
        save("brief-notes.json", {"notes": intake_notes}, "Game Designer")
        questions = ask(
            "Game Designer",
            "Ask 2-4 specific follow-up questions based on this brief and its answers: "
            + brief.model_dump_json()
            + f"\nGenre: {genre.get('title', genre['id'])}\n"
            + genre["body"],
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
        if run.config.get("gameplay_preview") and cfg.preview.mode == "game":
            from .game import game_step

            return game_step(store, run_id)
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
                    },
                    **(
                        {
                            "mood_board": {
                                "type": "string",
                                "title": "Optional mood board",
                                "enum": ["yes", "no"],
                                "default": "yes" if cfg.preview.mood_board else "no",
                            }
                        }
                        if run.config.get("gameplay_preview")
                        else {}
                    ),
                },
            },
        )
        return False
    if not run.config.get("legacy_preview"):
        raise ValueError("Deprecated concept preview requires explicit --legacy")
    from .concept import preview

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
                "Mood board, not final art. Simple low-detail landscape environment study. "
                "Large color shapes, chunky pixel art. No text, lettering, UI or HUD. "
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
                req = ImageRequest(prompt=prompt, references=references, size=(1536, 1024))
                result = provider.edit(req) if references else provider.generate(req)
                save(name, result.data, "Art Director", "image/png")
            finally:
                if hasattr(provider, "close"):
                    provider.close()
        native, enlarged, palette = preview(artifacts[name].data, brief.screen, legacy=True)
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
