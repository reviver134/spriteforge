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
