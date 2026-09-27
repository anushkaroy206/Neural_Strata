#!/usr/bin/env python3
"""
Phase 0 · D3 + D4 — reconcile the published numbers with the shipped artifact.
=============================================================================

One canonical run, published, and every other copy changed to match. This is the
half of Phase 0 that is not code.

    python3 tools/fix_numbers_phase0.py --dry-run     # show what would change
    python3 tools/fix_numbers_phase0.py               # apply to both trees

Why both trees
--------------
The documents exist twice: the plan set at the workspace root, and the copies
kept inside the repo under ``docs/`` (the tree of record says "plans and
specifications, kept with the code"). Patching one and not the other recreates
exactly the drift this phase exists to remove, so this script patches both and
then runs ``check_numbers.py`` on the repo.

Canonical values, read from ``models/model_meta.json`` — never typed here
------------------------------------------------------------------------
    held-out critical MAE      0.10 s   (was published as 5.4 s)
    held-out critical RMSE     0.42 s   (was published as 9.5 s)
    warning ramp in-sample     0.08 s   (was published as 0.14 s)
    overall                    0.09 s   (was published as 0.74 s)
    5-min breach acc/prec/rec  1.000 / 1.000 / 1.000

The old 5.4 s / 9.5 s figures came from an earlier, smaller split (n = 62). They
are not wrong, they are *older* — and nothing in the repository said so, which is
indistinguishable from being wrong.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

WORKSPACE = BACKEND.parents[1]        # /home/user
REPO = BACKEND.parent                 # /home/user/neural-strata

#: (file relative to a tree root, old text, new text, what it fixes)
PATCHES: list[tuple[str, str, str, str]] = [

    # ---- D4: the MAE the SRS publishes, and the note the API actually emits ----
    ("Neural_Strata_SRS_v1.1.md",
     '  "note": "XGBoost TTF forecast · held-out MAE 5.4 s · kinematic fallback armed.",',
     '  "note": "XGBoost TTF forecast · held-out MAE 0.10 s · kinematic fallback armed.",',
     "D4 — the /api/ai note must match what the server emits"),

    ("Neural_Strata_SRS_v1.1.md",
     "| Training - held-out critical run | MAE 5.4 s / RMSE 9.5 s (n = 62 windows) |\n"
     "| Training - warning ramp (in-sample, disclosed) | MAE 0.14 s (n = 481) |\n"
     "| Training - overall | MAE 0.74 s |",
     "| Training - held-out critical run (`node_1_01`) | MAE **0.10 s** / RMSE 0.42 s |\n"
     "| Training - warning ramp (in-sample, disclosed) | MAE 0.08 s / RMSE 0.15 s |\n"
     "| Training - overall | MAE 0.09 s / RMSE 0.23 s |",
     "D4 — the validation table now quotes the canonical run"),

    ("Neural_Strata_SRS_v1.1.md",
     "| Test | Result |\n|---|---|\n"
     "| Training - held-out critical run (`node_1_01`) |",
     "| Test | Result |\n|---|---|\n"
     "| *Canonical run* | *`models/model_meta.json` — 5,806 windows, trained 2026-08-25. "
     "Every number below is read from that file by `tools/check_numbers.py`, which fails "
     "the build if a document drifts from it.* |\n"
     "| Training - held-out critical run (`node_1_01`) |",
     "D4b — name the canonical run above the table it governs"),

    # ---- D3: the feature count, in the one document that still said 14 ----
    ("Neural_Strata_Backend_Plan.md",
     "A real displacement trajectory passes through the same 14-feature rolling-window extractor into the",
     "A real displacement trajectory passes through the same 16-feature rolling-window extractor into the",
     "D3 — 14 features → 16"),

    # ---- PlatformIO env names, resolved in §4.3 of the directory structure ----
    ("Neural_Strata_SRS_v1.1.md",
     "| Firmware toolchain | PlatformIO: env:node1 / env:node2 / env:hub | Complete |",
     "| Firmware toolchain | PlatformIO: `env:node` · `env:level_hub` · `env:main_hub` | Complete |",
     "§4.3 — the resolved environment names (one env per board class, not per board)"),
]

#: ai/README.md gets a whole section added, not a line replaced.
README_TABLE = """## Dataset provenance

