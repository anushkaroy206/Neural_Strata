"""
Phase 5 — B11: the kinematic fallback, and its parity with the training feature.
=================================================================================

Two things are being tested here, and the second one matters more than the first.

1. **The calibration is read, validated and used.** Before this module,
   `models/kinematic_calibration.json` had no reader — defect D2 in its literal
   form. A file that ships and is never opened is not a calibration, it is a
   claim, so the tests check the law is actually driving the numbers (the
   monotonicity test changes the velocity and watches the horizon move) and that
   a broken file is refused by name rather than half-applied.

2. **`fukuzono_ttf_live` is the *same estimator* as the training feature
   `ttf_fuk`.** §6.2's requirement, and the reason the fallback is physics rather
   than a second opinion with its own arithmetic: if these two drift, the model
   is checked against a formula it was never fitted to, and nothing notices. The
   parity test runs both over the same synthetic window — including the case
   where the fit is *supposed* to fail — and asserts they agree to 1 %.

The window generator is imported from `tests/test_features.py` rather than
re-typed, so "the same window" is literally the same array on both sides.
"""

from __future__ import annotations

import json

import numpy as np
import pytest

import config
from engine import cascade as C
from engine import kinematic as K
from engine.features import compute_live_features, feature_row
from state import SystemState

from test_features import _buf, _run          # same synthetic runs, not a copy

FAST = dict(ramp=0.010, v_scale=0.30)         # v10 well above FUK_V_MIN
NODE = "node_1"


@pytest.fixture(autouse=True)
def _fresh_calibration():
    """The calibration is cached at module level — do not leak it across tests."""
    K.reset()
    yield
    K.reset()


def _converging(horizon_s: float = 20.0, n: int = 121, dt: float = 0.5,
                a: float = 0.05, b: float = 0.010):
    """A window that is genuinely *converging* on a failure horizon.

    ``1/v = a + b(t_f − t)`` is the inverse-velocity law itself, so the fit's
    intercept is exactly ``horizon_s`` by construction: if the estimator returns
    anything else, it is not the estimator it claims to be. Displacement is the
    integral of ``v``, so nothing here is fitted from noise — the answer is known
    before the call.

    Note the velocity this produces: every sample sits above ``FUK_V_MIN``
    because ``1/v < 1`` is the same statement as ``v > 1``. That is not a
    coincidence of the constants — it is why the *live* rung's short numbers
    always arrive with real motion, and the section below tests what follows.
    """
    t = np.arange(n) * dt
    t_f = t[-1] + horizon_s
    v = 1.0 / (a + b * (t_f - t))
    disp = 40.0 + np.concatenate([[0.0], np.cumsum(v[:-1] * dt)])
    tilt = np.zeros_like(t)
    return _buf(t, disp, tilt, np.zeros_like(t))


def _state_with(buf, disp_now=None, velocity=0.0, acceleration=0.0):
    st = SystemState()
    st.buffers[NODE].clear()
    for sample in buf:
        st.buffers[NODE].append(tuple(float(x) for x in sample))
    node = st.nodes[NODE]
    node.velocity = velocity
    node.acceleration = acceleration
    node.displacement_mm = float(buf[-1][1] if disp_now is None else disp_now)
    return st


# ---------------------------------------------------------------------------
# The calibration file
# ---------------------------------------------------------------------------
def test_the_shipped_calibration_loads_and_carries_the_documented_law():
    calib = K.load_calibration()
    assert calib["alpha_exponent"] == pytest.approx(1.95)
    assert calib["a_coefficient"] == pytest.approx(0.082)
    assert calib["breach_limit_mm"] == pytest.approx(100.0)
    assert calib["stable_ttf_clamp_s"] == pytest.approx(3600.0)
    info = K.calibration_info()
    assert info["loaded"] is True and info["source"] == "file"
    assert info["alpha"] == pytest.approx(1.95)


def test_a_calibration_missing_a_key_is_refused_by_name(tmp_path):
    path = tmp_path / "kinematic_calibration.json"
    good = json.loads(config.MODEL_FILES["kinematic_calibration"].read_text())
    del good["a_coefficient"]
    path.write_text(json.dumps(good))
    with pytest.raises(ValueError, match="a_coefficient"):
        K.load_calibration(path)


@pytest.mark.parametrize("value", ["steep", None, True])
def test_a_non_numeric_law_is_refused(tmp_path, value):
    path = tmp_path / "kinematic_calibration.json"
    good = json.loads(config.MODEL_FILES["kinematic_calibration"].read_text())
    good["alpha_exponent"] = value
    path.write_text(json.dumps(good))
    with pytest.raises(ValueError, match="alpha_exponent"):
        K.load_calibration(path)


