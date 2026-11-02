# Level 2: game design, menus and levels, on an OKF record of the whole game

The plan is `level_2.md` (six sessions, L2.1 to L2.6). This file records what each session
built, how to use it, and its live results.

The test game for level 2 is a side-view platformer with three levels: the hero is a slime
that moves, jumps and dashes but cannot hurt enemies. Wooden crates dissolve into coins or a
transformation: acid lets the slime defeat enemies, metal lets it break walls. L2.1 builds it
as one level (levels arrive in L2.2 to L2.5).

## L2.1: story only on request; the whole game as an OKF bundle

### Story on request

* The G1 brief asks **Story?**: "No story" (default) or "Yes: named characters, a world and a
  story arc". The brief draft is told to suggest "Yes" only when the human's words ask for a
  story, and code enforces it (`fit_draft`: words like story, plot, lore, backstory,
  cutscene, quest, "characters named").
* Without a story there is no story call and no `story/`, `characters/` or `world/` concepts;
  entities are described from the design alone. The `game.story` config switch is gone (old
  run settings that carry it still load).
* G2 and G4 offer **Add a story** while the game has none. At G2 it writes the story for the
  current design and shows the design again; at G4 it writes it and shows the same build
  again (no rebuild). G4 text that asks for a story reaches the Director as
  `Revision.story_changes`; a revision that only touches the story never rebuilds.
* Stories are numbered artifacts `game/story/v<S>-<trigger>.json` (`design-v2`, `gate-5`), so
  a re-entered step never writes one twice. A design change revises the latest story.
* `spriteforge game make "..." --out DIR --story`; without `--story`, no story.

### The bundle (`runs/<id>/game/db/`, `out/db/` for `game make`)

| concept | type | what |
| --- | --- | --- |
| `game` | Game | title, pitch, camera, current version, play link, a link to every section |
| `design/rules` | Game Rules | controls, player, world, rules, progression, difficulty, win/lose, HUD, checks |
| `design/events` | Event Contract | `# Schema`: every event (design checks, SDK, code-only) with the files that emit it; every `sf.state` key with its contract and the files that set it |
| `entities/<id>` | Entity | role, size, looks, behaviour, animations, its image and asset; a character section only with a story |
| `art/style`, `art/assets/<id>` | Art Style, Asset | direction, scale, the locked assets; per asset `resource: /references/art/<id>.png` (4x first frame) and the sheet, source, Muse prompt, locked version, measured look |
| `code/overview`, `code/modules/<file>` | Code Overview, Code Module | files in load order and the Programmer's notes; per file `resource: /references/code/<file>.js`, classes and scene keys, functions, events (with the checks they satisfy), state keys, input and keys, flags, asset ids, menus, lines |
| `qa/suite`, `qa/tests/<slug>` | QA Suite, QA Test | `resource: /references/tests/test_game_qa.py`, the `# Schema` of a step; per test its purpose, steps, last result and the build it came from; dropped tests deprecated |
| `builds/v<N>` | Build | verdict, checks, attempts, QA, art review, cost, what changed and the decision that asked for it |
| `decisions/<n>` | Decision | the G1 brief, then each answered G2/G4 gate: the human's words (redacted), what it changed, the build it asked for or was about |
| `story/*`, `characters/*`, `world/*` | Story, Story Arc, Canon, Character, Location | only with a story |

* Written by code from the run's artifacts and gates (`stages/game_record.py`) or a
  `game make` folder (`game/dbsource.py`), never from objects in memory, after every step
  that changes the game. No model call. Code modules are read off the code by regular
  expressions (`game/codeinfo.py`).
* OKF v0.2: links are absolute from the bundle root (`/entities/hero.md`); `resource` points at
  a file under `references/`; `okf_version` only in the root index; one `log.md` entry per
  write, newest first. A bundle concept is written whole (a field left out is gone).
* Trust: approving G2 or G4 promotes every concept to human-reviewed; anything that changes
  afterwards is unverified again; a concept no longer wanted is deprecated, never deleted.
* Secrets: every concept and every text file is secret-checked. A code file that looks like it
  holds a credential is left out of `references/` and its concept says it was withheld.
* Validation on every write: our validator (in bundle mode it also requires absolute links
  that resolve and `resource` files that exist), plus `okf validate` or okflib's
  `okflib -C <dir> validate` when installed (`pip install okflib`, optional).

### Reading it

```powershell
spriteforge game db context runs/<id> --focus "level 2: a harder cave" --budget 2000
spriteforge game db mcp runs/<id>     # MCP stdio: game_index, game_search, game_show, game_context
spriteforge game db sync runs/<id>    # rebuild from the run's artifacts (old runs, repairs)
```

`game_index(dir)`, `game_search(query, budget)`, `game_show(id)` and
`game_context(focus, budget)` are read-only and never return code files. Grant them per role
like any MCP server: a config `{"transport": "stdio", "command": "spriteforge", "args":
["game", "db", "mcp", "<folder>"]}`, `spriteforge mcp add game-db <file>`, then
`spriteforge mcp enable game-db --role "Level/Tile Designer"`.

The context pack holds the game, the rules, the story (only when there is one), the player
entity with its image, the art style, then what the focus names; build history (builds,
decisions, QA tests) only when the focus asks for it.

### Tests

`tests/test_game_db.py`: code modules of the mock game; conformance of a bundle without a
story (types, absolute resolving links, resources, root-only `okf_version`, ISO newest-first
log); code, QA, build and decision concepts, a dropped test deprecated; story concepts only
with a story; trust tiers and secrets; the context pack and its budget; the MCP tools over
stdio; `game make` then `game db sync` gives the same bundle.
`tests/test_game_stage.py`: a gated mock run with no story makes no story call and has no
story concept; "add a story" at G4 writes it without a rebuild; `game db sync` rebuilds the
same bundle; G1 "Yes" writes the story with design v1 and revises it with design v2; "Add a
story" at G2 shows the same design again; the draft suggests a story only when asked.

