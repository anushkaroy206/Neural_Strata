/* Neural Strata — backend contract / robustness suite
 *
 *   node tests/contract.js
 *
 * Feeds the dashboard the payloads a REAL backend actually emits — missing
 * fields, nulls, strings-instead-of-numbers, empty history, out-of-range
 * values, NaN, extra nodes, HTTP errors, slow responses — and asserts that
 * nothing throws and no chart renders a bad value.
 *
 * Every failure here is a crash you would otherwise hit on integration day.
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try { ({ JSDOM } = require('jsdom')); }
catch (e) {
  try { ({ JSDOM } = require(path.join(__dirname, 'node_modules/jsdom'))); }
  catch (e2) { console.error('jsdom not found. Run:  cd tests && npm install'); process.exit(2); }
}

const HTML = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

let pass = 0, fail = 0;
function ok(cond, label, extra) {
  if (cond) { pass++; console.log('  OK   ' + label); }
  else { fail++; console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + extra : '')); }
}

/* ------------------------------------------------------------ payloads */
const NODE_OK = () => ({ status: 'NOMINAL', tilt_x: 1.1, displacement_mm: 5.2, acceleration: 0.01 });

const baseTelemetry = () => ({
  system_status: 'NOMINAL',
  ttf_seconds: 'N/A',
  nodes: { node_1: NODE_OK(), node_2: NODE_OK() },
  underground: { route_A_status: 'OPEN', route_B_status: 'OPEN' },
  rfid_log: [{ id: '001', name: 'Deepak', zone: 'Tunnel A', time: '10:00:00' }]
});

const baseExtended = () => ({
  mode: 'LIVE',
  nodes: { node_1: { velocity: 0.1, peak_acceleration: 0.4 },
           node_2: { velocity: 0.0, peak_acceleration: 0.2 } },
  muster: { total: 10, underground: 6, safe: 2 },
  events: [{ time: '10:00:00', level: 'NOMINAL', text: 'boot' }]
});

/* Each case: what a backend might plausibly send that isn't picture-perfect. */
const TELEMETRY_CASES = {
  'happy path': baseTelemetry,

  'ttf as a number': () => ({ ...baseTelemetry(), ttf_seconds: 240 }),
  'ttf null': () => ({ ...baseTelemetry(), ttf_seconds: null }),
  'ttf missing': () => { const t = baseTelemetry(); delete t.ttf_seconds; return t; },
  'ttf as numeric string': () => ({ ...baseTelemetry(), ttf_seconds: '240' }),

  'numbers as strings': () => ({
    ...baseTelemetry(),
    nodes: { node_1: { status: 'NOMINAL', tilt_x: '1.1', displacement_mm: '5.2', acceleration: '0.01' },
             node_2: NODE_OK() }
  }),

  'missing rfid_log': () => { const t = baseTelemetry(); delete t.rfid_log; return t; },
  'rfid_log null': () => ({ ...baseTelemetry(), rfid_log: null }),
  'rfid_log empty': () => ({ ...baseTelemetry(), rfid_log: [] }),
  'rfid entry missing zone': () => ({ ...baseTelemetry(), rfid_log: [{ id: '9', name: 'X', time: '1' }] }),

  'missing underground': () => { const t = baseTelemetry(); delete t.underground; return t; },
  'underground null': () => ({ ...baseTelemetry(), underground: null }),
  'unknown route status': () => ({ ...baseTelemetry(), underground: { route_A_status: 'DEGRADED', route_B_status: 'OPEN' } }),

  'missing node_2': () => { const t = baseTelemetry(); delete t.nodes.node_2; return t; },
  'nodes empty': () => ({ ...baseTelemetry(), nodes: {} }),
  'nodes null': () => ({ ...baseTelemetry(), nodes: null }),
  'extra unknown node': () => { const t = baseTelemetry(); t.nodes.node_3 = NODE_OK(); return t; },

  'node missing acceleration': () => {
    const t = baseTelemetry(); delete t.nodes.node_1.acceleration; return t;
  },
  'node fields null': () => ({
    ...baseTelemetry(),
    nodes: { node_1: { status: 'NOMINAL', tilt_x: null, displacement_mm: null, acceleration: null },
             node_2: NODE_OK() }
  }),
  'NaN acceleration': () => {
    const t = baseTelemetry(); t.nodes.node_1.acceleration = NaN; return t;
  },
  'Infinity displacement': () => {
    const t = baseTelemetry(); t.nodes.node_1.displacement_mm = Infinity; return t;
  },

  'displacement over range': () => {
    const t = baseTelemetry(); t.nodes.node_1.displacement_mm = 5000; return t;
  },
  'negative displacement': () => {
    const t = baseTelemetry(); t.nodes.node_1.displacement_mm = -20; return t;
  },
  'tilt beyond +/-45': () => {
    const t = baseTelemetry(); t.nodes.node_1.tilt_x = 300; t.nodes.node_2.tilt_x = -300; return t;
  },
  'acceleration far over threshold': () => {
    const t = baseTelemetry();
    t.system_status = 'CRITICAL';
    t.nodes.node_1.acceleration = 99; t.nodes.node_1.status = 'CRITICAL';
    return t;
  },

  'missing system_status': () => { const t = baseTelemetry(); delete t.system_status; return t; },
  'lowercase status': () => ({ ...baseTelemetry(), system_status: 'critical' }),
  'unknown status string': () => ({ ...baseTelemetry(), system_status: 'DEGRADED' }),
  'node status unknown': () => {
    const t = baseTelemetry(); t.nodes.node_1.status = 'MAINTENANCE'; return t;
  },

  'completely empty object': () => ({}),
  'null payload': () => null,
};

