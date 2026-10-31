/* Offline fixture: what the mock Programmer returns for `fallback_design` (mock runs and tests).
   A small arena game written against the SDK exactly like generated code: assets by id,
   sf.input for controls (so the bot can play), sf.event for every design check. */
class Arena extends Phaser.Scene {
  constructor() { super('arena'); }

  create() {
    const [w, h] = sf.screen(), s = sf.assets.scale || 1;
    this.s = s;
    this.add.tileSprite(0, 0, w, h, 'floor').setOrigin(0).setDepth(-1000);
    this.hero = sf.sprite(this, w / 2, h / 2, 'hero');
    this.hero.setCollideWorldBounds(true);
    this.enemies = this.physics.add.group();
    this.gems = this.physics.add.group();
    this.hp = 5; this.gemCount = 0; this.over = false;
    this.nextSpawn = 0.5; this.spawnEvery = 2.0; this.lastAttack = -1;
    this.hpText = sf.text(this, 4 * s, 4 * s, '');
    this.timeText = sf.text(this, w - 4 * s, 4 * s, '', {originX: 1});
    this.physics.add.overlap(this.hero, this.gems, (hero, gem) => {
      gem.destroy(); this.gemCount++; sf.event('gem_collect');
    });
    this.physics.add.overlap(this.hero, this.enemies, (hero, enemy) => this.hurt(enemy));
  }

  hurt(enemy) {
    if (enemy.getData('cool') > sf.clock.now) return;
    enemy.setData('cool', sf.clock.now + 1);
    this.hp = sf.flags.god ? Math.max(1, this.hp - 1) : this.hp - 1;
    sf.event('player_hurt');
    if (this.hp <= 0) this.finish(false);
  }

  spawn() {
    const [w, h] = sf.screen(), side = Math.floor(Math.random() * 4);
    const x = side === 0 ? 0 : side === 1 ? w : Math.random() * w;
    const y = side === 2 ? 0 : side === 3 ? h : Math.random() * h;
    const e = sf.sprite(this, x, y, 'slime');
    this.enemies.add(e);
    sf.event('enemy_spawn', {side});
  }

  finish(won) {
    if (this.over) return;
    this.over = true;
    this.physics.world.pause();
    sf.event(won ? 'win' : 'lose');
    const [w, h] = sf.screen();
    sf.text(this, w / 2, h / 2, won ? 'YOU SURVIVED' : 'GAME OVER', {originX: 0.5, size: 12});
  }

  update() {
    if (this.over) return;
    const now = sf.clock.now, s = this.s, inp = sf.input(this);
    this.hero.setVelocity(inp.x * 70 * s, inp.y * 70 * s);
    if (inp.x) this.hero.setFlipX(inp.x < 0);
    sf.play(this.hero, inp.x || inp.y ? 'walk' : 'idle');
    if (now >= this.nextSpawn) {
      this.spawn();
      this.spawnEvery = Math.max(0.6, 2.0 - Math.floor(now / 15) * 0.4);
      this.nextSpawn = now + this.spawnEvery;
    }
    for (const e of this.enemies.getChildren()) {
      this.physics.moveToObject(e, this.hero, 30 * s);
      e.setFlipX(e.x > this.hero.x);
    }
    if (inp.action && now - this.lastAttack > 0.5) {
      this.lastAttack = now;
      for (const e of [...this.enemies.getChildren()]) {
        if (Phaser.Math.Distance.Between(e.x, e.y, this.hero.x, this.hero.y) < 28 * s) {
          this.gems.add(sf.sprite(this, e.x, e.y, 'gem'));
          e.destroy();
          sf.event('enemy_defeat');
        }
      }
    }
    const left = Math.max(0, 45 - now);
    this.hpText.setText('HP ' + this.hp + '  GEMS ' + this.gemCount);
    this.timeText.setText(left.toFixed(0) + 's');
    sf.state = {hp: this.hp, gems: this.gemCount, time: +now.toFixed(1)};
    if (left <= 0) this.finish(true);
  }
}

// Bot: grab gems, otherwise drift away from the nearest enemy; attack when one is close.
function autoplay(scene) {
  const hero = scene.hero;
  if (!hero) return {};
  const near = (group) => group.getChildren().reduce((best, o) => {
    const d = Phaser.Math.Distance.Between(o.x, o.y, hero.x, hero.y);
    return !best || d < best.d ? {o, d} : best;
  }, null);
  const enemy = near(scene.enemies), gem = near(scene.gems);
  const s = scene.s || 1;
  const attack = !!enemy && enemy.d < 26 * s;
  if (gem && (!enemy || enemy.d > 20 * s)) return {x: gem.o.x - hero.x, y: gem.o.y - hero.y, action: attack};
  if (enemy && enemy.d < 60 * s) return {x: enemy.o.x - hero.x, y: enemy.o.y - hero.y, action: attack};
  const [w, h] = sf.screen();
  return {x: w / 2 - hero.x, y: h / 2 - hero.y, action: attack};
}

sf.start({scenes: [Arena], autoplay});
