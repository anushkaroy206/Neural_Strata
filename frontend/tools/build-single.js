#!/usr/bin/env node
'use strict';
// Inlines src/css/*.css and src/js/*.js into src/index.html and writes the self-contained result to ./index.html.
const fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '..'), SRC = path.join(ROOT, 'src');
const OUT = path.join(ROOT, 'index.html');
const read = rel => fs.readFileSync(path.join(SRC, rel), 'utf8');

let html = read('index.html');
const css = [];
html = html.replace(/[ \t]*<link rel="stylesheet" href="([^"]+)">\n?/g, (m, href) => { css.push(read(href)); return ''; });
if (!css.length) throw new Error('no stylesheet links found in src/index.html');
html = html.replace(/<script src="js\/00-config\.js"><\/script>/, () =>
  '<style>\n' + css.map(c => c.trim()).join('\n\n') + '\n</style>\n<script>\n' + read('js/00-config.js').trim() + '\n</script>');
let scripts = 0;
html = html.replace(/<script src="([^"]+\.js)"><\/script>/g, (m, srcPath) => {
  const body = read(srcPath);
  if (/<\/script/i.test(body)) throw new Error(srcPath + ' contains "</script>"');
  scripts++;
  return '<script>\n' + body.replace(/\n+$/, '') + '\n</script>';
});
fs.writeFileSync(OUT, html);
console.log('index.html · ' + (Buffer.byteLength(html) / 1024).toFixed(0) + ' KB · ' + css.length + ' stylesheets + ' + (scripts + 1) + ' scripts inlined from src/');
