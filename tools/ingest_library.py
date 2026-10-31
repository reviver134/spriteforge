"""Rebuild assets/library/ from assets/inbox/ zips and characters/ folders (the record of how
each pack was added).

Run: python tools/ingest_library.py            # every pack whose zip or folder is present
     python tools/ingest_library.py luizmelo-monsters
"""

import shutil
import sys
from pathlib import Path

from spriteforge.library.store import add_pack, library_root, load_packs, write_pack

ROOT = Path(__file__).parents[1]
INBOX = ROOT / "assets" / "inbox"
CHARACTERS = ROOT / "characters"

# Shared terms for the three GandalfHardcore packs (from the pack's READ ME.txt).
GANDALF_TERMS = (
    "License (READ ME.txt in the original download): use in commercial and non-commercial games "
    "and projects, modify freely; NO reselling, repackaging or redistributing, NO AI training, NO "
    "use in 'game development tools', NFTs or printed materials. Hence: git-ignored, "
    "ai_allowed=false (never sent to Muse), and only used in games the user builds, not bundled "
    "with the harness."
)
FROM_FOLDER = (
    " Re-ingested from characters/ on 2026-10-30 (no license file in that folder); license as "
    "recorded when the pack was first added."
)

PACKS: dict[str, dict] = {
    # One entry per pack: `zip` (in assets/inbox/) or `folder` (in characters/), plus any
    # add_pack arguments and `rename` / `grid`, which this script handles:
    #
    # "my-forest-pack": {
    #     "zip": "My_Forest_Pack.zip",        # or "folder": "My Forest Pack"
    #     "title": "My Forest Pack",
    #     "category": "tileset",              # default for files not matched by `categorize`
    #     "categorize": {"enemies/": "enemy", "hero/": "character", "props/": "prop"},
    #     "cell": "auto",                     # or (16, 16) for tile sheets
    #     "tags": ["top-down"],               # camera: "top-down" or "side-view"
    #     "author": "...", "source_url": "https://...",
    #     "license": "CC0", "license_verified": True,
    #     "redistributable": True,            # False keeps raw files out of git
    #     "ai_allowed": True,                 # False: never sent to Muse as a reference
    #     "faces": "right",                   # which way side-facing sprites look
    #     "notes": "What the pack contains and how it was checked.",
    # },
    "anokolisa-pixel-crawler": {
        "folder": "Pixel Crawler - Free Pack",
        "title": "Pixel Crawler - Free Pack 2.11 (Anokolisa)",
        "category": "prop",
        # First match wins; paths are POSIX-style relative to the pack folder.
        "categorize": {
            "Entities/Mobs/": "enemy",
            "Entities/Characters/": "character",
            "Entities/Npc's/": "character",
            "Tilesets/": "tileset",
            "MockUps/": "background",
            "Weapons/": "item",
            "Static/Tools": "item",
            "Static/Resources": "item",
        },
        "cell": "auto",
        "grid": {"tileset": 16},
        "name_depth": 4,  # every animation file is called e.g. Run-Sheet.png
        "tags": ["top-down", "16px"],
        "author": "Anokolisa",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": True,
        "notes": (
            "Terms.txt (original download): free for commercial and non-commercial projects, "
            "credit optional (appreciated), may be modified, but the art itself may not be sold "
            "or marketed as a product; kept out of git as a precaution. 16x16 tile grid. "
            "Characters are single-row sheets of 64x64 (some 32x32) frames with ~24 px figures; "
            "side/up/down directions. Body_A is an unclothed base body with the full animation "
            "set (idle, walk, run, slice, pierce, crush, collect, carry, fishing, death); "
            "Knight/Rogue/Wizzard NPCs and Orc/Skeleton crews have idle/run/death. Prop sheets "
            "are not uniform grids." + FROM_FOLDER
        ),
    },
    "luizmelo-monsters": {
        "folder": "Monsters_Creatures_Fantasy",
        "title": "Monsters Creatures Fantasy (Luiz Melo)",
        "category": "enemy",
        "categorize": {"All Characters": "other"},
        "cell": "auto",
        "name_depth": 2,  # "Goblin/Run.png" -> "goblin run"
        "tags": ["side-view", "platformer", "monster"],
        "author": "Luiz Melo",
        "source_url": "https://luizmelo.itch.io/monsters-creatures-fantasy",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": False,
        "notes": (
            "No license file in the download; free to use per the user (hackathon). Store-page "
            "terms not captured, so treated conservatively: git-ignored and never sent to Muse. "
            "Side view; 150x150 frames with 35-50 px creatures (matches 32 px tiles). Goblin, "
            "Skeleton, Mushroom: idle, run/walk, attack, take hit, death (+ skeleton shield). "
            "Flying eye: flight, attack, take hit, death." + FROM_FOLDER
        ),
    },
    "gandalfhardcore-platformer": {
        "folder": "GandalfHardcore FREE Platformer Assets",
        "title": "GandalfHardcore FREE Platformer Assets",
        "category": "prop",
        "categorize": {
            "Background layers": "background",
            "cloud": "background",
            "sun.png": "background",
            "Tiles": "tileset",
            "BG Dirt": "tileset",
            "Campfire": "effect",
            "Portal": "effect",
            "Torch": "effect",
            "blizzard": "effect",
        },
        "grid": {"tileset": 32},
        # "Normal BG/GandalfHardcore Background layers_layer 1.png" ->
        # "normal bg gandalfhardcore background layers layer 1" (library/tilesets/gandalf-seasons.json).
        "name_depth": 2,
        "tags": ["side-view", "32px"],
        "author": "GandalfHardcore",
        "source_url": "https://gandalfhardcore.itch.io/",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": False,
        "notes": (
            GANDALF_TERMS + " Side-view terrain on a 32 px grid with grass / autumn / snow "
            "variants (Floor Tiles, Other Tiles incl. spikes), house and water tiles, 5-layer "
            "parallax backgrounds (normal/autumn/winter, 1024x346), trees, decor, campfire/"
            "torch/portal sheets. No enemies. The .gif is the animated background preview "
            "(first frame imported)." + FROM_FOLDER
        ),
    },
    "gandalfhardcore-characters": {
        "folder": "GandalfHardcore Character Asset Pack",
        "title": "GandalfHardcore FREE Character Asset Pack",
        "category": "character",
        "cell": (80, 64),
        "name_depth": 2,  # "Male Clothing/Blue Pants.png" -> "male clothing blue pants"
        "tags": ["side-view", "human", "clothing"],
        "author": "GandalfHardcore",
        "source_url": "https://gandalfhardcore.itch.io/",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": False,
        "faces": "left",
        "notes": (
            GANDALF_TERMS + " Layered paper-doll sheets (skin, hair, clothing, sword), each "
            "800x448 = 10x7 grid of 80x64 frames, figures ~44 px tall. Rows: idle 5, walk 8, "
            "run 8, jump/fall 4, attack 4, attack 6, death 10 (composite the layers in that "
            "order: skin, boots/shoes, pants/skirt, shirt/corset, hair, sword). The layers are "
            "not actors on their own: no compositing code is installed." + FROM_FOLDER
        ),
    },
    "gandalfhardcore-hp-bar": {
        "folder": "GandalfHardcore Hp bar",
        "title": "GandalfHardcore HP bar",
        "category": "ui",
        "tags": ["hud", "health", "hp"],
        "author": "GandalfHardcore",
        "source_url": "https://gandalfhardcore.itch.io/",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": False,
        "notes": GANDALF_TERMS + " HP bar frame plus red/yellow/blue fills." + FROM_FOLDER,
    },
    "cainos-top-down-basic": {
        "folder": "Texture",
        "title": "Pixel Art Top Down - Basic v1.2.3 (Cainos)",
        "category": "prop",
        # "Extra/" sheets are props with baked shadows, so they must match before "Shadow".
        "categorize": {
            "Extra/": "prop",
            "Shadow": "effect",
            "Tileset": "tileset",
            "Struct": "tileset",
            "Player": "character",
        },
        "strip_prefix": "TX ",
        "cell": (32, 32),
        "tags": ["top-down", "32px", "outdoor"],
        "author": "Cainos",
        "source_url": "https://cainos.itch.io/pixel-art-top-down-basic",
        "license": "FREE-USE",
        "license_verified": True,
        "redistributable": False,
        "ai_allowed": True,
        "notes": (
            "No license file in the download. Free to use per the user for a non-commercial "
            "hackathon build; treated as free-to-use but NOT redistributable, so the files are "
            "git-ignored. Docs: https://docs.cainos.net/pixel-art-top-down-basic. 32x32 px "
            "grid, 3/4 top-down. Textures only. '-extra' files are the prop/plant sheets with "
            "baked shadows; 'shadow*' sheets are separate shadow layers. characters/Scene "
            "Overview.png is this pack's demo scene (not imported)." + FROM_FOLDER
        ),
    },
    "32x32-pixelart-armour": {
        "folder": ".",  # loose armourNN*.png files at the top of characters/
        "include": "armour",
        "title": "32x32 Pixel Art Assets 04: Armour",
        "category": "equipment",
        "subcategory": "armour",
        "tags": ["icon", "outfit"],
        "license": "CC0",
        "license_verified": True,
        "rename": {
            "magiciansrobe": ("magicians robe", ["mage", "cloth", "light", "robe"]),
            "whiteshirt": ("white shirt", ["cloth", "light", "noble"]),
            "velvetoutfit": ("velvet outfit", ["cloth", "light", "noble"]),
            "chainmail": ("chainmail", ["metal", "medium", "mail"]),
            "monksrobe": ("monks robe", ["cloth", "light", "hood", "robe"]),
            "platemail": ("plate mail", ["metal", "heavy", "plate", "mail"]),
            "leatherjerkin": ("leather jerkin", ["leather", "light"]),
        },
        "notes": (
            "Inventory/pickup icons, single 32x32 sprites, transparent background. No license "
            "file in the download. License CC0 as stated by the user on 2026-10-30; download "
            "page URL not yet recorded. characters/Hands.png is not part of this pack (origin "
            "unknown, not imported)." + FROM_FOLDER
        ),
    },
    "ptdesigns-cozy-fall-fireside": {
        "folder": "cozy_fall_fireside_living_sprites_v1.0",
        "include": "sprites/",  # skip the atlas, source sheet and contact-sheet previews
        "title": "Cozy Fall: Fireside Living (24 props)",
        "category": "prop",
        "tags": ["cozy", "autumn", "interior", "ai-generated"],
        "author": "PtDesigns",
        "source_url": "https://ptdesigns.itch.io/cozy-fall-pixel-art-props-tiles",
        "license": "CC0-1.0",
        "license_verified": True,
        "notes": (
            "CC0 per LICENSE.txt in the original zip. AI-generated by the author. Painted "
            "pixel-art style with soft anti-aliased edges, isometric-inspired view, 110-285 px "
            "per prop, uncoordinated relative scale, single frame. Not on a 16/32 px grid: "
            "needs downscaling + palette quantisation before use in a game; best as cozy "
            "interior decor or a style reference." + FROM_FOLDER
        ),
    },
}