### Live (2026-11-01)

Both runs use the level-2 test game (the slime platformer), driven by
`tools/game_live.py` (G1 from flags; G2 approved by the test operator; G4 left to the human).

**No story** (`workspace/l21-slime`): "Slime Shift Adventure", 13 entities. No story artifact
and no story call; 12 Spark + 10 Muse Image calls, about $0.70 (three repairs). The bundle
has 43 concepts and 28 files (game, rules, events, 13 entities, art style plus 13 assets with
images, code overview and module, QA suite plus 7 tests, build v1, 2 decisions) and our
validator reports no errors. A "level 2: a harder cave with more cracked walls and flyers" pack
read through the MCP tools (`game_context`) is 1941 tokens (budget 2000): the game, the
rules, the slime with its image, the art style, then the enemies and walls the focus names;
no code. QA 7/7 pass, but the bot playtest fails `exit_level` (1 of 3) and `win`: the
Programmer wrote all three levels into one scene and the bot clears only the first. Levels as
data (L2.4) and per-level playtests (L2.5) address this. The Art Director also picked a
library "flying eye" sprite for the slime, a poor fit worth fixing at G4.

**Story asked for at G1** (`workspace/l21-story`, stopped at G2): "Slippy the Slime: Three
Trials". The story bible was written with design v1 (`game/story/v1-design-v1.json`): Pip the
lost slime, two minions, three locations, a 4-beat arc. The bundle has `story/overview`,
`story/arc`, `story/canon`, three characters and three world concepts, with no validation
errors. 5 Spark calls, about $0.06.

Not checked live: the external okflib validator (`okflib -C <dir> validate`). It is supported
when installed, but it was not run in this session.

Full pytest: 318 passed, 4 failed. Two `test_asset_production` failures are known from M11.
`test_asset_ui` fails on an uncommitted change in the old asset pipeline (hero walk 2 to 4
frames). `test_qa_regression_across_versions` failed once while the full suite ran (a QA
timing flake also seen in M11) and passes when run alone.

## L2.2: research and the game plan

After the L2.1 playtest the order changed (level_2.md, "Revised order"): research and a game
plan first, so every later agent gets concrete numbers and challenge ideas instead of
adjectives.

### Web search, probed first

`tools/probe_web_search.py` (2026-11-02, three calls, about $0.07) decided the path before any code:

* Chat Completions rejects `{"type": "web_search"}` (HTTP 400 "tools[0] did not match any
  supported type"); `POST /v1/responses` accepts it.
* Citations: `url_citation` annotations on `output_text` are few (one per answer); with
  `include: ["web_search_call.action.sources"]` every search lists the pages it returned
  (11-12 each).
* `text.format` json_schema (strict) and `web_search` work in one call. The answer comes as
  several `message` items: progress lines first, the JSON last. A URL inside the JSON is not
  always one a search returned (the probe's Shovel Knight link was not), so code checks them.
* Searches are not in `usage`. Each `web_search_call` item (a search or an `open_page`) is
  counted as one search and charged at Meta's documented $2.50 per 1,000
  (`budget.web_search_cost`); `max_tool_calls` caps them (`research.max_searches`, 8).

Endpoint, caps and price are in `config.yaml` (`research`, `budget.web_search_cost`,
`llm.reasoning.research` / `plan`).

### Research (G1 to G2, one call)

* G1 asks **Research similar games?** ("Yes: study similar games on the web first", the
  default, or "No research").
* `providers/muse_search.py` sends one Responses call (role prompt as `instructions`, the
  `Research` schema, `web_search`, the sources included). If the last message does not fit the
  schema, one Chat Completions call structures the notes (using only the pages found).
* `Research`: 3-6 reference games (title, a page, what to learn), patterns (teaching a
  mechanic safely, gating, combining abilities, risk and reward, pacing: how good games do it
  and how this game could), feel numbers in tiles and seconds, pitfalls. The prompt says
  references teach patterns and the game never copies a game's levels, characters, story or
  art.
* Code (`game/research.py`): a reference URL is **verified** only when a search returned that
  page (scheme, `www.` and trailing slash ignored); other URLs are kept, marked and noted;
  non-web URLs are dropped; lists are bounded. A failed search falls back to the offline
  stand-in (from the genre notes, no references) and says so.
* Saved once per run: `game/research.json` and `.md`.

### The game plan (`game/plan.py`)

`GamePlan` (strict, Game Designer, `llm.reasoning.plan: high`): the human's requirements
numbered R1.. and copied word for word; style; movement (move speed, jump height and air
time, dash distance and cooldown, coyote time); abilities; mechanics with what each is for;
a challenge vocabulary; per level a teaching goal, its one new idea, its challenges, the
abilities it needs and a difficulty 1-10; feel (what the player sees and hears per event);
animation rows per entity (frames, what they show, loop or once); coverage (which plan parts
fulfil each requirement: `movement`, `style`, `ability:<name>`, `mechanic:<name>`,
`challenge:<name>`, `level:<n>`, `feel:<event>`, `animation:<entity>`); risks.

Code decides what holds:

* **Kinematics** from the movement numbers: gravity `8h/T²`, take-off speed `4h/T`, the
  distance covered in the air, the highest platform a jump reaches (`max_rise`), the widest
  gap a running jump clears (`max_gap`, and `max_gap_with_dash`), coyote distance. L2.3's
  reachability check uses these numbers.
* `fit_plan` repairs ids (R1.., levels 1..), clamps difficulty, zeroes negative numbers.
* `check_plan` lists what code cannot repair: a requirement that is not in the human's words
  or not covered, a coverage part that does not exist, movement out of range (jump 0.5-10
  tiles, air time 0.2-2 s, gravity 200-5000 px/s², a jump without air time, a dash without a
  cooldown), 1-8 levels, a level without a teaching goal or a new idea, a repeated new idea,
  unknown challenges or abilities, a level easier than the one before.
* Problems go back to the Game Designer once (`retry_prompt`); what remains is shown at G2.

The design is written from the plan (`design_prompt(request, plan_brief(...))`: its numbers,
abilities, mechanics, levels, feel and animations, with the kinematics). Plans are numbered
with the design they belong to: `game/plan/vK.json` and `.md` for design vK. G2 feedback
revises the plan (new requests added as requirements copied from the feedback), then the
design from it; a design change at G4 revises the plan too.

### G2 and the bundle

* G2 shows the research (references with their pages, a warning on an unverified one,
  patterns, feel numbers, pitfalls, what was searched), the plan (requirements with what
  covers each, movement and kinematics, abilities, mechanics, challenges, levels, feel,
  animations, risks, problems), then the design.
* Bundle: `research/overview` (how it was made, patterns, feel, pitfalls; verified pages as
  OKF `sources`), `research/references/<slug>` (the lesson and the page, tagged verified or
  unverified), `plan/overview` (requirements and coverage, risks, problems),
  `plan/movement` (a `# Schema` table of the numbers and the derived kinematics),
  `plan/mechanics`, `plan/feel`, `plan/levels/<n>`. The context pack always includes
  `plan/movement`.

### Fixes from the L2.1 live runs

* **Art stays locked on a design change.** The L2.1 jump-height feedback re-planned the whole
  art set; the new plan's Muse prompts differed, so 8 locked sprites were redrawn. Now
  `plan_new_art` keeps every pick of an entity that still exists and asks the Art Director
  only about entities new to the design (no call when there are none); entities the feedback
  names are still redrawn (`apply_art_changes`).
* **Play stays visible.** `/api/runs/<id>` returns `play` (the newest build with a play link)
  and the UI shows "Play the latest build (vN)" above the gate, also while the next build runs.
* **An idle player can lose** is a Programmer requirement (`requirements()` and `API.md`):
  attacks, shots and dodges happen only on key presses or in the autoplay policy.

### Tests

`tests/test_game_plan.py`: the Responses answer read as the probe recorded it (last message,
sources of searches and page opens, citations, search count); the request contract
(`/v1/responses`, `web_search`, `max_tool_calls`, `include`, strict json_schema, reasoning)
and the search cost in the ledger; reference URLs verified only against the pages searched;
research in one call, a structuring second call, the offline stand-in; kinematics; the
offline plan's requirements are the human's sentences and pass; the checks (paraphrased or
uncovered requirement, unknown part, floaty jump, missing teaching goal, unknown challenge,
repeated idea, difficulty drop, jump without air time); `fit_plan` renumbering; problems sent
back once; a gated mock run showing research, plan and design at G2, the plan and design
revised by G2 feedback, the bundle conforming; "No research" at G1; the art lock (no Art
Director call without new entities, only the new entity planned) and a G4 design change that
revises the plan and redraws nothing; the Play link; the idle-player requirement.

### Live (2026-11-02)

The slime game (`workspace/l22-slime`, `tools/game_live.py`; G1 from flags with research on,
G2 approved by the test operator, then one G4 design change by the test operator).

**Research** (one Responses call, 8 searches, 65.7k input + 3.5k output tokens, $0.091 of
which $0.02 searches): Celeste, Super Mario Bros. Wonder and Shovel Knight: Treasure Trove,
each with a page a search returned (all 3 verified); patterns for teaching safely, gating,
combining abilities, risk and reward and pacing, each with a use for this game; feel numbers
(6 tiles/s, 3-3.5-tile jump, 0.55-0.65 s air time, 4-tile dash with 0.8 s cooldown, 0.1 s
coyote time, 0.12 s jump buffer); six pitfalls. 30 source pages recorded.

**Plan v1** ($0.043, no problems, no retry): 14 requirements copied word for word, all
covered; movement 6 tiles/s, jump 3.2 tiles in 0.6 s, dash 4 tiles every 0.8 s, so code
derived gravity 1138 px/s², gaps up to 3 tiles (7 with a dash) and platforms up to 3 tiles;
three levels with distinct new ideas (dissolving crates, acid kills, metal breaks walls) at
difficulty 2, 5, 7; eight feel events; animation rows for the slime (idle, move, jump with
stretch and squash, dash, transform, hurt), the enemy, the crate (break), the cracked wall
(break), coin and exit. Design v1 was written from it (the player text carries the
kinematics). Two requirements ("side-scrolling-platformer", "side-scroll") are copies of the
genre and camera lines `request_text` adds, not the human's prose.

G2 total: 6 Spark calls, $0.175 (intake $0.015, research $0.091, plan $0.043, design $0.026).
Research and plan together add about $0.13 per run, above the $0.05-0.10 the plan estimated:
the search context is large (`research.search_context_size: low` would cut it).

**Build v1**: all 12 design checks pass after two repairs; QA 6/6. The Art Director again
chose the library "flying eye" sprite for the slime (as in L2.1). Run total $0.70.

**G4 design change** ("The slime's jump is too low: make the jump higher, about 4 tiles, and
the dash a bit longer."): the Director split it into a design change and a code change; plan
v2 (jump 4 tiles, dash 4.5, gravity 1422 px/s², platforms up to 4 tiles, gaps up to 8 with a
dash), design v2, build v2 with all checks passing in one attempt and QA 9/9. **No art was
redrawn for the design change**: `art-plan-new.json` equals v1's picks (no Art Director
call) and every Muse entity reports "muse (locked, from v1)". The one image call of v2 was the
Art Reviewer's capped fix of the crate (it reviews every build). Run total $0.93.

Found live and fixed: at G4 the plan revision got only the Director's summary, and the Game
Designer copied that summary as R15, which is not the human's words. Code flagged it, asked
once more (one extra call) and the problem stayed. Plan revisions now get the human's own
words at the gate as well (`plan_revision_prompt(..., said)`); the same revision called live
afterwards copied R15 word for word from the human with no problems ($0.033).

Bundle after v2: 54 concepts and 24 files; our validator and the conformance test report
nothing. A "level 2: acid corridor with patrolling enemies" context pack is 1971 tokens
(budget 2000) and includes `plan/movement` and `plan/levels/2`.

Full pytest: 333 passed, 4 failed. All four fail the same way on the previous commit with the
uncommitted asset-pipeline changes (a clean worktree baseline): `test_asset_production` (2)
and `test_asset_ui` (that uncommitted change), and
`test_memory_okf::test_search_bm25_budget_staleness_and_retrieval_events` ("Write batch failed
corpus validation").

### Follow-up: a second genre and two harness bugs (2026-11-02)

A run-and-gun game (`workspace/l22-runngun`, "Ironline Commando: Triple Front": 3 levels,
8-way aiming, weapon pods, bosses with weak points; an original game, not a copy of any) tested
research and planning outside the slime game. Research: 8 searches; Contra, Metal Slug,
Gunstar Heroes, Blazing Chrome and Cuphead as references, three of them flagged because their
URL was not among the pages searched. Plan: 16 requirements covered, no problems; gaps up to 4
tiles; 3 levels at difficulty 3, 5, 8. Build v1 about $0.86.

Playing it, the human saw the enemies float above the ground. Cause: the generated code set a
soldier's gravity and then added it to a physics group, which resets the body to the group's
defaults (gravity 0). The human's words went to G4 through the UI's endpoint; the Director and
the Programmer fixed it in v2 (soldiers stand on the ground; the bot now beats all three
bosses). The Programmer's Phaser notes (`skills/builtin/phaser-arcade-physics`) now say this,
for every game; the note was added before that repair, so the repair saw it.

The same run showed two harness bugs, both fixed:

* **False "never finished booting".** `page.wait_for_function` polls by compiling its string
  in the page when the condition is not true at the first check; the build's CSP (no
  `unsafe-eval`) blocks that with an EvalError, which the playtest reported as "no-boot" (and
  then skipped QA). Slow-loading builds (24 entities) hit it, fast ones rarely. It is also a
  likely cause of the QA timing flake seen in L2.1. `record.wait_until` (and
  `qa_runtime.booted`, which must stay standalone) poll with `page.evaluate` instead; the
  playtest, QA, recording and preview use them. The run-and-gun v1 build: no-boot 1 time in 2
  before, 6 times in 6 booted after.
* **The last repair was kept even when it was worse.** In v1 the fourth attempt "did not boot"
  and in v2 it broke the weapon pods that the third attempt had working; both were kept.
  `build_and_test` now ranks every attempt (everything passes, booted, playtest passed, more
  checks, more QA tests, fewer errors, later) and keeps the best: its code, report, QA results
  and screenshots, with its build rebuilt on disk. The kept attempt is marked `kept` in the
  report's attempts.

Tests: `test_game_sdk.py::test_boot_wait_works_under_the_build_csp` (the old wait fails with
the CSP error, the new one boots), `test_codegen_patch.py::
test_a_repair_that_makes_the_game_worse_is_not_kept` (a repair that adds `eval` is not kept;
the build on disk is the kept attempt; the ranking order).

## L2.3: the Level Designer and levels as data

A side-view game whose plan has a jump and levels now plays its levels as data. The Level
Designer writes each level, code proves it can be played with the plan's movement, the SDK
builds and plays it, and the Programmer writes the mechanics once. Top-down games keep the
single-scene flow.

### Levels as data (`game/levels.py`)

`LevelSpec` (strict, Level/Tile Designer, `llm.reasoning.level: high`): the map as rows of
characters (top row first, ' ' or '.' empty) and a legend: `kind` (solid, one_way, hazard,
breakable, crate, pickup, enemy, checkpoint, exit, start), the design `entity` that shows it,
`needs` (the ability that opens or beats it), `opened_by` (touch, dash, never), `holds` (what a
crate drops) and `gives` (the ability a pickup or a crate's drop gives); the plan's challenges
with the column where each starts; the design's check events the level makes happen; the
abilities that replace each other (`exclusive`). One call per level (plus one retry with the
problems code found), each with the plan level, the movement numbers, the entities, the two
levels before it and a 1500-token context pack from the game's bundle.

`fit_level` repairs what code can: ragged rows, characters not in the legend, entity ids close
to the design's ("crates" is `wooden_crate`'s; a checkpoint the design has no art for is drawn
by the SDK), a missing or second start or exit (a door-like prop is placed on the last floor),
things floating in the air (dropped onto the floor below), ability names close to the plan's
("acid" becomes "Acid Form"), events the design does not check.

### Reachability by kinematics (`game/reach.py`)

Code searches the level the way the SDK's player moves: the plan's run speed, gravity 8h/T²,
a take-off speed solved for Arcade's stepping, the dash (over 9 physics steps, gravity off),
60 Hz physics and the bot's inputs changing once per 30 fps playtest frame. The model jumps
2 px lower than the SDK (the SDK peaks 6 px above the plan's height, so a floor exactly at the
planned height can be landed on); a landing needs a quarter tile of the body on the floor.
Hazard tiles are whole cells; an enemy's whole patrol (up to 3 tiles each way along its floor)
is a hazard while it lives; crates cannot be stood on (they dissolve on touch).

* The Level Designer's brief gives the model's own limits for the player's body
  (`gap_limits`: the widest gap a jump crosses, and with a dash), not the plan's whole-tile
  estimate.
* Moves tried between floors: a jump with a constant air speed (from the tile centre or the
  ledge), rise first then move (beside a wall, over spikes or an enemy), a walk off a ledge,
  a jump with a dash at its peak or at take-off. With the slime's numbers (6 tiles/s, 4 tiles
  in 0.6 s, a 4.5-tile dash) gaps up to 3 tiles are crossed (8 with a dash) and ledges up to
  4 tiles climbed, as the plan's kinematics say; one more is refused.
* The search: what the player holds and what is open. Whatever the player can reach, open and
  come back from is opened greedily (crates, enemies it can beat, walls); taking a pickup
  branches (an exclusive ability drops the others). It returns the route the bot plays: walks,
  jumps and falls (take-off, air speed, wait, dash), touch, dash into, collect, exit.
* Checks 1-4 of the plan: every floor is reachable (an unreachable slab is lowered by code when
  that makes it reachable: "the floor at columns 11-14, row 2 was out of reach: lowered 2
  row(s)"); the exit is reachable; a lock before its key says so ("a breakable wall (wall) at
  column 49 blocks the way and needs Metal Form, but no Metal Form can be picked up before
  it"); an enemy that blocks the way needs its ability the same way.

### Logic and challenge checks

* The level's new idea: an ability new in this level must be needed or given by something in
  it, and when a pickup gives it, the exit must depend on it: code solves the level again
  without it ("the exit can be reached without Metal Form, the level's new idea: the player
  gets past what needs it ...", for example a 4-tall cracked wall that a 4-tile jump with a
  dash clears).
* A challenge between checkpoints: every stretch of 6 or more columns between the start, the
  checkpoints and the exit has a gap, a wide gap, a climb, a dash, a hazard or enemy jumped
  over, or a lock on the route.
* Harder than the level before: code's measure (gaps 1, wide gaps +1, climbs 1, dashes 1.5,
  hazards 1, enemies 2, locks 2, plus 0.1 a jump) never drops.
* Every challenge the plan gives the level is placed and in the vocabulary; every floor,
  pickup, crate and checkpoint is reachable.
* A challenge that asks for a dash (its name, test or needs say so) must need one: the route
  dashes somewhere (found live: the plan's "Dash Gap" became a 3-tile gap a plain jump clears).

Problems go back to the Level Designer once; the better answer (solvable first, then fewer
problems) is kept, and what remains is shown at G4 and in the bundle.

### The SDK's level engine (`sdk/sflevel.js`, `sdk/LEVELS.md`)

A build with levels loads `levels.js` (`window.SF_LEVELS`: the movement, what the player holds
from the start, every level with its route) and `sflevel.js` before the game's files. The
game's one scene calls `sf.level.build(this, {touch(thing, how), respawn, checkpoint,
complete})`; the SDK makes a tilemap (solid tiles collide, one-way tiles on top only, hazards
send the player back), a sprite per thing (crates and breakables solid; pickups, checkpoints
and the exit overlap; enemies walk their patrol), the player moved with the plan's numbers
(coyote time, a 0.1 s jump buffer, a variable jump, one air dash, falls capped below a tile per
step), the camera, checkpoints and respawns (pits, hazards), and the flow: `level_start`,
`level_complete`, the next level, `win` after the last, Enter restarts. `?level=n` starts at a
level; `?only=1` stops after it. The game implements what each thing does in `touch()` from
its data and calls `sf.level.open/drop/take/respawn`. In autoplay the SDK's bot follows the
route code found: it walks to each take-off point, presses jump with the planned air speed
(and dash), touches or dashes into things until they open, collects drops, retries a missed
jump and resumes from the checkpoint after a respawn; `window.sf.bot` says which step it is on
and, when it is stuck, why ("the crate t2 (crate) at column 4 did not open when touched (the
game's touch hook)").

The Programmer's contract for such a game (`level_requirements`, `LEVELS.md`, and a summary of
every kind of thing in the levels with its data): no level geometry, controller or autoplay;
mechanics in `touch()` as the data says; the design's events; each level's events. The offline
Programmer returns `mock_platformer.js`, which implements every kind of thing from its data.

### Playtest per level, G4 per level, the bundle

* The bot playtest plays each level on its own (`?autoplay=1&headless=1&god=1&only=1&level=n`,
  20 game seconds plus 6 per route step) until `level_complete`, a stuck bot, an error or the
  time limit. A level passes when it is cleared, emits the game's own events it lists and has
  no errors; the SDK's events (level_start, level_complete, checkpoint, respawn, jump, dash,
  win) and the design's checks are counted over all levels, since whether the bot dashes in a
  level depends on its route. Failures reach the Programmer per level.
* Each build checks the levels again with the player's real body from the art, draws a map
  preview per level with the game's art and the route (`game/vN/levels/level-n.png`), and its
  report lists per level the play link (`index.html?level=n`), the preview, code's check and
  the playtest (cleared, the bot, missing events).
* G4 shows the previews and offers **Level** (The whole game, Level 1, ...). Feedback with a
  level picked goes to the Level Designer for that level only: a new level set
  (`game/levels/vL/`) with that level revised, the same code and art, every level replayed.
  The Director can also split free text into `level_changes`. A design change checks every
  level again with the new design and plan and revises only those with new problems.
* Bundle: `levels/<n>` (type Level) with `resource: /references/levels/<n>.json` (what the SDK
  loads), the preview `/references/levels/<n>.png`, the map and legend, code's check, the
  playtest and a `# Schema` of the level data.

