# Level 2: game design, menus and levels, on an OKF record of the whole game

Builds on M10 (art requests, art review, QA tests) and M11.1 (the game database). Level 1 of
the project made one-screen games: one session, one scene, written as code. Level 2 makes
real games: a title and menus, several levels, progress between them, all designed by agents
that read the game's own OKF record instead of its code.

Six sessions, in order. Per AGENTS.md each one ends with pytest, a commit, a short report of
what works and what doesn't, and a stop for the go-ahead.

## Decisions taken

| Topic | Decision |
| --- | --- |
| Story | **Only when the human asks.** G1 asks "Story?" with "No story" as the default; the brief draft may suggest "Yes" only when the human's own words ask for a story, and the human sees it. G2 and G4 can add one later ("add a story ..."). Without a story there is no story call, and no invented names, personalities, backstories or arcs anywhere. |
| The game's record | **Everything about a game lives in its OKF v0.2 bundle** (`runs/<id>/game/db/`): design, entities, art, code, QA tests, levels, menus, builds, the human's decisions, and the story when there is one. Data files, code and images sit under `references/`; every one has a concept that describes it. The run's SQLite artifacts stay the audit trail; the bundle is what agents read. |
| How agents read it | Through read-only MCP tools (`game_index`, `game_search`, `game_show`, `game_context`), the progressive disclosure the OKF Agent Memory Convention asks for: index, then search, then one concept. Never the whole bundle, never the code. |
| Levels | **Data, not code.** A level is a JSON file the game loads through the SDK (`sf.level`). The Programmer writes the mechanics once; the Level Designer writes levels; code validates every level before it is built. |
| Menus | **Data, not code.** A menu spec rendered by the SDK in the game's style, keyboard first. Muse Image draws the title art; the SDK draws all text. |
| Gates | G2: design + game structure (+ story if asked). G3: menus and the level plan, before levels 2+ are built. G4: playtest, per level. G5: final review of the whole game and promotion of its record. |
| Muse | New enemies, tiles and title art come from Muse Image, styled from the game's locked art (M10 pipeline). Deterministic pixel tools clean, check and preview them. |

## Known facts these plans rely on

