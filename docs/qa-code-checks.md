# L2.8: QA code checks: the QA Playtester writes its own checks for each game

## Why

The user asked for many kinds of games. The harness's own testing has only fixed tools: the
bot playtest (for levels as data, a route bot that beats things by touching them) and QA tests
made of fixed step kinds (hold keys, wait, expect an event or a state value). A run-and-gun game
(live 2026-11-03, `workspace/l27-runngun`) showed the limit: the bot could not shoot, so the
Programmer added code that destroyed the nearest enemy whenever the player fired (hit or not)
to get the bot through; the fixed QA steps could not see it, and the player found enemies dying
by themselves and kills landing far beyond the bullets. No fixed check can know what every game
must do, so the QA Playtester writes checks for each game, as code.

## What happens

1. After the Programmer's first answer that passes static checks, the QA Playtester (one Muse
   Spark call per build) gets the design (the truth) and the code (where things live) and writes
   3-8 checks of this game's own rules (`qa_checks.CodeCheck`: name, purpose, god, start, level,
   `code`, a JavaScript function body).
2. Code checks every check before it runs (`check_problems`): `node --check`, at most 4000
   characters, and no network, eval, storage, timers or async code, page navigation, changes to
   the SDK, or faking outcomes (`sf.event`, `sf.level.open/take/drop/form/respawn`). A check that
   breaks a rule is left out, with a note.
3. Each check runs in a fresh page of the build (`qa_runtime.run_check`) with one helper, `qa`:
   `step`, `frames`, `hold`, `press`, `down`, `up` (key events the game reads like real keys),
   `scene`, `sprites(texture)`, `camera`, `onScreen`, `state`, `count`, `events`, `errors`,
   `level`, `expect(ok, message)`, `log`. The browser page is the sandbox (the build's CSP
   allows no network); a check may step at most 90 game seconds, and the browser stops one that
   runs past 60 wall-clock seconds.
4. A failed `qa.expect` (or a runtime error in the game during the check) fails the game: the
   Programmer gets the check, its message and its code in the repair prompt. Any other exception
   is the check's own mistake ("broken"): the QA Playtester gets the errors once to fix the
   checks; one still broken is shown at G4 and never counted against the game.
5. Checks run on every repair attempt and in the art review's replay, next to the QA tests; the
   attempt ranking treats a broken check as neither passed nor failed.
6. They are saved per build (`game/vN/qa-checks.json`; `qa-checks.json` for `game make`), shown
   at G4 ("[code check]", with the code), and exported in `build/tests/test_game_qa.py`, where
   the JavaScript stays data in Python and runs only in the browser.

`game.qa_checks: false` turns them off.

## Tests

`tests/test_qa_checks.py`: forbidden calls and syntax errors stop a check before it runs; in
the game a check passes, fails (assertion), is broken (its own error), is stopped by the browser
(an endless loop) or runs past its game time; a game that beats an enemy from afar when the
player fires (the live bug) fails a QA-written check and the same game without the cheat passes
it; in `build_and_test` the checks are written from the code, a broken one is fixed once, a
forbidden one is left out, and a failed one reaches the Programmer; the export.

## Not done

* Checks are written per build, not kept as a regression suite across versions (the code they
  read changes).
* Not run live with Muse Spark yet.