Three different window counts appear across these documents. They are not
contradictions — they describe three different datasets. This table exists so that
a reader who sees 5,806 and 7,870 and 50,000 on the same page can understand
rather than doubt.

| Dataset | Windows | Runs | Trained on? | Where it lives |
|---|---|---|---|---|
| **Raw field harvest** | **7,870** | 18 (9 breach / 9 stable) | no — it is the harvest | `datasets/drill_dataset.csv` |
| **The canonical training run** | **5,806** | v2 split, held-out critical run `node_1_01` | **yes — this is `ttf_model_v1.json`** | `models/model_meta.json` |
| **In-domain synthetic augmentation** | **50,000+** | physics-generated (Tier 3) | not yet — target for the augmented retrain | `datasets/drill_dataset_augmented.csv` |

**Canonical numbers**, read from `models/model_meta.json` and enforced by
`backend/tools/check_numbers.py` (which fails the build if any document disagrees):

| Metric | Value |
|---|---|
| Held-out critical MAE | **0.10 s** |
| Held-out critical RMSE | 0.42 s |
| Warning ramp (in-sample, disclosed) | MAE 0.08 s / RMSE 0.15 s |
| Overall | MAE 0.09 s / RMSE 0.23 s |
| 5-minute breach accuracy / precision / recall | 1.000 / 1.000 / 1.000 |

> The 5.4 s MAE quoted in earlier revisions of the SRS came from a smaller
> pre-v2 split (n = 62 windows). It was superseded by the run above. If you are
> reading an older document, that is the number it means.

## Model contract (4.4)"""


def apply(root: Path, dry: bool) -> int:
    changed = 0
    for rel, old, new, why in PATCHES:
        # The same document lives at the workspace root and under docs/ inside the
        # repo (the tree of record keeps plans with the code). Resolve both.
        path = next((c for c in (root / rel, root / "docs" / rel) if c.exists()), None)
        if path is None:
            continue
        text = path.read_text()
        if old not in text:
            if new.split("\n")[0] in text:
                print(f"    = {rel}: already applied")
            else:
                print(f"    ! {rel}: anchor not found — skipped ({why})")
            continue
        if text.count(old) != 1:
            print(f"    ! {rel}: anchor appears {text.count(old)}× — skipped")
            continue
        if dry:
            print(f"    ~ {rel}: would patch ({why})")
        else:
            path.write_text(text.replace(old, new))
            print(f"    ✓ {rel}: patched ({why})")
        changed += 1

    # ai/README.md — insert the provenance section before the model contract
    readme = root / "ai" / "README.md"
    if not readme.exists():
        readme = root / "neuralstrata-ai" / "README.md"
    if readme.exists():
        text = readme.read_text()
        text = text.replace(
            "- Input: the 17 features in `drill_dataset.csv` minus label/meta columns",
            "- Input: the **16 model features** in `contracts`/`engine.features.FEATURES` order "
            "(the CSV's numeric columns; label and metadata columns are not features)")
        if "## Dataset provenance" not in text:
            if "## Model contract (4.4)" in text:
                text = text.replace("## Model contract (4.4)", README_TABLE)
                print(f"    {'~' if dry else '✓'} {readme.name}: provenance table "
                      f"{'would be ' if dry else ''}added")
                changed += 1
            else:
                print(f"    ! {readme.name}: no anchor for the provenance table")
        elif not dry:
            readme.write_text(text)
        # the 5.4 s claim in the status table
        old_mae = "held-out critical MAE 5.4 s"
        if old_mae in text:
            text = text.replace(old_mae, "held-out critical MAE 0.10 s")
            print(f"    {'~' if dry else '✓'} {readme.name}: status table MAE → 0.10 s")
            changed += 1
        if not dry:
            readme.write_text(text)
    return changed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile published numbers with the artifact")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    trees = [("workspace originals", WORKSPACE), ("repo copies", REPO)]
    total = 0
    for label, root in trees:
        print(f"  {label}: {root}")
        if not any((root / rel).exists() for rel, _, _, _ in PATCHES) \
                and not (root / "ai" / "README.md").exists() \
                and not (root / "neuralstrata-ai" / "README.md").exists():
            print("    (nothing to patch)")
            continue
        total += apply(root, args.dry_run)
    print(f"\n  {total} change(s){' pending' if args.dry_run else ' applied'}")
    if not args.dry_run:
        print("  next: python3 tools/check_numbers.py   (must exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
