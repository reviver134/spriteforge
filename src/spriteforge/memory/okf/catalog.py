"""Mirror asset packs, tile sets and actors into the OKF corpus under `catalog/`.

Agents navigate knowledge through memory_index / memory_search / memory_show, so the art
inventory must live there too, not only in assets/library/index.json. Everything here is
derived from files on disk (pack.json, library/tilesets/*.json); re-running the sync rewrites
only entries whose text changed and deprecates entries whose source disappeared (e.g. the kit
and skin entries from before games were written from the prompt).
"""

from spriteforge.game.actors import ANIM_SOURCES, load_actors
from spriteforge.game.art import reference_assets, whole_objects
from spriteforge.game.tilesets import load_tilesets
from spriteforge.library.store import library_root, load_packs

from .models import Entry, Metadata
from .store import KnowledgeStore

SOURCE = {"resource": "spriteforge catalog sync", "author": "spriteforge/catalog"}
USAGE_TEXT = {
    "ship": "may be used in builds as-is",
    "ship-with-credit": "may be used in builds with a credits line",
    "reference-only": "reference only: never put into a build",
}
CAMERA = {"side": "side-view", "topdown": "top-down"}


def entry(ident: str, type_: str, title: str, description: str, tags, body: str, source: str):
    return Entry(
        id=ident,
        metadata=Metadata(
            type=type_,
            title=title,
            description=description,
            tags=sorted({t.lower() for t in tags if t}),
            sources=[{**SOURCE, "id": source}],
        ),
        body=body.strip() + "\n",
    )


def guide_entry() -> Entry:
    body = """
# How a game is made
Muse Spark writes every game from the human's request; there are no pre-built game templates.
1. The Game Designer writes a design doc: rules, every entity the player sees, progression,
   difficulty over time, win/lose, and *checks* (events the code must emit) - G2 gate.
2. The Art Director picks art per entity: the local library first (actors, whole objects,
   tile-set tiles, parallax backdrops), Muse Image for anything the library lacks.
3. The Programmer writes the Phaser 3 code against the SpriteForge SDK; the harness checks it,
   playtests the whole session with the game's own bot and sends failures back - G4 gate.

# What the library offers
Start at `catalog/library` (everything at a glance), then `catalog/packs/<pack>` (every asset
by category), `catalog/actors/<actor>` (animations, camera) and `catalog/tilesets/<id>`.

# License rules (enforced in code, repeated here for planning)
* Only packs whose usage is `ship` or `ship-with-credit` may appear in a build.
* `ai_allowed: false` packs must never be sent to Muse as references or edit inputs; their art
  may still be used in a build as-is.
* `redistributable: false` packs stay on this machine (git-ignored); builds may include them.
* Side-view art faces right in a game; packs marked `faces: left` are mirrored.
* Library first: if the library has the same kind of creature or object for the game's camera,
  it is used; otherwise Muse Image draws it from a concrete prompt with an AI-allowed style
  reference. Greybox appears only when the per-build image cap is reached or Muse fails.
"""
    return entry(
        "catalog/guide",
        "Catalog Guide",
        "How games are made and how agents use the art library",
        "Design -> art (library first, else Muse) -> code; license rules.",
        ["catalog", "guide", "license", "asset", "actor", "tileset", "muse"],
        body,
        "guide",
    )


def tileset_entry(ts: dict) -> Entry:
    tiles = "".join(f"| `{ts['id']}/{n}` | {t['what']} |\n" for n, t in ts["tiles"].items())
    variants = "".join(
        f"* `{name}`: for worlds described with {', '.join(v['words']) or '(default)'}\n"
        for name, v in ts["variants"].items()
    )
    body = f"""
# Summary
{ts["summary"]}

Camera: **{ts["camera"]}**, {ts["tile_size"]} px tiles. Pack: [{ts["pack"]}](../packs/{ts["pack"]}.md).
Fits worlds described with: {", ".join(ts["keywords"])}.
{"Has a parallax backdrop (ArtPlan source=backdrop, asset=" + ts["id"] + ")." if ts.get("backdrop") else ""}

# Variants
{variants}
# Tiles (ArtPlan source=tile)
| Asset | What |
|---|---|
{tiles}
"""
    return entry(
        f"catalog/tilesets/{ts['id']}",
        "Tile Set",
        ts["id"].replace("-", " ").title(),
        f"{ts['camera']} {ts['tile_size']} px tiles from {ts['pack']}: {ts['summary']}",
        ["tileset", "tiles", ts["camera"], *ts["keywords"], *ts["variants"], *ts["tiles"]],
        body,
        f"src/spriteforge/library/tilesets/{ts['id']}.json",
    )


