"""
Phase 0 — the 16-feature window, and its parity with the notebook that trained
the model.
=================================================================================

The whole value of the shipped TTF model rests on one property: the features the
*server* computes at 1 Hz must be the features the *notebook* computed when it
built the training set. If they drift, the model is still confident and still
wrong, and nothing in the system notices.

So this file does not test "16 numbers came out". It runs
``ai/notebooks/01_data_extraction.py::build_node_features`` — the actual training
extractor, imported from the repository — over the same synthetic run, and
compares all 16 values against ``engine.features.compute_live_features``.

Known and measured boundary difference
--------------------------------------
Four features are computed over the 60 s window: ``disp_max60``, ``accel_mean60``,
``accel_std60``, ``accel_max60``. pandas' ``rolling("60s")`` is closed on the
*right* edge only — it excludes the sample exactly 60 s old — while the server
includes it via ``searchsorted(..., side="left")``. At 2 Hz that is one sample
out of 121, so the aggregates differ by ≲0.5 %, never more. The test asserts
*exact* equality for the twelve offset features and a 1 % tolerance for those
four, which is the honest statement of the contract.
"""

from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from config import FUK_V_MIN, MODEL_BUF
from engine.features import (
    FEATURES, FEATURE_INDEX, N_FEATURES, compute_live_features, feature_row,
    live_val_at, scaler_matches, window_complete,
)

BACKEND = Path(__file__).resolve().parents[1]
NOTEBOOK = BACKEND.parent / "ai" / "notebooks" / "01_data_extraction.py"
MODELS = BACKEND / "models"

#: Windowed aggregates — the four with the documented one-sample edge difference.
WINDOWED = ("disp_max60", "accel_mean60", "accel_std60", "accel_max60")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _run(n: int = 260, hz: float = 2.0, ramp: float = 0.00012, v_scale: float = 0.004):
    """A synthetic node run: gentle creep plus a quadratic term, at 2 Hz.

    ``v_scale``/``ramp`` control how fast displacement moves; the parity test for
    ``ttf_fuk`` needs velocities above ``FUK_V_MIN`` (1 mm/s) to make the 1/v fit
    valid, which is why the fast variant exists.
    """
    t = np.arange(n) / hz
    disp = 5.0 + v_scale * t + ramp * t ** 2
    tilt = 0.05 * t / 60.0
    acc = 0.01 + 0.0004 * t
    return t, disp, tilt, acc


def _buf(t, disp, tilt, acc, upto: int | None = None):
    stop = len(t) if upto is None else upto
    return [(float(t[i]), float(disp[i]), float(tilt[i]), float(acc[i])) for i in range(stop)]


