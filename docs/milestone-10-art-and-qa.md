# Milestone 10: better art requests, faster images, art review, QA tests

Plan: `new_plan.md`. One section per milestone, with live results.

## M10.1: an art spec for every Muse Image request

Every Muse request now renders an `ArtSpec` (`game/art.py`) that code builds from the design
entity, the plan pick, the camera and the hero's size:

| field | example (top-down enemy, 14x12) |
| --- | --- |
| view | top-down 3/4 view: the camera is tilted about 45°, we see the top and the front |
| grid | drawn on a 14×12 grid of big square pixels, one solid colour per cell, no anti-aliasing |
| scale_note | the hero is 16 px tall; this is 12 px, three quarters the hero |
| anchor | feet (or base) at the bottom centre (actors), centred (pickups, projectiles), full bleed (tiles, backgrounds) |
| facing / light / outline | facing right; light from the top-left (side view: upper left, so the word "top" never tilts the camera); 1 px dark outline |
| palette | up to 16 hex colours from the style reference, else from hex codes and colour words in `design.look` |
| request_size | the size in `image.sizes` nearest the grid's aspect ratio (square, 1792x1024, 1024x1536) |

`art.json` records each Muse entity's request (size, reasoning strength, calls), spec and
read-back.

**Read-back** (`pixel/grid.py`). The old `detect_grid` looks for blocks up to 64 px; Muse
returns 1600 px images, so a grid of big pixels has 25-100 px cells. `detect_cells` takes the
strong colour boundaries of the keyed subject (short edges under a quarter of the strong ones
are half-cell details and are dropped), finds the most common one-cell gap, and uses the
boundaries themselves as cell edges: a line is added inside gaps that span several cells, and
edges closer than half a cell are skipped. A single lattice did not work on live output: Muse's
cells are a little uneven, differ per axis (32.0 vs 31.7 px), and parts of a sprite can sit on a
grid shifted by half a cell. Each cell's colour is the median of its centre, and
near-identical cell colours are merged.

