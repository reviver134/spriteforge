from pathlib import Path

import yaml

from .models import Entry, GenreSeed, Metadata
from .store import KnowledgeStore

BOOTSTRAP_SOURCE = {
    "resource": "Project owner Milestone 3 bootstrap instructions, 2026-10-28",
    "title": "Owner-authorized seed library and trust designation",
    "author": "human:project-owner",
}


def seed_entries() -> list[Entry]:
    directory = Path(__file__).with_name("seeds")
    entries = []
    for values in yaml.safe_load((directory / "genres.yaml").read_text(encoding="utf-8")):
        genre = GenreSeed.model_validate(values)
        body = (
            f"# Perspective and camera\n{genre.perspective}\n\n# Controls\n{genre.controls}\n\n"
            f"# Core mechanics template\n{genre.mechanics}\n\n# Typical entities\n"
            + ", ".join(genre.entities)
            + "\n\n# Recommended sizes\n"
            "16x16 tiles; 48x48 character canvases; subjects fit within about 40x40 logical pixels.\n\n"
            f"# Palette suggestions\n{genre.palette}\n\n# Asset checklist\n\n| Asset | Count |\n|---|---:|\n"
            + "".join(f"| {asset} | {count} |\n" for asset, count in genre.assets.items())
            + f"\n# Animation needs\n{genre.animations}\n\n# Common pitfalls\n"
            + "".join(f"* {pitfall}\n" for pitfall in genre.pitfalls)
            + "\n# Adaptive questions\n"
            + "".join(f"{i}. {question}\n" for i, question in enumerate(genre.questions, 1))
        )
        entries.append(
            Entry(
                id="genres/" + genre.id,
                metadata=Metadata(
                    type="Genre Template",
                    title=genre.title,
                    description=genre.mechanics,
                    tags=[genre.id, "genre", "game-design"],
                    sources=[BOOTSTRAP_SOURCE],
                ),
                body=body,
            )
        )
    for values in yaml.safe_load((directory / "other.yaml").read_text(encoding="utf-8")):
        values["metadata"].setdefault("sources", []).append(BOOTSTRAP_SOURCE)
        entries.append(Entry.model_validate(values))
    for entry in entries:
        entry.metadata.model_extra["x-spriteforge-review"] = "unreviewed"
    return entries


def seed(store: KnowledgeStore) -> int:
    present, _ = store.scan()
    existing = {entry.id for entry in present}
    missing = [entry for entry in seed_entries() if entry.id not in existing]
    legacy = [
        entry
        for entry in present
        if entry.id in {e.id for e in seed_entries()}
        and BOOTSTRAP_SOURCE in [s.model_dump(exclude_none=True) for s in entry.metadata.sources]
        and "x-spriteforge-review" not in entry.metadata.model_extra
    ]
    for entry in legacy:
        entry.metadata.model_extra["x-spriteforge-review"] = "unreviewed"
    if legacy:
        store.write_batch(
            legacy,
            "Clarify bootstrap review designation",
            verified_by="human:project-owner",
            preserve_generation=True,
        )
    if missing:
        store.write_batch(
            missing,
            f"Seed {sum(e.id.startswith('genres/') for e in seed_entries())} genres, "
            "4 art styles, 4 Phaser patterns and Muse prompt pattern",
            verified_by="human:project-owner",
        )
    return len(missing)
