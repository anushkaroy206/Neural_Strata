"""
Phase 3 (B2) — the two-stage crack engine.
==========================================

§3.7's acceptance table, plus everything that table implies and the two decisions
this phase had to make. Six groups:

**1 · The acceptance table.** ``test_crack_settle`` · ``test_crack_hysteresis`` ·
``test_crack_rail`` · ``test_coherence_truck_vs_crack`` — written to the rules in
§3.2, which is not always the same thing as §3.7's prose (see below).

**2 · The unit decision.** ``micro`` is crack-mouth opening in **µm**, not
micro-strain in µε, because the build has a load cell and not a foil gauge. The
tests pin the unit in the places a reader would otherwise have to trust: the
threshold's provenance, the plausibility window, the contract comments and the
firmware's calibration constant.

**3 · The zero.** Who supplies it, when it is refused, and the rule that no crack
event may fire while the channel is unsettled. Plus the trap that makes this
matter: a server-side zero layered on top of the node's own zero subtracts the
same offset twice.

**4 · Fault ≠ zero.** A railed cell, an open lead and a slack spring all have to
read as *fault*, never as a calm seam. This is §3.1's sentence and the reason
``FLAG_MICRO_UNLOADED`` exists at all.

**5 · The two-stage story.** The same opening ramp, read by two instruments whose
thresholds are ~1000× apart, produces ``Micro-crack initiated`` **before**
``Crack propagating rapidly``. If that ordering ever inverts, the phase's entire
demo claim is false.

**6 · Additivity.** Nothing here may remove a key, a route or a behaviour that
existed before Phase 3 — the crack column is additive by construction, and the
frozen fixtures are the proof.

**Two rows of §3.7 that could not be implemented as written** (both recorded in
the phase report rather than silently "fixed"):

* ``test_crack_hysteresis`` says *"a decay to 69 % does not clear, to 71 % does"*.
  §3.2.3 says a crossing *"must fall to 70 % of the threshold to clear it"* — so
  69 % clears and 71 % does not. The two sentences are mirror images; the rule
  wins, and ``test_crack_hysteresis_clear_line`` asserts **1 % either side of the
  clear line** so the test still does what the row intended.
* §3.4 says ``update_micro_baseline`` *"returns 0.0 until settled"* while §3.2.1
  says the channel *"reports null"* until settled. ``None`` is returned, because
  ``0.0`` means *subtract nothing* and would publish raw counts as opening.

Time is always passed in explicitly, so nothing here sleeps.
"""

from __future__ import annotations

import math

import pytest

import config
import contracts
from engine import crack as C
from ingestion import serial_reader, simulator
from state import SystemState

T0 = 1_000_000.0

N2 = "node_2"
N4 = "node_4"
N1 = "node_1"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def make_state(started_at: float = T0 - 600.0) -> SystemState:
    state = SystemState(started_at=started_at)
    return state


def ramp(state, rate_mm_s: float, seconds: float, t0: float = 0.0,
         start_after_s: float = 0.0, node_id: str = N2, flags: int = 0,
         vib: int = 0, dt: float = 0.5):
    """Drive the crack column of one node along a linear opening ramp.

    Yields ``(t, events)`` per sample. The node declares its own baseline settled
    (``flags`` without ``FLAG_BASELINE_UNSETTLED``), which is the real hardware's
    behaviour after its 15 s boot window (H2.4).
    """
    node = state.nodes[node_id]
    t = t0
    while t < seconds:
        opening = max(0.0, (t - start_after_s) * rate_mm_s)
        node.micro = opening * 1000.0
        node.macro = round(opening, 3)
        node.displacement_mm = 5.0 + opening
        node.vib = vib
        events = C.update_node_crack(state, node_id, node, t, flags=flags)
        for e in events:                     # the operator log, as the app does it
            e.publish(state)
        yield t, events
        t += dt


def collect_events(state) -> list[str]:
    return [f"{e['level']}:{e['text']}" for e in state.events]


def steady(state, seconds: float, value_um=0.0, macro=0.0, node_id: str = N2,
           flags: int = 0, t0: float = 0.0, dt: float = 0.5):
    """Hold the channel flat for ``seconds``. Returns the final timestamp."""
    node = state.nodes[node_id]
    t = t0
    while t < seconds:
        node.micro = value_um
        node.macro = macro
        node.displacement_mm = 5.0 + macro
        node.vib = 0
        C.update_node_crack(state, node_id, node, t, flags=flags)
        t += dt
    return t


# ===========================================================================
# 1 · §3.7 — the acceptance table
# ===========================================================================

def test_crack_settle_reports_null_until_the_baseline_lands():
    """§3.7 · with ``flags & 4`` set, ``micro`` reads ``null`` and no event fires.

    The channel reports *null*, never a raw count dressed as opening: before the
    zero lands, every number the instrument produces is relative to an unknown
    offset, and publishing it would show a crack that does not exist.
    """
    state = make_state()
    node = state.nodes[N2]
    unsettled = contracts.FLAG_BASELINE_UNSETTLED
    events = []
    t = 0.0
    while t < config.MICRO_SETTLE_S:                  # strictly inside the window
        node.micro = 388.0                            # a big, constant raw offset
        node.macro = 0.0
        events += C.update_node_crack(state, N2, node, t, flags=unsettled)
        assert node.micro is None, "unsettled channel published a number"
        assert node.crack_stage == C.STAGE_UNKNOWN
        t += 0.5
    assert events == [], "an event fired while the baseline was unsettled"
    assert C.crack_state(state, N2).settled is False

    # The window closes at exactly MICRO_SETTLE_S seconds of samples — 30 samples
    # at the 2 Hz cadence, which is the point of the rule rather than a
    # coincidence. With the node's flag still set (an older firmware, or a channel
    # the node cannot zero itself), the server takes the zero.
    node.micro = 388.0
    C.update_node_crack(state, N2, node, t, flags=unsettled)
    cs = C.crack_state(state, N2)
    assert cs.settled is True
    assert cs.baseline_source == "server"
    assert cs.baseline_um == pytest.approx(388.0)
    assert node.micro == pytest.approx(0.0)           # raw − baseline
    assert node.micro_settled is True


