/* SpriteForge sound (L2.7): the Sound Director's cues, synthesised in the browser with Web Audio.

   A build with sounds loads sounds.js (window.SF_SOUNDS: cues, the preset library, the moments)
   and this file right after sf.js. No audio files: every cue is one to four synth layers (a
   square, triangle, sine or sawtooth tone, or noise) with a pitch slide, an envelope (attack,
   hold, release), an optional filter sweep and a wobble.

   window.SFSynth.play(cue, volume) / SFSynth.render(cue) work without the game (the sound
   editor, sounds.html, uses them). With the game (window.sf):
   * every sf.event(name) and sf.fx(name, x, y, target, hint) is a moment; the cue whose `on`
     names it plays. Several cues on one moment: the one whose name or `match` shares a word
     with what happened (the thing's entity, the form) plays, as sf.fx picks its effect.
   * loops (`on: ['move']`) repeat every `every` seconds while the player moves on the ground
     (levels as data) or moves at all (other games, from sf.state.x/y).
   * sf.sound.play(name) plays a cue by its name; sf.sound.moment(name, hint) a moment.
   * sf.settings.sound (on/off) and sf.settings.volume; M toggles the sound (saved with the
     menus' settings). Browsers start audio only after a key press or a click.
   * Playtests and recordings (?headless, ?capture) make no sound but count every cue that
     would have played in sf.soundCount, so the harness can tell which sounds were heard. */
