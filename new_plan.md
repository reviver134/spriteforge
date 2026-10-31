# Milestone 10: better art requests, faster images, art review, QA tests

Builds on M9 (prompt → design → art → code → playtest → gates). Four milestones, in order.
Per AGENTS.md, each one ends with pytest, a commit, a short report of what works and
what doesn't, and a stop for the go-ahead.

## Decisions taken

| Topic | Decision |
| --- | --- |
| Art review fixes | Blocking issues are auto-fixed **once**, capped, before G4. Everything else becomes a one-click suggestion at G4. |
| Image model for review fixes | Every image call made by the art review uses Muse Image at its **highest setting**, `reasoning_strength: "high"`. The API offers `low` and `high` only. This lives in config (`game.review_image_reasoning`) so a stronger tier can be set if Meta adds one. |
| Review model | Spark vision, with the strongest reasoning setting Spark accepts. Whether Spark has such a setting is checked by probe in M10.2. |
| QA tests | The QA agent writes tests as a **JSON step list** (no model-written code is executed). The harness runs them with Playwright and also exports them as real Playwright `.py` files. |
| Camera types | They stay `top-down` (drawn 3/4) and `side-view` until the library has tiles for other views. |
| Batching | Meta has no batch or multi-prompt image API (docs checked 2026-10-31: `n` gives 1-10 variations of one prompt, 150 requests/min). Instead: parallel calls, a request cache, and an atlas probe only. |

## Known facts these plans rely on

* Muse Image `size` sets only the aspect ratio ("returned dimensions won't match the numbers exactly"), up to 16:1. The docs give no pixel-art guidance. Only 1024×1024 has been verified live, and it returns 1600×1600.
* Spark image input was verified live on 2026-10-28 (`tools/meta_smoke.py`).
* The older asset pipeline already has parts to reuse:
  * `stages/asset_review.py`: `review_set` and `SetReview`, contact sheets, the object-naming test;
  * deterministic checks: palette, alpha, padding, fill, seams;
  * a raw image cache keyed by prompt, reference hashes and parameters;
  * native-grid detection in `pixel/postprocess.py`.
* In the last live build, image calls ran one after another at about 15 s each, and `cached_input_tokens` was 0 on every Spark call.

---

## M10.1: Art spec for every Muse Image request (perspective + dimensions)

**Goal.** Every image request states the view, the exact pixel grid, the size compared with the hero, the anchor and the light direction. It is sent with a matching aspect ratio, and the result is checked by code.

### Changes
* **`game/art.py`**
  * New `ArtSpec` (pydantic), built by code from the design entity, the plan pick, the camera and the hero size. Fields:
    * `view` (explicit wording per camera: "camera tilted about 45°, we see the top and the front" or "flat side view");
    * `grid` (w×h);
    * `scale_note` ("the hero is 16 px tall; this is 8 px, half the hero");
    * `anchor` (feet at bottom centre for actors, centre for pickups and projectiles, full bleed for tiles);
    * `facing`;
    * `light` (top-left);
    * `outline`;
    * `palette` (up to 16 hex colours from the style reference, or from design.look when there is none);
    * `request_size`.
  * `muse_prompt(spec, pick)` is rewritten to render the spec. Grid wording: "drawn on a W×H grid of big square pixels, one solid colour per cell, no anti-aliasing".
  * `request_size(spec)` maps the subject's aspect ratio to the nearest verified size, for example 1:1 → 1024×1024, wide → 1792×1024, tall → 1024×1536. The list of verified sizes lives in config.yaml (`image.sizes`) and is filled from the probe.
  * `MUSE_SIZE` is removed.
  * `muse_frame` tries native-grid downscaling first, using the existing detection in `pixel/postprocess.py`. When grid confidence is below `native_grid_confidence`, it falls back to today's `fit_sprite`/`fit_tile`.
  * New `check_frame(spec, raw, frame)` → a list of problems:
    * subject aspect ratio off by more than 35%;
    * fill under 20% or over 98% (sprites only);
    * no subject;
    * a tile that isn't seamless (the existing seam check).

    On problems: **one** retry with the problems written into the prompt and `reasoning_strength: "high"`. If the retry fails too, the frame is accepted with a warning. Both calls count against `max_art_calls`.
* **`config.yaml` / `config.py`**: `image.sizes` (verified list) and `game.art_retry: 1`.
* **`tools/probe_art_spec.py`** (live, 3 image calls, about $0.03): one square sprite with grid wording, one 1792×1024 background, one 1024×1536 tall sprite. It saves the raw outputs, their real sizes and grid confidence, and the result goes into config.yaml comments.
* **Docs**: an M10.1 section in `docs/milestone-10-art-and-qa.md`.

