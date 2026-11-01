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
