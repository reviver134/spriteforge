/* SpriteForge flow and menus (L2.5): the screens around the game, drawn from data.

   A build with menus loads menus.js (window.SF_MENUS: the Menu Designer's screens fitted by
   code, the theme in the art's colours, the levels, controls and credits) and this file after
   sf.js (and sflevel.js). The game itself is one scene that starts straight in play; the SDK
   adds its own scene `sf-menu` on top and runs the flow:

     title (Press Enter) -> main (Start, Continue, Level select, Settings, Controls, Credits)
     play: Escape pauses (Resume, Restart level, Settings, Quit to title; game time stops)
     a level finished -> results (Next level, Title); the last one -> victory (Credits, Title)
     lose -> game over (Retry, Title)

   Keys: arrows/WASD move the highlight, Enter/Space pick, Escape goes back (resumes on pause),
   digits pick directly; the mouse works too. Events: title, menu_open {screen}, menu_select
   {screen, item, action}, pause, resume, level_start {n} (single-scene games; sflevel.js emits
   it for levels as data), level_complete {n}, level_failed {n}, game_complete.
   sf.menu = {screen, title, options, actions, index} while a screen is open (QA tests read it).
   sf.progress (localStorage, per game): levels unlocked, the best result per level, settings;
   ?reset=1 clears it. ?play=1, ?level=n and autoplay skip the title (bots and playtests);
   in autoplay a screen that ends a level picks its first item by itself. */