const EXTENDED_CASES = {
  'happy path': baseExtended,
  'missing muster': () => { const e = baseExtended(); delete e.muster; return e; },
  'muster null': () => ({ ...baseExtended(), muster: null }),
  'muster partial': () => ({ ...baseExtended(), muster: { total: 10 } }),
  'missing events': () => { const e = baseExtended(); delete e.events; return e; },
  'events null': () => ({ ...baseExtended(), events: null }),
  'event missing text': () => ({ ...baseExtended(), events: [{ time: '1', level: 'NOMINAL' }] }),
  'event unknown level': () => ({ ...baseExtended(), events: [{ time: '1', level: 'weird', text: 'x' }] }),
  'event with HTML in text': () => ({ ...baseExtended(), events: [{ time: '1', level: 'NOMINAL', text: '<img src=x onerror=alert(1)>' }] }),
  'missing nodes': () => { const e = baseExtended(); delete e.nodes; return e; },
  'nodes null': () => ({ ...baseExtended(), nodes: null }),
  'velocity as string': () => ({ ...baseExtended(), nodes: { node_1: { velocity: '0.4', peak_acceleration: '1.2' }, node_2: { velocity: 0, peak_acceleration: 0 } } }),
  'velocity null': () => ({ ...baseExtended(), nodes: { node_1: { velocity: null, peak_acceleration: null }, node_2: { velocity: 0, peak_acceleration: 0 } } }),
  'missing mode': () => { const e = baseExtended(); delete e.mode; return e; },
  'unknown extra node': () => { const e = baseExtended(); e.nodes.node_9 = { velocity: 1, peak_acceleration: 1 }; return e; },
  'empty object': () => ({}),
  'null payload': () => null,
};

const HISTORY_CASES = {
  'happy path': () => ({
    node_1: Array.from({ length: 50 }, () => NODE_OK()),
    node_2: Array.from({ length: 50 }, () => NODE_OK())
  }),
  'empty arrays': () => ({ node_1: [], node_2: [] }),
  'empty object': () => ({}),
  'null payload': () => null,
  'one node only': () => ({ node_1: Array.from({ length: 10 }, () => NODE_OK()) }),
  'over 240 samples': () => ({
    node_1: Array.from({ length: 900 }, () => NODE_OK()),
    node_2: Array.from({ length: 900 }, () => NODE_OK())
  }),
  'samples missing fields': () => ({
    node_1: [{ displacement_mm: 5 }, { tilt_x: 1 }, {}],
    node_2: [{}]
  }),
  'samples with nulls': () => ({
    node_1: [{ acceleration: null, displacement_mm: null, tilt_x: null }],
    node_2: []
  }),
  'array instead of object': () => ([{ acceleration: 1 }]),
};

const AI_CASES = {
  'happy path': () => ({ model: 'm', status: 'ONLINE', horizon: '45 min', last_train: 'x', confidence: '90%', note: 'n' }),
  'missing fields': () => ({ model: 'm' }),
  'null payload': () => null,
  'empty object': () => ({}),
  'nulls': () => ({ model: null, status: null, horizon: null, last_train: null, confidence: null, note: null }),
};

