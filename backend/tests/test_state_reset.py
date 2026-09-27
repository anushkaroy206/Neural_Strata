"""
Phase 1 (B1) — Re-arm must clear the model window, the D13 regression guard.
===========================================================================

The defect: ``reset_system()`` set each node's ``displacement_mm`` back to 5.0 and
reset the velocity, but left ``node_buffers`` (the 121-sample model window)
untouched. The next forecast was therefore computed from 60 s of **pre-reset**
samples stitched to a handful of new ones.

It did not produce an obviously wrong number. It produced a *plausible* one,
which is worse: an operator who re-arms and immediately sees a forecast has no way
to know that the window behind it is a recording of the event they just cleared.
The runbook's "wait ~65 s after a Re-arm" was an attempt to paper over this in
procedure rather than in code.

Phase 1 makes it structural: ``mark_discontinuity()`` moves the window origin
forward, and ``window_complete()`` refuses to report a window until 60 s of
**new** samples have arrived.
"""

from __future__ import annotations

import pytest

import app as app_module
from config import ACC_WARNING, MODEL_BUF, MODEL_DT
from engine.features import window_complete
from state import GAP_INVALIDATES_S, SystemState


@pytest.fixture()
def state():
    return SystemState()


def _fill(state, n_id, *, t0=0.0, n=None, start_disp=5.0):
    """Fill a node's window at the training cadence. Returns the last sample t."""
    n = n or MODEL_BUF
    t = t0
    for i in range(n):
        t = t0 + i * MODEL_DT
        state.append_model_buffer(n_id, t, start_disp + i * 0.1, 0.0, 0.0)
    return t


# ---------------------------------------------------------------------------
# window_complete — the primitive
# ---------------------------------------------------------------------------

def test_a_full_window_is_complete(state):
    _fill(state, "node_1")
    assert state.window_complete("node_1") is True
    assert len(state.buffers["node_1"]) == MODEL_BUF


def test_a_partial_window_is_not_complete(state):
    _fill(state, "node_1", n=MODEL_BUF - 1)
    assert state.window_complete("node_1") is False


def test_the_engine_sees_the_same_answer(state):
    """``engine.features.window_complete`` and the state must agree.

    The state owns the buffer; the engine owns the 60 s rule. If they disagreed,
    the model would warm up on a window the state had already invalidated.
    """
    _fill(state, "node_2", n=MODEL_BUF)
    engine_says = window_complete(state.buffers["node_2"], state._window_origin["node_2"])
    assert engine_says == state.window_complete("node_2") is True


# ---------------------------------------------------------------------------
# The reset — the actual defect
# ---------------------------------------------------------------------------

def test_reset_clears_every_buffer(state):
    """The one-line version of D13."""
    for n in state.node_ids:
        _fill(state, n)
        assert state.window_complete(n) is True

    state.reset()

    for n in state.node_ids:
        assert len(state.buffers[n]) == 0, f"{n} kept pre-reset samples"
        assert state.window_complete(n) is False


def test_no_forecast_until_sixty_seconds_of_new_samples(state):
    """The warm-up is now enforced, not advised.

    This is the acceptance criterion the phase is judged on: after a Re-arm the
    model must have nothing to say until it has a full window of samples that are
    all *newer* than the reset.
    """
    _fill(state, "node_1")
    state.reset()                                   # t = 0 for the new window

    # One sample short of the full window.
    for i in range(MODEL_BUF - 1):
        state.append_model_buffer("node_1", i * MODEL_DT, 5.0, 0.0, 0.0)
    assert state.window_complete("node_1") is False, "one sample short is not a window"

    # The sample that lands at t = 60.0 s completes it.
    state.append_model_buffer("node_1", (MODEL_BUF - 1) * MODEL_DT, 5.0, 0.0, 0.0)
    assert state.window_complete("node_1") is True, "60 s of new samples"


def test_pre_reset_samples_are_refused_outright(state):
    """A sample stamped before the reset must never re-enter the window.

    Without this, a replay that runs *backwards* through a discontinuity — or a
    node whose clock was not reset with the state — could refill the window with
    the very samples the operator just cleared.
    """
    _fill(state, "node_1", t0=0.0)
    state.reset()
    state._window_origin["node_1"] = 100.0           # the reset happened at t=100

    assert state.append_model_buffer("node_1", 50.0, 9.0, 0.0, 0.0) is False
    assert len(state.buffers["node_1"]) == 0
    assert state.append_model_buffer("node_1", 150.0, 9.0, 0.0, 0.0) is True


def test_reset_writes_a_visible_event(state):
    """The operator must be able to see that a warm-up is in progress."""
    state.reset()
    assert any("re-armed" in e["text"].lower() for e in state.events)


def test_mark_discontinuity_is_idempotent_and_counted(state):
    _fill(state, "node_3", n=MODEL_BUF)
    before = state.discontinuities
    state.mark_discontinuity("node_3", "gap")
    state.mark_discontinuity("node_3", "gap")
    assert state.discontinuities == before + 2
    assert state.window_complete("node_3") is False


# ---------------------------------------------------------------------------
# Through the API — the path the operator actually takes
# ---------------------------------------------------------------------------