def _notebook():
    """Import the training notebook as a module (it has no import side effects)."""
    if not NOTEBOOK.exists():                       # pragma: no cover
        pytest.skip(f"training notebook not present at {NOTEBOOK}")
    spec = importlib.util.spec_from_file_location("nb01_data_extraction", NOTEBOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# The feature list itself
# ---------------------------------------------------------------------------

def test_sixteen_features_in_canonical_order():
    assert N_FEATURES == 16
    assert FEATURES == (
        "disp_now", "d10", "d30", "d60",
        "v10", "v30", "v60", "disp_max60",
        "accel_now", "accel_mean60", "accel_std60", "accel_max60", "jerk",
        "tilt_now", "tilt_d30", "ttf_fuk",
    )
    assert FEATURE_INDEX["disp_now"] == 0 and FEATURE_INDEX["ttf_fuk"] == 15
    assert len(set(FEATURES)) == 16


def test_scaler_agrees_with_the_feature_list():
    """The shipped scaler.json is the artifact the model was trained against.

    This is the assertion that would have caught defect D3 directly: a 14-feature
    document, a 16-feature codebase and a scaler that matches exactly one of them.
    """
    import json
    scaler_path = MODELS / "scaler.json"
    if not scaler_path.exists():                    # pragma: no cover
        pytest.skip("scaler.json not present")
    scaler = json.loads(scaler_path.read_text())
    assert scaler_matches(scaler), (
        "scaler.json feature order disagrees with engine.features.FEATURES\n"
        f"  scaler : {scaler.get('features')}\n"
        f"  code   : {list(FEATURES)}"
    )
    assert len(scaler["mean"]) == 16 and len(scaler["scale"]) == 16
    assert all(s > 0 for s in scaler["scale"]), "a zero scale would divide by zero"


def test_scaler_mismatch_is_detected():
    assert not scaler_matches({"features": FEATURES[:-1]})
    assert not scaler_matches({"features": list(reversed(FEATURES))})
    assert not scaler_matches({})


# ---------------------------------------------------------------------------
# live_val_at — the offset rule
# ---------------------------------------------------------------------------

def test_live_val_at_picks_the_nearest_earlier_sample():
    ts = np.array([0.0, 0.5, 1.0, 1.5, 2.0])
    arr = np.array([10.0, 11.0, 12.0, 13.0, 14.0])
    assert live_val_at(ts, arr, 4, 1.0) == 12.0      # t=2.0 − 1.0 → sample at 1.0
    assert live_val_at(ts, arr, 4, 0.5) == 13.0
    assert math.isnan(live_val_at(ts, arr, 2, 60.0)), "no sample 60 s back → NaN"


def test_live_val_at_rejects_a_stale_sample():
    """A gap that exceeds the tolerance must produce NaN, not a wrong number."""
    ts = np.array([0.0, 100.0, 101.0])
    arr = np.array([1.0, 2.0, 3.0])
    assert math.isnan(live_val_at(ts, arr, 2, 10.0)), \
        "the nearest sample is 1 s back but 91 s of history is missing at the 10 s offset"


# ---------------------------------------------------------------------------
# Window completeness
# ---------------------------------------------------------------------------

def test_short_buffer_yields_nothing():
    t, disp, tilt, acc = _run(n=MODEL_BUF - 1)
    assert compute_live_features(_buf(t, disp, tilt, acc)) is None


def test_window_complete():
    t, disp, tilt, acc = _run()
    short = _buf(t, disp, tilt, acc, upto=MODEL_BUF - 1)
    full = _buf(t, disp, tilt, acc, upto=MODEL_BUF)
    assert not window_complete(short)
    assert window_complete(full)
    assert not window_complete(full, now=t[MODEL_BUF - 1] + 1.0), \
        "a discontinuity after the oldest sample invalidates the window (D13)"


def test_missing_60s_offset_rejects_the_window():
    """If the buffer's *recent* span is under a minute there is no d60 → None.

    Note this is about the tail, not the middle: the extractor only ever looks
    back 60 s from the newest sample, so a gap an hour ago is harmless, while a
    restart 30 s ago makes the window unusable.
    """
    t = np.concatenate([np.arange(0, 35, 0.5), np.arange(300, 330, 0.5)])   # 30 s tail
    disp = 5.0 + 0.01 * np.arange(len(t))
    buf = [(float(t[i]), float(disp[i]), 0.0, 0.01) for i in range(len(t))]
    assert len(buf) >= MODEL_BUF, "the buffer is long enough — it is the span that is short"
    assert compute_live_features(buf) is None


def test_features_are_finite_and_ordered():
    t, disp, tilt, acc = _run()
    feats = compute_live_features(_buf(t, disp, tilt, acc))
    assert feats is not None and len(feats) == 16
    assert all(isinstance(v, float) for v in feats)
    assert feats[FEATURE_INDEX["disp_now"]] == pytest.approx(float(disp[-1]))
    assert feats[FEATURE_INDEX["v10"]] == pytest.approx(feats[FEATURE_INDEX["d10"]] / 10.0)
    assert feats[FEATURE_INDEX["v60"]] == pytest.approx(feats[FEATURE_INDEX["d60"]] / 60.0)


def test_a_displacement_step_moves_velocity_not_tilt():
    """The channels are independent: a jump in displacement must not leak into
    tilt, which is what would happen if the buffers were ever crossed."""
    t, disp, _tilt, acc = _run()
    disp = disp.copy()
    disp[-1] += 5.0
    tilt = np.full_like(disp, 1.25)                  # constant, so tilt_d30 must be 0
    feats = compute_live_features(_buf(t, disp, tilt, acc))
    assert feats[FEATURE_INDEX["v10"]] > 0.4, "a 5 mm step in 10 s is 0.5 mm/s"
    assert abs(feats[FEATURE_INDEX["tilt_d30"]]) < 1e-9
    assert feats[FEATURE_INDEX["tilt_now"]] == pytest.approx(1.25)


def test_feature_row_is_named_and_nan_safe():
    row = feature_row([1.0] * 15 + [float("nan")])
    assert set(row) == set(FEATURES)
    assert row["ttf_fuk"] is None, "NaN must never leak into JSON as a bare NaN"


# ---------------------------------------------------------------------------
# THE PARITY TEST — server vs training notebook
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label,ramp,v_scale", [
    ("gentle creep (no valid 1/v fit)", 0.00012, 0.004),
    ("fast tertiary ramp (valid 1/v fit)", 0.010, 0.30),
])
def test_parity_with_the_training_notebook(label, ramp, v_scale):
    nb = _notebook()
    t, disp, tilt, acc = _run(ramp=ramp, v_scale=v_scale)

    df = pd.DataFrame(
        {"displacement_mm": disp, "tilt_x": tilt, "acceleration": acc},
        index=pd.to_datetime(1_700_000_000_000_000_000 + (t * 1e9).astype("int64")),
    )
    ref = nb.build_node_features(df)
    mine = compute_live_features(_buf(t, disp, tilt, acc))
    assert mine is not None

    exact, windowed, nan_nan = [], [], []
    for i, name in enumerate(FEATURES):
        want, got = float(ref[name].iloc[-1]), mine[i]
        if math.isnan(want) and math.isnan(got):
            nan_nan.append(name)
            continue
        assert not (math.isnan(want) ^ math.isnan(got)), \
            f"{label}: {name} — notebook={want}, server={got} (one is NaN)"
        if name in WINDOWED:
            windowed.append((name, want, got))
            assert abs(got - want) <= 0.01 * max(abs(want), 1e-9), \
                f"{label}: {name} differs by more than 1 % — notebook={want}, server={got}"
        else:
            exact.append(name)
            assert got == pytest.approx(want, rel=1e-9, abs=1e-12), \
                f"{label}: {name} — notebook={want}, server={got}"

    # The claim, stated plainly: 16 features accounted for, of which the 12
    # offset features are bit-exact and the 4 windowed aggregates sit within the
    # documented one-sample edge. ttf_fuk is either exact or NaN on both sides.
    assert len(windowed) == 4, f"{label}: expected 4 windowed features, got {windowed}"
    assert len(exact) + len(windowed) + len(nan_nan) == 16, (
        f"{label}: {len(exact)} exact + {len(windowed)} windowed + "
        f"{len(nan_nan)} NaN-nan != 16")
    assert len(exact) == 12 - len(nan_nan), (
        f"{label}: expected {12 - len(nan_nan)} exact matches, got {exact}")
    if ramp > 0.001:
        # A fast ramp makes v10 exceed FUK_V_MIN, so the 1/v fit is valid.
        assert not nan_nan, f"{label}: nothing should be NaN at this ramp ({nan_nan})"
        assert "ttf_fuk" in exact, f"{label}: the 1/v fit should be valid at this ramp"
        assert mine[FEATURE_INDEX["ttf_fuk"]] > 0, "a converging fit gives a positive TTF"
    else:
        assert nan_nan == ["ttf_fuk"], f"{label}: only ttf_fuk may be NaN, got {nan_nan}"


def test_ttf_fuk_is_nan_when_velocity_is_below_the_fit_floor():
    """Fukuzono needs v10 > FUK_V_MIN; below it, an extrapolation would be noise."""
    t, disp, tilt, acc = _run(v_scale=0.001)        # 1 mm/s needs 0.1 mm/s of creep
    feats = compute_live_features(_buf(t, disp, tilt, acc))
    assert feats[FEATURE_INDEX["v10"]] < FUK_V_MIN
    assert math.isnan(feats[FEATURE_INDEX["ttf_fuk"]])
