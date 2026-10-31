> **Superseded by milestone 9 (2026-10-31).** Kits were removed: Muse Spark now writes each game's
> code from the prompt (`docs/milestone-9-generated-games.md`). Kept for history; the tile skins
> live on as `src/spriteforge/library/tilesets/`.

# Milestone 8: genre kits (template-first games)

## Why

Gate 2 asked models to invent every block, the room layout and the perspective on each run.
Results mixed perspectives (top-down wall stacks in a side-scroller), cost up to 16 image calls
and still produced one static frame. Kits flip this: a hand-built, playable base owns camera,
scale, collision, level layout and movement feel; agents change **data** (`game.json`) and **art**
(sprite slots). The user sees a moving 20 s autoplay of a real build, not a mock-up.

## What exists now

| Piece | Where |
| --- | --- |
| Kit contract (slots, legend, mechanics, tuning, demo track) | `src/spriteforge/kits/models.py` |
| Side-scroll platformer kit (80x12 level, stomp/double jump/key door) | `src/spriteforge/kits/platformer/` |
| Top-down 3/4 adventure kit (two rooms, sword/chase/key door) | `src/spriteforge/kits/topdown/` |
| Shared Phaser runtime: input, autopilot, HUD, events, frame capture | `src/spriteforge/kits/runtime/runtime.js` |
| Mechanic modules toggled by `game.json` | `src/spriteforge/kits/runtime/mechanics.js` |
| Build: kit + config + slot overrides -> playable folder | `src/spriteforge/kits/build.py` |
| Deterministic 20 s video + poster + gameplay event report | `src/spriteforge/kits/record.py` |
| Greybox placeholder generator | `tools/build_kit_placeholders.py` |
| CLI: `spriteforge kit list / choose / build / record / play` | `src/spriteforge/kits/cli.py` |
| Tests (17, incl. both autoplays clearing their level) | `tests/test_kits.py` |

### Contracts agents work through

* **Sprite slots** have a fixed size and animation strip (`hero`: idle 2, run 4, jump, fall,
  hurt at 32x32). An override may be the full strip or **one frame**, which is repeated across
  every animation, so a single Muse call per slot already gives a moving preview.
* **`game.json`** may only set tuning knobs inside kit bounds and enable/parameterise mechanics
  the kit declares (`GameConfig.resolved` rejects everything else).
* **Levels** are character grids validated against the kit legend (exactly one player spawn,
  covers the screen). A Level Designer may supply a new grid; the default is the kit level.
* **Demo tracks** are position-conditioned autopilot steps (`move until_x`, `jump`, `goto`,
  `fight`), so they survive tuning changes better than timed key presses.

### Determinism

Capture stops Phaser's real-time loop at scene creation and advances fixed 1/30 s steps.
Enemy motion uses scene-relative time and deaths fade on the scene clock (Phaser tweens read
`performance.now()`). Same build + same demo => byte-identical frames (tested).
`demo-report.json` lists events (pickups, stomps, hurts, `level_clear`, demo timeouts) so QA can
fail a build whose autoplay no longer reaches the exit.

## Sources and licensing

* No code or art was taken from Frogatto & Friends or Evoland 2. Frogatto's engine is zlib but
  its game content is mostly proprietary, and it is a C++ engine; Evoland 2 is commercial and
  closed. Both are cited only as design references in the kits' `design_notes`.
* Placeholder art is generated greybox (this repo). CC0 packs (e.g. Kenney Pixel Platformer,
  Tiny Dungeon) can be dropped in per slot with `--slot ID=PATH` if their frames are cut to the
  slot sizes; they were not bundled because their download hosts were unreachable from the
  build environment.
* Phaser 3.90.0 (MIT) is reused from `templates/phaser/vendor`.

## Not done yet (next milestone)

1. **G2a gate**: after G1, `choose_kit(brief.perspective, brief.genre)`, build with
   placeholders, record, and show the video + playable link (0 image calls).
2. **Slot restyling**: for each slot, call Muse `edit` with the placeholder strip frame as the
   shape/pose reference plus the approved style anchor, run the existing pixel pipeline to the
   slot size, then rebuild. Order: hero, enemies, tiles, pickups. Budget: 12-14 calls.
