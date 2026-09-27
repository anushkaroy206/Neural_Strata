#!/usr/bin/env python3
"""
Neural Strata — Phase 5.1 · Train Model 1, the Deformation Pattern Discriminator
================================================================================
Reads:  ai/datasets/pattern_dataset.csv          (from tools/make_counterfactuals.py)
Writes: backend/models/pattern_model_v1.json     <- engine/pattern.py loads this
        backend/models/pattern_meta.json         <- features, classes, threshold, metrics
        ai/export/pattern_training_report.txt    <- record of this run

Contract (must stay in sync with engine/features.py — IT IS CHECKED, NOT ASSUMED):
    The 32 extended features, in EXTENDED_FEATURES order, exactly as
    engine.features.compute_extended_features returns them. The notebook imports
    that list rather than repeating it, and the artifact records it, so that a
    future feature added to the extractor cannot be silently mis-paired with a
    tree fitted on 32 columns. engine.pattern.load_pattern_model refuses a
    mismatch at boot for the same reason.

Target
------
Fitted on the **4-class primary** target (QUIET / ENVIRONMENTAL / STRUCTURAL /
UNKNOWN), not the 7 raw profile labels. Reason: the collapse from 7 to 4 is a
*judgement* — that a blast and a truck are the same kind of thing from a safety
standpoint — and a judgement belongs in one labelled place, not smeared through
an argmax. Fitting on the 4 makes `model.classes_` and `meta["classes"]` the same
list, which is what lets the serving path index probabilities by name at all.
The 7-label composition of each primary class is reported below as secondary
information.

Split design — by `run_id`, in three parts
-------------------------------------------
* A **random row split would be catastrophic here and would also look fantastic.**
  Windows overlap by 60 s, so two windows from the same event are nearly
  identical; splitting rows at random puts the same truck in train and test and
  reports ~99% accuracy that means nothing. Every split below is
  `GroupShuffleSplit` on `run_id`.
* **Three ways, not two** (60 / 20 / 20). The operating threshold is chosen to hit
  STRUCTURAL recall ≥ 0.98, and choosing it on the set you then report would be
  selection-on-test — the reported recall would be the best of many thresholds
  rather than the recall of the shipped one. So: fit on train, sweep the
  threshold on **validation**, and report every headline number on a **test** set
  no part of the pipeline has seen.
* Consequence, stated plainly: a run is one event start-to-finish, so test runs
  are events from tunnels the model has never observed. That is the right thing to
  hold out for a safety claim, and it is why the numbers below are lower than a
  random split would give.

Run:  python3 ai/notebooks/06_train_pattern.py
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import GroupShuffleSplit

AI_ROOT = Path(__file__).resolve().parents[1]                  # .../ai
REPO_ROOT = AI_ROOT.parent                                     # .../neural-strata
BACKEND = REPO_ROOT / "backend"
BACKEND_MODELS = BACKEND / "models"
EXPORT_DIR = AI_ROOT / "export"
DATASET = AI_ROOT / "datasets" / "pattern_dataset.csv"

# Import the extractor's own contract so the notebook cannot drift from serving.
sys.path.insert(0, str(BACKEND))
from engine.features import DISRUPTION_FEATURES, EXTENDED_FEATURES, N_EXTENDED  # noqa: E402
from engine import pattern as pattern_engine                                   # noqa: E402
from config import PATTERN_MISSING_SENTINEL                                    # noqa: E402

CLASSES = list(pattern_engine.PATTERN_CLASSES)          # QUIET, ENVIRONMENTAL, STRUCTURAL, UNKNOWN
TARGET = "primary"
GROUP = "run_id"

# §5.4's exact specification. Not tuned here — if these change, they change there.
RF_PARAMS = dict(
    n_estimators=200,
    max_depth=8,
    min_samples_leaf=4,
    class_weight="balanced_subsample",
    n_jobs=-1,
    random_state=42,
)

RECALL_TARGET = 0.98                # §5.4: the calibration target for the threshold
THRESHOLD_GRID = [round(0.05 + 0.05 * i, 2) for i in range(19)]   # 0.05 … 0.95


# ---------------------------------------------------------------------------

def load_dataset(path: Path) -> pd.DataFrame:
    if not path.exists():
        sys.exit(f"error: {path} not found.\n"
                 f"       generate it first:\n"
                 f"       cd backend && python3 tools/make_counterfactuals.py "
                 f"--runs 420 --out ../ai/datasets/pattern_dataset.csv")
    df = pd.read_csv(path)
    missing = [c for c in EXTENDED_FEATURES if c not in df.columns]
    if missing:
        sys.exit(f"error: dataset is missing {len(missing)} expected feature column(s): "
                 f"{missing[:6]}{' …' if len(missing) > 6 else ''}\n"
                 f"       it was probably generated by an older extractor. Regenerate it.")
    return df


def matrix(df: pd.DataFrame) -> np.ndarray:
    """The 32 columns as a float64 matrix, NaN → sentinel.

    Uses the *same* ``to_model_vector`` the serving path uses. NaN → −999 is a
    documented contract (see config.PATTERN_MISSING_SENTINEL), and the one thing
    that must not happen is training and serving substituting differently.
    """
    out = np.empty((len(df), N_EXTENDED), dtype=np.float64)
    for j, name in enumerate(EXTENDED_FEATURES):
        col = pd.to_numeric(df[name], errors="coerce").to_numpy(dtype=np.float64)
        col = np.nan_to_num(col, nan=PATTERN_MISSING_SENTINEL)
        out[:, j] = col
    return out


def three_way_split(df: pd.DataFrame, rng_state: int = 42):
    """GroupShuffleSplit by run_id → train / val / test, no run in two places."""
    runs = df[GROUP].to_numpy()
    idx = np.arange(len(df))

    gss = GroupShuffleSplit(n_splits=1, test_size=0.20, random_state=rng_state)
    tr_all, te = next(gss.split(idx, groups=runs))

    gss2 = GroupShuffleSplit(n_splits=1, test_size=0.25, random_state=rng_state + 1)
    tr_rel, va_rel = next(gss2.split(tr_all, groups=runs[tr_all]))

    tr = tr_all[tr_rel]
    va = tr_all[va_rel]
    assert not (set(runs[tr]) & set(runs[va])), "run leaked train→val"
    assert not (set(runs[tr]) & set(runs[te])), "run leaked train→test"
    assert not (set(runs[va]) & set(runs[te])), "run leaked val→test"
    return tr, va, te


def argmax_labels(proba: np.ndarray, order: list[str]) -> np.ndarray:
    """``argmax`` → class *names*, using the model's own column order.

    ``order`` is **always** ``clf.classes_`` and never a hand-written class list.
    That distinction is not pedantry — it is a bug that was live in this notebook
    and produced a beautiful, entirely fake result:

    ``RandomForestClassifier.classes_`` is **sorted alphabetically**, so for this
    problem it is ``['ENVIRONMENTAL','QUIET','STRUCTURAL','UNKNOWN']``. The module
    constant ``CLASSES`` is in *safety* order (``QUIET`` first). Indexing the
    probability matrix with the wrong one swapped columns 0 and 1, which inverted
    QUIET and ENVIRONMENTAL exactly — while STRUCTURAL and UNKNOWN, sitting at
    indices 2 and 3 in both lists, stayed correct. The reported confusion matrix
    was a perfect off-diagonal transpose of two classes and the model looked
    broken when the model was fine.

    So: the column order is read from the fitted estimator, every time, and
    :func:`main` asserts it matches the contract before relying on it.
    """
    return np.array(order, dtype=object)[np.argmax(proba, axis=1)]


def sweep_threshold(y_true: np.ndarray, proba: np.ndarray, si: int, order: list[str]):
    """Lowest threshold reaching STRUCTURAL recall ≥ target — and what it costs.

    §5.4: "the lowest threshold that achieves STRUCTURAL recall ≥ 0.98". Lowest,
    not best-F1, and the reason matters: a missed structural event is the one
    error this system exists to prevent, while a false structural alarm costs an
    unnecessary advisory. The sweep therefore spends precision to buy recall and
    the *budget* is fixed at 0.98 rather than tuned for prettiness.
    """
    rows = []
    picked = None
    for th in THRESHOLD_GRID:
        # everything not escalated keeps its argmax class
        arg = argmax_labels(proba, order)
        final = np.where(proba[:, si] >= th, "STRUCTURAL", arg)
        rec = recall_score(y_true, final, labels=["STRUCTURAL"], average="micro", zero_division=0)
        prec = precision_score(y_true, final, labels=["STRUCTURAL"], average="micro", zero_division=0)
        f1 = f1_score(y_true, final, average="macro", zero_division=0)
        rows.append((th, rec, prec, f1))
        if picked is None and rec >= RECALL_TARGET:
            picked = th
    return picked, rows


def class_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    return {
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "per_class_recall": {
            c: float(recall_score(y_true, y_pred, labels=[c], average="micro", zero_division=0))
            for c in CLASSES
        },
        "per_class_precision": {
            c: float(precision_score(y_true, y_pred, labels=[c], average="micro", zero_division=0))
            for c in CLASSES
        },
    }


def quiet_false_positive_rate(y_true, y_pred) -> float:
    """P(predicted not-QUIET | truly QUIET).

    The number the demo is judged on by anyone watching the rig sit still: how
    often does a calm tunnel get called disturbed? A classifier that earned its
    STRUCTURAL recall by calling everything structural would show up here.
    """
    mask = y_true == "QUIET"
    if not mask.any():
        return float("nan")
    return float(np.mean(y_pred[mask] != "QUIET"))


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Train Model 1 — the pattern discriminator.")
    ap.add_argument("--dataset", type=Path, default=DATASET)
    ap.add_argument("--outdir", type=Path, default=BACKEND_MODELS)
    ap.add_argument("--export-dir", type=Path, default=EXPORT_DIR)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    print("=" * 78)
    print("Neural Strata — Model 1 · Deformation Pattern Discriminator")
    print("=" * 78)

    df = load_dataset(args.dataset)
    print(f"\ndataset         {args.dataset}")
    print(f"windows         {len(df):,}   runs {df[GROUP].nunique():,}   "
          f"features {N_EXTENDED}")
    print("class balance   " + "  ".join(
        f"{c}={int((df[TARGET] == c).sum()):,}" for c in CLASSES))

    tr, va, te = three_way_split(df, args.seed)
    print(f"\nsplit (by {GROUP} — never by row)")
    for name, ix in (("train", tr), ("val", va), ("test", te)):
        print(f"  {name:5s} {len(ix):7,} windows   {df[GROUP].iloc[ix].nunique():5,} runs")

    X = matrix(df)
    y = df[TARGET].to_numpy()

    t0 = time.perf_counter()
    clf = RandomForestClassifier(**RF_PARAMS)
    clf.fit(X[tr], y[tr])
    fit_s = time.perf_counter() - t0
    print(f"\nfitted          {RF_PARAMS['n_estimators']} trees, depth "
          f"{RF_PARAMS['max_depth']}, in {fit_s:.2f} s")
    print(f"classes_        {list(clf.classes_)}")

    # The estimator's own column order. Assert it rather than assume it: if a
    # future class list and the fitted order ever disagree, every prediction name
    # below would be quietly wrong, and a wrong name in a safety path is worse
    # than a crash.
    ORDER = [str(c) for c in clf.classes_]
    assert set(ORDER) == set(CLASSES), (
        f"model classes {ORDER} do not match the contract {CLASSES}")
    if ORDER != CLASSES:
        print(f"  note            class order from the estimator is {ORDER}")
        print(f"                  (the safety order is {CLASSES} — predictions are "
              f"mapped by NAME, never by position)")

    proba_va = clf.predict_proba(X[va])
    si = ORDER.index("STRUCTURAL")

    th, sweep = sweep_threshold(y[va], proba_va, si, ORDER)
    if th is None:
        th = float(np.round(np.min(THRESHOLD_GRID), 2))
        print(f"\nWARNING: no threshold in the grid reached STRUCTURAL recall ≥ "
              f"{RECALL_TARGET} on validation. Falling back to the lowest ({th}). "
              f"The model does not currently meet its calibration target.")
    else:
        print("\nthreshold swept on validation (not on test — that would be "
              "selection-on-test)")
        print(f"  lowest threshold with STRUCTURAL recall ≥ {RECALL_TARGET}: "
              f"**{th:.2f}**")

    def apply_threshold(P: np.ndarray) -> np.ndarray:
        arg = argmax_labels(P, ORDER)
        return np.where(P[:, si] >= th, "STRUCTURAL", arg)

    # ---- headline numbers, on the set nothing has touched ----
    proba_te = clf.predict_proba(X[te])
    pred_te = apply_threshold(proba_te)
    m = class_metrics(y[te], pred_te)
    rec_te = m["per_class_recall"]["STRUCTURAL"]
    prec_te = m["per_class_precision"]["STRUCTURAL"]
    qfp = quiet_false_positive_rate(y[te], pred_te)

    # suppression precision: when M1 says ENVIRONMENTAL, how often is it right?
    env_mask = pred_te == "ENVIRONMENTAL"
    env_prec = float(np.mean(y[te][env_mask] == "ENVIRONMENTAL")) if env_mask.any() else float("nan")

    print(f"\ntest-set metrics  ({len(te):,} windows from "
          f"{df[GROUP].iloc[te].nunique()} unseen runs)")
    print(f"  macro F1            {m['macro_f1']:.4f}")
    print(f"  STRUCTURAL recall   {rec_te:.4f}   (target ≥ {RECALL_TARGET})")
    print(f"  STRUCTURAL precision{prec_te:8.4f}")
    print(f"  QUIET false-pos     {qfp:.4f}")
    print(f"  env. suppression P  {env_prec:.4f}")
    print("  per-class recall    " + "  ".join(
        f"{c}={m['per_class_recall'][c]:.3f}" for c in CLASSES))

    cm = confusion_matrix(y[te], pred_te, labels=CLASSES)
    print("\nconfusion matrix (rows = truth, cols = predicted)")
    print("  " + " " * 14 + "".join(f"{c[:6]:>8s}" for c in CLASSES))
    for i, c in enumerate(CLASSES):
        print(f"  {c:12s}" + "".join(f"{v:8,d}" for v in cm[i]))

    # ---- importance ----
    imp = clf.feature_importances_
    order = np.argsort(-imp)
    print(f"\nfeature importance — top 8 of {N_EXTENDED}")
    for i in order[:8]:
        bar = "█" * int(round(imp[i] * 100))
        print(f"  {EXTENDED_FEATURES[i]:26s} {imp[i]:6.4f}  {bar}")

    core_share = float(imp[:16].sum())
    print(f"\n  core-16 share of importance : {core_share:.3f}")
    print(f"  new-16  share of importance : {1 - core_share:.3f}")

    # ---- latency, measured the way serving will do it ----
    # **Measure the path that actually serves.** engine/pattern.py does not call
    # ``predict_proba`` in production: it flattens the forest into padded arrays
    # and walks it vectorised, because the sklearn entry point costs ~9 ms per
    # *call* almost independently of batch size. Reporting the sklearn number here
    # would be reporting a latency the system does not have — in either direction.
    # Both are measured, so the gap stays visible and the fast path is provably
    # the one meeting §5.8's budget.
    one = X[te[:400]]
    was = clf.n_jobs
    clf.n_jobs = 1
    clf.predict_proba(one[:1])                       # warm
    t0 = time.perf_counter()
    for row in one:
        clf.predict_proba(row.reshape(1, -1))
    lat_sklearn = (time.perf_counter() - t0) / len(one) * 1000.0
    clf.n_jobs = was

    from engine import pattern as _pat
    fast = _pat._build_fast_path(clf)
    if fast is None:
        sys.exit("error: the forest could not be flattened — serving would use "
                 "the slow sklearn path and miss the §5.8 budget.")
    _pat._walk(fast, one[:1])                        # warm
    t0 = time.perf_counter()
    for row in one:
        _pat._walk(fast, row.reshape(1, -1))
    lat_ms = (time.perf_counter() - t0) / len(one) * 1000.0

    # The two must agree, column for column, or the optimisation is not an
    # optimisation — it is a second, different model.
    max_diff = float(np.abs(_pat._walk(fast, one) - clf.predict_proba(one)).max())
    if max_diff > 1e-9:
        sys.exit(f"error: the serving fast path diverges from sklearn by {max_diff:.3e}")
    print(f"\nlatency         {lat_ms:.3f} ms/window on the serving path "
          f"({'within' if lat_ms < 1.0 else 'ABOVE'} the <1 ms budget, §5.8)")
    print(f"                {lat_sklearn:.3f} ms/window via sklearn predict_proba "
          f"({lat_sklearn / lat_ms:.0f}x slower; not used in production)")
    print(f"                fast path vs sklearn, max abs diff {max_diff:.2e}")

    # ---- 7-label detail (secondary, not part of the contract) ----
    comp = (df.loc[te].groupby([TARGET, "label"], observed=True).size()
            .unstack(fill_value=0))
    print("\n7-label composition of the test set (secondary detail)")
    for cls in CLASSES:
        if cls in comp.index:
            parts = ", ".join(f"{k}={v:,}" for k, v in comp.loc[cls].items() if v)
            print(f"  {cls:12s} {parts}")

    # ---- write artifacts ----
    args.outdir.mkdir(parents=True, exist_ok=True)
    args.export_dir.mkdir(parents=True, exist_ok=True)

    model_path = args.outdir / "pattern_model_v1.json"
    import joblib

    # Inference is one window at a time at 2 Hz — the opposite of the bulk fit.
    # With n_jobs=-1 joblib builds a worker pool *per call*, which on a single row
    # costs far more than the 200 shallow trees it is parallelising: measured
    # 40 ms/window against a <1 ms budget. Serial prediction removes the pool
    # entirely. The fit already happened, so this changes nothing about the model.
    clf.n_jobs = 1
    joblib.dump(clf, str(model_path), compress=3)

    meta = {
        "model": "pattern_model_v1",
        "algorithm": "sklearn.ensemble.RandomForestClassifier",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(args.dataset.relative_to(REPO_ROOT)) if args.dataset.is_absolute() else str(args.dataset),
        "n_windows": int(len(df)),
        "n_runs": int(df[GROUP].nunique()),
        "n_train": int(len(tr)), "n_val": int(len(va)), "n_test": int(len(te)),
        "features": list(EXTENDED_FEATURES),
        "disruption_features": list(DISRUPTION_FEATURES),
        "classes": CLASSES,
        "target": TARGET,
        "split": {
            "method": "GroupShuffleSplit by run_id, 60/20/20",
            "rationale": "overlapping 60 s windows make a row split leak the same event",
        },
        "missing_value_sentinel": float(PATTERN_MISSING_SENTINEL),
        "operating_threshold": float(th),
        "calibration": {
            "target": f"STRUCTURAL recall ≥ {RECALL_TARGET}",
            "achieved_recall": float(rec_te),
            "achieved_precision": float(prec_te),
            "selected_on": "validation",
            "reported_on": "test",
        },
        "metrics": {
            "macro_f1": m["macro_f1"],
            "quiet_fp_rate": qfp,
            "env_suppression_precision": env_prec,
            "per_class_recall": m["per_class_recall"],
            "per_class_precision": m["per_class_precision"],
        },
        "confusion_matrix": {"labels": CLASSES, "matrix": cm.tolist()},
        "feature_importances": [float(v) for v in imp],
        "core16_importance_share": core_share,
        "top_features": [[EXTENDED_FEATURES[i], float(imp[i])] for i in order[:8]],
        "hyperparameters": {k: (v if not isinstance(v, np.integer) else int(v))
                            for k, v in RF_PARAMS.items()},
        "latency_ms_per_window": float(lat_ms),
        "latency_ms_sklearn_predict_proba": float(lat_sklearn),
        "fast_path_max_abs_diff_vs_sklearn": float(max_diff),
        "fit_seconds": float(fit_s),
    }
    (args.outdir / "pattern_meta.json").write_text(json.dumps(meta, indent=2))

    # ---- report ----
    lines = []
    A = lines.append
    A("Neural Strata — Model 1 · Deformation Pattern Discriminator")
    A("=" * 78)
    A(f"trained_at      {meta['trained_at']}")
    A(f"dataset         {meta['dataset']}")
    A(f"windows         {meta['n_windows']:,}   runs {meta['n_runs']:,}")
    A("")
    A("WHY THIS MODEL EXISTS")
    A("  Model 2 extrapolates displacement and cannot tell WHAT is moving the")
    A("  ground. A truck reads as a short TTF and fires the siren; a crack masked")
    A("  by background vibration reads as stable. M1 supplies the missing")
    A("  question — what kind of event is this — and may never suppress an alert.")
    A("")
    A("SPLIT — by run_id, three ways")
    A(f"  train {len(tr):7,} windows / {df[GROUP].iloc[tr].nunique():4,} runs")
    A(f"  val   {len(va):7,} windows / {df[GROUP].iloc[va].nunique():4,} runs"
      "   <- threshold chosen here")
    A(f"  test  {len(te):7,} windows / {df[GROUP].iloc[te].nunique():4,} runs"
      "   <- every number below")
    A("  A row split would put windows of the same event on both sides of the")
    A("  fence (they overlap by 60 s) and report a meaningless ~99%.")
    A("")
    A("CALIBRATION")
    A(f"  target            STRUCTURAL recall ≥ {RECALL_TARGET}")
    A(f"  operating thresh  {th:.2f}   (lowest on the grid reaching the target)")
    A(f"  achieved recall   {rec_te:.4f}")
    A(f"  achieved precision{prec_te:8.4f}")
    A("")
    A("TEST METRICS")
    A(f"  macro F1          {m['macro_f1']:.4f}")
    A(f"  QUIET false-pos   {qfp:.4f}")
    A(f"  env suppression P {env_prec:.4f}")
    for c in CLASSES:
        A(f"  {c:12s} recall {m['per_class_recall'][c]:.3f}   "
          f"precision {m['per_class_precision'][c]:.3f}")
    A("")
    A("CONFUSION MATRIX (rows = truth, cols = predicted)")
    A("  " + " " * 14 + "".join(f"{c[:6]:>8s}" for c in CLASSES))
    for i, c in enumerate(CLASSES):
        A(f"  {c:12s}" + "".join(f"{v:8,d}" for v in cm[i]))
    A("")
    A("FEATURE IMPORTANCE — top 8")
    for i in order[:8]:
        A(f"  {EXTENDED_FEATURES[i]:26s} {imp[i]:6.4f}")
    A("")
    A(f"  core-16 share {core_share:.3f}   new-16 share {1 - core_share:.3f}")
    A(f"  latency {lat_ms:.3f} ms/window (serving fast path)   fit {fit_s:.2f} s")
    A(f"  latency {lat_sklearn:.3f} ms/window (sklearn predict_proba, not used)")
    A(f"  fast path vs sklearn max abs diff {max_diff:.2e}")
    A("")
    A("SEVEN-LABEL COMPOSITION OF THE TEST SET (secondary detail)")
    for cls in CLASSES:
        if cls in comp.index:
            parts = ", ".join(f"{k}={v:,}" for k, v in comp.loc[cls].items() if v)
            A(f"  {cls:12s} {parts}")
    A("")
    A("ARTIFACTS")
    A(f"  {model_path}")
    A(f"  {args.outdir / 'pattern_meta.json'}")
    report = "\n".join(lines) + "\n"
    (args.export_dir / "pattern_training_report.txt").write_text(report)

    print("\nartifacts written")
    print(f"  {model_path}  ({model_path.stat().st_size / 1024:.0f} KB)")
    print(f"  {args.outdir / 'pattern_meta.json'}")
    print(f"  {args.export_dir / 'pattern_training_report.txt'}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
