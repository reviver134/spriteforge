## Menus and flow: `sf.flow` (the SDK's screens around your game)

This game's menus are data (`menus.js`, loaded with `sfflow.js` before your files). The SDK
draws and runs every screen around play: the title, the main menu (start, continue, level
select, settings, controls, credits), the level intro, pause (Escape), results between levels,
game over and victory, and saves progress in the browser. Do not write a title scene, menus, a
pause key, a level-select or a game-over screen of your own: your first scene starts straight
in play, and the SDK starts it (from the title, or at once for the bot and `?play=1`).

* Escape belongs to the SDK: it pauses your scene (update, physics, timers and tweens stop,
  `sf.clock.now` stands still) and resumes it. Do not bind Escape or P.
* Keep emitting `win` and `lose` once when a run ends and showing a short message; about a
  second later the SDK opens victory or game over over your paused scene (Retry restarts your
  scene with `restart`; Enter after a win or loss keeps working as before).
* Difficulty: multiply enemy speed, damage and spawn rate by `sf.settings.multiplier`
  (easy 0.75, normal 1, hard 1.35), read when your scene starts. `sf.settings.fewerFlashes`
  is handled by `sf.fx` (no flash or shake); do not flash the screen yourself.
* Your scene may be stopped (Quit to title) and started again (Start, Continue, Retry): set up
  everything in `create()` and keep no state in globals that a new run would not reset.