/* --------------------------------------------------------------- harness */
function makeCtx(numbers) {
  const PROPS = ['font', 'fillStyle', 'strokeStyle', 'lineWidth', 'globalAlpha', 'textAlign',
    'textBaseline', 'lineCap', 'lineJoin', 'shadowColor', 'shadowBlur', 'lineDashOffset',
    'filter', 'globalCompositeOperation'];
  // any coordinate handed to the 2D API must be finite, or the path silently dies
  const track = (name) => (...args) => {
    for (const a of args) {
      if (typeof a === 'number' && !Number.isFinite(a)) numbers.push(name + '(' + args.join(',') + ')');
    }
  };
  return new Proxy({}, {
    get(t, k) {
      if (k === 'canvas') return { width: 900, height: 520 };
      if (k === 'getImageData' || k === 'createImageData')
        return (a, b, w, h) => ({ width: w || 128, height: h || 128, data: new Uint8ClampedArray((w || 128) * (h || 128) * 4) });
      if (k === 'createPattern') return () => ({});
      if (k === 'createLinearGradient' || k === 'createRadialGradient')
        return (...a) => { track('gradient')(...a); return { addColorStop() {} }; };
      if (k === 'measureText') return () => ({ width: 20 });
      if (k === 'setLineDash') return () => {};
      if (PROPS.includes(k)) return t[k] !== undefined ? t[k] : '';
      if (['moveTo', 'lineTo', 'arc', 'rect', 'fillRect', 'strokeRect', 'ellipse',
           'fillText', 'strokeText', 'roundRect', 'quadraticCurveTo', 'bezierCurveTo',
           'translate', 'scale', 'setTransform', 'clearRect'].includes(k)) return track(k);
      return () => {};
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}

function boot(routes, { settle = 3500 } = {}) {
  const errors = [];
  const badNumbers = [];
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://localhost/',
    beforeParse(w) {
      w.HTMLCanvasElement.prototype.getContext = () => makeCtx(badNumbers);
      w.Element.prototype.scrollIntoView = function () {};
      w.HTMLElement.prototype.getBoundingClientRect = () =>
        ({ width: 900, height: 520, top: 0, left: 0, right: 900, bottom: 520, x: 0, y: 0 });
      w.fetch = (url) => {
        const u = String(url);
        const key = Object.keys(routes).find(k => u.includes(k));
        if (!key) return Promise.reject(new Error('offline'));
        const r = routes[key];
        if (r === 'error') return Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error('no json')) });
        if (r === 'badjson') return Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError('Unexpected token')) });
        if (r === 'html') return Promise.resolve({ ok: true, status: 200, json: () => Promise.reject(new SyntaxError('Unexpected token < in JSON')) });
        return Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(typeof r === 'function' ? r() : r) });
      };
      w.AudioContext = w.webkitAudioContext = function () {
        const p = () => ({ value: 0, setValueAtTime() {}, setTargetAtTime() {}, linearRampToValueAtTime() {}, cancelScheduledValues() {}, exponentialRampToValueAtTime() {} });
        return { state: 'running', resume() {}, currentTime: 0, destination: {},
          createOscillator: () => ({ type: '', frequency: p(), connect() {}, start() {}, stop() {}, disconnect() {} }),
          createGain: () => ({ gain: p(), connect() {}, disconnect() {} }) };
      };
      w.addEventListener('error', e => errors.push(String(e.message)));
      w.addEventListener('unhandledrejection', e => {
        const r = (e.reason && e.reason.message) || e.reason;
        if (!/offline|abort/i.test(String(r))) errors.push('REJ ' + r);
      });
    }
  });
  return new Promise(res => setTimeout(() => res({ w: dom.window, d: dom.window.document, errors, badNumbers }), settle));
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

/* Look for the tell-tale strings a broken numeric pipeline paints.
 * Only *visible* text counts — body.textContent would otherwise include the
 * page's own <script> source and match every token. */
