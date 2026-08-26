/* Neural Strata — headless regression suite (JSDOM)
 *
 *   cd tests && npm install     # one-time, installs jsdom
 *   node tests/regression.js    # run from repo root
 *
 * Covers: boot, mesh-cell pointing (round 4), cell detail views (round 3),
 * command centre + scenario console (round 2), keyboard shortcuts,
 * offline polling backoff, canvas paint assertions and render-loop cost.
 */
const fs = require('fs');
const path = require('path');

let JSDOM;
try {
  ({ JSDOM } = require('jsdom'));
} catch (e) {
  try { ({ JSDOM } = require(path.join(__dirname, 'node_modules/jsdom'))); }
  catch (e2) {
    console.error('jsdom not found. Run:  cd tests && npm install');
    process.exit(2);
  }
}

const HTML = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');

/* ---------------------------------------------------------------- harness */
const errors = [];
let pass = 0, fail = 0;

function ok(cond, label, extra) {
  if (cond) { pass++; console.log('  OK   ' + label); }
  else { fail++; console.log('  FAIL ' + label + (extra !== undefined ? '  -> ' + extra : '')); }
}
const eq = (got, want, label) => ok(got === want, label, JSON.stringify(got) + ' != ' + JSON.stringify(want));

/* A 2D context stub that records the text it is asked to paint. */
function makeCtx(sink, counters, canvas) {
  const PROPS = ['font', 'fillStyle', 'strokeStyle', 'lineWidth', 'globalAlpha', 'textAlign',
    'textBaseline', 'lineCap', 'lineJoin', 'shadowColor', 'shadowBlur', 'lineDashOffset',
    'filter', 'globalCompositeOperation'];
  return new Proxy({}, {
    get(t, k) {
      if (k === 'canvas') return canvas || { width: 900, height: 520 };
      if (k === 'fillText' || k === 'strokeText') return (s) => { sink.push(String(s)); };
      if (k === 'getImageData' || k === 'createImageData')
        return (a, b, w, h) => { w = w || 128; h = h || 128; return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; };
      if (k === 'createPattern') return () => { counters.pattern++; return {}; };
      if (k === 'createLinearGradient' || k === 'createRadialGradient')
        return () => ({ addColorStop() {} });
      if (k === 'measureText') return () => ({ width: 20 });
      if (PROPS.includes(k)) return t[k] !== undefined ? t[k] : '';
      return () => {};
    },
    set(t, k, v) { t[k] = v; return true; }
  });
}

function boot({ fetchImpl, onReady, timeout = 4000 }) {
  const painted = [];
  const counters = { pattern: 0, getContext: 0, resize: 0, fetch: 0 };

  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously',
    pretendToBeVisual: true,
    url: 'http://localhost/',
    beforeParse(w) {
      const CE = w.HTMLCanvasElement;
      Object.defineProperty(CE.prototype, 'width', {
        configurable: true,
        get() { return this.__w === undefined ? 300 : this.__w; },
        set(v) { if (this.__w !== v) counters.resize++; this.__w = v; }
      });
      Object.defineProperty(CE.prototype, 'height', {
        configurable: true,
        get() { return this.__h === undefined ? 150 : this.__h; },
        set(v) { this.__h = v; }
      });
      CE.prototype.getContext = function () { counters.getContext++; return makeCtx(painted, counters, this); };

      w.Element.prototype.scrollIntoView = function () {};
      w.HTMLElement.prototype.getBoundingClientRect = function () {
        return { width: 900, height: 520, top: 0, left: 0, right: 900, bottom: 520, x: 0, y: 0 };
      };
      w.fetch = (...a) => { counters.fetch++; return (fetchImpl || (() => Promise.reject(new Error('offline'))))(...a); };
      w.AudioContext = w.webkitAudioContext = function () {
        const param = () => ({ value: 0, setValueAtTime() {}, setTargetAtTime() {}, linearRampToValueAtTime() {}, cancelScheduledValues() {}, exponentialRampToValueAtTime() {} });
        return {
          state: 'running', resume() {}, currentTime: 0, destination: {},
          createOscillator: () => ({ type: '', frequency: param(), connect() {}, start() {}, stop() {}, disconnect() {} }),
          createGain: () => ({ gain: param(), connect() {}, disconnect() {} })
        };
      };
      w.addEventListener('error', e => errors.push('ERR ' + e.message));
      w.addEventListener('unhandledrejection', e => {
        const r = (e.reason && e.reason.message) || e.reason;
        if (!/offline|aborted|Abort/i.test(String(r))) errors.push('REJ ' + r);
      });
    }
  });

  return new Promise(res => setTimeout(() => res({ dom, w: dom.window, d: dom.window.document, painted, counters }), timeout));
}

