## Levels as data: `sf.level` (side-view platformers)

This game's levels are data (`levels.js`, loaded with `sflevel.js` before your files). Do not
draw or place level geometry, do not write a player controller and do not write an autoplay:
the SDK builds each level, moves the player with the game plan's numbers (the same numbers
code proved every level with), and its bot plays each level along the route code found. You
write the mechanics once, for every level, in one scene:

```js
class Level extends Phaser.Scene {
  constructor() { super('level'); }
  create() {
    this.L = sf.level.build(this, {
      touch: (thing, how) => { /* the player touched a thing; how: 'touch' | 'dash' */ },
      respawn: cause => {},     // optional: sent back to the checkpoint ('pit', 'hazard', yours)
      checkpoint: thing => {},  // optional
      complete: n => {},        // optional: level n is finished
    });
    // background images, HUD, your own state
  }
  update() { /* your mechanics; Object.assign(sf.state, {hp, score}) */ }
}
sf.start({scenes: [Level]});
```

`sf.level.build` makes the tilemap (solid, one-way and hazard tiles), a sprite for every thing,
the player (`L.player`), the camera, checkpoints, respawns and the exit. It emits `level_start
{n}`, `jump`, `dash`, `checkpoint`, `respawn {cause}`, `level_complete {n}` and `win` (after
the last level), goes to the next level by itself, and restarts from the first level on Enter
after `win`. It sets `sf.state.level`, `x`, `y` and `time`; add yours with
`Object.assign(sf.state, {...})`.

Things (`L.list`, `L.things[id]`, `L.groups.crate|breakable|pickup|enemy|checkpoint|exit`):
`{id, kind, entity, col, row, needs, opened_by, holds, gives, sprite, open}`. `needs` is the
ability that opens or beats it ('' = none), `opened_by` is 'touch', 'dash' or 'never',
`holds` is the entity a crate drops, `gives` the ability a pickup (or a crate's drop) gives.
Enemies walk their patrol (the SDK moves them). Implement in `touch(thing, how)` what the
design says, following the data (the bot's route relies on it):
* crate: when the player has `needs` (or it is '') and `how` matches `opened_by` ('touch'
  accepts any touch, 'dash' needs how === 'dash'): `sf.level.open(thing)`, then
  `sf.level.drop(thing)` makes what it holds appear (a pickup thing that gives `gives`).
* breakable: the same rule, then `sf.level.open(thing)`.
* pickup: `sf.level.take(thing)` and give `thing.gives` when it has one (abilities in
  `L.data.exclusive` replace each other: taking one drops the others).
* enemy: when it can be beaten by the same rule: `sf.level.open(thing)`; otherwise the
  design's hurt rule (e.g. `sf.level.respawn('enemy')`, or knockback with a short cooldown).

Helpers: `sf.level.open(t)` (plays its break or die row once with the break or kill effect,
then removes it), `sf.level.drop(t)`, `sf.level.take(t)` (the pickup effect; a pickup that
gives a form ability turns the player into it), `sf.level.form(ability)` (the player shows
that form: its row, or a tint with particles; `sf.level.form('')` ends it, e.g. when a timed
form runs out), `sf.level.respawn(cause)`, `sf.level.current`
(= L): `L.player`, `L.dashing`, `L.grounded`, `L.facing`, `L.n`, `L.name`, `L.tile` (pixels per
tile), `L.data` (the level). The SDK plays the player's rows by what they are for (idle, move,
jump, fall, land, dash, the current form's row), squashes on landing, stretches on jumps and
plays the motion design's effects at jumps, landings, dashes, breaks, kills, pickups,
transforms, respawns, checkpoints and the exit: do not add your own. `L.player` is the physics
body; what is drawn copies its frame, tint, alpha and visibility. Set `L.autoAnim = false` only
to play rows yourself. `sf.level.data.abilities` lists
what the player holds at the start of every level (the plan's abilities from the start). Emit the design's own events
with `sf.event(...)` in your hooks (a kill, a pickup, a break).