function visibleText(d) {
  let out = '';
  const walk = (el) => {
    for (const n of el.childNodes) {
      if (n.nodeType === 3) { out += n.nodeValue + '\n'; continue; }
      if (n.nodeType !== 1) continue;
      const tag = n.tagName;
      if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'TEMPLATE' || tag === 'NOSCRIPT') continue;
      walk(n);
    }
  };
  walk(d.body);
  return out;
}
function domRot(d) {
  const txt = visibleText(d);
  const bad = ['NaN', 'undefined', 'Infinity', '[object Object]'];
  const hits = bad.filter(b => txt.includes(b));
  // bare "null" as a whole word is also a leaked value
  if (/(^|[\s>:,(])null([\s<,.)]|$)/.test(txt)) hits.push('null');
  return hits;
}
function styleRot(d) {
  // widths like "NaN%" silently collapse bars
  const out = [];
  d.querySelectorAll('[style]').forEach(el => {
    const s = el.getAttribute('style') || '';
    if (/NaN|undefined|Infinity/.test(s)) out.push(el.id || el.className);
  });
  return out;
}

/* ----------------------------------------------------------------- suites */
async function suiteTelemetry() {
  console.log('\n[A] GET /api/telemetry — malformed & edge payloads');
  for (const [name, make] of Object.entries(TELEMETRY_CASES)) {
    const { w, d, errors, badNumbers } = await boot({
      '/api/telemetry': make, '/api/extended': baseExtended,
      '/api/history': HISTORY_CASES['happy path'], '/api/ai': AI_CASES['happy path']
    });
    const rot = domRot(d), srot = styleRot(d);
    const clean = errors.length === 0 && rot.length === 0 && srot.length === 0 && badNumbers.length === 0;
    ok(clean, 'telemetry: ' + name,
      [errors[0], rot.length && 'text:' + rot, srot.length && 'style:' + srot[0],
       badNumbers.length && 'canvas:' + badNumbers[0]].filter(Boolean).join(' | '));
    w.close();
  }
}

async function suiteExtended() {
  console.log('\n[B] GET /api/extended — malformed & edge payloads');
  for (const [name, make] of Object.entries(EXTENDED_CASES)) {
    const { w, d, errors, badNumbers } = await boot({
      '/api/telemetry': baseTelemetry, '/api/extended': make,
      '/api/history': HISTORY_CASES['happy path'], '/api/ai': AI_CASES['happy path']
    });
    const rot = domRot(d), srot = styleRot(d);
    const clean = errors.length === 0 && rot.length === 0 && srot.length === 0 && badNumbers.length === 0;
    ok(clean, 'extended: ' + name,
      [errors[0], rot.length && 'text:' + rot, srot.length && 'style:' + srot[0],
       badNumbers.length && 'canvas:' + badNumbers[0]].filter(Boolean).join(' | '));
    w.close();
  }
}

async function suiteHistory() {
  console.log('\n[C] GET /api/history — backfill shapes (drives the charts)');
  for (const [name, make] of Object.entries(HISTORY_CASES)) {
    const { w, d, errors, badNumbers } = await boot({
      '/api/telemetry': baseTelemetry, '/api/extended': baseExtended,
      '/api/history': make, '/api/ai': AI_CASES['happy path']
    });
    // force every chart to actually paint
    w.__NS.show('telemetry');
    await sleep(500);
    const rot = domRot(d);
    const clean = errors.length === 0 && badNumbers.length === 0 && rot.length === 0;
    ok(clean, 'history: ' + name,
      [errors[0], badNumbers.length && 'canvas:' + badNumbers[0], rot.length && 'text:' + rot].filter(Boolean).join(' | '));
    w.close();
  }
}

async function suiteAi() {
  console.log('\n[D] GET /api/ai + forecast charts');
  for (const [name, make] of Object.entries(AI_CASES)) {
    const { w, d, errors, badNumbers } = await boot({
      '/api/telemetry': baseTelemetry, '/api/extended': baseExtended,
      '/api/history': HISTORY_CASES['happy path'], '/api/ai': make
    });
    w.__NS.show('ai');
    await sleep(500);
    const rot = domRot(d);
    const clean = errors.length === 0 && badNumbers.length === 0 && rot.length === 0;
    ok(clean, 'ai: ' + name,
      [errors[0], badNumbers.length && 'canvas:' + badNumbers[0], rot.length && 'text:' + rot].filter(Boolean).join(' | '));
    w.close();
  }
}

