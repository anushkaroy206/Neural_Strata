"""§5.8 test surface — the inference scheduler's rates.

    cd backend && python3 -m pytest tests/test_scheduler_rates.py -q

**What this file is defending.**

The scheduler is the only place in the system where a classifying error can make
something *cheaper*. Every other consumer of Model 1 — the firewall, the tier, the
route — is safe when M1 is wrong, because the design routes around M1 rather than
through it. The cadence is the exception: if M1 mistakenly says QUIET, M2 drops to
0.2 Hz and the system becomes less precise.

That is an acceptable trade *only* while the firewall does not read the cadence.
So the two facts this file pins are:

1. the rates are exactly what §5.7 specifies — 2 Hz for M1 (§5.2's table, and the
   reason is a physical one: a truck impulse lasts 0.5 s, so anything slower than
   2 Hz can step over the event it exists to catch);
2. ``m2_hz_for`` cannot influence ``firewall_check`` — asserted by calling the
   firewall with every label at every cadence and requiring an identical answer.

The second is the load-bearing one. A future edit that made the firewall consult
the cadence would break
``test_the_firewall_does_not_read_the_cadence`` immediately, which is the point:
the guarantee should fail loudly the moment it stops being true, not quietly on
the night a collapse is misclassified.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

import config                                                         # noqa: E402
import state as ST                                                    # noqa: E402
from ingestion import simulator                                       # noqa: E402
from engine import cascade as C                                       # noqa: E402
from engine import pattern as PAT                                     # noqa: E402
from engine import ttf as TTF                                         # noqa: E402


# ---------------------------------------------------------------------------
# The table itself
# ---------------------------------------------------------------------------

def test_m1_runs_at_two_hertz():
    """2.0 Hz, not "about 1 Hz". §5.2 fixes this from the physics.

    ``SIM_*`` impulses last 0.5 s. At 1 Hz a window boundary can straddle a strike
    and the peak lands in the wrong window; at 2 Hz the shortest event the
    simulator produces is two samples wide, which is the minimum a 121-sample
    window can still see.
    """
    assert config.M1_HZ == 2.0


def test_m2_rates_are_the_plan_table():
    """The four rates, by name. Changing one is a deliberate act with a diff."""
    assert config.M2_HZ == {
        "QUIET": 0.2, "ENVIRONMENTAL": 1.0, "STRUCTURAL": 2.0, "UNKNOWN": 2.0,
    }


def test_m2_is_slower_than_m1_for_every_class():
    """The saving in §5.7 exists only because M2 is never *more* frequent.

    If any class asked for M2 faster than M1 there would be no scheduling to do,
    and the "amortisation" table in ``tools/bench_latency.py`` would be reporting
    a saving that had already been given away.
    """
    for label, hz in config.M2_HZ.items():
        assert hz <= config.M1_HZ, f"{label}: M2 at {hz} Hz outruns M1 at {config.M1_HZ} Hz"


# ---------------------------------------------------------------------------
# m2_hz_for
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", sorted(config.M2_HZ))
def test_m2_hz_matches_the_table(label):
    assert C.m2_hz_for(label) == config.M2_HZ[label]


def test_suspicious_labels_are_watched_fastest():
    """The ordering that matters: doubt must never buy a slower cadence.

    UNKNOWN is the *sensor fault* case — the instrument is not reporting, so the
    system knows less than at any other moment. It is scheduled at the same rate
    as a confirmed structural event, not at the quiet rate. Getting this backwards
    would make a broken gauge the cheapest thing that can happen to a mine.
    """
    assert C.m2_hz_for("UNKNOWN") >= C.m2_hz_for("STRUCTURAL")
    assert C.m2_hz_for("STRUCTURAL") > C.m2_hz_for("ENVIRONMENTAL")
    assert C.m2_hz_for("ENVIRONMENTAL") > C.m2_hz_for("QUIET")


def test_m1_unavailable_is_scheduled_like_unknown():
    """A missing classifier is not a reason to look less often.

    ``MANIFEST.json`` declares ``model_1.required = false``, so M1 being absent is
    a *supported* configuration — the system is supposed to keep working, on M2
    plus the physics chain. Degrading the cadence as well would turn a supported
    configuration into a quietly worse system, which is the failure mode the whole
    phase exists to prevent.
    """
    assert C.m2_hz_for(C.M1_UNAVAILABLE) == config.M2_HZ["UNKNOWN"]
    assert C.m2_hz_for(C.M1_UNAVAILABLE) > config.M2_HZ["QUIET"]


def test_an_unknown_label_fails_up_not_down():
    """A label the table has never heard of is treated as UNKNOWN, never QUIET.

    This is the direction that has to be right. A new class added to
    ``pattern.PATTERN_CLASSES`` without a matching cadence entry would otherwise
    silently inherit the slowest rate in the table, and the first symptom would be
    a missed event on a rig nobody had thought to re-benchmark.
    """
    assert C.m2_hz_for("SOMETHING_NEW") == config.M2_HZ["UNKNOWN"]
    assert C.m2_hz_for("") == config.M2_HZ["UNKNOWN"]
    assert C.m2_hz_for(None) == config.M2_HZ["UNKNOWN"]


# ---------------------------------------------------------------------------
# The isolation property
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("label", ["QUIET", "ENVIRONMENTAL", "STRUCTURAL",
                                   "UNKNOWN", "SOMETHING_NEW"])
@pytest.mark.parametrize("m2_ttf", [0.0, 5.0, 29.0, 30.0, 31.0, 600.0, None])
def test_the_firewall_does_not_read_the_cadence(label, m2_ttf):
    """**The property this file exists for.**

    ``firewall_check`` is run for every label at every TTF, and its answer must be
    a function of ``(m2_ttf, kin_ttf, label)`` alone. The test therefore compares
    the firewall's verdict against a decision rebuilt with the *same* inputs —
    which would fail the moment the firewall started consulting ``m2_hz`` or
    ``mode``, because the rebuilt decision's cadence is derived and the firewall's
    input set is fixed by its signature.

    The practical statement: **an error in the cadence table can cost precision. It
    cannot cost an alert.**
    """
    dec = C.CascadeDecision(node="node_1", t=0.0, m1={"label": label},
                            m2_ttf=m2_ttf, m1_label=label, kin_ttf=None,
                            mode="advisory")
    fired = C.firewall_check(dec)

    # The expected answer is computed from first principles rather than from a
    # second call, so this cannot pass by comparing the function with itself.
    expected = (
        (m2_ttf is not None and m2_ttf <= C.CRITICAL_BAND_S)
        or label in ("STRUCTURAL", "UNKNOWN")
    )
    assert fired is expected, (
        f"label={label} m2_ttf={m2_ttf}: firewall said {fired}, "
        f"the rule says {expected}"
    )


@pytest.mark.parametrize("mode", ["advisory", "gate"])
def test_the_cadence_never_changes_the_firewall(mode):
    """Mode-independence, restated at the scheduler's boundary.

    ``CASCADE_MODE`` is described in the plan as the knob that trades compute for
    certainty. It is a trade in *cadence only*: turning the gate on must not be
    able to switch off an alert, or the "gate" would be a way to buy throughput
    with safety, and nobody would ever be able to say which setting a given run
    used.
    """
    for m2_ttf in (0.0, 10.0, 29.999, 300.0):
        # Built through ``decide()``, the way every real caller builds one. A
        # hand-built ``CascadeDecision`` was how the tier default went unnoticed:
        # it skipped the one line that computes the verdict, and an unset tier
        # read as NOMINAL. That is now impossible (the default is ``""`` and
        # ``route_decision`` recomputes), but a test should exercise the real
        # constructor path regardless.
        dec = C.decide(m1={"label": "QUIET"}, m2_ttf=m2_ttf, kin_ttf=None,
                       mode=mode, node="node_1", t=0.0)
        fired = C.firewall_check(dec)
        assert fired == (m2_ttf <= C.CRITICAL_BAND_S)
        # ...and the tier the mode produces must still respect it.
        tier = C.route_decision(dec, mode=mode)
        if fired:
            assert tier in C.TIERS[1:], f"firewalled decision routed to {tier}"


def test_the_scheduler_is_a_pure_function_of_the_label():
    """No hidden state, no clock. The cadence is reproducible from the verdict.

    It has to be: ``cascade_status()`` publishes ``m2_hz`` to the dashboard, and a
    number that changed between two reads of the same decision would make the AI
    card flicker on stage with no underlying event to explain it.
    """
    for label in sorted(config.M2_HZ):
        first = C.m2_hz_for(label)
        for _ in range(5):
            assert C.m2_hz_for(label) == first


# ---------------------------------------------------------------------------
# §5.8 — the scheduler as a function, and the rest of the cascade's surface
#
# The file above pins the *rates*. These pin the clock that applies them, because
# a correct table read by a wrong scheduler is the same failure in a new place.
# ---------------------------------------------------------------------------

def _quiet_decision(node="node_1"):
    return C.decide(m1={"label": "QUIET"}, m2_ttf=3600.0, node=node, t=0.0)


@pytest.fixture(autouse=True)
def _fresh_cascade():
    """Each test starts from an empty decision table and clock."""
    C.reset()
    yield
    C.reset()


def test_every_node_is_due_on_the_first_tick():
    """The first window of a node's life is always measured.

    A node with no history has no label, and an unknown label must fail *up*
    (``m2_hz_for`` returns the UNKNOWN rate) rather than be skipped — a scheduler
    that skipped unlabelled nodes would never label them.
    """
    assert sorted(C.inference_scheduler(None, 0.0)) == sorted(config.ACTIVE_NODES)


def test_a_quiet_node_is_not_due_again_inside_its_slot():
    """0.2 Hz is a slot of 5 s, and the node is due once per slot."""
    C._DECISIONS["node_1"] = _quiet_decision()
    C._M2_NEXT["node_1"] = 100.0
    assert "node_1" not in C.inference_scheduler(None, 100.0 - 0.5)
    assert "node_1" not in C.inference_scheduler(None, 100.0 - 0.001)
    assert "node_1" in C.inference_scheduler(None, 100.0)
    assert "node_1" in C.inference_scheduler(None, 100.0 + 4.0)


def test_a_structural_node_is_due_at_two_hertz():
    """The promotion is automatic, and it is the label that does it.

    This is the safety consequence of the scheduler being a function of the
    label: nothing has to *tell* the system that a node looks suspicious.
    """
    C._DECISIONS["node_2"] = C.decide(m1={"label": "STRUCTURAL"}, node="node_2", t=0.0)
    C._M2_NEXT["node_2"] = 50.0
    assert "node_2" not in C.inference_scheduler(None, 50.0 - 0.25)
    assert "node_2" in C.inference_scheduler(None, 50.0)
    assert "node_2" in C.inference_scheduler(None, 50.0 + 0.5)


def test_the_scheduler_is_read_only():
    """Asking who is due must never change who is due.

    A dashboard, a test and a second thread all call this. If it advanced the
    clock, "how fast is this machine thinking?" would only be answerable by
    running it, and the answer would differ per caller.
    """
    C._M2_NEXT["node_1"] = 7.5
    before = dict(C._M2_NEXT)
    for i in range(50):
        C.inference_scheduler(None, float(i))
    assert dict(C._M2_NEXT) == before


def _settled_state():
    """A ``SystemState`` with a full 60 s window on every node, like a quiet rig.

    Built through ``simulator.measure`` + ``append_model_buffer`` — the same two
    calls the live ticker makes — so the test exercises the real extractor rather
    than a fixture shaped to pass.

    Both models are loaded here because ``app.py`` loads both at boot and the
    cadence is a function of M1's *label*: with no classifier the label is
    UNAVAILABLE, ``m2_hz_for`` fails up to the UNKNOWN rate (2 Hz), and every tick
    is a slot — which is correct behaviour and a useless way to test gating.
    """
    C.reset()
    PAT.load_pattern_model()
    TTF.load_ttf_model()
    TTF.load_ttf_band()
    st = ST.SystemState()
    t = 0.0
    for _ in range(config.MODEL_BUF):
        t += config.MODEL_DT
        simulator.apply_injections(st, config.MODEL_DT)
        for n in st.iter_nodes():
            d, ti, a = simulator.measure(float(n.displacement_mm or 0.0),
                                         float(n.tilt_x or 0.0),
                                         float(n.acceleration or 0.0), t, n.node_id)
            st.append_model_buffer(n.node_id, t, d, ti, a, int(n.vib),
                                   n.micro, n.macro, clock="sched-test")
    return st, t


def test_the_scheduler_really_gates_m2_and_carries_the_value_forward():
    """The whole point of the scheduler, end to end through ``cascade_tick``.

    Three claims, and the third is the safety one:

    1. a quiet node's first tick runs M2 (``extra["m2_ran"]``);
    2. half a second later it does **not**, because a quiet node is on a 5 s slot;
    3. the TTF the firewall reads is *unchanged* across that skipped tick — the
       value is carried, not blanked. Without this, reducing the cadence would
       silently remove a short countdown from the firewall for up to 5 s, which is
       exactly the window §5.10's Rule 2 exists to cover.
    """
    st, t0 = _settled_state()
    first = C.cascade_tick(st, t0)
    dec1 = first["node_1"]
    assert dec1.extra.get("m2_ran") is True, "the first tick must measure"
    # A settled rig reading anything but QUIET is a finding about the extractor or
    # the simulator, not a flake to be tolerated — so it fails here by name.
    assert dec1.m1_label == "QUIET", f"a settled rig classified as {dec1.m1_label}"

    second = C.cascade_tick(st, t0 + 0.5)
    dec2 = second["node_1"]
    assert dec2.extra.get("m2_ran") is False, "0.2 Hz means this tick is not its slot"

    if dec1.m2_ttf is not None:
        assert dec2.m2_ttf == dec1.m2_ttf, "a skipped tick must carry the value forward"
        assert dec2.m2_band == dec1.m2_band
    else:
        # Even a rejected prediction must be consistent across the slot: the gate
        # rejecting once and accepting the identical window 0.5 s later would make
        # the countdown flicker on the panel.
        assert dec2.m2_ttf is None

    # And when the slot comes round, M2 runs again.
    third = C.cascade_tick(st, t0 + 6.0)
    assert third["node_1"].extra.get("m2_ran") is True


def test_without_m1_the_cadence_fails_up_to_two_hertz():
    """An absent classifier is not a reason to think less often.

    ``m2_hz_for(UNAVAILABLE)`` is the UNKNOWN rate, so losing M1 makes the fleet
    *more* expensive, never quieter. Pinned here because the tempting "no label,
    so no need to watch it closely" reasoning is exactly backwards.
    """
    C.reset()
    assert C.m2_hz_for(C.M1_UNAVAILABLE) == 2.0
    assert sorted(C.inference_scheduler(None, 0.0)) == sorted(config.ACTIVE_NODES)
    C._M2_NEXT["node_1"] = 10.0
    assert "node_1" not in C.inference_scheduler(None, 9.75)
    assert "node_1" in C.inference_scheduler(None, 10.0)


def test_run_cascade_reports_absence_rather_than_a_default():
    """A cold node is ``None``, never a fabricated NOMINAL.

    ``CascadeDecision.tier`` no longer defaults to ``NOMINAL`` for exactly this
    reason; returning a warm-up default here would put that default straight back.
    """
    C.reset()
    assert C.run_cascade(ST.SystemState(), "node_1", 1.0) is None


def test_disagreement_flag_is_the_field_on_the_decision():
    """Two readers of one rule: the flag and the published predicate.

    A dashboard saying "no disagreement" while the alert log says the models
    contradict each other is the failure this pins shut.
    """
    for label in ("QUIET", "ENVIRONMENTAL", "STRUCTURAL", "UNKNOWN"):
        for ttf in (None, 5.0, 29.0, 30.0, 60.0, 3600.0):
            dec = C.decide(m1={"label": label}, m2_ttf=ttf, node="node_1", t=0.0)
            assert dec.disagreement == C.disagreement_flag(dec), (label, ttf)
            expected = label in ("QUIET", "ENVIRONMENTAL") and ttf is not None and ttf <= 30.0
            assert dec.disagreement is expected, (label, ttf)


def test_explain_decision_is_one_line_naming_both_models():
    """The alert log's sentence: greppable, and honest about both models."""
    dec = C.decide(m1={"label": "ENVIRONMENTAL"}, m2_ttf=12.0, node="node_3", t=0.0)
    line = C.explain_decision(dec)
    assert "\n" not in line
    assert line.startswith("node_3:")
    assert "MODEL 1" in line and "ENVIRONMENTAL" in line
    assert "M2 median 12 s" in line
    assert dec.tier in line

    # With no M2 at all the line says so rather than inventing a countdown.
    bare = C.decide(m1=None, node="node_1", t=0.0)
    assert "no TTF estimate" in C.explain_decision(bare)

    # And a band, when there is one, is quoted as an interval.
    banded = C.decide(m1={"label": "STRUCTURAL"}, m2_ttf=45.0,
                      m2_band=(20.0, 45.0, 90.0), node="node_2", t=0.0)
    assert "q10–q90 20–90 s" in C.explain_decision(banded)


def test_the_cascade_keeps_the_model_status_honest():
    """The dashboard must not say "warming up" on a live cascade.

    ``ttf.model_fresh()`` reads ``MODEL_TTF`` and ``STATE["last_ok"]``, and
    §5.9 deleted the only caller that used to write them. This is the regression
    guard for that: after one tick on a warm node, ``model_status()`` reports
    ACTIVE and the note is no longer the warm-up sentence — the exact string the
    operator reads to decide whether to trust the panel.
    """
    st, t0 = _settled_state()
    # Explicitly cold: the module-level bookkeeping is shared by every test in
    # this file, and a test that only passes when it runs first is a test that
    # will one day report a regression that is not there.
    TTF.STATE["last_ok"] = 0.0
    for _n in TTF.MODEL_TTF:
        TTF.MODEL_TTF[_n] = None
    before = TTF.model_status()["status"]
    C.cascade_tick(st, t0)
    after = TTF.model_status()
    if before == "FALLBACK":
        assert after["status"] == "FALLBACK"
    else:
        assert before == "WARMING UP", f"expected a cold start, got {before}"
        assert after["status"] == "ACTIVE", after["status"]
        assert "Warming up" not in after["note"]
    assert any(v is not None for v in TTF.MODEL_TTF.values())
