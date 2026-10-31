# Milestone 9: games written from the prompt (replaces kits)

## Why

Kits picked one of three hand-built games and let Spark change only art and tuning. The prompt
"a top down view game with 10 min survival where different monsters come from the corners …
collect gems after killing monsters and upgrade himself or his weapons … becomes hard" produced
a key-and-door dungeon, because no kit had waves, gems or upgrades. Now **Muse Spark writes the
game's code for each prompt**; deterministic code checks it, plays it and sends failures back.

## M9.1 (done): `spriteforge game make "<prompt>" --out DIR [--mock]`

| Step | Who | Output |
| --- | --- | --- |
| Design | Game Designer (Spark) | `design.json/.md`: entities, rules, progression, difficulty, win/lose, **checks** (events the code must emit) |
| Art | Art Director (Spark) + code | `art-plan.json`, `art.json`, `art.png`, `art-raw/`: library first, otherwise Muse Image |
| Code | Programmer (Spark) | `attempts/N/files/*.js` written against the SDK |
| Static checks | code | no network/eval/modules/URLs/wall clock, known asset ids, size cap, `node --check` |
| Playtest | code | `playtest.json`: the **whole session** simulated with the game's own bot in god mode |
| Repair | Programmer (Spark) | failures go back with the files; up to `game.max_repairs` (3) rounds |
| Record | code | `poster.png` (+ `demo.mp4` when ffmpeg is installed) |

`report.json` summarises who decided, art sources, warnings, attempts and model calls/cost.
Budget caps apply to every call (ledger in `run.sqlite3`).

### SDK (`src/spriteforge/game/sdk/sf.js`, reference `API.md`)

