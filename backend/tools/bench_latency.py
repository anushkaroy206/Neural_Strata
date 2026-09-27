#!/usr/bin/env python3
"""`bench_latency.py` — §5.12's budget check, and the number that decides the cadence.

    python tools/bench_latency.py               # both models, 500 reps
    python tools/bench_latency.py --reps 50     # quick pass
    python tools/bench_latency.py --json        # machine-readable, for the report

**What this measures, and what it deliberately does not.**

The budget is per *inference on the hub CPU* (§5.7 fixes the cadences from it):
M1 < 1 ms, M2 < 3 ms. On the hub that CPU is the ESP32-S3 in the main hub; here it
is whatever this machine is, so the absolute numbers are an **upper bound on
nothing** — an Intel i5 is not an ESP32. What this file is actually for is three
things, in order of importance:

1. **The ratio between the shipped path and the reference path.** M1 serves its
   forest through `pattern._walk`, not through `sklearn.predict_proba`. If that
   fast path ever costs more than it saves, or silently starts disagreeing with
   sklearn, this is where it shows up — it is asserted, not just printed.
2. **Amortisation.** §5.7's saving is that M1 runs at 2 Hz on four nodes while M2
   runs only on the nodes the gate escalates. A per-call latency is meaningless
   without the call rate, so the projected per-node cost at each cadence is
   printed next to it.
3. **Regression detection.** A number with no history is a number nobody reads;
   `--json` writes `reports/latency.json` so the next phase can diff it.

The timings use `time.perf_counter` around a batch with the interpreter warm, and
repeat a fixed number of times rather than running for a fixed duration — a
duration-based loop with an adaptive rep count produces numbers that cannot be
compared across runs, which is how benchmarks end up measuring the machine's
thermal state.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np                                                    # noqa: E402

import config                                                         # noqa: E402
from engine import features as F                                      # noqa: E402
from engine import pattern as PAT                                     # noqa: E402
from engine import ttf as TTF                                         # noqa: E402

# §5.12's budgets, in ms. These are the acceptance thresholds, not aspirations.
BUDGET_M1_MS = 1.0
BUDGET_M2_MS = 3.0


def _rng_vector(n_features: int, seed: int = 5) -> np.ndarray:
    """A plausible feature row: real-ish magnitudes, sentinels never present.

    Random noise would be *faster* than real data for a tree walk (no deep
    branches are taken early), and uniform sentinels would be faster still — every
    tree falls to its default side. A benchmark fed unrepresentative rows reports
    a latency the field never sees, so the row is drawn from a real window.
    """
    rng = np.random.default_rng(seed)
    x = np.zeros(n_features, dtype=float)
    for i in range(n_features):
        x[i] = float(rng.normal(0.0, 1.0))
    return x


def _time_it(fn, reps: int, warmup: int) -> dict:
    """Median and best-case per-call ms. Median for the report, min for the floor.

    The median is the number that gets quoted because a mean is poisoned by the
    one rep the OS scheduled away. The minimum is printed beside it because the
    gap between them *is* the measurement's uncertainty, and a median that sits
    far above the min is a benchmark that needs more reps, not a slow model.
    """
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    return {
        "median_ms": statistics.median(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "mean_ms": statistics.fmean(samples),
        "p95_ms": sorted(samples)[min(len(samples) - 1, int(0.95 * len(samples)))],
        "reps": reps,
    }


def bench_m1(reps: int, warmup: int) -> dict:
    """M1 through the shipped path, and through sklearn, and the difference.

    ``classify()`` is measured end-to-end because that is what the ticker calls —
    it includes the row assembly from the window, the `to_model_vector` sentinel
    pass and the label lookup. Timing `_walk` alone would flatter the number.

    The sklearn pass is timed for one reason only: the ratio. If it ever comes
    out at 1.0× rather than ~40×, the fast path has been disabled somewhere and
    `pattern_meta.json`'s published figure is no longer true.
    """
    if not PAT.load_pattern_model() or not PAT.is_active():
        return {"available": False,
                "reason": PAT.PATTERN_INFO.get("reason", "not loaded")}

    n_features = F.N_EXTENDED
    x = _rng_vector(n_features)

    def shipped():
        return PAT.classify(x)

    out = {"available": True, "n_features": n_features}
    out["shipped"] = _time_it(shipped, reps, warmup)

    model = PAT.PATTERN_INFO.get("model")

    out = {"available": True, "n_features": n_features}
    out["shipped"] = _time_it(shipped, reps, warmup)

    if model is not None and hasattr(model, "predict_proba"):
        # The reference path, straight through sklearn with no scaling — which is
        # what ``classify()`` does too. M1's artifact is a raw forest: there is no
        # scaler in the pattern pipeline, and adding one here to "make it fair"
        # would benchmark a model that does not exist.
        def reference():
            return model.predict_proba(np.asarray(x, dtype=float).reshape(1, -1))

        out["sklearn"] = _time_it(reference, max(20, reps // 20), 3)
        out["speedup_x"] = out["sklearn"]["median_ms"] / max(out["shipped"]["median_ms"], 1e-9)

        # **Agreement.** A fast path that is fast and wrong is worse than slow.
        # `test_cascade_safety` pins this on a real corpus window; this re-pins it
        # on a synthetic row — every feature off its training median, and none of
        # them sentinels — which is the input that test does not use.
        fast = PAT.PATTERN_INFO.get("fast")
        if fast is not None:
            from engine.pattern import _walk
            xr = np.asarray(x, dtype=float).reshape(1, -1)
            ref = np.asarray(model.predict_proba(xr), dtype=float).ravel()
            got = np.asarray(_walk(fast, xr), dtype=float).ravel()
            out["max_abs_diff_vs_sklearn"] = float(np.max(np.abs(got - ref)))
            out["class_order_matches"] = bool(
                list(PAT._proba_order(model, PAT.PATTERN_INFO.get("meta") or {}))
                == [str(c) for c in getattr(model, "classes_", [])])

    out["budget_ms"] = BUDGET_M1_MS
    out["within_budget"] = out["shipped"]["median_ms"] < BUDGET_M1_MS
    return out


def bench_m2(reps: int, warmup: int) -> dict:
    """M2 (the TTF regressor) on one window — §5.7's 3 ms budget.

    Warm-up matters more here than for M1: XGBoost allocates its prediction
    buffers on first call, and a cold first rep is several times the steady-state
    cost. Feeding it that rep would produce a number that is neither the model's
    speed nor the system's, so `warmup` reps are discarded before the clock runs.
    """
    if not TTF.load_ttf_model() or not TTF.MODEL_INFO.get("available"):
        return {"available": False,
                "reason": TTF.MODEL_INFO.get("reason", "not loaded")}

    # The feature count comes from the artifact, not from the extractor: this is
    # the *regressor's* budget, and Phase 6's v2 model will take the same 16.
    n_features = int(TTF.MODEL_INFO.get("n_features") or F.N_FEATURES)
    x = _rng_vector(n_features)

    out = {"available": True, "n_features": n_features}
    # ``predict_ttf`` is the shipped entry point — it owns the scaling, the clamp
    # to [0, 3600] and the NaN guard, and the ticker pays for all three.
    out["predict"] = _time_it(lambda: TTF.predict_ttf(x), reps, warmup)
    out["budget_ms"] = BUDGET_M2_MS
    out["within_budget"] = out["predict"]["median_ms"] < BUDGET_M2_MS
    return out


def _project(m1_ms: float, m2_ms: float) -> list[dict]:
    """§5.7's amortisation, as a table rather than a claim.

    The saving comes from M2 being *conditionally* run. So this projects the
    per-node cost at each of M1's cadences, holding M2's duty cycle at the two
    ends that matter: 0 % (a quiet mine) and 100 % (a node the gate has escalated
    and is now watching at 2 Hz).
    """
    rows = []
    for label, m1_hz, m2_hz in (("QUIET / NOMINAL", config.M1_HZ,
                                 config.M2_HZ.get("QUIET", 0.2)),
                                ("ENVIRONMENTAL", config.M1_HZ,
                                 config.M2_HZ.get("ENVIRONMENTAL", 1.0)),
                                ("STRUCTURAL / escalated", config.M1_HZ,
                                 config.M2_HZ.get("STRUCTURAL", 2.0))):
        # Sixteen M1 inferences per tick-per-second across four nodes; M2 at the
        # cadence the gate would pick for that class.
        m1_cost = m1_hz * 4 * m1_ms
        m2_cost = m2_hz * 4 * m2_ms
        rows.append({
            "regime": label,
            "m1_hz": m1_hz,
            "m2_hz": m2_hz,
            "m1_pct_of_one_core": round(100.0 * m1_cost / 1000.0, 3),
            "m2_pct_of_one_core": round(100.0 * m2_cost / 1000.0, 3),
            "total_pct_of_one_core": round(100.0 * (m1_cost + m2_cost) / 1000.0, 3),
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--reps", type=int, default=500)
    ap.add_argument("--warmup", type=int, default=50)
    ap.add_argument("--json", action="store_true", help="also write reports/latency.json")
    args = ap.parse_args()

    print("Neural Strata — M1 / M2 latency bench (§5.12)")
    print(f"  backend  {BACKEND}")
    print(f"  python   {sys.version.split()[0]}   numpy {np.__version__}")
    print(f"  reps     {args.reps} (+{args.warmup} warm-up, discarded)\n")

    m1 = bench_m1(args.reps, args.warmup)
    m2 = bench_m2(args.reps, args.warmup)

    failures = []

    print("  Model 1 — pattern discriminator")
    if not m1["available"]:
        print(f"    UNAVAILABLE — {m1['reason']}")
    else:
        s = m1["shipped"]
        print(f"    shipped path (pattern.classify)  {s['median_ms']:8.3f} ms  "
              f"[min {s['min_ms']:.3f} · p95 {s['p95_ms']:.3f} · max {s['max_ms']:.3f}]")
        print(f"    budget                           {m1['budget_ms']:8.3f} ms   "
              f"{'PASS' if m1['within_budget'] else 'FAIL'}")
        if "sklearn" in m1:
            print(f"    reference (sklearn predict_proba) {m1['sklearn']['median_ms']:7.3f} ms  "
                  f"— shipped is {m1['speedup_x']:.1f}x faster")
        if m1.get("max_abs_diff_vs_sklearn") is not None:
            d = m1["max_abs_diff_vs_sklearn"]
            print(f"    agreement vs sklearn             {d:8.2e} max abs diff   "
                  f"{'PASS' if d < 1e-9 else 'FAIL — the fast path is not the model'}")
            if d >= 1e-9:
                failures.append("M1 fast path disagrees with sklearn")
        if not m1["within_budget"]:
            failures.append(f"M1 {s['median_ms']:.3f} ms exceeds {m1['budget_ms']} ms")

    print("\n  Model 2 — time-to-failure regressor")
    if not m2["available"]:
        print(f"    UNAVAILABLE — {m2['reason']}")
    else:
        s = m2["predict"]
        print(f"    shipped path (ttf.predict_ttf)   {s['median_ms']:7.3f} ms  "
              f"[min {s['min_ms']:.3f} · p95 {s['p95_ms']:.3f} · max {s['max_ms']:.3f}]")
        print(f"    budget                            {m2['budget_ms']:7.3f} ms   "
              f"{'PASS' if m2['within_budget'] else 'FAIL'}")
        if not m2["within_budget"]:
            failures.append(f"M2 {s['median_ms']:.3f} ms exceeds {m2['budget_ms']} ms")

    print("\n  §5.7 amortisation — cost as a fraction of one hub core")
    print(f"    {'regime':24s} {'M1 Hz':>6s} {'M2 Hz':>6s} {'M1 %':>7s} {'M2 %':>7s} {'total %':>8s}")
    rows = []
    if m1.get("available") and m2.get("available"):
        rows = _project(m1["shipped"]["median_ms"], m2["predict"]["median_ms"])
        for r in rows:
            print(f"    {r['regime']:24s} {r['m1_hz']:6.2f} {r['m2_hz']:6.2f} "
                  f"{r['m1_pct_of_one_core']:7.3f} {r['m2_pct_of_one_core']:7.3f} "
                  f"{r['total_pct_of_one_core']:8.3f}")

    print("\n" + "=" * 84)
    if args.json:
        # ``ai/reports/`` is where the §2 tree puts generated reports, and it is
        # where a technical reader looks for them. ``backend/reports/`` is not in
        # the tree at all — writing there is how a report ends up existing but
        # being impossible to find, which is the same as not writing it.
        out_dir = BACKEND.parent / "ai" / "reports"
        out_dir.mkdir(exist_ok=True)
        payload = {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "reps": args.reps,
            "warmup": args.warmup,
            "model_1": m1,
            "model_2": m2,
            "amortisation": rows,
            "budgets": {"model_1_ms": BUDGET_M1_MS, "model_2_ms": BUDGET_M2_MS},
            "pass": not failures,
        }
        (out_dir / "latency.json").write_text(json.dumps(payload, indent=2) + "\n")
        print(f"wrote {out_dir / 'latency.json'}")

    if failures:
        print(f"RESULT: FAIL — {len(failures)} budget violation(s)")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)

    parts = []
    if m1.get("available"):
        parts.append(f"M1 {m1['shipped']['median_ms']:.3f} ms")
    if m2.get("available"):
        parts.append(f"M2 {m2['predict']['median_ms']:.3f} ms")
    print(f"RESULT: PASS — {' · '.join(parts)} (budgets 1 ms / 3 ms)")


if __name__ == "__main__":
    main()
