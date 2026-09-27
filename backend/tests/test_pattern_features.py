"""
Phase 5 (Model 1) — the 32-feature extended extractor.
======================================================

§5.6's sixteen disruption features, plus the contract that makes them safe to
bolt onto a shipping model: the **core 16 must not move**. ``ttf_model_v1.json``
was trained on ``compute_live_features``' output and is what the dashboard shows
today; a Phase 5 that nudged one of those sixteen numbers would silently degrade
the predictor that already works, and nothing would fail — the pill would just be
a little more wrong, for ever.

So group 1 is that contract, asserted rather than promised, and groups 2–7 are
the features themselves. Three of them are subtle enough to deserve their own
paragraphs:

**`impulse_periodicity` — the one that was wrong first.** The naive reading of
§5.6 is "the autocorrelation peak in the 0.3–3 s lag band". Implemented that way
it measures **smoothness**: any smooth trace — a rock burst, an exponential
decay — has its largest autocorrelation at the shortest lag and decays from
there, so a Gaussian burst scored 0.94 while a genuinely periodic signal scored
0. The feature reported the opposite of its own name on the very first test.
It now requires a strict **interior peak** scored by prominence, which is what
"periodic" means: the correlation has to *come back up*.

**`settlement_ratio` — the one where the plan's formula has an off-by-six.**
Written literally as ``|d60| / (|d10| + ε)``, a steady 1 mm/s gives 60/10 = **6.0**,
not 1.0. The code returns a velocity ratio instead (each delta divided by its own
window) so the pivot lands where §5.6's prose wants it; the deviation is recorded
in §5.0.

**NaN discipline.** Eleven of these features need channels, peers or engine
output that may legitimately be absent. Absence returns ``NaN``, never ``0.0``:
"the other nodes are quiet" and "I cannot see the other nodes" are exactly the
two statements this feature set exists to tell apart, and a flattering zero
collapses them into one.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

import config
from engine import features as F

N = config.MODEL_BUF
DT = config.MODEL_DT


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def buf(acc, disp=None, tilt=None, vib=None, micro=None, macro=None) -> list[tuple]:
    """A 7-channel window: ``(t, disp, tilt, acc, vib, micro, macro)``."""
    acc = np.asarray(acc, dtype=float)
    n = len(acc)
    def col(v, default):
        return np.full(n, default, dtype=float) if v is None else np.asarray(v, dtype=float)
    return [(i * DT, float(col(disp, 5.0)[i]), float(col(tilt, 0.0)[i]), float(acc[i]),
             float(col(vib, 0.0)[i]), float(col(micro, 0.0)[i]), float(col(macro, 0.0)[i]))
            for i in range(n)]


def f(buf_, name: str) -> float:
    return F.compute_disruption_features(buf_)[F.DISRUPTION_FEATURES.index(name)]


def is_nan(v: float) -> bool:
    return isinstance(v, float) and math.isnan(v)


T = np.arange(N) * DT
STEADY = np.zeros(N)
RAMP = 5.0 + 0.5 * T                     # 0.5 mm/s, accelerating nothing
PERIODIC = np.sin(2 * np.pi * 0.5 * T)   # 0.5 Hz ⇒ 2 s period ⇒ lag 4, inside the band
BURST = np.exp(-((T - 10.0) ** 2) / 2.0) * 4.0


# ===========================================================================
# 1 · The additive contract — the core 16 must not move
# ===========================================================================

def test_the_core_sixteen_are_bit_identical_inside_the_thirty_two():
    """The whole reason ``compute_extended_features`` *calls* the core extractor
    instead of reimplementing it."""
    b = buf(RAMP)
    core = F.compute_live_features(b)
    ext = F.compute_extended_features(b)
    assert core is not None and ext is not None
    # NaN-aware: `ttf_fuk` is legitimately NaN on a window with no usable
    # Fukuzono fit, and `nan != nan`, so a plain list compare would report a
    # change where there is none.
    assert all((a == b) or (is_nan(a) and is_nan(b)) for a, b in zip(ext[:16], core)), \
        "the shipping predictor's features changed"
    assert len(ext) == 32


def test_the_feature_names_are_ordered_and_unique():
    assert F.N_FEATURES == 16 and F.N_EXTENDED == 32
    assert F.EXTENDED_FEATURES[:16] == F.FEATURES
    assert len(set(F.EXTENDED_FEATURES)) == 32
    assert F.EXTENDED_FEATURES[23] == "recovery_ratio"      # §5.6 #24, 1-indexed
    assert F.EXTENDED_FEATURES[25] == "impulse_periodicity"
    assert F.EXTENDED_FEATURES[28] == "spatial_coherence"
    assert F.EXTENDED_FEATURES[31] == "tilt_acc_coupling"


def test_an_incomplete_window_yields_none_not_a_short_row():
    """A truncated row is worse than no row: a tree model reads positionally, so
    31 values against 32 names silently mislabels every column after the gap."""
    assert F.compute_extended_features(buf(RAMP)[:10]) is None
    assert F.compute_extended_features([]) is None


def test_the_scaler_contract_is_untouched():
    """``scaler_matches`` guards Model 2. Phase 5 must not have moved it."""
    assert F.scaler_matches({"features": list(F.FEATURES)})
    assert not F.scaler_matches({"features": list(F.EXTENDED_FEATURES) + ["x"]})


# ===========================================================================
# 2 · impulse_periodicity — periodic vs a burst (the defect that was caught)
# ===========================================================================

def test_periodicity_separates_an_engine_from_a_rock():
    """The feature's whole job, and the one it initially got backwards.

    A machine is periodic — engine firing, axle wheel-pass — so its
    autocorrelation comes *back up* inside the lag band. A rock is a burst with a
    decay: smooth, monotone, no interior peak. Implemented as a raw band maximum
    this scored the rock **0.94** and a periodic signal **0.00**.
    """
    periodic = f(buf(PERIODIC), "impulse_periodicity")
    rock = f(buf(BURST), "impulse_periodicity")
    assert periodic > 0.5, f"a genuinely periodic window scored {periodic}"
    assert rock < 0.05, f"a monotone rock burst scored {rock} — that is smoothness, not periodicity"


def test_periodicity_is_not_just_loudness():
    """Amplitude must not move it: scaling the same periodic trace changes nothing."""
    quiet = f(buf(PERIODIC * 0.01), "impulse_periodicity")
    loud = f(buf(PERIODIC * 50.0), "impulse_periodicity")
    assert quiet == pytest.approx(loud, abs=1e-9)


def test_periodicity_of_a_flat_window_is_zero_and_of_a_short_one_is_nan():
    """``0.0`` means "measured, and there is no periodic structure". ``NaN`` means
    "this window is too short to ask" — never the same answer."""
    assert f(buf(STEADY), "impulse_periodicity") == 0.0
    assert is_nan(f(buf(np.ones(4)), "impulse_periodicity"))


# ===========================================================================
# 3 · recovery_ratio — the truck/rock discriminator (§5.6 calls it #24)
# ===========================================================================

def test_a_recovered_truck_reads_one_and_an_accumulating_crack_reads_zero():
    """1.0 = whatever moved has come back (the loader has gone). ~0.0 = still
    accumulating (the rock is not giving anything back)."""
    truck = buf(STEADY, disp=np.concatenate([np.linspace(5, 9, 60), np.linspace(9, 5.05, 61)]))
    crack = buf(STEADY, disp=np.linspace(5, 9, N))            # monotone, never returns
    assert f(truck, "recovery_ratio") > 0.95
    assert f(crack, "recovery_ratio") < 0.05


def test_a_window_that_never_moved_is_fully_recovered_not_undefined():
    """No excursion to recover *from* is the recovered case, and saying ``NaN``
    here would throw away the most common quiet window on the rig."""
    assert f(buf(STEADY, disp=np.full(N, 5.0)), "recovery_ratio") == 1.0


# ===========================================================================
# 4 · settlement_ratio — the pivot is 1.0, not 6.0
# ===========================================================================

def test_settlement_ratio_is_a_velocity_ratio_with_a_pivot_at_one():
    """The plan's literal ``|d60| / (|d10| + ε)`` puts a steady drift at **6.0**,
    because it divides a 60 s delta by a 10 s delta. Dividing each delta by its
    own window gives the ratio §5.6's prose describes: 1.0 = steady.
    """
    steady = f(buf(STEADY, disp=RAMP), "settlement_ratio")
    assert steady == pytest.approx(1.0, abs=0.02), (
        "a constant-velocity window must sit on the pivot, not at 6.0")

    # **Orientation: HIGH = decelerating, LOW = accelerating** — the same way
    # round as `recovery_ratio`, so the two do not point in opposite directions.
    #
    # Decelerating: it moved early and then stopped. `d60` spans the whole 60 s
    # window, so it still contains that early motion and is large, while `d10` —
    # the last 10 s — is zero. The ratio is large. This reads backwards until you
    # notice that the window *is* 60 s, so `d60` is a historical average rather
    # than a trend.
    slowing = buf(STEADY, disp=np.concatenate([np.linspace(5, 15, 20), np.full(101, 15.0)]))
    assert f(slowing, "settlement_ratio") > 1.5

    # Accelerating: still for 50 s, then a sharp ramp.
    speeding = buf(STEADY, disp=np.concatenate([np.full(100, 5.0), np.linspace(5, 15, 21)]))
    assert f(speeding, "settlement_ratio") < 0.5


# ===========================================================================
# 5 · excursion duration and the local waveform features
# ===========================================================================

def test_excursion_duration_counts_the_trailing_run_only():
    """Seconds continuously above ``ACC_WARNING`` — a 3 s truck impulse and a
    40 s creep are the same *peak* and different *durations*, which is the
    distinction the feature exists to carry."""
    over = config.ACC_WARNING * 1.5
    # The trailing run is what counts: a 3 s impulse that has just started, at the
    # END of the window. (An earlier version of this test put the band at t >= 3,
    # which is not a 3 s impulse — it is 57 s of continuous excursion, and the
    # feature correctly said so.)
    short = buf(np.where(T >= (N - 1) * DT - 3.0, over, 0.0))
    assert f(short, "excursion_duration_s") == pytest.approx(3.0, abs=1.1)

    quiet = buf(STEADY)
    assert f(quiet, "excursion_duration_s") == 0.0

    # A burst that has already passed leaves no trailing excursion.
    passed = buf(np.where(T < 20.0, over, 0.0))
    assert f(passed, "excursion_duration_s") == 0.0


def test_vibration_features_count_the_sw420_channel():
    """The SW-420 is a *counter*, not a level — 17/18 sum it rather than reading it."""
    # One event per sample across the last 10 s. The band is inclusive at both
    # ends, so t = 50.0 … 60.0 is 21 samples at 2 Hz ⇒ 21 events, rate 2.1/s.
    b = buf(STEADY, vib=np.where(T >= (N - 1) * DT - 10.0, 1.0, 0.0))
    assert f(b, "vib_count_60") == pytest.approx(21.0)
    assert f(b, "vib_rate_10") == pytest.approx(2.1, abs=0.01)
    # Older events count toward the minute but have dropped out of the 10 s rate.
    old_events = buf(STEADY, vib=np.where((T > 5.0) & (T < 15.0), 1.0, 0.0))
    assert f(old_events, "vib_count_60") == pytest.approx(19.0)
    assert f(old_events, "vib_rate_10") == pytest.approx(0.0)


def test_zero_cross_rate_measures_roughness_not_level():
    # 0.5 Hz ⇒ 2 sign changes per cycle × 0.5 cycles/s = 1.0 crossing/s.
    assert f(buf(PERIODIC), "zero_cross_rate") == pytest.approx(1.0, abs=0.1)
    assert f(buf(STEADY), "zero_cross_rate") == 0.0
    # A DC offset with no oscillation has no zero crossings, however large.
    assert f(buf(np.full(N, 9.0)), "zero_cross_rate") == 0.0


# ===========================================================================
# 6 · The cross-sensor and cross-node features — NaN, never a flattering zero
# ===========================================================================

def test_coherence_comes_from_the_crack_engine_not_a_second_implementation():
    """§5.6 #23 is Phase 3's ``cross_sensor_coherence``. It is passed in, because
    a second implementation in a feature extractor is a second answer to the same
    question — and Model 1 would be trained on one and served the other."""
    b = buf(RAMP)
    assert is_nan(f(b, "cross_sensor_coherence")), "no engine reading ⇒ NaN"
    v = F.compute_disruption_features(b, crack={"coherence": 0.13})[
        F.DISRUPTION_FEATURES.index("cross_sensor_coherence")]
    assert v == pytest.approx(0.13)


def test_micro_and_macro_pass_through_from_the_crack_engine():
    b = buf(RAMP)
    out = F.compute_disruption_features(
        b, crack={"micro_rate": 6.0, "macro_d30": 1.2, "macro_total": 3.4})
    Ix = F.DISRUPTION_FEATURES.index
    assert out[Ix("micro_rate")] == pytest.approx(6.0)
    assert out[Ix("macro_d30")] == pytest.approx(1.2)
    assert out[Ix("macro_total")] == pytest.approx(3.4)
    # micro_z is normalised *and clamped*: it says "at or past the threshold",
    # not "by how many multiples" — otherwise one railed sample owns the split.
    assert out[Ix("micro_z")] == pytest.approx(
        float(np.clip(6.0 / config.MICRO_RATE_THRESHOLD, -3.0, 3.0)))
    huge = F.compute_disruption_features(b, crack={"micro_rate": 9_000.0})
    assert huge[Ix("micro_z")] == 3.0


def test_spatial_features_are_nan_without_peers():
    """A node that cannot see the others says so. ``0.0`` here would claim
    "no spatial correlation" from no data at all."""
    b = buf(STEADY)
    out = F.compute_disruption_features(b)
    Ix = F.DISRUPTION_FEATURES.index
    assert is_nan(out[Ix("spatial_coherence")])
    assert is_nan(out[Ix("spatial_decay")])
    assert out[Ix("n_active_anomalous")] == 0.0


def test_spatial_coherence_is_high_when_a_neighbour_shakes_too():
    """*Local ⇒ machinery; multi-node ⇒ ground.* The same burst on two nodes is a
    different physical claim from the same burst on one."""
    mine = buf(BURST)
    shouter = {"node_3": buf(BURST * 0.9)}
    quiet = {"node_3": buf(STEADY)}
    Ix = F.DISRUPTION_FEATURES.index

    with_peer = F.compute_disruption_features(mine, peers=shouter)[Ix("spatial_coherence")]
    alone = F.compute_disruption_features(mine, peers=quiet)[Ix("spatial_coherence")]
    assert with_peer > 0.8, "a neighbour shaking in step should correlate"
    # NOT NaN: a neighbour that is present and perfectly still is a measurement
    # ("I am not shaking"), and it is the evidence that the disturbance is local.
    assert alone == pytest.approx(0.0), (
        "a still-but-present neighbour must read as no coherence, not as unknown")


def test_n_active_anomalous_counts_neighbours_that_are_shaking():
    mine = buf(STEADY)
    peers = {"node_2": buf(STEADY, vib=np.ones(N)), "node_3": buf(STEADY)}
    Ix = F.DISRUPTION_FEATURES.index
    assert F.compute_disruption_features(mine, peers=peers)[Ix("n_active_anomalous")] == 1.0


# ===========================================================================
# 7 · Determinism and the published row
# ===========================================================================

def test_the_same_window_always_gives_the_same_numbers():
    """A replay has to reproduce a demo. Any ``random`` in this path would make
    the feature set unrehearsable."""
    b = buf(BURST, vib=np.linspace(0, 2, N))
    a1 = F.compute_disruption_features(b)
    a2 = F.compute_disruption_features(b)
    assert a1 == a2 or all(
        (x == y) or (is_nan(x) and is_nan(y)) for x, y in zip(a1, a2))


def test_extended_feature_row_maps_nan_to_none_and_keeps_the_names():
    row = F.extended_feature_row(F.compute_extended_features(buf(RAMP)))
    assert len(row) == 32
    assert set(row) == set(F.EXTENDED_FEATURES)
    for k, v in row.items():
        assert v is None or isinstance(v, float), f"{k} is {type(v).__name__}"


def test_an_unmeasured_window_returns_all_none():
    row = F.extended_feature_row(None)
    assert len(row) == 32 and all(v is None for v in row.values())


def test_a_degenerate_timestamp_column_cannot_crash_the_window():
    """The dt fallback is the branch nothing exercised — and it was broken.

    Every buffer in this file is built by ``buf()``, whose timestamps increase by
    exactly ``DT``, so the ``else`` branch that falls back to the nominal cadence
    was never reached. That branch referred to ``MODEL_DT`` without importing it:
    a ``NameError`` waiting for the first caller to hand this function a window
    with a repeated or backwards timestamp — a one-sample window, a clock step, a
    replay that rewrote the column. It was found by ``ruff --select F821`` in the
    end-of-phase sweep, not by a test, which is why there is now a test.

    Two windows reach it: 121 samples sharing one timestamp, and a single sample
    (no previous sample to difference against at all).
    """
    flat = [(5.0, 1.0, 0.0, 0.1, 2.0, 3.0, 4.0)] * N
    assert len(F.compute_disruption_features(flat)) == 16

    single = [(5.0, 1.0, 0.0, 0.1, 2.0, 3.0, 4.0)]
    assert len(F.compute_disruption_features(single)) == 16

    # The same missing import sat behind the ACF helper's `dt=None`, which is the
    # signature every caller that does not know the cadence would use. Nine
    # samples is the first length the helper will fit at all; seven is below
    # `_MIN_PERIODICITY_SAMPLES` and gets the documented NaN, not a crash.
    assert math.isfinite(F._acf_periodicity(np.array([0.1, 0.2, 0.15, 0.3, 0.1, 0.25, 0.2, 0.1, 0.3])))
    assert math.isnan(F._acf_periodicity(np.array([0.1, 0.2, 0.15, 0.3, 0.1, 0.25, 0.2])))

    # and the fallback is the nominal cadence rather than a second guess
    assert DT == pytest.approx(0.5)
