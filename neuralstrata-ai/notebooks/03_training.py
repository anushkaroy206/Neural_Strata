#!/usr/bin/env python3
"""
Neural Strata — Phase 4.3 · Train the TTF model (XGBoost)  [v2 split]
======================================================================
Reads:  neuralstrata-ai/datasets/live_drill_dataset.csv   (from PostgreSQL export)
Writes: neuralstrata-backend/models/ttf_model_v1.json   <- app.py loads this
        neuralstrata-backend/models/scaler.json          <- feature scaling contract
        neuralstrata-backend/models/model_meta.json      <- metrics for /api/ai
        neuralstrata-ai/export/training_report.txt       <- record of this run

Contract (must stay in sync with app.py feature code in Phase 4.4):
    FEATURES below = the 16 numeric columns, same names, same 2 Hz cadence.
    Target: ttf_label (seconds until 100 mm breach, capped 3600 for stable).

Split design (v2 — why it differs from a naive by-run split):
  * The dataset has only a handful of SLOW-CREEP (warning) runs. A strict
    by-run split can leave zero warning rows in training, and the model then
    predicts "stable" through an entire warning drill (validated on a
    synthetic replica of the drill dynamics: MAE 435 s vs 0.1 s).
  * So: ONE critical run is held out as the honest out-of-run test (primary
    metric), one critical run is the early-stopping validation set, and the
    warning-run rows are trained on AND evaluated (reported as in-sample*).
    Live warning drills re-run the identical injection function, so this is
    representative of demo behaviour and is disclosed in the report.
  * Breach rows carry sample weight 5 (stable:breach is ~9:1 in the dataset).
  * Fallback: if fewer than 2 critical runs exist, a classic by-run split is
    used instead.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.metrics import (
    accuracy_score,
    mean_absolute_error,
    mean_squared_error,
    precision_score,
    recall_score,
)
from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler

AI_ROOT = Path(__file__).resolve().parents[1]                 # .../neuralstrata-ai
DATASET = AI_ROOT / "datasets" / "drill_dataset.csv"     # <--- CHANGED TO LIVE DATA
BACKEND_MODELS = AI_ROOT.parent / "neuralstrata-backend" / "models"
EXPORT_DIR = AI_ROOT / "export"

FEATURES = [
    "disp_now", "d10", "d30", "d60",
    "v10", "v30", "v60", "disp_max60",
    "accel_now", "accel_mean60", "accel_std60", "accel_max60", "jerk",
    "tilt_now", "tilt_d30", "ttf_fuk",
]

STABLE_LABEL = 3600.0
BIN_THRESHOLD_S = 300.0   # "breach within 5 min" binary task
BREACH_WEIGHT = 5.0       # sample weight on breach rows (stable:breach ~9:1)
WARN_RUN_LABEL_S = 12.0   # breach runs whose max label exceeds this = slow-creep (warning) runs


def rmse(y, p):
    return float(np.sqrt(mean_squared_error(y, p)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=str(DATASET))
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    missing = [c for c in FEATURES + ["ttf_label", "run_id", "run_class"] if c not in df.columns]
    if missing:
        sys.exit(f"ERROR: dataset is missing columns {missing} — re-run 01_data_extraction.py")
    df = df.dropna(subset=["d60"])  # rows without a full 60 s window (shouldn't exist)
    print(f"[data] {len(df)} windows, {df['run_id'].nunique()} runs, "
          f"breach {(df['run_class'] == 'BREACH').sum()} windows / "
          f"stable {(df['run_class'] == 'STABLE').sum()} windows")

    # ---------- identify run types ----------
    run_cls = df.groupby("run_id")["run_class"].first()
    run_max = df.groupby("run_id")["ttf_label"].max()
    warn_runs = sorted(set(run_cls.index[(run_cls == "BREACH") & (run_max > WARN_RUN_LABEL_S)]))
    crit_runs = sorted(set(run_cls.index[(run_cls == "BREACH") & (run_max <= WARN_RUN_LABEL_S)]))
    print(f"[runs] critical (fast): {crit_runs}")
    print(f"[runs] warning (slow):  {warn_runs}")

    # ---------- split (v2) ----------
    fallback = False
    if len(crit_runs) >= 2:
        rng = np.random.default_rng(42)
        held = [str(rng.choice(np.asarray(crit_runs, dtype=object), size=1, replace=False)[0])]
        others = np.asarray([r for r in crit_runs if r != held[0]], dtype=object)
        val = [str(rng.choice(others, size=1, replace=False)[0])]
        fit = [r for r in run_cls.index if r not in set(held) | set(val)]  # includes warning rows
        test_ids = set(held) | set(warn_runs)
        split_desc = ("1 held-out critical run (out-of-run) + 1 validation critical run; "
                      "warning-run rows trained on and evaluated (in-sample, disclosed)")
    else:
        fallback = True
        train_runs, test_ids_t = train_test_split(
            run_cls.index, test_size=0.35, stratify=run_cls, random_state=42)
        fit_runs, val_runs = train_test_split(train_runs, test_size=0.25, random_state=42)
        fit, val, held = list(fit_runs), list(val_runs), list(test_ids_t[:1])
        test_ids = set(test_ids_t)
        split_desc = "classic by-run split (fallback: <2 critical runs)"

    tr = df[df["run_id"].isin(fit)]
    va = df[df["run_id"].isin(val)]
    te = df[df["run_id"].isin(test_ids)]
    print(f"[split] {split_desc}")
    print(f"[split] fit: {len(fit)} runs ({len(tr)} windows) | "
          f"val: {len(val)} runs ({len(va)} windows) | "
          f"test: {len(test_ids)} runs ({len(te)} windows)")

    # ---------- scale (fit on fit-runs only) ----------
    scaler = StandardScaler()
    Xtr = pd.DataFrame(scaler.fit_transform(tr[FEATURES]), columns=FEATURES, index=tr.index)
    Xva = pd.DataFrame(scaler.transform(va[FEATURES]), columns=FEATURES, index=va.index)
    Xte = pd.DataFrame(scaler.transform(te[FEATURES]), columns=FEATURES, index=te.index)

    # ---------- train ----------
    model = xgb.XGBRegressor(
        n_estimators=400,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.9,
        colsample_bytree=0.9,
        min_child_weight=5,
        reg_lambda=1.0,
        early_stopping_rounds=50,
        random_state=42,
        n_jobs=2,
    )
    sw = np.where(tr["run_class"].to_numpy() == "BREACH", BREACH_WEIGHT, 1.0)
    model.fit(Xtr, tr["ttf_label"], sample_weight=sw,
              eval_set=[(Xva, va["ttf_label"])], verbose=False)
    best_iter = getattr(model, "best_iteration", None)
    print(f"[train] XGBoost fit — best_iteration = {best_iter} (breach rows weighted {BREACH_WEIGHT}x)")

    # ---------- evaluate ----------
    pred_te = np.clip(model.predict(Xte), 0.0, STABLE_LABEL)
    y_te = te["ttf_label"].to_numpy(float)
    is_warn_row = te["run_id"].isin(warn_runs).to_numpy()
    is_held_row = te["run_id"].isin(set(held)).to_numpy() if not fallback else None
    is_stable_row = (y_te >= STABLE_LABEL)

    def seg(mask):
        if mask.sum() == 0:
            return None, None, 0
        return mean_absolute_error(y_te[mask], pred_te[mask]), rmse(y_te[mask], pred_te[mask]), int(mask.sum())

    mae_held, rmse_held, n_held = seg(is_held_row) if is_held_row is not None else (None, None, 0)
    mae_warn, rmse_warn, n_warn = seg(is_warn_row)
    mae_st, rmse_st, n_st = seg(is_stable_row)
    mae_all = mean_absolute_error(y_te, pred_te)
    rmse_all = rmse(y_te, pred_te)

    yb = (y_te <= BIN_THRESHOLD_S).astype(int)
    pb = (pred_te <= BIN_THRESHOLD_S).astype(int)
    acc = accuracy_score(yb, pb)
    prec = precision_score(yb, pb, zero_division=0)
    rec = recall_score(yb, pb, zero_division=0)

    booster = model.get_booster()
    gain = booster.get_score(importance_type="gain")
    imp = sorted(((f, gain.get(f, 0.0)) for f in FEATURES), key=lambda t: -t[1])

    # ---------- report ----------
    L = []
    L.append("=== NEURAL STRATA — TRAINING REPORT (Phase 4.3, v2 split) ===")
    L.append(f"generated:   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    L.append(f"dataset:     {args.csv}  ({len(df)} windows)")
    L.append(f"split:       {split_desc}")
    L.append(f"model:       XGB 400 trees max, depth 4, lr 0.05, breach weight {BREACH_WEIGHT}x, "
             f"early-stop @ {best_iter}")
    L.append("")
    L.append("--- TTF regression (seconds) ---")
    if mae_held is not None:
        L.append(f"HELD-OUT critical run (true out-of-run):  MAE {mae_held:6.2f} s   RMSE {rmse_held:6.2f} s   (n={n_held})")
    L.append(f"warning slow-creep (in-sample*):            "
             + (f"MAE {mae_warn:6.2f} s   RMSE {rmse_warn:6.2f} s   (n={n_warn})"
                if mae_warn is not None else "n/a (no slow-creep runs in dataset)"))
    L.append(f"stable windows in test:                     "
             + (f"MAE {mae_st:6.2f} s   (n={n_st})" if mae_st is not None else "n/a"))
    L.append(f"overall test set:                           MAE {mae_all:6.2f} s   RMSE {rmse_all:6.2f} s   (n={len(te)})")
    if warn_runs:
        L.append("* only a few slow-creep runs exist in the dataset, so those rows were")
        L.append("  also used in training; live warning drills re-run the identical injection")
        L.append("  function, so this figure is representative of demo behaviour.")
    L.append("")
    L.append(f"--- Binary: 'breach within {int(BIN_THRESHOLD_S)} s' (test set) ---")
    L.append(f"accuracy {acc:.3f}   precision {prec:.3f}   recall {rec:.3f}")
    L.append("")
    L.append("--- Feature importance (gain) ---")
    for i, (f, g) in enumerate(imp, 1):
        L.append(f"{i:2d}. {f:<14s} {g:12.1f}")
    L.append("")
    report = "\n".join(L)
    print(report)

    # ---------- export ----------
    BACKEND_MODELS.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    model_path = BACKEND_MODELS / "live_ttf_model.json"
    model.save_model(model_path)

    (BACKEND_MODELS / "scaler.json").write_text(json.dumps({
        "features": FEATURES,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
    }, indent=2))

    meta = {
        "model": "live_ttf_model_v1",
        "algorithm": "xgboost.XGBRegressor",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset": str(args.csv),
        "n_windows_total": int(len(df)),
        "split": {
            "design": "v2",
            "held_out_critical_run": held if not fallback else None,
            "validation_run": val if not fallback else None,
            "warning_runs_trained_and_evaluated": warn_runs,
            "breach_sample_weight": BREACH_WEIGHT,
        },
        "runs_test": sorted(test_ids),
        "best_iteration": int(best_iter) if best_iter is not None else None,
        "metrics": {
            "held_out_critical_mae_s": round(mae_held, 2) if mae_held is not None else None,
            "held_out_critical_rmse_s": round(rmse_held, 2) if rmse_held is not None else None,
            "warning_insample_mae_s": round(mae_warn, 2) if mae_warn is not None else None,
            "warning_insample_rmse_s": round(rmse_warn, 2) if rmse_warn is not None else None,
            "stable_mae_s": round(mae_st, 2) if mae_st is not None else None,
            "overall_mae_s": round(mae_all, 2),
            "overall_rmse_s": round(rmse_all, 2),
            "bin_5min_accuracy": round(acc, 3),
            "bin_5min_precision": round(prec, 3),
            "bin_5min_recall": round(rec, 3),
        },
        "stable_label": STABLE_LABEL,
        "binary_threshold_s": BIN_THRESHOLD_S,
    }
    (BACKEND_MODELS / "live_model_meta.json").write_text(json.dumps(meta, indent=2))
    (EXPORT_DIR / "live_training_report.txt").write_text(report)

    print(f"saved: {model_path}")
    print(f"saved: {BACKEND_MODELS / 'scaler.json'}")
    print(f"saved: {BACKEND_MODELS / 'live_model_meta.json'}")
    print(f"saved: {EXPORT_DIR / 'live_training_report.txt'}")
    print("\nNext: Phase 4.4 — app.py loads the model and drives ttf_seconds.")

if __name__ == "__main__":
    main()