const key = (w, d, k) => d.dispatchEvent(new w.KeyboardEvent('keydown', { key: k, bubbles: true }));
const click = (w, el) => el.dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* ------------------------------------------------------------------ suites */
async function suiteBoot() {
  console.log('\n[1] boot / structure (offline)');
  const { w, d, counters } = await boot({});
  ok(!!w.SURF, 'window.SURF built');
  ok(!!w.UG, 'window.UG built');
  ok(!!w.__NS, 'window.__NS exported');
  eq(d.querySelectorAll('#cells .cell').length, 12, '12 mesh cells');
  eq(d.querySelectorAll('#cells .cell')[0].tagName, 'BUTTON', 'cells are buttons');
  ok(!d.querySelector('.title h2'), 'legacy .title h2 removed');
  eq(/raniganj/i.test(d.body.innerHTML), false, 'no "Raniganj" in markup');
  eq(/panel 3b/i.test(d.body.innerHTML), false, 'no "Panel 3B" in markup');
  ok(!!d.getElementById('ccNode'), 'command centre node present');
  ok(!!d.getElementById('cellBar'), 'cell selection strip present');
  ok(!!d.getElementById('ndLocate'), '"Show on model" button present');
  ok(d.querySelector('.cellcap').textContent.includes('CLICK TO POINT'), 'mesh caption present');
  ok(d.querySelector('.hint').textContent.includes('MESH CELL'), 'hint documents [ ]');
  ok(counters.fetch > 0, 'polling attempted');
  w.close();
}

async function suiteCellPointing() {
  console.log('\n[2] round 4 — pointing at a cell on the model');
  const { w, d } = await boot({});
  const cells = d.querySelectorAll('#cells .cell');

  let allPoint = true;
  for (let i = 0; i < 12; i++) {
    click(w, cells[i]);
    if (w.SURF.api.sel !== i || w.UG.api.sel !== i) allPoint = false;
    if (d.getElementById('v-node').classList.contains('on')) allPoint = false;
  }
  ok(allPoint, 'all 12 cells point on both models, detail stays closed');

  w.__NS.clearCellPointer();
  click(w, cells[6]);
  eq(d.getElementById('cellBar').classList.contains('on'), true, 'selection strip shows');
  eq(cells[6].classList.contains('act'), true, 'selected cell marked .act');
  click(w, cells[6]);
  await sleep(120);
  eq(d.getElementById('v-node').classList.contains('on'), true, 'second click opens detail');
  eq(d.getElementById('ndBig').textContent, 'E7', 'detail shows E7');

  click(w, d.getElementById('ndLocate'));
  await sleep(120);
  eq(d.getElementById('v-node').classList.contains('on'), false, '"Show on model" closes detail');
  eq(w.SURF.api.sel, 6, 'pointer kept after locate');

  w.__NS.setMode('underground');
  await sleep(120);
  eq(w.UG.api.sel, 6, 'pointer carries into underground');
  w.__NS.setMode('surface');

  click(w, d.getElementById('cbClear'));
  eq(w.SURF.api.sel, null, 'clear nulls SURF pointer');
  eq(w.UG.api.sel, null, 'clear nulls UG pointer');
  w.close();
}

async function suiteKeyboard() {
  console.log('\n[3] keyboard');
  const { w, d } = await boot({});
  key(w, d, ']'); eq(w.__NS.selCell, 'E1', '] selects E1 from empty');
  key(w, d, ']'); key(w, d, ']'); eq(w.__NS.selCell, 'E3', '] advances');
  key(w, d, '['); eq(w.__NS.selCell, 'E2', '[ steps back');
  w.__NS.pointAtCell('E1'); key(w, d, '['); eq(w.__NS.selCell, 'E12', '[ wraps E1 -> E12');
  key(w, d, ']'); eq(w.__NS.selCell, 'E1', '] wraps E12 -> E1');

  w.__NS.showCell('E2'); await sleep(60);
  key(w, d, ']');
  eq(d.getElementById('ndBig').textContent, 'E3', '] pages the open detail view');
  key(w, d, 'Escape');
  eq(d.getElementById('v-node').classList.contains('on'), false, 'Esc closes detail');
  eq(w.__NS.selCell, 'E3', 'Esc from detail keeps pointer');
  key(w, d, 'Escape');
  eq(w.__NS.selCell, null, 'Esc on overview clears pointer');

  key(w, d, '2'); await sleep(60);
  eq(d.getElementById('v-telemetry').classList.contains('on'), true, '"2" opens telemetry');
  key(w, d, 'Escape'); await sleep(60);
  key(w, d, 'u'); await sleep(60);
  eq(d.body.classList.contains('ug'), true, '"u" switches underground');
  key(w, d, 's'); await sleep(60);
  eq(d.body.classList.contains('ug'), false, '"s" switches surface');
  w.close();
}

