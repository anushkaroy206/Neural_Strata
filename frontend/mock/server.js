#!/usr/bin/env node
'use strict';
// Zero-dependency stand-in for the Flask surface gateway.
// Serves the frontend (project root = built index.html; --root=src = the modular sources) and answers every
// /api/* route the dashboard polls, with a small physics + blackbox/cloud simulation behind it. See docs/API.md.
const http = require('http'), fs = require('fs'), path = require('path');

const args = process.argv.slice(2);
const flag = (k, d) => { const a = args.find(x => x.startsWith('--' + k + '=')); return a ? a.split('=')[1] : d; };
const ROOT = path.resolve(__dirname, '..', flag('root', '.'));
const PORT = +(flag('port', process.env.PORT || 8080));
const NO_API = args.includes('--no-api');
const MIME = { '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon',
  '.md': 'text/markdown; charset=utf-8', '.txt': 'text/plain; charset=utf-8', '.map': 'application/json' };

/* ---------------- simulation state ---------------- */
const hms = (d = new Date()) => d.toTimeString().slice(0, 8);
const round = (v, p) => +v.toFixed(p);
const NODES = {
  node_1: { base: 5.2, tiltBase: 1.1, tunnel: 'A' },
  node_2: { base: 4.1, tiltBase: 0.8, tunnel: 'B' }
};
const CREW = [['001', 'Deepak', 'Tunnel A'], ['002', 'Anil', 'Tunnel A'], ['003', 'Meera', 'Tunnel B'], ['004', 'Ravi', 'Tunnel B'],
  ['005', 'Sunita', 'Tunnel A'], ['006', 'Bikash', 'Tunnel B'], ['007', 'Farhan', 'Headframe'], ['008', 'Laxmi', 'Headframe']];
const S = { nodes: {}, rfid: [], events: [], history: {}, peak: {}, faults: {} };

function pushEvent(level, text) { S.events.unshift({ time: hms(), level, text }); S.events.length = Math.min(S.events.length, 64); }
function resetNodes() {
  for (const n in NODES) {
    S.nodes[n] = { d: NODES[n].base, tilt: NODES[n].tiltBase, v: 0, a: 0, status: 'NOMINAL', scen: null, t: 0 };
    S.peak[n] = 0; S.history[n] = S.history[n] || [];
  }
  S.rfid = CREW.map(([id, name, zone]) => ({ id, name, zone, time: hms(), _t: Date.now() }));
}
resetNodes();
pushEvent('NOMINAL', 'Mock gateway online — telemetry, extended feed and blackbox/cloud state are simulated.');

const BB = { sd: true, usb: true, online: true, power: true, chunk: 42, row: 450, rows: 0, rowsPerChunk: 1000, cardMB: 7580, freeMB: 3712,
  ram: 0, retained: [{ chunk_id: 41, rows: 1000 }], pending: 96, sent: 41354, nextUp: 30, lastUpload: { ts: hms(), rows: 240, batches: 3, ok: true },
  lastAck: { chunk_id: 40, ts: hms() } };