async function suiteTransport() {
  console.log('\n[E] transport failures');
  const modes = { 'HTTP 500': 'error', 'invalid JSON': 'badjson', 'HTML error page': 'html' };
  for (const [name, mode] of Object.entries(modes)) {
    const { w, d, errors, badNumbers } = await boot({
      '/api/telemetry': mode, '/api/extended': mode, '/api/history': mode, '/api/ai': mode
    });
    const rot = domRot(d);
    const clean = errors.length === 0 && badNumbers.length === 0 && rot.length === 0;
    ok(clean, 'all endpoints ' + name + ' -> falls back to SIM',
      [errors[0], rot.length && 'text:' + rot].filter(Boolean).join(' | '));
    ok(d.getElementById('tkMode').textContent !== 'LIVE', '  ' + name + ': mode is not LIVE');
    w.close();
  }

  // partial outage: telemetry fine, everything else broken
  const { w, d, errors } = await boot({
    '/api/telemetry': baseTelemetry, '/api/extended': 'error',
    '/api/history': 'error', '/api/ai': 'error'
  });
  ok(errors.length === 0 && domRot(d).length === 0, 'partial outage (only /api/telemetry alive)', errors[0]);
  w.close();
}

async function suiteViewsUnderLoad() {
  console.log('\n[F] every view renders against a hostile payload');
  const nasty = () => {
    const t = baseTelemetry();
    t.system_status = 'CRITICAL';
    t.nodes.node_1 = { status: 'CRITICAL', tilt_x: 999, displacement_mm: 1e6, acceleration: NaN };
    delete t.nodes.node_2;
    t.rfid_log = null;
    t.ttf_seconds = null;
    return t;
  };
  const { w, d, errors, badNumbers } = await boot({
    '/api/telemetry': nasty, '/api/extended': () => ({}), '/api/history': () => ({}), '/api/ai': () => ({})
  });
  for (const v of ['overview', 'telemetry', 'control', 'ai', 'records', 'evacmap']) {
    w.__NS.show(v);
    await sleep(320);
    ok(errors.length === 0 && badNumbers.length === 0, 'view "' + v + '" survives hostile payload',
      [errors[0], badNumbers[0]].filter(Boolean).join(' | '));
  }
  // and the per-cell detail views, which interpolate from the nodes
  for (const id of ['E1', 'E5', 'E8', 'E12']) {
    w.__NS.showCell(id);
    await sleep(160);
    const big = d.getElementById('ndBig').textContent;
    const vals = ['ndD', 'ndT', 'ndA'].map(x => d.getElementById(x).textContent);
    ok(big === id && !vals.some(v => /NaN|undefined|Infinity/.test(v)),
      'cell ' + id + ' detail renders clean numbers', vals.join('/'));
  }
  ok(errors.length === 0, 'no page errors across all views', errors[0]);
  w.close();
}

async function suiteLiveTransition() {
  console.log('\n[G] SIM -> LIVE handover and chart continuity');
  let live = false;
  const routes = {
    '/api/telemetry': () => { if (!live) throw new Error('offline'); return baseTelemetry(); },
    '/api/extended': () => { if (!live) throw new Error('offline'); return baseExtended(); },
    '/api/history': () => { if (!live) throw new Error('offline'); return HISTORY_CASES['happy path'](); },
    '/api/ai': () => { if (!live) throw new Error('offline'); return AI_CASES['happy path'](); },
  };
  const { w, d, errors, badNumbers } = await boot(routes, { settle: 3000 });
  ok(d.getElementById('tkMode').textContent !== 'LIVE', 'starts in SIM while backend is down');

  live = true;                       // backend comes up mid-flight
  await sleep(6000);                 // allow the backoff timer to retry
  ok(d.getElementById('tkMode').textContent === 'LIVE', 'flips to LIVE once the backend answers',
    d.getElementById('tkMode').textContent);
  w.__NS.show('telemetry');
  await sleep(500);
  ok(errors.length === 0 && badNumbers.length === 0, 'no errors across the handover',
    [errors[0], badNumbers[0]].filter(Boolean).join(' | '));

  live = false;                      // backend dies again
  await sleep(3000);
  ok(errors.length === 0, 'no errors when the backend drops out again', errors[0]);
  w.close();
}

