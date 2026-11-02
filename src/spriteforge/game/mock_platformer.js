/* Offline fixture: what the mock Programmer returns for a game with levels as data (mock runs
   and tests). It is written against the SDK exactly like generated code: one scene that calls
   sf.level.build and implements every kind of thing from the level data in touch(): crates and
   breakable blocks open when the player has what they need (with a dash when they ask for
   one), pickups give their ability (exclusive ones replace each other) or a point, enemies are
   beaten when they can be and otherwise send the player back to the checkpoint. */
class Level extends Phaser.Scene {
  constructor() { super('level'); }

  create() {
    const s = sf.assets.scale || 1;
    this.held = new Set(sf.level.data.abilities || []);  // what the plan gives from the start
    this.score = 0;
    this.L = sf.level.build(this, {touch: (t, how) => this.touch(t, how)});
    this.hud = sf.text(this, 4 * s, 4 * s, '');
  }

  can(t, how) {
    return (!t.needs || this.held.has(t.needs)) && (t.opened_by !== 'dash' || how === 'dash');
  }

  touch(t, how) {
    if (t.kind === 'pickup') {
      sf.level.take(t);
      if (t.gives) {
        const exclusive = this.L.data.exclusive || [];
        if (exclusive.includes(t.gives)) exclusive.forEach(a => this.held.delete(a));
        this.held.add(t.gives);
        sf.event('power', {gives: t.gives});
      } else {
        this.score++;
        sf.event(t.entity + '_collect');
      }
    } else if (t.kind === 'crate' && this.can(t, how)) {
      sf.level.open(t);
      sf.level.drop(t);
      sf.event('crate_open');
    } else if (t.kind === 'breakable' && this.can(t, how)) {
      sf.level.open(t);
      sf.event('wall_break');
    } else if (t.kind === 'enemy') {
      if (t.opened_by !== 'never' && this.can(t, how)) {
        sf.level.open(t);
        sf.event('enemy_defeat');
      } else {
        sf.event('hurt');
        sf.level.respawn('enemy');
      }
    }
  }

  update() {
    this.hud.setText('LEVEL ' + this.L.n + '  GEMS ' + this.score);
    Object.assign(sf.state, {hp: 3, score: this.score});
  }
}

sf.start({scenes: [Level]});
