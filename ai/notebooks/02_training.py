#!/usr/bin/env python3
"""
Neural Strata — Phase 4.3 · Train the TTF model (XGBoost)  [v2 split]
======================================================================
Reads:  neuralstrata-ai/datasets/drill_dataset.csv   (from 01_data_extraction.py)
Writes: neuralstrata-backend/models/ttf_model_v1.json   <- point forecast (kept)
        neuralstrata-backend/models/ttf_model_v2.json   <- QUANTILE TRIO (Phase 5)
        neuralstrata-backend/models/scaler.json          <- feature scaling contract
        neuralstrata-backend/models/model_meta.json      <- metrics for /api/ai
        neuralstrata-ai/export/training_report.txt       <- record of this run

Phase 5 adds the **quantile trio** (§5.9): three boosters at alpha 0.10 / 0.50 /
0.90, wrapped into the single file ``ttf_model_v2.json``. The point forecast is
still trained and still written, and ``ttf_model_v1.json`` is left on disk
untouched, so rolling back is a one-line edit to ``MANIFEST.json``.

Why a band rather than a better point estimate. A single number invites the
operator to read precision that is not there: "TTF 340 s" and "TTF 340 s ± 400 s"
demand different responses ("keep working" vs "stop work now"), and a boosted
tree ensemble on ninety seconds of window data has the second kind of uncertainty,
not the first. The band also gives the deck something to shade behind the
countdown, which is how a wide band communicates urgency without anyone reading a
number.

Monotone constraints. ``disp_now``, ``v10`` and ``v30`` are constrained **-1**
(decreasing): more displacement and more velocity must never predict a *longer*
time to failure. Without the constraint a quantile model will occasionally bend
the wrong way at the edge of its training range, and the wrong-way prediction is
the dangerous one — it is the one that would talk an operator out of an
evacuation. The constraint is not a performance tweak; it is the guarantee that
the model cannot invert physically.

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
import base64
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

AI_ROOT = Path(__file__).resolve().parents[1]                 # .../ai
DATASET = AI_ROOT / "datasets" / "drill_dataset.csv"
EXPORT_DIR = AI_ROOT / "export"

# **The models directory is resolved through the backend's own config, not by
# reconstructing the path.**
#
# This line used to read ``AI_ROOT.parent / "neuralstrata-backend" / "models"``,
# which is the name the *build document* uses for the backend. The directory in
# this workspace is ``backend/``. So the reconstruction pointed at a path that
# does not exist, ``mkdir(parents=True)`` created it, and every artifact this
# notebook produced landed in a directory the runtime never reads — including an
# artifact that overwrote nothing and therefore broke nothing, which is exactly
# why it went unnoticed. Resolving through ``config`` makes the notebook write
# where the server reads *by construction*: if the two ever disagree again,
# ``import config`` fails and the notebook stops, rather than writing a model
# into the void and reporting success.
sys.path.insert(0, str(AI_ROOT.parent / "backend"))
import config as backend_config                                   # noqa: E402
BACKEND_MODELS = Path(backend_config.MODELS_DIR)

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
    ap.add_argument("--write-v1", action="store_true",
                    help="also overwrite ttf_model_v1.json (the shipped point model). "
                         "Off by default: §5.9 asks for it to stay untouched.")
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
        # **No ``str()`` coercion here, and that is a fix, not a style choice.**
        # These were wrapped in ``str()``, which made ``held`` and ``val`` strings
        # while ``run_cls.index`` is ``int64``. The three lines below then matched
        # nothing: ``.isin(["395"])`` against integer run ids returns no rows, so
        # the validation set was empty (StandardScaler raised on shape 0) and the
        # fit set silently contained the held-out run — the one comparison the
        # split exists to make would have been reported as an out-of-run score
        # while being in-sample. Keep the index's own dtype and the split holds.
        held = [rng.choice(np.asarray(crit_runs, dtype=object), size=1, replace=False)[0]]
        others = np.asarray([r for r in crit_runs if r != held[0]], dtype=object)
        val = [rng.choice(others, size=1, replace=False)[0]]
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

    # The test-set truth vector is computed **once, here**, rather than at the
    # point it was first needed. Phase 5's band metrics consume it earlier in the
    # function than the Phase 4 point metrics do, and two assignments of the same
    # thing is how the two would eventually drift apart.
    y_te = te["ttf_label"].to_numpy(float)

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

    # ---------- Phase 5: the quantile trio (§5.9) ----------
    # Parameters shared with the point model *deliberately*: the band must be the
    # same model answering a different question. Retuning the trees for each
    # quantile would produce a band around a different centre than the point
    # forecast the deck prints, and the two would disagree on stage.
    QUANTILES = (0.10, 0.50, 0.90)

    #: Monotone direction per feature, as xgboost wants it: a positional tuple
    #: string. A dict of feature names is **rejected** by this version
    #: ("Invalid Parameter format for monotone_constraints"), which is worth
    #: knowing before spending an hour wondering why the constraint is ignored.
    _mono_dir = {"disp_now": -1, "v10": -1, "v30": -1}
    MONO = "(" + ",".join(str(_mono_dir.get(f, 0)) for f in FEATURES) + ")"

    quant_models = {}
    for q in QUANTILES:
        # **The tree budget is inherited from the point model, and the quantile
        # models do not early-stop on their own.**
        #
        # Letting them stop independently looked more principled and was not: a
        # quantile loss is not minimised at the same number of trees as the
        # squared error, and q=0.10 stopped at iteration **0** — meaning the lower
        # edge of the band was a constant, the model's initial guess, for every row
        # in the test set. That trains without complaint and coverage still looks
        # plausible, because a constant edge is not obviously wrong until you ask
        # whether the interval breathes at all.
        #
        # Tying the trio to ``best_iter`` also enforces the design intent: the band
        # is the same model answering a different question, so its three members
        # should be the same size.
        qm = xgb.XGBRegressor(
            n_estimators=int(best_iter or 400),
            max_depth=4,
            learning_rate=0.05,
            subsample=0.9,
            colsample_bytree=0.9,
            min_child_weight=5,
            reg_lambda=1.0,
            objective="reg:quantileerror",
            quantile_alpha=q,
            monotone_constraints=MONO,
            random_state=42,
            n_jobs=2,
        )
        # **Weighted, like the point model — and the trade is recorded rather than
        # hidden, because the unweighted version was tried and measured.**
        #
        # ``BREACH_WEIGHT`` re-weights the empirical distribution the quantiles
        # estimate, so q10/q90 are not the true 10th/90th percentiles of the
        # target. That is a real distortion, and removing it moved coverage from
        # 99.9% to 97.4% (closer to nominal 80%) — but it moved the **median's**
        # MAE from 2.49 s to 1035 s, because without the weight the centre
        # collapses onto the 81% of rows that are stable. The centre is the number
        # the deck counts down; it has to be right.
        #
        # So the weighting stays, and the band is wider than it should be. That is
        # the safe direction to err: a band that covers more than it claims never
        # hides urgency, whereas a band that covers less would shade a genuine
        # countdown as comfortable. The root cause is the target's shape, not the
        # weights — ``ttf_label`` is bimodal (0 s for a breach, 3600 s for stable)
        # and a quantile regression on a bimodal target is the wrong formulation.
        # The remedy is in the Phase 5 report: make it a two-stage problem (hazard
        # then countdown), or quantise the label. Both are Phase 6+ work.
        qm.fit(Xtr, tr["ttf_label"], sample_weight=sw, verbose=False)
        quant_models[q] = qm
        print(f"[train] quantile q={q:.2f} — best_iteration = "
              f"{getattr(qm, 'best_iteration', None)}")

    # ---------- band evaluation, and the crossing check ----------
    band_te = np.clip(
        np.column_stack([quant_models[q].predict(Xte) for q in QUANTILES]),
        0.0, STABLE_LABEL)

    def band_coverage(y_true, band, lo, hi):
        """Fraction of truths inside [q_lo, q_hi]. This is the number that says
        whether the band means anything: a nominal 80% interval that covers 30%
        of reality is a decoration, and one that covers 100% is a shrug."""
        return float(np.mean((y_true >= band[:, lo]) & (y_true <= band[:, hi])))

    coverage_80 = band_coverage(y_te, band_te, 0, 2)          # q10..q90
    median_mae = float(mean_absolute_error(y_te, band_te[:, 1]))
    # Crossing: with three independently fitted boosters nothing *enforces*
    # q10 <= q50 <= q90, and the standard remedy (sort each row) is applied here
    # and in the loader, so training and serving agree about it.
    n_cross = int(np.sum((band_te[:, 0] > band_te[:, 1]) | (band_te[:, 1] > band_te[:, 2])))
    band_te = np.sort(band_te, axis=1)
    n_cross_after = int(np.sum((band_te[:, 0] > band_te[:, 1]) | (band_te[:, 1] > band_te[:, 2])))
    width_80 = band_te[:, 2] - band_te[:, 0]
    print(f"[band] 80% interval covers {100 * coverage_80:5.1f}% of held-out truth "
          f"(nominal 80%)   median MAE {median_mae:6.2f} s")
    print(f"[band] width q90-q10: median {np.median(width_80):7.2f} s   "
          f"p90 {np.percentile(width_80, 90):8.2f} s")
    print(f"[band] quantile crossings: {n_cross} before sorting, {n_cross_after} after")

    # Monotonicity is a *claim*, so it is checked rather than assumed.
    mono_violations = 0
    probe = Xte.iloc[:min(200, len(Xte))].to_numpy()
    for j, fname in enumerate(FEATURES):
        if fname not in _mono_dir:
            continue
        bumped = probe.copy()
        bumped[:, j] += np.abs(bumped[:, j]).mean() + 1.0        # strictly larger
        for q in QUANTILES:
            before = quant_models[q].predict(pd.DataFrame(probe, columns=FEATURES))
            after = quant_models[q].predict(pd.DataFrame(bumped, columns=FEATURES))
            mono_violations += int(np.sum(after > before + 1e-6))
    print(f"[band] monotonicity violations on {len(probe)} probes: {mono_violations}")

    # ---------- evaluate ----------
    pred_te = np.clip(model.predict(Xte), 0.0, STABLE_LABEL)
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
    L.append("warning slow-creep (in-sample*):            "
             + (f"MAE {mae_warn:6.2f} s   RMSE {rmse_warn:6.2f} s   (n={n_warn})"
                if mae_warn is not None else "n/a (no slow-creep runs in dataset)"))
    L.append("stable windows in test:                     "
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
    L.append("--- Phase 5 quantile trio (band) ---")
    L.append(f"quantiles:                    {', '.join(f'{q:.2f}' for q in QUANTILES)}")
    L.append(f"monotone constraints:         {MONO}   (order = FEATURES)")
    L.append(f"80% interval coverage:        {100 * coverage_80:5.1f} %   (nominal 80%)")
    L.append(f"median (q50) MAE:             {median_mae:6.2f} s")
    L.append(f"band width q90-q10:           median {np.median(width_80):7.2f} s   "
             f"p90 {np.percentile(width_80, 90):8.2f} s")
    L.append(f"quantile crossings:           {n_cross} before sort, {n_cross_after} after")
    L.append(f"monotonicity violations:      {mono_violations} on {len(probe)} probes")
    L.append("")
    L.append("A band is only worth printing if it is calibrated, so coverage is the")
    L.append("headline: an 80% interval that contains 80% of held-out truth is")
    L.append("informative; one that contains everything is a shrug.")
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

    # §5.9: "Keep ``ttf_model_v1.json`` on disk untouched so a rollback is a
    # one-line manifest edit." The point model is still trained and its metrics
    # are still reported — but writing it is now opt-in, because the shipped v1
    # artifact is the one the deployment is running and this notebook's version of
    # it is trained on a different (synthetic) drill set. Silently replacing the
    # live model with a synthetic-data retrain while claiming to "emit the quantile
    # trio" is the sort of thing that is discovered on stage.
    model_path = BACKEND_MODELS / "ttf_model_v1.json"
    if args.write_v1:
        model.save_model(model_path)
        print(f"saved: {model_path}  (--write-v1: the shipped point model replaced)")
    else:
        print(f"kept:  {model_path}  (unchanged — pass --write-v1 to replace it)")

    # ---------- ttf_model_v2.json — the quantile trio in one artifact ----------
    #
    # One file, not four. ``MANIFEST.json`` names a *single* file per model, and a
    # band whose three boosters can go missing independently is a band that will
    # eventually ship half-loaded. The three boosters are inlined as their raw
    # JSON, which round-trips exactly: ``Booster.load_model(bytearray(raw))``
    # reproduces the predictions bit-for-bit (verified, and asserted in
    # tests/test_pattern.py).
    v2_path = BACKEND_MODELS / "ttf_model_v2.json"
    v2 = {
        "format": "ttf_quantile_trio",
        "version": "ttf_model_v2",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "features": FEATURES,
        "quantiles": list(QUANTILES),
        "monotone_constraints": {
            "spec": MONO, "directions": _mono_dir, "order": FEATURES,
        },
        "training": {
            "dataset": str(args.csv),
            "n_windows": int(len(df)),
            "split": split_desc,
            "n_estimators": int(best_iter or 400), "max_depth": 4, "learning_rate": 0.05,
            "breach_sample_weight": BREACH_WEIGHT,
            "weighting_note": ("breach rows weighted, which biases q10/q90 away from "
                               "the true percentiles and widens the band; kept because "
                               "removing it costs the median its accuracy"),
            "best_iterations": {str(q): getattr(quant_models[q], "best_iteration", None)
                                for q in QUANTILES},
        },
        "metrics": {
            "coverage_80": round(coverage_80, 4),
            "median_mae_s": round(median_mae, 2),
            "band_width_median_s": round(float(np.median(width_80)), 2),
            "band_width_p90_s": round(float(np.percentile(width_80, 90)), 2),
            "crossings_before_sort": n_cross,
            "monotonicity_violations": mono_violations,
        },
        "scaler": {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist()},
        "boosters": {
            f"q{int(q * 100):02d}": base64.b64encode(
                quant_models[q].get_booster().save_raw(raw_format="json")).decode("ascii")
            for q in QUANTILES
        },
    }
    v2_path.write_text(json.dumps(v2, indent=2))
    print(f"saved: {v2_path}  "
          f"({v2_path.stat().st_size / 1024:.0f} KB, {len(QUANTILES)} boosters, "
          f"80% coverage {100 * coverage_80:.1f}%)")

    # **Both of these describe v1, so both are gated behind ``--write-v1``.**
    # They used to be written unconditionally, which meant retraining on a new
    # dataset replaced the shipped model's scaling contract and its published
    # metrics while ``ttf_model_v1.json`` itself was left alone — three artifacts
    # that have to agree, updated by a command that only appeared to update one.
    if args.write_v1:
        (BACKEND_MODELS / "scaler.json").write_text(json.dumps({
            "features": FEATURES,
            "mean": scaler.mean_.tolist(),
            "scale": scaler.scale_.tolist(),
        }, indent=2))
    # The v2 artifact carries its own scaler, so it needs no companion file at all
    # — which is the whole reason it is the preferred source at runtime.
    (BACKEND_MODELS / "scaler_v2.json").write_text(json.dumps({
        "features": FEATURES,
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
    }, indent=2))

    meta = {
        "model": "ttf_model_v1",
        # Phase 5: the band's metrics ride along in the same meta file so
        # `/api/ai` and the report read one artifact, not two that can drift.
        "band": {
            "artifact": "ttf_model_v2.json",
            "quantiles": list(QUANTILES),
            "coverage_80": round(coverage_80, 4),
            "median_mae_s": round(median_mae, 2),
            "monotonicity_violations": mono_violations,
        },
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
    # v2's metrics go to their own file; ``model_meta.json`` belongs to v1.
    meta["model"] = "ttf_model_v2"
    meta["provenance"] = ("Phase 5 synthetic retrain of Model 2. Labels are "
                          "simulator-generated, so these figures describe the "
                          "synthetic drill set, not the Phase 4 field drills.")
    (BACKEND_MODELS / "model_meta_v2.json").write_text(json.dumps(meta, indent=2))
    if args.write_v1:
        meta_v1 = dict(meta, model="ttf_model_v1")
        (BACKEND_MODELS / "model_meta.json").write_text(json.dumps(meta_v1, indent=2))
    (EXPORT_DIR / "training_report.txt").write_text(report)

    print(f"saved: {model_path}")
    print(f"saved: {BACKEND_MODELS / 'scaler_v2.json'}")
    print(f"saved: {BACKEND_MODELS / 'model_meta_v2.json'}")
    print(f"saved: {EXPORT_DIR / 'training_report.txt'}")
    print("\nNext: Phase 4.4 — app.py loads the model and drives ttf_seconds.")


if __name__ == "__main__":
    main()