### Tests: `tests/test_art_spec.py`
* `test_spec_prompt_states_view_grid_scale_anchor_light`: a top-down 14×12 enemy prompt contains the 45° wording, "14×12 grid", "hero is 16 px", "feet at the bottom", "light from the top-left", and the palette hexes. A side-view prompt contains "side view" and does not contain "top".
* `test_request_size_follows_aspect`: background → wide size, a 10×24 sprite → tall size, 16×16 → square. Only sizes from `image.sizes` are ever used.
* `test_native_grid_downscale_is_exact`: a synthetic 14×12 sprite blown up ×100 with ±2 px block jitter and JPEG-like noise comes back pixel-identical to the source.
* `test_falls_back_to_fit_without_grid`: a smooth gradient image still yields a frame of the target size, and the report says "fit".
* `test_bad_frame_retried_once_with_high_reasoning`: a mock image provider returns an empty image first and a good one second. The result is 2 calls, and the second request has `reasoning_strength == "high"` and the problem text in its prompt.
* `test_retry_respects_cap`: with `max_art_calls` reached, there is no retry and a warning is listed.
* Updated in `tests/test_game_make.py`: the mock end-to-end build still passes, and `art.json` records each request's size and spec.

### Acceptance
* The probe has run and config.yaml lists the verified sizes.
* One live `game make` on the survival prompt shows grid-native downscaling on at least half of the Muse sprites, with no aspect warnings on correct art.
* Full pytest passes, apart from the 3 known asset-production failures.

---

## M10.2: Parallel image calls, request cache, token savings

**Goal.** The same images in about a quarter of the time, free repeats, and smaller or cacheable Spark requests.

### Changes
* **`game/art.py` `make_art`**
  * Library picks and locked art resolve first, as today.
  * The remaining Muse jobs run in a `ThreadPoolExecutor(max_workers=game.image_workers)`, default 4.
  * Cap order is kept: jobs get call slots in `PRIORITY` order *before* dispatch, so the cap can never be exceeded and the same entities get greybox under a cap as today.
  * Results are assembled in entity order, so outputs are deterministic.
  * One failure never cancels the others.
* **Thread safety**
  * Each worker uses its own `MuseImageProvider` (httpx client).
  * `Budget.attempt` reservations already use `BEGIN IMMEDIATE`; a test verifies them under concurrency.
* **Request cache**: `workspace/cache/muse-image/<sha256>.png`. The key covers model, endpoint, prompt, size, `reasoning_strength`, `n` and the reference image hashes (the same scheme as the old production cache). A hit makes no image call, costs nothing, and the source becomes `muse (cached)`. The cache never crosses the AI-allowed rule, because the references are part of the key.
* **Atlas probe** (`tools/probe_atlas.py`, live, 1 call): 6 small sprites on one magenta grid, sliced by connected components. The slice quality goes into the docs. This is measurement only; it is not wired into the build.
* **Spark token savings** (from the earlier review):
  * *Repairs as edits*: the `GameCode` repair and change answers become `CodePatch {edits: [{path, old, new}], notes}`, applied by code. If an `old` string is not found exactly once, that round falls back to full files. A file can still be replaced whole with `old: ""` for a new file.
  * *Stable prefix for caching*: programmer, repair and change prompts are reordered as system → API reference → design → assets → (code, failures, instruction).
  * *Filtered library list for the Art Director*:
    * `library_context(camera, design)` lists only actors, objects and tiles whose names or tags share words with the design's entities;
    * it always includes up to 3 hero options and the tile sets for the camera;
    * the rest is available through the existing `library_search` MCP tool.
  * *Probe*: `tools/probe_spark_reasoning.py` (2 small Spark calls) checks whether Spark accepts a reasoning-effort field and whether a repeated long prefix reports `cached_tokens`. The findings go into config.yaml. If a reasoning setting exists, `llm.reasoning` becomes per role, with `low` for G1, the art plan and the feedback split, and `high` for design, code and art review.

### Tests: `tests/test_art_parallel.py`, `tests/test_codegen_patch.py`
* `test_parallel_calls_bounded_and_ordered`: a mock image provider that sleeps and records concurrency. Peak concurrency is ≤ 4, wall time is under 2× one call for 6 jobs, and the `filled` order equals the sequential order.
* `test_cap_under_concurrency`: with cap 3 and 6 Muse jobs, exactly 3 calls are made, the greybox placeholders are the 3 lowest-priority entities, and the budget ledger shows 3 image attempts.
* `test_one_failure_isolated`: one job raises, it becomes a placeholder with a warning, and the other 5 succeed.
* `test_cache_hit_skips_call` / `test_cache_key_includes_references_and_reasoning`: the same request twice gives 1 call. Changing a reference byte, or the reasoning strength, gives a new call.
* `test_patch_applies_and_falls_back`: a patch edits `game.js`. An ambiguous `old` triggers the full-file fallback request.
* `test_prompts_share_stable_prefix`: the programmer, repair and change prompts for the same design share an identical prefix of at least 90% of the API reference plus the design text.
* `test_library_context_filtered`: for the survival design, the context lists the skeleton and knight but no tavern citizens or fishing animations, and it is under 600 tokens (chars/4).
* Updated `test_game_stage`: rebuilds still lock art, and a cached image counts as unchanged.