Confidence is how much better these cells explain the image than the same cells shifted by
half a cell (purity: pixels near their cell's median colour). Big-pixel art scores 0.8-1.0;
a painted background scored 0.27. Two earlier measures failed on live images: gap regularity
scored clean sprites 0.0, and plain purity was 0.92+ for everything. Below
`game.native_grid_confidence` (0.6) the frame is fitted from the raw image as before.

Modes in `art.json`: `native` (cells fit the grid, placed 1:1), `native-resized` (Muse drew
more cells than asked; the clean cells are mode-shrunk), `fit` (no confident grid).

**Checks and one retry.** `check_frame` flags no subject, aspect off by more than 35%, fill
under 20% or over 98% (sprites) and tiles whose opposite edges differ more than neighbouring
columns inside the tile do. The plan named the existing seam check, but
`pixel.validate.seam_scores` uses the maximum difference along the edge and flags every
textured tile, so the tile check is relative. On problems: one retry with the problems in the
prompt and `reasoning_strength: "high"`; then the frame is kept with a warning. Retries count
against `game.max_art_calls`; with the cap reached there is no retry and a warning says so.

Config: `image.sizes`, `game.art_retry: 1`, `game.native_grid_confidence: 0.6`.
`MUSE_SIZE` is gone.

### Probe (`tools/probe_art_spec.py`, 2026-10-31, 2 runs x 3 image calls)

| request | returned | read-back (run 1 / run 2) |
| --- | --- | --- |
| 16x16 knight, 1024x1024 | 1600x1600 | 20x27 cells at 46 px, 0.98 / 29x32 cells at 43 px, 0.98 |
| 320x180 dungeon, 1792x1024 | 2016x1152 | 314x184 cells at 6.4 px, 0.94 / painted, 0.27: fit |
| 10x24 mage, 1024x1536 | 1280x1920 | 32x54 cells at 32 px, 0.99 / 34x55 cells, 1.0 |

(Values from the final detector, re-run on the saved raw images.)

All three sizes are accepted. Muse draws clean big pixels when asked but chooses its own cell
count, about 1.3-3x the requested grid. Naming the block size as a fraction of the image (run
2) did not change that and was dropped. The run-2 knight also faced left although the prompt
said right; the spec cannot enforce facing.

### Live build (survival prompt, 2026-10-31)

`game make` with the survival prompt (`workspace/m10-1-survival`): "Corner Siege", 12
entities, 600 s; the code passed on the first attempt. 3 Spark + 10 image calls, about
$0.23. The library supplied the hero (wizzard) and both tiles; 9 entities went to Muse.
Requests used 1024x1024 for sprites and 1792x1024 for the `dark_veil` background.

* Read-back during that run (earlier detector, gap-regularity confidence): only 2 of 9 on the
  grid (stone brute 57x51 cells, the background 318x182). Most sprites scored 0.0 although
  they were clean big-pixel art, which led to the confidence above. Re-running the final
  `muse_frame` on the same raw images: 9 of 9 on the grid, the gem `native` (16x16 cells
  into 8x8) and the rest `native-resized` (Muse drew 20-100 cells across for 8-16 px
  sprites). Side by side at game size, the cell read-back keeps details the fit loses (the
  slime's eye, the bat's ears, the gem's diamond shape).
* One retry: `cave_bat` (12x10) came back 1.96:1 wide twice (wings spread), so it was kept
  with an aspect warning. The art is right for a bat; the design's box is not. No other
  aspect warnings.
* Not re-run live after the confidence change (the raw images give the same input).

### Sprite sheets: several small frames in one image (added after M10.1, by request)

Muse picks its own pixel count for a sprite that fills the image. Asked for several frames
in one image, it gives each frame only a slot, so each frame has far fewer, bigger pixels,
close to the requested grid, and the frames form an animation.

`tools/probe_sheet.py` (2026-10-31, 4 image calls, 16x16 knight unless noted):

| layout | request | returned | pixels per frame |
| --- | --- | --- | --- |
| walk cycle 4x1 | 2048x512 (new, accepted) | 3200x800 | 12-13 x 14, all `native` (1:1) |
| walk cycle 2x2 | 1024x1024 | 1600x1600 | 18-34 x 32-33 |
| 4 directions x 4 walk frames | 1024x1024 | 1600x1600 | 15-20 x 20-22, same design in all 16 |
| 8x8 gem sparkle 2x2 | 1024x1024 | 1600x1600 | 6 x 7, `native` (a thin sparkle breaks one frame) |

Single images of the same subjects came back 21-32 pixels tall for 16x16.

**Build.** Muse sprites (not tiles or backgrounds) are now drawn as a sheet
(`sheet_prompt`): `game.sheet_frames` (4) columns, one row per animation (idle first, at
most 4 rows), with a pose per frame (walk: left foot forward, passing, right foot forward,
passing; attack: wind-up, strike, follow-through, recover; pickups: glints; ...). The request
size follows the sheet's aspect (4x1 -> 2048x512, 4x2 -> 1792x1024, 4x4 -> square).
`muse_sheet` cuts equal cells and reads each frame:

* all frames of a sheet were drawn at one pixel size, so a frame read at a pitch more than
  20% off the sheet's median is read again at the median (a rock frame read at half pitch
  came out twice as big as the others);
* frames of one row (one animation) share one shrink, so the animation does not pulse;
  rows are scaled separately, so a wide death row does not shrink the idle row;
* a frame without a grid is fitted on its own; an empty cell repeats the first frame, and
  empty cells are a problem for the one retry;
* one palette limit over the whole strip keeps colours identical across frames.

The sprite becomes a strip with `anims: {name: [start, 4]}`, which the SDK already plays.
Locked art is reused as the strip. Offline, the mock image provider draws its sprite in
every cell of a requested sheet. `game.sheet_frames: 1` restores one image per sprite.

**Live (survival prompt, same Spark design shape):** 10 image calls, about $0.24, the same
as with single images. Pixels per frame, single image -> sheet: slime 50x45 -> 16x15, heart
31x24 -> 13x13, rock 27x26 -> 19x15, brute 57x51 -> 29x32, gem 16x16 -> 11x12 (8x8 grid),
magic bolt 20x23 -> 8x8. Every sprite animates (slime: idle, hop, hurt, death; brute: walk,
attack). Still weak: the cave bat spreads its wings 45 pixels wide in a 12x10 box, so its
frames are shrunk hard; the design's box is too small for a flying bat. The "fading away"
death pose came back translucent and keyed to a pink smear; it is now "lying flat,
defeated".

**Second live build** with the row scale, shared pitch and new death pose
(`workspace/m10-1-sheets2`): 13 image calls (3 aspect retries), about $0.26, code passed on
the first attempt. Brute (32x35 per frame for 16x16), portal, rock and heart animate cleanly
and keep their size across frames. Weak: small creatures whose design box does not fit their
pose (grub 12x10 drawn tall, bat 12x8 with spread wings, 8x8 gem shard with sparkles) come
out small and busy after the shrink, and still get aspect warnings.

**Two fixes for small creatures** (tried on request):

* *The frame follows the art* (`game.adapt_size: true`, `art_size`). When the shape Muse drew
  differs from the design box by more than 15%, the sprite's frame takes the drawn shape at
  about the box's area (a 12x8 bat flying with spread wings becomes 13x7; a grub drawn tall in
  a 12x10 box becomes 9x14). The shape comes from the main row: the movement row (walk, run,
  fly) if there is one, else idle; the other rows shrink to fit it. The aspect retry is
  skipped, since the frame now has the art's aspect. The Programmer writes the code after
  the art, from `assets.json`, so it uses the real frame size; locked art keeps its
  `frame_size`. Sizing from all rows was tried first and changed little (wide death poses
  average the shape out); sizing from idle shrank a flying bat's main animation.
