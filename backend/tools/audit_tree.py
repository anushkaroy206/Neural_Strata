#!/usr/bin/env python3
"""
audit_tree.py — the end-of-phase §2 audit.

    python3 tools/audit_tree.py            # from backend/
    python3 backend/tools/audit_tree.py    # from the repo root

Why this file exists
--------------------
`Directory_Structure.md` §2 is binding on the filesystem: nothing gets created
outside that tree, and nothing declared in it may go missing unexplained. Both
halves of that rule fail silently without a checker. A file created "just for
now" drifts the tree; a declared file quietly dropped from a phase leaves a hole
nobody notices until the phase that imports it.

It was run out of `/tmp` for the Phase-5 audit, which means it was gone the
moment that sandbox was recycled — so the audit could not actually be re-run,
which is the only property that makes an audit worth having. It lives here now.

What it checks
--------------
1. **Declared but absent** — every path §2 names that is not on disk. Each one
   must be *owed* by a phase, and the owing phase is read out of §4's phase
   table rather than copied into this file. A declared file that no future phase
   claims is a real gap and fails the run.
2. **Present but outside §2** — anything under the source areas that §2 neither
   declares nor reaches through a declared directory. Runtime state is exempt,
   because `.gitignore` already declares it regenerable.

Exit code is 1 when there is anything the two rules cannot explain, so this can
gate a phase close-out exactly like `check_contracts.py` does.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "Neural_Strata_Directory_Structure.md"

# Source areas audited. `docs/` is included because it is part of the tree;
# runtime output folders are handled by RUNTIME below.
AREAS = ["backend", "firmware", "frontend", "ai", "infra", "docs"]

# Regenerable, gitignored, and excluded from the tree on purpose (`.gitignore`
# calls `runtime/` and `recordings/` "runtime state (regenerable, never
# committed)"). They are named in §2 as runtime rows, not as source.
RUNTIME = {"runtime", "recordings"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv",
             "dist", "build", "platformio", ".mypy_cache", ".ruff_cache"}

# Files §2 declares that were deliberately *not* created, with the revision that
# recorded the decision. Without this the audit would report the same known
# absence every phase and the report would stop being read.
RECORDED_NOT_CREATED = {
    "frontend/blackbox.js":
        "Rev 6.0.6 — the Phase-12 panel ships as frontend/src/js/13-blackbox-cloud.js "
        "and is inlined into the built index.html; a second copy outside the bundle "
        "would be an edit that never ships",
    "frontend/gis-map.js":
        "Rev 6.0.9 — the Phase-7 GIS panel ships as frontend/src/js/14-gis.js and is "
        "inlined into the built index.html, for the same reason as the black-box "
        "panel above: the tree predates the build step, and a copy outside the "
        "bundle would be an edit that never ships",
}


# ---------------------------------------------------------------------------
# the two halves of §2
# ---------------------------------------------------------------------------
def parse_tree(text: str) -> tuple[set[str], set[str]]:
    """Return (declared paths, declared directories) from §2's fenced tree.

    Entries are box-drawing rows. §2 names never contain spaces, so the name is
    the first whitespace-delimited token; everything after it is description.
    A directory row declares its contents by range (``js/  00-config … 13-…``),
    so a file is covered if it or any ancestor directory is declared.
    """
    lines = text.splitlines()
    fences = [i for i, l in enumerate(lines) if l.strip().startswith("```")]
    block: list[str] = []
    for a, b in zip(fences, fences[1:]):
        seg = lines[a + 1:b]
        if any(x.startswith(("├── backend/", "├── firmware/")) for x in seg):
            block = seg
            break

    declared: set[str] = set()
    declared_dirs: set[str] = set()
    stack: dict[int, str] = {}
    for line in block:
        idx = max(line.find("├──"), line.find("└──"))
        if idx < 0:
            continue
        depth = idx // 4
        rest = line[idx + 3:].strip()
        if not rest:
            continue
        name = rest.split()[0]
        is_dir = name.endswith("/")
        name = name.rstrip("/")
        stack[depth] = name
        for k in [k for k in stack if k > depth]:
            del stack[k]
        path = "/".join(stack[k] for k in sorted(stack))
        declared.add(path)
        if is_dir:
            declared_dirs.add(path)

    # the §4.1/§4.2 coverage tables name firmware files the tree elides
    for token in set(re.findall(r"^\| `(firmware/[^`]+)`", text, re.M)):
        token = token.rstrip("/")
        if " " not in token and "·" not in token:
            declared.add(token)
    return declared, declared_dirs


def parse_phase_ownership(text: str, declared: set[str]) -> dict[str, str]:
    """Map every path named in §4's phase table to the phase that writes it.

    Rows look like ``| **6** · B3 severity, progression, confidence | `backend/engine/severity.py` | — | — |``.
    Both the backend/AI column and the firmware column are read: a file can be
    owed by a hardware phase (``H4``) as easily as by a numbered one.

    The table abbreviates: the first token in a cell carries the path, later ones
    drop the directory (``... alarms/dispatcher.py`` · ``channels.py`` · ``templates.py``)
    or name it from the repo root. A bare token is therefore tried against the
    previous token's directory and against the two source roots — and it is
    accepted **only if it resolves to a path §2 declares**. A guess that misses
    degrades to "no phase claims this file", which is a question worth a human's
    time; a guess that lands on a real-but-unrelated path would be a claim that
    reads as fact and is not.
    """
    owed: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("| **"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2:      # §4.4 is a two-column table; §4 is four
            continue
        label = cells[0].split("·")[0]
        m = re.match(r"^\**\s*(H?\d+)\b", label.strip())
        if not m:
            continue            # §4.7's drift table and §5's notes own no files
        phase = m.group(1)

        for cell in cells[1:3]:
            prev_dir = ""
            for token in re.findall(r"`([A-Za-z0-9_./*-]+)`", cell):
                if token.endswith("/"):
                    prev_dir = token.rstrip("/")
                    continue
                glob = "*" in token
                cands: list[str] = []
                if "/" in token:
                    cands += [token, f"backend/{token}", f"firmware/{token}"]
                else:
                    if prev_dir:
                        cands.append(f"{prev_dir}/{token}")
                    cands += [f"backend/{token}", f"firmware/{token}", token]
                if glob:
                    for cand in list(cands):
                        rx = re.compile("^" + cand.replace(".", r"\.").replace("*", "[^/]*") + "$")
                        hit = next((d for d in sorted(declared) if rx.match(d)), None)
                        if hit:
                            owed.setdefault(hit, phase)
                    continue
                hit = next((c for c in cands if c in declared), None)
                if hit:
                    owed.setdefault(hit, phase)
                    prev_dir = hit.rsplit("/", 1)[0] if "/" in hit else ""

    # §4.1 and §4.2 reverse the column order — the path is first, the phase
    # second — and §4.1 is how a pure-hardware file (H4's serial link) gets an
    # owner at all, since it has no backend phase.
    for m in re.finditer(r"^\| `(firmware/[^`]+)`\s*\|\s*[* ]*(H?\d+)", text, re.M):
        owed.setdefault(m.group(1).rstrip("/"), m.group(2))
    return owed


# ---------------------------------------------------------------------------
# the filesystem
# ---------------------------------------------------------------------------
def walk_areas() -> set[str]:
    found: set[str] = set()
    for area in AREAS:
        base = ROOT / area
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in SKIP_DIRS and not d.startswith(".")]
            rel = Path(dirpath).relative_to(ROOT).as_posix()
            if rel != ".":
                found.add(rel)
            for name in filenames:
                if name.startswith(".") or name.endswith(".pyc"):
                    continue
                found.add(f"{rel}/{name}" if rel != "." else name)
    return found


def covered(path: str, declared: set[str], declared_dirs: set[str]) -> bool:
    if path in declared:
        return True
    parts = path.split("/")
    return any("/".join(parts[:i + 1]) in declared_dirs for i in range(len(parts)))


def main() -> int:
    text = DOC.read_text(encoding="utf-8")
    declared, declared_dirs = parse_tree(text)
    owed = parse_phase_ownership(text, declared)
    on_disk = walk_areas()

    missing = sorted(p for p in declared
                     if not (ROOT / p).exists() and p not in RECORDED_NOT_CREATED)
    stray = sorted(p for p in on_disk
                   if not covered(p, declared, declared_dirs)
                   and p.split("/")[0] not in RUNTIME)

    print(f"§2 declares {len(declared)} paths "
          f"({len(declared_dirs)} of them directories); {len(on_disk)} paths on disk.")

    unexplained_missing: list[str] = []
    print(f"\n── DECLARED BUT ABSENT ({len(missing)}) ──")
    if not missing:
        print("   none")
    for path in missing:
        if path in RECORDED_NOT_CREATED:
            print(f"   {path}\n      recorded, not created: {RECORDED_NOT_CREATED[path]}")
            continue
        phase = owed.get(path)
        if phase:
            print(f"   {path:<52} owed by {phase}")
        else:
            print(f"   {path:<52} ** no phase claims this file **")
            unexplained_missing.append(path)

    print(f"\n── PRESENT BUT OUTSIDE §2 ({len(stray)}) ──")
    if not stray:
        print("   none")
    for path in stray:
        print(f"   {path}")

    print("\nRESULT:", "PASS" if not unexplained_missing and not stray else "FAIL")
    if unexplained_missing:
        print(f"  {len(unexplained_missing)} declared file(s) no phase owes — "
              f"either build them or add the row that says when.")
    if stray:
        print(f"  {len(stray)} path(s) sit outside §2 — move them into the tree "
              f"or declare them in it.")
    return 0 if not unexplained_missing and not stray else 1


if __name__ == "__main__":
    sys.exit(main())
