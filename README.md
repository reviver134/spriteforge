# SpriteForge

Local-first pixel-game builder. Milestone 1 supplies the foundation and a mock demo;
Milestone 2 adds deterministic pixel processing, validation, palettes, atlases, and
previews. Game generation, human approval gates, MCP integration, and knowledge
retrieval are later milestones. The revised milestone plan supersedes the original;
provider-based asset production belongs to Milestone 7.

Python 3.11+:

```powershell
python -m pip install -e ".[dev]"
spriteforge start --mock
spriteforge runs
```

The browser opens at http://127.0.0.1:8765. The demo takes about ten seconds and
shows a timeline and two synthetic images. Ctrl+C or kill the process, then use
`spriteforge resume <id>` to continue. Use `--no-open-browser` for headless sessions.
`start` without `--mock` opens the existing-run viewer. No live generation workflow
is started by the foundation UI.

The default workspace is `~/spriteforge-workspace`; use `--workspace PATH` to override
it. Initialization creates `runs/`, `memory/`, `skills/`, `mcp.json`, and
`state.sqlite3`. Existing workspace files are preserved. SQLite stores configuration,
checkpoints, append-only events, budgets, and image bytes, so a database backup alone
can restore a run and its artifacts. An interrupted provider call may consume a budget
reservation before its checkpoint commits; reservations are intentionally conservative.

Configuration lives in `src/spriteforge/config.yaml` (or `--config PATH`). Model IDs,
base URLs, endpoint paths and budgets are configurable. The image cap defaults to
150 per run; model call caps and an estimated image-cost cap also apply. LLM token
pricing is not estimated in this milestone.

Live provider adapters read `META_API_KEY` only from the environment. The CLI also
loads that variable from `.env` in the current directory when it is absent from the
environment; existing environment values take precedence. `.env` is ignored by Git,
and its values are never copied into configuration or the event journal. Never put keys
in configuration. HTTP errors expose status and response text with credentials
redacted. No telemetry or external frontend dependencies. Model-provided output
must pass the `chat_json` schema validator (one corrective retry).

