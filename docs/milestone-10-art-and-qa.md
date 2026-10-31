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
out small and busy after the shrink, and still get aspect warnings. Next lever: let the design
size follow what Muse drew, or ask for fewer, simpler poses for 8 px sprites.

### Tests

`tests/test_art_spec.py`: prompt wording (view, grid, scale, anchor, light, palette; no "top"
in side view), request sizes, exact read-back of a 14x12 sprite blown up x100 with ±2 px edge
jitter, noise and JPEG (alpha identical, colours within 3, same colour count: JPEG shifts a
colour by a value or two, so bit-exact colours are not possible), fit fallback on a gradient,
retry at high reasoning strength, no retry at the cap, and an empty retry keeping the first
frame. Sheets: prompt layout and poses, one scale per row, an animated strip, and a retry
when sheet cells are empty. `test_game_make` checks that `art.json` records request size and spec.