def test_crack_settle_takes_the_nodes_zero_when_the_node_says_it_is_done():
    """H2.4 — the node owns its own zero; the server must not average a second.

    Two zeros stacked is not a harmless double-check: averaging the *already
    subtracted* signal subtracts the same offset twice, and a 400 µm reading
    becomes 200 µm.
    """
    state = make_state()
    node = state.nodes[N2]
    node.micro = 412.5                                # relative to the node's zero
    node.macro = 0.4
    C.update_node_crack(state, N2, node, 0.0, flags=0)
    cs = C.crack_state(state, N2)
    assert cs.settled is True                         # immediately: nothing to wait for
    assert cs.baseline_source == "node"
    assert cs.baseline_um == 0.0
    assert node.micro == pytest.approx(412.5)         # untouched, as it should be


def test_crack_hysteresis_needs_three_samples_to_raise():
    """§3.2.3 · one sample above threshold fires nothing; three fire once."""
    state = make_state()
    node = state.nodes[N2]
    steady(state, 20.0)                               # quiet, settled, stage NONE

    # A single sample at twice the threshold: the rate window needs two ends, so
    # build the spike from one sample and then hold at zero again.
    node.micro = 60.0
    assert C.update_node_crack(state, N2, node, 20.0, flags=0) == []
    assert C.crack_state(state, N2).micro_armed is False

    # Now a real opening: the rate stays above the threshold for three
    # consecutive samples. The first of the four establishes a rate at all (a rate
    # needs two ends of a window), and the third one above threshold raises.
    fired = []
    for i, t in enumerate((20.5, 21.0, 21.5, 22.0)):
        node.micro = 60.0 + 90.0 * (i + 1)            # 180 µm/s, way above 1.5
        fired += C.update_node_crack(state, N2, node, t, flags=0)
    assert [e.event_type for e in fired] == [C.EV_MICRO_INITIATION]
    assert C.crack_state(state, N2).micro_armed is True

    # And it does not fire again while the latch holds.
    again = []
    for i, t in enumerate((22.0, 22.5, 23.0)):
        node.micro = 500.0 + 90.0 * i
        again += C.update_node_crack(state, N2, node, t, flags=0)
    assert again == []
    assert C.crack_state(state, N2).counts["micro_initiation"] == 1


def test_crack_hysteresis_clear_line():
    """§3.2.3, **not** §3.7's prose: 70 % of the threshold clears, 71 % does not.

    Built as physics rather than as arithmetic on a stored sample: the seam opens
    fast enough to arm the latch, then slows to a constant rate either side of the
    70 % clear line. The rate is a 5 s delta, so the slow regime has to fill a whole
    window before the number means anything — which is also why a *real* arrest is
    never instant.
    """
    clear_line = config.MICRO_RATE_THRESHOLD * config.CRACK_CLEAR_FRACTION
    assert clear_line == pytest.approx(1.05)

    for fraction, should_clear in ((0.69, True), (0.71, False)):
        state = make_state()
        node = state.nodes[N2]
        steady(state, 10.0)
        target = config.MICRO_RATE_THRESHOLD * fraction
        fast = config.MICRO_RATE_THRESHOLD * 40.0     # 60 µm/s
        t, value = 10.0, 0.0
        while t < 18.0:                               # 8 s fast — the latch arms
            value += fast * 0.5
            node.micro = value
            C.update_node_crack(state, N2, node, t, flags=0)
            t += 0.5
        assert C.crack_state(state, N2).micro_armed is True, "the rise never armed"
        while t < 26.0:                               # 8 s at the target rate
            value += target * 0.5
            node.micro = value
            C.update_node_crack(state, N2, node, t, flags=0)
            t += 0.5
        cs = C.crack_state(state, N2)
        assert cs.rate_um_s == pytest.approx(target, rel=0.05)
        assert cs.micro_armed is (not should_clear), (
            f"rate at {fraction:.0%} of the threshold ({cs.rate_um_s:.3f} µm/s, "
            f"clear line {clear_line}): armed={cs.micro_armed}")


def test_crack_rail_raises_a_fault_never_an_initiation():
    """§3.7 · a railed HX711 raises ``SENSOR_FAULT``, never ``CRACK_MICRO_INITIATION``.

    The rail is a *number*, not a null: 90 000 µm at 2 Hz looks like the fastest
    crack in history to anything that only reads the value.
    """
    state = make_state()
    node = state.nodes[N2]
    steady(state, 30.0, value_um=0.0)                 # settled and quiet

    saturated = contracts.FLAG_MICRO_SATURATED
    events = []
    for i in range(10):
        node.micro = 90_000.0
        events += C.update_node_crack(state, N2, node, 30.0 + 0.5 * i, flags=saturated)

    types = [e.event_type for e in events]
    assert C.EV_SENSOR_FAULT in types
    assert C.EV_MICRO_INITIATION not in types
    assert types.count(C.EV_SENSOR_FAULT) == 1, "the fault must be edge-triggered"
    fault = events[0]
    assert fault.kind == C.KIND_INFRASTRUCTURE
    assert "FAULT" in fault.text
    cs = C.crack_state(state, N2)
    assert cs.fault_reason == "micro_saturated"
    assert cs.stage == C.STAGE_UNKNOWN
    assert cs.rate_um_s is None, "a railed channel must not publish a rate"