A helper layer, not a game: boots Phaser 3.90 from `sf.start({scenes, autoplay})`, loads the
art manifest (`assets.js`), animations `<id>:<anim>`, `sf.input` (keyboard or the game's bot),
`sf.event` / `sf.counts` (what checks read), `sf.clock`, seeded `Math.random`, HUD text/bars,
a level-up style choice menu (auto-picks under autoplay), god mode. Harness hooks:
`sfCapture` (recorder, frame-exact) and `sfRun` (playtest; logic-only `headlessStep`, so a
10 minute session runs in seconds). Errors inside the game's update become report entries.
Builds also play when `index.html` is opened directly (images load as elements, no fetch).

### Art rule: library first, else Muse Image

* Spark picks per entity: library actor, whole object, tile-set tile, parallax backdrop, or
  `muse`. There is no placeholder choice.
* Code enforces it: an invalid library pick (unknown, other camera, not shippable) becomes a
  Muse request; a Muse request for something the library has **by name** (actor names, object
  and tile names matched on the entity id, not on colour words) uses the library instead.
* Muse prompts come from the design; style references are AI-allowed library assets (actor
  keys resolve to that actor's sheet; `ai_allowed: false` packs are never sent). Output is
  keyed, fitted and snapped to its own 16 colours.
* Greybox only when `game.max_art_calls` (8) is reached or Muse fails; each is a warning.

Tile sets moved from kit skins to `src/spriteforge/library/tilesets/` (camera, tile size,
named tiles, variants, backdrops). Actor grouping moved to `game/actors.py`.

### Live result (survival prompt, 2026-10-30)

First run: Spark's design matched the prompt (600 s, 4 corner portals, slime → bat at 60 s →
skeleton archer at 180 s → ogre at 360 s, gems → XP → 3-choice upgrades, 11 checks). The
first code (586 lines) ran 600 game seconds without errors but its bot never collected gems;
the repair round fixed that; a second round worked around a harness deadline bug (fixed since:
a deadline at the session end now means "any time"). Final: 828 kills, 828 gems, 38 level-ups
and upgrades, all phases on time, win at 599.5 s. 5 Spark + 8 image calls, about $0.35.
That run exposed two art problems, both fixed: Spark sent the mage and the skeleton to Muse
although the library has them (now enforced in code), and palette-locking to an unrelated
reference turned a cyan gem grey (now each sprite keeps its own colours).

### Probe

One live Spark call returned a complete 375-line Phaser game through strict json_schema
(8578 completion tokens, `finish_reason: stop`); recorded in `config.yaml`.

## M9.2 (done): the harness flow, kits removed

`spriteforge start --agents [--goal "<prompt>"]` now runs:

    G1 genre -> brief -> follow-up questions          (genre and brief read from the prompt)
    G2 design gate   game/design/vK.json/.md           text -> design v(K+1); empty -> build
    build vN         art (library first, else Muse), Programmer, static checks, full-session
                     playtest, repairs, recording       -> game/vN/*, runs/<id>/game/vN/build
    G4 playtest gate report, poster/video, art sheet, play link
                     text -> Director splits it (Revision: design / art / code) -> v(N+1)
                     empty -> approval.json, run complete

Feedback only redoes what it is about: a design change makes a new design version (and a new
art plan for new entities); art changes redraw the named entities as Muse *edits* of their
previous image while all other Muse art stays locked (`muse (locked, from vN)`); code
instructions make the Programmer edit the working game, then it is playtested again. Art-only
feedback reuses the same code with the new assets.

| Piece | Where |
| --- | --- |
| Gated stage | `src/spriteforge/stages/game.py` (replaces `stages/kit_preview.py`) |
| Reusable steps | `game/pipeline.py`: `write_design`, `plan_art`, `produce_art`, `write_code`, `build_and_test` |
| Director's split of feedback | `game/revision.py` (`Revision`, offline fallback) |
| Programmer edits from feedback | `codegen.change_prompt` |
| UI | design doc and build report rendering (checks, art sources, attempts, cost, play link) |
| Play route | `/api/runs/<id>/play/vN/...` serves `runs/<id>/game/vN/build` (path-traversal guarded) |
| Catalog / MCP | tile sets replace kits and skins; `library_tiles(camera)` replaces `library_environment(kit)` |

Removed: `src/spriteforge/kits/` (3 kits, runtime, compose, interpret, skins, CLI),
`stages/kit_preview.py`, `stages/description.py`, `tools/build_kit_placeholders.py`, kit tests.
`preview.mode: kit` and `preview.max_art_calls` in saved runs load as `game` (legacy). The image
cap per build is now 16. Report artifacts store per-entity data as lists, because the secret
scrubber blanks dict keys containing "key" (an entity called `key` or `monkey`).

### G1 is no longer a fixed genre list

Before, G1 offered the 11 seeded genres as a closed list (default: dungeon crawler) and a brief
prefilled with dungeon defaults, so the survival prompt had to be squeezed into "Turn-based
roguelike" and Spark had to ask whether to drop its turn and stairs rules. Now:

* **Genre** (`genre-pick.json`): the Game Designer reads the prompt and names its genre in the
  prompt's own terms, with up to 3 seeded genres as references and 2-6 open questions. The
  gate field is free text: the suggestion is the default, seeded titles are chips, and the
  human can type any genre.
* A seeded title or id uses that genre entry. Anything else becomes `custom/<slug>` with a body
  written for the idea (mechanics, adaptive questions, related seeded genres as references
  only). Typing a genre different from the suggestion asks Spark once more to describe it.
* **Brief** (`brief-defaults.json`): every brief field is drafted from the prompt and the genre
  instead of the dungeon defaults; the draft is the first suggestion chip.
* Offline/mock and runs without a prompt use a word-overlap guess against the seeded genres.
  Drop-in genres in `memory/genres/*.md` still appear as suggestions.

Live check (survival prompt): genre "Top-down arena survival" (references: dungeon crawler,
shmup, beat-em-up), brief "10-minute survival run", "WASD to move with auto-attack", "Ramping
spawn rate, variety, health and speed over 10 minutes", four questions about attacks, upgrade
choices, difficulty ramp and the win condition.
