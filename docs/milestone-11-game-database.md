# Milestone 11.1: the game database (OKF canon for every later level)

## Plan

**Goal.** Before game, menu and level design start, each game gets its own knowledge base:
the story, the characters (with their locked art) and the world, gameplay and art canon, as
plain OKF entries. Every later level or menu design reads a short context pack built from it,
instead of the code, so it stays grounded in the core story.

**Story bible** (`game/story.py`). The design says what the game does; nothing says who the
characters are or what the story is. The Game Designer writes a `StoryBible` right after each
design version (one Spark call, `llm.reasoning.story: high`): title, logline, premise, setting,
tone, themes, characters (each tied to a design entity, exactly one the player: name, role,
look, personality, backstory, goal, abilities, relationships), locations, the arc (3-7 beats;
the first is the current game) and canon (facts no later level may break). It is shown at G2
with the design and revised with it; G4 feedback that changes the design revises it too.
Offline: `fallback_story(design)`.

**Game database** (`game/gamedb.py`): one OKF corpus per game, `runs/<id>/game/db/` (`out/db/`
for `game make`), git-backed like the memory corpus (Markdown + YAML frontmatter, provenance,
trust tiers):

| entry | from |
| --- | --- |
| `story/overview`, `story/arc`, `story/canon` | the story bible |
| `characters/<entity>` + `<entity>.png` (4x first frame), `<entity>-sheet.png` | story + design entity + locked art |
| `world/<location>` | story locations |
| `gameplay/rules` | design: controls, rules, progression, difficulty, win/lose, HUD, checks |
| `art/style`, `art/assets` | design look, measured palette, art sources and locked versions |
| `levels/level-1` | the current game: objective, enemies, pickups, session, build, QA |

Written by code from the artifacts (no extra model calls); an entry is rewritten only when it
changed. Entries start `generated` (unverified); a gate approval promotes everything the human
saw (G2: story, characters, world, gameplay; G4: art and levels), and a later change makes an
entry unverified again until the next approval.

**Context pack** (`context_pack(db, focus, budget)`): compact Markdown for a designer: always
the story overview, canon, arc, the player character, the gameplay rules and the art style;
then the entries the focus names (characters, locations, levels) by retrieval, with image
paths, within the token budget. `spriteforge game db <run or folder> [--focus ...]` prints it.

**UI.** G2 shows the story bible under the design; the G4 report says where the database is.

### Tests (`tests/test_game_db.py`, `tests/test_game_stage.py`)

* the story bible's validation (characters tied to real entities, one player, unique names);
* a mock run: the G2 gate carries the story; design feedback revises it; the database holds
  the story, characters, world, gameplay entries, unverified, then human-reviewed after G2;
* after build v1: character images in the database, linked from their entries; art/assets
  lists the sources; feedback that redraws an entity updates its character entry;
* the context pack: always-included sections, focus retrieval, image paths, the budget;
* every write passes OKF validation and the secret check.

### Acceptance

* A live survival run through G2 and G4: the story bible is shown at G2, the database has the
  story, a character entry with its image for every story character with art, and the
  context pack for a "level 2" focus is under its budget and names the player, the canon and
  the art style without reading code.
* Full pytest passes.

## What was built

* `game/story.py`: `StoryBible` (strict schema), the Game Designer's prompt and revision prompt,
  `fallback_story`, and `fit_story`, which repairs an answer against the design (characters
  only for real entities, one per entity, the player always present, unique names, bounded
  lists) and notes what it changed. `game.story: false` uses the offline story.
* `game/gamedb.py`: the entry builders, `sync` (rewrite only changed entries and images,
  deprecate entries whose source is gone, one git commit), `sync_story` (at G2), `sync_build`
  (after each build: character images, art, level 1), `promote_all` (approval) and
  `context_pack`.
* `memory/okf/store.py`: a corpus can have its own top-level categories, and `write_batch`
  takes binary files (`.png` only, path-checked) committed with the entries that link them.
* Stage: story v1 with design v1; design feedback revises both; G2 shows both; approving G2
  promotes the database; each build syncs it; approving G4 promotes it; a G4 change to the
  design revises the story. The report has `game_db` (path, entries); the G2 page shows the
  story bible; `game make` writes `story.json` and `db/` too.
* `spriteforge game db <folder> --focus "..." --budget N` prints the context pack.
* Found on the way: `design_versions()` counted every `game/design/v*.json` artifact, so
  M10.4's `vK-qa-tests.json` (and now `vK-story.json`) would have been counted as design
  versions and approving G2 would have loaded a design that does not exist. It now counts
  only `vN.json`.

## Live (survival prompt through the gates, 2026-11-01, `workspace/m11-db`)

"Corner Siege: 10 Minute Survival". G2 showed `game/design/v1.json` and
`game/design/v1-story.json`. The story bible (one Spark call, $0.026): Rook, a scavenger locked
in a sealed combat pit (the survivor); Gnasher, Skitter and Mauler (crawler, runner, brute,
the last a boss), each with a personality, goal and backstory consistent with its entity's
look and behaviour; three locations (the locked arena, the corner portals, the void outside);
a 5-beat arc ("Second Lockdown", "Elite Surge", "Portal Fever", "Break the Corners" after this
game); 7 canon facts taken from the design's rules (monsters only from the four portals, 1 gem
each and 3 from brutes, the arena has no exit until the timer ends, it never gets easier).

The build needed 3 repairs, all from QA tests (the idle player could not lose again, and one
repair broke shooting); attempt 4 passed the bot playtest and 6/6 QA tests. 12 Spark + 10
image calls, $0.48.

The database after approving G4: 14 entries, all human-reviewed (story overview, arc and
canon; four characters, each with its 4x portrait and sheet; three locations; gameplay; art
style and assets; level 1). The context pack for "level 2: the next level after the first
night in the arena" was **1924 tokens** (budget 2000): the story, the canon, the arc's first
three beats (beat 2 is the next level), Rook with his image path, the gameplay rules, the art
style, then level 1 and Gnasher by relevance. No code was read.

Acceptance: met. Full pytest: 312 passed; the 3 known asset-production failures remain (out
of scope). One run that overlapped the live build failed `test_qa_regression_across_versions`
once (a QA test on the mock build); it passed alone twice and in the clean full run.