* *Tiny sprites are simple* (longest side 10 px or less): one idle row of nearly identical
  poses (other animations show it) and "tiny icons: one bold, simple silhouette with at
  most 4 colours and a dark outline; no sparkles, glow, particles, trails or effects".

**Third live build** (`workspace/m10-1-sheets3`): 7 image calls, about $0.21, no aspect
retries (the second build had 3), code passed on the first attempt. The shambler (14x14 box,
frame 13x15 from the art), brute, portal and the 8x8 magic bolt (a clean 4-frame fireball,
no sparkles) look clean. The 12x10 runner, drawn 35 pixels across, is still small and busy.
The gem got a Meta HTTP 500 and became a placeholder; a failed call now uses the retry and is
sent again unchanged.

### Layout probe: does a denser sheet give smaller frames? (`tools/probe_layouts.py`)

2026-10-31, 18 image calls: 3 subjects, each drawn as N copies of one pose in 6 layouts.
Long side of a frame in pixels (median over copies; the target is the design's long side):

| layout | request | runner (12) | knight (16) | gem (8) |
| --- | --- | --- | --- | --- |
| 1x1 | 1024x1024 | 32 | 24 | 13 |
| 3x2 | 1792x1024 | 30 | 42 | 14 |
| 4x1 | 2048x512 | 44 | 23 | 15 |
| 6x2 | 1536x512 (new, accepted -> 2736x912) | 24 | 22 | 13 |
| 4x4 | 1024x1024 | 24 | 29 | 12 |
| 8x2 | 2048x512 | 39 | 22 | 9 |

* More slots help a little and unevenly: 6x2 was among the best for all three (1.4-2x the
  target against 1.5-2.7x for one image), but no layout reached the target and the same
  layout varies a lot between subjects (4x1: knight 23, runner 44). Muse picks the pixel
  size mostly by subject.
* Copies in one sheet come out the same size (best and median differ by 0-3 px), so picking
  the best copy does not make a sprite smaller. It does help with damage: asked for 6x2,
  Muse drew 7 wolves per row, and equal-cell slicing cut through some of them.
* Equal-cell slicing is therefore fragile whenever Muse draws a different count; slicing by
  connected components (each copy stands alone on magenta) would not be.

### Two game bugs found by playing the build (SDK fixes)

Playing `workspace/m10-1-sheets3` by hand showed two bugs the bot playtest did not:

* *Projectiles froze where they were fired.* The code set a bolt's velocity, then added it
  to `this.physics.add.group()`. Phaser 3.90 applies the group's defaults on add, velocity 0
  included. `sf.js` now keeps a body's velocity on group add unless the group sets a default
  velocity itself. The bot never noticed: enemies walked into the frozen bolts.
* *After a restart the player could not move.* `sf.input` cached each scene's keys by scene
  object; `scene.restart()` keeps the object but Phaser drops its keys, so the restarted
  scene read dead keys. The cache is now cleared on scene shutdown.

Both were reproduced with Playwright on the live build (old SDK: bolt stays at 160,90,
player does not move after restart; new SDK: bolt flies, player moves 72 px in 1 s) and are
covered by `tests/test_game_sdk.py` (fails with the old SDK). Harness quirk found on the way:
`sfRun` stops after one frame once a `lose` event has been counted, also after a restart, so
restart tests must step frame by frame. M10.4's QA tests (real keys, restart, lose path) are
the planned guard against this class of bug.

### Tests

`tests/test_art_spec.py`: prompt wording (view, grid, scale, anchor, light, palette; no "top"
in side view), request sizes, exact read-back of a 14x12 sprite blown up x100 with ±2 px edge
jitter, noise and JPEG (alpha identical, colours within 3, same colour count: JPEG shifts a
colour by a value or two, so bit-exact colours are not possible), fit fallback on a gradient,
retry at high reasoning strength, no retry at the cap, and an empty retry keeping the first
frame. Sheets: prompt layout and poses, one scale per row, an animated strip, and a retry
when sheet cells are empty. `test_game_make` checks that `art.json` records request size and spec.

## M10.2: parallel image calls, request cache, token savings

### Parallel Muse calls (`game/art.py`)

`make_art` resolves library picks and locked art first, as before. Each remaining Muse
entity becomes a `MuseJob` (spec, prompt and request built in the main thread), and its
first call slot is taken from `CallSlots` in `PRIORITY` order *before* anything is sent: the
cap can never be exceeded, and under a cap the same entities get greybox as before. Jobs then
run in a `ThreadPoolExecutor(game.image_workers)` (default 4); retries take the slots that
are left. Each job keeps its own warnings; results and warnings are put back in entity order,
so the output does not depend on which call finishes first. A job that raises becomes a
reported greybox and never cancels the others. Each worker thread gets its own
`MuseImageProvider` (its own httpx client); budget reservations already use `BEGIN
IMMEDIATE`, now covered by a 12-thread test.

One behaviour change: before, a job's retry took a slot before the next job's first call;
now every job's first call is reserved first, so a cap starves retries before it starves
entities.

### Request cache (`game/image_cache.py`)

`<workspace>/cache/muse-image/<sha256>.png`. The key covers model, base URL, endpoint
(generate or edit), prompt, size, reasoning strength, `n` and the SHA-256 of every reference
image. A hit makes no call, needs no call slot, costs nothing, and the source is
`muse (cached)`; its record keeps the raw image, so a later version locks it like any Muse
art. References are part of the key, so the cache cannot answer a request with other pixels
(the AI-allowed rule is decided before a request exists). Writes are atomic. `game make` uses
the cache for live runs only (`--mock` stays deterministic); the G2/G4 stage uses the
workspace next to its state database, mock runs included (their key names the mock model).
`game.image_cache: false` turns it off.

### Spark token savings

* **Repairs and changes as edits** (`codegen.py`). The Programmer answers repair and G4
  change rounds with `CodePatch {edits: [{path, old, new}], notes}`. `apply_patch` applies
  the edits in order; every `old` must occur exactly once at that point, and `old: ""` makes
  `new` the whole file. If any edit fails, nothing is applied and that round asks again for
  complete files (`GameCode`), with the reasons. `report.json` attempts record
  `repair: edits | full files`.
* **Stable prefix.** Every Programmer prompt starts with `code_context`: Phaser notes, SDK
  reference, design JSON, assets. New-game, repair and change prompts for one design share it
  word for word; the parts that change (current files, failures, the instruction, the answer
  format) come after it. The system prompt is the same role prompt for all three.
* **Filtered library list.** `library_context(camera, design)` lists actors named like the
  design's player or enemies (`entity_words`), up to 3 hero options (`hero_candidates`, ranked
  by name match, then animation count), every tile set for the camera, and objects named like
  a pickup, prop or projectile (`name_words`, the keyword pick's rule; matching on the look
  text pulled in "red bar", "small tent" and "stone furnace"). A plan may still pick anything:
  `resolve_plan` checks against the whole library, and the `library_search` MCP tool serves
  the rest. Installed library, top-down: 1802 tokens unfiltered, 226-319 for the M10.1
  survival designs. The reference line now says what a reference may be: in the live build
  below, the Art Director named tile ids as references for 4 entities (harmless: the harness
  picked another style example).
