# SpriteForge game SDK (`sf`) for generated games

The build's `index.html` loads, in order: `phaser.min.js` (Phaser 3.90, global `Phaser`),
`sf.js` (global `sf`), `assets.js`, then your files in the order you list them; `game.js`
must be last and call `sf.start(...)`. Write plain browser scripts: no `import`/`export`,
`require`, `fetch`, XMLHttpRequest, WebSocket, `eval`, `new Function`, or any URL.

## Start (required, once, at the end of game.js)
```js
sf.start({
  scenes: [MainScene],             // Phaser.Scene classes; the first one starts the game
  autoplay: (scene, sf) => ({x: 0, y: 0, action: false, action2: false}),
  gravity: 0,                      // arcade gravity y in px/s^2 (0 for top-down)
});
```
Do not create `new Phaser.Game` yourself; `sf.start` does, with pixelArt, arcade physics and the
screen size from `sf.assets.screen`.

## Input (always through sf.input so the test bot can play)
`const inp = sf.input(this)` every frame → `{x, y, action, action2}`; x/y are -1..1 and
already normalised for diagonals. Keyboard: arrows/WASD move, Space/Z/J = action,
X/K/Shift = action2. In autoplay mode the same call returns your `autoplay` policy's answer.
**autoplay must play well enough to exercise every design check**: e.g. move toward pickups,
keep distance from enemies, attack when enemies are close, walk to the exit.

## Time, randomness, state
* `sf.clock.now`: seconds of game time since your first scene started (stepped by the
  harness in tests, so never use `Date.now()` or `performance.now()`). `this.time.delayedCall`
  and `this.time.addEvent` are fine too.
* `Math.random()` and `Phaser.Math.Between` are already seeded.
* `sf.state = {...}`: a small summary you update (hp, level, score, time) for the report.

## Events (how the harness checks the design)
Call `sf.event('name', {optional data})` the moment something happens. Use **exactly** the
event names of the design's checks (e.g. `enemy_spawn`, `gem_collect`, `level_up`), and always
`sf.event('win')` / `sf.event('lose')` once when the session ends. The playtest runs the whole
session (a 10 minute game is simulated for 10 game minutes) and fails any check whose event is
missing, too rare, or too late.

## God mode (tests)
If `sf.flags.god` is true the player still takes hits (emit the hurt event) but HP never drops
below 1, so the bot can reach the end of the session. Never skip any other rule in god mode.

## Art
`sf.assets.screen` → `[w, h]`; `sf.assets.scale` is 1 (16 px art) or 2 (32 px art): multiply
speeds and distances you tune in pixels by it. Use only the ids listed in the assets summary.
* Sprites: `const s = sf.sprite(this, x, y, 'player')` → arcade physics sprite (pass
  `{physics: false}` for a plain sprite). Animations are named `<id>:<anim>`;
  `sf.play(s, 'walk')` plays one if it exists and is a no-op otherwise. Sprites face right:
  `s.setFlipX(true)` when moving left. `s.setDepth(s.y)` sorts top-down scenes.
* Images and tiles: `this.add.image(x, y, 'floor')`,
  `this.add.tileSprite(0, 0, w, h, 'floor').setOrigin(0)` for repeating ground.
* `sf.has('gem')` tells whether an id exists. You may draw extra simple shapes with
  `this.add.rectangle/circle` (projectile trails, flashes), but characters, enemies, pickups and
  tiles must use their asset ids.

## HUD and menus (fixed to the camera)
* `sf.text(this, x, y, 'HP 10', {size: 8, color: '#ffffff', originX: 0})` → Phaser Text.
* `sf.bar(this, x, y, w, h, 0x5ad35a)` → `.set(fraction)` (also `setDepth`, `setVisible`, `destroy`).
* `sf.choose(this, 'LEVEL UP', ['Damage +20%', 'Speed +10%', 'Max HP +2'], i => {...})`:
  a modal menu (keys 1-9, arrows + Enter, click). In autoplay it picks by itself. Pause play
  while it is open with `this.physics.world.pause()` and a flag your update() checks; resume
  in the callback. Never call `this.scene.pause()` (the menu would freeze too).

## Rules of thumb
* Keep entity counts bounded (destroy what leaves the world; cap live enemies ~150).
* Camera: `this.cameras.main.startFollow(player, true, 0.2, 0.2)` for worlds larger than the
  screen; set `this.physics.world.setBounds(...)` to the world size.
* On lose, show a message and let the player restart with `this.scene.restart()`.
* Physics groups: `this.physics.add.group()` then `group.add(sprite)` keeps a velocity set
  before the add (the SDK guards this); input keeps working after `this.scene.restart()`.
* Keep each file under ~600 lines; split helpers into extra files if needed.