### Tests

`tests/test_levels.py`: jumps reach what the plan says and no further (gaps with and without a
dash, ledges, spikes beside the take-off); a gated level is solved with its keys in order; a
lock before its key says so; a wall the player can jump over is no gate; an unreachable slab is
lowered; a stretch without a challenge, a level easier than the last and a missing new idea
are found; fit repairs; problems go back to the Level Designer once; the offline levels hold
and get harder; level feedback reaches the Director's `level_changes`; the preview; in the
browser, the bot clears a gated level (acid, an enemy, metal, a dash through the wall) and gets
stuck with a reason when the crate mechanic is broken; a gated mock run writes levels after
G2, plays each level, shows them at G4 with the Level form, writes `levels/<n>` into the
bundle, and "Make it longer" on Level 2 changes level 2 only, with the same code.

### Live (2026-11-02)

The slime game (`tools/game_live.py`, the L2.2 brief; G1 from flags with research, G2 approved
by the test operator, G4 left to the human). Three runs; the first two found five problems,
all fixed before the third.

**Found live and fixed:**

* **A dash challenge a jump clears.** The plan's "Dash Gap" became a 3-tile gap; nothing in
  level 1 needed the dash, yet the level listed `dash` as an event, so its playtest would fail
  on something the Programmer cannot fix. Now a challenge that asks for a dash (its name, test
  or needs say so) must need one on the route, and per-level events are the game's own (the
  SDK's are counted over all levels).
