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

### Tests

`tests/test_art_spec.py`: prompt wording (view, grid, scale, anchor, light, palette; no "top"
in side view), request sizes, exact read-back of a 14x12 sprite blown up x100 with ±2 px edge
jitter, noise and JPEG (alpha identical, colours within 3, same colour count: JPEG shifts a
colour by a value or two, so bit-exact colours are not possible), fit fallback on a gradient,
retry at high reasoning strength, no retry at the cap, and an empty retry keeping the first
frame. `test_game_make` checks that `art.json` records request size and spec.