def pack_entry(pack) -> Entry:
    counts: dict[str, int] = {}
    for a in pack.assets:
        counts[a.category] = counts.get(a.category, 0) + 1
    tags = sorted({t for a in pack.assets for t in a.tags})
    grid = sorted({f"{a.cell[0]}x{a.cell[1]}" for a in pack.assets if a.frames > 1})
    body = f"""
# Summary
{pack.title} by {pack.author}. {len(pack.assets)} assets:
{", ".join(f"{n} {c}" for c, n in sorted(counts.items()))}.

# License and handling
* License: **{pack.license}** ({"verified" if pack.license_verified else "NOT verified"}).
* Usage: `{pack.usage}`: {USAGE_TEXT[pack.usage]}.
* AI use: {"allowed" if pack.ai_allowed else "**forbidden**: never send to Muse"}.
* Redistributable: {"yes" if pack.redistributable else "no (git-ignored, local only)"}.
* Side-view art faces: {pack.faces}.
* Source: {pack.source_url or "not recorded"}.

# Notes
{pack.notes}

# Grids
Animation/tile cells: {", ".join(grid) or "single images"}. Files live under
`assets/library/{pack.id}/`.

# Contents
{pack_contents(pack)}
"""
    return entry(
        f"catalog/packs/{pack.id}",
        "Asset Pack",
        pack.title,
        f"{pack.license} ({pack.usage}{'' if pack.ai_allowed else ', no AI'}): "
        + ", ".join(f"{n} {c}" for c, n in sorted(counts.items())),
        ["asset-pack", pack.id, pack.license, pack.usage, *tags[:20]],
        body,
        f"assets/library/{pack.id}/pack.json",
    )


def _asset_line(a) -> str:
    grid = f"{a.cell[0]}x{a.cell[1]}" + (f" x{a.frames}" if a.frames > 1 else "")
    return f"`{a.id}` {a.name} ({grid})"


def pack_contents(pack) -> str:
    """Every asset by category, so an agent can see that a pack has, say, bonfires or icons."""
    lines = []
    for cat in sorted({a.category for a in pack.assets}):
        items = [a for a in pack.assets if a.category == cat]
        shown = items if len(items) <= 40 else items[:40]
        more = f" ... and {len(items) - 40} more" if len(items) > 40 else ""
        lines.append(
            f"* **{cat}** ({len(items)}): " + "; ".join(_asset_line(a) for a in shown) + more
        )
    return "\n".join(lines)


def library_entry() -> Entry:
    """One page an agent reads to know the whole library: packs, actors, objects, tile sets."""
    packs = {p.id: p for p in load_packs()} if library_root().exists() else {}
    rows = []
    for p in packs.values():
        counts: dict[str, int] = {}
        for a in p.assets:
            counts[a.category] = counts.get(a.category, 0) + 1
        views = sorted({t for a in p.assets for t in a.tags if t in {"top-down", "side-view"}})
        rows.append(
            f"| `{p.id}` | {', '.join(views) or 'any'} | "
            + ", ".join(f"{n} {c}" for c, n in sorted(counts.items()))
            + f" | {p.usage} | {'yes' if p.ai_allowed else '**no**'} |"
        )
    actors = load_actors() if packs else []
    actor_lines = "\n".join(
        f"* `{a.key}` ({CAMERA[a.side()]} {'hero' if a.category == 'character' else 'enemy'}): "
        + ", ".join(sorted(a.anims))
        for a in actors
    )
    objects = whole_objects() if packs else {}
    object_lines = "\n".join(
        f"* `{i}` {o['category']}: {o['name']} ({o['cell'][0]}x{o['cell'][1]}"
        + (f", {o['frames']} frames" if o["frames"] > 1 else "")
        + ("" if o.get("ai_allowed", True) else ", no AI")
        + ")"
        for i, o in sorted(objects.items())
    )
    tilesets = load_tilesets() if packs else []
    tile_lines = "\n".join(
        f"* `{ts['id']}` ({ts['camera']}, {ts['tile_size']} px): " + ", ".join(ts["tiles"])
        for ts in tilesets
    )
    refs = sorted({a["pack"] for a in reference_assets().values()}) if packs else []
    body = f"""
# Packs
| Pack | Camera | Contents | Usage | AI use |
| --- | --- | --- | --- | --- |
{chr(10).join(rows) or "| (no packs installed) | | | | |"}

Details per pack (every asset by category): `catalog/packs/<pack>`.

# Actors (animated characters and enemies; ArtPlan source=actor)
{actor_lines or "none installed"}

# Tile sets (ArtPlan source=tile / backdrop)
{tile_lines or "none installed"}

# Whole objects (pickups, props, projectiles; ArtPlan source=object)
{object_lines or "none installed"}

# Muse style references
Packs Muse may see as a style example: {", ".join(f"`{r}`" for r in refs) or "none"}.
Anything the library lacks is drawn by Muse Image (source=muse with a concrete prompt).
"""
    return entry(
        "catalog/library",
        "Asset Library",
        "What the local pixel-art library contains",
        f"{len(packs)} packs, {len(actors)} actors, {len(tilesets)} tile sets, "
        f"{len(objects)} whole objects",
        ["catalog", "library", "asset", "pack", "actor", "tileset", "muse", "reference"],
        body,
        "assets/library/index.json",
    )