def test_crack_slack_spring_is_a_fault_not_a_closing_seam():
    """The tension rig's own trap — ``FLAG_MICRO_UNLOADED``.

    With a spring across the gap, a slack spring reads a constant, calm number no
    matter what the seam does next. The channel is not measuring, and the only
    thing that can say so is the flag.
    """
    state = make_state()
    node = state.nodes[N2]
    steady(state, 30.0)
    unloaded = contracts.FLAG_MICRO_UNLOADED
    events = []
    for i in range(6):
        node.micro = -400.0                           # pre-tension released
        events += C.update_node_crack(state, N2, node, 30.0 + 0.5 * i, flags=unloaded)
    assert [e.event_type for e in events] == [C.EV_SENSOR_FAULT]
    assert C.crack_state(state, N2).fault_reason == "micro_unloaded"
    assert C.crack_state(state, N2).stage == C.STAGE_UNKNOWN


def test_a_fault_that_changes_kind_is_announced_again():
    """Railed → unloaded is a different sentence, so it gets its own line.

    Both are faults, and suppressing the second would leave the operator acting
    on the first one's advice: "open gauge or broken lead" sends someone to the
    bridge, "spring slack" sends them to the spring.
    """
    state = make_state()
    node = state.nodes[N2]
    steady(state, 20.0)
    node.micro = 90_000.0
    first = C.update_node_crack(state, N2, node, 20.0, flags=contracts.FLAG_MICRO_SATURATED)
    assert [e.event_type for e in first] == [C.EV_SENSOR_FAULT]
    assert "open gauge" in first[0].text

    second = []
    for i in range(1, 5):
        node.micro = -400.0
        second += C.update_node_crack(state, N2, node, 20.0 + 0.5 * i,
                                      flags=contracts.FLAG_MICRO_UNLOADED)
    assert [e.event_type for e in second] == [C.EV_SENSOR_FAULT]
    assert "spring slack" in second[0].text
    assert "unloaded" in second[0].text
    assert second[0].kind == C.KIND_INFRASTRUCTURE
    assert C.crack_state(state, N2).counts["faults"] == 2

    # ... and a *repeated* fault is still suppressed
    again = []
    for i in range(5, 9):
        node.micro = -400.0
        again += C.update_node_crack(state, N2, node, 20.0 + 0.5 * i,
                                     flags=contracts.FLAG_MICRO_UNLOADED)
    assert again == []


def test_crack_fault_recovery_is_announced_once():
    """A recovered channel is recovered, and says so — once."""
    state = make_state()
    node = state.nodes[N2]
    saturated = contracts.FLAG_MICRO_SATURATED
    steady(state, 20.0)
    node.micro = 90_000.0
    C.update_node_crack(state, N2, node, 20.0, flags=saturated)
    recovered = []
    for i in range(1, 5):
        node.micro = 0.0
        recovered += C.update_node_crack(state, N2, node, 20.0 + 0.5 * i, flags=0)
    assert [e.event_type for e in recovered] == [C.EV_SENSOR_FAULT]
    assert recovered[0].level == "NOMINAL"
    assert C.crack_state(state, N2).fault_reason is None
    assert C.crack_state(state, N2).counts["recoveries"] == 1


def test_coherence_truck_vs_crack():
    """§3.7 · a machinery pass gives coherence < 0.3; a structural onset > 0.45.

    The two rows are the plan's own §3.6 table, and the numbers are the table's:
    a truck has almost no structural evidence and a lot of vibration; an onset has
    strain *and* the small vibration that comes with a rock beginning to move
    (which is what makes the plan's row come out at 0.50 rather than 0.00).

    This is the whole Dual-Model argument in one scalar — the false alarms the
    ministry complains about are exactly the low-coherence cases.
    """
    # -- truck: 46 counted impacts, no strain at all -------------------------
    state = make_state()
    node = state.nodes[N2]
    steady(state, 10.0)
    truck = []
    for i in range(30):
        node.micro = 2.0 * math.sin(i * 1.7)          # ±2 µm of shake, no opening
        node.macro = 0.0
        node.displacement_mm = 5.0
        node.vib = 46
        C.update_node_crack(state, N2, node, 10.0 + 0.5 * i, flags=0)
    truck.append(node.cross_sensor_coherence)
    assert truck[0] < 0.30, f"a truck passed as structural (coherence {truck[0]})"

    # -- onset: the plan's own row — structural 0.80, nonspecific 0.30 --------
    # 1.2 µm/s is 0.8 × MICRO_RATE_THRESHOLD (its table value); 15 counted impacts
    # is 0.3 × VIB_REFERENCE. That row is what §3.7's 0.45 threshold was read off,
    # so it is reproduced rather than approximated.
    state2 = make_state()
    node2 = state2.nodes[N2]
    steady(state2, 10.0)
    for i in range(40):
        t = 10.0 + 0.5 * i
        node2.micro = config.MICRO_RATE_THRESHOLD * 0.8 * (t - 5.0)
        node2.macro = 0.0
        node2.displacement_mm = 5.0
        node2.vib = 15
        C.update_node_crack(state2, N2, node2, t, flags=0)
    assert node2.cross_sensor_coherence == pytest.approx(0.50, abs=0.03)
    assert node2.cross_sensor_coherence > 0.45

    # -- and the documented §3.6 defect, pinned so Phase 5 cannot miss it -----
    # A *stronger* structural signal scores LOWER: 1 µm/s is 0.67 × threshold, so
    # structural saturates at 1.0 while nonspecific stays 0.3, and coherence falls
    # to 0.30. A vibration-free onset falls to 0.00 — the same score as a truck.
    # That is the flaw recorded in the module docstring, and this assertion is
    # here so a future repair has to notice it.
    state3 = make_state()
    node3 = state3.nodes[N2]
    steady(state3, 10.0)
    for i in range(40):
        t = 10.0 + 0.5 * i
        node3.micro = config.MICRO_RATE_THRESHOLD * 2.0 * (t - 5.0)
        node3.macro = 0.0
        node3.displacement_mm = 5.0
        node3.vib = 15
        C.update_node_crack(state3, N2, node3, t, flags=0)
    assert node3.cross_sensor_coherence < 0.45