* **OKF v0.2 is current** (spec: [GoogleCloudPlatform/open-knowledge-format SPEC.md](https://github.com/GoogleCloudPlatform/open-knowledge-format/blob/main/SPEC.md), checked 2026-11-01; no later version found). A bundle is a directory of Markdown concepts with YAML frontmatter; a concept's ID is its path without `.md`. Only `type` is required (open vocabulary); `title`, `description`, `resource` and `tags` are recommended; `sources`, `generated`, `verified`, `status` (draft, stable, deprecated) and `stale_after` are standard. `index.md` (progressive-disclosure links, `okf_version` only at the root) and `log.md` (`## YYYY-MM-DD` entries, newest first) are reserved. Links are plain Markdown, absolute bundle-relative (`/entities/hero.md`) recommended. `resource` is the canonical path or URI of the thing described. Code and external material go by convention under `references/`. Data is described in a `# Schema` section. Trust tiers are derived: unverified, machine-confirmed, human-reviewed (`verified.by: human:<id>`). **The spec says nothing about images or binaries:** they are plain files under `references/`, pointed to by `resource` and links.
* **OKF Agent Memory Convention v0.1** ([okf-memory](https://github.com/okf-memory/okf-agent-memory/blob/main/docs/spec/CONVENTION.md)), on top of OKF v0.2: agents never claim human verification for generated content, mark inferences, never load the whole corpus, prefer index and search, and human corrections take priority over agent output.
* Our store already writes v0.2 (`memory/okf`): validated, git-committed batches, `log.md`, generated/verified provenance, trust tiers, PNG resources (M11.1). It links relatively (`../characters/x.md`); level 2 switches to absolute links.
* Playwright key presses reach a game while the harness steps it frame by frame, menus included (M10.4 probe). QA tests already find what the bot cannot: in 2 of 3 live builds the Programmer wrote auto-fire that an idle player could never lose to.
* A build with art review and QA costs about $0.35-0.48 live; a Spark call with high reasoning about $0.02-0.05; a Muse image $0.01.

---

## L2.1: Story only on request; the whole game as an OKF bundle

**Goal.** The game's bundle becomes its complete record, design to code to decisions, so every later agent reads the bundle and nothing else. The story is written only when the human asks.

### Changes
* **Story on request**
  * `GameBrief.story`, a G1 brief field: "No story" (default) or "Yes: named characters, a world and a story arc". The brief draft is told to suggest "Yes" only when the human's own words ask for a story.
  * `spriteforge game make ... --story`; without it, no story.
  * G2 and G4 forms get **Add a story**; free G4 text that asks for one reaches it through the Director (`Revision.story_changes`), which writes or revises the story bible.
  * Without a story: no `StoryBible` call, no `story/`, `characters/` or `world/` concepts. Entities are described from the design alone; the canon is the design's rules plus the human's decisions. The `game.story` config switch goes; the brief decides.
* **The bundle** (`runs/<id>/game/db/`, `out/db/` for `game make`), all OKF v0.2:

  | concept | type | resource / content |
  | --- | --- | --- |
  | `game` | Game | title, pitch, camera, current version, play link; links to every section |
  | `design/rules` | Game Rules | controls, rules, progression, difficulty, win/lose, HUD |
  | `design/events` | Event Contract | `# Schema`: every event the build emits (checks + SDK events) and every `sf.state` key |
  | `entities/<id>` | Entity | role, size, behaviour, animations, its asset; story fields only with a story |
  | `art/style`, `art/assets/<id>` | Art Style, Asset | `/references/art/<id>.png` (4x portrait), `-sheet.png`; source, Muse prompt, locked version, measured look |
  | `code/overview`, `code/modules/<file>` | Code Overview, Code Module | `/references/code/<file>.js`; scenes, functions, events emitted, state keys, keys read, asset ids used, menus opened, the Programmer's notes |
  | `qa/suite`, `qa/tests/<slug>` | QA Suite, QA Test | `/references/tests/test_game_qa.py`; `# Schema` of a step; each test's purpose, steps, last result, the build it came from; dropped tests deprecated |
  | `builds/v<N>` | Build | verdict, attempts, QA, art review, cost, what changed and why |
  | `decisions/<n>` | Decision | the human's words at a gate (redacted), what they changed, the build that answered them |
  | `story/*`, `characters/*`, `world/*` | Story, Character, Location | only with a story |

  * Code modules are read off the final code by code (no model call): classes and scenes, functions, `sf.event` names (matched to the checks they satisfy), `sf.state` keys, `sf.input` and keys, asset ids, `sf.choose` titles, line counts.
  * Absolute bundle-relative links; `resource` on every concept that describes a file; `# Schema` on data concepts; one `log.md` entry per write; `okf_version: "0.2"` at the root only.
  * Approvals promote what the human saw (as in M11.1); anything that changes afterwards is unverified again; whatever disappears is deprecated, never deleted.
* **Reading it**
  * `spriteforge game db mcp <folder>`: read-only MCP tools `game_index(dir)`, `game_search(query)`, `game_show(id)`, `game_context(focus, budget)`; granted per role like the library tools.
  * `context_pack` reads the new layout and works without a story.
  * `spriteforge game db sync <run or folder>` rebuilds a bundle from a run's artifacts (old runs, repairs).
* **Validation**: our validator on every write, plus the external `okf validate` when installed (check `okflib` from PyPI in the session; optional).

### Tests: `tests/test_game_db.py` + `tests/test_game_stage.py`
* A mock gated run asks for no story: no story artifact, no story call, no `story/` concept, entities without invented lore.
* G1 "Yes" writes the story; "Add a story" at G4 writes it then.
* Conformance: every concept has `type`; every link is absolute and resolves; every `resource` exists; the root index alone declares `okf_version`; `log.md` dates are ISO and newest first.
* Code modules of the mock game list `enemy_spawn`, `enemy_defeat`, `gem_collect`, `win`, `lose` and the state keys `hp`, `x`, `y`, `time`, `score`; the resource equals the build's `game.js`.
* QA suite and per-test concepts; a dropped test is deprecated; build and decision concepts per version; the MCP tools over stdio; `sync` rebuilds the same bundle.

### Acceptance
* A live survival run that does not ask for a story makes no story call. Its bundle holds the design, entities with images, art, code modules, the QA suite, the build and the human's decisions. A "level 2" context pack read through the MCP tools stays within its budget.
* A live run that answers "Yes" at G1 gets its story in the bundle. Both bundles pass validation.
* Full pytest passes.

---

## Revised order after the L2.1 playtest (2026-11-01, awaiting go-ahead)

The human played the L2.1 slime builds. Levels were flat with no challenge or logic to work
out; the slime slid instead of moving like a slime; crates and walls vanished with no break
animation; transforms showed no lasting change; floating slabs sat above the slime's jump. The
Programmer had also put all three levels in one scene, so the bot cleared only the first. Root
causes:
* no agent researches how good games of the genre build challenge before designing;
* nobody designs levels: the Programmer improvises them in code;
* the art pipeline draws stills and walk rows but no motion design (squash and stretch, break,
  transform) and no effect states;
* nothing checks a level against the player's real movement (jump height and distance).

New order: **L2.2 Research and game plan → L2.3 Level Designer and levels as data (old L2.4 +
L2.5) → L2.4 Art and animation design → L2.5 Structure, flow and menus (old L2.2 + L2.3) →
L2.6 The whole game.** The rest of this file keeps the old sections for reference; the three new
sessions follow.

Fixes that go first, inside L2.2 (found in the L2.1 live runs):
* A design change re-planned all the art: the jump-height feedback redrew 8 locked sprites with
  Muse. On a design change, entities that still exist keep their locked art; only new or
  targeted entities are planned.
* While a build runs the UI hides the last playable build: always show the latest build's Play.
* "An idle player can lose" becomes a Programmer requirement (QA caught it again).

### L2.2: Research and the game plan (the first agent gets web search)

**Goal.** Before designing, the Game Designer studies how games of this kind work, with live
web search, and writes a detailed, human-approved game plan from the human's requirements.
The plan gives every later agent concrete numbers and challenge ideas, not adjectives.

* **Web search, verified first.** Meta documents `{"type": "web_search"}` as a built-in,
  server-side tool on the **Responses API** (`https://api.meta.ai/v1`, `responses.create`)
  with inline citations. Our provider uses Chat Completions, where Meta does not mention the
  tool. A live probe decides three things before any code: the path, the citation format,
  and whether `json_schema` output and `web_search` work together in one call (if not: one
  research call with search, then one structured call). Endpoint, model and caps go in
  config.yaml. The probe also measures the per-search cost, which Meta's docs don't state.
* **Research (G1 → G2, one or two Spark calls).** `Research`: 3-6 reference games of the genre
  (title, URL, what to learn from each), the patterns that make the genre's levels interesting
  (gating, teaching a mechanic safely, combining abilities, risk and reward, pacing), the feel
  numbers players expect (jump height and air time in tiles, dash length, coyote time), common
  pitfalls, and the sources. Rule: references teach patterns. The design is original and never
  copies a specific game's levels, characters or art. Searches capped (`research.max_searches`,
  default 8); offline stand-in from the genre seed.
* **The game plan (`GamePlan`, strict, Game Designer).** The human's requirements, numbered and
  kept word for word. Then:
  - abilities with numbers (move speed, jump height in tiles, air time, dash distance and
    cooldown) and the **kinematics** code derives from them (max jump height and gap width in
    pixels), which later checks use;
  - each mechanic and what it is for: dissolve, acid kills, metal breaks;
  - a challenge vocabulary: obstacle kinds and how each tests the player;
  - per level: a teaching goal, its new idea, its challenges, which abilities it needs and
    how hard it is;
  - feedback and feel: what the player sees and hears when something happens (kill, transform,
    break, pick up, hurt);
  - an animation list per entity (slime: idle, hop cycle with squash and stretch, jump, land,
    dash, transform; crate: break; wall: crumble);
  - risks.

  Every human requirement must map to a part of the plan (checked by code), and the design is
  written from the plan.
* **G2 shows** the research (sources, patterns), the plan, then the design; feedback revises
  the plan. The bundle gains `research/*` (references with URLs as sources) and `plan/*`.
* **Tests:** the probe is recorded; the plan validator (every requirement covered,
  kinematics consistent, every level has a teaching goal and a new idea); mock research
  and plan; the art-lock fix; the Play link.
* **Acceptance (live, the slime game):** research cites real sources; the plan has numbered
  requirements, kinematics and three distinct levels; the art is not redrawn on a design
  change. Cost about +$0.05-0.10 per run (plus searches).

### L2.3: The Level Designer and levels as data (old L2.4 + L2.5, for platformers first)

**Goal.** A Level Designer writes each level as data from the plan and the game's record; code
proves each level can be played with the slime's real movement; the Programmer writes the
mechanics once.

* `LevelSpec` as in the old L2.4, plus platformer pieces: tile rows, one-way platforms,
  crates (what each holds), breakable walls, spikes, gaps, enemies with patrol paths,
  checkpoints and the exit.
* **Reachability by kinematics** (`game/levels.py`): a search over tiles using the plan's jump
  height, gap width and dash distance (and what each transform unlocks). It checks four things:
  1. every platform the level needs can be reached;
  2. the exit can be reached;
  3. a wall that gates the exit has its metal crate reachable before it;
  4. an enemy that blocks the way has acid available.

  Unreachable slabs like the one in L2.1 are rejected and repaired, and code repairs what it
  can (lowering a slab).
* **Logic and challenge checks:** each level uses its planned new idea, has a challenge
  between checkpoints, and gets harder than the last. A level preview is drawn by code.
* SDK `sf.level` loads levels; the scenes are the SDK's; the bot plays one level at a time
  (`?level=n`) along the path code found, so all three levels are played and checked.
* G4 per level: play link, map preview, feedback changes that level only.
* **Acceptance:** three slime levels that each teach something, every slab reachable (proved
  by code and played by the bot), all three cleared in the playtest.

### L2.4: Art and animation design (an Art Designer for motion and effects)

**Goal.** Things move and react like what they are: the slime hops and squashes instead of
sliding, crates and walls break, and transforms visibly change the slime for as long as they
last.

* **Art Designer** (the Art Director gains an animation brief): per entity, the animation rows
  from the plan with frame counts and what each frame shows (hop: squash, stretch, airborne,
  land), timing (fps, loop or once), and effect states.
* **Muse Image draws the rows** as sheets (existing M10 pipeline), using the locked art as the
  style reference. Transforms are their own rows (acid: green drip; metal: grey shine), or a
  tint plus particles when a row fails its checks.
* **Effects as data:** an `EffectSpec` (particles, flash, shake, squash) per event (kill,
  transform, break, pickup, hurt). The SDK plays them with `sf.fx(event, x, y)`, so the
  Programmer does not improvise them.
* **Motion checks by code:** frames of a moving row must differ (no sliding still frame), keep
  their size and ground line, and loop cleanly. Break rows end empty. The Art Reviewer sees
  the rows animated.
* **Acceptance:** the slime's hop cycle, crate break, wall crumble and both transform states,
  checked by code and seen at G4.

Then L2.5 (structure, flow and menus: the old L2.2 and L2.3) and L2.6 as written below.

### Questions for the human
1. This order (research first, then levels, then animation, then menus)?
2. Web search spend: about 8 searches per research (cost measured by the probe). Research every
   run, or only when you ask at G1 ("Research similar games?", default yes)?

L2.2 (2026-11-02) went ahead in this order and, for question 2, built the G1 question
("Research similar games?", default yes; `--no-research` in tools/game_live.py). Measured: one
research is 8 searches plus about 66k input tokens, about $0.09. Change the default if you
prefer research only on request.

---

## L2.2: Game structure: screens, flow and progress (game design)

**Goal.** A game is a title, a run of levels, results in between, game over and victory, with progress saved, designed at G2 and running on SDK screens. The level is still level 1; the structure is what is new.

### Changes
* **Design** gains `structure`: planned levels (1-8, a name and one-line goal each), what carries over between levels (score, upgrades, health), what is saved (unlocked levels, best results) and how the game is won (finish the last level). G2 shows it with the design; feedback revises it.
* **SDK `sf.flow`**: title, level intro, results, game over, victory and a pause overlay (Escape), in plain default screens until L2.3 styles them; `sf.progress` (local storage, cleared by `?reset=1`; every QA test starts clean); events `title`, `level_start {n}`, `level_complete {n}`, `level_failed {n}`, `game_complete`, `pause`, `resume`.
* **Programmer contract** (`API.md`, requirements): the game writes one level scene; `sf.level()` says which level and with what settings; the flow is the SDK's. A requirement that an idle player can lose (the bug QA caught twice).
* **Playtest and QA**: the bot plays each level directly (`?level=n&autoplay=1&headless=1&god=1`); QA flow tests: title → Enter → level → pause → resume → lose → retry → back to the title. A `reload` step keeps local storage, so saved progress can be tested.
* **Bundle**: `design/structure`; `levels/<n>` stubs for the planned levels (draft).

### Tests: `tests/test_game_flow.py` + stage additions
* SDK flow on the mock game: the order of screens and events, pause stops game time, Enter and Escape with real keys; progress survives a `reload` and `?reset=1` clears it.
* The structure is validated (1-8 levels, unique names) and shown at G2; feedback changes it.
* The bot playtest of `?level=1` matches the single-level playtest; flow QA tests pass on the mock game.

### Acceptance
* A live survival build with a title, results, game over and victory; the flow QA tests pass; the planned levels are in the bundle as drafts.
* Full pytest passes.

---

## L2.3: Menu design

**Goal.** Every screen is designed for its game: a Menu Designer writes the menus as data in the game's style, Muse draws the title art, the SDK renders them, QA tests drive them.

### Changes
* **Menu Designer** (new role file): reads the bundle (`game_context("menus")`: title, structure, art style, story if any).
* **`MenuSpec`** (strict, all fields required): theme (palette from the locked art's measured colours, panel style, the built-in pixel font), and the screens: title (title, subtitle, prompt), main (start, continue, level select, settings, credits), pause (resume, restart, settings, quit to title), settings (difficulty easy/normal/hard as multipliers the game reads, show controls, fewer flashes), level select (from progress), results (stats from `sf.state`), game over (retry, title), victory (credits).
* **Muse Image**: a title background and an emblem (2 calls, styled from the locked art, no text drawn by Muse), checked by the pixel tools.
* **SDK renders menus from the spec**: arrows/WASD + Enter/Space, Escape is back, mouse too; `menu_open {screen}`, `menu_select {screen, item}`; `sf.menu = {screen, items, index}` for tests.
* **Validator**: every action known, every screen reachable from the title, Escape always leads somewhere, every label fits with the font, text contrast on its panel.
* **QA**: steps `expect_menu {screen}` and `select {item}` (moves the highlight with real keys, then Enter); difficulty from settings shows in `sf.state.difficulty`.
* **Bundle**: `menus/<screen>` concepts, `/references/menus.json` (`# Schema`), `/references/art/title.png`.
* **G3** (new): the menu screenshots and the level plan, before levels 2+ are built; feedback revises the menus (Menu Designer) or the plan.

### Tests: `tests/test_menus.py`
* Validator rejects an unreachable screen, an unknown action, an overlong label, unreadable contrast.
* Every screen of the mock spec renders without errors; navigation with real keys reaches each screen; menu QA steps pass and fail as they should.
* The offline Menu Designer gives a valid spec for any design; the title art falls back to a code-drawn panel offline.

### Acceptance
* A live build whose menus use its palette and Muse title art; G3 shows every screen; the menu QA tests pass.
* Full pytest passes.

---

## L2.4: Levels as data: the level format, the SDK level engine and the validator

**Goal.** A level is a data file the game loads, checked by code, so a new level needs no new code.

### Changes
* **`LevelSpec`** (strict, all fields required): number, name, story beat ('' without a story), objective (survive / defeat / collect / reach the exit / boss, with its target and time), map (columns, rows, a legend of tile codes to tile entities and solidity, the rows as strings; or an arena the size of the screen), player start, spawns (entity, from corners / edges / cells, waves: when, how many, how often), pickups and props placed, difficulty multipliers (enemy health, speed, spawn rate), and `new_entities` it needs.
* **SDK `sf.level`**: loads `levels/<n>.json` from the build, builds a Phaser tilemap from the tile art with a solid layer, runs the spawn schedule through the game's `onSpawn(entity, x, y)`, tracks the objective, emits `level_start`, `wave_start`, `objective_progress`, `level_complete` / `level_failed`; `sf.path(from, to)` (grid search) for bots.
* **Programmer contract**: mechanics only (behaviour per entity id, combat, pickups, `onSpawn`, the multipliers).
* **Validator** (`game/levels.py`): size limits; legend tiles exist with art; the start is on floor; exits and pickups reachable; spawn points valid; at most 150 alive (simulated from the schedule); the objective possible (a defeat target no larger than what spawns, a collect target no larger than what is placed); difficulty not below the previous level; every entity has behaviour and art, otherwise listed in `new_entities`.
* **Level 1 as data**: made by code from the design (an arena of its floor and wall tiles, spawns as its rules say); the code is adapted to the level engine once.
* **Bundle**: `levels/<n>` with `# Schema`, `/references/levels/<n>.json`, and a map preview drawn by code (`/references/levels/<n>.png`).

### Tests: `tests/test_levels.py`
* The validator catches an unreachable exit, an unknown tile, too many enemies alive, an impossible objective, a drop in difficulty.
* The mock game on the level engine: spawns follow the schedule, the objective completes, the events arrive in order; the map preview matches the grid.
* The bot playtest and the QA tests of level 1 as data match the single-level results.

### Acceptance
* A live survival game running from its level-1 file passes its bot playtest and QA tests as well as before; the level, its data and its preview are in the bundle.
* Full pytest passes.

---

## L2.5: The Level Designer, grounded in the bundle

**Goal.** The Level Designer writes levels 2..N from the bundle alone (canon, structure, earlier levels, entities), each checked, built, played and approved at its own gate.

### Changes
* **Level/Tile Designer** (existing role, updated prompt and the `game_db` tools): its prompt is the context pack for "level n: <planned goal>", the earlier levels' concepts, the entities and tiles it may use, and the difficulty target. It answers a `LevelSpec`; `fit_level` repairs what code can; the validator's problems go back once.
* **New entities and tiles** the level asks for: art through the M10 pipeline (styled from the locked art), behaviour through a Programmer change, QA tests for them, a new design version that lists them.
* **Per level**: the bot plays it (checks from its objective); the QA Playtester writes tests from its spec (the objective reachable with real keys, results, next level).
* **G4 per level**: play link (`?level=n`), map preview, the new art; feedback changes that level only, earlier levels stay locked and their tests rerun.
* **Continuity checks** by code: every entity used exists in the bundle and is not deprecated; the objective is one the design allows; the beat is the arc's next one when there is a story; the difficulty curve holds.
* **Bundle**: the level, its decisions, its new entities and assets; human-reviewed when G4 approves it.

### Tests: `tests/test_level_designer.py`
* The offline Level Designer writes a valid level 2 from a mock bundle; repairs and the one retry; continuity checks reject an unknown or deprecated entity.
* A level that needs a new enemy brings art, a code change and QA tests; feedback on level 2 changes neither level 1's file nor its results.

### Acceptance
* Live: level 2 written from the bundle (its context pack within budget, no code read), validated, built, passing its bot playtest and QA tests, shown at its gate; "make level 2 shorter" changes level 2 only.
* Full pytest passes.

---

## L2.6: The whole game: all levels, regression, final review (G5)

**Goal.** A finished multi-level game: progress across levels, every level retested on every build, the art reviewed as one set, a final review, and lessons kept for future games.

### Changes
* **One build with every approved level**; level select from progress; finishing the last level shows victory and credits.
* **Regression**: every build plays every level with the bot and runs every QA test (levels, flow, menus), within a time budget per level.
* **Art Reviewer across levels**: one sheet of every entity and one screenshot per level.
* **G5 final review**: levels, QA, art, cost, the bundle; approval promotes the whole bundle; the Librarian writes lessons (generated, for human promotion) to the shared memory (`lessons/`); the export holds the build, its bundle and its tests.

### Tests: `tests/test_game_complete.py`
* A three-level mock game: progress, level select, victory; regression runs every level's tests; G5 promotes everything and writes lessons as generated knowledge.

### Acceptance
* A live three-level survival game: every level passes its bot playtest and QA tests, G5 approves it, its bundle is human-reviewed, and lessons are in the shared memory awaiting promotion.
* Full pytest passes.

---

## Cross-cutting

* **Budget** (per build, in config, every call through `Budget`): +1 Spark for the menu spec, +1 per level (+1 retry), +1 QA call per level, the story call only when asked; Muse: 2 for title art, at most 4 new entities or tiles per level.
* **Offline**: every new model step has a deterministic stand-in (structure, menu spec, level spec, lessons), so `--mock` and pytest stay free.
* **Security**: levels and menus are data; no model-written code runs except the game itself (sandboxed, under the CSP); bundle writes are secret-checked; `ai_allowed: false` art never goes to Muse.
* **Test time**: every level adds bot and QA time; mock levels stay small.
* **Docs**: `docs/level-2.md` (one section per session, with live results), README and role prompts.
* **Interpretation to confirm**: "store the whole dataset logic and everything" is read here as the game's full record (design, data, code, tests, decisions) in its OKF bundle; the cross-game memory (`workspace/memory`, with the asset catalog) is already OKF and stays as it is.