3. **ChangeRequest targets**: add `tuning`, `mechanic` and `level` targets beside asset/palette;
   only asset targets spend image calls.
4. Hero-only extra frames (run cycle) once the single-frame path is approved.
5. A third kit (top-down arena shooter) if time allows; isometric maps to top-down for now.

## Update: briefs choose and customise library art (`spriteforge kit compose`)

`src/spriteforge/kits/compose.py` turns a one-line brief into a build and a `plan.json` that
records every decision with its reason:

1. **Kit** from whole-word camera cues (platformer/side vs top-down/dungeon).
2. **Skin** (`kits/skins/*.json`): curated tile picks per installed pack, chosen by keywords,
   with variants (e.g. `autumn`/`winter` rows of a sheet, matching parallax backdrop).
   A skin sets the build scale: 32 px packs build the kit at 2x (32 px tiles, 640x360).
3. **Actors**: library sheets grouped as `<creature> <verb>`. Scored by creature names in the brief (with synonyms such as
   undead -> skeleton, eyeball -> eye), perspective, animation coverage and variety (a second
   slot avoids repeating a creature when the brief asked for another). The side-view flying
   slot takes flyers; if the brief asks for a walker instead it becomes a ground patrol.
4. **Adapt**: frames anchored on one feet line, integer-shrunk (mode filter) or doubled to fit
   the slot, own frame counts kept (<= 8), hitbox derived from the art.
5. **Customise**: colour words bind to the creature right after them ("red orc shamans") and hue-shift that sprite only.

No model is called, so packs with `ai_allowed: false` are safe here. Pickups, door, shots and
pots still use greybox placeholders (no matching library art yet).

Demo autopilot gained `stomp` (run at the next ground enemy, steer onto it in the air, never
across a gap via `until_x`) and `move dir=none` (walk to x from either side). Respawn points
are only recorded over solid ground.

The third-party packs these briefs were verified against have been removed from the library (2026-10-30); re-verify once new packs are ingested.

## Update: OKF catalog for agents (`spriteforge memory sync-catalog`)

Agents navigate knowledge with memory_index / memory_search / memory_show, so kits and art are
mirrored into the OKF corpus under `catalog/` (`src/spriteforge/memory/okf/catalog.py`):
`catalog/guide` (walk kit -> skin -> actors, license rules), `catalog/kits/*` (slots,
animations, mechanics, tuning ranges), `catalog/skins/*`, `catalog/packs/*` (license, usage,
AI and redistribution flags, facing, notes) and `catalog/actors/*` (animations, which kit
slots they fit). Entries are derived from files, written through `KnowledgeStore.write_batch`
(git commit, secret check, indexes), only rewritten when their text changes, and deprecated
when their source disappears. `tools/ingest_library.py` re-syncs automatically. The Director,
Game Designer, Art Director and Librarian role prompts now point at `catalog/`.

## Update: G2 library mode in `start --agents` (Spark picks from the catalog)

With `preview.mode: kit` (default) and library art installed, the run goes
G1 genre -> brief -> follow-ups -> **G2 kit_preview** instead of the Muse building blocks:

1. `stages/kit_preview.py` asks the **Director (Muse Spark)** for a `KitChoice`
   (`kits/interpret.py`): kit, skin + variant, one catalog actor per character slot, optional
   recolours, hero outfit, mechanics, tuning, title and a reason per pick. The prompt carries the
   brief plus the OKF `catalog/` guide, indexes and kit slot tables (synced first if missing).
2. `kits/compose.resolve_choice` checks every ID against the real library and kit (slot role,
   perspective, skin/kit match, tuning ranges, known mechanics). Invalid parts are replaced by the
   keyword pick and listed under `warnings` in plan.json; OKF slugs and `-`/`_` variants of actor
   IDs are accepted.
3. The build is recorded (20 s autoplay) and the gate shows `kit/vN/demo.mp4` and `plan.json`
   (who chose: the Spark model or "mock (keyword pick)", each slot's reasons, autoplay result,
   warnings) with a **Play this build** link served from `/api/runs/<id>/play/vN/`.