def test_coherence_table_rows_reproduce_the_plan():
    """All six rows of §3.6's table, from the formula as written.

    The table is the specification of this scalar; a test that only checked the
    two endpoints would let a rewrite quietly change the middle.
    """
    rows = [
        # structural, nonspecific, expected coherence
        (0.00, 0.05, 0.95),      # quiet
        (0.02, 0.90, 0.12),      # truck / machinery pass
        (0.15, 1.00, 0.15),      # blast nearby
        (0.80, 0.30, 0.50),      # micro-crack initiating
        (1.00, 0.85, 0.85),      # tertiary collapse
        (0.10, 0.45, 0.65),      # rain / seepage
    ]
    for structural, nonspecific, expected in rows:
        got = C.clamp(1.0 - abs(structural - nonspecific), 0.0, 1.0)
        assert got == pytest.approx(expected, abs=0.005), (structural, nonspecific)


def test_coherence_is_none_when_the_structural_evidence_is_unknown():
    """Unknown in, unknown out — and the reason matters.

    With ``structural = 0`` the formula returns ``1 − nonspecific``, so **the more
    the ground shakes the more coherent it looks** on a node that has no strain
    channel. A node with no crack column must answer ``None``.
    """
    state = make_state()
    assert C.cross_sensor_coherence(state, N1, state.nodes[N1]) is None
    # ... and a crack node that is unsettled has no structural evidence either
    node = state.nodes[N2]
    node.micro = None
    node.macro = None
    C.update_node_crack(state, N2, node, 0.0, flags=contracts.FLAG_BASELINE_UNSETTLED)
    assert node.cross_sensor_coherence is None


# ===========================================================================
# 2 · The unit decision — µm of crack-mouth opening, not µε
# ===========================================================================

def test_micro_is_um_and_the_threshold_keeps_the_plans_physics():
    """The threshold number changed because the unit did, and only because of it.

    §3.1's starting value was ``>15 µε/s``. Strain × gauge length = elongation:
    at the plan's own 100 mm crack-mouth gauge, 15 µε/s is 1.5 µm/s. Same physics,
    measurable with a shim.
    """
    assert config.MICRO_RATE_THRESHOLD == pytest.approx(1.5)
    assert 15e-6 * 100.0 * 1000.0 == pytest.approx(1.5)     # µε/s → µm/s at 100 mm


def test_micro_plausibility_window_straddles_zero_and_catches_unit_errors():
    """The frame contract's job: catch a µε number read as µm (~1000×), not argue
    with a real opening. A rejected frame costs a sample; a flagged one costs
    nothing."""
    lo, hi = config.MICRO_PLAUSIBLE_UM
    assert lo < 0 < hi
    assert hi >= 100_000.0, "100 mm of opening must never be rejected"
    ok, why = contracts.validate_node_frame(
        {"v": 2, "node": 2, "ms": 1, "disp": 5, "tilt": 0, "acc": 0, "micro": 412_500.0})
    assert ok is False and "micro" in why
    ok, _ = contracts.validate_node_frame(
        {"v": 2, "node": 2, "ms": 1, "disp": 5, "tilt": 0, "acc": 0, "micro": 412.5})
    assert ok is True, "412.5 µm is an ordinary micro-crack reading"


def test_gap_open_and_slack_windows_are_in_the_contract():
    """``micro`` is documented as µm (CMOD) and the bit-7 flag is declared."""
    assert contracts.FLAG_MICRO_UNLOADED == 1 << 7
    assert contracts.flags_to_list(contracts.FLAG_MICRO_UNLOADED) == ["micro_unloaded"]
    assert contracts.flags_to_list(0xFF) == [
        "micro_saturated", "macro_at_rail", "baseline_unsettled", "low_battery",
        "last_gasp", "synthetic", "heartbeat", "micro_unloaded"]


def test_macro_opening_is_a_two_point_calibration_and_never_negative():
    """§3.4 / §3.8 — the pot's ruler, and backlash must never subtract."""
    assert C.macro_opening(0) == pytest.approx(0.0)
    assert C.macro_opening(4095) == pytest.approx(25.0)
    assert C.macro_opening(2047.5) == pytest.approx(12.5, abs=0.01)
    assert C.macro_opening(-400) == 0.0                # below the low endpoint
    assert C.macro_opening(5000) == pytest.approx(25.0 + 905 / 4095 * 25, abs=0.01)
    # a degenerate calibration must not divide by zero
    assert C.macro_opening(100, adc_lo=100, adc_hi=100) == 0.0
    # the backend's calibration is the firmware's calibration
    assert config.MACRO_ADC_LO == 0.0 and config.MACRO_MM_HI == 25.0


# ===========================================================================
# 3 · The zero
# ===========================================================================

def test_update_micro_baseline_returns_none_not_zero_while_unsettled():
    """§3.2.1 beats §3.4's wording, with a reason: ``0.0`` means *subtract
    nothing*, and that publishes raw counts as opening."""
    state = make_state()
    assert C.update_micro_baseline(state, N2, 388.0, 0.0) is None
    assert C.update_micro_baseline(state, N2, 388.0, 1.0) is None
    assert C.crack_state(state, N2).settled is False


def test_update_micro_baseline_is_idempotent():
    """Once the zero lands it never moves — a re-averaging baseline is a
    high-pass filter, and it would filter away the creep the channel exists for."""
    state = make_state()
    t = 0.0
    while t <= config.MICRO_SETTLE_S:
        C.update_micro_baseline(state, N2, 100.0, t)
        t += 0.5
    first = C.crack_state(state, N2).baseline_um
    assert first == pytest.approx(100.0)
    for i in range(20):                                # a big excursion afterwards
        assert C.update_micro_baseline(state, N2, 900.0, t + i) == pytest.approx(first)
    assert C.crack_state(state, N2).settle_n == int(config.MICRO_SETTLE_S / 0.5) + 1