### Acceptance
* Live survival build: the image phase takes under 40 s for about 7 images (it was about 1:45).
* A repair round's output tokens are at least 50% below a full-file repair, measured in the ledger.
* The probe findings are recorded.

---

## M10.3: Art review agent (whole art combined)

**Goal.** After art is made and the game runs, an Art Reviewer looks at all the art together and in context, then fixes blocking problems once and suggests the rest at G4.

### Changes
* **Role**: `agents/roles/art-reviewer.md`. It judges consistency, readability, perspective, scale and palette as one set. The role is grantable like the others; it gets read-only library tools.
* **Inputs**, built by code:
  * the art sheet at native size and at 4× (`art_sheet`);
  * 3 in-game screenshots captured during the playtest (early, middle, late) through `sfCapture`, so sprites appear on the real floor at game scale with HUD and crowds. `playtest.py` gains `capture_at=[...]`.
* **Deterministic pre-checks** (free; `game/art_checks.py`, reusing the old pipeline's checks where they fit):
  * sprite-vs-floor contrast, as luminance difference against the tile it stands on;
  * hue clash between an enemy and a pickup;
  * sizes against the design's `size` and against the hero;
  * placeholders still present;
  * an unreadable silhouette (fill ratio, or a bounding box under 50% of the frame).
* **Vision review** (`game/art_review.py`): one Spark call with the images, the design entities and the pre-check findings. The answer is `ArtReview`:
  * `scores {consistency, readability, perspective, scale, palette}`, each 1-5;
  * `named [{entity, seen_as}]`, the naming test: what each sprite looks like, without being told;
  * `issues [{entity, problem, severity: blocking|major|minor, fix_prompt}]`.

  Code adds an issue when the naming test misses, for example a "gem" seen as "a blue blob".
* **Fix policy**:
  * At most `game.review_max_fixes` (default 4) **blocking** issues are fixed once, before G4.
  * Each fix is a Muse Image *edit* of the current image with `reasoning_strength: "high"` (`game.review_image_reasoning`) and the spec from M10.1.
  * Library art with `ai_allowed: false` is never sent. A blocking issue on such art becomes a fresh Muse draw of that entity, and the report says so.
  * After fixes, the build reuses the same code with the new assets and replays the playtest; there is no second review.
  * Major and minor issues are listed only.
  * All other art stays locked (principle 4).
* **Gate G4**:
  * the report gains `art_review` (scores, issues, what was fixed, before/after thumbnails);
  * each suggestion has an **Apply** button that submits feedback through the existing `Revision.art_changes` path, so it becomes a normal targeted rebuild.
  * `app/index.html` renders this.
* **Config**: `game.review: true`, `game.review_max_fixes: 4`, `game.review_image_reasoning: high`.
* **Mock**: an offline `ArtReview` fallback built from the pre-checks alone, so the pipeline and tests run without Spark.

### Tests: `tests/test_art_review.py` + additions to `tests/test_game_stage.py`
* `test_prechecks_flag_contrast_hue_size_placeholder`: synthetic sprites trigger each finding: a dark sprite on a dark floor, an enemy and pickup both cyan, a gem drawn at 3× the hero size, and a greybox.
* `test_review_request_carries_sheet_and_screenshots`: a mock chat provider captures the request, which holds 1 sheet, 1 zoomed sheet and 3 screenshots as images, plus the design entities.
* `test_blocking_issue_fixed_once_with_high_reasoning`: for a review with 2 blocking issues and 1 minor one, there are exactly 2 image edit calls with `reasoning_strength == "high"`, the minor issue is unchanged and listed, and the other entities' bytes are identical.
* `test_fix_cap_and_no_second_review`: 6 blocking issues with cap 4 give 4 fixes and 2 listed as unfixed, and the review call count is 1.
* `test_non_ai_allowed_library_art_is_redrawn_not_sent`: a blocking issue on a GandalfHardcore/LuizMelo actor never sends its pixels to the image provider (asserted on the captured requests) and produces a fresh draw.
* `test_naming_miss_becomes_issue`: `seen_as` unrelated to the entity id adds a major issue.
* Stage: `test_g4_apply_suggestion_makes_targeted_rebuild`: applying suggestion 1 at the playtest gate gives v2, where only that entity's source is `muse` and the others are `locked`.

### Acceptance
* A live survival build shows review scores, at least one concrete suggestion, and fixed art (if any) on the G4 report.
* The review plus fixes cost at most $0.08 per build.
* Full pytest passes.

---

## M10.4: QA agent that writes Playwright tests

**Goal.** Tests written from the design only (not from the code) that press real keys, cover menus, restart and the lose path, and run with Playwright. They are exported as Playwright files the user can run.

### Changes
* **Probe first** (`tools/probe_keys.py`, no model calls): check that Playwright `keyboard.down/up` reaches `sf.input` while the harness steps the game frame by frame with `sfRun`. If it doesn't, add `sf.inject(keys)` to the SDK, which the runner sets per step.
* **`game/qa.py`**
  * Test schema `QATest {name, purpose, god: bool, steps: [Step]}`. `Step` is one of:
    * `hold {keys, seconds}` and `press {key}`. Keys are whitelisted: arrows, WASD, Space, Z, X, J, K, Shift, Enter, Escape, 1-9.
    * `wait {seconds}`.
    * `choose {index}`, for an open `sf.choose` menu.
    * `expect_event {name, min_count, within_seconds}`.
    * `expect_state {path, op: ==|!=|<|<=|>|>=, value}`, read from `sf.state` via a dotted path.
    * `expect_no_errors`.
    * `screenshot {label}`.

    Limits: at most 12 tests, 40 steps per test, 120 game seconds per test; values are bounded.
  * The **QA Playtester** (existing role, updated prompt) writes `QATests` from the design, its checks and the controls. It must include: real-input movement, the level-up menu, the lose path (`god: false`, expecting `lose`), restart after lose, and the HUD state changing.
  * **Runner**: Playwright opens `index.html?headless`, sets the flags (god per test), and steps in game time. It returns per-test pass or fail, the failing step index, `sf.state`, errors and screenshots. No model output is ever executed; steps are interpreted.
  * **Exporter**: deterministic code turns `QATests` into `build/tests/test_game_qa.py`, a pytest + Playwright file the user can run with `pytest build/tests`. Model text appears only inside escaped string literals (built with `repr`/`json.dumps`, never by formatting code).
* **Pipeline**:
  * QA tests are written once per design version and saved as `game/vN/qa-tests.json`.
  * They run after the bot playtest.
  * Failures join the repair loop's failure summary, as "QA test 'restart works' failed at step 5: expected event lose…".
  * A QA test still failing after the repairs is shown at G4 as "test or game bug?". Feedback can drop the test ("ignore the restart test"), which goes through `Revision`.
  * Later versions rerun earlier versions' tests, as a regression suite.
* **G4 report / UI**: a QA section lists each test, pass/fail, the failing step and its screenshots, with a link to download the exported Playwright file.

### Tests: `tests/test_qa_tests.py` + `tests/test_game_stage.py`
* `test_schema_rejects_unknown_step_key_and_limits`: an unknown step type, a key outside the whitelist, 200 seconds, or 50 steps → validation error.
* `test_runner_passes_and_fails_on_mock_game`: against `mock_game.js`, "hold right 2 s, expect no errors, expect enemy_spawn" passes, and "expect event never_emitted" fails at the right step index with a screenshot.
* `test_lose_path_without_god`: on the mock game with god off, the "expect lose within 60 s while idle" test passes.
* `test_export_compiles_and_escapes`: a test named `"; import os; os.system("x") #` exports to a file that passes `py_compile`, the payload appears only inside a string literal, and the exported file passes when run against the mock build (`pytest` subprocess, Playwright).
* `test_failures_reach_repair_prompt`: a mock programmer receives the QA failure text in its repair prompt.
* Stage: `test_qa_regression_across_versions`: the v1 tests are rerun on v2, and dropping a test by feedback removes it from v2's run.

### Acceptance
* A live survival build runs at least 5 QA tests, including lose and restart, and exports `build/tests/test_game_qa.py`, which passes when the user runs it.
* At least one real bug class not covered by the bot playtest is exercised, such as restart or menu input.
* Full pytest passes.

---

## Cross-cutting

* **Budget**: every new call goes through `Budget`. Per-build caps:
  * image calls: `max_art_calls` plus `review_max_fixes`;
  * Spark: +1 call for the review and +1 for QA tests.
* **Offline**: each new model step has a mock fallback, so `--mock` and pytest stay free and deterministic.
* **Security**: no model-written code is executed apart from the game itself, which runs sandboxed in the browser under a CSP. `ai_allowed: false` art is never sent to Muse. No keys are written to logs or files.
* **Docs**: `docs/milestone-10-art-and-qa.md` (one section per milestone, with live results), plus README and role prompt updates.
* **Before M10.1 starts**: the M9, G1 and plan work so far is uncommitted. The user decides how to commit it (one commit, or split by milestone) before M10.1's commit.
* **Test runs**: `python -m pytest -q --basetemp=workspace/.pt`, and delete `workspace/.pt` afterwards. The 3 known asset-production failures stay out of scope.
