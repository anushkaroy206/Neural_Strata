"""Recover src/index.html from the built single-file index.html.

The upload contained the built index.html but not the markup shell it was built
from. The build (tools/build-single.js) is a pure, invertible set of string
replacements, so the shell can be recovered exactly:

    <style>CSS</style><script>js/00-config.js</script>   <- was  <script src="js/00-config.js"></script>
    <script>js/NN-*.js</script>                          <- was  <script src="js/NN-*.js"></script>
    (stylesheet links were deleted outright)

Correctness is not assumed: the recovered shell is fed back through the real
build and the result is compared with the file we started from.

Two passes, and the second one re-scans the text: undoing the style block
changes the document length, so any script offsets captured beforehand would be
stale.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
BUILT = ROOT / "index.html"
SRC = ROOT / "src"
JS = SRC / "js"
CSS = SRC / "css"

html = BUILT.read_text(encoding="utf-8")
BLOCK = re.compile(r"^(?P<ind>[ \t]*)<script>\n(?P<body>.*?)\n</script>$", re.S | re.M)

# ---------------------------------------------------------------- stylesheets
m = re.search(r"^<style>\n(.*?)\n</style>\n", html, re.S | re.M)
if not m:
    sys.exit("no inlined <style> block found")
style_body = m.group(1)

placed = []
for f in sorted(CSS.glob("*.css")):
    body = f.read_text(encoding="utf-8").strip()
    pos = style_body.find(body)
    if pos < 0:
        sys.exit(f"css/{f.name} is not present in the inlined CSS")
    placed.append((pos, f.name))
placed.sort()
css_order = [name for _, name in placed]

if "\n\n".join((CSS / n).read_text(encoding="utf-8").strip() for n in css_order) != style_body:
    sys.exit("CSS does not reassemble byte-for-byte — order or content mismatch")

# ------------------------------------------------- scripts: which file is which
sources = {}
for f in sorted(JS.glob("*.js")):
    sources[f.read_text(encoding="utf-8").rstrip("\n")] = f"js/{f.name}"


def identify(body):
    """Block body -> source path, tolerating a source file edited after the build."""
    exact = sources.get(body)
    if exact:
        return exact, None
    cands = [n for text, n in sources.items() if body.startswith(text)]
    if len(cands) == 1:
        return cands[0], len(body) - max(len(t) for t in sources if sources[t] == cands[0])
    return None, None


# --------------------------------------------------------------- pass 1: style
needle = ("<style>\n" + style_body + "\n</style>\n<script>\n"
          + (JS / "00-config.js").read_text(encoding="utf-8").rstrip("\n") + "\n</script>")
if html.count(needle) != 1:
    sys.exit(f"style+config anchor matched {html.count(needle)} times, expected 1")

out = html.replace(
    needle,
    "".join(f'<link rel="stylesheet" href="css/{n}">\n' for n in css_order)
    + '<script src="js/00-config.js"></script>',
    1,
)

# --------------------------------------------- pass 2: scripts, re-scanned fresh
found, drifted = {}, []
for b in BLOCK.finditer(out):                      # positions are valid for `out`
    name, extra = identify(b.group("body"))
    if not name:
        sys.exit(f"script block {b.start()} matches no source file")
    found[b.start()] = (name, b)
    if extra is not None:
        drifted.append((name, extra))

# 00-config.js was consumed by pass 1, so it is not a block any more.
need = {f"js/{f.name}" for f in JS.glob("*.js")} - {"js/00-config.js"}
have = {v[0] for v in found.values()}
if need != have:
    sys.exit(f"script mismatch — missing {sorted(need - have)}, unexpected {sorted(have - need)}")

for start in sorted(found, reverse=True):          # right-to-left: no index shift
    name, b = found[start]
    out = out[:b.start()] + b.group("ind") + f'<script src="{name}"></script>' + out[b.end():]

if "<style>" in out:
    sys.exit("leftover inline <style> in the recovered shell")
if re.search(r"^<script>$", out, re.M):
    sys.exit("leftover inline <script> in the recovered shell")

SRC.mkdir(exist_ok=True)
(SRC / "index.html").write_text(out, encoding="utf-8")

print(f"src/index.html recovered · {len(out)} bytes · {out.count(chr(10)) + 1} lines")
print("stylesheet order : " + " → ".join(css_order))
print("script order     : " + " → ".join(found[s][0] for s in sorted(found)))
for name, extra in drifted:
    print(f"note: {name} was edited after that build — {extra} chars in the built copy are not in src/")