* **Reasoning effort per task.** `llm.reasoning` maps tasks to Spark's `reasoning_effort`:
  `intake`, `art_plan` and `revision` low; `design` and `code` high. Each Spark request also
  sends `prompt_cache_key: spriteforge-<task>`. G1 sets the effort on its provider, so the
  role-based `propose` keeps its signature.

### Probes

`tools/probe_spark_reasoning.py` (2026-10-31, 3 runs of 3-4 calls, about $0.03 in all).
Meta documents a top-level `reasoning_effort` (minimal, low, medium, high, xhigh, max; "none"
returns HTTP 400). Live:

* it is accepted and works: on the same question, `low` used 113-128 reasoning tokens and
  `max` 65-455 (mostly 260-455). `max` is the strongest setting Spark accepts (for M10.3's
  review);
* prefix caching is automatic and works, also across efforts: 1521 of 1558-1583 prompt
  tokens were cached. A shared prefix with a different question, 60 s later, hit too. The
  cache fills a few seconds after a request: a call sent right after the first one missed
  every time.

`tools/probe_atlas.py` (1 image call): 6 different 16x16 subjects in 3x2 slots on magenta, at
1792x1024 (returned 2016x1152). Keying and slicing by connected components found exactly 6
slices and no specks, each the right subject, in reading order. All 6 read back on a grid
(confidence 0.82-0.97), but at 13x12 to 41x18 cells, with cell sizes from 19 to 40 px: one
image does not make Muse draw its sprites at one pixel size. Measurement only; not wired in.