(function () {
  'use strict';
  const sf = window.sf;
  const M = window.SF_MENUS || null;
  const params = new URLSearchParams(location.search);
  const flow = (sf.flow = {active: !!M, menus: M, state: 'boot', screen: null});
  if (!M) return;

  const ITEM = 8, HEAD = 12, BIG = 16, CHAR_EM = 0.62, PANEL_MAX = 260, PAD = 10, LINE = 12;
  const DEPTH = 6000;
  const theme = M.theme || {};
  const col = h => parseInt(String(h || '#ffffff').slice(1), 16);
  const levelGame = () => !!(sf.level && sf.level.data);
  const levelCount = () => (M.levels || []).length || 1;

  /* ---------- saved progress and settings ---------------------------------------------------- */
  const KEY = 'sf-progress:' + (M.title || document.title || 'game');
  const store = {
    read() { try { return JSON.parse(window.localStorage.getItem(KEY) || '{}') || {}; } catch (e) { return {}; } },
    write(v) { try { window.localStorage.setItem(KEY, JSON.stringify(v)); } catch (e) { /* private mode */ } },
    clear() { try { window.localStorage.removeItem(KEY); } catch (e) { /* private mode */ } },
  };
  if (params.has('reset')) store.clear();
  const P = (sf.progress = Object.assign({unlocked: 1, best: {}, settings: {}, completed: false}, store.read()));
  P.unlocked = Math.max(1, Math.min(levelCount(), P.unlocked | 0 || 1));
  const save = () => store.write(P);
  function applySettings() {
    const d = (P.settings.difficulty in (M.difficulty || {})) ? P.settings.difficulty : 'normal';
    sf.settings.difficulty = d;
    sf.settings.multiplier = (M.difficulty || {})[d] || 1;
    sf.settings.fewerFlashes = !!P.settings.fewerFlashes;
    sf.settings.sound = P.settings.sound !== false;  // sfsound.js plays nothing when off
    if (sf.state) sf.state.difficulty = d;
  }
  applySettings();

  /* ---------- the flow ---------------------------------------------------------------------- */
  const skip = sf.flags.autoplay || params.has('play') || params.has('level');
  let menu = null;  // the sf-menu scene
  let pending = null;  // a screen waiting to open after the game's own end message
  let shownAt = 0;  // when the open screen opened (autoplay picks a little later)
  flow.current = 1;  // the level being played (1 for single-scene games)

  flow.boot = function (boot, game, ready) {
    flow.gameKey = game.sys.settings.key;
    game.events.on('create', onGameCreate);
    if (skip) {
      flow.state = 'play';
      game.events.once('create', ready);
      boot.scene.launch('sf-menu');
      boot.scene.start(flow.gameKey);
    } else {
      flow.state = 'title';
      flow.onMenuReady = ready;
      boot.scene.start('sf-menu');
    }
  };

  function onGameCreate() {
    pending = null;
    flow.state = 'play';
    sf.paused = false;
    if (flow.screen) close();
    flow.current = levelGame() ? sf.level.n : 1;
    if (!levelGame()) sf.event('level_start', {n: 1});
    intro(flow.current);
  }
  // (sf.js emits restart whenever the game's scene starts again, except for the next level.)

  // Events the game emits drive the screens after play: lose -> game over, win -> victory.
  const event = sf.event;
  sf.event = function (name, data) {
    event(name, data);
    if (flow.state !== 'play') return;
    if (name === 'lose') {
      event('level_failed', {n: flow.current});
      if (M.screens.game_over) later('game_over');
    } else if (name === 'win') {
      if (!levelGame()) event('level_complete', {n: 1});
      record(flow.current);
      P.completed = true;
      save();
      event('game_complete');
      later('victory');
    }
  };

  function record(n) {
    const st = sf.state || {}, best = P.best[n] || {};
    const time = +st.time || 0, score = +st.score || 0;
    P.best[n] = {time: best.time ? Math.min(best.time, time) : time, score: Math.max(best.score || 0, score)};
    flow.last = {n, time, score, best: best.time ? best : null};
  }

  // The sflevel engine finished level n (not the last): results, then the next level.
  flow.levelDone = function (n) {
    record(n);
    P.unlocked = Math.max(P.unlocked, Math.min(levelCount(), n + 1));
    save();
    later(M.screens.results ? 'results' : null);
  };

  function later(screen) {  // let the game's own message show first
    flow.state = 'ending';
    pending = {screen, at: 0.9};
  }

  function gameScene() { return sf.game.scene.getScene(flow.gameKey); }
  const gameScenes = () => sf.game.scene.scenes.filter(sc => !String(sc.sys.settings.key).startsWith('sf-'));

  let stopped = [];  // the game's scenes a screen paused
  function stopPlay(state) {  // the game stands still under a screen
    flow.state = state;
    sf.paused = true;
    for (const sc of gameScenes()) {
      if (sc.sys.isActive()) { sc.sys.pause(); stopped.push(sc); }
    }
  }

  flow.pause = function () {
    if (flow.state !== 'play' || (sf.menu && !sf.menu.screen)) return;  // not over an sf.choose
    stopPlay('paused');
    sf.event('pause');
    open('pause', true);
  };
  flow.resume = function () {
    if (flow.state !== 'paused') return;
    close();
    flow.state = 'play';
    sf.paused = false;
    for (const sc of stopped) if (sc.sys.isPaused()) sc.sys.resume();
    stopped = [];
    sf.event('resume');
  };

  // Start level n (a new game, Continue, a level from Level select, the next level).
  flow.play = function (n, advancing) {
    close();
    n = Math.max(1, Math.min(levelCount(), n || 1));
    if (levelGame()) { sf.level.n = n; sf.level.first = n; sf.level.advancing = !!advancing; }
    flow.current = n;
    flow.state = 'play';
    sf.paused = false;
    stopped = [];
    menu.scene.launch(flow.gameKey);  // starts it, or starts it again when it runs
  };
  flow.newGame = n => flow.play(n || 1, false);
  flow.restartLevel = () => flow.play(flow.current, false);
  flow.toTitle = function () {
    close();
    for (const sc of gameScenes()) {
      if (sc.sys.isActive() || sc.sys.isPaused()) menu.scene.stop(sc.sys.settings.key);
    }
    stopped = [];
    flow.state = 'title';
    sf.paused = false;
    pending = null;
    open('title', true);
  };

  /* ---------- screens ----------------------------------------------------------------------- */
  const stack = [];
  function open(id, reset) {
    if (!M.screens[id]) return;
    if (reset) stack.length = 0;
    else if (flow.screen && flow.screen !== id) stack.push(flow.screen);
    flow.screen = id;
    flow.index = 0;
    shownAt = 0;
    if (id === 'title') sf.event('title');
    sf.event('menu_open', {screen: id});
    draw();
  }
  flow.open = id => open(id, false);
  // Screenshots (G3): any screen as the player sees it; the ones over play stop the game first.
  flow.preview = function (id) {
    if (['pause', 'results', 'game_over', 'victory'].includes(id)) {
      const st = sf.state || {};
      if (!flow.last) flow.last = {n: flow.current, time: +st.time || 0, score: +st.score || 0, best: null};
      if (flow.state === 'play' || flow.state === 'ending') stopPlay(id === 'pause' ? 'paused' : 'over');
      pending = null;
    }
    open(id, true);
  };
  function close() {
    flow.screen = null;
    stack.length = 0;
    if (sf.menu && sf.menu.screen) sf.menu = null;
    if (menu) menu.clear();
  }
  function back() {
    const id = flow.screen;
    if (id === 'pause') return flow.resume();
    if (['results', 'game_over', 'victory'].includes(id)) return flow.toTitle();
    if (id === 'title') return;
    const prev = stack.pop() || (flow.state === 'title' ? 'title' : null);
    if (prev) { flow.screen = null; open(prev, false); return; }
    close();
  }

  // The items of the open screen: [{label, action, value, locked, level}].
  function items(id) {
    const out = (M.screens[id].items || []).map(i => Object.assign({}, i));
    if (id === 'level_select') {
      const lv = (M.levels || []).map(l => ({label: l.n + '. ' + l.name, action: 'level', level: l.n,
                                             locked: l.n > P.unlocked}));  // fmt: skip
      out.unshift(...lv);
    }
    if (id === 'main' && P.unlocked <= 1) {  // nothing to continue yet
      out.forEach(i => { if (i.action === 'continue') i.locked = true; });
    }
    for (const i of out) {
      if (i.action === 'difficulty') i.value = sf.settings.difficulty;
      if (i.action === 'fewer_flashes') i.value = sf.settings.fewerFlashes ? 'on' : 'off';
      if (i.action === 'sound') i.value = sf.settings.sound ? 'on' : 'off';
    }
    return out;
  }

  function pick(index) {
    const id = flow.screen;
    if (!id) return;
    const list = items(id), it = list[index];
    if (!it || it.locked) return;
    sf.event('menu_select', {screen: id, item: it.label, action: it.action});
    const act = it.action;
    if (act === 'main') return open('main', true);
    if (act === 'start') return flow.newGame(1);
    if (act === 'continue') return flow.newGame(P.unlocked);
    if (act === 'level') return flow.newGame(it.level);
    if (['level_select', 'settings', 'controls', 'credits'].includes(act)) return open(act, false);
    if (act === 'resume') return flow.resume();
    if (act === 'restart' || act === 'retry') return flow.restartLevel();
    if (act === 'next_level') return flow.play(flow.current + 1, true);
    if (act === 'title') return flow.toTitle();
    if (act === 'back') return back();
    if (act === 'difficulty') {
      const names = Object.keys(M.difficulty || {normal: 1});
      P.settings.difficulty = names[(names.indexOf(sf.settings.difficulty) + 1) % names.length];
    }
    if (act === 'fewer_flashes') P.settings.fewerFlashes = !sf.settings.fewerFlashes;
    if (act === 'sound') P.settings.sound = !sf.settings.sound;
    applySettings();
    save();
    flow.index = index;
    draw();
  }
  flow.pick = pick;
  // The sound on or off (the settings item, or M anywhere: sfsound.js), saved with the settings.
  flow.setSound = function (on) {
    P.settings.sound = !!on;
    applySettings();
    save();
    if (flow.screen) draw();
  };

  function onKey(e) {
    const k = e.key;
    if (!flow.screen) {
      if (k === 'Escape') flow.pause();
      return;
    }
    const n = items(flow.screen).length;
    if (!n) return;
    const digit = Number(k);
    if (digit >= 1 && digit <= Math.min(9, n)) return pick(digit - 1);
    if (k === 'ArrowUp' || k === 'w' || k === 'W') flow.index = (flow.index + n - 1) % n;
    else if (k === 'ArrowDown' || k === 's' || k === 'S') flow.index = (flow.index + 1) % n;
    else if (k === 'Enter' || k === ' ') return pick(flow.index);
    else if (k === 'Escape') return back();
    else return;
    if (sf.sound && sf.sound.moment) sf.sound.moment('menu_move');
    draw();
  }

  /* ---------- drawing ----------------------------------------------------------------------- */
  const fit = (text, px, room) => {
    const max = Math.max(1, Math.floor(room / (px * CHAR_EM)));
    text = String(text || '');
    return text.length <= max ? text : text.slice(0, Math.max(1, max - 1)) + '~';
  };

  function draw() {
    if (!menu || !flow.screen) return;
    const scene = menu, id = flow.screen, def = M.screens[id];
    scene.clear();
    const [w, h] = sf.screen(), s = sf.assets.scale || 1;
    const add = o => { scene.parts.push(o); return o.setScrollFactor ? o.setScrollFactor(0) : o; };
    const text = (x, y, str, px, color, originX) => add(sf.text(scene, x, y, str, {
      size: px, color: color, originX: originX == null ? 0.5 : originX, depth: DEPTH + 2}));  // fmt: skip
    const behindTitle = flow.state === 'title';
    if (behindTitle) {
      if (M.art && M.art.title && sf.has(M.art.title)) {
        const img = add(scene.add.image(w / 2, h / 2, M.art.title).setDepth(DEPTH));
        img.setScale(Math.max(w / img.width, h / img.height));
      } else {
        add(scene.add.rectangle(0, 0, w, h, col(theme.panel)).setOrigin(0).setDepth(DEPTH));
        add(scene.add.rectangle(0, h * 0.62, w, h * 0.38, col(theme.border), 0.18).setOrigin(0).setDepth(DEPTH));
      }
    } else {
      add(scene.add.rectangle(0, 0, w, h, 0x000000, 0.55).setOrigin(0).setDepth(DEPTH));
    }
    const list = items(id);
    const panelW = Math.min(w - 32 * s, PANEL_MAX * s), room = panelW - 2 * PAD * s;
    let top = null;
    const lines = [];
    if (def.title) lines.push({str: def.title, px: HEAD, color: theme.highlight});
    // (The controls screen lists the controls itself: a subtitle repeating them is left out.)
    if (def.subtitle && id !== 'controls') lines.push({str: def.subtitle, px: ITEM, color: theme.text});
    if (id === 'results' && flow.last) {
      lines.push({str: 'Level ' + flow.last.n + ': ' + ((M.levels || [])[flow.last.n - 1] || {}).name, px: ITEM, color: theme.text});
      lines.push({str: 'Time ' + flow.last.time.toFixed(1) + ' s' + (flow.last.score ? '   Score ' + flow.last.score : ''), px: ITEM, color: theme.text});
      if (flow.last.best) lines.push({str: 'Best ' + flow.last.best.time.toFixed(1) + ' s', px: ITEM, color: theme.dim});
    }
    if (id === 'controls') (M.controls || []).forEach(c => lines.push({str: c, px: ITEM, color: theme.text}));
    if (id === 'credits') (M.credits || []).forEach(c => lines.push({str: c, px: ITEM, color: theme.text}));
    const headH = lines.reduce((a, l) => a + (l.px + 4) * s, 0);
    const panelH = headH + (list.length * LINE) * s + 2 * PAD * s;
    if (id === 'title' || id === 'main') {
      // The panel sits at the bottom; the game's title, its tagline and the emblem share what is
      // left above it: the emblem shrinks (or goes) first, then the tagline (live 2026-11-02: a
      // six-item main menu under a full-size emblem ran off the screen).
      top = h - panelH - 4 * s;
      const room = top - 2 * s;
      const sub = !!M.subtitle && room >= (BIG + 4 + LINE + 4) * s;
      const words = (BIG + 4) * s + (sub ? LINE * s : 0);
      let y = Math.max(2 * s, Math.min(h * 0.12, room - words));
      if (M.art && M.art.emblem && sf.has(M.art.emblem)) {
        const space = Math.min(h * 0.3, room - words - y - 4 * s);
        if (space >= 12 * s) {
          const em = add(scene.add.image(w / 2, y, M.art.emblem).setOrigin(0.5, 0).setDepth(DEPTH + 1));
          em.setScale(Math.min(1, space / em.height));
          y += em.displayHeight + 4 * s;
        }
      }
      text(w / 2, y, fit(M.title, BIG * s, w - 24 * s), BIG, theme.highlight);
      y += (BIG + 4) * s;
      if (sub) { text(w / 2, y, fit(M.subtitle, ITEM * s, w - 24 * s), ITEM, theme.text); y += LINE * s; }
      top = Math.max(top, y + 2 * s);
    }
    if (top == null) top = Math.max(4 * s, (h - panelH) / 2);
    const px = w / 2 - panelW / 2;
    const panel = add(scene.add.rectangle(px, top, panelW, panelH, col(theme.panel), 0.94).setOrigin(0).setDepth(DEPTH + 1));
    if (theme.style !== 'solid') panel.setStrokeStyle(s, col(theme.border));
    if (theme.style === 'double') {
      add(scene.add.rectangle(px + 2 * s, top + 2 * s, panelW - 4 * s, panelH - 4 * s).setOrigin(0)
        .setStrokeStyle(s, col(theme.border)).setDepth(DEPTH + 1));
    }
    let y = top + PAD * s;
    for (const l of lines) { text(w / 2, y, fit(l.str, l.px * s, room), l.px, l.color); y += (l.px + 4) * s; }
    const labels = [];
    list.forEach((it, i) => {
      const sel = i === flow.index;
      const label = it.label + (it.value != null ? ': ' + it.value : '');
      const t = text(px + PAD * s, y + i * LINE * s, (sel ? '> ' : '  ') + fit(label, ITEM * s, room - 2 * ITEM * s * CHAR_EM),
                     ITEM, it.locked ? theme.dim : sel ? theme.highlight : theme.text, 0);  // fmt: skip
      if (!sf.flags.headless) t.setInteractive().on('pointerdown', () => { flow.index = i; pick(i); });
      labels.push(it.label);
    });
    flow.panel = {x: px, y: top, w: panelW, h: panelH};
    sf.menu = {screen: id, title: def.title || M.title, options: labels, index: flow.index,
               actions: list.map(it => it.action)};  // fmt: skip
  }

  // The level's intro: its number, name and goal over the first seconds of play.
  function intro(n) {
    if (!menu) return;
    const lv = (M.levels || [])[n - 1];
    if (!lv) return;
    const [w] = sf.screen(), s = sf.assets.scale || 1;
    menu.clearIntro();
    const a = sf.text(menu, w / 2, 26 * s, 'Level ' + n + ': ' + lv.name, {size: HEAD, color: theme.highlight, originX: 0.5, depth: DEPTH - 1});
    const b = sf.text(menu, w / 2, 42 * s, fit(lv.goal, ITEM * s, w - 24 * s), {size: ITEM, color: theme.text, originX: 0.5, depth: DEPTH - 1});
    menu.intro = {parts: [a, b], until: sf.clock.now + 1.8};
  }

  class MenuScene extends Phaser.Scene {
    constructor() { super('sf-menu'); }
    create() {
      menu = this;
      this.parts = [];
      this.intro = null;
      this.scene.bringToTop();
      if (this.input.keyboard) this.input.keyboard.on('keydown', onKey);
      if (flow.state === 'title') {
        open('title', true);
        if (flow.onMenuReady) { const r = flow.onMenuReady; flow.onMenuReady = null; r(); }
      } else if (flow.state === 'play') intro(flow.current);
    }
    clear() { for (const p of this.parts) p.destroy(); this.parts = []; }
    clearIntro() { if (this.intro) this.intro.parts.forEach(p => p.destroy()); this.intro = null; }
    update(time, delta) {
      const dt = delta / 1000;
      if (this.intro && (sf.clock.now >= this.intro.until || flow.state !== 'play')) this.clearIntro();
      if (pending) {
        pending.at -= dt;
        if (pending.at <= 0) {
          const p = pending;
          pending = null;
          if (p.screen) { stopPlay('over'); open(p.screen, true); }
          else if (flow.state === 'ending') flow.play(flow.current + 1, true);  // no results screen
        }
      }
      // A bot cannot read menus: a screen that ends a level picks its first item by itself.
      if (sf.flags.autoplay && flow.screen && ['results', 'game_over', 'victory'].includes(flow.screen)) {
        shownAt += dt;
        if (shownAt > 1.5) { shownAt = -1e9; pick(0); }
      }
    }
  }

  // The SDK's scene goes last, so it draws over the game; sf.state shows the difficulty the
  // player picked (QA tests read it).
  const start = sf.start;
  sf.start = function (opts) {
    opts = Object.assign({}, opts || {});
    opts.scenes = [...(opts.scenes || []), MenuScene];
    const out = start(opts);
    if (sf.game) sf.game.events.on('poststep', () => { if (sf.state) sf.state.difficulty = sf.settings.difficulty; });
    return out;
  };
})();