function stepNode(n, dt) {
  const s = S.nodes[n];
  if (s.scen) {
    s.t += dt;
    if (s.scen === 'warning') { s.a = Math.min(0.6, s.a + 0.05 * dt); s.v = Math.min(0.9, s.v + s.a * dt); }
    else { s.a = Math.min(4.2, s.a + 0.35 * dt); s.v = Math.min(12, s.v + s.a * dt); }
    s.d = Math.min(100, s.d + s.v * dt);
    s.tilt = Math.min(45, s.tilt + s.v * 0.12 * dt);
    const next = (s.a >= 3.0 || s.d >= 60) ? 'CRITICAL' : (s.a >= 0.25 || s.d >= 20) ? 'WARNING' : s.status;
    if (next !== s.status) { s.status = next; pushEvent(next, n.toUpperCase() + ' ' + next + ' — a=' + s.a.toFixed(2) + ' mm/s² · d=' + s.d.toFixed(1) + ' mm'); }
  } else {
    s.d += (NODES[n].base - s.d) * 0.02 + (Math.random() - 0.5) * 0.02;
    s.tilt += (NODES[n].tiltBase - s.tilt) * 0.02 + (Math.random() - 0.5) * 0.01;
    s.a = (Math.random() - 0.5) * 0.04; s.v = (Math.random() - 0.5) * 0.02;
  }
  S.peak[n] = Math.max(S.peak[n], s.a);
}
function stepCrew() {
  const now = Date.now();
  for (const t of ['A', 'B']) {
    const crit = Object.keys(NODES).some(n => NODES[n].tunnel === t && S.nodes[n].status === 'CRITICAL');
    S.rfid.forEach(r => {
      if (crit && r.zone === 'Tunnel ' + t && now - r._t > 2500) { r.zone = 'Evacuating ' + t; r.time = hms(); r._t = now; }
      else if (r.zone === 'Evacuating ' + t && now - r._t > 14000) { r.zone = 'Safe Exit'; r.time = hms(); r._t = now; }
    });
  }
}
function stepBlackbox(dt) {
  if (!BB.power) return;
  BB.rows += 8 * dt;
  while (BB.rows >= 1) {
    BB.rows -= 1;
    if (BB.sd) { BB.row++; } else { BB.ram = Math.min(2000, BB.ram + 1); }
    if (BB.usb) BB.pending++;
    if (BB.row >= BB.rowsPerChunk) {
      BB.retained.push({ chunk_id: BB.chunk, rows: BB.rowsPerChunk });
      BB.freeMB = Math.max(0, BB.freeMB - BB.rowsPerChunk * 26 / 1048576);
      BB.chunk++; BB.row = 0;
      if (BB.freeMB / BB.cardMB <= 0.05 && BB.retained.length > 1) BB.retained.shift();
    }
  }
  BB.nextUp -= dt;
  if (BB.nextUp <= 0) { BB.nextUp = 30; upload(false); }
}
function upload(force) {
  if (!BB.online) { BB.lastUpload = { ts: hms(), rows: 0, batches: 0, ok: false }; return { ok: false, uploaded: 0, acked: [] }; }
  const rows = BB.pending, batches = Math.ceil(rows / 100);
  BB.pending = 0; BB.sent += rows;
  BB.lastUpload = { ts: hms(), rows, batches, ok: true };
  const acked = BB.usb ? BB.retained.splice(0, BB.retained.length).map(c => c.chunk_id) : [];
  if (acked.length) { BB.lastAck = { chunk_id: acked[acked.length - 1], ts: hms() }; BB.freeMB = Math.min(BB.cardMB, BB.freeMB + acked.length * BB.rowsPerChunk * 26 / 1048576); }
  if (force) pushEvent('NOMINAL', 'FORCE SYNC — ' + rows + ' rows pushed in ' + batches + ' batch(es)' + (acked.length ? ' · ACK_DELETE ' + acked.map(i => String(i).padStart(4, '0')).join(', ') : ''));
  return { ok: true, uploaded: rows, acked };
}
let last = Date.now(), histAcc = 0;
setInterval(() => {
  const now = Date.now(), dt = Math.min(0.5, (now - last) / 1000); last = now;
  for (const n in NODES) stepNode(n, dt);
  stepCrew(); stepBlackbox(dt);
  histAcc += dt;
  if (histAcc >= 0.5) { histAcc = 0; for (const n in NODES) { const h = S.history[n]; h.push(nodeSample(n)); if (h.length > 240) h.shift(); } }
}, 100);

/* ---------------- payloads ---------------- */
const nodeSample = n => { const s = S.nodes[n]; return { status: s.status, displacement_mm: round(s.d, 1), tilt_x: round(s.tilt, 2), acceleration: round(s.a, 3) }; };
function telemetry() {
  const order = ['NOMINAL', 'WARNING', 'CRITICAL'];
  const worst = Object.keys(NODES).reduce((w, n) => order.indexOf(S.nodes[n].status) > order.indexOf(w) ? S.nodes[n].status : w, 'NOMINAL');
  let ttf = 'N/A';
  for (const n in NODES) { const s = S.nodes[n]; if (s.scen && s.v > 0.05 && s.a > 0.5) { const t = Math.max(3, Math.min(999, Math.round((100 - s.d) / Math.max(s.v, 0.1)))); if (ttf === 'N/A' || t < ttf) ttf = t; } }
  const nodes = {}; for (const n in NODES) nodes[n] = nodeSample(n);
  return { system_status: worst, ttf_seconds: ttf, nodes,
    underground: { route_A_status: S.nodes.node_1.status === 'CRITICAL' ? 'BLOCKED' : 'OPEN', route_B_status: S.nodes.node_2.status === 'CRITICAL' ? 'BLOCKED' : 'OPEN' },
    rfid_log: S.rfid.slice().sort((a, b) => b._t - a._t).slice(0, 10).map(({ id, name, zone, time }) => ({ id, name, zone, time })),
    timestamp: new Date().toISOString() };
}
function extended() {
  const nodes = {}; for (const n in NODES) nodes[n] = { velocity: round(S.nodes[n].v, 2), peak_acceleration: round(S.peak[n], 2) };
  const under = S.rfid.filter(r => /^Tunnel|^Evacuating/.test(r.zone)).length, safe = S.rfid.filter(r => r.zone === 'Safe Exit').length;
  return { mode: 'LIVE', nodes, muster: { total: S.rfid.length, underground: under, safe }, events: S.events.slice(0, 24),
    blackbox: { sd_ok: BB.sd, chunk_id: BB.chunk, row: BB.row, rows_per_chunk: BB.rowsPerChunk, free_mb: Math.round(BB.freeMB), card_mb: BB.cardMB,
      usb_ok: BB.usb, ram_ring: BB.ram, retained: BB.retained.slice(), power_ok: BB.power },
    cloud: { online: BB.online, pending: BB.pending, sent: BB.sent, next_upload_s: Math.max(0, Math.round(BB.nextUp)), last_upload: BB.lastUpload, last_ack: BB.lastAck } };
}
const ai = () => ({ model: 'NS-TTF (stub)', status: 'AWAITING_INTEGRATION', horizon: '—', last_train: '—', confidence: '—',
  note: 'Mock gateway: plug the time-series model service in here. Kinematic fallback active on the dashboard.', forecasts: {} });