async function suiteDetail() {
  console.log('\n[4] round 3 — per-cell detail readings');
  const { w, d } = await boot({});
  const open = id => { w.__NS.showCell(id); return d.getElementById('ndBig').textContent; };
  eq(open('E5'), 'E5', 'E5 detail opens');
  eq(d.getElementById('ndBadge').textContent.trim(), 'NOMINAL', 'E5 badge NOMINAL (instrumented)');
  eq(open('E1'), 'E1', 'E1 detail opens');
  eq(d.getElementById('ndBadge').textContent.trim(), 'RELAY OK', 'E1 badge RELAY OK');
  ok(d.querySelectorAll('#ndNav .cellnav-b, #ndNav button, #ndNav .nb').length >= 12 ||
     d.getElementById('ndNav').children.length >= 12, 'node nav lists cells');
  const nbr = d.getElementById('ndNbr').textContent;
  ok(nbr.includes('E2') && nbr.includes('E5'), 'E1 neighbours are E2 / E5', nbr);
  w.close();
}

async function suiteScenarios() {
  console.log('\n[5] round 2 — scenario console + command centre');
  const { w, d } = await boot({});
  eq(d.getElementById('scStatus').textContent.trim(), 'NO INJECTION ARMED', 'console idle');
  const btns = d.querySelectorAll('.sc-b');
  eq(btns.length, 4, '4 scenario injections in one place');

  w.__NS.doTrigger('node_1', 'critical');
  await sleep(5200);
  const stat = w.SURF.api.mesh.stat;
  eq(stat[4], 'CRITICAL', 'E5 goes CRITICAL after node_1 injection');
  eq(JSON.stringify(w.UG.api.meshStat), JSON.stringify(stat), 'mesh status mirrored into 3D model');
  ok(w.SURF.api.hub.online < 12, 'command centre reports a child offline', w.SURF.api.hub.online);
  ok(/REROUTING|ALERT/.test(d.getElementById('meshParent').textContent), 'parent shows rerouting/alert');

  w.__NS.rearm();
  await sleep(400);
  eq(d.getElementById('scStatus').textContent.trim(), 'NO INJECTION ARMED', 'rearm clears console');
  w.close();
}

async function suitePaint() {
  console.log('\n[6] canvas paint — reticle actually renders');
  const { w, painted } = await boot({});
  w.__NS.pointAtCell('E7');
  painted.length = 0;
  await sleep(400);
  const surfText = painted.join(' | ');
  ok(/\bE7\b/.test(surfText), 'plan paints the E7 callout');
  ok(/SELECTED/.test(surfText), 'plan callout marked SELECTED');

  w.__NS.setMode('underground');
  w.__NS.pointAtCell('E8');
  await sleep(700);
  painted.length = 0;
  await sleep(400);
  const ugText = painted.join(' | ');
  ok(/E8/.test(ugText), '3D model paints the E8 callout');
  ok(/NODE 2|INSTRUMENTED/.test(ugText), '3D callout identifies the instrumented node');
  eq(/\uFFFD/.test(surfText + ugText), false, 'no mojibake in painted text');
  w.close();
}

async function suitePerf() {
  console.log('\n[7] render loop cost');
  const { w, d, counters } = await boot({});
  counters.resize = 0; counters.getContext = 0; counters.pattern = 0;
  await sleep(2000);
  eq(counters.resize, 0, 'no canvas backing-store reallocation in steady state');
  eq(counters.getContext, 0, 'context is cached, not re-fetched each frame');
  eq(counters.pattern, 0, 'grain pattern built once, not per frame');

  w.__NS.show('telemetry');
  await sleep(900);
  eq(w.SURF.scene.wantRender, false, 'scene suspended behind a full-screen overlay');
  eq(w.UG.scene.wantRender, false, '3D scene suspended too');
  w.__NS.show('overview');
  await sleep(200);
  eq(w.SURF.scene.wantRender, true, 'scene resumes on overview');
  w.close();
}

async function suitePolling() {
  console.log('\n[8] offline polling backoff');
  const { w, counters } = await boot({});
  const start = counters.fetch;
  await sleep(12000);
  const n = counters.fetch - start;
  ok(n < 20, 'backoff keeps offline requests low over 12s (' + n + ' requests)', n);
  w.close();
}

/* -------------------------------------------------------------------- run */
(async () => {
  const only = process.argv[2];
  const suites = {
    boot: suiteBoot, cells: suiteCellPointing, keys: suiteKeyboard, detail: suiteDetail,
    scenarios: suiteScenarios, paint: suitePaint, perf: suitePerf, polling: suitePolling
  };
  const names = only ? [only] : Object.keys(suites);
  for (const n of names) {
    if (!suites[n]) { console.error('unknown suite: ' + n); process.exit(2); }
    try { await suites[n](); }
    catch (e) { fail++; console.log('  FAIL suite "' + n + '" threw: ' + e.message); }
  }
  console.log('\n---------------------------------------------');
  console.log('assertions: ' + pass + ' passed, ' + fail + ' failed');
  console.log('page errors: ' + errors.length);
  errors.forEach(e => console.log('   ' + e));
  process.exit(fail || errors.length ? 1 : 0);
})();
