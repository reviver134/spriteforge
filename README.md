# SpriteForge

Local-first pixel-game builder. Milestone 1 supplies the foundation and a mock demo;
Milestone 2 adds deterministic pixel processing, validation, palettes, atlases, and
previews. Milestone 3 adds local OKF knowledge, retrieval, and a read-only MCP server.
Milestone 4 adds editable roles, the bounded agent runtime, Director planning, and
durable human gates. Milestone 5 adds adaptive intake and concept previews with
mock or live providers. Playable game generation and production asset workflows
remain outside this milestone.

Milestone 6 adds role-scoped skills and official-SDK MCP clients. Open **Skills and
MCP servers** in the local UI to inspect role skill summaries, discover server tools,
enable a server for a selected role, and configure each tool's confirmation policy.
New servers are disabled. Inspecting a stdio server starts its executable to list
tools; adding or listing configuration does not start it. Tool calls default to
human confirmation, including tools advertising read-only annotations. Approval
binds the exact role, arguments, connection and discovered tool definition, and is
consumed once. Revoking a role or changing a discovered definition invalidates access.
Declines never invoke the server. Interrupted calls marked `running` or `failed`
are not automatically replayed, since their side effects may already have occurred.

```powershell
spriteforge skills list --workspace workspace --role "Pixel Artist"
spriteforge skills add C:/path/to/my-skill --workspace workspace --role "Pixel Artist"
spriteforge skills add https://github.com/owner/repo.git --subdir skills/my-skill --revision COMMIT --workspace workspace
spriteforge skills remove my-skill --workspace workspace
spriteforge mcp add filesystem-read src/spriteforge/mcp/templates/filesystem-read.json --workspace workspace
spriteforge mcp inspect filesystem-read --workspace workspace
spriteforge mcp enable filesystem-read --role "Pixel Artist" --workspace workspace
spriteforge mcp disable filesystem-read --role "Pixel Artist" --workspace workspace
```

Skills load from `src/spriteforge/skills/builtin/` and `<workspace>/skills/`.
`SKILL.md` needs YAML `name` and `description` plus a nonempty body. Names are unique;
workspace skills cannot shadow builtins. Role Markdown `allowed_skills` and explicit
workspace grants in `skill-grants.json` form each role's effective skill allowlist.
The add command defaults to a Pixel Artist grant; use `--role` for another role.
Models initially see only name/description, then call `load_skill` for the body or
a selected relative reference. Bundled scripts are readable but never automatically
executed. Builtins cover Muse sprite prompting, tile seams, animation consistency,
and Phaser **3** Arcade Physics. Git installs copy only the selected skill folder;
check its license and retain required attribution when importing third-party skills.

MCP configuration is `<workspace>/mcp.json`, with an `mcpServers` map. Stdio servers
use `transport`, `command`, and `args`; HTTP uses `transport: "http"` and `url`
(Streamable HTTP). `env` maps child variable names to host environment variable
names; `headers_env` maps HTTP header names to host variable names containing the
full header value. Never place credentials in arguments, URLs, JSON or skill files.
Servers get only the SDK's basic process environment plus explicit environment
bindings. Server stderr is suppressed so it cannot leak credentials into the UI.
Tool schemas are shown in the UI and exposed to enabled roles as `server.tool`.
Both CLI and UI role grants are persisted in `enabled_roles`; manually adding an
`allowed_tools` entry alone does not bypass MCP grants. Per-tool `confirmation`
overrides can be changed explicitly by the human in the UI. Calls, denials,
confirmation decisions and results are journaled with secret redaction. MCP calls
also consume the run's per-server call cap (the configured per-model cap); external
MCP service billing is not estimated.

