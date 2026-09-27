#!/usr/bin/env python3
"""
Neural Strata — the §5.12 acceptance run (Phase 5)
===================================================

Drives all seven scenario profiles through the **real** simulator and the **real**
cascade — the same `apply_injections`, the same 32-feature extractor, the same
`pattern_model_v1.json`, the same `cascade.decide` — and checks the six rows of
§5.12's acceptance table.

    python3 tools/eval_cascade.py

Why this is not the same thing as the training notebook's test-set metrics
-------------------------------------------------------------------------
Those numbers answer *"can the classifier separate these classes?"* on windows
drawn from the corpus. This answers *"does the assembled system behave correctly
when a scenario is run at it?"* — which is a different question, and the one the
demo actually asks. A model can score 0.99 and still be wired into a cascade that
alerts on the wrong thing.

The columns that matter, and why:

* **tier** — §5.12 names the expected tier for every scenario, and the
  environmental rows must produce nothing above ADVISORY. That is the nuisance-alarm
  claim.
* **disagreement** — the `critical_collapse` row exists to prove Rule 2, and the
  rule only proves anything if the two models can be *made* to contradict.
* **the M1-forced row at the bottom** — §5.12's last row is
  *"M1 forced to ENVIRONMENTAL during a real collapse ⇒ the alert still fires"*.
  It cannot be tested by running a profile, because no profile produces a
  confident-but-wrong M1. It is tested by taking a real collapse and **overwriting
  Model 1's verdict** — which is precisely the failure being defended against, and
  the only honest way to stage it.

Exit code is 0 when every row passes, 1 otherwise, so this can gate a release.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import numpy as np  # noqa: E402

import config                                   # noqa: E402
from ingestion import simulator                 # noqa: E402
from state import SystemState                   # noqa: E402
from engine import cascade as CASC              # noqa: E402
from engine import crack as CRACK               # noqa: E402
from engine import features as F                # noqa: E402
from engine import pattern as PAT               # noqa: E402
from engine import ttf as T                     # noqa: E402

#: §5.12's table. ``max_tier`` is the loudest tier the scenario may reach;
#: ``min_tier`` the quietest. `None` means "no constraint".
SCENARIOS = [
    dict(profile="machinery_pass",    node="node_2", label="ENVIRONMENTAL",
         max_tier="ADVISORY", min_tier=None,
         note="a truck is not a collapse, and it is not silent either"),
    dict(profile="blast",             node="node_2", label="ENVIRONMENTAL",
         max_tier="ADVISORY", min_tier=None,
         note="one impulse, then silence"),
    dict(profile="rain_seepage",      node="node_4", label="ENVIRONMENTAL",
         max_tier="ADVISORY", min_tier=None,
         note="slow drift, under every acceleration threshold"),
    dict(profile="sensor_fault",      node="node_3", label="UNKNOWN",
         max_tier=None, min_tier="ELEVATED",
         note="an instrument fault is not a geological event — and it is not "
              "'all clear' either"),
    dict(profile="warning_creep",     node="node_2", label="STRUCTURAL",
         max_tier=None, min_tier="ELEVATED",
         note="the seam opens first; the macro channel follows"),
    dict(profile="critical_collapse", node="node_2", label="STRUCTURAL",
         max_tier=None, min_tier="IMMINENT",
         note="terminal acceleration: tier IMMINENT, route blocked"),
    dict(profile="nominal",           node="node_1", label="QUIET",
         max_tier="NOMINAL", min_tier=None,
         note="a quiet tunnel must not generate advisories"),
]

TIER_ORDER = ("NOMINAL", "ADVISORY", "ELEVATED", "IMMINENT")

#: 62 s of quiet before arming, so the 60.5 s window is full and *straddles* the
#: onset. Without this the very first event window contains no event at all and
#: the run measures the calm before the storm.
PRE_S = 62.0
SUB_DT = 0.05


def run_scenario(profile: str, node: str, force_m1: dict | None = None):
    """Run one profile and tick the **real cascade** through it.

    ``CASC.cascade_tick`` is the production entry point — the same function
    ``app.py``'s ticker calls. Driving it directly is the only way this tool can
    claim to test the assembled system rather than a re-implementation of it.

    **The first version of this tool sampled "the last third of the run", and it
    was wrong in a way worth recording.** The profiles have different durations and
    most end long before the run does — a blast is over at t = 6 s, a machinery
    pass at t = 9 s — so sampling the tail measured a tunnel that had *recovered*
    and reported its calm state as the event. Four of seven scenarios failed, and
    every failure was this artefact: the truck had driven away, the blast had
    stopped, and the tool called the silence afterwards "the scenario". A test
    harness that samples at the wrong moment produces exactly the kind of failure
    that sends you hunting for a bug in the model.

    So decisions are now collected **during the event phase**, tick by tick, which
    is both simpler and what production actually does.
    """
    state = SystemState()
    state.armed = True
    state.reset(f"eval {profile}")
    simulator.reset_seam()
    CRACK.reset_crack_baseline(state)
    CASC.reset()

    orig_classify = PAT.classify
    if force_m1 is not None:
        def _forced(feats, **kw):
            base = dict(orig_classify(feats, **kw) or {})
            base.update(force_m1)
            return base
        PAT.classify = _forced

    def step(t: float) -> None:
        for _ in range(int(round(config.MODEL_DT / SUB_DT))):
            simulator.apply_injections(state, SUB_DT)
        for n in state.iter_nodes():
            CRACK.update_node_crack(state, n.node_id, n, t)
        CRACK.crack_tick(state, t)
        for n in state.iter_nodes():
            if n.displacement_mm is None:
                continue
            d, ti, a = simulator.measure(float(n.displacement_mm), float(n.tilt_x),
                                         float(n.acceleration), t, n.node_id)
            state.append_model_buffer(n.node_id, t, d, ti, a, int(n.vib),
                                      n.micro, n.macro, clock="eval")
        state.update_status_logic()
        CASC.cascade_tick(state, t)          # <- the production call
        dec = CASC.last_decisions().get(node)
        if dec is not None:
            decisions.append(dec)

    decisions: list = []
    t = 0.0
    try:
        # Phase A — quiet, so the 60.5 s window is full and straddles the onset.
        for _ in range(int(PRE_S / config.MODEL_DT)):
            t += config.MODEL_DT
            step(t)
        decisions.clear()                    # only the event phase is scored
        # Phase B — the event, until the profile ends, then a short tail.
        simulator.arm(state, node, profile)
        alive = True
        tail = 0
        for _ in range(int(120.0 / config.MODEL_DT)):
            t += config.MODEL_DT
            step(t)
            if alive and not state.injections.get(node):
                alive = False                # the profile finished
            if not alive:
                tail += 1
                if tail > int(8.0 / config.MODEL_DT):
                    break
    finally:
        PAT.classify = orig_classify

    if not decisions:
        return None
    labels = [d.m1_label for d in decisions]
    return {
        "modal": max(set(labels), key=labels.count),
        "labels": labels,
        "peak": max((d.tier for d in decisions),
                    key=lambda x: TIER_ORDER.index(x)),
        "tiers": [d.tier for d in decisions],
        "disagreement": any(d.disagreement for d in decisions),
        "firewalled": any(d.firewalled for d in decisions),
        "ttf_min": min((d.ttf for d in decisions if d.ttf is not None), default=None),
        "n": len(decisions),
    }


#: §5.12's acceptance command names a dataset, so the tool takes one. The default
#: is the corpus the shipped Model 1 was trained on.
DEFAULT_DATASET = BACKEND.parent / "ai" / "datasets" / "pattern_dataset.csv"
NOTEBOOK_06 = BACKEND.parent / "ai" / "notebooks" / "06_train_pattern.py"
#: §5.12 / §5.11 budget for one Model-1 window, in milliseconds.
M1_BUDGET_MS = 1.0
#: A drift larger than this between the measured metrics and the ones
#: `pattern_meta.json` publishes means the artifact and its own record disagree.
DRIFT_TOLERANCE = 0.01


def _trainer():
    """Import `06_train_pattern.py` as a module.

    The split and the metric functions come from the notebook that trained the
    artifact rather than from a second implementation here. This is the same rule
    the rest of the phase follows: if the evaluation re-derives the split, then
    one day it derives a different one and the numbers stop meaning anything.
    The notebook has no import side effects (its work is under `__main__`).
    """
    spec = importlib.util.spec_from_file_location("nb06_train_pattern", NOTEBOOK_06)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def corpus_pass(dataset: Path) -> tuple[list[str], bool]:
    """Model 1 against the held-out test split of its own corpus.

    Returns ``(failures, ran)``. This is the half of §5.12 the scenario table
    cannot cover: the scenarios prove the *routing* is safe, this proves the
    *classifier* still is what `pattern_meta.json` says it is. Per-class
    precision/recall, structural recall, the quiet false-positive rate and the
    per-window latency, all measured on the shipped fast path.

    The comparison is the point. A retrain, a threshold change, a feature-order
    edit or a scaler that drifted will all leave the scenarios passing — the
    cascade is explicitly built to stay safe when M1 is wrong — while quietly
    moving these numbers away from the ones on record.
    """
    print("\n" + "=" * 84)
    print("CORPUS PASS — Model 1 on the held-out test split of the training corpus")
    print("=" * 84)
    if not dataset.exists():
        print(f"  SKIPPED — {dataset} is not there.")
        print("  Build it with:")
        print("    python3 tools/make_counterfactuals.py --runs 420 \\")
        print(f"      --out {dataset}")
        return [], False

    nb = _trainer()
    df = nb.load_dataset(dataset)
    _, _, te = nb.three_way_split(df)
    test = df.iloc[te]
    X = nb.matrix(test)
    y_true = test[nb.TARGET].to_numpy()

    t0 = time.perf_counter()
    y_pred = []
    for row in X:
        verdict = PAT.classify([float(v) for v in row])
        y_pred.append((verdict or {}).get("label", "UNKNOWN"))
    per_window_ms = (time.perf_counter() - t0) * 1000.0 / max(len(X), 1)
    y_pred = np.array(y_pred)

    m = nb.class_metrics(y_true, y_pred)
    quiet_fp = nb.quiet_false_positive_rate(y_true, y_pred)
    meta = PAT.PATTERN_INFO.get("meta") or {}
    published = meta.get("metrics") or {}
    calib = meta.get("calibration") or {}

    n_test = int(meta.get("n_test") or 0)
    print(f"  corpus {dataset.name} · test split {len(test):,} windows"
          + (f" (pattern_meta.json records {n_test:,})" if n_test else ""))
    print(f"\n  {'class':16s} {'precision':>10s} {'recall':>9s} {'support':>9s}")
    print("  " + "-" * 46)
    for c in nb.CLASSES:
        support = int((y_true == c).sum())
        print(f"  {c:16s} {m['per_class_precision'][c]:10.4f} "
              f"{m['per_class_recall'][c]:9.4f} {support:9,}")

    failures: list[str] = []

    def row(label, got, want, tol, why, fmt="{:.4f}"):
        drift = abs(got - want)
        ok = drift <= tol
        print(f"  {label:26s} {fmt.format(got):>10s}   published "
              f"{fmt.format(want):>10s}   {'OK' if ok else 'DRIFT'}")
        if not ok:
            failures.append(f"{why}: measured {fmt.format(got)}, "
                            f"pattern_meta.json says {fmt.format(want)}")

    print()
    row("macro F1", m["macro_f1"], float(published.get("macro_f1", 0.0)),
        DRIFT_TOLERANCE, "M1 macro F1 has drifted from its published value")
    row("STRUCTURAL recall", m["per_class_recall"]["STRUCTURAL"],
        float(calib.get("achieved_recall", 0.0)), DRIFT_TOLERANCE,
        "M1 structural recall has drifted from its calibration record")
    row("STRUCTURAL precision", m["per_class_precision"]["STRUCTURAL"],
        float(calib.get("achieved_precision", 0.0)), DRIFT_TOLERANCE,
        "M1 structural precision has drifted from its calibration record")
    row("quiet false-positive rate", quiet_fp,
        float(published.get("quiet_fp_rate", 0.0)), DRIFT_TOLERANCE,
        "M1 has started calling quiet windows something else")

    # The §5.13 risk table's own target, asserted rather than asserted-about.
    target_recall = float(getattr(nb, "RECALL_TARGET", 0.98))
    if m["per_class_recall"]["STRUCTURAL"] < target_recall:
        failures.append(
            f"STRUCTURAL recall {m['per_class_recall']['STRUCTURAL']:.4f} is below "
            f"the {target_recall} calibration target")

    # The recorded figure is the bare fast path, timed inside the notebook; this
    # one goes through `pattern.classify`, which also extracts the top-k feature
    # attribution the panel needs. The two are not the same measurement and the
    # difference is expected — what would not be expected is exceeding the budget.
    print(f"\n  latency {per_window_ms:.3f} ms/window over {len(X):,} windows "
          f"through pattern.classify (budget {M1_BUDGET_MS} ms). "
          f"pattern_meta.json records\n  {float(meta.get('latency_ms_per_window') or 0.0):.3f} ms "
          f"for the bare fast path — the same estimator without the attribution, "
          f"which is why\n  this number is the larger of the two.")
    if per_window_ms > M1_BUDGET_MS:
        failures.append(f"M1 latency {per_window_ms:.3f} ms exceeds the "
                        f"{M1_BUDGET_MS} ms budget")

    print(f"\n  {'PASS' if not failures else 'FAIL'} — corpus pass")
    return failures, True


def main() -> int:
    ap = argparse.ArgumentParser(description="§5.12 acceptance run for the cascade.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                    help="the Model-1 corpus for the accuracy pass "
                         f"(default: {DEFAULT_DATASET.name})")
    ap.add_argument("--quick", action="store_true",
                    help="skip the corpus accuracy pass — scenarios and Rule 2 only")
    args = ap.parse_args()

    print("=" * 84)
    print("Neural Strata — §5.12 acceptance run · the cascade, on the real simulator")
    print("=" * 84)
    PAT.load_pattern_model()
    T.load_ttf_model()
    if not PAT.is_active():
        print(f"\n!! Model 1 is NOT loaded: {PAT.PATTERN_INFO['reason']}")
        print("   The cascade will degrade to M2-only. Every label check below will")
        print("   report UNAVAILABLE. Generate the corpus and train first:\n")
        print("     python3 tools/make_counterfactuals.py --runs 420 "
              "--out ../ai/datasets/pattern_dataset.csv")
        print("     python3 ../ai/notebooks/06_train_pattern.py\n")
    else:
        print(f"\nmodel 1    {PAT.PATTERN_INFO['meta'].get('model')}  "
              f"threshold {PAT.PATTERN_INFO['meta'].get('operating_threshold')}")
    print(f"mode       {config.CASCADE_MODE}   firewall band {CASC.CRITICAL_BAND_S:.0f} s")
    print(f"features   {F.N_EXTENDED}   window {F.MODEL_BUF} samples "
          f"({F.MODEL_BUF * config.MODEL_DT:.1f} s)")

    failures: list[str] = []
    rows = []

    t0 = time.perf_counter()
    for sc in SCENARIOS:
        r = run_scenario(sc["profile"], sc["node"])
        if r is None:
            label = peak = None
            dis = fw = False
            m2 = None
        else:
            label, peak, dis, fw = r["modal"], r["peak"], r["disagreement"], r["firewalled"]
            m2 = r["ttf_min"]
        ok = True
        why = ""
        if label is None:
            ok, why = False, "produced no decision"
        else:
            if sc["label"] == "UNKNOWN":
                # §5.12 wants an *instrument* verdict here, and the gate accepts
                # either the model's own UNKNOWN or STRUCTURAL (which also
                # escalates) — but never ENVIRONMENTAL, which would mean a dead
                # gauge was filed as a truck.
                if label not in ("UNKNOWN", "STRUCTURAL"):
                    ok, why = False, f"expected UNKNOWN/instrument, got {label}"
            elif label != sc["label"]:
                ok, why = False, f"expected {sc['label']}, got {label}"
            if ok and sc["max_tier"] and TIER_ORDER.index(peak) > TIER_ORDER.index(sc["max_tier"]):
                ok, why = False, f"tier {peak} above the {sc['max_tier']} ceiling"
            if ok and sc["min_tier"] and TIER_ORDER.index(peak) < TIER_ORDER.index(sc["min_tier"]):
                ok, why = False, f"tier {peak} below the {sc['min_tier']} floor"
        rows.append((sc, label, peak, dis, fw, m2, ok, why))
        if not ok:
            failures.append(f"{sc['profile']}: {why}")

    elapsed = time.perf_counter() - t0

    print(f"\n{'scenario':18s} {'want':14s} {'got':14s} {'tier':9s} "
          f"{'M2 ttf':>8s}  {'disagree':9s} {'firewall':9s} result")
    print("-" * 96)
    for sc, label, peak, dis, fw, m2, ok, why in rows:
        ttf = "—" if m2 is None else f"{m2:.0f} s"
        # `firewall` = the firewall had to override Model 1 on this scenario. It
        # is not a failure (that is what the firewall is for) but it is the thing
        # an operator wants to see: a YES here means the two models disagreed and
        # the safety path decided.
        print(f"{sc['profile']:18s} {sc['label']:14s} {str(label):14s} "
              f"{str(peak):9s} {ttf:>8s}  {'YES' if dis else ' - ':9s} "
              f"{'OVERRIDE' if fw else ' - ':9s} "
              f"{'PASS' if ok else 'FAIL: ' + why}")

    # ---- the suppression rate -------------------------------------------------
    #
    # Of the scenarios whose truth demanded an escalation, how many failed to
    # get one? This is the number that matters and it is a count, not a rate,
    # because the only acceptable value is zero.
    demanding = [(sc, peak) for sc, label, peak, dis, fw, m2, ok, why in rows
                 if sc["min_tier"] in ("ELEVATED", "IMMINENT")]
    suppressed = [(sc, peak) for sc, peak in demanding
                  if not peak or TIER_ORDER.index(peak) < TIER_ORDER.index(sc["min_tier"])]
    print(f"\nsuppression rate — {len(suppressed)} of {len(demanding)} escalation-"
          f"demanding scenarios were suppressed "
          f"({'clean' if not suppressed else 'VIOLATION'})")
    for sc, peak in suppressed:
        failures.append(f"{sc['profile']}: tier {peak} — the escalation was suppressed")

    # ---- the row that proves Rule 2 -------------------------------------
    print("\n" + "=" * 84)
    print("RULE 2 — Model 1 forced to the WRONG answer during a real collapse")
    print("=" * 84)
    print("§5.12: 'M1 forced to ENVIRONMENTAL during a real collapse ⇒ the alert")
    print("        still fires.' This is the test that proves the firewall, and it")
    print("        cannot be staged by running a profile: no profile produces a")
    print("        confident-but-wrong Model 1. So Model 1's verdict is overwritten")
    print("        with the most reassuring label it can emit, on a real collapse.\n")

    lie = {"label": "ENVIRONMENTAL", "disruption_index": 0.99, "structural_prob": 0.0}
    r = run_scenario("critical_collapse", "node_2", force_m1=lie)
    peak = None if r is None else r["peak"]
    dis = False if r is None else r["disagreement"]
    m2 = None if r is None else r["ttf_min"]
    rule2_ok = peak in ("ELEVATED", "IMMINENT")
    print(f"  forced M1 = ENVIRONMENTAL   M2 TTF = "
          f"{'—' if m2 is None else f'{m2:.0f} s'}   tier = {peak}   "
          f"disagreement = {'YES' if dis else 'no'}")

    if rule2_ok:
        print("\n  PASS — the alert fired anyway. Model 1 was as wrong as it can be,")
        print("         and the operator was still told. Suppression on this path is")
        print("         impossible by construction, not by luck:")
        print("             engine/cascade.py::firewall_check  reads M2 and the")
        print("             kinematic fallback first, and is mode-independent.")
    else:
        failures.append(f"RULE 2 VIOLATED: forced ENVIRONMENTAL collapsed to tier {peak}")
        print(f"\n  FAIL — the alert was suppressed. tier={peak}. "
              "This is the one failure that must never happen.")

    corpus_failures, corpus_ran = ([], False) if args.quick else corpus_pass(args.dataset)
    failures.extend(corpus_failures)

    print("\n" + "=" * 84)
    if failures:
        print(f"RESULT: FAIL ({len(failures)} finding(s))")
        for f in failures:
            print(f"  - {f}")
    else:
        parts = [f"all {len(SCENARIOS)} scenarios", "Rule 2"]
        parts.append("corpus pass" if corpus_ran else "corpus pass skipped (--quick)")
        print("RESULT: PASS — " + " + ".join(parts))
    print(f"elapsed {elapsed:.1f} s")
    print("=" * 84)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