def source_path(spec: dict) -> Path:
    return INBOX / spec["zip"] if "zip" in spec else CHARACTERS / spec["folder"]


def ingest(pack_id: str) -> None:
    spec = dict(PACKS[pack_id])
    source = source_path(spec)
    spec.pop("zip", None), spec.pop("folder", None)
    rename = spec.pop("rename", {})
    grid = spec.pop("grid", {})
    shutil.rmtree(library_root() / pack_id, ignore_errors=True)
    add_pack(source, pack_id, **spec)
    pack = next(p for p in load_packs() if p.id == pack_id)
    assets = []
    for a in pack.assets:
        slug = a.id.split("/", 1)[1]
        if slug in rename:
            name, tags = rename[slug]
            a = a.model_copy(update={"name": name, "tags": sorted({*a.tags, *tags})})
        if a.category in grid:
            g = grid[a.category]
            a = a.model_copy(update={"cell": (g, g), "frames": (a.size[0] // g) * (a.size[1] // g)})
        if a.category in {"character", "enemy"} and a.frames > 1:
            a = a.model_copy(update={"tags": sorted({*a.tags, "animated"})})
        assets.append(a)
    write_pack(pack.model_copy(update={"assets": assets}))
    print(f"{pack_id}: {len(assets)} assets")


if __name__ == "__main__":
    wanted = sys.argv[1:] or [k for k, v in PACKS.items() if source_path(v).exists()]
    for pack_id in wanted:
        ingest(pack_id)
    # Keep the agents' OKF catalog in step with the library (workspace/memory, if present).
    workspace = ROOT / "workspace"
    if (workspace / "memory").exists():
        from spriteforge.memory.cli import open_store
        from spriteforge.memory.okf.catalog import sync_catalog

        print("memory catalog:", sync_catalog(open_store(workspace)))