The client uses the installed MCP 1.x API, verified with real SDK stdio and
Streamable HTTP round trips and the [official 1.x documentation](https://py.sdk.modelcontextprotocol.io/v1/).
Each operation opens a bounded session; use stateless tools or tools with explicit
file IDs/paths, as the included servers do. Stateful browser-session servers are
not included. The runtime persists a pending call and its conversation checkpoint,
and consumes the saved result on resume without repeating the external call.
CLI/UI acceptance tests cover role denial, progressive skill loading, revocation,
confirmation persistence across reload, single-use approval, and both transports.

The requested research additions are installed in the repository's `workspace`:

| Addition | Role / state | Source |
|---|---|---|
| `create-game-assets` | Pixel Artist and Art Director | [Game-development skills](https://github.com/gamedev-skills/awesome-gamedev-agent-skills/tree/19d93f97c9c1ea0f8b306e77aa81e0d3e352d3e9/skills/disciplines/create-game-assets) |
| `level-design` | Level/Tile Designer | Same collection, Apache-2.0 |
| `game-feel` | Game Designer | Same collection, Apache-2.0 |
| `aseprite` MCP 0.1.0 | Installed under `workspace/extensions/python`, disabled | [Aseprite MCP](https://github.com/jcscocca/aseprite-mcp/tree/a04d18a6f97ecd7c05c7b2f6391034eba66bd10e), MIT |
| `filesystem-read` MCP | Disabled; confines reads to workspace runs | Bundled example |
| `knowledge` MCP | Disabled; local corpus retrieval | Existing SpriteForge knowledge server |

The selected external skills include their original references and license. The
collection's Phaser skill targets Phaser 4, so it was not installed. Source commits,
licenses and install status are recorded in `workspace/extensions/sources.json`.
`tools/install_game_extensions.py` reproduces registration from those downloaded
source directories and the locally installed Aseprite MCP package; it never enables
a server. No external skill scripts were executed. Workspace files remain local
and ignored by Git.

Aseprite itself is not included or installed. Set `ASEPRITE_BIN` in the environment
to your separately installed executable before inspecting/enabling `aseprite`.
Its tools can write files, so keep per-call confirmation enabled and use explicit
run-folder paths. Its upstream spritesheet metadata targets Godot: use exported
PNG frames with SpriteForge's existing Phaser atlas pipeline. Live Aseprite edits
have not been verified on this machine.

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
ratio hints, not guaranteed pixel dimensions. The Milestone 5 credentialed smoke
test verified generation and editing; details appear below. The separate Milestone 3
memory workflow implements MCP and OKF locally.

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
dark outline follow. The outline guard skips addition when more than 90% of boundary
pixels match a dark palette color (L* <= 35) within LAB distance 12. Characters default
to a 48x48 canvas, with generated art intended to fit within about 40x40 logical pixels;
tiles default to 16x16. The processing script defaults to a 48px output. Flat, mostly unobstructed
borders are assumed; this is not general-purpose background segmentation.

The Python API enables these operations with
`postprocess(raw, spec, style, options=ProcessingOptions(), report=ProcessingReport())`.
`ProcessingOptions` and `ProcessingReport` are exported by `spriteforge.pixel`.
Omitting `options` preserves the original explicit RGB key, BOX downscale, and
cardinal-only outline behavior, including existing tile processing, with the shared
guard against adding a doubled outline. Neither path calls models.

## Knowledge layer (Milestone 3)

```powershell
spriteforge memory search "metroidvania map"
spriteforge memory validate
git -C workspace/memory log --oneline
```

Memory commands default to the repository's `workspace/` directory, as requested for
this milestone; `--workspace PATH` selects another workspace (including the general
harness default `~/spriteforge-workspace`). The corpus is `workspace/memory/`, with
its own local Git repository. SQLite audit Events stay in `workspace/state.sqlite3`,
outside the corpus. Every application write batch updates indexes and `log.md`,
validates, and makes a descriptive Git commit. Writes are serialized by an OS file
lock, with file restoration on a failed write/commit. No remote is configured and
no knowledge is sent to a model or external service.

The first memory command seeds ten genre templates, four art styles, four Phaser
patterns, and one Muse sprite prompt. Every genre includes camera, controls,
mechanics, entities, sizes, palette guidance, counted assets, animation needs,
pitfalls, and six or seven adaptive questions. Existing entries are never replaced
by seeding. Per the explicit bootstrap instruction, seeds carry verification by
`human:project-owner`, with provenance identifying that trust designation; this is
not a claim of an independent human review of agent-authored material. The prompt
pattern's scores are explicitly unmeasured until a production run supplies scores.

The implementation follows [Google's OKF v0.2 spec](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md)
and the [OKF Agent Memory Convention](https://github.com/okf-memory/okf-agent-memory/blob/main/docs/spec/CONVENTION.md).
Only `type` is universally required; IDs are file paths without `.md`. Recommended
metadata (`title`, `description`, `resource`, `tags`) and optional `sources`,
`generated`, `verified`, `status`, and `stale_after` use the standard names. Unknown
fields and types survive round trips. A bare verifier mapping is accepted as a
one-element list. Trust labels are derived as `unverified`, `machine-confirmed`, or
`human-reviewed`; they are not invented frontmatter fields. `stale_after` is an
absolute timezone-aware timestamp, and stale/deprecated entries remain retrievable
with visible labels. Attested-computation metadata is preserved, but executors and
attesters are never run by the memory store.

Custom extensions include `x-spriteforge-scope` (`project` or `private`),
`x-spriteforge-scores` (prompt measurements), `x-spriteforge-review` (bootstrap
review status), `x-spriteforge-reviewed-at` (review time), and
`x-spriteforge-designation` (bootstrap verification designation). Ordinary agent
writes set `generated` and clear prior verification. An explicit human command
`spriteforge memory promote lessons/example --by project-owner` records verification
without rewriting generation provenance. Agents cannot promote through MCP.

Browse progressively with `spriteforge memory index`, then `memory index genres`,
`memory search "query"`, and `memory show genres/metroidvania`. Directory indexes
contain link summaries, not full entries. Root index alone declares `okf_version`.
Search uses in-memory BM25 over title/tags/body, refreshing from disk so a dropped-in
genre becomes searchable without restarting. For example, save
`workspace/memory/genres/custom.md`:

```markdown
---
type: Genre Template
title: Clockwork skating
tags: [skating, clockwork]
---
Explore clockwork skating arenas with reversible route puzzles.
```

This file starts unverified. `memory index genres` synthesizes current summaries,
including newly dropped files; persisted indexes refresh at the next application
write batch. File additions made directly by the user can be committed with Git.

Python API: `KnowledgeStore(root, events=game_event_store, run_id=game_run_id)` and
`retrieve(query, scopes=["project"], token_budget=1024)`. Results include ranked
snippets, IDs, trust, lifecycle, scope, and cost. Budget accounting conservatively
charges one unit per UTF-8 byte of the rendered trust/ID/title/snippet text, rather
than claiming an exact count for a particular model tokenizer. All retrievals,
including index/full-entry reads and no-hit searches, append `memory.retrieved`
Events. Events store query hashes and result metadata, not snippet bodies.

`user-prefs/` is always private, even without metadata. Other entries can opt into
private scope using the namespaced field. Private retrieval requires `--scope private`
or explicit API scopes. `memory export DESTINATION` reconstructs public concepts and
indexes only; it never copies preferences, audit records, logs, or Git history.
Known environment credential values, bearer credentials, private-key headers, and
credential-style assignments are rejected before serialization; no secret values
are included in rejection messages. This is not a general detector for arbitrary
unlabeled secrets, so never submit secrets as knowledge. These are heuristics, not guarantees.
Redaction happens at the source before the Librarian sees run data: producers must
scrub events, tool results, and artifacts before passing them to the Librarian.
See [the memory README](src/spriteforge/memory/README.md).

`memory validate` checks Markdown frontmatter, optional metadata, reserved index/log
structures, and date formats, and runs `okf validate CORPUS` if that executable is
installed. Missing optional indexes, unknown fields/types, and broken links do not
invalidate an otherwise conformant concept. The separate `okf` CLI is optional and
was not installed for this milestone; its invocation is covered by a test double.

Start the MCP server with `spriteforge memory mcp --workspace PATH`. It exposes
`memory_search`, `memory_index`, and `memory_show` over stdio, using the official
Python MCP SDK 1.x. Private scope is disabled unless the operator starts the server
with `--allow-private`; clients must still request private scope explicitly. The SDK
dependency is bounded below 2 for the tested API, and SSE is bounded below 3 to keep
the existing FastAPI/Starlette environment compatible. Protocol stdout contains no
application logs. Example client configuration (replace paths with absolute paths):

```json
{
  "mcpServers": {
    "spriteforge-memory": {
      "command": "python",
      "args": ["-m", "spriteforge", "memory", "mcp", "--workspace", "C:/path/to/game_engine/workspace"]
    }
  }
}
```

Seam repair remains deferred to Milestone 7. This milestone adds no image-edit calls
or game-generation workflow.

Bootstrap review extensions: `x-spriteforge-review: unreviewed` distinguishes an
owner-authorized seed tier from a content review. Its verification record carries
`x-spriteforge-designation: bootstrap`. `spriteforge memory review` lists pending
entries; `spriteforge memory review <id> --confirm` records `reviewed` and the
`x-spriteforge-reviewed-at` timestamp. These are SpriteForge extensions to OKF.

Retrieval filters BM25 scores using `memory.min_score` and `memory.relative_score`
in config.yaml (defaults: 0 and 0.5 of the best score). CLI overrides are
`--min-score` and `--relative-score`; `--verbose` prints each retained score.


## Agent runtime and gates (Milestone 4)

```powershell
spriteforge start --mock --agents --goal "A top-down dungeon crawler"
# After stopping the process, including while paused:
spriteforge runs
spriteforge resume <id>
```

The acceptance workflow pauses at G1 Brief, G2 Concept preview, G3 Asset contact
sheet, G4 Playtest, and G5 Final review. Gates and answers persist in SQLite with
no expiration. Approve, Request changes (with tags and text), and Abort are explicit
human actions. G1 demonstrates a schema-driven questionnaire. Images appear side
by side with a generated contact sheet; JSON artifacts remain inspectable.

At G3, enter `make the hero rounder`. The Director produces a typed change request
with `target: [hero]` and `needs_regen: true`, then opens a second confirmation gate.
No image calls run until that gate is approved. Confirmation re-plans the affected
role, creates hero v2, retains tile v1 and all original bytes, and reopens G3. Abort
ends the run. Unrecognized mock feedback conservatively targets a mechanic without
regenerating images. This interpreter is an offline scripted provider example;
live natural-language interpretation is supplied through the provider interface.

Eight Markdown roles live in `src/spriteforge/agents/roles/`. Their frontmatter
specifies name, goal, model, allowed tools and skills, memory scopes, and step cap.
Each new run reloads and snapshots these files; editing them does not mutate an
already-paused run. The CLI acceptance workflow requires `model: mock` and uses
scripted responses. `AgentRuntime` accepts an injected LLM provider or a dictionary
keyed by role model, plus explicitly registered typed tools and skill text; it does
not implicitly grant filesystem, MCP, or skill access. Existing live provider
adapters can be injected by callers. No new live API calls are needed for this demo.

Each invocation retrieves within its allowed scopes and byte-accounted memory
budget, records retrieved IDs, enforces step/time limits, validates tool arguments
and results, and allows one corrective JSON-schema retry. Outputs, plans, feedback,
and change requests are typed artifacts; the append-only event journal records
progress. Stable invocation IDs allow replay of completed outputs after a crash.
Budget reservations count attempted calls. Synchronous provider I/O may finish
remotely after a timeout; its late result is discarded. Tools must check the runtime
cancellation boundary before publishing side effects (the image registry does).
Accepted image versions are immutable; revisions require explicit confirmed targets.
The mock workflow persists state between stages and gates, and uses an OS lock to
serialize concurrent resumes. An interrupted image request may still consume a
reservation; an image already saved to the registry is reused on retry.

The Programmer and QA artifacts in this workflow describe mock work; this milestone
does not produce a playable game, run a live art critic, or automatically promote
Librarian knowledge. These remain later work.

Validation uses the reusable `ScriptedHuman` pytest fixture and a real CLI/Chromium
restart test. Install the headless browser once with
`python -m playwright install chromium`, then run `python -m pytest` and
`python -m ruff check .`.
# Milestone 5: intake and concept preview

Run `python -m spriteforge start --intake --mock --workspace workspace --no-open-browser`
and open http://127.0.0.1:8765. Choose a genre, complete or skip the brief fields,
answer the adaptive follow-ups, and choose a palette/style. Choice chips, free text,
suggested options and default buttons are available in the questionnaire.

G2 shows the labeled concept and a full-scene pixel preview at 320×180, enlarged
3× with nearest-neighbor sampling. The pixel pipeline's LAB palette extraction
and quantization preserve the entire scene rather than removing backgrounds or
discarding enemies as detached sprite components. Request changes, confirm the
ChangeRequests, and review v2. All versions remain visible in Artifacts. Edit the
extracted color swatches before approving. Approval locks `style.json` and
`anchor.png` in `workspace/runs/<run-id>/`, with `brief.json`, concept versions,
native previews, and approval provenance alongside them. Resume with
`python -m spriteforge resume <run-id> --workspace workspace --no-open-browser`.

Omit `--mock` to use the configured Muse providers and `META_API_KEY`. Live concept
generation is opt-in; tests use offline providers. There is no revision-count
limit, but every image call consumes the persisted budget (150 by default), and
the UI shows calls and the configured estimated cost. The estimate is not billed
usage. This milestone stops after G2; it does not generate the final game.

The 2026-10-28 smoke test used two successful Muse Image calls through the existing
provider, one generation and one edit. Both accepted `model=muse-image-1.0`,
`n=1`, `size=1024x1024`, `response_format=b64_json`, `output_format=png`, and
`reasoning_strength=low`. Paths were `/v1/images/generations` and `/v1/images/edits`
on `https://api.meta.ai`; editing accepted `images=[{"image_url": "data:image/png;base64,…"}]`.
Both responses contained `data[0].b64_json` but decoded to **1600×1600**, so requested
size is not an output-size guarantee. One reference was verified; neither the
maximum reference count nor `reasoning_strength=high` was tested. No provider
request changes were needed; the discrepancy is documented in `config.yaml`.

Redacted wire metadata is in `workspace/milestone5-smoke/responses.json` (the
existing scrubber also masks usage token counts). Sprite grid detection was
inconclusive and used fit mode; the edited pose detected a 20×16 logical subject
with a 50-pixel block size. Both passed processing validation, with unused-color
and background-tint warnings. Compare `workspace/milestone5-smoke/sprite/comparison.png`
and `workspace/milestone5-smoke/pose/comparison.png`; each directory also contains
the processing report, palette, source, result, and enlarged result.