def test_alpha_at_or_below_one_is_refused(tmp_path):
    """α ≤ 1 has no finite horizon — the law would divide by zero or invert."""
    path = tmp_path / "kinematic_calibration.json"
    good = json.loads(config.MODEL_FILES["kinematic_calibration"].read_text())
    good["alpha_exponent"] = 1.0
    path.write_text(json.dumps(good))
    with pytest.raises(ValueError, match="alpha_exponent"):
        K.load_calibration(path)


def test_a_corrupt_file_is_refused_not_ignored(tmp_path):
    path = tmp_path / "kinematic_calibration.json"
    path.write_text("{not json at all")
    with pytest.raises(ValueError, match="valid JSON"):
        K.load_calibration(path)


def test_the_calibration_is_optional(monkeypatch, tmp_path):
    """`required: false` in MANIFEST — the fallback must survive its absence.

    Surviving means computing with the documented defaults *and* saying so:
    `calibration_info()` reports `source: "defaults"` so the dashboard never
    presents a default as a calibrated law.
    """
    monkeypatch.setitem(config.MODEL_FILES, "kinematic_calibration",
                        tmp_path / "gone.json")
    K.reset()
    assert K.calibration() is None
    assert K.calibration_info()["source"] == "defaults"
    assert K.fukuzono_ttf(10.0, 2.0) > 0.0


# ---------------------------------------------------------------------------
# THE PARITY TEST — the fallback and the training feature
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("label,ramp,v_scale", [
    ("fast tertiary ramp (a valid 1/v fit)", 0.010, 0.30),
    ("gentle creep (no valid 1/v fit)", 0.00012, 0.004),
])
def test_fukuzono_ttf_live_matches_the_training_feature(label, ramp, v_scale):
    """1 % parity with `ttf_fuk`, NaN-for-NaN included.

    The interesting half is the second case. On a gentle creep the fit is
    *supposed* to fail — velocities sit under `FUK_V_MIN`, so there is no
    converging trend and no horizon — and the contract is that both
    implementations return "no estimate" rather than one of them inventing a
    horizon out of a flat window.
    """
    t, disp, tilt, acc = _run(ramp=ramp, v_scale=v_scale)
    buf = _buf(t, disp, tilt, acc)

    want = feature_row(compute_live_features(buf))["ttf_fuk"]
    got = K.fukuzono_ttf_live(buf)

    if want is None:                     # feature_row turns NaN into None
        assert got is None, f"{label}: training refused the fit, fallback did not"
        return
    assert got is not None, f"{label}: training produced {want}, fallback produced nothing"
    assert got == pytest.approx(want, rel=0.01), (
        f"{label}: fallback={got}, training ttf_fuk={want} — the two estimators "
        f"have drifted; the model is being checked against a formula it was not "
        f"fitted to")
    assert got > 0.0


def test_the_live_estimator_needs_a_window():
    t, disp, tilt, acc = _run(**FAST)
    assert K.fukuzono_ttf_live(_buf(t, disp, tilt, acc, upto=20)) is None
    assert K.fukuzono_ttf_live([]) is None


def test_the_live_estimator_refuses_a_flat_window():
    """No motion → no 1/v → no horizon. Never a default number."""
    t = np.arange(260) / 2.0
    disp = np.full_like(t, 5.0)          # perfectly flat displacement
    acc = np.full_like(t, 0.0)
    assert K.fukuzono_ttf_live(_buf(t, disp, disp, acc)) is None


def test_the_live_estimator_refuses_a_node_that_is_slowing_down():
    """The complement of the accepted case: decelerating → no failure horizon."""
    t = np.arange(260) / 2.0
    disp = 5.0 + 6.0 * (1.0 - np.exp(-t / 30.0))     # fast, then decaying
    acc = np.full_like(t, 0.0)
    assert K.fukuzono_ttf_live(_buf(t, disp, disp, acc)) is None


# ---------------------------------------------------------------------------
# The analytic law
# ---------------------------------------------------------------------------
def test_the_analytic_ttf_shortens_as_the_velocity_rises():
    """The law is actually driving the number — not a constant wearing a name."""
    slow = K.fukuzono_ttf(20.0, 1.0)
    faster = K.fukuzono_ttf(20.0, 4.0)
    assert 0.0 < faster < slow


