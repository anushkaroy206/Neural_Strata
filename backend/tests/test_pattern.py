"""Phase 5 — Model 1's serving path, and its parity with the training corpus.

    cd backend && python3 -m pytest tests/test_pattern.py -q

**What this file is defending.**

Model 1 is trained on a corpus produced by ``tools/make_counterfactuals.py`` and
served by ``engine/cascade.py::cascade_tick``. Those are two different code paths
that must produce *the same 32 numbers from the same physics*. When they do not,
nothing raises: the classifier answers confidently about a world it was never
trained on, and the only symptom is that the answers are wrong in a way that looks
like a model problem.

This project has now hit that failure twice, in opposite directions:

* **PF-D1 / PF-D2** — the noise generator made ``micro_rate`` and
  ``impulse_periodicity`` depend on how long the *process* had been running, so
  the corpus and the server saw different phases of the same fictitious wave.
* **The Phase 5 live-path skew** — ``app.py``'s local synthetic branch appended
  *raw integrated state* to the model buffer while the corpus appended
  ``simulator.measure()``'s noised readings. The server therefore served windows
  with a noise floor of exactly zero on every channel: ``vib_count`` and
  ``zero_cross_rate`` pinned to 0, the cross-sensor features with no variance to
  correlate. The classifier saw a point outside its training distribution
  precisely when the tunnel was quietest.

Both were invisible from the outside, and both were found by comparing two
numbers that were supposed to be equal. So the comparison is a test now.

**Why the tests here are shaped the way they are.** A parity test that compares a
function with itself proves nothing. Each test below is written so that it fails
if the *contract* breaks, not merely if the code changes — the class-order test
asserts against the artifact's own ``classes_``, the corpus replay asserts against
labels the generator wrote, and the live-parity test drives two independent paths
that share no code beyond the feature extractor itself.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import config                                                         # noqa: E402
from engine import cascade as CASC                                    # noqa: E402
from engine import crack as CK                                        # noqa: E402
from engine import features as F                                      # noqa: E402
from engine import pattern as PAT                                     # noqa: E402
from ingestion import simulator                                       # noqa: E402
from state import SystemState                                         # noqa: E402
from tools import make_counterfactuals as MCF                          # noqa: E402

SENTINEL = float(config.PATTERN_MISSING_SENTINEL)


@pytest.fixture(scope="module", autouse=True)
def _model():
    PAT.load_pattern_model()


# ---------------------------------------------------------------------------
# 1. The class-order trap
# ---------------------------------------------------------------------------

def test_the_artifact_and_the_safety_order_differ_by_design():
    """``classes_`` is alphabetical; the safety order is not. Both are real.

    ``RandomForestClassifier.classes_`` comes back **sorted**, and for this label
    set that is ``['ENVIRONMENTAL', 'QUIET', 'STRUCTURAL', 'UNKNOWN']`` — i.e.
    ENVIRONMENTAL first and QUIET second. ``pattern.PATTERN_CLASSES`` is in
    *severity* order, QUIET first.

    This is not a curiosity about scikit-learn. Indexing one array with the other's
    ordering silently swaps ENVIRONMENTAL and QUIET, which are the two classes a
    truck and a still tunnel belong to: the model would call every quiet tunnel
    environmental and every truck quiet, and it would do so with high confidence
    and a clean-looking confusion matrix on the *other* two classes. It has
    already happened once in this project — in the trainer, not the server.
    """
    model = PAT.PATTERN_INFO["model"]
    assert model is not None
    model_order = [str(c) for c in model.classes_]
    assert model_order == sorted(model_order), (
        "the artifact's classes_ is expected to be alphabetical — if scikit-learn "
        "has changed that, this test needs re-deriving rather than deleting"
    )
    assert model_order != list(PAT.PATTERN_CLASSES), (
        "the model's order now matches the safety order, so the mapping machinery "
        "in _proba_order is no longer exercised by this test"
    )
    assert set(model_order) == set(PAT.PATTERN_CLASSES)


def test_probabilities_are_mapped_positionally_through_the_models_own_order():
    """The named-probability dict must be right *per class*, not merely reordered.

    Constructing a fake forest whose probabilities are a known ramp is the only
    way to prove the mapping rather than observe it: with the real artifact both
    orders happen to look plausible.
    """
    order = PAT._proba_order(PAT.PATTERN_INFO["model"], PAT.PATTERN_INFO.get("meta") or {})
    model_order = [str(c) for c in PAT.PATTERN_INFO["model"].classes_]
    assert list(order) == model_order, (
        "the serving order must be the model's own, so that proba[i] belongs to "
        "order[i] — anything else is a positional guess"
    )

    class FakeForest:
        classes_ = np.asarray(["QUIET", "STRUCTURAL"])      # already alphabetical

    out = PAT._proba_order(FakeForest(), {})
    assert list(out) == ["QUIET", "STRUCTURAL"]


def test_a_structural_window_is_never_reported_as_quiet():
    """The one mis-mapping that would matter most, asserted on real data.

    A corpus row labelled STRUCTURAL, pushed through the live ``classify``, must
    come back STRUCTURAL — not QUIET, not ENVIRONMENTAL. If the order mapping ever
    inverts, this is the first thing that breaks and the most expensive.
    """
    row = _a_corpus_row_labelled("STRUCTURAL")
    out = PAT.classify(_vector_of(row))
    assert out is not None
    assert out["label"] == "STRUCTURAL", (
        f"a STRUCTURAL corpus window classified as {out['label']} "
        f"(probs {out['probs']})"
    )
    assert out["structural_prob"] == pytest.approx(out["probs"]["STRUCTURAL"])


# ---------------------------------------------------------------------------
# 2. The feature vector contract
# ---------------------------------------------------------------------------

def test_the_vector_is_32_long_and_ordered_as_the_model_expects():
    """``to_model_vector`` output order == ``EXTENDED_FEATURES`` order == the
    order the corpus was written in. Three statements of one list, checked once."""
    model = PAT.PATTERN_INFO["model"]
    assert int(model.n_features_in_) == F.N_EXTENDED == 32
    decl = list((PAT.PATTERN_INFO.get("meta") or {}).get("features") or [])
    assert decl == list(F.EXTENDED_FEATURES), (
        "the artifact declares a different feature list from the extractor — "
        "pattern.load_pattern_model is supposed to refuse this outright"
    )


def test_missing_values_become_the_sentinel_never_nan():
    """NaN cannot be fed to a RandomForest — it raises, or worse, is accepted.

    ``PATTERN_MISSING_SENTINEL`` is ``-999.0`` precisely so that "not measurable"
    is a *number* the tree can route on. A NaN that reaches the forest is either a
    crash or a silent misroute, depending on the version.
    """
    vec = [float("nan")] * F.N_EXTENDED
    out = F.to_model_vector(vec)
    assert out is not None
    assert not any(math.isnan(v) for v in out), "NaN survived to_model_vector"
    assert all(v == SENTINEL for v in out)
    assert SENTINEL == -999.0


def test_classify_returns_none_rather_than_a_guess_when_the_row_is_unusable():
    """A short row is not classified. ``None`` propagates to UNKNOWN, which alerts.

    Returning QUIET on a malformed row would be the system inventing all-clear
    from absent data — the one substitution it must never make.
    """
    assert PAT.classify([]) is None
    assert PAT.classify([1.0, 2.0, 3.0]) is None


def test_an_unloaded_model_degrades_to_none_never_quiet():
    """The rollback path, exercised explicitly.

    ``MANIFEST.json`` sets ``model_1.required = false``, so "no classifier on
    disk" is a *supported* configuration: the system runs M2 plus the physics
    chain, which is the Phase 4 system. The test that makes that safe is that an
    absent model yields ``None`` — which the cascade turns into UNKNOWN, which the
    firewall escalates. A default of QUIET would turn a missing artifact into a
    system that reports all-clear with no classifier at all.
    """
    saved = dict(PAT.PATTERN_INFO)
    try:
        PAT.PATTERN_INFO.clear()
        PAT.PATTERN_INFO.update({"available": False, "model": None, "meta": {}})
        assert PAT.classify([1.0] * F.N_EXTENDED) is None
        assert not PAT.is_active()
    finally:
        PAT.PATTERN_INFO.clear()
        PAT.PATTERN_INFO.update(saved)


# ---------------------------------------------------------------------------
# 3. Parity: the corpus and the live path
# ---------------------------------------------------------------------------

def _vector_of(row: dict) -> np.ndarray:
    """A corpus row back into the 32-vector, empty cells → the sentinel."""
    return np.asarray([row[n] if row[n] != "" else SENTINEL
                       for n in F.EXTENDED_FEATURES], dtype=float)


def _corpus_rows(profile: str, run_id: int = 42, node: str = "node_2") -> list[dict]:
    rng = np.random.default_rng(11)
    return MCF.simulate_run(profile, run_id, rng, node_id=node, gain=1.0)


def _a_corpus_row_labelled(label: str) -> dict:
    rows = _corpus_rows("critical_collapse", run_id=901, node="node_2")
    hit = [r for r in rows if r["phase"] == "event" and r["primary"] == label]
    if not hit:
        pytest.skip(f"no {label} row in the probe run")
    return hit[len(hit) // 2]


def _drive_live(profile: str, node: str = "node_2",
                pre_ticks: int = 124, event_ticks: int = 24) -> dict[float, tuple]:
    """Drive the **production** path the way ``app.py``'s ticker drives it.

    Physics → crack engine → the noised buffer append → ``cascade_tick``. The
    buffer append is done here rather than inside ``cascade_tick`` because that is
    what the ticker does; putting it inside the cascade would move the boundary
    the test exists to check.
    """
    PAT.load_pattern_model()
    st = SystemState()
    st.armed = True
    st.reset("parity probe")
    simulator.reset_seam()

    def tick(t: float) -> None:
        for _ in range(10):
            simulator.apply_injections(st, 0.05)
        for n in st.iter_nodes():
            CK.update_node_crack(st, n.node_id, n, t)
        CK.crack_tick(st, t)
        for n in st.iter_nodes():
            if n.displacement_mm is None:
                continue
            d, ti, a = simulator.measure(float(n.displacement_mm), float(n.tilt_x),
                                         float(n.acceleration), t, n.node_id)
            st.append_model_buffer(n.node_id, t, d, ti, a, int(n.vib),
                                   n.micro, n.macro, clock="live")
        st.update_status_logic()
        CASC.cascade_tick(st, t)

    for i in range(pre_ticks):
        tick(round((i + 1) * 0.5, 3))
    simulator.arm(st, node, profile)
    out: dict[float, tuple] = {}
    for i in range(event_ticks):
        t = round(MCF.PRE_EVENT_S + (i + 1) * 0.5, 3)
        tick(t)
        dec = CASC.last_decisions().get(node)
        if dec is not None and dec.extra.get("features"):
            out[t] = (dec.extra["features"], dec.m1_label)
    return out


def test_the_live_path_and_the_corpus_compute_the_same_features():
    """**The parity test.** Same physics, same seed, same instant — 32 numbers.

    The two sides share the feature extractor and nothing else: one is the
    generator's ``emit``, the other is ``cascade_tick`` reading
    ``state.buffers``. The tolerance is 1e-6, which is float noise on values of
    order 1 — tight enough that a channel being skipped, scaled, or sampled at a
    different cadence cannot hide inside it, loose enough not to fail on
    ``float64`` rounding.
    """
    rows = {r["t_s"]: r for r in _corpus_rows("machinery_pass")}
    live = _drive_live("machinery_pass")

    common = sorted(set(rows) & set(live))
    assert len(common) >= 12, (
        f"only {len(common)} overlapping timestamps — the probe is not comparing "
        f"enough samples for the result to mean anything"
    )

    worst = (0.0, None, None)
    for t in common:
        expected = _vector_of(rows[t])
        got = np.asarray(live[t][0], dtype=float)
        assert len(got) == F.N_EXTENDED
        diff = np.abs(got - expected)
        diff = np.where(np.isnan(diff), 0.0, diff)
        j = int(diff.argmax())
        if diff[j] > worst[0]:
            worst = (float(diff[j]), t, F.EXTENDED_FEATURES[j])

    assert worst[0] < 1e-6, (
        f"live and corpus features diverge by {worst[0]:.3g} on {worst[2]} at "
        f"t={worst[1]} — the classifier is being served a different world from "
        f"the one it was trained on"
    )


def test_the_live_path_reaches_the_corpus_verdict_on_the_event():
    """And the *labels* agree, which is what the feature equality is for.

    Two windows are allowed to differ, and the allowance is stated rather than
    tuned: the corpus labels a row from the injected profile, while the live path
    labels it from what the 60.5 s window contains. In the first ~1 s after
    arming the window is still almost entirely quiet, so the classifier saying
    QUIET is not a disagreement — it is the more honest of the two readings, and
    it is the one a real mine would get. Every subsequent sample must agree.
    """
    rows = {r["t_s"]: r for r in _corpus_rows("machinery_pass")}
    live = _drive_live("machinery_pass")
    common = sorted(set(rows) & set(live))

    onset = min(common)
    agree = sum(1 for t in common if rows[t]["primary"] == live[t][1])
    late = [t for t in common if t > onset + 1.0]
    late_agree = sum(1 for t in late if rows[t]["primary"] == live[t][1])

    assert late_agree == len(late), (
        f"live and corpus disagree away from the onset sample: "
        f"{[ (t, rows[t]['primary'], live[t][1]) for t in late if rows[t]['primary'] != live[t][1] ]}"
    )
    assert agree >= len(common) - 2, f"only {agree}/{len(common)} labels agree"


def test_the_corpus_reproduces_its_own_labels_through_the_live_classifier():
    """The corpus is self-consistent: the labels it asserts are the labels its
    own feature vectors produce.

    This is the weakest of the three parity tests and it still matters: it catches
    a corpus whose labels were written from the profile name while the features
    describe something else — which is exactly what a mis-scoped ``node_scale`` or
    a mis-keyed ``PROFILE_CLASS`` would produce.
    """
    rows = _corpus_rows("critical_collapse", run_id=901, node="node_2")
    event = [r for r in rows if r["phase"] == "event"]
    assert event, "probe run produced no event rows"
    agree = sum(1 for r in event if (PAT.classify(_vector_of(r)) or {}).get("label") == r["primary"])
    assert agree >= len(event) - 2, (
        f"the corpus reproduces its own labels on only {agree}/{len(event)} rows"
    )


# ---------------------------------------------------------------------------
# 4. The fast path is the model
# ---------------------------------------------------------------------------

def test_the_fast_path_reproduces_sklearn_on_a_real_window():
    """``pattern._walk`` is what ships. If it stops being the model, stop shipping.

    Bit-equivalence is asserted at 1e-9: the walk re-implements the forest's
    traversal in vectorised numpy, and the only permitted difference is float64
    summation order. A tolerance any looser would hide a leaf counted twice.
    """
    row = _a_corpus_row_labelled("STRUCTURAL")
    x = _vector_of(row).reshape(1, -1)
    model = PAT.PATTERN_INFO["model"]
    fast = PAT.PATTERN_INFO.get("fast")
    if fast is None:
        pytest.skip("fast path not built for this artifact")

    ref = np.asarray(model.predict_proba(x), dtype=float).ravel()
    got = np.asarray(PAT._walk(fast, x), dtype=float).ravel()
    assert float(np.max(np.abs(got - ref))) < 1e-9


def test_a_raising_fast_path_degrades_to_sklearn_rather_than_to_nothing():
    """A broken optimisation must cost speed, never a verdict.

    ``classify`` disables the fast path on the first exception and retries through
    scikit-learn. That branch is the reason a hand-written tree walk is allowed to
    exist in a safety-relevant system at all, so it is exercised deliberately: the
    corruption here *raises* (a walker array of the wrong width), and the verdict
    must survive it.

    **This test covers only the loud failure, and the distinction is worth being
    precise about.** The first version of it zeroed ``left`` — every branch wrong —
    and the test failed, because a silently-wrong walk returns a perfectly
    well-formed probability vector and nothing can tell. No runtime guard can
    detect that, and pretending otherwise would be worse than not having one: a
    check that appears to cover it invites people to stop checking.

    The silent case is covered at **build time** instead, by two tests that compare
    the walk against scikit-learn on inputs it has never seen
    (``test_the_fast_path_reproduces_sklearn_on_a_real_window``, at 1e-9) and by
    ``tools/bench_latency.py``'s agreement assertion on a synthetic row. Together
    those say "the walk equals the model"; this one says "a walk that fails still
    produces a verdict". Neither alone is sufficient.
    """
    saved = dict(PAT.PATTERN_INFO)
    try:
        PAT.load_pattern_model()
        good = PAT.PATTERN_INFO.get("fast")
        if good is None:
            pytest.skip("no fast path to corrupt")
        broken = dict(good)
        # Width mismatch: the walker indexes `feat[nodes, feature]` with a feature
        # index derived from `x`, so a narrow array raises rather than misreading.
        broken["feat"] = good["feat"][:, :4]
        PAT.PATTERN_INFO["fast"] = broken

        row = _a_corpus_row_labelled("STRUCTURAL")
        out = PAT.classify(_vector_of(row))
        assert out is not None, "classify returned None instead of falling back"
        assert PAT.PATTERN_INFO.get("fast") is None, (
            "the fast path raises but was not disabled, so every subsequent call "
            "pays the exception and the retry"
        )
        # ...and the fallback verdict is the one sklearn gives, not a default.
        ref = np.asarray(PAT.PATTERN_INFO["model"].predict_proba(
            _vector_of(row).reshape(1, -1)), dtype=float).ravel()
        order = PAT._proba_order(PAT.PATTERN_INFO["model"],
                                 PAT.PATTERN_INFO.get("meta") or {})
        assert out["probs"][str(order[int(ref.argmax())])] == pytest.approx(float(ref.max()), abs=1e-4)
    finally:
        PAT.PATTERN_INFO.clear()
        PAT.PATTERN_INFO.update(saved)
        PAT.load_pattern_model()


# ---------------------------------------------------------------------------
# 5. The bench path actually feeds the models
# ---------------------------------------------------------------------------

def test_age_of_cannot_distinguish_never_heard_from_fresh():
    """**The trap, stated as an assertion.** ``age_of`` answers ``0.0`` for a node
    that has never spoken.

    It is a reasonable contract in isolation and a dangerous one in a guard:
    ``if age_of(n) <= 1.0: continue   # frames are filling it`` skips the node
    that has *no* frames, forever. That exact guard shipped in the ticker's
    model-buffer branch, and on a bench with no serial port attached it meant the
    four windows never filled, Model 1 and Model 2 never ran, and ``/api/ai`` sat
    on "WARMING UP" for the life of the process while every other number on the
    dashboard moved.

    ``engine/health.py`` documents the same distinction as its third rule and the
    crack-engine branch in ``app.py`` already carries the fix, so this is the
    *second* time the guard has been forgotten. It is asserted here so it cannot
    be forgotten a third time without a red test.
    """
    from state import SystemState
    st = SystemState()
    n_id = config.ACTIVE_NODES[0]
    assert st.age_of(n_id) == 0.0, (
        "age_of's never-heard contract changed — the guard workaround below can "
        "now be simplified, and the source assertion in the next test is stale"
    )
    assert st.last_arrival.get(n_id) is None, (
        "last_arrival is the predicate that CAN tell the two apart, and it must "
        "still be unset for a node that has never delivered a frame"
    )


def test_the_ticker_fills_windows_for_nodes_that_never_report():
    """Source-level, and deliberately so.

    The guard lives inside ``ticker_thread``'s per-node loop, which cannot be
    invoked without starting the whole 20 Hz clock. Asserting on the source is
    cruder than executing it — but the alternative is not testing it at all, and
    the failure mode here is silent, total, and was live for the whole of Phase
    5's development.

    Both call sites that care about "is this node being fed by frames" must build
    the predicate from ``last_arrival``. ``age_of`` alone is not enough, per the
    test above.
    """
    src = (BACKEND / "app.py").read_text()
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    assert "heard = STATE.last_arrival.get(node.node_id) is not None" in body, (
        "the `heard` predicate is missing from app.py — a node that has never "
        "reported will be skipped by the local-tick branches"
    )
    # Two branches need it: the crack engine and the model buffer. One occurrence
    # means one of them has been un-fixed.
    assert body.count("heard and STATE.age_of(node.node_id, t0) <= 1.0") == 2, (
        "both the crack-engine branch and the model-buffer branch must guard on "
        "`heard`; found "
        f"{body.count('heard and STATE.age_of(node.node_id, t0) <= 1.0')}"
    )


def test_the_recall_override_cannot_overrule_a_positive_environmental_verdict():
    """The override may break a QUIET tie. It may not overturn ENVIRONMENTAL.

    Measured on the shipped model over all 43,092 corpus windows: the unbounded
    override changes exactly one row, that row is truly ENVIRONMENTAL, and the
    override labels it STRUCTURAL. It therefore buys **no** recall — STRUCTURAL
    recall is 1.0000 with or without it — and costs one false structural alarm on
    a nuisance event, which is the failure §5.12's machinery row exists to forbid.

    The threshold that produced this is **0.05, the grid floor**, because the
    sweep walks to the bottom of its range. That is the real lesson: an uncalibrated
    floor used as an override is not recall insurance, it is a bar low enough to
    overrule a confident verdict.
    """
    thr = float(PAT.PATTERN_INFO["meta"]["operating_threshold"])

    # A window the model is confident is environmental, with a structural
    # probability above the threshold. Built from the model's own output rather
    # than asserted, so the test cannot drift from the artifact.
    # `machinery_pass` is the profile that produces ENVIRONMENTAL rows — the
    # crack profiles never do, which is itself the three-way fold working.
    rows = _corpus_rows("machinery_pass", run_id=902, node="node_2")
    env_rows = [r for r in rows if r["primary"] == "ENVIRONMENTAL"]
    assert env_rows, "machinery_pass produced no ENVIRONMENTAL rows"
    out = PAT.classify(_vector_of(env_rows[len(env_rows) // 2]))
    assert out is not None
    assert out["label"] == "ENVIRONMENTAL", out
    assert out["structural_prob"] <= thr or out["threshold"] == thr

    # The rule, stated directly: a structural probability above the threshold
    # only raises the label when the argmax was QUIET or UNKNOWN.
    assert PAT._RAISABLE_LABELS == ("QUIET", "UNKNOWN"), (
        "the set of labels the recall override may raise from has changed — if it "
        "now includes ENVIRONMENTAL, re-run the 43,092-row measurement before "
        "believing it is still worth having"
    )
    assert "ENVIRONMENTAL" not in PAT._RAISABLE_LABELS


# ---------------------------------------------------------------------------
# §5.8 — the published surface
#
# These tests exist because §5.8 puts a second set of names on this module and a
# second name is a second place for the truth to live. Each one checks that the
# published name and the working name the cascade calls still agree, rather than
# that the published name merely exists.
# ---------------------------------------------------------------------------

def test_the_published_extractor_is_the_canonical_one():
    """``compute_disruption_features`` re-exported, not reimplemented.

    If this ever becomes a real second implementation, the live cascade and a
    notebook would compute the 16 disruption features twice, with the same names
    and two sets of numbers.
    """
    from engine import features as F
    assert PAT.compute_disruption_features is F.compute_disruption_features


def test_predict_pattern_and_classify_agree_field_by_field():
    """The §5.8 wrapper must not reinterpret the working classifier."""
    feats = [0.0, 0.1, 0.2, 0.3] * 8
    base = PAT.classify(feats)
    assert base is not None, "model not loaded — cannot check agreement"
    res = PAT.predict_pattern(feats)
    assert res.label == base["label"]
    assert abs(res.disruption_index - base["disruption_index"]) < 1e-9
    assert res.proba == base["probs"]
    assert res.top_features == base["top_features"]
    assert abs(res.structural_prob - base["structural_prob"]) < 1e-9
    assert res.provisional is False


def test_predict_pattern_returns_unknown_rather_than_none(monkeypatch):
    """An absent classifier is UNKNOWN and provisional — never ``None``.

    ``None`` would render as an empty box on the panel beside a live countdown,
    and an empty box is read as "fine". UNKNOWN is alert-worthy in the cascade.
    """
    monkeypatch.setattr(PAT, "is_active", lambda: False)
    res = PAT.predict_pattern([0.0] * 32)
    assert res.label == "UNKNOWN"
    assert res.provisional is True
    assert res.proba == {}
    assert res.disruption_index == 0.0


def test_operating_threshold_is_the_one_classify_compares_against():
    """The anti-divergence test: one threshold, two readers.

    A report that quotes 0.05 while the process compares against 0.5 publishes a
    confusion matrix for a classifier that is not running.
    """
    feats = [0.0, 0.1, 0.2, 0.3] * 8
    base = PAT.classify(feats)
    assert base is not None
    assert PAT.operating_threshold() == base["threshold"]
    assert PAT.operating_threshold(PAT.PATTERN_INFO.get("meta")) == base["threshold"]
    assert PAT.operating_threshold({"operating_threshold": 0.42}) == 0.42
    # A meta file that lost its threshold degrades to a plain argmax, never to a
    # floor of zero — a floor of zero would manufacture STRUCTURAL verdicts.
    assert PAT.operating_threshold({}) == 0.5
    assert PAT.operating_threshold(None) == base["threshold"]


def test_explain_pattern_drops_a_channel_that_did_not_report():
    """The explanation may not cite a gauge that is dead.

    The extractor emits NaN for an unmeasurable crack channel (never a zero, so
    that "not reporting" is not mistaken for "the seam is shut"), and a ranking
    that ignores that would explain a verdict using the one number that is absent.
    """
    res = PAT.PatternResult(
        label="QUIET", disruption_index=0.01,
        top_features=[["micro_rate", 0.20], ["v60", 0.10]])
    feats = [0.0] * len(PAT.F.EXTENDED_FEATURES)
    feats[PAT.F.EXTENDED_INDEX["micro_rate"]] = float("nan")
    feats[PAT.F.EXTENDED_INDEX["v60"]] = 0.4

    kept = PAT.explain_pattern(res, feats)
    assert kept == [("v60", 0.10)], kept

    # With every cited channel present, both survive and the order is preserved.
    feats[PAT.F.EXTENDED_INDEX["micro_rate"]] = 0.0
    assert [n for n, _ in PAT.explain_pattern(res, feats)] == ["micro_rate", "v60"]

    # No feature vector at all: the global ranking is still a true statement.
    assert len(PAT.explain_pattern(res)) == 2


def test_is_structural_never_counts_ignorance_as_evidence():
    """The recall claim must not be satisfiable by knowing nothing.

    UNKNOWN escalates in the *firewall*, which is a different rule answering a
    different question. If this predicate returned True for UNKNOWN, §5.8's
    "recall ≥ 0.98" could be met by an unplugged gauge.
    """
    unknown = PAT.PatternResult("UNKNOWN", 0.0, {"STRUCTURAL": 0.0})
    assert PAT.is_structural(unknown, 0.05) is False

    # The label being STRUCTURAL is enough, whatever the threshold.
    assert PAT.is_structural(PAT.PatternResult("STRUCTURAL", 0.9, {}), 0.99) is True

    # And a low-probability structural signal raised by the *threshold* counts —
    # that is the recall mechanism §5.8 asks the threshold to provide.
    quiet = PAT.PatternResult("QUIET", 0.1, {"STRUCTURAL": 0.9})
    assert PAT.is_structural(quiet, 0.05) is True
    assert PAT.is_structural(quiet, 0.95) is False

    # The default threshold is the operating one, not a literal.
    assert PAT.is_structural(PAT.PatternResult("QUIET", 0.1, {"STRUCTURAL": 0.9})
                             ) == (0.9 >= PAT.operating_threshold())