def test_baseline_settle_clock_does_not_start_without_a_value():
    """A channel with no reading must not be declared settled on three samples."""
    state = make_state()
    for i in range(60):
        C.update_micro_baseline(state, N2, None, i * 0.5)
    assert C.crack_state(state, N2).settle_t0 is None
    assert C.crack_state(state, N2).settled is False


def test_reset_crack_baseline_clears_everything_except_the_counters():
    state = make_state()
    node = state.nodes[N2]
    for i, t in enumerate((20.0, 20.5, 21.0, 21.5)):
        node.micro = 0.0 if i == 0 else 90.0 * i
        node.macro = 0.4
        C.update_node_crack(state, N2, node, t, flags=0)
    cs = C.crack_state(state, N2)
    assert cs.counts["micro_initiation"] >= 0
    raised = cs.counts["micro_initiation"]

    C.reset_crack_baseline(state, "test")
    assert cs.settled is False and cs.baseline_um is None
    assert cs.micro_armed is False and cs.macro_armed is False
    assert cs.stage == C.STAGE_UNKNOWN
    assert len(cs.hist) == 0
    assert cs.counts["micro_initiation"] == raised, "counters survive a re-arm"
    assert node.micro_rate is None and node.micro_settled is False
    assert node.crack_stage == C.STAGE_UNKNOWN


def test_state_reset_re_arms_the_crack_column():
    """``state.reset()`` is the operator's Re-arm — it must reach the engine."""
    state = make_state()
    node = state.nodes[N2]
    for i in range(200):
        node.micro = i * 40.0
        node.macro = i * 0.04
        node.displacement_mm = 5.0 + i * 0.04
        C.update_node_crack(state, N2, node, i * 0.5, flags=0)
    assert C.crack_state(state, N2).stage != C.STAGE_UNKNOWN
    state.reset("test re-arm")
    assert C.crack_state(state, N2).settled is False
    assert C.crack_state(state, N2).stage == C.STAGE_UNKNOWN
    assert node.micro is None and node.macro is None


# ===========================================================================
# 4 · The stage machine
# ===========================================================================

def test_two_stage_ordering_micro_fires_first():
    """**The demo.** One opening ramp, two instruments, thresholds ~1000× apart.

    The fine channel crosses after ~1.5 µm of opening; the coarse one needs
    1.5 mm. On a 0.075 mm/s ramp that is seconds versus half a minute, and the
    ordering is the phase's whole claim.
    """
    state = make_state()
    first: dict[str, float] = {}
    for t, events in ramp(state, 0.075, 140.0, start_after_s=5.0):
        for e in events:
            first.setdefault(e.event_type, t)
    assert C.EV_MICRO_INITIATION in first
    assert C.EV_MACRO_PROPAGATION in first
    assert first[C.EV_MICRO_INITIATION] < first[C.EV_MACRO_PROPAGATION]
    gap = first[C.EV_MACRO_PROPAGATION] - first[C.EV_MICRO_INITIATION]
    assert gap >= 10.0, f"the lead time was only {gap:.1f} s — not a two-stage story"
    stages = [e for e in state.events if e["level"] == "CRITICAL"]
    assert stages, "a collapse march must end in CRITICAL"


def test_stage_walks_none_initiation_propagation_critical():
    state = make_state()
    node = state.nodes[N2]
    seen = []
    for t, _ in ramp(state, 0.075, 200.0, start_after_s=5.0):
        if node.crack_stage not in seen:
            seen.append(node.crack_stage)
    assert seen[:4] == [C.STAGE_NONE, C.STAGE_INITIATION, C.STAGE_PROPAGATION,
                        C.STAGE_CRITICAL]


def test_stage_holds_on_the_level_after_the_delta_decays():
    """A crack does not heal: the *rate* says calm, the *stage* stays up.

    A slow seam — 0.04 mm/s, so the 30 s delta sits at 1.2 mm, below the 1.5 mm jump
    threshold — never trips the rate trigger. It is the **level** trigger that arms
    the latch when the opening passes 1.5 mm, and from then on the stage is
    PROPAGATION even though the rate is under the threshold. That is the whole
    point of two triggers: one measures speed, the other measures how far the seam
    has already gone.

    The stage is monotone by design, so 20 s later §3.2.5's hold route takes it to
    CRITICAL — *"propagation has not arrested"* — with `held_s` reported. A stage
    that could decay on its own would show a calming dashboard over an unchanged
    hazard.
    """
    state = make_state()
    node = state.nodes[N2]
    for t, _ in ramp(state, 0.04, 55.0, start_after_s=5.0):
        pass
    cs = C.crack_state(state, N2)
    assert cs.macro_d30 == pytest.approx(1.2, abs=0.05), "the rate trigger was in play"
    assert cs.macro_armed is True
    assert cs.stage == C.STAGE_PROPAGATION, "the stage followed the rate back down"
    assert node.crack_stage == C.STAGE_PROPAGATION

    # ... and the hold route, 20 s after the latch armed (opening ≈ 1.6 mm, well
    # short of CRACK_CRITICAL_MM — this is the *not arrested* route, not the
    # opening one).
    events = []
    t = 55.0
    while t < 70.0:
        node.macro = 0.04 * (t - 5.0)
        node.micro = node.macro * 1000.0
        node.displacement_mm = 5.0 + node.macro
        events += C.update_node_crack(state, N2, node, t, flags=0)
        t += 0.5
    crit = [e for e in events if e.event_type == C.EV_CRITICAL]
    assert crit, "the 20 s hold route never fired"
    assert crit[0].detail["trigger"] == "hold"
    assert crit[0].detail["held_s"] >= config.CRACK_CRITICAL_HOLD_S
    assert node.macro < config.CRACK_CRITICAL_MM
    assert "not arrested" in crit[0].text


