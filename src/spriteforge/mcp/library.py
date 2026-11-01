"""Read-only asset library surface: agents look up the art a game can be built from.

Run with `spriteforge library mcp` (stdio); the library folder is SPRITEFORGE_LIBRARY or
assets/library.

Answers come from the same files the game art step uses (index.json, pack.json, tile sets,
actor grouping), so every id returned here is one an ArtPlan may name. Only metadata leaves
the server, never pixels, so `ai_allowed: false` packs are safe to list; their flag is in every
answer so planners do not pick them as Muse references.
"""

from collections import Counter

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from spriteforge.game.actors import canon, load_actors, words
from spriteforge.game.art import PERSPECTIVE_TAGS, actor_sheet, reference_assets, whole_objects
from spriteforge.game.tilesets import load_tilesets
from spriteforge.library.store import load_packs, search, tile_descriptions

PERSPECTIVES = {"side": "side", "side-view": "side", "topdown": "topdown", "top-down": "topdown"}
CAMERAS = {"side": "side-view", "topdown": "top-down"}
USAGES = ("ship", "ship-with-credit", "reference-only")
# What `spriteforge library describe` says an image shows (and its measured look).
DESCRIBED = ("what", "description", "view", "look")


def perspective(camera: str) -> str:
    if camera not in PERSPECTIVES:
        raise ValueError(f"camera must be one of {sorted(PERSPECTIVES)}")
    return PERSPECTIVES[camera]


def query_words(text: str) -> set[str]:
    """Brief words plus their creature synonyms ('undead' also finds 'skeleton')."""
    found = words(text)
    return set(found) | canon(found)


def asset_summary(a: dict) -> dict:
    """What a planner needs; hashes and bounding boxes stay in index.json."""
    keys = ("id", "name", "category", "pack", "file", "size", "cell", "frames", "tags", "usage")
    return (
        {k: a[k] for k in keys}
        | {"ai_allowed": a.get("ai_allowed", True)}
        | {k: a[k] for k in DESCRIBED if a.get(k)}
    )


def search_assets(
    query: str = "",
    category: str = "",
    camera: str = "",
    usage: str = "ship-with-credit",
    limit: int = 20,
) -> list[dict]:
    """Assets by words in their name or tags (2 points a word) and in what their description
    says they show (1 point), best first."""
    if usage not in USAGES:
        raise ValueError(f"usage must be one of {USAGES}")
    tags = PERSPECTIVE_TAGS[perspective(camera)] if camera else set()
    wanted = query_words(query)
    hits = []
    for a in search(category=category, usage=usage):
        if tags and not tags & set(a["tags"]):
            continue
        named = set(words(a["name"])) | set(a["tags"])
        seen = query_words(" ".join(a.get(k, "") for k in ("what", "description", "look")))
        score = 2 * len(wanted & named) + len(wanted & seen)
        if wanted and not score:
            continue
        hits.append((-score, a["id"], a))
    return [asset_summary(a) for *_, a in sorted(hits)[: max(1, min(limit, 100))]]


def create_server() -> FastMCP:
    server = FastMCP("SpriteForge asset library", log_level="ERROR")
    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    def library_packs() -> list[dict]:
        """Installed art packs: camera, contents by category, license usage, AI and redistribution flags, notes. Start here."""
        out = []
        for p in load_packs():
            cameras = {t for a in p.assets for t in a.tags if t in {"top-down", "side-view"}}
            out.append(
                {
                    "id": p.id,
                    "title": p.title,
                    "author": p.author,
                    "camera": sorted(cameras),
                    "contents": dict(sorted(Counter(a.category for a in p.assets).items())),
                    "license": p.license,
                    "usage": p.usage,
                    "ai_allowed": p.ai_allowed,
                    "redistributable": p.redistributable,
                    "faces": p.faces,
                    "notes": p.notes,
                }
            )
        return out

    @server.tool(annotations=read)
    def library_search(
        query: str = "",
        category: str = "",
        camera: str = "",
        usage: str = "ship-with-credit",
        limit: int = 20,
    ) -> list[dict]:
        """Find assets by words in their name, tags or description (what the image shows, its colours), best matches first. category: character, enemy, tileset, prop, item, equipment, ui, effect, background, other. camera: side-view or top-down. usage 'ship-with-credit' (default) returns only art a build may include."""
        return search_assets(query, category, camera, usage, limit)

    @server.tool(annotations=read)
    def library_actors(camera: str = "", query: str = "") -> list[dict]:
        """Animated heroes and enemies (sheets grouped by creature) with frame counts per animation. Use `key` as the asset of an ArtPlan pick with source=actor; the camera must match the game's."""
        view = perspective(camera) if camera else ""
        wanted = query_words(query)
        packs = {p.id: p for p in load_packs()}
        notes = {a["id"]: a for a in search()}
        out = []
        for actor in load_actors():
            if (view and actor.side() != view) or (wanted and not wanted & actor.tokens):
                continue
            out.append(
                {
                    "key": actor.key,
                    "name": actor.name,
                    "pack": actor.pack,
                    "role": "hero" if actor.category == "character" else "enemy",
                    "camera": CAMERAS[actor.side()],
                    "faces": actor.faces,
                    "animations": {
                        verb: len(frames) for verb, frames in sorted(actor.anims.items())
                    },
                    "ai_allowed": packs[actor.pack].ai_allowed,
                }
                | {k: v for k in DESCRIBED if (v := notes.get(actor_sheet(actor), {}).get(k))}
            )
        return out

    @server.tool(annotations=read)
    def library_tiles(camera: str = "") -> dict:
        """What a game world can be built from for a camera (top-down or side-view): installed tile sets (named tiles, variants with their trigger words, parallax backdrops), whole objects for pickups/props/projectiles, and the packs Muse may use as style references."""
        view = perspective(camera) if camera else ""
        tags = PERSPECTIVE_TAGS[view] if view else set()
        other = set().union(*PERSPECTIVE_TAGS.values()) - tags if view else set()
        tilesets = load_tilesets(CAMERAS[view] if view else "")
        objects = [o for o in whole_objects().values() if not other & set(o["tags"])]
        tile_notes = tile_descriptions()
        return {
            "camera": CAMERAS[view] if view else "any",
            "tilesets": [
                {
                    "id": ts["id"],
                    "camera": ts["camera"],
                    "tile_size": ts["tile_size"],
                    "summary": ts["summary"],
                    "keywords": ts["keywords"],
                    "variants": {name: v["words"] for name, v in ts["variants"].items()},
                    "tiles": {
                        f"{ts['id']}/{n}": {"what": t["what"]}
                        | {
                            k: v
                            for k in ("description", "look")
                            if (v := tile_notes.get(f"{ts['id']}/{n}", {}).get(k))
                        }
                        for n, t in ts["tiles"].items()
                    },
                    "backdrop": bool(ts.get("backdrop")),
                }
                for ts in tilesets
            ],
            "whole_objects": [asset_summary(o) for o in sorted(objects, key=lambda o: o["id"])],
            "muse_reference_packs": sorted({a["pack"] for a in reference_assets().values()}),
        }

    return server
