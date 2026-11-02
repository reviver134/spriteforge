/* SpriteForge game SDK: generic helpers that generated Phaser 3 games are written against.
   It is not a game: it boots Phaser, loads the build's art, reads input (keyboard or the game's
   own autoplay bot), and exposes the hooks the harness uses to test and record a build:
   window.sf.{ready, errors, events, counts, state, menu, settings}, window.sfCapture(frames, fps),
   window.sfRun(seconds, fps, keepGoing) and window.sfShot(fps). QA tests press real keys while
   the harness steps frames (tools/probe_keys.py: Playwright key presses reach sf.input and
   sf.choose). The model-facing reference is API.md. A build with menus (L2.5) also loads
   sfflow.js: the title, menus, pause, results, game over and victory screens and the saved
   progress; while it pauses the game, sf.paused stops the game clock. */
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
    ready: false, errors: [], events: [], counts: {}, state: {}, flags, menu: null,
    assets: {screen: [320, 180], scale: 1, background: '#0b0c14', sprites: {}, images: {}},
    game: null, clock: {now: 0}, paused: false,
    // Settings the player picks in the menus (sfflow.js); the game multiplies enemy speed,
    // damage and spawn rate by `multiplier` (easy 0.75, normal 1, hard 1.35).
    settings: {difficulty: 'normal', multiplier: 1, fewerFlashes: false},
  });
  const MAX_EVENTS = 400, MAX_TIMES = 300, ONCE = new Set(['attack', 'death', 'die', 'break', 'hurt', 'hit', 'jump', 'land', 'transform']);

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
    if (sf.paused) return;  // a menu paused the game (sfflow.js): game time stands still
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

  /* ---------- effects (L2.4): the motion design's particles, flash, shake and squash ----------
     sf.assets.fx = {name: {on, particles, colors, speed, flash, shake, squash, seconds}}.
     sf.fx(name, x, y, target) plays the effect called `name`, or every effect whose `on` is
     `name` (the level engine plays jump, land, dash, break, kill, pickup, transform, hurt,
     checkpoint and exit itself). `target` (a sprite) flashes and squashes. Particles use their
     own random numbers, so effects never change the game's seeded randomness. */
  sf.fxCount = {};
  let fxSeed = 12345, fxLive = 0;
  const fxRand = () => ((fxSeed = (Math.imul(fxSeed, 1103515245) + 12345) >>> 0) / 4294967296);
  const MAX_PARTICLES = 48;
  function activeScene(target) {
    if (target && target.scene) return target.scene;
    const scenes = sf.game ? sf.game.scene.getScenes(true) : [];
    return scenes.find(s => !String(s.sys.settings.key).startsWith('sf-')) || null;
  }
  sf.particles = function (scene, x, y, n, colors, speed, seconds) {
    const s = sf.assets.scale || 1;
    n = Math.max(0, Math.min(n | 0, MAX_PARTICLES - fxLive));
    for (let i = 0; i < n; i++) {
      const c = parseInt(String(colors[i % colors.length] || '#ffffff').slice(1), 16) || 0xffffff;
      const size = s * (1 + (i % 2));
      const dot = scene.add.rectangle(x, y, size, size, c).setDepth(900);
      const a = fxRand() * Math.PI * 2, v = (speed || 40) * s * (0.4 + 0.6 * fxRand());
      fxLive++;
      scene.tweens.add({
        targets: dot, alpha: 0, duration: seconds * 1000,
        x: x + Math.cos(a) * v * seconds, y: y + Math.sin(a) * v * seconds + 10 * s * seconds,
        onComplete: () => { fxLive--; dot.destroy(); },
      });
    }
    return n;
  };
  // Several effects may share a moment (acid_transform and metal_transform both on transform;
  // a crate's dissolve and a wall's break both on break): `hint` (what happened: the form, the
  // thing's entity and what opens it) picks the one whose name shares a word with it, else
  // the one named after the moment, else the first. One effect per moment, never all of them.
  const words = s => String(s || '').toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2);
  sf.fx = function (name, x, y, target, hint) {
    const all = sf.assets.fx || {};
    let list = Object.entries(all).filter(([, e]) => e.on === name);
    if (!list.length && all[name]) list = [[name, all[name]]];  // the game names one: sf.fx('dissolve')
    if (list.length > 1) {
      const want = new Set(words(hint));
      const matched = list.filter(([key]) => words(key).some(w => want.has(w)));
      list = matched.length ? matched.slice(0, 1) : list.filter(([key]) => key === name).concat(list).slice(0, 1);
    }
    const scene = activeScene(target);
    if (!list.length || !scene || !scene.add) return 0;
    for (const [key, effect] of list) {
      // The player asked for fewer flashes (settings): no flash, no screen shake.
      const e = sf.settings.fewerFlashes ? Object.assign({}, effect, {flash: '', shake: 0}) : effect;
      sf.fxCount[key] = (sf.fxCount[key] || 0) + 1;
      const seconds = Math.max(0.1, e.seconds || 0.3);
      sf.particles(scene, x, y, e.particles, e.colors || ['#ffffff'], e.speed, seconds);
      if (e.shake > 0 && scene.cameras && scene.cameras.main) {
        scene.cameras.main.shake(seconds * 500, 0.012 * e.shake);
      }
      if (target && target.sfFx) target.sfFx(e);  // the player's skin squashes and flashes itself
      else if (target && target.active) {
        if (e.flash) {
          target.setTintFill(parseInt(e.flash.slice(1), 16));
          scene.time.delayedCall(90, () => { if (target.active) target.clearTint(); });
        }
        if (e.squash) {
          scene.tweens.add({targets: target, scaleX: target.scaleX * (1 + e.squash),
                            scaleY: target.scaleY * (1 - e.squash), duration: seconds * 400, yoyo: true});  // fmt: skip
        }
      }
    }
    return list.length;
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
    // QA tests see the open menu (and its 'menu' event) and pick from it with real keys.
    sf.menu = {title: String(title), options: options.map(String)};
    sf.event('menu', {title: String(title)});
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
      sf.menu = null;
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
          // The motion design's rows say their own fps and loop (L2.4); older art: by name.
          const row = (a.rows || {})[name];
          const loop = row ? row.loop : !ONCE.has(name);
          this.anims.create({
            key: id + ':' + name, frameRate: (row && row.fps) || a.fps || 8,
            repeat: loop && count > 1 ? -1 : 0,
            frames: this.anims.generateFrameNumbers(id, {start, end: start + count - 1}),
          });
        }
      }
      // The game's first scene (the SDK's own scenes are named sf-...).
      const next = this.scene.manager.scenes.find(s => s !== this && !String(s.sys.settings.key).startsWith('sf-'));
      if (!next) { sf.errors.push('sf.start needs at least one scene'); return; }
      let created = false;
      next.events.on('create', () => {
        if (!created) { created = true; return; }
        // Created again: the next level, or a new run (scene.restart(), Retry, Start again
        // from a menu: QA tests check restart after win/lose).
        if (sf.level && sf.level.advancing) { sf.level.advancing = false; return; }
        if (!(sf.menu && sf.menu.screen)) sf.menu = null;
        sf.event('restart');
      });
      const ready = () => {
        sf.clock.now = 0;
        if (flags.capture || flags.headless) sf.game.loop.stop();  // the harness steps frames
        sf.ready = true;
        sf.event('boot', {seed: startSeed});
      };
      // With menus the flow decides what comes first (the title, or the game for bots and
      // ?play=1); it calls ready() when that scene exists.
      if (sf.flow && sf.flow.boot) { sf.flow.boot(this, next, ready); return; }
      next.events.once('create', ready);
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
  // `draw` renders this step even in a logic-only playtest (same game logic either way).
  function step(dt, draw) {
    manual.t += dt;
    try {
      if (flags.headless && !draw) sf.game.headlessStep(manual.t, dt); else sf.game.step(manual.t, dt);
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
  // Playtest screenshot: one drawn step (it advances the game like any other step), as a PNG
  // data URL of the canvas at game size.
  window.sfShot = function (fps) {
    step(1000 / fps, true);
    return sf.game.canvas.toDataURL('image/png');
  };
  // Playtest: advance `seconds` of game time; stops early on win, lose or an error flood.
  // `keepGoing` (QA tests) steps on after win or lose, so a test can restart the game.
  window.sfRun = function (seconds, fps, keepGoing) {
    const dt = 1000 / fps, n = Math.round(seconds * fps);
    for (let i = 0; i < n; i++) {
      step(dt);
      if (sf.errors.length > 20 || (!keepGoing && (sf.counts.win || sf.counts.lose || sf.halted))) break;
    }
    return {t: sf.clock.now, errors: sf.errors.length};
  };
})();
