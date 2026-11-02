/* SpriteForge level engine (L2.3): side-view platformer levels as data.

   The build loads it after sf.js when the game has levels (window.SF_LEVELS from levels.js:
   the plan's movement and every level). `sf.level.build(scene, hooks)` builds the current
   level in the game's scene: a tilemap of its solid, one-way and hazard tiles, a sprite per
   thing (crates, breakable blocks, pickups, patrolling enemies, checkpoints, the exit), the
   player moved with the plan's numbers (the same model code checks levels with: game/reach.py),
   the camera, checkpoints and respawns, and the level flow (level_start, level_complete, the
   next level, win). The game's code supplies the mechanics through `hooks.touch(thing, how)`.

   ?level=n starts at level n; ?only=1 stops after it (the playtest plays one level at a time).
   In autoplay the SDK's bot follows the route code found for the level (window.sf.bot says
   where it is). */
(function () {
  'use strict';
  const sf = window.sf;
  const data = window.SF_LEVELS || null;
  const params = new URLSearchParams(location.search);
  const count = data ? data.levels.length : 0;
  const first = Math.max(1, Math.min(count || 1, Number(params.get('level')) || 1));
  const level = (sf.level = {data, count, n: first, first, only: params.has('only'),
                             advancing: false, current: null, build});  // fmt: skip
  sf.bot = null;
  sf.halted = false;
  if (!data) return;

  const KINDS = ['crate', 'breakable', 'pickup', 'enemy', 'checkpoint', 'exit'];
  const SOLID = {crate: true, breakable: true};
  const PATROL_SPEED = 1.5;  // tiles/s

  /* The route bot is the autoplay of a level game (the game does not write one). */
  const start = sf.start;
  sf.start = function (opts) {
    opts = Object.assign({}, opts || {});
    opts.autoplay = botPolicy;
    opts.gravity = data.movement.gravity * 16 * (((window.SF_ASSETS || {}).scale) || 1);
    return start(opts);
  };

  function tileTexture(scene, L) {
    const key = '__sf_tiles_' + L.n, T = L.tile, chars = Object.keys(L.data.tiles);
    if (!scene.textures.exists(key)) {
      const canvas = scene.textures.createCanvas(key, Math.max(1, chars.length) * T, T);
      const ctx = canvas.context;
      ctx.imageSmoothingEnabled = false;
      chars.forEach((ch, i) => {
        const t = L.data.tiles[ch];
        if (sf.has(t.entity)) {
          const tex = scene.textures.get(t.entity), fr = tex.has(0) ? tex.get(0) : tex.get();
          ctx.drawImage(fr.source.image, fr.cutX, fr.cutY, fr.cutWidth, fr.cutHeight, i * T, 0, T, T);
        } else {
          ctx.fillStyle = t.kind === 'hazard' ? '#d04040' : t.kind === 'one_way' ? '#a08060' : '#607080';
          ctx.fillRect(i * T, t.kind === 'one_way' ? 0 : 0, T, t.kind === 'one_way' ? T / 3 : T);
        }
      });
      canvas.refresh();
    }
    return {key, index: Object.fromEntries(chars.map((ch, i) => [ch, i]))};
  }

  function build(scene, hooks) {
    hooks = hooks || {};
    const d = data.levels[level.n - 1], mv = data.movement;
    const s = sf.assets.scale || 1, T = 16 * s, W = d.cols * T, H = d.rows * T;
    const world = scene.physics.world;
    const L = {n: level.n, name: d.name, data: d, tile: T, width: W, height: H, hooks,
               things: {}, list: [], groups: {}, checkpoint: null, done: false, over: false,
               facing: 1, grounded: false, dashing: false, autoAnim: true, t0: sf.clock.now};  // fmt: skip
    level.current = L;
    world.gravity.y = mv.gravity * T;
    world.TILE_BIAS = T;
    world.OVERLAP_BIAS = Math.max(4, T / 4);
    world.setBounds(0, 0, W, H);
    world.setBoundsCollision(true, true, false, false);

    // Tiles: one tilemap layer; one-way tiles collide on top only; hazards do not collide.
    const tex = tileTexture(scene, L);
    const grid = d.map.map(row => Array.from(row, ch => (ch in tex.index ? tex.index[ch] : -1)));
    const map = scene.make.tilemap({data: grid, tileWidth: T, tileHeight: T});
    const tileset = map.addTilesetImage(tex.key, tex.key, T, T, 0, 0);
    const layer = (L.layer = map.createLayer(0, tileset, 0, 0));
    const kindOf = {};
    for (const [ch, t] of Object.entries(d.tiles)) kindOf[tex.index[ch]] = t.kind;
    layer.setCollision(Object.keys(kindOf).map(Number).filter(i => kindOf[i] === 'solid'));
    layer.forEachTile(t => { if (kindOf[t.index] === 'one_way') t.setCollision(false, false, true, false); });
    layer.setDepth(-10);
    L.hazardAt = (x, y, w, h) => layer.getTilesWithinWorldXY(x, y, w, h).some(t => kindOf[t.index] === 'hazard');

    // Things.
    for (const k of KINDS) L.groups[k] = scene.physics.add.group();
    for (const td of d.things) {
      if (td.kind === 'start') continue;
      const t = Object.assign({}, td, {sprites: [], open: false, taken: false, drop: null, touched: -1});
      for (const [c, r] of td.cells) t.sprites.push(place(scene, L, t, c, r));
      t.sprite = t.sprites[0];
      t.x = t.sprite.x; t.y = t.sprite.y;
      if (t.kind === 'enemy') t.dir = 1;
      L.things[t.id] = t;
      L.list.push(t);
    }

    // The player, feet on the start cell's floor.
    const p = (L.player = sf.sprite(scene, (d.start[0] + 0.5) * T, (d.start[1] + 0.5) * T, d.player));
    p.body.setMaxVelocity(T * 100, mv.max_fall * T);
    p.setCollideWorldBounds(true);
    p.body.reset(p.x, (d.start[1] + 1) * T - (p.body.bottom - p.y));
    p.setDepth(10);
    L.spawnAt = [p.x, p.y];
    scene.physics.add.collider(p, layer);
    scene.physics.add.collider(L.groups.enemy, layer);
    const touch = (_, o) => touched(L, o.getData('thing'));
    scene.physics.add.collider(p, L.groups.crate, touch);
    scene.physics.add.collider(p, L.groups.breakable, touch);
    scene.physics.add.overlap(p, L.groups.pickup, touch);
    scene.physics.add.overlap(p, L.groups.enemy, touch);
    scene.physics.add.overlap(p, L.groups.checkpoint, (_, o) => checkpoint(L, o.getData('thing')));
    scene.physics.add.overlap(p, L.groups.exit, () => complete(scene, L));

    const cam = scene.cameras.main;
    cam.setBounds(0, 0, W, H);
    cam.startFollow(p, true, 0.2, 0.2);

    // The controller: the plan's numbers (tiles) at the tile size; the jump peaks at mv.jump
    // under Arcade's stepping (as game/reach.py assumes).
    const dt = 1 / (world.fps || 60), g = mv.gravity;
    const v0 = (g * dt / 2 + Math.sqrt((g * dt / 2) ** 2 + 2 * g * mv.jump)) * T;
    const dashSpeed = mv.dash > 0 ? mv.dash / (mv.dash_steps * dt) * T : 0;
    const c = (L.control = {lastGround: -1, buffer: -1, rising: false, dashSteps: 0, dashDir: 1,
                            dashReady: 0, airDash: true, prevA: false, prevB: false, inputX: 0,
                            safeUntil: 0});  // fmt: skip
    world.on('worldstep', onStep);
    scene.events.once('shutdown', () => world.off('worldstep', onStep));
    function onStep() {
      if (c.dashSteps > 0 && p.body) {
        c.dashSteps--;
        if (c.dashSteps === 0) {
          p.body.setAllowGravity(true);
          p.body.setVelocityX(c.inputX * mv.speed * T);
          L.dashing = false;
        } else p.body.setVelocity(c.dashDir * dashSpeed, 0);
      }
    }
    // Scene listeners outlive scene.restart(): remove them when this level shuts down.
    const onUpdate = () => control(scene, L, mv, v0, dashSpeed);
    scene.events.on('update', onUpdate);
    scene.events.once('shutdown', () => {
      scene.events.off('update', onUpdate);
      if (level.current === L) level.current = null;
    });

    const label = sf.text(scene, sf.screen()[0] / 2, 30 * s, 'Level ' + L.n + ': ' + L.name,
                          {originX: 0.5, color: '#f4d35e'});  // fmt: skip
    scene.time.delayedCall(1800, () => label.destroy());
    sf.state.level = L.n;
    sf.bot = {level: L.n, step: 0, steps: d.route.length, status: 'playing', detail: ''};
    L.bot = {i: 0, phase: 'start', t0: 0, retries: 0, resume: 0, frame: -1, out: null, dashed: false,
             stepStart: sf.clock.now, last: -1};  // fmt: skip
    sf.event('level_start', {n: L.n});
    return L;
  }

  function place(scene, L, t, c, r) {
    const T = L.tile;
    const x = (c + 0.5) * T;
    const o = sf.sprite(scene, x, (r + 0.5) * T, t.entity);
    o.setData('thing', t);
    L.groups[t.kind].add(o);
    const b = o.body;
    if (t.kind === 'enemy') {
      b.setAllowGravity(true);
      o.y = (r + 1) * T - (b.bottom - o.y) - 1;
      sf.play(o, 'walk');
      return o;
    }
    b.setAllowGravity(false);
    b.setImmovable(true);
    if (SOLID[t.kind] || t.kind === 'pickup') b.setSize(T / o.scaleX, T / o.scaleY, true);
    if (t.kind !== 'pickup' && !SOLID[t.kind]) o.y = (r + 1) * T - o.displayHeight / 2;  // stands on the floor
    o.setDepth(t.kind === 'exit' || t.kind === 'checkpoint' ? 5 : 6);
    return o;
  }

  function touched(L, t) {
    if (!t || t.open || L.done) return;
    const now = sf.clock.now;
    if (now - t.touched < 0.2) return;
    t.touched = now;
    const how = L.dashing ? 'dash' : 'touch';
    if (L.hooks.touch) {
      try { L.hooks.touch(t, how); } catch (e) { sf.errors.push('touch hook: ' + e.message); }
    } else if (t.kind === 'pickup') L.take(t);
  }

  function checkpoint(L, t) {
    if (!t || L.checkpoint === t) return;
    L.checkpoint = t;
    L.bot.resume = L.bot.i;
    sf.play(t.sprite, 'active');
    sf.event('checkpoint', {n: L.n, id: t.id});
    if (L.hooks.checkpoint) L.hooks.checkpoint(t);
  }

  function complete(scene, L) {
    if (L.done) return;
    L.done = true;
    L.player.body.setVelocity(0, 0);
    sf.event('level_complete', {n: L.n});
    if (L.hooks.complete) L.hooks.complete(L.n);
    const [w, h] = sf.screen();
    const last = L.n >= count;
    sf.text(scene, w / 2, h / 2, last ? 'YOU WIN!' : 'LEVEL CLEAR', {originX: 0.5, size: 12});
    if (last) {
      L.over = true;
      sf.event('win');
    } else if (level.only) {
      sf.halted = true;
    } else {
      scene.time.delayedCall(1000, () => { level.n = L.n + 1; level.advancing = true; scene.scene.restart(); });
    }
    if (last || level.only) {
      const enter = scene.input.keyboard && scene.input.keyboard.addKey('ENTER');
      if (enter) enter.once('down', () => { level.n = level.first; sf.halted = false; scene.scene.restart(); });
    }
  }

  /* The game's code calls these. */
  const api = {
    open(t) {  // a crate dissolved, a wall broken, an enemy beaten
      if (!t || t.open) return;
      t.open = true;
      for (const o of t.sprites) {
        if (o.body) o.body.enable = false;
        const key = o.texture.key, scene = o.scene;
        const anim = ['break', 'die', 'death', 'open'].find(a => scene && scene.anims.exists(key + ':' + a));
        if (anim) { sf.play(o, anim); o.once('animationcomplete', () => o.destroy()); } else o.destroy();
      }
    },
    drop(t) {  // what a crate holds appears where it was
      if (!t || !t.holds || t.drop) return t ? t.drop : null;
      const L = level.current, T = L.tile;
      const dt = {id: t.id + '>', kind: 'pickup', entity: t.holds, col: t.col, row: t.row,
                  cells: [[t.col, t.row]], needs: '', opened_by: 'never', holds: '', gives: t.gives};  // fmt: skip
      const d = Object.assign(dt, {sprites: [], open: false, taken: false, drop: null, touched: -1});
      const o = place(L.player.scene, L, d, t.col, t.row);
      d.sprites.push(o); d.sprite = o; d.x = o.x; d.y = o.y;
      L.things[d.id] = d; L.list.push(d);
      t.drop = d;
      return d;
    },
    take(t) {  // a pickup collected
      if (!t || t.taken) return;
      t.taken = true;
      t.open = true;
      for (const o of t.sprites) o.destroy();
    },
    respawn(cause) {  // back to the last checkpoint (or the start)
      const L = level.current;
      if (!L || L.done) return;
      const p = L.player, T = L.tile, c = L.control;
      const at = L.checkpoint ? [(L.checkpoint.col + 0.5) * T, (L.checkpoint.row + 1) * T] : null;
      c.dashSteps = 0; L.dashing = false; p.body.setAllowGravity(true);
      if (at) p.body.reset(at[0], at[1] - (p.body.bottom - p.y));
      else p.body.reset(L.spawnAt[0], L.spawnAt[1]);
      p.body.setVelocity(0, 0);
      c.safeUntil = sf.clock.now + 0.3;
      L.bot.respawned = true;
      sf.event('respawn', {n: L.n, cause: String(cause || '')});
      if (L.hooks.respawn) L.hooks.respawn(cause);
    },
  };
  for (const [name, fn] of Object.entries(api)) level[name] = fn;
  level.thing = id => level.current && level.current.things[id];

  function control(scene, L, mv, v0, dashSpeed) {
    const p = L.player, b = p.body;
    if (!b || L.done) return;
    const T = L.tile, c = L.control, now = sf.clock.now;
    const inp = sf.input(scene);
    const grounded = (L.grounded = b.blocked.down || b.touching.down);
    if (grounded) { c.lastGround = now; c.airDash = true; }
    c.inputX = inp.x;
    if (!c.dashSteps) b.setVelocityX(inp.x * mv.speed * T);
    if (inp.x) L.facing = Math.sign(inp.x);
    const jump = inp.action || inp.y < -0.5;  // Space (or up) jumps; X/Shift dashes
    if (jump && !c.prevA) c.buffer = now;
    if (c.buffer >= 0 && now - c.buffer <= 0.1 && c.lastGround >= 0 && now - c.lastGround <= Math.max(mv.coyote, 0.001) + 1e-6 && !c.dashSteps) {
      b.setVelocityY(-v0);
      c.buffer = -1; c.lastGround = -1; c.rising = true;
      sf.event('jump');
    }
    if (c.rising && !jump && b.velocity.y < 0) { b.setVelocityY(b.velocity.y * 0.5); c.rising = false; }
    if (b.velocity.y >= 0) c.rising = false;
    if (inp.action2 && !c.prevB && dashSpeed > 0 && now >= c.dashReady && (grounded || c.airDash) && !c.dashSteps) {
      c.dashSteps = mv.dash_steps;
      c.dashDir = inp.x ? Math.sign(inp.x) : L.facing;
      c.dashReady = now + mv.cooldown;
      if (!grounded) c.airDash = false;
      c.rising = false;
      b.setAllowGravity(false);
      b.setVelocity(c.dashDir * dashSpeed, 0);
      L.dashing = true;
      sf.event('dash');
    }
    c.prevA = jump; c.prevB = inp.action2;
    if (L.autoAnim) {
      const moving = Math.abs(b.velocity.x) > 1;
      const anim = L.dashing ? 'dash' : !grounded ? 'jump'
        : moving ? ['walk', 'move', 'run'].find(a => scene.anims.exists(p.texture.key + ':' + a)) || 'walk' : 'idle';
      sf.play(p, anim);
      p.setFlipX(L.facing < 0);
    }
    // Pits and hazards send the player back to the last checkpoint.
    if (now >= c.safeUntil) {
      if (b.top > L.height + T) level.respawn('pit');
      else if (L.hazardAt(b.x + 2, b.y + 2, b.width - 4, b.height - 4)) level.respawn('hazard');
    }
    for (const t of L.list) {  // enemies walk their patrol
      if (t.kind !== 'enemy' || t.open || !t.sprite.body || !t.patrol) continue;
      const o = t.sprite, lo = (t.patrol[0] + 0.5) * T, hi = (t.patrol[1] + 0.5) * T;
      if (o.x <= lo) t.dir = 1;
      if (o.x >= hi) t.dir = -1;
      o.body.setVelocityX(t.dir * (t.speed || PATROL_SPEED) * T);
      o.setFlipX(t.dir < 0);
      t.x = o.x; t.y = o.y;
    }
    sf.state.level = L.n;
    sf.state.x = Math.round(p.x);
    sf.state.y = Math.round(p.y);
    sf.state.time = +(now - L.t0).toFixed(2);
  }

  /* ---------- the route bot ------------------------------------------------------------------ */
  function botPolicy(scene) {
    const L = level.current;
    if (!L || !L.player || !L.player.body) return {};
    const B = L.bot, now = sf.clock.now;
    if (B.frame === now && B.out) return B.out;  // one decision per frame, however often asked
    B.frame = now;
    B.out = decide(L, B, now) || {};
    return B.out;
  }

  function stuck(B, detail) {
    B.status = 'stuck';
    sf.bot.status = 'stuck';
    sf.bot.detail = detail;
    return {};
  }

  function decide(L, B, now) {
    const d = L.data, mv = data.movement, T = L.tile, p = L.player, b = p.body;
    if (L.done || B.status === 'stuck') return {};
    if (B.respawned) {
      B.respawned = false;
      B.retries++;
      if (B.retries > 3) return stuck(B, 'sent back to a checkpoint more than 3 times');
      B.i = B.resume; B.phase = 'start'; B.stepStart = now;
    }
    if (B.i >= d.route.length) return stuck(B, 'the route ended before the exit');
    const st = d.route[B.i];
    sf.bot.step = B.i;
    const x = b.center.x / T, feet = b.bottom / T, grounded = L.grounded;
    const frame = B.last >= 0 && now > B.last ? now - B.last : 1 / 30;  // seconds per frame
    B.last = now;
    const fps = 1 / frame;
    const steer = tx => Math.max(-1, Math.min(1, (tx - x) * fps / mv.speed));
    const next = () => { B.i++; B.phase = 'start'; B.stepStart = now; B.dashed = false; return null; };
    if (B.phase === 'start') { B.phase = 'approach'; B.stepStart = now; }
    const age = now - B.stepStart;
    if (age > 10) return stuck(B, 'step ' + B.i + ' (' + st.do + (st.thing ? ' ' + st.thing : '') + ') took over 10 s at x=' + x.toFixed(2) + ', feet=' + feet.toFixed(2));
    if (st.do === 'walk') {
      if (Math.abs(st.x - x) < 0.06 && grounded) { next(); return {x: 0}; }
      return {x: steer(st.x)};
    }
    if (st.do === 'jump' || st.do === 'fall') {
      const w = data.body[0];
      if (B.phase === 'approach') {
        const ax = st.do === 'fall' ? st.x0 - st.side * (w + 0.04) : st.x0;
        if (Math.abs(ax - x) < 0.05 && grounded && Math.abs(b.velocity.x) < T * 0.5 * mv.speed + 1) {
          B.phase = 'air'; B.t0 = now; B.left = false;
        } else return {x: steer(ax)};
      }
      if (B.phase === 'air') {
        const t = now - B.t0;
        if (st.do === 'jump' && t > 0.1 && grounded || st.do === 'fall' && B.left && grounded) {
          const want = st.cell[1];
          if (Math.abs((feet - 1) - want) < 0.3) { next(); return {x: 0}; }
          B.retries++;
          if (B.retries > 3) return stuck(B, 'step ' + B.i + ' (' + st.do + ') landed at x=' + x.toFixed(2) + ', row ' + Math.round(feet - 1) + ' instead of row ' + want);
          B.phase = 'approach';
          return {x: 0};
        }
        if (!grounded) B.left = true;
        let vx = t < st.wait - 1e-6 ? 0 : st.vx;
        let dash = false;
        if (st.dash >= 0 && !B.dashed && t >= st.dash - 1e-6) { B.dashed = true; dash = true; }
        if (B.dashed && !dash) vx = st.after;
        return {x: vx / mv.speed, action: st.do === 'jump', action2: dash};
      }
    }
    if (st.do === 'touch' || st.do === 'dash') {
      const t = L.things[st.thing];
      if (!t || t.open) return next() || {x: 0};
      if (B.phase === 'approach') {
        if (Math.abs(st.x - x) < 0.08 && grounded) { B.phase = 'push'; B.t0 = now; B.lastDash = -9; }
        else return {x: steer(st.x)};
      }
      const tx = (t.sprite && t.sprite.active ? t.sprite.x : (t.col + 0.5) * T) / T;
      const side = Math.sign(tx - x) || 1;
      if (now - B.t0 > 6) return stuck(B, 'the ' + t.kind + ' ' + t.id + ' (' + t.entity + ') at column ' + t.col + ' did not open when touched' + (st.do === 'dash' ? ' with a dash' : '') + ' (the game\'s touch hook)');
      if (st.do === 'dash' && now - B.lastDash > mv.cooldown + 0.1 && now >= L.control.dashReady) {
        B.lastDash = now;
        return {x: side, action2: true};
      }
      return {x: side};
    }
    if (st.do === 'collect') {
      const t = L.things[st.thing], target = st.drop ? t && t.drop : t;
      if (!target || target.taken || target.open) {
        if (Math.abs(st.x - x) < 0.1 || age > 2) return next() || {x: 0};
        return {x: steer(st.x)};
      }
      if (age > 4) return next() || {x: 0};  // not collected: the game's touch hook decides
      const tx = (target.sprite && target.sprite.active ? target.sprite.x : (target.col + 0.5) * T) / T;
      return {x: steer(tx)};
    }
    if (st.do === 'exit') return {x: steer(st.x + 0.3)};
    return next() || {};
  }
})();
