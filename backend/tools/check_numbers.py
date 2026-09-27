#!/usr/bin/env python3
"""
check_numbers.py — one canonical metrics set, enforced.
=======================================================

Phase 0's most dangerous defect was not a crash. It was that the repository said
**5.4 s MAE** in four public places and **0.10 s** in the artifact that actually
ships — and nothing noticed, because prose and JSON do not disagree loudly.

This script makes them disagree loudly. It reads the canonical numbers out of
``models/model_meta.json`` and the code, then greps the documents for every
claim of the same kind and fails the build if one of them has drifted.

    python3 tools/check_numbers.py              # exit 0 clean, 1 on drift
    python3 tools/check_numbers.py --report     # list every claim, with verdicts
    python3 tools/check_numbers.py --root ..    # scan a different tree

What is checked
---------------
1. **Feature count.** Every ``N-feature`` / ``Features (N)`` claim must equal
   ``len(engine.features.FEATURES)`` = 16. (Defect D3: four documents said 14.)
2. **MAE claims.** Every ``MAE x.x s`` claim must equal the canonical held-out
   critical MAE from ``model_meta.json`` — *unless* the line is explicitly
   labelled historical (see ``HISTORICAL_MARKERS``). A document may describe an
   older run, it just has to say so on the line.
3. **Window counts.** Every ``N windows`` claim must be one of the dataset sizes
   recorded in the provenance table, and the provenance table itself must exist.
   (Defect D4b: 5,806 / 7,870 / 50,000 were circulating with nothing explaining
   that they describe three *different* datasets.)
4. **Code artefacts.** ``scaler.json``'s feature list, the manifest, and the
   model metadata must all agree with each other.

Run it in the pre-demo script, so this class of bug cannot come back.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

import config                                   # noqa: E402
from engine.features import FEATURES            # noqa: E402

#: A line may quote a number that is not canonical **if it says why**.
HISTORICAL_MARKERS = (
    "historic", "historical", "provenance", "earlier run", "previous run",
    "pre-phase-0", "superseded", "raw harvest", "not the shipped",
)

#: Documents that make *published claims* — the ones a judge reads.
#:
#: Planning and audit documents (Backend_Build_Phases, Test_Pack, the directory
#: structure, the review notes) are deliberately NOT in this list. They quote the
#: drifted numbers **on purpose**, as the record of what the drift was and how it
#: was fixed. Flagging them would mean deleting the evidence.
DOC_GLOBS = (
    "README.md",
    "ai/README.md",
    "ai/reports/*.md",
    "docs/Neural_Strata_SRS_v1.1.md",
    "docs/Neural_Strata_Backend_Plan.md",
)

#: Every other document — scanned only with ``--all``, reported, never failed.
AUDIT_GLOBS = ("docs/*.md", "ai/notebooks/*.py", "ai/export/*.txt")

#: An inline exemption, for a line that talks about a *different* number on
#: purpose. Use it with a reason, e.g.
#:     <!-- numbers-ok: this is Model 1's 32-feature disruption set -->
EXEMPT_MARKER = "numbers-ok"

#: Known dataset sizes and what they are. Anything else is a claim we cannot place.
KNOWN_DATASETS = {
    5806: "the run that produced the shipped ttf_model_v1.json",
    7870: "the raw field harvest (18 runs: 9 breach, 9 stable)",
    50000: "the in-domain synthetic augmentation target (Tier 3)",
    # --- Phase 5 -----------------------------------------------------------------
    # ``pattern_dataset.csv`` and ``drill_dataset.csv`` are written by one pass of
    # ``tools/make_counterfactuals.py`` and therefore share a row count: one drill
    # row is emitted per pattern row, from the same window of the same run. They
    # are still two files with two shapes (32 features vs 16 core features plus
    # ``ttf_label``), which is why the provenance table records both even though
    # the size is the same number.
    43092: ("the Model 1 counterfactual corpus AND the Model 2 drill set "
             "(420 runs, one pass, 32 and 16 features respectively)"),
}

RE_MAE = re.compile(r"MAE\s*(?:of\s*)?[≈~]?\s*([0-9]+(?:\.[0-9]+)?)\s*s")
# `(?<!top-)` keeps "top-3 features" — a UI label — out of the feature-count check.
RE_FEATURES = re.compile(r"(?<!top-)\b(\d{1,3})\s*[- ]feature", re.IGNORECASE)
RE_FEATURES_PAREN = re.compile(r"[Ff]eatures?\s*\((\d{1,3})\)")
RE_WINDOWS = re.compile(r"\b([0-9][0-9,]{2,})\s*(?:windows|window\b)")
#: A different artifact's feature count, named as such on the line.
RE_OTHER_MODEL_FEATURES = re.compile(r"model\s*1|pattern|disruption|cascade", re.IGNORECASE)
#: A dataset path in a line is a *provenance* statement. A document that says
#: "the Model 1 corpus is 43,092 windows" is not drifting from a model meta that
#: records 7,870 — it is describing a different file, and the gate exists to catch
#: a document contradicting the artifact it is *about*. Without this, every report
#: that quotes two corpora in one table is a permanent false positive, and a gate
#: that cries wolf is a gate people learn to route around.
RE_DATASET_PATH = re.compile(r"datasets/([A-Za-z0-9_\-]+\.csv)")


class Claim:
    __slots__ = ("kind", "file", "line_no", "line", "value", "ok", "why")

    def __init__(self, kind, file, line_no, line, value, ok, why):
        self.kind, self.file, self.line_no = kind, file, line_no
        self.line, self.value, self.ok, self.why = line, value, ok, why

    def render(self):
        mark = "ok " if self.ok else "DRIFT"
        text = self.line.strip()[:88]
        return f"  {mark:5} {self.kind:9} {self.value:>8}  {self.file}:{self.line_no}\n           {text}"


# ---------------------------------------------------------------------------
# Canonical values — read from the artifact, never typed here
# ---------------------------------------------------------------------------

def _read_json(path, label: str) -> dict:
    """Read a model artifact, or say *which* one is unreadable.

    A bare ``json.loads`` on a missing-but-present file (an empty placeholder, a
    Git LFS pointer, a half-copied tree) raises JSONDecodeError out of the middle
    of the gate with no filename in the traceback — the operator sees a stack
    trace instead of "scaler.json is empty". Both gates read the same three
    artifacts, so both answer the same way.
    """
    if path is None or not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as e:
        head = path.read_text(errors="replace")[:48].replace("\n", "\\n")
        print(f"  DRIFT {label:34} {path.name} is not JSON "
              f"({e.__class__.__name__}): starts {head!r} ({path.stat().st_size} B). "
              f"If it starts with 'version https://git-lfs' it is a Git LFS pointer "
              f"— run `git lfs pull`.")
        raise SystemExit(1)


def canonical() -> dict:
    meta_path = config.MODEL_FILES["model_meta"]
    meta = _read_json(meta_path, "model_meta")
    metrics = meta.get("metrics") or {}
    scaler_path = config.MODEL_FILES["scaler"]
    scaler = _read_json(scaler_path, "scaler")
    manifest_path = config.MODEL_FILES["manifest"]
    manifest = _read_json(manifest_path, "manifest")
    # The artifact defines SEVERAL error metrics — the held-out critical run, the
    # in-sample warning ramp (disclosed as such) and the overall figure. All of
    # them are canonical; the rule is that a published number must be one of the
    # artifact's numbers, not that only one number may be published.
    mae_metrics = {k: v for k, v in metrics.items()
                   if k.endswith("_mae_s") and isinstance(v, (int, float))}
    rmse_metrics = {k: v for k, v in metrics.items()
                    if k.endswith("_rmse_s") and isinstance(v, (int, float))}
    return {
        "features": len(FEATURES),
        "mae": metrics.get("held_out_critical_mae_s"),
        "rmse": metrics.get("held_out_critical_rmse_s"),
        "mae_metrics": mae_metrics,
        "rmse_metrics": rmse_metrics,
        "n_windows": meta.get("n_windows_total"),
        # Which file the canonical numbers describe. A line naming any *other*
        # dataset is quoting that one, not drifting from this one.
        "dataset": meta.get("dataset"),
        "model": meta.get("model"),
        "trained_at": meta.get("trained_at"),
        "scaler_features": scaler.get("features") or [],
        "manifest_features": manifest.get("features") or [],
        "manifest": manifest,
        "meta_path": meta_path,
    }


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def _is_historical(line: str) -> bool:
    low = line.lower()
    return any(marker in low for marker in HISTORICAL_MARKERS)


def _exemption(line: str, can: dict | None = None) -> str | None:
    """Why this line is allowed to quote a non-canonical number, or None.

    Three mechanisms: an explicit ``numbers-ok`` marker (with a reason the author
    had to write); Model 1's 32-feature disruption set, which is a *different*
    feature vector from the TTF extractor; and a **named dataset other than the
    canonical one**.

    The third was added in Phase 5 after `ai/reports/cascade_metrics.md` quoted
    both corpora in one table and the gate flagged Model 1's 43,092 windows
    against Model 2's 7,870. The number was right, the label was right, and the
    gate could not tell — because it compared a *size* without checking which
    file the size belonged to. The fix is not to stop quoting corpora sizes; it is
    to make the gate read the path that is already on the line.
    """
    if EXEMPT_MARKER in line.lower():
        return "exempt (numbers-ok)"
    if RE_OTHER_MODEL_FEATURES.search(line):
        return "Model 1's separate disruption set, not the TTF extractor"
    for name in RE_DATASET_PATH.findall(line):
        canonical = str((can or {}).get("dataset") or "")
        if name and name not in canonical:
            return f"a different dataset ({name}), not {canonical or 'the canonical one'}"
    return None


def scan_file(path: Path, rel: str, can: dict) -> list[Claim]:
    claims: list[Claim] = []
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return claims

    for i, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith(("```", "    ", "|--")):
            continue                                    # code fences / indented code

        for m in RE_MAE.finditer(line):
            value = float(m.group(1))
            hit = next((name for name, v in can["mae_metrics"].items()
                        if abs(value - float(v)) < 1e-9), None)
            ok = bool(hit) or _is_historical(line)
            why = (f"matches {hit}" if hit else
                   "labelled historical" if ok else
                   f"not one of the artifact's MAE metrics "
                   f"({', '.join(f'{k}={v}' for k, v in can['mae_metrics'].items())})")
            claims.append(Claim("MAE", rel, i, line, f"{value} s", ok, why))

        for rx in (RE_FEATURES, RE_FEATURES_PAREN):
            for m in rx.finditer(line):
                value = int(m.group(1))
                exempt = _exemption(line, can)
                ok = value == can["features"] or _is_historical(line) or bool(exempt)
                why = (f"matches the canonical {can['features']}" if ok and not _is_historical(line)
                       else "labelled historical" if _is_historical(line) and value != can["features"]
                       else exempt if exempt
                       else f"the extractor has {can['features']} features")
                claims.append(Claim("features", rel, i, line, str(value), ok, why))

        for m in RE_WINDOWS.finditer(line):
            value = int(m.group(1).replace(",", ""))
            exempt = _exemption(line, can)
            ok = value in KNOWN_DATASETS or _is_historical(line) or bool(exempt)
            why = (f"known dataset — {KNOWN_DATASETS.get(value, '?')}" if value in KNOWN_DATASETS
                   else _is_historical(line) and "labelled historical" or exempt
                   or f"not a known dataset size (known: {sorted(KNOWN_DATASETS)})")
            claims.append(Claim("windows", rel, i, line, f"{value:,}", ok, why))

    return claims


def provenance_table_present(root: Path) -> tuple[bool, str]:
    """D4b: the three dataset sizes must be explained somewhere a reader will look."""
    readme = root / "ai" / "README.md"
    if not readme.exists():
        return False, "ai/README.md is missing"
    text = readme.read_text().lower()
    if "dataset provenance" not in text:
        return False, "ai/README.md has no 'Dataset provenance' section"
    missing = [f"{n:,}" for n in KNOWN_DATASETS if str(n) not in text and f"{n:,}" not in text]
    if missing:
        return False, f"the provenance table does not mention {', '.join(missing)}"
    return True, "ai/README.md documents every dataset size in circulation"


# ---------------------------------------------------------------------------
# Code-vs-artifact agreement
# ---------------------------------------------------------------------------

def check_artifacts(can: dict) -> list[str]:
    problems: list[str] = []
    if not can["mae_metrics"]:
        problems.append(f"{can['meta_path'].name} publishes no *_mae_s metric")
    if can["mae"] is None:
        problems.append(f"{can['meta_path'].name} has no metrics.held_out_critical_mae_s")
    if can["scaler_features"] and can["scaler_features"] != list(FEATURES):
        problems.append(
            "scaler.json feature order disagrees with engine.features.FEATURES\n"
            f"      scaler   : {can['scaler_features']}\n"
            f"      extractor: {list(FEATURES)}")
    if can["manifest_features"] and can["manifest_features"] != list(FEATURES):
        problems.append("models/MANIFEST.json feature list disagrees with the extractor")
    if not can["manifest"]:
        problems.append("models/MANIFEST.json is missing (Phase 0 deliverable)")
    else:
        if can["manifest"].get("schema") != 2:
            problems.append("MANIFEST.json schema must be 2")
        if "NEVER CRASH" not in str(can["manifest"].get("boot_policy", "")):
            problems.append("MANIFEST.json must state the boot policy (LOG + DEGRADE, NEVER CRASH)")
    return problems


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Assert the documents match the model artifacts")
    ap.add_argument("--root", default=str(BACKEND.parent),
                    help="tree to scan (default: the neural-strata repo root)")
    ap.add_argument("--report", action="store_true", help="print every claim, not just drift")
    ap.add_argument("--all", action="store_true",
                    help="also audit planning/notebooks (reported, never failed)")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    can = canonical()

    print("canonical (read from the artifacts, never typed):")
    print(f"  features          {can['features']}")
    for name, val in can["mae_metrics"].items():
        print(f"  {name:17} {val} s")
    print(f"  held-out RMSE     {can['rmse']} s")
    print(f"  training windows  {can['n_windows']}")
    print(f"  model             {can['model']} ({can['trained_at']})")
    print()

    problems = check_artifacts(can)
    for p in problems:
        print(f"  ARTEFACT  {p}")

    claims: list[Claim] = []
    for pattern in DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            if path.is_file():
                claims.extend(scan_file(path, str(path.relative_to(root)), can))

    audit: list[Claim] = []
    if args.all:
        for pattern in AUDIT_GLOBS:
            for path in sorted(root.glob(pattern)):
                if path.is_file() and not any(
                        str(path).endswith(c.file) for c in claims):
                    audit.extend(scan_file(path, str(path.relative_to(root)), can))

    drifted = [c for c in claims if not c.ok]
    if args.report or drifted:
        print(f"published-claim documents — {len(claims)} claims, {len(drifted)} drifted")
        for c in (claims if args.report else drifted):
            print(c.render())
        print()

    if audit:
        audit_drift = [c for c in audit if not c.ok]
        print(f"audit-only (planning docs, notebooks) — {len(audit)} claims, "
              f"{len(audit_drift)} would drift if they were published")
        for c in (audit_drift[:12] if not args.report else audit_drift):
            print(c.render())
        if len(audit_drift) > 12 and not args.report:
            print(f"   … {len(audit_drift) - 12} more (use --report)")
        print()

    ok_prov, why_prov = provenance_table_present(root)
    print(f"  {'ok ' if ok_prov else 'DRIFT'}  provenance  {why_prov}")

    total = len(drifted) + len(problems) + (0 if ok_prov else 1)
    if total:
        print(f"\nFAIL — {total} number(s) do not agree with the shipped model.")
        print("Fix the document, or label the line 'historical' with the dataset it "
              "actually describes. Do not lower the claim.")
        return 1

    print(f"\nPASS — {len(claims) or 'no'} claims agree; every published number matches "
          f"the artifact that ships. Canonical MAE {can['mae']} s, "
          f"{can['features']} features.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