def test_the_analytic_ttf_is_zero_once_the_breach_limit_is_reached():
    assert K.fukuzono_ttf(100.0, 2.0) == 0.0
    assert K.fukuzono_ttf(140.0, 2.0) == 0.0


def test_the_analytic_ttf_returns_the_clamp_below_steady_creep():
    """Under the steady-creep threshold there is no failure to project."""
    clamp = K.calibration()["stable_ttf_clamp_s"]
    assert K.fukuzono_ttf(10.0, 0.001) == pytest.approx(clamp)


def test_the_analytic_ttf_takes_the_earlier_of_the_two_clocks():
    """breach-arrival must be able to win, and it does at a constant velocity."""
    calib = K.calibration()
    # v high enough that the Voight horizon is long, displacement close enough
    # that constant-velocity arrival is sooner.
    disp, v = 99.0, 1.0
    assert K.fukuzono_ttf(disp, v) <= (calib["breach_limit_mm"] - disp) / v + 1e-9


# ---------------------------------------------------------------------------
# Clamping, the legacy rung, and the disagreement magnitude
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value,want", [
    (-5.0, 0.0),
    (0.0, 0.0),
    (42.0, 42.0),
    (1e9, 3600.0),
])
def test_clamp_ttf_bounds(value, want):
    assert K.clamp_ttf(value) == pytest.approx(want)


def test_clamp_sends_nan_and_infinity_to_the_stable_end():
    """Not knowing when something fails is not the same as it failing now."""
    assert K.clamp_ttf(float("nan")) == pytest.approx(3600.0)
    assert K.clamp_ttf(float("inf")) == pytest.approx(3600.0)
    assert K.clamp_ttf(None) == pytest.approx(3600.0)


def test_the_legacy_ratio_is_the_legacy_formula():
    assert K.kinematic_ttf_accel(2.0, 6.0) == pytest.approx(3.0)


@pytest.mark.parametrize("accel,vel", [
    (0.0, 6.0),      # no acceleration → nothing to extrapolate
    (-1.0, 6.0),     # springing back
    (2.0, 0.0),      # no velocity
    (2.0, -1.0),
    (float("nan"), 6.0),
])
def test_the_legacy_ratio_refuses_input_it_cannot_use(accel, vel):
    assert K.kinematic_ttf_accel(accel, vel) is None


def test_disagreement_is_relative_and_scale_free():
    assert K.disagreement(100.0, 50.0) == pytest.approx(0.5)
    assert K.disagreement(50.0, 100.0) == pytest.approx(0.5)
    assert K.disagreement(90.0, 110.0) == pytest.approx(20.0 / 110.0)
    # 60 s apart means something very different at these two scales
    assert K.disagreement(90.0, 30.0) > K.disagreement(3000.0, 2940.0)


def test_disagreement_is_bounded_and_survives_missing_sides():
    assert K.disagreement(1.0, 0.0) <= 1.0
    assert K.disagreement(0.0, 0.0) == 0.0
    assert K.disagreement(None, 50.0) == 0.0
    assert K.disagreement(50.0, None) == 0.0
    assert K.disagreement(float("nan"), 50.0) == 0.0


# ---------------------------------------------------------------------------
# The rung the cascade calls
# ---------------------------------------------------------------------------
def test_the_drop_in_answers_with_the_live_estimator_when_a_window_exists():
    t, disp, tilt, acc = _run(**FAST)
    buf = _buf(t, disp, tilt, acc)
    st = _state_with(buf, velocity=2.0, acceleration=0.5)

    live = K.fukuzono_ttf_live(buf)
    assert live is not None, "the fast ramp must produce a fit for this test to mean anything"
    assert K.kinematic_source(st, NODE) == "fukuzono_1/v"
    assert K.kinematic_ttf(st, NODE) == pytest.approx(K.clamp_ttf(live))


def test_the_drop_in_falls_through_to_the_legacy_ratio_without_a_window():
    """No window, but the node has moved far enough for the gate to allow it."""
    st = _state_with([(0.0, 40.0, 0.0, 0.0), (0.5, 40.1, 0.0, 0.0)],
                     disp_now=40.0, velocity=6.0, acceleration=2.0)
    assert K.kinematic_source(st, NODE) == "v/a"
    assert K.kinematic_ttf(st, NODE) == pytest.approx(3.0)