Meta contracts checked against [image docs](https://dev.meta.ai/docs/image-generation),
[image understanding](https://dev.meta.ai/docs/image-understanding), and
[structured outputs](https://dev.meta.ai/docs/structured-output). Images use
base64 responses and JSON inline references for edits. Requested sizes are aspect
ratio hints, not guaranteed pixel dimensions. Live account access remains unverified
until a credentialed call succeeds. MCP and OKF are not implemented or assumed here.

To explicitly verify a live account, run `python tools/meta_smoke.py` from the repository
root. It checks Spark JSON, Image generation and editing, and Spark vision, with a cap
of two image calls. It writes test images under `workspace/runs/` and keeps its call
journal in `workspace/meta-smoke.sqlite3`. This is a paid connectivity check, separate
from the offline pytest suite and mock demo.

Live verification on 2026-10-28 succeeded for all four operations with the configured
models. The generated image was 1600×1600 for a 1024×1024 request. Spark identified
the edited slime as a jellyfish; connectivity passed, while semantic consistency still
needs the planned critic and repair loop. These test images are not accepted game assets.

```powershell
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

## Pixel pipeline (Milestone 2)

Run `python scripts/demo_pixel.py` to generate `workspace/pixel-demo/before.png`,
`after.png`, `after-8x.png`, `comparison.png`, a contact sheet, an extracted palette,
and `atlas.png` / `atlas.json`. The deliberately blurred input has a near-magenta
gradient background and stray pixels. The script asserts that the processed output
passes its pixel checks. It never loads credentials or calls a provider.

```python
from PIL import Image
from spriteforge.pixel import AssetSpec, Style, postprocess, validate
from spriteforge.pixel.palette import load_palette

spec = AssetSpec(id="hero", size=(32, 32))
style = Style(palette=load_palette("dungeon").colors, outline=True)
sprite = postprocess(Image.open("raw.png"), spec, style)
failures = validate(sprite, spec, style)
```

Processing keys background pixels by RGB Euclidean distance (0–442), trims the alpha
bounds, fits at an integer working scale, and downsamples with BOX. All RGB mapping
uses nearest CIELAB color without dithering. Alpha is binary, transparent RGB is
canonical zero, cluster cleanup uses 4-connectivity, and the optional outline is one
pixel in the four cardinal directions. An outline reserves extra space inside the
sprite canvas; tiles default to zero padding and allow edge coverage.

`validate` returns structured `Failure(code, message, fix_hint)` objects for dimensions,
palette, alpha, color count, clipping, fill, and tile seams. Palette and color-count
checks ignore fully transparent RGB. Tile scores are maximum premultiplied RGBA
differences normalized to 0–1, catching even a single bad edge pixel. The half-offset
test rolls the image and checks the moved seams; it checks the same periodic boundary
in the offset view, rather than judging interior artwork. Seam repair is not automatic.
`validate_animation` also checks consistent frame sizes and one shared allowed palette;
individual poses may use different subsets of that palette.

`extract_palette` uses deterministic weighted k-means in LAB, ignores transparent
pixels, and samples at most 16,384 visible pixels at fixed positions. Near-duplicate
merging uses Euclidean LAB distance (Delta E 76). `load_palette("dungeon")` loads the
bundled 16-color palette; `load_palette(path)` and `save_palette(palette, path)` use JSON.

`pixel.atlas.pack` uses deterministic shelf packing with transparent padding and no
rotation or trimming. Supply named sprites such as `hero/idle/0`, `hero/idle/1` and an
`animations={"hero/idle": ["hero/idle/0", "hero/idle/1"]}` mapping. Frame keys remain
unchanged. The JSON follows [Phaser 3's JSON-hash atlas format](https://github.com/phaserjs/phaser/blob/v3.90.0/src/textures/parsers/JSONHash.js);
the extra `meta.animations` map describes sequences for later game assembly, and does
not create Phaser animations automatically. Oversized atlases fail explicitly.

### Cleaning real model images (still Milestone 2)

```powershell
python scripts/process_image.py INPUT.png --size 32 --palette auto
python scripts/process_image.py INPUT.png --size 48 --palette auto --output workspace/pixel-demo/real/out/48
```

The default output directory is `workspace/pixel-demo/real/out/`; use separate
`--output` paths to retain multiple sizes. Outputs are `before.png`, `after.png`,
`after_8x.png`, `comparison.png`, `style.json`, and `processing.json`. The comparison
uses the uncorrected raw image naively BOX-downscaled beside the cleaned sprite.
`--palette` also accepts a palette JSON path. Auto extraction requests 16 LAB k-means
colors from the despilled, isolated subject, excluding background and watermark colors.
Near-duplicates are merged with a default LAB distance of 5, configurable through
`--merge-distance`. The same merge applies to palettes loaded from a file; source
palette files are not modified. The final palette may contain fewer than 16 colors.

The real-output path samples a narrow image-border band, estimates its median RGB,
and keys by LAB distance (default Delta E 76 tolerance 18). A three-source-pixel edge
band removes background-like contamination up to LAB distance 45. Isolation keeps
the largest 4-connected component. `--keep-detached` also retains components with
area at least `--min-area-ratio` times the largest (default 0.02). Every removed
component's area, bounds, and ratio are printed and saved.

Grid detection measures repeated strong horizontal/vertical color-run lengths. It
requires evidence on both axes, reports the inferred square block size and phase,
and falls back to unsnapped processing when evidence is insufficient. Confidence is
the smaller fraction of sampled repeated run lengths near the inferred block size
on the two axes; native snapping requires at least 0.35 (configurable in the Python
options). Each detected cell is reduced to its mode. By default, a confident logical
subject is placed at exactly 1:1 with padding; `--scale-mode fit` restores stretching
to fit the canvas. Without a confident grid, fitting is used. `--baseline center`
(default) centers the subject; `--baseline bottom` aligns it to the bottom padding.

Native placement never enlarges a subject. Overflow first tries the smallest integer
reduction factor when it divides both logical dimensions exactly; otherwise it falls
back to the existing mode fit. Both reductions warn with the logical dimensions and
a suggested larger canvas. `processing.json` records `logical_subject_size`,
`grid_confidence`, `scale_mode`, and `scale_factor` (1 means native, 0.5 means halved).
It also records the final `palette_size`, per-color `palette_usage` pixel counts,
and warnings for unused colors or dark colors whose LAB hue resembles the detected
background. Tint warnings do not automatically alter legitimate dark artwork.

Palette quantization, binary alpha, stray cleanup, and the existing corner-connected
dark outline follow. Automatic outline skipping has not been enabled because a
doubled outline has not yet been confirmed by user review. Flat, mostly unobstructed
borders are assumed; this is not general-purpose background segmentation.

The Python API enables these operations with
`postprocess(raw, spec, style, options=ProcessingOptions(), report=ProcessingReport())`.
`ProcessingOptions` and `ProcessingReport` are exported by `spriteforge.pixel`.
Omitting `options` preserves the original explicit RGB key, BOX downscale, and
cardinal-only outline behavior, including existing tile processing. No existing
tests were weakened or changed to enable the new path. Neither path calls models.