def actor_slug(key: str) -> str:
    return key.replace("/", "-").replace(" ", "-")


def actor_entry(actor, packs: dict) -> Entry:
    pack = packs.get(actor.pack)
    camera = CAMERA[actor.side()]
    anims = ", ".join(f"{a} x{len(f)}" for a, f in sorted(actor.anims.items()))
    usable = sorted(k for k, sources in ANIM_SOURCES.items() if set(sources) & set(actor.anims))
    flying = "fly" in actor.anims or "eye" in actor.tokens
    role = "playable character" if actor.category == "character" else "enemy"
    body = f"""
# Summary
{actor.name.title()} from [{actor.pack}](../packs/{actor.pack}.md); {camera} art,
{role}{", flies" if flying else ""}. Use it in an ArtPlan as source=actor, asset=`{actor.key}`
for a {camera} game.

# Animations
{anims}. Can stand in for animations: {", ".join(usable)}.

# Handling
Usage `{pack.usage if pack else "unknown"}`; AI use {"allowed" if pack and pack.ai_allowed else "forbidden"};
faces {actor.faces}. Matched from requests by the words: {", ".join(sorted(actor.tokens))}.
"""
    return entry(
        f"catalog/actors/{actor_slug(actor.key)}",
        "Actor",
        f"{actor.name.title()} ({actor.pack})",
        f"{camera} {'hero' if actor.category == 'character' else 'enemy'}"
        f"{' (flying)' if flying else ''}: {anims}",
        ["actor", camera, actor.category, actor.pack, *actor.tokens],
        body,
        f"assets/library/{actor.pack}",
    )


def catalog_entries() -> list[Entry]:
    packs = {p.id: p for p in load_packs()} if library_root().exists() else {}
    entries = [guide_entry(), library_entry()]
    entries += [tileset_entry(ts) for ts in load_tilesets()]
    entries += [pack_entry(p) for p in packs.values()]
    if packs:
        entries += [actor_entry(a, packs) for a in load_actors()]
    return entries


def sync_catalog(store: KnowledgeStore) -> dict[str, int]:
    """Write changed catalog entries; deprecate ones whose source is gone. One git commit."""
    present, _ = store.scan()
    current = {e.id: e for e in present if e.id.startswith("catalog/")}
    wanted = {e.id: e for e in catalog_entries()}
    changed = [
        e
        for e in wanted.values()
        if e.id not in current
        or current[e.id].body != e.body
        or current[e.id].metadata.description != e.metadata.description
        or current[e.id].metadata.status != "stable"
    ]
    gone = []
    for ident, old in current.items():
        if ident not in wanted and old.metadata.status != "deprecated":
            meta = old.metadata.model_copy(update={"status": "deprecated"})
            gone.append(Entry(id=ident, metadata=meta, body=old.body))
    if changed or gone:
        store.write_batch(
            changed + gone,
            f"Catalog sync: {len(changed)} updated, {len(gone)} deprecated "
            f"({len(wanted)} packs/tile sets/actors)",
            actor="spriteforge/catalog",
        )
    return {"total": len(wanted), "updated": len(changed), "deprecated": len(gone)}


__all__ = ["catalog_entries", "sync_catalog"]
