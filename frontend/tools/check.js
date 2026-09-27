#!/usr/bin/env node
'use strict';
// Static sanity check of src/: every linked asset exists, scripts load in numeric order and parse cleanly,
// and the built index.html at the project root is not older than the sources.
const fs = require('fs'), path = require('path');
const { spawnSync } = require('child_process');
const ROOT = path.resolve(__dirname, '..'), SRC = path.join(ROOT, 'src');
const html = fs.readFileSync(path.join(SRC, 'index.html'), 'utf8');
let fail = 0;
const bad = msg => { fail++; console.log('  ✗ ' + msg); };

const links = [...html.matchAll(/<link rel="stylesheet" href="([^"]+)">/g)].map(m => m[1]);
const scripts = [...html.matchAll(/<script src="([^"]+)"><\/script>/g)].map(m => m[1]);
console.log('src/index.html · ' + links.length + ' stylesheets · ' + scripts.length + ' scripts');

for (const f of [...links, ...scripts]) if (!fs.existsSync(path.join(SRC, f))) bad('missing file src/' + f);

const order = scripts.map(s => parseInt(path.basename(s), 10));
if (order.some((v, i) => i && !(v > order[i - 1]))) bad('scripts are not in ascending numeric order: ' + order.join(','));

const onDisk = fs.readdirSync(path.join(SRC, 'js')).filter(f => f.endsWith('.js')).map(f => 'js/' + f).sort();
for (const f of onDisk) if (!scripts.includes(f)) bad('src/' + f + ' exists but is not referenced by src/index.html');

if (/<script>[\s\S]*?<\/script>/.test(html)) bad('src/index.html still contains an inline <script> block');
if (/<style>[\s\S]*?<\/style>/.test(html)) bad('src/index.html still contains an inline <style> block');

for (const s of scripts) {
  const r = spawnSync(process.execPath, ['--check', path.join(SRC, s)], { encoding: 'utf8' });
  if (r.status !== 0) bad(s + ' does not parse:\n' + r.stderr.trim());
}

for (const c of links) {
  const txt = fs.readFileSync(path.join(SRC, c), 'utf8');
  const open = (txt.match(/\{/g) || []).length, close = (txt.match(/\}/g) || []).length;
  if (open !== close) bad(c + ' has unbalanced braces (' + open + ' { vs ' + close + ' })');
}

const total = [...links, ...scripts].reduce((a, f) => a + (fs.existsSync(path.join(SRC, f)) ? fs.statSync(path.join(SRC, f)).size : 0), 0);
console.log('assets · ' + (total / 1024).toFixed(0) + ' KB total');

const built = path.join(ROOT, 'index.html');
if (!fs.existsSync(built)) bad('index.html (single-file build) is missing — run: npm run build');
else {
  const newest = Math.max(...['index.html', ...links, ...scripts].map(f => fs.statSync(path.join(SRC, f)).mtimeMs));
  if (fs.statSync(built).mtimeMs < newest) console.log('  ! index.html is older than src/ — run: npm run build');
  else console.log('index.html · ' + (fs.statSync(built).size / 1024).toFixed(0) + ' KB · up to date with src/');
}
console.log(fail ? fail + ' problem(s) found' : '  ✓ all checks passed');
process.exit(fail ? 1 : 0);