def test_critical_by_opening_is_a_different_sentence_from_critical_by_hold():
    """Two routes into CRITICAL, two honest sentences.

    Reaching CRITICAL by the hold rule while the seam is only at 1.6 mm and
    printing "opening 1.60 mm (limit 5.0 mm)" would look like a contradiction on
    the operator's screen.
    """
    state = make_state()
    node = state.nodes[N2]
    # a two-sample confirmation of a *pre-existing* 6 mm opening (re-arm mid-incident)
    events = []
    for i in range(4):
        node.micro = 6000.0
        node.macro = 6.0
        node.displacement_mm = 11.0
        events += C.update_node_crack(state, N2, node, i * 0.5, flags=0)
    kinds = [e.event_type for e in events]
    assert C.EV_CRITICAL in kinds
    crit = [e for e in events if e.event_type == C.EV_CRITICAL][0]
    assert crit.detail["trigger"] == "opening"
    assert "limit 5.0 mm" in crit.text

    # ... and the hold route
    state2 = make_state()
    for t, _ in ramp(state2, 0.075, 200.0, start_after_s=5.0):
        pass
    crit2 = [e for e in state2.events if e["level"] == "CRITICAL"][0]
    assert "not arrested" in crit2["text"]


def test_a_single_sample_jump_raises_nothing_on_either_channel():
    """§3.2.3 applies to the coarse channel too — one sample is not an event."""
    state = make_state()
    node = state.nodes[N2]
    steady(state, 30.0)
    node.macro = 4.0                                 # a pot glitch, one sample
    node.micro = 0.0
    assert C.update_node_crack(state, N2, node, 30.0, flags=0) == []
    assert node.crack_stage == C.STAGE_NONE


def test_propagation_by_rate_uses_the_plan_sentence():
    """A fast jump is announced with the delta that measured it."""
    state = make_state()
    node = state.nodes[N2]
    steady(state, 40.0)
    events = []
    for i in range(20):
        t = 40.0 + 0.5 * i
        node.macro = min(2.0, i * 0.12)              # 0.24 mm/s → 7.2 mm/30 s
        node.micro = node.macro * 1000.0
        node.displacement_mm = 5.0 + node.macro
        events += C.update_node_crack(state, N2, node, t, flags=0)
    prop = [e for e in events if e.event_type == C.EV_MACRO_PROPAGATION][0]
    assert prop.detail["trigger"] == "rate"
    assert "in 30 s" in prop.text


def test_worst_stage_ranks_unknown_above_none():
    """A blind node is not a safe node — this is what Phase 7 will route on."""
    assert C.worst_stage([C.STAGE_NONE, C.STAGE_NONE]) == C.STAGE_NONE
    assert C.worst_stage([C.STAGE_NONE, C.STAGE_UNKNOWN]) == C.STAGE_UNKNOWN
    # UNKNOWN outranks INITIATION on purpose: a blind node must never be the
    # safer end of a comparison (Phase 7 routes on this).
    assert C.worst_stage([C.STAGE_UNKNOWN, C.STAGE_INITIATION]) == C.STAGE_UNKNOWN
    assert C.worst_stage([C.STAGE_PROPAGATION, C.STAGE_CRITICAL]) == C.STAGE_CRITICAL
    assert C.STAGE_RANK[C.STAGE_UNKNOWN] > C.STAGE_RANK[C.STAGE_NONE]


def test_node_without_a_crack_column_is_untouched():
    """N1/N3: the engine must do nothing at all, and say NONE rather than UNKNOWN."""
    state = make_state()
    node = state.nodes[N1]
    node.micro = 900.0                                # impossible, and irrelevant
    node.macro = 2.0
    assert C.update_node_crack(state, N1, node, 0.0, flags=0) == []
    assert N1 not in C._ledger(state).nodes
    assert C.crack_stage(state, N1) == C.STAGE_NONE
    block = C.crack_public(state, N1)
    assert block["stage"] == C.STAGE_NONE and "no crack column" in block["note"]


def test_status_escalation_from_a_critical_stage():
    """§3.5 — a 6 mm opening is a hazard while the accelerometer reads zero.

    Escalation is to WARNING only: CRITICAL status stays the acceleration
    channel's verdict until Phase 6 owns severity.
    """
    state = make_state()
    node = state.nodes[N2]
    for i in range(4):
        node.micro = 6000.0
        node.macro = 6.0
        node.displacement_mm = 11.0
        C.update_node_crack(state, N2, node, i * 0.5, flags=0)
    node.acceleration = 0.0
    state.update_status_logic()
    assert node.status == "WARNING"
    assert state.system_status == "WARNING"
    assert state.levels["1"]["status"] == "WARNING"
    assert state.levels["1"]["route"] == "BLOCKED"

    # A quiet crack node stays NOMINAL
    state2 = make_state()
    steady(state2, 30.0)
    state2.update_status_logic()
    assert state2.nodes[N2].status == "NOMINAL"


def test_crack_tick_marks_a_silent_node_unknown_not_none():
    """A node that goes dark mid-propagation must not keep showing PROPAGATION."""
    state = make_state()
    node = state.nodes[N2]
    state.note_arrival(N2, T0)                        # it spoke once
    for t, _ in ramp(state, 0.075, 40.0, start_after_s=5.0):
        state.note_arrival(N2, T0 + t)
    assert node.crack_stage == C.STAGE_PROPAGATION
    C.crack_tick(state, T0 + 60.0 + config.STALE_AFTER_S + 0.1)
    assert node.crack_stage == C.STAGE_UNKNOWN
    assert C.crack_state(state, N2).stage == C.STAGE_UNKNOWN
    assert node.micro_rate is None