def test_post_reset_through_the_api_invalidates_every_window():
    """The end-to-end version: the dashboard's Re-arm button.

    Asserts on the *live* app state, because that is what the button reaches.
    """
    app_module.app.config.update(TESTING=True)
    for n in app_module.STATE.node_ids:
        _fill(app_module.STATE, n)

    client = app_module.app.test_client()

    # The warm-up is visible before the reset…
    ext = client.get("/api/extended").get_json()
    assert all(v["window_complete"] for v in ext["nodes"].values())

    resp = client.post("/api/reset")
    assert resp.status_code == 200
    assert resp.get_json()["warming_up_s"] == 60

    ext = client.get("/api/extended").get_json()
    assert not any(v["window_complete"] for v in ext["nodes"].values()), (
        "after Re-arm the deck must report a warm-up, not a fresh forecast"
    )

    tele = client.get("/api/telemetry").get_json()
    assert tele["system_status"] == "NOMINAL"
    for n_id, node in tele["nodes"].items():
        assert node["displacement_mm"] == 5.0, f"{n_id} not zeroed"
        assert node["acceleration"] == 0.0
    # `velocity` is server-internal and stripped from the public payload, so the
    # reset is asserted on the state and on /api/extended, where it is published.
    assert all(app_module.STATE.nodes[n].velocity == 0.0
               for n in app_module.STATE.node_ids)
    ext = client.get("/api/extended").get_json()
    assert all(v["velocity"] == 0.0 for v in ext["nodes"].values())


def test_the_gap_threshold_is_five_seconds():
    """The number the discontinuity rule quotes; pinned so it cannot drift."""
    assert GAP_INVALIDATES_S == 5.0


def test_a_real_frame_takes_over_a_boot_time_local_window(state):
    """The window clock is not first-come-first-served.

    The defect: at cold boot the ticker reaches every node within its first 0.5 s
    tick (no node has been heard from, so every node looks stale and the bench
    case needs a window from somewhere). That fill is a wall-clock guess. The old
    rule then refused every *sample*-clock frame for the life of the process, so
    ``/api/ai`` could never go ACTIVE from real data — on the rig, ESP-NOW frames
    would never reach the model at all, and the window behind a confident number
    would be wall-clock snapshots rather than the training cadence.

    A guess must be revocable; a measurement must not. This is that rule.
    """
    for i in range(10):                                   # the boot-time guess
        state.append_model_buffer("node_1", i * MODEL_DT, 5.0, 0.0, 0.0, clock="local")
    assert len(state.buffers["node_1"]) == 10

    # The first real frame discards the guess rather than refusing to be mixed in.
    assert state.append_model_buffer("node_1", 0.0, 5.0, 0.0, 0.0) is True
    assert len(state.buffers["node_1"]) == 1, "the local samples must be discarded, not mixed"
    assert state._window_clock["node_1"] == "sample"
    assert state.window_clock_takeovers == 1

    # And the reverse never happens: a tick cannot displace real samples.
    assert state.append_model_buffer("node_1", 0.5, 5.0, 0.0, 0.0, clock="local") is False
    assert len(state.buffers["node_1"]) == 1
    assert state.clock_mismatches == 1, "refusing the local tick is still the rule"


def test_a_taken_over_window_still_fills_to_completion(state):
    """Taking over must leave a window that can actually warm up.

    Clearing the buffer is only correct if the restart clears the *origin* too —
    otherwise the origin the local fill left behind refuses the new samples and
    the window never completes, which would be a worse bug than the one fixed.
    """
    for i in range(10):
        state.append_model_buffer("node_1", i * MODEL_DT, 5.0, 0.0, 0.0, clock="local")
    assert state.append_model_buffer("node_1", 0.0, 5.0, 0.0, 0.0) is True

    _fill(state, "node_1", t0=MODEL_DT, n=MODEL_BUF - 1)
    assert len(state.buffers["node_1"]) == MODEL_BUF
    assert state.window_complete("node_1") is True


def test_rearm_grace_ignores_sensor_triggers_for_thirty_seconds(state):
    """Prototype hold: a Re-arm stays NOMINAL until the grace clock runs out."""
    hot = ACC_WARNING + 0.5
    state.nodes["node_1"].acceleration = hot
    state.reset("operator re-arm", grace_s=30.0)
    state.nodes["node_1"].acceleration = hot
    state.update_status_logic()
    assert state.system_status == "NOMINAL"
    assert state.nodes["node_1"].status == "NOMINAL"
    assert state.levels["1"]["route"] == "OPEN"
    assert 0 < state.rearm_grace_remaining() <= 30

    state.rearm_grace_until = 0.0
    state.update_status_logic()
    assert state.nodes["node_1"].status == "WARNING"
    assert state.system_status == "WARNING"


def test_a_critical_node_returns_to_nominal_when_the_reading_falls(state):
    """A spike must not leave the node critical after the accelerometer is quiet."""
    state.nodes["node_4"].status = "CRITICAL"
    state.nodes["node_4"].acceleration = -1.5
    state.system_status = "CRITICAL"
    state.update_status_logic()
    assert state.nodes["node_4"].status == "NOMINAL"
    assert state.system_status == "NOMINAL"