### Live build (survival prompt, 2026-10-31, `workspace/m10-2-survival`)

"Corners of Doom: 10-Minute Survival", 12 entities, 7 checks, 600 s. The library supplied the
hero (wizzard), the skeleton enemy and both tiles; 8 entities went to Muse. 9 image calls +
4 Spark calls, about $0.27. The first game failed its playtest (no `enemy_kill`,
`gem_collect` or `level_up` events); the repair came back as edits, applied cleanly, and the
second attempt passed.

* **Image phase: 76.7 s** for 8 jobs, against about 105 s for 7 calls one at a time in M10.1.
  Four calls started together; each took 16-26 s (M10.1: about 15 s one at a time), so 8 jobs
  needed two rounds (about 47 s), and the bolt's HTTP 500 and its retry added a third.
* **8 workers do not help.** The same art phase again with `image_workers: 8` and no cache
  (`workspace/probes/art-timing`, 9 calls): with 8 in flight every call took 38-48 s, 53.2 s in
  all. Meta's throughput per key looks about fixed, so the default stays 4. **The under-40 s
  target is not met**: parallel calls bring 8 images to about 45-50 s.
* The bolt request (a 4x1 sheet at 2048x512) got HTTP 500 on 3 of 4 tries across both runs,
  while the other 2048x512 sheets succeeded, so it looks request-specific. In the second run
  it ended as greybox.

Spark tokens (input / output / of which reasoning):

| call | M10.1 builds | this build |
| --- | --- | --- |
| design (high) | 1112 / 4236-8025 / 2016-6024 | 1112 / 4912 / 2761 |
| art plan (low) | 3259-3346 / 3304-5371 / 2355-4266 | **1426** / 2484 / 1615 |
| game code (high) | 4620-4990 / 17316-21748 / 9037-14989 | 4884 / 18344 / 9315 |
| repair as edits (high) | (none) | 13248 / 11954 / 9820 |

* The repair's visible output was 2134 tokens against 9029 for the full game: **76% less**.
  Total output (with reasoning) was 35% less, because the high-effort reasoning (about 9.8k
  tokens) is spent either way. The acceptance asks for 50% fewer output tokens than a
  full-file repair; no full-file repair is in any ledger, so this compares with the first
  full write. Met for the visible code, not for the total.
* `cached_tokens` was 0 on the repair, although its first ~4.5k tokens match the first call's
  and the probe showed that shared prefixes hit. One difference the probe does not have is the
  response schema (`CodePatch` vs `GameCode`), which may be placed before the messages; this
  is not verified. Consecutive repair rounds share the `CodePatch` schema.

### Tests

