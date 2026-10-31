/* SpriteForge game SDK: generic helpers that generated Phaser 3 games are written against.
   It is not a game: it boots Phaser, loads the build's art, reads input (keyboard or the game's
   own autoplay bot), and exposes the hooks the harness uses to test and record a build:
   window.sf.{ready, errors, events, counts, state}, window.sfCapture(frames, fps) and
   window.sfRun(seconds, fps). The model-facing reference is API.md. */
(function () {
  'use strict';
  const params = new URLSearchParams(location.search);
  const flags = {
    autoplay: params.has('autoplay') || params.has('demo'),
    capture: params.has('capture'),   // recorder: frozen loop, stepped frame by frame
    headless: params.has('headless'), // playtest: logic-only steps (no drawing), as fast as possible
    god: params.has('god'),           // playtest: the player may be hurt but must not die
  };
  const sf = (window.sf = {
    ready: false, errors: [], events: [], counts: {}, state: {}, flags,
    assets: {screen: [320, 180], scale: 1, background: '#0b0c14', sprites: {}, images: {}},
    game: null, clock: {now: 0},
  });
  const MAX_EVENTS = 400, MAX_TIMES = 300, ONCE = new Set(['attack', 'death', 'hurt', 'hit', 'jump']);

  // Every script error, rejected promise and console.error reaches the playtest report.
  window.addEventListener('error', e => sf.errors.push(String(e.message || e)));
  window.addEventListener('unhandledrejection', e => sf.errors.push('Unhandled: ' + String(e.reason)));
  const consoleError = console.error.bind(console);
  console.error = (...a) => { sf.errors.push(a.map(String).join(' ')); consoleError(...a); };

  // Seeded randomness: tests and recordings repeat exactly; players get a fresh seed.
  let seed = (Number(params.get('seed')) ||
              (flags.autoplay || flags.capture || flags.headless ? 1 : Date.now())) >>> 0;
  const startSeed = seed;
  sf.rand = function () {
    seed = (seed + 0x6D2B79F5) >>> 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  Math.random = sf.rand;

  /* ---------- events, state, time ------------------------------------------------------- */
  sf.event = function (name, data) {
    const t = Math.round(sf.clock.now * 100) / 100;
    const c = sf.counts[name] || (sf.counts[name] = {count: 0, first: t, last: t, times: []});
    c.count++; c.last = t;
    if (c.times.length < MAX_TIMES) c.times.push(t);
    if (sf.events.length < MAX_EVENTS) sf.events.push(Object.assign({t, name}, data || {}));
  };
  sf.screen = () => [sf.game.config.width, sf.game.config.height];
  const timers = [];  // SDK-owned delayed actions (autoplay menu picks) on the game clock
  function tick(delta) {
    sf.clock.now += delta / 1000;
    for (let i = timers.length - 1; i >= 0; i--) {
      if (timers[i].at <= sf.clock.now) { const fn = timers[i].fn; timers.splice(i, 1); fn(); }
    }
  }

  /* ---------- input: keyboard for players, the game's autoplay bot for tests ---------------- */
  let policy = null;
  const keys = new WeakMap();
  function norm(x, y, action, action2) {
    const l = Math.hypot(x, y);
    return l > 1 ? {x: x / l, y: y / l, action, action2} : {x, y, action, action2};
  }
  sf.input = function (scene) {
    if (flags.autoplay && policy) {
      let out = {};
      try { out = policy(scene, sf) || {}; } catch (e) { sf.errors.push('autoplay: ' + e.message); }
      return norm(+out.x || 0, +out.y || 0, !!out.action, !!out.action2);
    }
    if (!scene.input || !scene.input.keyboard) return norm(0, 0, false, false);
    let k = keys.get(scene);
    if (!k) {
      k = scene.input.keyboard.addKeys('LEFT,RIGHT,UP,DOWN,W,A,S,D,SPACE,Z,J,X,K,SHIFT');
      keys.set(scene, k);
      // scene.restart() keeps the scene object but drops its keys: forget them, or the
      // restarted scene would read dead keys and the player could not move.
      scene.events.once('shutdown', () => keys.delete(scene));
    }
    const x = (k.RIGHT.isDown || k.D.isDown) - (k.LEFT.isDown || k.A.isDown);
    const y = (k.DOWN.isDown || k.S.isDown) - (k.UP.isDown || k.W.isDown);
    return norm(x, y, k.SPACE.isDown || k.Z.isDown || k.J.isDown,
                k.X.isDown || k.K.isDown || k.SHIFT.isDown);
  };

  /* ---------- art --------------------------------------------------------------------------- */
  const reported = new Set();
  sf.has = id => !!(sf.assets.sprites[id] || sf.assets.images[id]);
  sf.sprite = function (scene, x, y, id, opts) {
    opts = opts || {};
    const a = sf.assets.sprites[id] || sf.assets.images[id];
    if (!a && !reported.has(id)) { reported.add(id); sf.errors.push('Unknown asset id: ' + id); }
    const key = a ? id : '__sf_missing';
    const s = opts.physics === false ? scene.add.sprite(x, y, key) : scene.physics.add.sprite(x, y, key);
    if (a && a.body && s.body) { s.body.setSize(a.body[2], a.body[3]); s.body.setOffset(a.body[0], a.body[1]); }
    return sf.play(s, 'idle');
  };
  sf.play = function (sprite, anim) {
    const key = sprite.texture.key + ':' + anim;
    if (sprite.scene && sprite.scene.anims.exists(key)) sprite.anims.play(key, true);
    return sprite;
  };

  /* ---------- HUD and menus ----------------------------------------------------------------- */
  sf.text = function (scene, x, y, str, opts) {
    opts = opts || {};
    const s = sf.assets.scale || 1;
    return scene.add.text(x, y, String(str), {
      fontFamily: 'monospace', fontSize: (opts.size || 8) * s + 'px', color: opts.color || '#e8e8f0',
      stroke: '#000000', strokeThickness: 2 * s,
    }).setScrollFactor(0).setDepth(opts.depth == null ? 1000 : opts.depth)
      .setOrigin(opts.originX || 0, opts.originY || 0);
  };
  sf.bar = function (scene, x, y, w, h, color) {
    const back = scene.add.rectangle(x, y, w, h, 0x000000, 0.6).setOrigin(0, 0)
      .setScrollFactor(0).setDepth(999);
    const fill = scene.add.rectangle(x, y, w, h, color == null ? 0x5ad35a : color).setOrigin(0, 0)
      .setScrollFactor(0).setDepth(1000);
    // Chainable like a game object, so code that treats it as one does not crash.
    return {
      back, fill,
      set(f) { fill.width = Math.max(0, Math.min(1, f)) * w; return this; },
      setDepth(d) { back.setDepth(d); fill.setDepth(d + 1); return this; },
      setVisible(v) { back.setVisible(v); fill.setVisible(v); return this; },
      setScrollFactor(s) { back.setScrollFactor(s); fill.setScrollFactor(s); return this; },
      destroy() { back.destroy(); fill.destroy(); },
    };
  };
  sf.choose = function (scene, title, options, onPick) {
    const [w, h] = sf.screen(), s = sf.assets.scale || 1, depth = 5000;
    const lineH = 12 * s, boxH = (22 + options.length * 12) * s, top = (h - boxH) / 2;
    const parts = [scene.add.rectangle(w / 2, h / 2, Math.min(w - 16 * s, 260 * s), boxH, 0x10121c, 0.94)
      .setStrokeStyle(s, 0xf4d35e).setScrollFactor(0).setDepth(depth)];
    parts.push(sf.text(scene, w / 2, top + 4 * s, title, {originX: 0.5, color: '#f4d35e', depth: depth + 1}));
    const items = options.map((label, i) =>
      sf.text(scene, w / 2, top + 18 * s + i * lineH, (i + 1) + '  ' + label, {originX: 0.5, depth: depth + 1}));
    parts.push(...items);
    let index = 0, done = false;
    const highlight = () => items.forEach((t, i) => t.setColor(i === index ? '#f4d35e' : '#e8e8f0'));
    const kb = scene.input && scene.input.keyboard;
    const onKey = e => {
      const n = Number(e.key);
      if (n >= 1 && n <= options.length) return finish(n - 1);
      if (e.key === 'ArrowUp' || e.key === 'w') index = (index + options.length - 1) % options.length;
      if (e.key === 'ArrowDown' || e.key === 's') index = (index + 1) % options.length;
      if (e.key === 'Enter' || e.key === ' ') return finish(index);
      highlight();
    };
    function finish(i) {
      if (done) return;
      done = true;
      if (kb) kb.off('keydown', onKey);
      parts.forEach(p => p.destroy());
      sf.event('choice', {index: i, option: String(options[i])});
      onPick(i);
    }
    highlight();
    if (kb) kb.on('keydown', onKey);
    if (!flags.headless) items.forEach((t, i) => t.setInteractive().on('pointerdown', () => finish(i)));
    if (flags.autoplay) {
      const pick = Math.floor(sf.rand() * options.length);
      timers.push({at: sf.clock.now + 0.4, fn: () => finish(pick)});
    }
  };

  /* ---------- boot ------------------------------------------------------------------------ */
  class SFBoot extends Phaser.Scene {
    constructor() { super('sf-boot'); }
    preload() {
      this.load.on('loaderror', f => sf.errors.push('Could not load ' + f.key));
      for (const [id, a] of Object.entries(sf.assets.sprites)) {
        this.load.spritesheet(id, a.file, {frameWidth: a.frame[0], frameHeight: a.frame[1]});
      }
      for (const [id, a] of Object.entries(sf.assets.images)) this.load.image(id, a.file);
    }
    create() {
      const g = this.make.graphics({x: 0, y: 0, add: false});
      g.fillStyle(0xff00ff).fillRect(0, 0, 16, 16).generateTexture('__sf_missing', 16, 16);
      g.destroy();
      for (const [id, a] of Object.entries(sf.assets.sprites)) {
        for (const [name, [start, count]] of Object.entries(a.anims || {})) {
          this.anims.create({
            key: id + ':' + name, frameRate: a.fps || 8, repeat: ONCE.has(name) || count < 2 ? 0 : -1,
            frames: this.anims.generateFrameNumbers(id, {start, end: start + count - 1}),
          });
        }
      }
      const next = this.scene.manager.scenes.find(s => s !== this);
      if (!next) { sf.errors.push('sf.start needs at least one scene'); return; }
      next.events.once('create', () => {
        sf.clock.now = 0;
        if (flags.capture || flags.headless) sf.game.loop.stop();  // the harness steps frames
        sf.ready = true;
        sf.event('boot', {seed: startSeed});
      });
      this.scene.start(next.sys.settings.key);
    }
  }

  sf.start = function (opts) {
    opts = opts || {};
    if (!opts.scenes || !opts.scenes.length) { sf.errors.push('sf.start({scenes}) is required'); return; }
    policy = typeof opts.autoplay === 'function' ? opts.autoplay : null;
    if (flags.autoplay && !policy) sf.errors.push('sf.start needs an autoplay(scene, sf) policy');
    Object.assign(sf.assets, window.SF_ASSETS || {});
    const [w, h] = opts.size || sf.assets.screen;
    const still = flags.capture || flags.headless;
    keepVelocityOnGroupAdd();
    sf.game = new Phaser.Game({
      // Canvas even in playtests: text and tile sprites need a renderer; playtests skip
      // drawing by stepping with headlessStep (logic only) instead.
      type: Phaser.CANVAS,
      width: w, height: h, pixelArt: true, roundPixels: true, antialias: false,
      audio: {noAudio: true}, banner: false, seed: [String(startSeed)],
      backgroundColor: opts.background || sf.assets.background,
      // Image elements (not XHR) so the build also plays when index.html is opened directly.
      loader: {imageLoadType: 'HTMLImageElement'},
      physics: {default: 'arcade', arcade: Object.assign(
        {gravity: {x: 0, y: opts.gravity || 0}, debug: false}, opts.arcade || {})},
      scale: still ? {mode: Phaser.Scale.NONE}
                   : {mode: Phaser.Scale.FIT, autoCenter: Phaser.Scale.CENTER_BOTH},
      scene: [SFBoot, ...opts.scenes],
    });
    sf.game.events.on('prestep', (time, delta) => tick(delta));
  };

  /* Adding a body to a physics group (this.physics.add.group()) applies the group's defaults,
     including velocity 0, so a projectile given its velocity before group.add() froze where it
     was fired. Keep the body's velocity unless the group sets a default velocity itself. */
  function keepVelocityOnGroupAdd() {
    const G = Phaser.Physics && Phaser.Physics.Arcade && Phaser.Physics.Arcade.Group;
    if (!G || G.prototype.sfKeepsVelocity) return;
    const add = G.prototype.createCallbackHandler;
    G.prototype.createCallbackHandler = function (child) {
      const v = child.body && {x: child.body.velocity.x, y: child.body.velocity.y};
      add.call(this, child);
      const d = this.defaults || {};
      if (v && child.body && (v.x || v.y) && !d.setVelocityX && !d.setVelocityY) {
        child.body.setVelocity(v.x, v.y);
      }
    };
    G.prototype.sfKeepsVelocity = true;
  }

  /* ---------- harness hooks: fixed steps, no wall clock ------------------------------------- */
  const manual = {t: 0};
  // A throw inside the game's update must become a reported error, not abort the harness call.
  function step(dt) {
    manual.t += dt;
    try {
      if (flags.headless) sf.game.headlessStep(manual.t, dt); else sf.game.step(manual.t, dt);
    } catch (e) {
      sf.errors.push(String((e && e.stack) || e).split('\n').slice(0, 3).join(' | '));
    }
  }
  // Recorder: n frames as PNG data URLs (same build + same seed => same frames).
  window.sfCapture = function (frames, fps) {
    const dt = 1000 / fps, out = [];
    for (let i = 0; i < frames; i++) { step(dt); out.push(sf.game.canvas.toDataURL('image/png')); }
    return out;
  };
  // Playtest: advance `seconds` of game time; stops early on win, lose or an error flood.
  window.sfRun = function (seconds, fps) {
    const dt = 1000 / fps, n = Math.round(seconds * fps);
    for (let i = 0; i < n; i++) {
      step(dt);
      if (sf.counts.win || sf.counts.lose || sf.errors.length > 20) break;
    }
    return {t: sf.clock.now, errors: sf.errors.length};
  };
})();
