/* The sound editor (sounds.html): every cue of window.SF_SOUNDS, playable and editable.

   Served by the local app (/api/runs/<id>/play/v<N>/sounds.html) it saves to the run: POST
   /api/runs/<id>/sounds/v<N> makes a new locked sounds version and rewrites this build's
   sounds.js. Opened from a folder it downloads the edited sounds.js to put in its place. */
(function () {
  'use strict';
  const data = window.SF_SOUNDS || {cues: [], presets: {}, moments: {}, volume: 0.8, style: ''};
  const presets = data.presets || {};
  const moments = data.moments || {};
  const $ = id => document.getElementById(id);
  const clone = o => JSON.parse(JSON.stringify(o));
  const saved = () => clone({style: data.style || '', volume: data.volume, cues: data.cues || []});
  let state = saved(), base = saved();

  const run = location.pathname.match(/\/api\/runs\/([^/]+)\/play\/v(\d+)\//);
  const saveUrl = run ? '/api/runs/' + run[1] + '/sounds/v' + run[2] : null;
  $('save').hidden = !saveUrl;
  $('title').textContent = (data.title ? data.title + ' · ' : '') + 'Sounds';
  document.title = $('title').textContent;

  const FIELDS = [  // [key, label, min, max, step]
    ['freq', 'pitch start (Hz)', 20, 12000, 1], ['freq_end', 'pitch end (Hz)', 20, 12000, 1],
    ['attack', 'attack (s)', 0, 1, 0.005], ['hold', 'hold (s)', 0, 2, 0.01],
    ['release', 'release (s)', 0.005, 2, 0.01], ['delay', 'delay (s)', 0, 2, 0.01],
    ['volume', 'volume', 0, 1, 0.05], ['cutoff', 'filter start (Hz)', 40, 16000, 10],
    ['cutoff_end', 'filter end (Hz)', 40, 16000, 10], ['q', 'filter Q', 0.1, 20, 0.1],
    ['wobble', 'wobble (Hz)', 0, 60, 0.5], ['wobble_depth', 'wobble depth', 0, 1, 0.01],
  ];
  const WAVES = ['square', 'triangle', 'sine', 'sawtooth', 'noise'];
  const FILTERS = ['none', 'lowpass', 'highpass', 'bandpass'];

  function status(text, bad) {
    $('status').textContent = text;
    $('status').className = 'status' + (bad ? ' bad' : '');
  }
  const el = (tag, props, ...kids) => {
    const e = Object.assign(document.createElement(tag), props || {});
    for (const k of kids) if (k != null) e.append(k);
    return e;
  };
  function select(options, value, onChange) {
    const s = el('select');
    for (const o of options) s.add(new Option(o.label || o, o.value == null ? o : o.value));
    s.value = value;
    s.onchange = () => onChange(s.value);
    return s;
  }
  function number(value, min, max, step, onChange) {
    const i = el('input', {type: 'number', min, max, step, value});
    i.oninput = () => { const v = parseFloat(i.value); if (isFinite(v)) onChange(Math.max(min, Math.min(max, v))); };
    return i;
  }
  function slider(value, onChange, min, max, step) {
    const i = el('input', {type: 'range', min: min == null ? 0 : min, max: max == null ? 1 : max,
                           step: step || 0.05, value});  // fmt: skip
    i.oninput = () => onChange(parseFloat(i.value));
    return i;
  }
  const play = cue => {
    if (!window.SFSynth.audio()) return status('This browser has no Web Audio', true);
    window.SFSynth.play(cue, state.volume);
  };
  const layersOf = (name, pitch, length) => clone((presets[name] || {}).layers || []).map(Ly => {
    for (const k of ['freq', 'freq_end', 'cutoff', 'cutoff_end']) Ly[k] *= pitch || 1;
    for (const k of ['attack', 'hold', 'release', 'delay']) Ly[k] *= length || 1;
    return Ly;
  });
  function changed(i) {
    const card = $('cues').children[i];
    if (card) card.classList.toggle('dirty', JSON.stringify(state.cues[i]) !== JSON.stringify(base.cues[i]));
  }

  function card(cue, i) {
    const set = (k, v, redraw) => { cue[k] = v; changed(i); if (redraw) render(); };
    const box = el('section', {className: 'cue', ariaLabel: 'Sound ' + cue.name});
    const name = el('input', {type: 'text', className: 'name', value: cue.name, ariaLabel: 'Name'});
    name.oninput = () => set('name', name.value.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_'));
    const on = el('input', {type: 'text', className: 'on', value: (cue.on || []).join(', ')});
    on.setAttribute('list', 'moment-list');
    on.oninput = () => {
      const list = on.value.split(',').map(s => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, '_')).filter(Boolean);
      cue.on = list;
      cue.loop = list.includes('move');
      changed(i);
      loopBox.hidden = !cue.loop;
    };
    const presetPick = select([{label: '— custom —', value: ''}, ...Object.keys(presets)], cue.preset || '', v => {
      if (!v) return;
      cue.layers = layersOf(v);
      cue.preset = v;
      if (presets[v].every) cue.every = presets[v].every;
      changed(i);
      render();
      play(cue);
    });
    const tweak = (label, fn) => el('button', {type: 'button', textContent: label, onclick: () => {
      cue.layers.forEach(fn);
      changed(i);
      render();
      play(cue);
    }});  // fmt: skip
    const pitch = f => Ly => { Ly.freq *= f; Ly.freq_end *= f; Ly.cutoff *= f; Ly.cutoff_end *= f; };
    const stretch = f => Ly => { for (const k of ['attack', 'hold', 'release', 'delay']) Ly[k] *= f; };
    const loopBox = el('label', {hidden: !cue.loop}, 'repeat every (s) ',
                       number(cue.every || 0.3, 0.08, 2, 0.01, v => set('every', v)));  // fmt: skip
    box.append(
      el('div', {className: 'row'},
        el('button', {type: 'button', className: 'play', textContent: '▶', title: 'Play', onclick: () => play(cue)}),
        name,
        el('label', null, 'plays on ', on),
        loopBox,
        el('label', null, 'preset ', presetPick)),
      el('div', {className: 'row'},
        el('input', {type: 'text', className: 'what', value: cue.what || '', placeholder: 'what it sounds like',
                     oninput: e => set('what', e.target.value)}),
        el('label', null, 'volume ', slider(cue.volume == null ? 1 : cue.volume, v => set('volume', v))),
        el('label', null, 'variation ', slider(cue.jitter || 0, v => set('jitter', v), 0, 0.3, 0.01))),
      el('div', {className: 'row tweaks'},
        tweak('Higher', pitch(1.25)), tweak('Lower', pitch(0.8)), tweak('Shorter', stretch(0.8)),
        tweak('Longer', stretch(1.25)),
        el('button', {type: 'button', textContent: 'Duplicate', onclick: () => {
          const copy = clone(cue);
          copy.name = cue.name + '_copy';
          state.cues.splice(i + 1, 0, copy);
          render();
        }}),
        el('button', {type: 'button', textContent: 'Delete', onclick: () => {
          if (!confirm('Delete the sound "' + cue.name + '"?')) return;
          state.cues.splice(i, 1);
          render();
        }})),
      layerEditor(cue, i));
    return box;
  }

  function layerEditor(cue, i) {
    const box = el('div', {className: 'layers'});
    cue.layers.forEach((Ly, j) => {
      const row = el('div', {className: 'layer'});
      const custom = () => changed(i);  // the preset stays what it started from
      row.append(
        el('label', null, 'wave', select(WAVES, Ly.wave, v => { Ly.wave = v; custom(); })),
        el('label', null, 'filter', select(FILTERS, Ly.filter || 'none', v => { Ly.filter = v; custom(); })));
      for (const [k, label, min, max, step] of FIELDS) {
        row.append(el('label', null, label, number(Ly[k] == null ? 0 : Ly[k], min, max, step, v => { Ly[k] = v; custom(); })));
      }
      row.append(el('div', {className: 'tools'},
        el('button', {type: 'button', textContent: '▶ this layer', onclick: () => play(Object.assign({}, cue, {layers: [Ly]}))}),
        el('button', {type: 'button', textContent: 'Remove layer', disabled: cue.layers.length <= 1, onclick: () => {
          cue.layers.splice(j, 1); custom(); render();
        }})));
      box.append(row);
    });
    const add = el('button', {type: 'button', textContent: '+ Add a layer', disabled: cue.layers.length >= 4, onclick: () => {
      cue.layers.push(clone(cue.layers[cue.layers.length - 1] || layersOf('blip')[0]));
      changed(i);
      render();
    }});
    return el('details', {open: !!openLayers[cue.name], ontoggle: e => { openLayers[cue.name] = e.target.open; }},
              el('summary', null, 'Layers (' + cue.layers.length + ')'), box, add);  // fmt: skip
  }
  const openLayers = {};

  function render() {
    $('master').value = state.volume;
    $('style').value = state.style || '';
    const list = $('cues');
    list.replaceChildren(...state.cues.map(card));
    state.cues.forEach((_, i) => changed(i));
  }
  function payload() { return {style: state.style, volume: state.volume, cues: state.cues}; }

  $('master').oninput = e => { state.volume = parseFloat(e.target.value); };
  $('style').oninput = e => { state.style = e.target.value; };
  $('play-all').onclick = () => {
    state.cues.forEach((c, i) => setTimeout(() => play(c), i * 650));
  };
  $('reset').onclick = () => { state = clone(base); render(); status('Back to the saved sounds'); };
  $('add').onclick = () => {
    const names = new Set(state.cues.map(c => c.name));
    let n = 1;
    while (names.has('new_sound_' + n)) n++;
    state.cues.push({name: 'new_sound_' + n, on: ['menu_select'], what: '', preset: 'blip',
                     layers: layersOf('blip'), volume: 1, loop: false, every: 0.3, jitter: 0.04, match: []});  // fmt: skip
    render();
  };
  $('download').onclick = () => {
    const out = Object.assign({}, data, payload());
    const blob = new Blob(['window.SF_SOUNDS = ' + JSON.stringify(out, null, 1) + ';\n'], {type: 'text/javascript'});
    const a = el('a', {href: URL.createObjectURL(blob), download: 'sounds.js'});
    document.body.append(a);
    a.click();
    a.remove();
    status('Downloaded: put sounds.js next to index.html (replacing the old one) and reload the game');
  };
  $('save').onclick = async () => {
    $('save').disabled = true;
    status('Saving…');
    try {
      const r = await fetch(saveUrl, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                      body: JSON.stringify({sounds: payload()})});  // fmt: skip
      const answer = await r.json();
      if (!r.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : JSON.stringify(answer.detail));
      Object.assign(data, answer.script);
      state = saved();
      base = saved();
      render();
      const extra = (answer.notes || []).concat(answer.problems || []);
      status('Saved as sounds v' + answer.version + '. Reload the game to hear it.' + (extra.length ? ' Code: ' + extra.join('; ') : ''));
    } catch (e) {
      status('Not saved: ' + e.message, true);
    } finally {
      $('save').disabled = false;
    }
  };

  const ml = $('moment-list'), mb = $('moments');
  for (const [m, what] of Object.entries(moments)) {
    ml.append(el('option', {value: m}));
    mb.append(el('div', null, el('code', null, m), ' ', what));
  }
  render();
  window.sfEditor = {state: () => state, payload, render};
})();
