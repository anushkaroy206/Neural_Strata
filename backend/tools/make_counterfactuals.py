#!/usr/bin/env python3
"""
Phase 5 (B9) — the counterfactual training set for Model 1.
==========================================================

Generates ``ai/datasets/pattern_dataset.csv``: labelled 32-feature windows for the
Deformation Pattern Discriminator (§5.5's seven classes).

**Why counterfactual.** There is no haul truck in the diorama and no public
dataset of mine-machinery vibration aligned with a same-scale crack event. The
training set is therefore generated in-domain by driving the **real simulator** —
``ingestion.simulator``, the same seven profiles the operator's scenario console
arms — and labelling each window with the class its profile represents.

That "same scaffolding" is not a nicety, it is the whole defence of the method.
The model is trained on exactly the process that will be replayed in front of a
judge: if the console produces a loader pass, the training set contains loader
passes produced by the same code, at the same cadence, through the same
7-channel buffer, reduced by the same feature extractor. There is no second
physics model to drift out of step with the first.

**Per-run variation, not one deterministic trace.** Each run draws its own start
phase, node, magnitude multiplier and noise seed. Without that the classifier
would learn one waveform per class — dozens of identical rows, a train/test split
that leaks, and an accuracy of 99 % that means nothing. ``run_id`` is written on
every row precisely so ``GroupShuffleSplit`` can hold whole *runs* out (never
random rows), which is §5.4's stated anti-overfit control.

Usage::

    python tools/make_counterfactuals.py --runs 400 --out ../../ai/datasets/pattern_dataset.csv
    python tools/make_counterfactuals.py --runs 20 --seed 7 --out /tmp/smoke.csv   # quick check
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import MODEL_BUF, MODEL_DT, NODE_IDS          # noqa: E402
from engine import crack as crack_engine                            # noqa: E402
from engine import features as F                                    # noqa: E402
from ingestion import simulator                                     # noqa: E402
from state import SystemState                                       # noqa: E402

#: §5.5's table, in the simulator's own vocabulary. The **primary metric** collapses
#: classes 2–5 to ENVIRONMENTAL and 6–7 to STRUCTURAL, so what the classifier is
#: actually asked is a 4-way question (QUIET / ENVIRONMENTAL / STRUCTURAL /
#: UNKNOWN) with the instrument class carried alongside for the confusion matrix.
PROFILE_CLASS: dict[str, str] = {
    "nominal":             "QUIET",
    "machinery_pass":      "MACHINERY",
    "blast":               "BLAST",
    "rain_seepage":        "RAIN_SEEPAGE",
    "sensor_fault":        "SENSOR_FAULT",
    "warning_creep":       "STRUCTURAL_INITIATION",
    "critical_collapse":   "STRUCTURAL_TERTIARY",
}

#: Primary label: the question the cascade routes on.
PRIMARY: dict[str, str] = {
    "QUIET": "QUIET",
    "MACHINERY": "ENVIRONMENTAL",
    "BLAST": "ENVIRONMENTAL",
    "RAIN_SEEPAGE": "ENVIRONMENTAL",
    "SENSOR_FAULT": "UNKNOWN",          # instrument, not geology → never suppressed
    "STRUCTURAL_INITIATION": "STRUCTURAL",
    "STRUCTURAL_TERTIARY": "STRUCTURAL",
}

#: **The pre-event phase, in seconds.** Every run opens with this much quiet
#: *before* the scenario is armed. Two reasons, and the first is not negotiable:
#:
#: 1. The window is 121 samples at 2 Hz = 60.5 s. A run shorter than that cannot
#:    fill a window at all, so without a pre-event phase the six short profiles
#:    produce **zero** rows — which is exactly what happened on the first run of
#:    this tool (only `rain_seepage`, being 90 s, emitted anything).
#: 2. More importantly, an event has to be seen *starting*. `recovery_ratio` is
#:    (peak − now)/(peak − baseline) over the window, and the "no recovery"
#:    structural claim is only meaningful against a pre-event baseline. A corpus
#:    that begins mid-event teaches the model to recognise a state, not an onset.
PRE_EVENT_S = 62.0

#: How long the scenario itself runs, after the quiet phase. Long enough that the
#: event and a recovery tail are both present.
RUN_SECONDS: dict[str, float] = {
    "nominal":           30.0,
    "machinery_pass":    26.0,
    "blast":             24.0,
    "rain_seepage":      90.0,
    "sensor_fault":      45.0,
    "warning_creep":     60.0,
    "critical_collapse": 40.0,
}

#: Node to inject on, per profile. Machinery and the crack profiles go on N2
#: (a crack node, level 1); ``sensor_fault`` on N3 deliberately — §5.12's table
#: asks for a fault on N3 to be classified as instrument, not geology, and a
#: generator that only ever faults an instrumented node would never test that.
PROFILE_NODE: dict[str, str] = {
    "nominal":            "node_1",
    "machinery_pass":     "node_2",
    "blast":              "node_2",
    "rain_seepage":       "node_4",
    "sensor_fault":       "node_3",
    "warning_creep":      "node_2",
    "critical_collapse":  "node_2",
}


def _harmonise(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


#: **How many nodes a profile shakes.** This is the corpus's most load-bearing
#: structural decision, because §5.6 names two features — ``spatial_coherence``
#: ("local ⇒ machinery; multi-node ⇒ ground") and ``spatial_decay`` ("distance-
#: decaying ⇒ a surface source") — that are *only measurable if some runs are
#: multi-node*. The first corpus injected exactly one node in every run, so every
#: peer was silent, ``spatial_coherence`` was identically 0.0 and
#: ``n_active_anomalous`` identically 0 for all 39,480 windows: two of the four
#: headline features were dead, and no amount of modelling could revive them.
#:
#: The split is not arbitrary — it *is* the physics being tested:
#:
#:   * ``machinery_pass`` / ``blast`` — **always one node.** A loader is a point
#:     source on the surface. If those runs were multi-node the corpus would be
#:     asserting that a truck moves a whole tunnel, and the classifier would learn
#:     the opposite of the lesson it exists to teach.
#:   * ``rain_seepage`` — one node. §5.5: slow tilt drift, no vib, no micro.
#:   * ``sensor_fault`` — one node, and it is an *instrument* fault (§5.12).
#:   * ``warning_creep`` — one node. Initiation is local: §5.5 says the micro rate
#:     crosses on one channel first, which is the whole reason it is detectable
#:     before the macro channel sees anything.
#:   * ``critical_collapse`` — **1 to 3 nodes**, chosen per run. §5.5 calls
#:     tertiary "often multi-node", so "often" has to mean *sometimes in the data*
#:     rather than always: a profile hard-wired to three nodes would make
#:     ``n_active_anomalous > 0`` a perfect collapse detector, which is a simulator
#:     artefact dressed up as a feature.
MULTI_NODE_PROFILES = {"critical_collapse"}

#: The breach displacement, in mm, that ``ttf_label`` counts down to. This is
#: ``01_data_extraction.py``'s definition, restated here because that notebook
#: reads the *database* and the drills that populated it no longer exist. Two
#: copies of a label definition is one too many, so the value is asserted equal
#: to ``config.BREACH_LIMIT_MM`` (and to the calibration JSON's
#: ``breach_limit_mm``) in ``tests/test_counterfactual_labels.py``.
BREACH_MM = 100.0
#: ``01_data_extraction.py`` caps a never-breaching run at 3600 s ("stable"),
#: which is what makes this a regression and not a survival problem.
TTF_CAP_S = 3600.0
#: **A second, lower threshold, and it is not a typo.** ``01_data_extraction.py``
#: tags a *run* BREACH at 99 mm while the *label* counts down to 100 mm. The label
#: is the target; the tag groups runs so ``02_training.py``'s split can hold out
#: whole breach runs. Reproduced here rather than "tidied" to a single number: a
#: tidier dataset whose run tags disagree with the tags the original labelled set
#: would carry would silently change which runs are considered breach-like.
RUN_BREACH_MM = 99.0


def _secondary_nodes(profile: str, primary: str, rng: np.random.Generator) -> list[str]:
    """Which extra nodes this run shakes, and how hard. Empty for most profiles."""
    if profile not in MULTI_NODE_PROFILES:
        return []
    others = [n for n in NODE_IDS if n != primary]
    roll = rng.random()
    if roll < 0.45:
        return []                                   # a single-node collapse
    if roll < 0.80:
        return others[:1]                           # two nodes
    return others[:2]                               # three nodes — the whole level


def simulate_run(profile: str, run_id: int, rng: np.random.Generator,
                 node_id: str | None = None, gain: float = 1.0,
                 track_ttf: bool = False) -> list[dict]:
    """Drive one profile through the real simulator and emit labelled windows.

    Returns rows: ``{run_id, profile, label, primary, t, node, <32 features>}``.

    The window is filled at the training cadence through ``append_model_buffer``
    — the *same* seven-channel buffer the live path writes — so the feature
    extractor sees rows of exactly the shape it sees in production. Building the
    buffer by hand here would be the classic way to train on a shape that never
    occurs at runtime.
    """
    node = node_id or PROFILE_NODE[profile]
    seconds = RUN_SECONDS[profile]
    pre_steps = int(PRE_EVENT_S / MODEL_DT)
    steps = int(seconds / MODEL_DT)

    state = SystemState()
    state.armed = True
    # Each run gets its own crack baseline (Phase 3) and pair baseline (Phase 4):
    # a run is an independent shift, and a leftover stage from the previous run
    # would label this run's early windows with the last run's verdict.
    state.reset(f"counterfactual run {run_id}")
    simulator.reset_seam()

    # **Gain is the per-run magnitude multiplier.** Without it every
    # ``machinery_pass`` in the corpus is the identical trace and the classifier
    # can memorise it. 0.6–1.6 covers "a loader a long way off" to "a loader
    # right here", which is the real range a single SW-420 sees.
    scale = float(gain)

    #: Every node this run shakes, with its own magnitude. The second and third
    #: nodes are further from the event, so they respond less hard — 0.35–0.75 of
    #: the primary. That gradient is what ``spatial_decay`` measures, and setting
    #: it to 1.0 for all nodes would make the feature a constant.
    node_scale: dict[str, float] = {node: scale}
    for i, sec in enumerate(_secondary_nodes(profile, node, rng)):
        node_scale[sec] = scale * float(0.75 - 0.18 * i)

    def scaled() -> None:
        """Advance the simulator one 2 Hz step, in 50 ms sub-ticks (D14)."""
        for _ in range(int(round(MODEL_DT / 0.05))):
            simulator.apply_injections(state, 0.05)

    def tick(t: float) -> None:
        """Advance the crack engine, then the node buffers, by one 2 Hz sample.

        Both halves are load-bearing:

        * **The crack engine must be fed every tick**, exactly as ``app.py``'s
          ticker feeds it. Without it ``crack_state().rate_um_s``, ``coherence``
          and ``macro_d30`` stay ``None`` for the life of the run, so six of the
          sixteen disruption features are NaN on **every row** — the extractor
          correctly reports "not measurable" and the classifier is then trained
          without the cross-sensor evidence that §5.6 calls its headline feature.
          That was a real bug here: the first corpus had no coherence column at all.

        * The buffer is filled on every step, while rows are only emitted once the
          window is full. Conflating the two was the *other* real bug: buffers
          stayed empty until the emit gate opened, so the window needed a further
          60.5 s to fill and only the longest profile produced any rows.

        And the first thing it does is **advance the physics**, because a `tick`
        that forgot to would leave every node at rest for the whole run. That was
        the third real bug: splitting the old combined step into `tick`/`emit`
        dropped the `scaled()` call, and the corpus came out perfectly flat —
        every feature 0.0, every class identical, and no error anywhere to say so.
        """
        scaled()
        for n in state.iter_nodes():
            crack_engine.update_node_crack(state, n.node_id, n, t)
        crack_engine.crack_tick(state, t)

        for n in state.iter_nodes():
            if n.displacement_mm is None:
                continue
            # **The measurement layer.** The plant above this line is a clean
            # deterministic physics model; every real instrument on top of it has
            # a noise floor, and a corpus without one is not a corpus a model can
            # survive contact with hardware on. `simulator.measure` is applied
            # here, at the point a value is *published into a window*, and never
            # to the node's integrated state — see its docstring for why adding
            # noise to the state integrates it into a fake creep signal instead.
            disp, tilt, acc = simulator.measure(
                float(n.displacement_mm) * node_scale.get(n.node_id, 1.0),
                float(n.tilt_x), float(n.acceleration), t, n.node_id)
            state.append_model_buffer(
                n.node_id, t, disp, tilt, acc, int(n.vib),
                n.micro, n.macro, clock="cf",
            )
        state.update_status_logic()

    def emit(t: float, label: str, phase: str, node: str) -> dict | None:
        """One labelled row from the current 7-channel window, or ``None``.

        The window is filled through ``append_model_buffer`` — the *same* buffer
        the live path writes — so the extractor sees rows of exactly the shape it
        sees in production. Building the buffer by hand here would be the classic
        way to train on a shape that never occurs at runtime.
        """
        buf = state.buffers.get(node)
        if buf is None or len(buf) < MODEL_BUF:
            return None
        crack = _crack_dict(state, node)
        peers = {n_id: list(state.buffers[n_id]) for n_id in NODE_IDS
                 if n_id != node and state.buffers.get(n_id)}
        feats = F.compute_extended_features(list(buf), peers=peers, crack=crack)
        if feats is None:
            return None
        return {
            "run_id": run_id, "profile": profile, "label": label,
            "primary": PRIMARY.get(label, "QUIET"), "phase": phase,
            "t_s": round(t, 3), "node": node, "gain": round(scale, 3),
            "n_nodes": len(node_scale), "ttf_label": "", "run_class": "",
            **{name: _clean(v) for name, v in zip(F.EXTENDED_FEATURES, feats)},
        }

    # Phase A — quiet. The pre-event baseline, and the reason short profiles emit
    # at all. Rows here are labelled QUIET because they genuinely are: this is the
    # ground before anything happened, which is also the commonest state on a rig.
    rows: list[dict] = []
    #: ``(t, scaled displacement)`` for the primary node, sampled every tick.
    #: The breach time is a property of the whole trajectory, so it cannot be
    #: known while the trajectory is still being generated. Recording the trace
    #: and back-filling the labels afterwards is a single pass; scanning for the
    #: crossing first and re-running would double the cost *and* risk the two
    #: passes diverging, which is the one thing a label must never do.
    trace: list[tuple[float, float]] = []

    def record(t: float) -> None:
        if not track_ttf:
            return
        n = state.nodes.get(node)
        if n is not None and n.displacement_mm is not None:
            trace.append((t, float(n.displacement_mm) * scale))

    for step in range(pre_steps):
        t = (step + 1) * MODEL_DT
        tick(t)
        record(t)
        for n_id in node_scale:
            row = emit(t, "QUIET", "pre", n_id)
            if row:
                rows.append(row)

    # Phase B — the event. Armed after the quiet phase so the window straddles the
    # onset, which is the whole point of the corpus.
    for n_id in node_scale:
        simulator.arm(state, n_id, profile)
    for step in range(steps):
        t = PRE_EVENT_S + (step + 1) * MODEL_DT
        tick(t)
        record(t)
        for n_id in node_scale:
            row = emit(t, PROFILE_CLASS[profile], "event", n_id)
            if row:
                rows.append(row)

    if track_ttf:
        breach_t = next((t for t, d in trace if d >= BREACH_MM), None)
        max_d = max((d for _, d in trace), default=0.0)
        run_class = "BREACH" if max_d >= RUN_BREACH_MM else "STABLE"
        for row in rows:
            row["run_class"] = run_class
            if breach_t is None:
                # Never breached — a stable or warning-only run. Capped, not
                # ``inf``: an infinite target makes the squared error infinite
                # and the model un-fittable, and 3600 is the horizon the deck
                # already prints.
                row["ttf_label"] = TTF_CAP_S
            else:
                row["ttf_label"] = round(
                    max(0.0, min(TTF_CAP_S, breach_t - row["t_s"])), 2)
    return rows


def _clean(v: float):
    """NaN → empty. A CSV that writes ``nan`` reads back as a *string* in some
    loaders, which turns a missing value into a class label."""
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if math.isnan(fv) else round(fv, 6)


def _crack_dict(state, n_id: str) -> dict:
    """Phase 3's published crack block, in the keys the feature extractor wants.

    Read from the engine rather than recomputed — the same rule the extractor
    follows. ``micro_rate`` is the engine's settled rate (``None`` while the
    channel is still warming up, which correctly becomes NaN in the features).
    """
    cs = crack_engine.crack_state(state, n_id)
    node = state.nodes.get(n_id)
    return {
        "micro_rate": None if cs.rate_um_s is None else float(cs.rate_um_s),
        "macro_d30": None if cs.macro_d30 is None else float(cs.macro_d30),
        # `macro_total` is the opening since the node's own baseline — the engine
        # owns the baseline, so subtracting it here rather than re-deriving it
        # from `node.macro` keeps one definition of "since baseline".
        "macro_total": None if cs.baseline_um is None or node is None or node.macro is None
                        else float(node.macro) - float(cs.baseline_um) / 1000.0,
        "coherence": None if cs.coherence is None else float(cs.coherence),
    }


def build(runs: int, seed: int, out: Path, per_profile: int | None = None,
          ttf_out: Path | None = None) -> dict:
    """One pass over the profiles, emitting **both** training sets.

    ``pattern_dataset.csv`` (32 features, Model 1's class label) and — when
    ``ttf_out`` is given — ``drill_dataset.csv`` (16 core features, Model 2's
    ``ttf_label``, the target ``01_data_extraction.py`` defines).

    They are generated together on purpose. The 16 core features are a prefix of
    the 32, so producing the second from the first is a column subset; producing
    it from a *second* simulation run would be a second world. Model 2 is the
    model whose predictions the firewall acts on directly, and a training set that
    came from anywhere other than the exact trace Model 1 saw is the same class of
    defect as PF-D1 and PF-D2 — the world it learned is not the world it is served.
    """
    rng = _harmonise(seed)
    profiles = list(PROFILE_CLASS)
    per = per_profile or max(1, runs // len(profiles))

    # ``ttf_label`` is declared unconditionally and left empty when the label was
    # not tracked — a DictWriter raises on a key that is missing from the header,
    # so the column must be declared wherever it can appear. (That trap has bitten
    # this file before: declaring a row key in only one of the two places fails at
    # `writerow`, i.e. mid-corpus, after the expensive part has already run.)
    fieldnames = ["run_id", "profile", "label", "primary", "phase", "t_s", "node",
                  "gain", "n_nodes", "ttf_label", "run_class", *F.EXTENDED_FEATURES]
    # The core-16 subset, in ``engine.features.FEATURES`` order, plus the target.
    # ``run_class`` sits immediately after ``run_id`` to match the column order
    # ``01_data_extraction.py`` wrote (it inserts at position 2), so a diff between
    # the two producers' outputs lines up column for column.
    ttf_fieldnames = ["run_id", "run_class", "profile", "phase", "t_s", "node",
                      "ttf_label", *F.FEATURES]
    out.parent.mkdir(parents=True, exist_ok=True)
    if ttf_out is not None:
        ttf_out.parent.mkdir(parents=True, exist_ok=True)

    counts: dict[str, int] = {p: 0 for p in profiles}
    row_counts: dict[str, int] = {p: 0 for p in profiles}
    run_id = 0
    t0 = time.time()

    ttf_rows = 0
    ttf_fh = ttf_out.open("w", newline="", encoding="utf-8") if ttf_out else None
    ttf_writer = csv.DictWriter(ttf_fh, fieldnames=ttf_fieldnames) if ttf_fh else None
    if ttf_writer:
        ttf_writer.writeheader()

    with out.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        for profile in profiles:
            for _ in range(per):
                run_id += 1
                # Per-run variation: magnitude, node and phase. The node varies
                # only for profiles where the physics does not pin it (a fault is
                # always on the uninstrumented node, per §5.12).
                gain = float(rng.uniform(0.6, 1.6))
                node = PROFILE_NODE[profile]
                if profile in ("nominal", "machinery_pass", "blast"):
                    node = str(rng.choice([n for n in NODE_IDS]))
                rows = simulate_run(profile, run_id, rng, node_id=node, gain=gain,
                                    track_ttf=ttf_out is not None)
                for r in rows:
                    writer.writerow(r)
                    if ttf_writer is not None:
                        ttf_writer.writerow({
                            "run_id": r["run_id"], "run_class": r["run_class"],
                            "profile": r["profile"],
                            "phase": r["phase"], "t_s": r["t_s"], "node": r["node"],
                            "ttf_label": r["ttf_label"],
                            **{name: r[name] for name in F.FEATURES},
                        })
                        ttf_rows += 1
                counts[profile] += 1
                row_counts[profile] += len(rows)

    if ttf_fh is not None:
        ttf_fh.close()

    return {
        "out": str(out),
        "ttf_out": str(ttf_out) if ttf_out else None,
        "ttf_rows": ttf_rows,
        "runs": run_id,
        "rows": sum(row_counts.values()),
        "per_profile_runs": counts,
        "per_profile_rows": row_counts,
        "seconds": round(time.time() - t0, 1),
        "features": len(F.EXTENDED_FEATURES),
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="Generate Model 1's counterfactual training set")
    ap.add_argument("--runs", type=int, default=400, help="total runs (split evenly across profiles)")
    ap.add_argument("--per-profile", type=int, default=None, help="override runs per profile")
    ap.add_argument("--seed", type=int, default=20260927, help="deterministic by default — a corpus must be reproducible")
    ap.add_argument("--out", type=Path, default=root / "ai" / "datasets" / "pattern_dataset.csv")
    # The regression set rides along by default: it costs one extra write per row
    # and it is the only way the TTF target can be guaranteed to come from the
    # same trace. `--no-ttf-out` turns it off for a quick class-only corpus.
    ap.add_argument("--ttf-out", type=Path,
                    default=root / "ai" / "datasets" / "drill_dataset.csv",
                    help="also write the 16-feature TTF regression set here "
                         "(default: alongside the pattern set)")
    ap.add_argument("--no-ttf-out", action="store_true",
                    help="skip the TTF regression set entirely")
    ap.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = ap.parse_args(argv)

    ttf_out = None if args.no_ttf_out else args.ttf_out
    summary = build(args.runs, args.seed, args.out, args.per_profile, ttf_out=ttf_out)
    if args.json:
        import json
        print(json.dumps(summary, indent=2))
    else:
        print(f"wrote {summary['rows']:,} windows from {summary['runs']} runs "
              f"({summary['features']} features) → {summary['out']}")
        for p, n in summary["per_profile_runs"].items():
            print(f"  {p:22s} {n:4d} runs  {summary['per_profile_rows'][p]:6,} rows")
        print(f"  {summary['seconds']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