`tests/test_art_parallel.py`: peak concurrency 4; wall time with 0.5 s calls under the local
work plus 3 calls (6 calls one at a time); the same `filled` order, images and warnings as one
worker; cap 3 with 6 jobs gives 3 calls, greybox for the 3 lowest-priority entities and 3
image attempts in the ledger; one failing job becomes a placeholder with a warning while 5
succeed; a cache hit makes no call (even at cap 0), and the key changes with reference bytes,
reasoning strength, size, endpoint and model; the previous image (an edit's reference) is in
the key; 12 threads reserving against a cap of 5 get exactly 5. The plan's "under 2x one call
for 6 jobs" is not possible with 4 workers (two rounds of calls), so the bound is 3 calls.

`tests/test_codegen_patch.py`: edits apply in order and add files; ambiguous, missing or
misplaced `old` text lists every failed edit; through `make_game`, an ambiguous repair makes
the round ask for complete files; programmer, repair and change prompts share the whole
`code_context` (at least 90% of the SDK reference + design); the filtered library list on a
synthetic library keeps the skeleton, the knight and a heart object and drops the orc, the
tavern citizen, the body with its fishing animation and a frying pan, under 600 tokens (a
second test runs on the installed library when it is present).

Also: `test_game_stage` (a second run in the same workspace gets every image from the cache
with 0 image calls, and its rebuild locks that art), `test_game_make` (repairs scripted as
edits, `repair: edits` in attempts), `test_providers` (effort and cache key in the request
body).

**Found in M10.3:** a cache write could fail the image call that produced the image. Under a
long folder path the temp file name (64-character key plus a 32-character suffix) passed
Windows' 260-character limit, the write raised, and the call was counted as failed (its image
thrown away). Cache writes are now best effort with a short temp name
(`test_cache_write_failure_never_fails_the_call`).

## M10.3: Art Reviewer (all the art together, in the running game)

### What it sees (`game/art_review.py`, `game/playtest.py`, `sdk/sf.js`)

* **Sheets.** Every entity's first frame side by side at game size on a neutral grey
  (`review_sheets`), and the same sheet at 4x with each sprite numbered. Sprites and tiles go
  8 to a row with cells sized by their row; backgrounds are shrunk to 80 px wide and get their
  own row (in the live run below, one shared cell size made the 4x sheet about 2000x1700 with
  tiny sprites; with the rows it is 704x428).
* **Screenshots.** The playtest steps logic only (`headlessStep`), so nothing is drawn.
  `sfShot(fps)` draws one frame (the same game logic as any step) and returns the canvas.
  `playtest(capture_at=...)` stops at early, middle and late times (15%, 50% and 85% of the
  session, `review_times`) and takes a shot; a session that ends earlier gets one last shot at
  the end. A test checks that the event counts match a run without shots. Shots at 320x180
  are doubled for the reviewer.
* **Pre-checks** (`game/art_checks.py`, free):
  * contrast: the larger of the body's and the outline ring's luminance difference against
    the floor tile (a tile named floor, ground, grass, ...; else the first tile), flagged
    under 0.12. The plan's mean luminance alone flagged outlined library heroes that read
    fine in play (0.39 on a 0.43 floor);
  * hue: an enemy and a pickup whose dominant hues (the main peak of their saturated pixels)
    are within 20 degrees;
  * size: the drawn long side outside 0.5-1.6x of the design's, or a size relative to the
    hero off by 2x either way;
  * placeholders;
  * silhouette: under 20% of its bounding box filled, or a subject under half its frame.

  On the art of the 5 live builds so far: the grey golem on a grey floor, a dark-green hero on
  grass (borderline), and red hearts next to red-brown enemies (3 builds), plus the one
  placeholder. Nothing else.
* **The design's entities** and the pre-check findings, in the prompt.

### The review: one Spark vision call, blind naming test

`Art Reviewer` (`agents/roles/art-reviewer.md`, grantable like the other roles; the role text
names the read-only `library.*` tools) answers `ArtReview {scores {consistency, readability,
perspective, scale, palette} 1-5, named [{slot, seen_as}], issues [{slot, problem, severity,
fix_prompt}]}` at `reasoning_effort: max` (`llm.reasoning.review`), with the 2 sheets and 3
screenshots as images.

The plan's answer is keyed by entity. Here it is keyed by sheet number so the naming test
stays blind in one call: the sheet has numbers only, and the prompt lists the entities in
alphabetical order without numbers (the pre-check findings are given by number). Code maps
numbers back to entities and then:

* adds a **major** issue when a name misses: no meaningful word of `seen_as` (colours, sizes
  and "blob", "thing", "creature" ... do not count) is in the entity's id, look or library
  source, with simple plurals and the library's synonyms (`mage` = `wizzard`);
* adds a **blocking** issue for a placeholder the reviewer did not report;
* ignores issues on unknown numbers (noted in the report).

If the review call fails, the build keeps going with the pre-check review and says so.
Offline (`--mock`), `fallback_review` builds the answer from the pre-checks alone: one issue
per finding with its severity (placeholder and empty frame blocking, the rest major or minor),
scores lowered per finding, no naming test.

### Fixes before G4

* Blocking issues are grouped per entity, ordered player, enemies, pickups, ... and at most
  `game.review_max_fixes` (4) entities are fixed, once each (no retry: `art_retry` does not
  apply), as one Muse Image call at `game.review_image_reasoning` (`high`) with the M10.1 spec,
  through the same `Muse` jobs as the art step (parallel, cache, own call slots, so the cap is
  `max_art_calls + review_max_fixes`).
* What is sent: Muse art is an edit of its raw Muse image; AI-allowed library art is an edit
  of its first frame (scaled up); art from an `ai_allowed: false` pack is drawn fresh from text
  (the report says so); a placeholder is drawn fresh. Parallax backdrops are not fixed
  automatically (the code uses their layer ids); their issues stay suggestions.
* The fix becomes the entity's pick (`reason: "art review: ..."`), and the build's
  `art-plan-reviewed.json` carries it. The next version starts from that plan, so the fixed
  art locks like any Muse art. `resolve_plan`'s "library first" rule no longer replaces such
  explicit redraws (a review fix of a library skeleton, or G4 feedback on one) with the library
  sprite. Before, that rule silently ignored G4 feedback on library entities.
* The same code is built with the new art and the session played again; no second review. If
  a game that passed fails with the new art, the fixes are reverted and the report says why.
  A failed fix call keeps the current art.

### G4

`report.json` gains `art_review`: who reviewed, scores, the naming results, the pre-check
findings, every issue with its number, origin (review, naming test, pre-check) and status
(fixed / not fixed: why / suggestion), the fixes with before/after pictures
(`game/vN/review/<entity>-before.png`, `-after.png`), the replay result and the review's
inputs (`review/sheet.png`, `sheet-4x.png`, `shot-N.png`). The G4 page shows them, with
**Apply #n** on every issue that is not fixed. Apply submits the gate with
`form: {"apply_suggestion": n}`; the stage turns it into a `Revision` with one art change
(`<look>; <fix_prompt>`) for that entity without asking the Director, so it is an ordinary
targeted rebuild. Checked in a browser on a mock run: clicking Apply #2 built v2 with only that
entity redrawn and the review-fixed sprite locked from v1.

Config: `game.review: true`, `game.review_max_fixes: 4`, `game.review_image_reasoning: high`,
`game.review_cost_cap: 0.08`, `llm.reasoning.review: max`.

### Live (survival prompt through the gates, 2026-11-01, `workspace/m10-3-stage`)

The survival prompt went G1 -> G2 -> build v1 -> G4 with every gate before G4 approved as is.
"Corner Swarm: 10 Minute Survival" passed its playtest on the first attempt. The library gave
the hero (rogue), the floor and the wall tiles; 7 entities went to Muse (one HTTP 500, sent
again). 7 Spark + 11 image calls, about $0.28 for the whole run.

* **Review**: `muse-spark-1.3 (vision, reasoning max)`: consistency 2, readability 2,
  perspective 2, scale 4, palette 2; 8 issues, 3 blocking and 5 major. The review call cost
  **$0.045** (3692 input tokens with the 5 images, 9540 output of which 8730 reasoning, 114 s).
* **Fixes**: 3 Muse edits at high strength ($0.03), all kept, and the replay passed. **Review
  plus fixes: $0.075** (the cap is $0.08).
  * `void_background`: Muse had drawn an isometric arcade room with cabinets. It had copied the
    subject of its style example, although the prompt says not to, and the room peeked out
    behind the arena. The reviewer called it a placeholder from another game and the wrong
    camera; the fix is the dark void the design asks for (its faint grid lines are lost at
    320x180).
  * `floor_tile` (library, AI-allowed pack): a tan stone floor in a neon-on-dark game; edited
    into dark purple stone. The edit came back with edges that do not repeat (a warning; no
    retry for fixes).
  * `bullet`: given a dark outline.