(function () {
  'use strict';

  /* ---------- the synth (no game needed) ----------------------------------------------------- */
  const MAX_VOICES = 14;
  let live = 0;
  const noiseBuffers = new WeakMap();
  function noise(ac) {
    let b = noiseBuffers.get(ac);
    if (!b) {
      b = ac.createBuffer(1, ac.sampleRate, ac.sampleRate);
      const d = b.getChannelData(0);
      let s = 22222;  // fixed noise: the same cue sounds the same every time
      for (let i = 0; i < d.length; i++) { s = (Math.imul(s, 1103515245) + 12345) >>> 0; d[i] = s / 2147483648 - 1; }
      noiseBuffers.set(ac, b);
    }
    return b;
  }
  const num = (v, lo, hi, dflt) => { v = Number(v); return isFinite(v) ? Math.max(lo, Math.min(hi, v)) : dflt; };

  // One layer into `out`, starting at t0; returns when it ends. `rate` scales pitch and cutoff.
  function layer(ac, Ly, t0, rate, out) {
    const start = t0 + num(Ly.delay, 0, 2, 0);
    const a = num(Ly.attack, 0.001, 1, 0.005), h = num(Ly.hold, 0, 2, 0.03), r = num(Ly.release, 0.005, 2, 0.1);
    const end = start + a + h + r;
    const isNoise = Ly.wave === 'noise';
    let src;
    if (isNoise) {
      src = ac.createBufferSource();
      src.buffer = noise(ac);
      src.loop = true;
    } else {
      src = ac.createOscillator();
      src.type = ['square', 'triangle', 'sine', 'sawtooth'].includes(Ly.wave) ? Ly.wave : 'square';
      const f0 = num(Ly.freq, 20, 12000, 440) * rate, f1 = num(Ly.freq_end, 20, 12000, f0) * rate;
      src.frequency.setValueAtTime(f0, start);
      src.frequency.exponentialRampToValueAtTime(Math.max(20, f1), end);
    }
    let node = src, filter = null;
    if (['lowpass', 'highpass', 'bandpass'].includes(Ly.filter)) {
      filter = ac.createBiquadFilter();
      filter.type = Ly.filter;
      filter.Q.value = num(Ly.q, 0.1, 20, 1);
      const c0 = num(Ly.cutoff, 40, 16000, 2000) * rate, c1 = num(Ly.cutoff_end, 40, 16000, c0) * rate;
      filter.frequency.setValueAtTime(Math.min(c0, ac.sampleRate / 2 - 1), start);
      filter.frequency.exponentialRampToValueAtTime(Math.max(40, Math.min(c1, ac.sampleRate / 2 - 1)), end);
      node.connect(filter);
      node = filter;
    }
    const env = ac.createGain();
    const vol = num(Ly.volume, 0, 1, 0.3);
    env.gain.setValueAtTime(0, start);
    env.gain.linearRampToValueAtTime(vol, start + a);
    env.gain.setValueAtTime(vol, start + a + h);
    env.gain.exponentialRampToValueAtTime(0.0001, end);
    node.connect(env);
    let tail = env;
    const wob = num(Ly.wobble, 0, 60, 0), depth = num(Ly.wobble_depth, 0, 1, 0);
    let lfo = null;
    if (wob > 0 && depth > 0) {
      lfo = ac.createOscillator();
      lfo.frequency.value = wob;
      const amount = ac.createGain();
      if (!isNoise) { amount.gain.value = depth * num(Ly.freq, 20, 12000, 440) * rate; lfo.connect(amount).connect(src.frequency); }
      else if (filter) { amount.gain.value = depth * num(Ly.cutoff, 40, 16000, 2000) * rate; lfo.connect(amount).connect(filter.frequency); }
      else {  // noise with no filter: a tremolo
        const trem = ac.createGain();
        trem.gain.value = 1 - depth / 2;
        amount.gain.value = depth / 2;
        lfo.connect(amount).connect(trem.gain);
        env.connect(trem);
        tail = trem;
      }
      lfo.start(start);
      lfo.stop(end);
    }
    tail.connect(out);
    src.start(start);
    src.stop(end + 0.02);
    return end;
  }

  const SFSynth = (window.SFSynth = {
    ctx: null,
    // The cue now, at `volume` (0-1, times the cue's own); `rate` shifts its pitch (1 = as is).
    play(cue, volume, rate, ac) {
      ac = ac || SFSynth.audio();
      if (!ac || !cue || !cue.layers || !cue.layers.length || live >= MAX_VOICES) return 0;
      const out = ac.createGain();
      out.gain.value = num(volume == null ? 1 : volume, 0, 1, 1) * num(cue.volume, 0, 1, 1);
      out.connect(ac.destination);
      const t0 = ac.currentTime + 0.005;
      let end = t0;
      for (const Ly of cue.layers.slice(0, 4)) end = Math.max(end, layer(ac, Ly, t0, rate || 1, out));
      live++;
      setTimeout(() => { live--; try { out.disconnect(); } catch (e) { /* already gone */ } },
                 Math.max(50, (end - ac.currentTime) * 1000 + 60));  // fmt: skip
      return end - t0;
    },
    audio() {
      if (!SFSynth.ctx) {
        const AC = window.AudioContext || window.webkitAudioContext;
        if (!AC) return null;
        try { SFSynth.ctx = new AC(); } catch (e) { return null; }
      }
      if (SFSynth.ctx.state === 'suspended') SFSynth.ctx.resume().catch(() => {});
      return SFSynth.ctx;
    },
    // Offline: the cue rendered to samples -> {seconds, peak, rms} (tests, the editor's meter).
    render(cue, rate) {
      const OAC = window.OfflineAudioContext || window.webkitOfflineAudioContext;
      const seconds = Math.min(4, 0.1 + Math.max(...(cue.layers || []).map(Ly =>
        num(Ly.delay, 0, 2, 0) + num(Ly.attack, 0.001, 1, 0.005) + num(Ly.hold, 0, 2, 0) + num(Ly.release, 0.005, 2, 0.1)), 0));
      const ac = new OAC(1, Math.ceil(22050 * seconds), 22050);
      const out = ac.createGain();
      out.gain.value = num(cue.volume, 0, 1, 1);
      out.connect(ac.destination);
      for (const Ly of (cue.layers || []).slice(0, 4)) layer(ac, Ly, 0, rate || 1, out);
      return ac.startRendering().then(buf => {
        const d = buf.getChannelData(0);
        let peak = 0, sum = 0;
        for (let i = 0; i < d.length; i++) { const v = Math.abs(d[i]); if (v > peak) peak = v; sum += d[i] * d[i]; }
        return {seconds, peak, rms: Math.sqrt(sum / Math.max(1, d.length))};
      });
    },
  });

  /* ---------- the game's sounds -------------------------------------------------------------- */
  const sf = window.sf;
  const S = window.SF_SOUNDS || null;
  if (!sf) return;  // the sound editor: the synth only
  const sound = (sf.sound = {active: !!S, data: S, count: {}, last: {}});
  sf.soundCount = sound.count;
  if (sf.settings.sound == null) sf.settings.sound = true;
  if (sf.settings.volume == null) sf.settings.volume = 1;
  if (!S) { sound.play = sound.moment = () => null; return; }

  const silent = sf.flags.headless || sf.flags.capture;  // tests and recordings: count only
  const cues = () => (sf.sound.data && sf.sound.data.cues) || [];
  const words = s => String(s || '').toLowerCase().split(/[^a-z0-9]+/).filter(w => w.length > 2);
  let jitterSeed = 777;
  const jitterRand = () => ((jitterSeed = (Math.imul(jitterSeed, 1103515245) + 12345) >>> 0) / 4294967296);
  let frame = 0;

  function fire(cue) {
    // One cue at most every 3 steps: a moment the SDK sees twice (sf.event('jump') and
    // sf.fx('jump') in the same step) plays once.
    if (sound.last[cue.name] != null && frame - sound.last[cue.name] < 3) return null;
    sound.last[cue.name] = frame;
    sound.count[cue.name] = (sound.count[cue.name] || 0) + 1;
    if (silent || !sf.settings.sound) return cue.name;
    const rate = 1 + (jitterRand() * 2 - 1) * (+cue.jitter || 0);
    SFSynth.play(cue, (+sf.sound.data.volume || 0) * num(sf.settings.volume, 0, 1, 1), rate);
    return cue.name;
  }

  // The cue for a moment: among those whose `on` names it (loops excluded), the one sharing a
  // word with what happened, else the one named after the moment, else the first.
  sound.pick = function (moment, hint) {
    const list = cues().filter(c => !c.loop && (c.on || []).includes(moment));
    if (list.length <= 1) return list[0] || null;
    const want = new Set(words(hint));
    if (want.size) {
      const hit = list.find(c => words(c.name).concat(...(c.match || []).map(words)).some(w => want.has(w)));
      if (hit) return hit;
    }
    return list.find(c => c.name === moment) || list[0];
  };
  sound.moment = function (moment, hint) {
    const cue = sound.pick(String(moment), hint);
    return cue ? fire(cue) : null;
  };
  sound.play = function (name) {
    const cue = cues().find(c => c.name === name);
    return cue ? fire(cue) : null;
  };
  sound.toggle = function () {
    const on = !sf.settings.sound;
    if (sf.flow && sf.flow.setSound) sf.flow.setSound(on); else sf.settings.sound = on;
    if (on) SFSynth.audio();
    return on;
  };

  // Moments: every event and every effect the game or the SDK plays.
  const hintOf = data => (data && typeof data === 'object')
    ? Object.values(data).filter(v => typeof v === 'string').join(' ') : '';
  const event = sf.event;
  sf.event = function (name, data) {
    event(name, data);
    try { sound.moment(name, hintOf(data)); } catch (e) { sf.errors.push('sound: ' + e.message); }
  };
  const fx = sf.fx;
  sf.fx = function (name, x, y, target, hint) {
    const n = fx.apply(this, arguments);
    try { sound.moment(name, hint); } catch (e) { sf.errors.push('sound: ' + e.message); }
    return n;
  };

  // Loops: `move` while the player moves (on the ground, for levels as data).
  let lastX = null, lastY = null;
  function moving() {
    const lv = sf.level && sf.level.current;
    if (lv && lv.player && lv.player.body) {
      return !!lv.grounded && Math.abs(lv.player.body.velocity.x) > 10 && !lv.over;
    }
    const st = sf.state || {};
    if (typeof st.x !== 'number' || typeof st.y !== 'number') return false;
    const moved = lastX != null && Math.hypot(st.x - lastX, st.y - lastY) > 0.3;
    lastX = st.x; lastY = st.y;
    return moved;
  }
  const loopAt = {};
  function step() {
    frame++;
    if (sf.paused || !sf.ready) return;
    const loops = cues().filter(c => c.loop);
    if (!loops.length) return;
    const now = sf.clock.now, on = moving();
    for (const c of loops) {
      if ((c.on || []).includes('move') && on) {
        if (loopAt[c.name] == null || now - loopAt[c.name] >= Math.max(0.08, +c.every || 0.3)) {
          loopAt[c.name] = now;
          fire(c);
        }
      } else loopAt[c.name] = null;
    }
  }
  const start = sf.start;
  sf.start = function (opts) {
    start(opts);
    if (sf.game) sf.game.events.on('poststep', step);
  };

  // Browsers allow audio after a gesture; M toggles the sound.
  const wake = () => { if (!silent && sf.settings.sound) SFSynth.audio(); };
  window.addEventListener('pointerdown', wake, true);
  window.addEventListener('keydown', e => {
    if (e.key === 'm' || e.key === 'M') { if (!e.repeat) sound.toggle(); return; }
    wake();
  }, true);
})();