* **Gap limits from the model.** The plan's whole-tile `max_gap` understates a jump: from the
  very edge the run-3 slime crossed 4 tiles where the plan said 3, so a "dash gap" of 4 needed
  no dash. The Level Designer's brief and the dash message now give the model's limits for the
  player's body (`reach.gap_limits`: jumps up to 4 tiles, a dash gap 5-9 in run 3).
* **A coin in mid-air read as a landing** (run 2). Arcade sets `body.touching` for overlaps as
  well as collisions, so a jump through a floating coin looked grounded; the bot judged the
  landing wrong and steered back into the pit, levels 2 and 3 stuck after the slime took acid.
  The SDK now counts as ground only tiles below and the top of a crate or wall it collides
  with. Replayed with the same Spark-written game, all three levels cleared.
* **Checkpoints without art** (run 2). The design had no checkpoint entity, so every
  checkpoint was dropped and the `checkpoint` check failed. Code now matches entity ids loosely
  ("crates" is `wooden_crate`'s, a flag-like prop for a checkpoint), and a checkpoint the
  design gave no art keeps working: the SDK draws a pole and a flag.
* **Unreachable-coin false positives.** Coins that mark a jump's arc over a gap were reported
  unreachable; pickups now count as reachable inside the jump envelope of a floor the player
  reaches (floors keep the strict check).
* Also: the design prompt says respawn is never a check (the bot never fails on purpose; run 2
  made it one), the Level Designer sees each design check's minimum count, and the provider's
  read timeout is 420 s (a high-reasoning repair took over 180 s and stopped run 2).

**Run 3** (`workspace/l23-slime3`, "Slime Slides"): plan with 3 levels (dissolve crates, acid
kills, metal breaks walls), movement 6.5 tiles/s, jump 3.25 tiles in 0.6 s, dash 4.5 tiles.
The Level Designer wrote each level twice (every first answer had problems code found: a dash
challenge that needed no dash, unreachable coins); code fitted level 3 (stray characters in its
rows, two crates floating in the air dropped onto the floor). Every level is solvable by code
with the plan's movement, and every floor reachable; difficulty 13.8, 13.8, 32.1.

Build v1: **3/3 levels cleared by the bot** (10.9, 12.2 and 18.3 game seconds), each level's
route with a dash; **all 12 design checks pass** (level_start 3, jump 12, dash 3, checkpoint
35, crate_dissolved 14, coin_collected 39, acid_form 6, acid_kill 3, metal_form 2,
wall_broken 2, level_complete 3, win 1). The first attempt failed one design check
(`wall_broken` 1 of 2: only level 3's wall is needed); the Programmer's repair counted each
wall tile. QA 5 of 6: "checkpoint and time HUD" waits 15 s for a checkpoint without moving
(a test that cannot pass; two more repair rounds chased it, the best attempt was kept). The
previews show the game's art, the cracked-wall gate and the route (the dash in gold). The
Art Director again chose the library "flying eye" for the slime.

Cost: $1.41 (Spark $1.30, Muse Image $0.11). The Level Designer is the new large item: six
calls at `reasoning: high`, 11k-30k output tokens each, $0.55 together. Repairs took $0.38
(two rounds for the QA test above). `llm.reasoning.level: medium` would cut the first; G4
feedback such as "ignore the checkpoint test" drops the second.

**The human's G4 round on run 3** ("the game is to simple and the enemies are not disappearing
after dying they remain in the dying motion"): the Director split it into a design change
("increase difficulty") and a code instruction. The plan revision added challenges to every
level, so all three levels had new problems and were revised (six Level Designer calls,
$0.47); the run then stopped at its $2.00 cap before the Programmer's call. With the human's
consent the cap was raised to $3.00 and the run resumed. Found and fixed:

* **Beaten enemies stayed in their dying motion.** The SDK looped every animation row not on its
  play-once list, so a multi-frame `die` row never completed and `sf.level.open()` never
  removed the enemy. `die`, `break`, `land` and `transform` now play once, `open()` plays the
  row once whatever its repeat says, and a timer removes the sprite regardless
  (`test_a_beaten_enemy_plays_its_death_once_and_is_gone` fails on the old SDK).
* **A resumed G4 step made a stray design version.** The step counted design versions again on
  re-entry, so the resume wrote plan v3 and design v3 instead of reusing v2 (about $0.07; the
  build reports design v3, and the levels written for plan v2 now list challenge names plan v3
  renamed). The version a build answers with is now saved once
  (`game/vN/design-version.json`; `test_a_g4_step_resumed_after_a_failure_reuses_its_design_version`).

Build v2: **3/3 levels cleared by the bot, all design checks pass**; code's difficulty 11.8,
26.7, 64.8 (v1: 13.8, 13.8, 32.1); enemies beaten in levels 2 and 3; three repair rounds (one
level-3 enemy did not die on touch until the Programmer's repair). QA 8 of 10: both failures
are tests that cannot pass on these levels ("checkpoint and time HUD" waits for a checkpoint
without moving; "move left-right" holds Right for 3 s and walks into the first pit, respawning
at the start); real keys move, jump and dash. Run total $2.79.

Not checked live: a G4 feedback round with Level n picked (the per-level revision and replay
are covered by the mock test). The run waits at G4 for the human: play each level from the
report's links (`index.html?level=n`), or pick a level under Level to change it alone.

Full pytest: 351 passed, 4 failed: the same four as in L2.2 (`test_asset_production` (2) and
`test_asset_ui` on the uncommitted asset-pipeline changes, and
`test_memory_okf::test_search_bm25_budget_staleness_and_retrieval_events`).

## L2.4: art and animation design

Things move and react like what they are: the slime hops and squashes instead of sliding,
crates and walls break, beaten enemies are gone, and a transform changes the slime for as long
as it lasts. The Art Director writes the motion, Muse draws it, code checks and repairs it,
and the SDK plays it, so the Programmer improvises none of it.

### The motion design (`game/motion.py`)

Once per design version, after the levels and before the art plan, the Art Director
(`llm.reasoning.motion: medium`) answers an `AnimationBrief` (`game/design/vK-motion.json/.md`)
from the design, the plan's animation list and feel, and what the levels open:

* `rows`: per entity, 1-4 frames each with what the frame shows and whether the subject is off
  the ground (a hop: squashed low and wide; stretched tall; round in the air; squashed wide,
  landing), `plays` (when the game shows it: idle, move, jump, fall, land, dash, hurt, attack,
  break, die, form, pickup, other), fps and loop or once; at most 6 rows per entity.
* `forms`: per form ability, how the player looks in it, a tint and a particle colour.
* `effects`: per feel event, when the SDK plays it (`on`: jump, land, dash, break, kill,
  pickup, transform, hurt, checkpoint, exit, or game), particles, colours, speed, flash, shake
  and the player's squash.

`fit_brief` repairs what code can: entity ids and ability names matched loosely ("the player",
"crates", "acid"), frames trimmed to 4, fps 2-20, jump/land/hurt/break/die rows play once,
break and die rows end with "gone", tiles keep only break rows, a form tint that is no
#RRGGBB comes from its look's colours or material (acid green, metal grey), effect numbers
clamped. It adds what the game needs from a vocabulary: an idle row, a move row for the
player and every enemy (a blob hops), a jump and dash row for a side-view player that has
them, a break or die row for whatever the levels open (crates, breakables, enemies that can be
beaten), a form look and row per form ability, and the default effects for jump, land, dash,
break, kill, pickup, hurt and transform. The offline Art Director (`fallback_brief`) is the
plan's rows, else the design's names, with poses from the vocabulary. A design change keeps
the rows of every entity that still exists (its locked art is not redrawn) and asks only about
new entities (no call when there are none).

### Muse draws the rows; code checks and repairs them

* A Muse sprite is one sheet: a row per motion row, labelled in the prompt ("Row 2 (hop): 5.
  hop: squashed low and wide ...", "Row 4 (acid form, in Acid Form: bright green, glossy,
  dripping acid)"), short rows padded with holds. Form rows may change only colours and
  surface. The request key holds the rows, so unchanged rows stay locked and changed ones are
  edits of the locked image.
* `row_problems` measures each drawn row: a moving row (move, form, dash) whose frames barely
  differ would slide; frames whose area leaves 0.5-2x the row's median changed size; grounded
  frames whose bottom line moves more than a pixel left the ground; a loop whose last-to-first
  change is over 1.5x its median step jumps; a break or die row whose last frame keeps more
  than 10% of the first did not end empty. These go into the one retry (high reasoning
  strength) with the other frame problems; the attempt with fewer problems is kept.
* `finish_rows` repairs what is left: a still moving row becomes squash, rest, stretch, rest
  made from its first frame; a break or die row's last frame is emptied; a form row that came
  back empty or the wrong size is shown by the SDK as a tint with particles. Each sprite keeps
  its checks (`report.json` "motion", the art records) and its reasons list the repairs.
* A library actor is kept only when it can show the rows (a move and a jump animation; never
  a form): run 3's recoloured "flying eye" for a hopping, transforming slime now goes to Muse.
  Actor and object art is cut into the rows (a missing die row is the art crumbled by code).
* A breakable tile stays an image (it tiles) and gains `<id>_break`: the tile crumbled by code
  from its own pixels (cracks, chunks that fall and thin out, then empty).
* `assets.json` gives each sprite's `rows` (plays, fps, loop, form, and the lift of the
  airborne frames of a ground row), `fx` and `forms` (the row that shows each form, or none).

### The SDK plays them (`sf.js`, `sflevel.js`)

* Animations take their fps and loop from the rows.
* `sf.fx(name, x, y, sprite)` plays an effect by name or by its `on`: particles (their own
  random numbers, so effects never change the game's seeded randomness; at most 48 alive),
  flash, camera shake, squash. `sf.fxCount` counts them for tests.
* The level engine draws the player as a skin: `L.player` keeps its body, its animations and
  what the game sets on it; the skin copies its frame, tint, alpha and visibility every frame
  and adds what must not touch the physics body: squash on landing, stretch on a jump, the
  lift of a hop's airborne frames, flashes, a form's tint. It plays the row that fits (move,
  jump, fall, land, dash, idle; the form's row while a form lasts) and the effects at jumps,
  landings, dashes, breaks, kills, pickups, transforms, respawns, checkpoints and the exit.
* `sf.level.take(t)` turns the player into the form a pickup gives; `sf.level.form(ability)`
  (or `''`) does it for the game's own timers; `sf.state.form` and a `form` event follow it.
  `sf.level.open(t)` plays the break or die row (or the tile's `<id>_break`) once and removes
  the sprite. Enemies patrol with their move row.
* `API.md` and `LEVELS.md` tell the Programmer to call `sf.fx` instead of writing particles or
  shakes, and that the SDK plays the rows and the effects.

### Seen by the reviewer and the human

The Art Reviewer gets every animated row as filmstrips (a last image) and code's motion
findings, and judges motion as a fourth task. G4 shows `game/vN/motion.gif` (every row playing
at its fps, once rows ending empty) before the art sheet. The bundle gains `art/motion` (type
Motion Design): the rows, forms and effects, code's checks of the latest build, the GIF and a
`# Schema` of `/references/art/motion.json`; `art/style` links to it. `game make` writes
`motion.json/.md` and `motion.png/.gif` too. `game.motion: false` returns to the design's
animation names.

### Tests

`tests/test_motion.py`: the offline brief gives the slime a hop with an airborne frame, a jump,
a dash and both forms (tints from the materials), the walker a move and a die row, the crate a
break row, the wall only a break row, and every effect; fit repairs (an unknown entity, a
second row, a looping break, a row that does not end gone, a tile's idle row, an ability that
is not the plan's, missing move/jump/dash/form rows, out-of-range effects); a design change
keeps the rows and asks only about the new entity (no call without one); the row checks find a
sliding row, a size change, a frame off the ground line, a loop that jumps and a break that
does not end empty, and code repairs the first and the last; the Muse prompt lists the rows and
the form's look, and the manifest says how to play them (lift, once, forms, effects; the
walker's die row ends empty; the wall's crumble sprite); a row drawn still is drawn again with
the problem, then made to move by code, and the reviewer sees the repair; locked rows are
reused without a call; a library slime that cannot show the forms goes to Muse while a walker
keeps its library frames cut into rows; in the browser the bot clears the gated level while the
SDK plays the hop and both form rows, squashes and lifts the skin, never draws the body, plays
jump, land, dash, break, kill and transform effects and removes everything that opened (the
wall through its crumble); a form without its row is a tint; when two effects share a moment,
each transform plays its own form's and the crates' dissolve stays off the wall; only
abilities that change the look are forms; a gated mock run writes the motion once, shows
`motion.gif` at G4, puts `art/motion` in the bundle, and level feedback keeps it and the art.

### Live (2026-11-02)

The slime game (`tools/game_live.py`, `workspace/l24-slime`, the L2.3 goal word for word;
research on, G2 approved by the test operator, G4 left to the human): "Slimeling: Acid and
Metal Run", 3 levels (dissolve crates, an acid crate before an enemy patrol, a metal crate
before a cracked wall), 9 tiles/s, a 3.5-tile jump, a 4-tile dash.

**The motion design** (one call, $0.041, 8.9k output tokens at medium reasoning): the slime's
idle, a move row "squashed low and wide crouched for hop; stretched tall and narrow springing
up; round compact blob tucked in mid-air; squashed flat and wide landing", a jump row, an acid
form row ("bright dripping green acid blob ... droplets falling") and a metal form row
("silver-grey metal blob ... shiny glint ... small clang lines"); the enemy's idle, walk and die
(squashed flat with X eyes, a purple puddle with acid bubbles, crumbs, gone); the crate's break
(cracking with green bubbles, panels bursting, planks slumped, gone); the cracked wall's break;
coin spin; the exit's idle and open; 11 effects from the plan's feel (dissolve, coin_pickup,
acid_transform, metal_transform, kill, break, hurt, exit, jump, dash; code added land).

**Build v1** ($1.38 for the whole run: Spark $1.28 in 19 calls, Muse Image $0.10 in 10
calls): the slime is drawn by Muse, no longer a recoloured library flying eye; its 6-row sheet
was redrawn once (a frame of one row changed size), and then every row passed code's checks.
Of 17 rows, 16 passed on Muse's drawing; the enemy's die row kept a few crumbs and code emptied
its last frame. The cracked wall got its crumble sprite. **3/3 levels cleared by the bot, all
12 design checks pass** (dissolve 14, coin_collect 24, acid_form 3, acid_kill 4, metal_form 1,
metal_break 1, dash 7 ...). G4 shows `motion.gif` with every row playing. Watched in a
browser (the bot playing each level, frames captured): the slime hops with squash and stretch, takes acid and shows the
acid row for every move (Spark's HUD says "Form: acid"), takes metal and shows the metal row,
the wall crumbles where it stood, crates burst, beaten enemies splat and are gone, and
effects play at jumps, landings, dashes, breaks, kills and pickups. Spark's code calls
`sf.level.form` (and `sf.level.form('')` when its form timer runs out) and writes no particles,
shakes or tweens of its own. The Art Reviewer (with the filmstrips): readability 2,
consistency 3; it fixed the spike (too small to see) and suggested a richer background and an
acid pickup that reads less like a potion bottle.

**Found live and fixed:**

* **An ability with "a form" in its text became a form.** "Dissolve: touch dissolves wooden
  crates to spawn coins or a form" matched code's form test, so code added a Dissolve form row
  (drawn as a sixth row and never shown, since Dissolve is held from the start), and the
  slime's dash row, which the Art Director had written, no longer fitted on its sheet. A form
  is now an ability whose name says form, transform or morph, or whose description says it
  transforms, turns into or becomes something
  (`test_only_abilities_that_change_the_look_are_forms`).
* **Every effect of a moment played.** The Art Director gave acid_transform and
  metal_transform both `on: transform`, and dissolve (crates) and break (walls) both
  `on: break`, so each transform flashed both colours and the wall also played the crates'
  dissolve. The SDK now plays one effect per moment: the one whose name shares a word with what
  happened (the form, the thing's entity and what opens it), else the one named after the
  moment, else the first. Replayed with the same Spark-written game and the fixed SDK: level 3
  plays acid_transform, metal_transform once, dissolve for each crate and break for the wall
  (`test_one_effect_per_moment_the_one_that_fits_what_happened`).

QA 4 of 7: the three failures are tests that wait 15-20 s for an event that fixed key presses
cannot reach (taking acid, taking metal, winning the game); the Programmer's three repair rounds
could not fix them, and the fourth attempt was kept. Not from L2.4, seen live: a checkpoint
shows the exit door's art (the design has no checkpoint entity and code's loose match took the
flag-topped door), level 1 has a stretch without a challenge and level 3 measures a little
easier than level 2 (both reported at G4). Build v1 was made before the two fixes: its slime
sheet carries the Dissolve row and its SDK plays every effect of a moment; a G4 round rebuilds
with the fixed SDK.

Full pytest: 365 passed, 4 failed, the same four as in L2.2 and L2.3 (`test_asset_production`
(2) and `test_asset_ui` on the uncommitted asset-pipeline changes, and
`test_memory_okf::test_search_bm25_budget_staleness_and_retrieval_events`); after the two live
fixes `test_motion` (14), `test_levels`, `test_game_sdk`, `test_qa_tests` and `test_game_make`
pass again (50).
