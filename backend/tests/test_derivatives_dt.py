"""
Phase 1 (B1) — measured Δt, the D5 regression guard.
====================================================

The defect this file exists for: ``calculate_derivatives`` hardcoded ``dt = 0.5``
for every sample, whatever the real arrival interval was. A node delivering
frames every 0.25 s therefore reported **half** its true velocity, and a node
delivering them every 1.0 s reported **double** — a 4x spread between two links
carrying identical data.

That is not a rounding error. Velocity and acceleration drive every threshold in
the product: ``ACC_WARNING``, ``ACC_CRITICAL``, ``VEL_WARNING`` and the kinematic
``v/a`` time-to-failure. An error that varies with the radio's throughput means
the alarm threshold depends on how busy the channel is.

The test is written the way the defect reads: **same displacement slope, different
sample intervals, same velocity.** With the old code these numbers differed by 4x.
"""

from __future__ import annotations

import pytest

from config import MODEL_DT
from contracts import NodeState
from ingestion.serial_reader import (
    DT_GAP_S, DT_MAX_S, DT_MIN_S, compute_derivatives, use_measured_accel,
)

SLOPE_MM_PER_S = 0.8          # a slow creep, well under the warning thresholds


def _run(intervals, slope=SLOPE_MM_PER_S):
    """Feed samples to one node at the given intervals. Return the final velocity.

    Position is a pure function of time (``slope * t``), so the correct answer is
    ``slope`` no matter what the intervals are. The derivative must not care how
    often it is asked.
    """
    node = NodeState(node_id="node_1", displacement_mm=0.0)
    t = 0.0
    prev_ms = None
    for dt in intervals:
        t += dt
        disp = slope * t
        ms = t * 1000.0
        step = None if prev_ms is None else (ms - prev_ms) / 1000.0
        prev_ms = ms
        compute_derivatives(node, disp, step)
    return node.velocity


def test_same_slope_at_different_intervals_gives_the_same_velocity():
    """The headline assertion of the phase.

    The old implementation returned ``slope * (0.5 / dt)`` for each rate, i.e.
    four different answers for one physical reality.
    """
    fast = _run([0.25] * 40)      # a 4 Hz link
    slow = _run([1.0] * 40)       # a 1 Hz link

    assert fast == pytest.approx(slow, rel=0.02), (
        f"velocity must not depend on the sample interval: "
        f"0.25 s → {fast:.5f} mm/s, 1.0 s → {slow:.5f} mm/s"
    )
    assert fast == pytest.approx(SLOPE_MM_PER_S, rel=0.05), (
        "and it must equal the true slope"
    )


def test_ema_reaches_the_true_slope_and_stays_there():
    """0.3/0.7 smoothing has a ~3-sample time constant; it must converge."""
    node = NodeState(node_id="node_1", displacement_mm=0.0)
    t = 0.0
    for _ in range(60):
        t += MODEL_DT
        compute_derivatives(node, SLOPE_MM_PER_S * t, MODEL_DT)
    assert node.velocity == pytest.approx(SLOPE_MM_PER_S, rel=0.01)


def test_the_ema_factors_are_not_retuned():
    """ACC_WARNING = 0.25 m/s² is calibrated against 0.3/0.7.

    Changing the smoothing silently moves every threshold in the product, so the
    constants are pinned here rather than left to a future refactor's judgement.
    """
    from ingestion import serial_reader as sr
    assert (sr.EMA_PREV, sr.EMA_NEW) == (0.3, 0.7)
    assert sr.EMA_PREV + sr.EMA_NEW == pytest.approx(1.0)


def test_a_duplicate_timestamp_is_skipped_not_clamped():
    """Two samples with the same clock would divide by zero.

    The old code could not express this because it never looked at a timestamp.
    """
    node = NodeState(node_id="node_1", displacement_mm=10.0, velocity=1.0)
    before = (node.velocity, node.acceleration)
    compute_derivatives(node, 12.0, 0.0)
    assert (node.velocity, node.acceleration) == before, "a zero interval is skipped"
    assert node.displacement_mm == 10.0, "and it does not move the position either"


def test_a_gap_is_skipped_and_counted():
    """A 4 s gap is a dropped link, not a 4 s interval.

    Clamping it to 2 s — which is what a naive ``min(dt, 2.0)`` would do — turns
    an outage into a velocity spike and then into a false alarm.
    """
    from ingestion.serial_reader import COUNTS
    node = NodeState(node_id="node_1", displacement_mm=10.0, velocity=0.5)
    skips = COUNTS["derivative_skips"]

    compute_derivatives(node, 40.0, 4.0)          # well past DT_MAX_S
    assert COUNTS["derivative_skips"] == skips + 1
    assert node.velocity == 0.5, "unchanged — no invented spike"
    assert node.displacement_mm == 10.0

    compute_derivatives(node, 40.0, 0.001)        # below DT_MIN_S
    assert COUNTS["derivative_skips"] == skips + 2


def test_the_clamp_bounds_are_the_documented_ones():
    assert (DT_MIN_S, DT_MAX_S) == (0.05, 2.0)
    assert DT_GAP_S == 1.0


def test_first_sample_after_start_produces_no_velocity():
    """``dt=None`` means "no previous sample" — the honest answer is no derivative.

    v4.4 computed one anyway, against the boot-time default of 5.0 mm, which is
    why every node showed a small phantom velocity for its first half second.
    """
    node = NodeState(node_id="node_1", displacement_mm=5.0)
    acc, vel = compute_derivatives(node, 5.1, None)
    assert (acc, vel) == (0.0, 0.0)
    assert node.displacement_mm == 5.1, "position still updates"


def test_measured_acceleration_is_used_directly(d9):
    """D9 — the MPU-6050's reading is a measurement, not a second derivative."""
    node = NodeState(node_id="node_2", displacement_mm=5.0)
    model_dt = MODEL_DT
    # Feed a constant 1.2 m/s² for long enough for the EMA to converge.
    for _ in range(40):
        use_measured_accel(node, 1.2, model_dt)
    assert node.acceleration == pytest.approx(1.2, rel=0.02)
    # And the position is NOT touched: the measured path must not double-count
    # motion by also integrating a differentiated position into it.
    assert node.displacement_mm == 5.0


@pytest.fixture
def d9():
    """Marker fixture — lets the test name carry the defect id it closes."""
    return "D9"