/* ---------------- API routing ---------------- */
function api(req, res, body) {
  const route = req.method + ' ' + req.url.split('?')[0];
  const send = (code, obj) => { res.writeHead(code, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*' }); res.end(JSON.stringify(obj)); };
  switch (route) {
    case 'GET /api/telemetry': return send(200, telemetry());
    case 'GET /api/extended': return send(200, extended());
    case 'GET /api/history': return send(200, Object.fromEntries(Object.keys(NODES).map(n => [n, S.history[n]])));
    case 'GET /api/ai': return send(200, ai());
    case 'POST /api/sim/trigger': {
      const n = body.node, sev = body.severity === 'critical' ? 'critical' : 'warning';
      if (!NODES[n]) return send(400, { ok: false, error: 'node must be one of ' + Object.keys(NODES).join(', ') });
      Object.assign(S.nodes[n], { scen: sev, t: 0 });
      pushEvent(sev === 'critical' ? 'CRITICAL' : 'WARNING', n.toUpperCase() + ' scenario injected — ' + (sev === 'critical' ? 'terminal creep' : 'sustained creep') + '.');
      console.log('  ▶ trigger', n, sev);
      return send(200, { ok: true, node: n, severity: sev });
    }
    case 'POST /api/reset': resetNodes(); pushEvent('NOMINAL', 'System re-armed — crew returning to posts.'); console.log('  ▶ reset'); return send(200, { ok: true });
    case 'POST /api/cloud/force_sync': { const r = upload(true); console.log('  ▶ force_sync', r); return send(r.ok ? 200 : 503, r); }
    case 'POST /api/mock/faults': {
      if ('sd_ok' in body) BB.sd = !!body.sd_ok; if ('usb_ok' in body) BB.usb = !!body.usb_ok;
      if ('online' in body) BB.online = !!body.online; if ('power_ok' in body) BB.power = !!body.power_ok;
      if (BB.sd) BB.ram = 0;
      console.log('  ▶ faults', { sd_ok: BB.sd, usb_ok: BB.usb, online: BB.online, power_ok: BB.power });
      return send(200, { ok: true, sd_ok: BB.sd, usb_ok: BB.usb, online: BB.online, power_ok: BB.power });
    }
    default: return send(404, { ok: false, error: 'no such route: ' + route });
  }
}

/* ---------------- static files ---------------- */
function serveStatic(req, res) {
  let p = decodeURIComponent(req.url.split('?')[0]);
  if (p === '/') p = '/index.html';
  const file = path.normalize(path.join(ROOT, p));
  if (!file.startsWith(ROOT) || /[\\/](node_modules|\.git)[\\/]/.test(file)) { res.writeHead(403); return res.end(); }
  fs.stat(file, (err, st) => {
    if (err || !st.isFile()) { res.writeHead(404, { 'Content-Type': 'text/plain' }); return res.end('404 ' + p); }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(file).toLowerCase()] || 'application/octet-stream', 'Content-Length': st.size, 'Cache-Control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  });
}

http.createServer((req, res) => {
  if (req.method === 'OPTIONS') { res.writeHead(204, { 'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Methods': 'GET,POST,OPTIONS', 'Access-Control-Allow-Headers': 'Content-Type' }); return res.end(); }
  if (req.url.startsWith('/api/')) {
    if (NO_API) { res.writeHead(404, { 'Content-Type': 'application/json' }); return res.end('{"ok":false,"error":"api disabled (--no-api)"}'); }
    let raw = '';
    req.on('data', c => { raw += c; if (raw.length > 65536) req.destroy(); });
    req.on('end', () => { let body = {}; try { body = raw ? JSON.parse(raw) : {}; } catch (e) { body = {}; } api(req, res, body); });
    return;
  }
  serveStatic(req, res);
}).listen(PORT, '0.0.0.0', () => {
  const rel = path.relative(path.resolve(__dirname, '..'), ROOT);
  console.log('Neural Strata frontend  →  http://localhost:' + PORT + '/   (' + (rel ? 'serving ' + rel + '/ — modular sources' : 'serving ./index.html — single-file build') + ')');
  console.log(NO_API ? 'API disabled: dashboard runs in SIM mode' : 'mock gateway API on /api/* (telemetry · extended · history · ai · sim/trigger · reset · cloud/force_sync · mock/faults)');
});