async function suiteFuzz() {
  console.log('\n[H] randomised payload fuzzing (one page, many polls)');
  const JUNK = [undefined, null, NaN, Infinity, -Infinity, '', 'n/a', '12.5', -1e9, 1e9,
                0, {}, [], true, '<b>x</b>', '\u0000'];
  const pick = a => a[Math.floor(Math.random() * a.length)];
  const maybe = (good) => Math.random() < 0.45 ? pick(JUNK) : good;
  let n = 0;
  const fuzzTelemetry = () => {
    n++;
    return {
      system_status: maybe(pick(['NOMINAL', 'WARNING', 'CRITICAL'])),
      ttf_seconds: maybe(Math.random() * 600),
      nodes: Math.random() < 0.1 ? maybe({}) : {
        node_1: Math.random() < 0.12 ? maybe({}) : {
          status: maybe(pick(['NOMINAL', 'WARNING', 'CRITICAL'])),
          tilt_x: maybe((Math.random() - 0.5) * 200),
          displacement_mm: maybe(Math.random() * 400),
          acceleration: maybe(Math.random() * 8 - 1)
        },
        node_2: Math.random() < 0.12 ? maybe({}) : {
          status: maybe(pick(['NOMINAL', 'WARNING', 'CRITICAL'])),
          tilt_x: maybe((Math.random() - 0.5) * 200),
          displacement_mm: maybe(Math.random() * 400),
          acceleration: maybe(Math.random() * 8 - 1)
        }
      },
      underground: Math.random() < 0.15 ? maybe({}) :
        { route_A_status: maybe(pick(['OPEN', 'BLOCKED'])), route_B_status: maybe(pick(['OPEN', 'BLOCKED'])) },
      rfid_log: Math.random() < 0.15 ? maybe([]) :
        Array.from({ length: Math.floor(Math.random() * 12) }, (_, i) => Math.random() < 0.1 ? pick(JUNK) : ({
          id: maybe('0' + i), name: maybe('Crew ' + i),
          zone: maybe(pick(['Tunnel A', 'Tunnel B', 'Evacuating A', 'Evacuating B', 'Safe Exit', 'Headframe'])),
          time: maybe('10:00:0' + (i % 10))
        }))
    };
  };
  const fuzzExtended = () => ({
    mode: maybe('LIVE'),
    nodes: Math.random() < 0.15 ? maybe({}) :
      { node_1: { velocity: maybe(Math.random()), peak_acceleration: maybe(Math.random() * 5) },
        node_2: { velocity: maybe(Math.random()), peak_acceleration: maybe(Math.random() * 5) } },
    muster: Math.random() < 0.15 ? maybe({}) :
      { total: maybe(10), underground: maybe(6), safe: maybe(3) },
    events: Math.random() < 0.15 ? maybe([]) :
      Array.from({ length: 3 }, (_, i) => ({ time: maybe('10:0' + i), level: maybe('WARNING'), text: maybe('event ' + i) }))
  });

  const { w, d, errors, badNumbers } = await boot({
    '/api/telemetry': fuzzTelemetry, '/api/extended': fuzzExtended,
    '/api/history': HISTORY_CASES['samples missing fields'], '/api/ai': AI_CASES['missing fields']
  }, { settle: 2500 });

  // cycle every view while the junk keeps arriving at the normal poll rate
  const views = ['overview', 'telemetry', 'control', 'ai', 'records', 'evacmap'];
  for (let i = 0; i < 18; i++) {
    w.__NS.show(views[i % views.length]);
    if (i % 3 === 0) w.__NS.showCell('E' + (1 + (i % 12)));
    await sleep(400);
  }
  w.__NS.show('overview');
  await sleep(400);

  ok(errors.length === 0, 'fuzz: no page errors over ' + n + ' random payloads', errors.slice(0, 3).join(' | '));
  ok(badNumbers.length === 0, 'fuzz: no non-finite value ever reached a canvas call',
    badNumbers.slice(0, 3).join(' | '));
  const rot = domRot(d), srot = styleRot(d);
  ok(rot.length === 0, 'fuzz: no NaN/undefined leaked into visible text', 'text:' + rot);
  ok(srot.length === 0, 'fuzz: no NaN/undefined leaked into inline styles', 'style:' + srot[0]);
  w.close();
}

/* -------------------------------------------------------------------- run */
(async () => {
  const only = process.argv[2];
  const suites = {
    telemetry: suiteTelemetry, extended: suiteExtended, history: suiteHistory,
    ai: suiteAi, transport: suiteTransport, views: suiteViewsUnderLoad,
    live: suiteLiveTransition, fuzz: suiteFuzz
  };
  for (const n of (only ? [only] : Object.keys(suites))) {
    if (!suites[n]) { console.error('unknown suite: ' + n); process.exit(2); }
    try { await suites[n](); }
    catch (e) { fail++; console.log('  FAIL suite "' + n + '" threw: ' + e.message); }
  }
  console.log('\n---------------------------------------------');
  console.log('assertions: ' + pass + ' passed, ' + fail + ' failed');
  process.exit(fail ? 1 : 0);
})();