def test_crack_tick_leaves_a_locally_driven_node_alone():
    """The bench case: no frame ever arrives, so ``last_arrival`` is empty.

    Reading that as silence wiped the rate and the stage on every tick — the
    detector looked like it never fired at all.
    """
    state = make_state()
    node = state.nodes[N2]
    for t, _ in ramp(state, 0.075, 40.0, start_after_s=5.0):
        pass
    assert node.crack_stage == C.STAGE_PROPAGATION
    C.crack_tick(state, 0.0)                          # "now" is far from any clock
    assert node.crack_stage == C.STAGE_PROPAGATION


def test_fault_beats_a_stale_stage():
    """A railed channel is UNKNOWN even while the node is delivering frames."""
    state = make_state()
    node = state.nodes[N2]
    state.note_arrival(N2, T0)
    for i in range(8):
        node.micro = 90_000.0
        node.macro = 0.0
        C.update_node_crack(state, N2, node, i * 0.5, flags=contracts.FLAG_MICRO_SATURATED)
    assert node.crack_stage == C.STAGE_UNKNOWN
    assert C.crack_state(state, N2).fault_reason == "micro_saturated"


# ===========================================================================
# 5 · The ingest path and the simulator
# ===========================================================================

def test_handle_node_frame_routes_the_crack_channels():
    """§3.5 — the frame path folds the column in before the status cascade."""
    state = make_state()
    state.hardware_source = "usb"
    fr = {"v": 2, "node": 2, "ms": 0, "disp": 5.4, "tilt": 0.1, "acc": 0.0,
          "vib": 12, "micro": 420.0, "macro": 0.42, "flags": 0}
    assert serial_reader.handle_node_frame(state, fr) is True
    node = state.nodes[N2]
    assert node.micro == pytest.approx(420.0)         # node-side zero: untouched
    assert node.macro == pytest.approx(0.42)
    assert node.micro_settled is True
    assert node.crack_stage in (C.STAGE_NONE, C.STAGE_UNKNOWN)


def test_a_frame_from_a_node_without_a_column_carries_no_crack_fields():
    """The contract refuses ``micro`` on N1/N3 — an absent channel is absent."""
    ok, why = contracts.validate_node_frame(
        {"v": 2, "node": 1, "ms": 1, "disp": 5, "tilt": 0, "acc": 0, "micro": 12.0})
    assert ok is False and "crack channels exist only on" in why


def test_simulator_seam_opens_only_for_structural_profiles():
    """The simulator's own physics: a truck shakes, a creep opens.

    If the seam followed raw displacement, a machinery pass would register as a
    30 mm crack and Phase 5 would learn *"trucks crack the roof"* — from a
    simulator artefact, not from physics.
    """
    simulator.reset_seam()
    state = make_state()
    simulator.arm(state, N2, "machinery_pass")
    peak_vib = 0
    total_vib = 0
    for _ in range(120):                              # the profile runs 12 s
        simulator.apply_injections(state, 0.1)
        peak_vib = max(peak_vib, state.nodes[N2].vib)
        total_vib += state.nodes[N2].vib
    # The *peak* here moved from ≥40 to a single-digit number in Phase 5, and the
    # reason is a change of shape rather than a quieter truck: §5.5 specifies an
    # "impulse train 0.5–4 s", so `machinery_pass` became four narrow axle strikes
    # instead of one wide burst. A narrow strike spread over far fewer sub-ticks
    # cannot pile 46 impacts into one 0.1 s sample. What the assertion is *for* —
    # "the vibration channel saw the truck" — is unchanged, so it is stated in the
    # way that survives a profile reshaping: a clear peak in any single sample,
    # and a large count across the pass.
    assert peak_vib >= 10, "the vibration channel should have seen the truck"
    assert total_vib >= 100, "the truck pass should sum to a real impact count"
    assert simulator.seam_open_mm(N2) == 0.0, "a truck pass opened the seam"

    simulator.reset_seam()
    state2 = make_state()
    simulator.arm(state2, N2, "warning_creep")
    for _ in range(800):
        simulator.apply_injections(state2, 0.1)
    assert simulator.seam_open_mm(N2) > 1.5
    assert state2.nodes[N2].micro > 1000.0
    assert state2.nodes[N2].macro > 1.0


def test_simulator_sensor_fault_reports_a_rail_not_a_zero():
    """§3.1 — "an open gauge must read as fault, not as zero strain"."""
    simulator.reset_seam()
    state = make_state()
    simulator.arm(state, N2, "sensor_fault")
    for _ in range(20):
        simulator.apply_injections(state, 0.1)
    node = state.nodes[N2]
    assert node.micro == pytest.approx(simulator.SIM_MICRO_RAIL_UM)
    assert node.flags & contracts.FLAG_MICRO_SATURATED
    assert node.flags & contracts.FLAG_MACRO_AT_RAIL
    assert node.macro == 0.0


def test_simulator_micro_channel_is_quantised_to_the_fine_resolution():
    """The two-stage story needs the *resolution* difference to be real.

    A pot with 24.4 µm per count cannot see the 1.5 µm crossing that the load cell
    sees; that is why the events are tens of seconds apart rather than
    simultaneous, and the simulator has to reproduce it.
    """
    simulator.reset_seam()
    state = make_state()
    simulator.arm(state, N2, "warning_creep")
    for _ in range(400):
        simulator.apply_injections(state, 0.1)
    node = state.nodes[N2]
    counts = node.macro / simulator.SIM_POT_RES_MM
    assert counts == pytest.approx(round(counts), abs=0.02), (
        "the macro channel ignored its own ADC resolution")
    assert abs(node.micro - node.macro * 1000.0) < 5.0, "the channels disagree"