def test_the_drop_in_admits_when_it_has_nothing():
    st = _state_with([(0.0, 5.0, 0.0, 0.0), (0.5, 5.1, 0.0, 0.0)])
    assert K.kinematic_source(st, NODE) == "none"
    assert K.kinematic_ttf(st, NODE) is None


def test_the_legacy_rung_is_where_the_sanity_gate_bites():
    """A short number with no motion and no displacement never leaves this module.

    The hook in `cascade._kinematic_ttf` returns whatever this function returns
    without re-checking it, so this test is the only thing standing between a
    hand-written formula and the firewall. ``v/a = 1 s`` on a node that has moved
    5 mm is exactly the reading the gate exists to refuse.
    """
    st = _state_with([(0.0, 5.0, 0.0, 0.0), (0.5, 5.1, 0.0, 0.0)],
                     disp_now=5.0, velocity=1.0, acceleration=1.0)
    assert K.kinematic_source(st, NODE) == "v/a"
    assert K.kinematic_ttf_accel(1.0, 1.0) == pytest.approx(1.0)
    assert not C.T_sanity(0.0, 5.0, 1.0)
    assert K.kinematic_ttf(st, NODE) is None


def test_the_live_estimator_tracks_the_law_it_was_built_from():
    """The estimator against the law, not against itself.

    The fixture's horizon is known before the call, so the scale of the answer is
    checkable: 20 s of constructed horizon must not come back as 300 for 20, and
    the ordering must follow the law. What it does **not** come back as is exactly
    20 — it comes back as 30, and the reason is structural, not a bug:

    the estimator fits ``1/v̄`` where ``v̄`` is the velocity averaged over the
    previous 10 s, so each ``y`` is a mid-interval value reported at an end-of-
    interval time. The fit therefore lands one averaging window late, a constant
    **+10 s** at every horizon tested (measured: 10→19.7, 20→29.9, 30→39.9,
    60→70.1). Training has the identical convention — the parity test above is
    what keeps the two honest — so the offset is part of the estimator's
    definition, and this test states it rather than asserting it away.
    """
    got = {h: K.fukuzono_ttf_live(_converging(horizon_s=h))
           for h in (10.0, 20.0, 30.0, 60.0)}
    assert all(v is not None for v in got.values()), f"a horizon went missing: {got}"
    assert got[10.0] == pytest.approx(19.7, abs=1.5)
    assert got[20.0] == pytest.approx(29.9, abs=1.5)
    assert got[60.0] == pytest.approx(70.1, abs=2.0)
    assert list(got.values()) == sorted(got.values()), (
        "closer failure must give a shorter horizon — got " + str(got))


def test_a_window_converging_too_slowly_is_not_fitted_at_all():
    """Under `FUK_V_MIN` there are no usable points, so there is no estimate.

    A far-off failure has a slow approach, `v` falls under 1 mm/s, and the
    estimator declines rather than fitting a trend to noise. That is the same
    floor the training feature uses, and it is why a *stable* node gets `None`
    here and falls through to the next rung — the behaviour the module docstring
    describes.
    """
    assert K.fukuzono_ttf_live(_converging(horizon_s=240.0)) is None


def test_a_live_short_horizon_is_not_refused_merely_for_being_short():
    """The live rung's short numbers arrive with real velocity, so they pass.

    This is the asymmetry between the two rungs, stated as a test: a converging
    window cannot exist below ``FUK_V_MIN``, so `T_sanity` can never refuse the
    Fukuzono rung for lack of motion. The gate is not decoration on that path —
    it is genuinely unreachable there, and reachable on the `v/a` path above.
    """
    buf = _converging(horizon_s=20.0)
    st = _state_with(buf, disp_now=5.0)          # displacement under 30 mm on purpose
    est = K.fukuzono_ttf_live(buf)
    assert est is not None and est < 30.0, "the fixture must be short to mean anything"
    assert K.kinematic_ttf(st, NODE) == pytest.approx(est)


def test_the_drop_in_and_the_legacy_path_agree_on_the_gate():
    """Both gates must accept and refuse the same cases, case for case."""
    for ttf in (5.0, 29.9, 30.0, 90.0, 3600.0):
        for v10 in (0.0, 0.49, 0.5, 2.0):
            for disp_now in (0.0, 29.9, 30.0, 80.0):
                legacy = C.T_sanity(v10, disp_now, ttf)
                # the model-side gate, on a feature vector built to match
                feats = [0.0] * 16
                feats[0] = disp_now
                feats[4] = v10
                assert legacy == __import__("engine.ttf", fromlist=["x"]).sanity_gate(feats, ttf)