* **Suggestions at G4** (major, each with Apply): the hero confusable with the cyan gem, the
  crawler and the bat brown or orange instead of the red and purple of the design and lost on
  the floor, the gem too close to the hero's teal, and a flat wall tile with no 3/4 cue.
* **Naming test**: 6 of 10 named in words of their entity; the 4 misses (hero "tiny teal
  humanoid figure", wall "flat dark grey-green square", bullet "tiny yellow-white blob",
  background "isometric arcade room interior") were all issues already.
* The G4 page was opened in a browser on this run: scores, the 3 fixes with before/after
  pictures, the replay, 8 issues with Apply on the 5 suggestions, and no page errors.
* Not re-run live after the sheet-row change above (see the follow-up below).

### Follow-up: the four gaps of the first live run

* **Colours came from the style example.** The art spec's palette line ("use only these
  colours") was the style example's palette whenever there was one. The rogue body sheet's
  tans and browns made the red crawler orange and the purple bat brown (2 of the 8 issues),
  and the void was told to use only a floor tileset's tans and greens. Now the colours the
  request names win (`art_spec(text=...)`, `look_palette`: "dark purple" is darkened, a colour
  after "remove", "no", "instead" ... in its clause is left out), as "Colours: ..., with
  darker and lighter shades", and the reference gives technique only ("do not draw its
  subject, objects or layout, and do not use its colours"). With no colour named, the
  example's palette still keeps the set together (`palette_from: reference`).
* **The background fix came out flat black.** Not the fix itself: its faint grid lines, 106 px
  apart, were read back as the pixel grid (18x11 cells for a 320x180 scene) and blown up.
  A tile or background grid read under half the asked size in either direction is now
  treated as drawn lines and the image is fitted instead (`info.note` says so).
* **A fixed tile that does not repeat** is now blended by code (`make_tileable`: each edge
  band fades into the tile shifted by half, the middle stays as drawn), for any Muse tile
  still failing the seam check after its retries, so fixes (no retry) are covered too.
  Reason: "edges blended by code: the drawn tile did not repeat".
* **Cost at the full fix cap.** `game.review_cost_cap: 0.08`: what the review call cost
  decides how many of `review_max_fixes` fit at `budget.image_cost` each (`fix_cap`); the
  reasoning stays `max`. Blocking issues past it stay suggestions ("not fixed: review cost
  cap reached ..."). At the first run's $0.045 review, 3 fixes fit, not 4. The report's
  `art_review.cost` (review, fixes, cap) is on the G4 page.

Live again (survival prompt, 2026-11-01, `workspace/m10-3-followup`): "Corner Siege:
10-Minute Survival", passed on the second attempt (the first missed its `win` event; the
repair came back as edits). The library gave the hero (rogue), the floor and the wall; 7
entities went to Muse. 8 Spark + 9 image calls, $0.275 in all, no failed calls.

* The Muse sprites now have the design's colours: the reviewer named a "bright green slime
  blob", a "tiny red horned imp", a "purple horned brute monster" and a "small cyan diamond
  gem", and the void came out dark blue-black (it still shows stone blocks seen at 3/4: the
  request asked for "near-black stone" and the background spec asks for a 3/4 view; hidden
  behind the arena in play, and the reviewer raised nothing on it).
* **Review**: consistency 2, readability 3, perspective 2, scale 4, palette 2; 4 issues (2
  blocking, 2 major) against 8 (3 blocking); 1 naming miss against 4. One run each with
  different designs and non-deterministic Muse art, so this is a sign, not a measurement.
* **Fixes**: the runner (drawn in side profile; the edit asked for a 3/4 view, and at 11x9 px
  the change is hard to judge) and the flat library wall (now a brown block with a grey top
  and a dark outline; its edges passed the seam check, so it was not blended). The replay
  passed. **Review $0.037 + fixes $0.02 = $0.057** of the $0.08 cap.
* **Suggestions at G4**: the library floor is tan, not the grey stone the Art Director read
  into its name, and the recoloured library hero is a dark speck at game size. Both are
  library art, which the palette change does not touch.
* The 4x sheet with rows is 704x388: sprites at a readable size, the background on its own
  row.