def test_an_idle_crack_node_reports_a_measurement_not_a_blank():
    """A crack node nobody is driving is *quiet*, not *blind*.

    On the rig an undisturbed node's HX711 keeps sampling, so it keeps publishing
    ``micro ≈ 0`` — which is a measurement, and the specific one the coherence
    formula is built on ("the seam did not open"). The bench has to say the same
    thing. It did not, and the visible cost was that a creep drill on N2 showed
    ``worst_stage = UNKNOWN`` for its first twenty seconds: the untouched N4 was
    publishing no channel, the engine correctly refused to call a missing channel
    safe, and the blind node outranked the real INITIATION.
    """
    simulator.reset_seam()
    state = make_state()
    simulator.arm(state, N1, "machinery_pass")        # a truck, on a node with no column
    for _ in range(40):
        simulator.apply_injections(state, 0.1)

    for n_id in (N2, N4):
        node = state.nodes[n_id]
        assert node.micro is not None, f"{n_id} published no fine channel at all"
        assert abs(node.micro) <= 2.0 * simulator.SIM_MICRO_NOISE_UM, (
            "an idle seam must sit on the channel's noise floor")
        assert node.macro == 0.0, "an idle seam was reported as open"

    # ... and once the engine has seen them for longer than the settle window,
    # the answer is NONE — not UNKNOWN, which would mean "cannot be trusted".
    t = 0.0
    while t < 2 * config.MICRO_SETTLE_S:
        for n_id in (N2, N4):
            C.update_node_crack(state, n_id, state.nodes[n_id], t)
        t += 0.5
    for n_id in (N2, N4):
        assert C.crack_state(state, n_id).settled is True
        assert C.crack_stage(state, n_id) == C.STAGE_NONE


# ===========================================================================
# 6 · Additive by construction
# ===========================================================================

def test_the_payload_gained_keys_and_lost_none():
    """Every key the dashboard read before Phase 3 is still there, and the new
    ones are additions."""
    public = contracts.NodeState(node_id=N2).to_public()
    for key in ("status", "tilt_x", "displacement_mm", "acceleration", "velocity",
                "vib", "micro", "macro", "crack_stage", "last_seen_s", "stale"):
        assert key in public, f"Phase 3 removed {key}"
    for key in ("micro_rate", "micro_baseline_um", "micro_settled", "macro_d30",
                "cross_sensor_coherence"):
        assert key in public, f"Phase 3 failed to add {key}"
    assert set(public) >= set(contracts.NODE_PUBLIC_FIELDS)

    # ... and the top-level frozen paths are untouched too
    body = contracts.TelemetryPayload.skeleton().to_public()
    tops = {p.split(".")[0] for p in contracts.REQUIRED_TELEMETRY_PATHS}
    assert tops <= set(body), f"Phase 3 removed {tops - set(body)}"


def test_the_database_row_is_unchanged():
    """Migration 002 already carries the columns; Phase 3 adds no column and
    changes no order — it only narrows what the existing `micro` column *means*,
    and that is documented in code because there is nothing to migrate."""
    assert contracts.TELEMETRY_DB_COLUMNS == (
        "node_id", "tilt_x", "displacement_mm", "acceleration",
        "vib", "micro", "macro", "rssi", "batt", "level")
    node = contracts.NodeState(node_id=N2, micro=420.0, macro=0.4)
    row = node.to_db_tuple()
    assert len(row) == len(contracts.TELEMETRY_DB_COLUMNS)
    assert row[5] == pytest.approx(420.0)


def test_every_event_type_is_a_fixed_string():
    """The alert dispatcher, the runbook and the viva script quote these."""
    assert C.CRACK_EVENT_TYPES == ("CRACK_MICRO_INITIATION", "CRACK_MACRO_PROPAGATION",
                                   "CRACK_CRITICAL")
    assert C.EV_SENSOR_FAULT == "SENSOR_FAULT"
    assert set(C.STAGES) == {"NONE", "INITIATION", "PROPAGATION", "CRITICAL", "UNKNOWN"}


def test_only_the_configured_nodes_have_the_column():
    assert contracts.CRACK_NODE_IDS == ("node_2", "node_4")
    assert config.CRACK_NODE_IDS == contracts.CRACK_NODE_IDS
    for n_id in (N1, "node_3"):
        assert n_id not in contracts.CRACK_NODE_IDS


def test_crack_config_is_validated():
    """A clear fraction of 1.0 is a latch that can never release: fatal, not
    documented."""
    assert config.validate_config() == []
    import importlib

    saved = config.CRACK_CLEAR_FRACTION
    try:
        config.CRACK_CLEAR_FRACTION = 1.0
        problems = config.validate_config()
        assert any("CRACK_CLEAR_FRACTION" in p for p in problems)
    finally:
        config.CRACK_CLEAR_FRACTION = saved
    importlib.reload(config)


def test_describe_publishes_the_crack_block_in_json_scalars():
    """``/api/health`` renders this: an object that is not JSON-serialisable is a
    500 on the one endpoint an operator reaches for when things are wrong."""
    import json

    block = config.describe()["crack"]
    json.dumps(block)
    assert block["micro_unit"] == "um"
    assert block["instrumented_nodes"] == ["node_2", "node_4"]
    assert block["micro_rate_threshold_um_s"] == config.MICRO_RATE_THRESHOLD
    assert set(block["macro_calibration"]) == {"adc_lo", "mm_lo", "adc_hi", "mm_hi"}


def test_crack_summary_is_json_scalars():
    import json

    state = make_state()
    steady(state, 30.0)
    block = C.crack_summary(state)
    json.dumps(block)
    assert set(block) == {"nodes", "stages", "counts", "worst_stage"}
    assert block["stages"][N2] in C.STAGES
    node_block = C.crack_public(state, N2)
    json.dumps(node_block)
    assert set(node_block) >= {"stage", "settled", "baseline_um", "rate_um_s",
                               "macro_d30_mm", "coherence", "fault", "counts"}
