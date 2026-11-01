# Asset library

Third-party pixel art the harness can search. One folder per pack; `index.json` is the flat
catalog agents query (`spriteforge.library.search`). Never edit files by hand; re-run `add`.

| Usage | Meaning |
| --- | --- |
| `ship` | Verified CC0 / public domain / your own art: builds may include it as-is. |
| `ship-with-credit` | Verified CC-BY, OGA-BY or MIT: builds include it plus a credits line. |
| `reference-only` | License unknown or restrictive: style reference for people only, never in a build or sent to Muse. |

## Adding a download

1. Put the `.zip` in `assets/inbox/` and keep the download page URL.
2. Check the license on that page.
3. Ingest it:

```bash
python -m spriteforge library add assets/inbox/tiny-dungeon.zip --pack kenney-tiny-dungeon \
  --category tileset --cell 16x16 --author Kenney --license CC0 --verified \
  --source-url https://kenney.nl/assets/tiny-dungeon
python -m spriteforge library license kenney-tiny-dungeon CC0 --source-url https://...
python -m spriteforge library search --category equipment --usage ship
```

Categories: `character`, `enemy`, `tileset`, `prop`, `item`, `equipment`, `ui`, `effect`,
`background`, `other`. Mixed packs: `--categorize` from Python (`add_pack(categorize={...})`).

Packs marked `redistributable: false` (free to use, but raw files may not be shared) are
listed in the generated `.gitignore`, so they stay on this machine and out of a public repo.
The pack's own LICENSE/README/manifest files are kept in `<pack>/meta/`.

## Current packs

Rebuild everything with `python tools/ingest_library.py` (it records how each pack was added;
a recipe reads a zip from `assets/inbox/` or a folder from `characters/`). `ai_allowed: false`
packs must never be sent to Muse (`search(ai=True)` excludes them).

| Pack | From `characters/` | Contents | AI use | In git |
| --- | --- | --- | --- | --- |
| `anokolisa-pixel-crawler` | `Pixel Crawler - Free Pack/` | top-down 16 px heroes, orcs, skeletons, NPCs, tiles, props | yes | no |
| `cainos-top-down-basic` | `Texture/` | top-down 32 px grass/stone/wall tiles, plants, props | yes | no |
| `luizmelo-monsters` | `Monsters_Creatures_Fantasy/` | side-view goblin, skeleton, mushroom, flying eye | **no** | no |
| `gandalfhardcore-platformer` | `GandalfHardcore FREE Platformer Assets/` | side-view 32 px terrain (3 seasons), parallax, props | **no** | no |
| `gandalfhardcore-characters` | `GandalfHardcore Character Asset Pack/` | side-view paper-doll layers (no compositing installed) | **no** | no |
| `gandalfhardcore-hp-bar` | `GandalfHardcore Hp bar/` | HP bar frame and fills | **no** | no |
| `32x32-pixelart-armour` | `armour*.png` | 7 armour icons (CC0) | yes | yes |
| `ptdesigns-cozy-fall-fireside` | `cozy_fall_.../sprites/` | 24 painted cozy interior props (CC0) | yes | yes |

`characters/Hands.png` (origin unknown) and `characters/Scene Overview.png` (Cainos demo
scene) are not imported. Tile sets for these packs live in `src/spriteforge/library/tilesets/`.

## Descriptions: what each image shows

```bash
python -m spriteforge library describe            # after adding or changing a pack
python -m spriteforge library describe --mock     # offline: measured looks only
```

Every image (and every named tile of a tile set) gets a **measured look** from its pixels:
main colours by name, brightness (0-1) and detail ("tan; mid (0.49); low-contrast detail").
Images from packs that allow AI use also get a **description** from Muse Spark (vision): what
it is, one sentence on what it shows, and the camera it is drawn for, 16 images per call on a
numbered contact sheet (`--max-calls`, `--max-cost` cap it; the ledger is
`<workspace>/library.sqlite3`). `ai_allowed: false` packs are never sent to a model: their
entry is the file name plus the measured look.

They are written to `descriptions/<pack>.json`, keyed by the image's sha256 (a changed image is
described again, an unchanged one is not sent twice), and merged into `index.json`, so
`search()`, the MCP tools, the Art Director's library list and the `catalog/` pages show them.
Model-written entries are `status: generated`: correct the `text` (or `what`, `view`), set
`status: reviewed`, and later runs keep your words.

## Agents use the library through MCP

`spriteforge library mcp` serves four read-only tools (registered as the `library` server in
`workspace/mcp.json`): `library_packs`, `library_search`, `library_actors` and
`library_tiles(camera)`. They return metadata, ids and descriptions, never pixels;
`library_search` also matches words in the descriptions ("slime", "tan").

Use `--cell WxH` for sprite sheets so frames are counted.