4. **Request changes**: the feedback text plus the previous choice go back to Spark ("change only
   what the feedback asks for") -> v2. **Approve**: `approval.json` (`mode: kit`) and the run
   completes.

No image calls are made in this mode. `--mock` runs answer with the keyword pick, so the whole
gate works offline. Set `preview.mode: muse` to get the original Muse Image preview; it also runs
automatically when no library art is installed. Tests use an empty library by default
(`tests/conftest.py`), so the Muse G2 tests are unaffected; `tests/test_kit_preview.py` covers
the library flow end to end, including path-traversal rejection on the play route.

## Update: genres without a kit are flagged, not hidden

A live farming-life-sim brief was answered with the top-down combat kit and reasons that read
as a fit. Now: the G1 genre picker says which genres have a playable kit; the preview compares
the brief genre with each kit's `genres` (deterministic, independent of the model); Spark must
list unsupported features in `missing_features` and may not claim a kit supports them; when no
kit covers the genre the gate title says "CLOSEST MATCH ONLY" and the first plan warning lists
what the game will not have. `plan.json` carries a `coverage` block.

## Update: description -> library art + Muse gap fill -> feedback loop (G2 kit mode)

The G2 library preview now covers the whole "find art, make what's missing, iterate" loop.

1. **The harness knows the library.** `memory sync-catalog` writes `catalog/library`: every
   pack (camera, contents by category, usage, AI flag), every actor, every *whole object*
   (a single sprite or one-row strip usable directly in a key/coin/door/pot/shot slot), which
   packs Muse may see as a style reference, and which kit slots each kit + installed skin
   leaves uncovered. Each `catalog/packs/<pack>` entry now lists every asset by category.
2. **Description first.** After the G1 questions the Game Designer writes `description.json`
   (+ `.md`): title, pitch, camera, player, world, enemies, objects, look, loop, goal.
3. **Art plan.** Spark answers `KitChoice` with a new `art` list: one pick per uncovered slot,
   `library` (whole-object id), `muse` (prompt + reference asset) or `placeholder`. Muse may
   also take a hero/enemy slot when no actor matches. Slots the agent forgets get the keyword
   default (matching library object, else Muse); `placeholder` keeps greybox on purpose.
4. **Enforced in code** (`kits/art.py`): library picks must be shippable whole objects; Muse
   references must come from `ai_allowed`, shippable packs (others are replaced, with a
   warning; no AI-allowed art at all -> text-only call); pixel-art references are preferred
   over painted ones; tile references come from the chosen skin's sheet, character references
   from a chosen actor's idle sheet. Output is keyed, cropped, mode-filtered into the slot
   (solid tiles opaque, overlay/partial tiles and sprites transparent, characters and doors
   standing on the bottom row) and quantised to the reference's palette.
5. **Cap and lock.** `preview.max_art_calls` (default 6) per version, filled in priority order
   hero/enemy -> pickups -> props -> tiles -> projectiles; the rest stays greybox with a warning.
   Muse art is saved as `kit/vN/art/<slot>.png|-raw.png|.json`; a later version reuses it for
   free while the slot's prompt and reference are unchanged, and turns a changed prompt into an
   **edit** of the previous image (previous raw first, style reference second).
6. **One feedback box.** The `kit_preview` gate sets `empty_feedback_approves`; the UI shows a
   single submit button. Submitting text makes v(N+1) (Spark revises only what the feedback is
   about); submitting it empty approves, writes `approval.json` (now with `description` and the
   locked `art` files) and completes the run. The API accepts `{"action": "submit", "text": ...}`
   only on gates that opt in.
7. Fixed: the server and UI refused "Request changes" on `kit_preview`, so the loop only worked
   from tests; and secret scrubbing blanked the `key` slot's entries in plan.json.

Offline (`--mock`): the keyword pick is the agent, mock images are synthetic, and a feedback
sentence naming an art slot ("make the key golden") appends to that slot's prompt, so the
lock/edit path is exercised without a network. Tests: `tests/test_art_fill.py`.

Next: game logic and stages on top of the approved build (`approval.json`).

## Update: homestead kit (farming and world building)

Farming and world-building briefs used to fall back to the top-down combat kit (skeletons, a
sword, a key door) under "CLOSEST MATCH ONLY", or through `kit compose` to the platformer. They
now get `kits/homestead`: top-down 3/4, no combat, 40x24 map.

| Verb | Key | Mechanic (game.json) |
| --- | --- | --- |
| till grass, plant a seed in soil, harvest a ripe crop | Space/Z on the tile in front | `farming` (grow_ms, seeds) |
| chop a tree (wood), mine a rock (stone) | Space/Z, `hits` swings | `gathering` (hits, wood, stone) |
| sell every harvested crop | Space/Z at the shipping crate | `selling` (price) |
| place a 2x2 hut on free grass | X | `building` (wood, stone) |
| animals wander, never hurt | - | `critters` |
| win: coins and huts reached | - | `goal` (coins, huts) |

Slots: hero, critter (new `critter` role: harmless), sprout, crop, tree, rock, bin, house, hut,
grass, grass_alt, path, soil, water, fence. A skin (see `kits/skins/README.md`) can supply the
tiles and library objects the props; everything else is planned for Muse (cap raised to 8 per
version). The 20 s autoplay plants three crops, chops
two trees, mines a rock, builds a hut, harvests, sells and meets the goal at about 14.5 s
(`demo` steps gained `use` and `build`).

Routing: genre `farming-life-sim` or the new `world-building` seed picks this kit; free-text
briefs pick it when growing/building words (farm, crops, build, village, sandbox, "world
building", ...) outnumber fighting words, so a stray "mine" in a crypt brief stays top-down.

Fixed on the way: `KitChoice.art` was optional, which Meta's strict json_schema mode rejects
(any live G2 call would 400); a guard test now checks every Spark schema. Camera words in a
brief ("top-down") no longer count as creature names (`body top` was being chosen as the hero).
Characters filled by Muse or library objects keep the kit's designed collision body.

Next: day cycle, energy, shops/NPCs and more structure types are game logic for the next stage.

## Update: third-party packs removed

All eight previously ingested packs, their zips, their tile skins, the dress-up hero code built for one of them (`hero_gender` / `hero_color` are gone from `KitChoice`) and their ingest recipes were deleted on 2026-10-30. The library is empty until new packs are added with `tools/ingest_library.py`; until then every kit builds from greybox placeholders plus Muse art drawn from text prompts (no style references are available).

## Update: characters/ packs and the asset library MCP service

The user added the art as a plain folder, `characters/` (git-ignored: most packs may not be
redistributed). `tools/ingest_library.py` recipes now read a zip from `assets/inbox/` **or** a
folder from `characters/` and rebuild all eight packs with the license, AI and redistribution
flags recorded at their first ingest (GandalfHardcore and Luiz Melo stay `ai_allowed: false`).
The two tile skins were restored: `gandalf-seasons` (platformer, normal/autumn/winter rows with
5-layer parallax + castle) and `pixel-crawler-dungeon` (topdown). Folder ingest now yields POSIX
paths, so `categorize` rules match on Windows.

`spriteforge library mcp` is a read-only MCP server (`src/spriteforge/mcp/library.py`) so agents
can query the library during any stage instead of only reading the catalog snapshot:

| Tool | Answers |
| --- | --- |
| `library_packs` | packs, camera, contents by category, usage, AI/redistribution flags, notes |
| `library_search(query, category, camera, usage, limit)` | assets ranked by name/tag words (creature synonyms included) |
| `library_actors(camera, query)` | animated heroes/enemies, frames per animation, kit slots they fit |
| `library_environment(kit)` | kit slots, installed skins + variant words, actors per slot, whole objects, uncovered slots, Muse reference packs |

Registered as `library` in `workspace/mcp.json`, granted to Director, Game Designer, Art Director
and Level/Tile Designer, per-call confirmation off (read-only, metadata only, no pixels).

Gaps: no side-view hero actor (GandalfHardcore characters are paper-doll layers and the
compositing code was removed), so the platformer hero falls to Muse or the placeholder; no
homestead skin yet (Cainos/Pixel Crawler grass, soil and fence cells are not mapped).
