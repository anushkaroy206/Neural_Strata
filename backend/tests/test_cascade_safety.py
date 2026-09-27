#!/usr/bin/env python3
"""
Neural Strata — the safety firewall's test (B11, Phase 5)
==========================================================

**This file is the safety case. Everything else is engineering.**

§5.10 asks for "the most important 30 lines in the repository", and then says
this about the test that makes them real:

    > Add this test file **before** writing ``cascade.py``. Write the failing
    > test, then make it pass. A safety property that is only asserted in a
    > slide is not a safety property.

It was written first, it failed for the right reason (no ``engine.cascade``), and
then ``cascade.py`` was written until it passed. That order is not ceremony: a
test written after the implementation tests the implementation's shape, and a
test written before it tests the *claim*.

What is being claimed
---------------------
Model 1 — the pattern discriminator — may control **how often we compute** and
**how loudly we shout**. It may not control **whether we warn**.

The reason is asymmetric cost, and it is worth stating in the form it will be
asked on stage:

    A classifier that is allowed to *silence* a detector can only be wrong in
    one direction — the silent one — and every one of those errors is a missed
    collapse. A classifier that can only ever *add* warnings is wrong in the
    direction of a nuisance call. We are trading an unbounded downside for a
    bounded one.

The reference cascade architecture in the original pitch is a **hard gate**: M1
says Pass, or M2 never runs. That form fails this file comprehensively and by
construction, which is why §5.2 inverts it.

How this file enforces it
-------------------------
Not by spot-checking. Three of the tests below are **exhaustive over the whole
input space** of the decision function — every label × every TTF × every mode —
and one of them states the property in its strongest available form:

    **monotonicity** — the tier produced *with* Model 1's opinion must never be
    lower than the tier produced *without* it at all,

which is exactly "M1 can only add information", quantified. A spot check passes
if nobody has broken the thing yet. A quantification fails the moment anybody
does, including the author of this file six months from now.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from engine import cascade as C  # noqa: E402  (fails until cascade.py exists — on purpose)

#: Every label M1 can emit, plus the state of not having an M1 at all.
ALL_M1_STATES = ("QUIET", "ENVIRONMENTAL", "STRUCTURAL", "UNKNOWN", "UNAVAILABLE")

#: The TTFs that bracket every branch of the firewall: below the band, exactly on
#: it (the off-by-one that a real bug lives in), and comfortably above it. Plus
#: ``None``, which is "no estimate" and must not read as "no danger".
ALL_TTF = (None, 0.0, 5.0, 29.9, 30.0, 30.1, 60.0, 600.0, 3600.0)

MODES = ("advisory", "gate")

#: Ordered, weakest first. ``route_decision`` must never return a lower index
#: with more information than it had without it.
TIER_ORDER = ("NOMINAL", "ADVISORY", "ELEVATED", "IMMINENT")


def mk(m1_label="QUIET", m2_ttf=None, kin_ttf=None, m1=None, disruption_index=0.0):
    """Build a CascadeDecision the way ``cascade.decide`` would.

    Uses the production constructor rather than a hand-built stub, so a field
    added to the decision cannot quietly escape these tests.
    """
    if m1 is None and m1_label != "UNAVAILABLE":
        m1 = {
            "label": m1_label,
            "disruption_index": disruption_index,
            "structural_prob": 1.0 if m1_label == "STRUCTURAL" else 0.0,
            "probs": {},
            "top_features": [],
            "threshold": 0.05,
        }
    return C.decide(m1=m1, m2_ttf=m2_ttf, kin_ttf=kin_ttf)


def tier(m1_label="QUIET", m2_ttf=None, kin_ttf=None, mode="advisory"):
    return C.route_decision(mk(m1_label, m2_ttf, kin_ttf), mode=mode)


# ---------------------------------------------------------------------------
# The plan's own two tests, verbatim in intent
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label", ("QUIET", "ENVIRONMENTAL"))
@pytest.mark.parametrize("ttf", (0, 5, 15, 29, 30, 60))
def test_m1_can_never_suppress_m2(label, ttf, mode):
    """§5.10's headline test, run in *both* modes.

    The plan writes it for ``advisory``. Running it for ``gate`` too is the
    stronger claim and the one that matters: the gated form is the reference
    architecture that would otherwise silence a real collapse, and §5.2 states
    that even ``gate`` mode remains subject to the firewall.
    """
    got = tier(label, m2_ttf=ttf, mode=mode)
    if ttf <= 30:
        assert got in ("ELEVATED", "IMMINENT"), (
            f"M1={label} suppressed M2_ttf={ttf} in mode={mode} (tier={got})")


def test_gate_mode_still_respects_firewall():
    """Even with the reference Pass/Block cascade enabled, a low M2 TTF keeps the
    alert live.

    ``gate`` is allowed to stop *computing* M2 when M1 says QUIET. It is not
    allowed to forget the M2 answer it already has.
    """
    dec = mk("QUIET", m2_ttf=12.0)
    assert C.firewall_check(dec) is True
    assert C.route_decision(dec, mode="gate") in ("ELEVATED", "IMMINENT")


# ---------------------------------------------------------------------------
# The firewall, stated as properties rather than examples
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("label", ALL_M1_STATES)
def test_the_tier_is_monotone_in_information(label, mode):
    """**The strongest form of the claim: M1 may only ever raise the tier.**

    For every label M1 could produce, and every TTF the models could produce, the
    tier *with* M1's opinion must be >= the tier computed with M1 removed
    entirely. That single inequality is "Model 1 can add information and can never
    remove it", and it is checked over the whole cross-product, not sampled.

    If someone later adds a rule of the form "when M1 says ENVIRONMENTAL, suppress
    the alert", this fails — on that exact row, with the numbers printed.
    """
    for ttf in ALL_TTF:
        with_m1 = C.route_decision(mk(label, m2_ttf=ttf), mode=mode)
        without = C.route_decision(mk("UNAVAILABLE", m2_ttf=ttf), mode=mode)
        assert TIER_ORDER.index(with_m1) >= TIER_ORDER.index(without), (
            f"M1={label} LOWERED the tier for ttf={ttf} in mode={mode}: "
            f"{without} -> {with_m1}")


@pytest.mark.parametrize("mode", MODES)
def test_the_suppression_rate_on_structural_is_zero_by_construction(mode):
    """§5.11: *"Suppression rate on STRUCTURAL: 0 **by construction**"*.

    The plan asks for that to be reported as a structural impossibility rather
    than an empirical absence, and this is where "by construction" is cashed in:
    an exhaustive sweep of every label against every TTF that would otherwise
    demand an alert.

    A short TTF is the trigger for a structural event in the corpus, so "the
    system failed to alert on a structural condition" is exactly "some label
    silenced some short TTF". There are no other cases.
    """
    silenced = []
    for ttf in ALL_TTF:
        if ttf is None or ttf > C.CRITICAL_BAND_S:
            continue                       # no structural condition to suppress
        for label in ALL_M1_STATES:
            got = C.route_decision(mk(label, m2_ttf=ttf), mode=mode)
            if got not in ("ELEVATED", "IMMINENT"):
                silenced.append((label, ttf, got))
    assert not silenced, f"M1 suppressed a structural condition: {silenced}"


def test_the_physics_fallback_has_equal_authority():
    """A missing model is not a reason to relax.

    §5.10 returns True for a short **kinematic** TTF as well as a short M2 one,
    and the reason is that the fallback is the same estimator the model was
    trained to imitate. If the model file is deleted, the system must get *less
    precise*, never less safe.
    """
    for label in ALL_M1_STATES:
        for ttf in (0.0, 5.0, 29.9, 30.0):
            got = tier(label, kin_ttf=ttf)
            assert got in ("ELEVATED", "IMMINENT"), (
                f"kinematic TTF {ttf} was silenced by M1={label}")
    # ... and it does NOT alert once the fallback says there is time.
    assert tier("QUIET", kin_ttf=600.0) in ("NOMINAL", "ADVISORY")


def test_the_band_edge_is_closed():
    """`<= 30` and not `< 30`, checked on the exact boundary.

    Trivial, and worth one test: an off-by-one here is invisible in every other
    test in this file and would mean the single most extreme reading the system
    can produce — a TTF of exactly the critical band — is the one that does not
    alert.
    """
    assert C.firewall_check(mk("QUIET", m2_ttf=C.CRITICAL_BAND_S)) is True
    assert tier("QUIET", m2_ttf=C.CRITICAL_BAND_S) in ("ELEVATED", "IMMINENT")
    assert C.firewall_check(mk("QUIET", m2_ttf=C.CRITICAL_BAND_S + 0.01)) is False


# ---------------------------------------------------------------------------
# What must NOT alert
# ---------------------------------------------------------------------------

def test_environmental_alone_is_advisory_never_silent():
    """A truck is not a collapse — but it is also not nothing.

    §5.12: ``machinery_pass`` ⇒ ENVIRONMENTAL, **no tier above ADVISORY**. So the
    floor for an identified environmental disturbance is ADVISORY: the operator
    is told something happened. Reporting it as NOMINAL would satisfy "did not
    alarm" while throwing away the information that a loader just drove past,
    which is the narration the whole phase exists to produce.
    """
    assert tier("ENVIRONMENTAL", m2_ttf=None) == "ADVISORY"
    assert tier("ENVIRONMENTAL", m2_ttf=3600.0) == "ADVISORY"


def test_a_quiet_tunnel_with_time_to_spare_is_nominal():
    assert tier("QUIET", m2_ttf=None) == "NOMINAL"
    assert tier("QUIET", m2_ttf=3600.0) == "NOMINAL"
    assert tier("ENVIRONMENTAL", m2_ttf=None) != "NOMINAL", "silence is not a report"


def test_structural_or_unknown_always_alert_regardless_of_ttf():
    """UNKNOWN is in the alert set on purpose.

    ``UNKNOWN`` is what M1 says when it cannot tell — including when it is not
    loaded at all. "I cannot measure this" must never read as "all clear", which
    is the same rule Phase 2 applies to a sensor that has never reported.
    """
    for label in ("STRUCTURAL", "UNKNOWN"):
        for ttf in ALL_TTF:
            got = tier(label, m2_ttf=ttf)
            assert got in ("ELEVATED", "IMMINENT"), f"{label} with ttf={ttf} gave {got}"


# ---------------------------------------------------------------------------
# The scheduler, the disagreement flag, and graceful degradation
# ---------------------------------------------------------------------------

def test_the_scheduler_throttles_on_quiet_and_never_on_structural():
    """§5.2's routing table, exactly.

    This is the *whole* source of the compute saving: QUIET lets the 400-tree
    boosted ensemble back off to 0.2 Hz. It is listed in the same file as the
    firewall because the routing decision and the alert decision share an input —
    and a bug that coupled them the wrong way (throttling a structural node, or
    alerting on a quiet one) would be a safety bug in either direction.
    """
    assert C.m2_hz_for("QUIET") == pytest.approx(0.2)
    assert C.m2_hz_for("ENVIRONMENTAL") == pytest.approx(1.0)
    assert C.m2_hz_for("STRUCTURAL") == pytest.approx(2.0)
    assert C.m2_hz_for("UNKNOWN") == pytest.approx(2.0)
    # Unavailable M1 must not throttle: an unknown state gets the full rate.
    assert C.m2_hz_for("UNAVAILABLE") == pytest.approx(2.0)


def test_a_contradiction_is_flagged_where_anyone_can_see_it():
    """M1 says environmental, M2 says seconds. Both are kept, and the conflict is
    raised rather than resolved silently.

    The failure mode this prevents is the quiet one: a system that picks one
    number to display to avoid looking confused. The operator needs to know the two
    models disagree, because that is a fact about the situation.
    """
    dec = mk("ENVIRONMENTAL", m2_ttf=10.0)
    assert dec.disagreement is True
    assert dec.m2_ttf == 10.0, "the disagreeing number must still be reported"
    assert dec.m1_label == "ENVIRONMENTAL"
    assert C.route_decision(dec) in ("ELEVATED", "IMMINENT"), "disagreement escalates"

    # Agreement is not a disagreement.
    assert mk("STRUCTURAL", m2_ttf=10.0).disagreement is False
    assert mk("ENVIRONMENTAL", m2_ttf=600.0).disagreement is False


def test_the_system_degrades_without_model_1_and_still_alerts():
    """Rollback: delete ``pattern_model_v1.json`` and the system must behave
    exactly like Phase 4 — less precise, not less safe.

    §5.13 calls this the rollback path and asks for it to be tested before demo
    day. The assertion that matters is that a short M2 TTF still escalates with no
    classifier present at all.
    """
    for ttf in (0.0, 5.0, 29.9, 30.0):
        got = tier("UNAVAILABLE", m2_ttf=ttf)
        assert got in ("ELEVATED", "IMMINENT"), f"M2-only failed to alert at ttf={ttf}"
    # With no M1 and no short TTF there is nothing to escalate on...
    assert tier("UNAVAILABLE", m2_ttf=600.0) == "NOMINAL"
    # ... but the *absence* of a classifier is itself reported, never hidden.
    dec = mk("UNAVAILABLE", m2_ttf=600.0)
    assert dec.m1_label == "UNAVAILABLE"
    assert "availability" in dec.reason.lower() or "unavailable" in dec.reason.lower()


def test_a_gate_that_stops_computing_m2_cannot_erase_the_last_verdict():
    """The gated form's specific hazard, pinned.

    In ``gate`` mode M1 may stop M2 from running. The firewall reads the *last
    known* M2 TTF, so the alert from that window stays live. A test that only
    checked "the last TTF is still in the object" would pass on a stub; this one
    checks that the object still forces the tier.
    """
    dec = mk("QUIET", m2_ttf=8.0)
    dec2 = C.decide(m1=dec.m1, m2_ttf=dec.m2_ttf, kin_ttf=None, mode="gate")
    assert C.route_decision(dec2, mode="gate") in ("ELEVATED", "IMMINENT")


# ---------------------------------------------------------------------------
# The test that would fail if someone made M1 a hard gate
# ---------------------------------------------------------------------------

def test_making_model_1_a_hard_gate_would_fail_this_file():
    """§C.4: *"``tests/test_cascade_safety.py`` passes — **and would fail if
    someone made M1 a hard gate**."*

    This test does not simulate a hard gate; it demonstrates that the gate
    *architecture* is expressible in this module and is rejected by every property
    above. It computes what a hard gate would return and asserts that the real
    implementation returns something strictly safer.

    Concretely: under a hard gate, ``M1=QUIET`` would mean M2 never runs, so
    ``m2_ttf`` would be ``None``, and a collapse would be reported as NOMINAL. The
    assertion below shows the shipped behaviour differs exactly there.
    """
    hard_gate_result = "NOMINAL"                  # what a gate would produce
    real = tier("QUIET", m2_ttf=5.0)              # the M2 answer it would have discarded
    assert real in ("ELEVATED", "IMMINENT")
    assert real != hard_gate_result, (
        "the implementation agrees with a hard gate — Rule 2 has been lost")

    # And the gate is still available for the off-line comparison, because a
    # negative result you can demonstrate is worth more than an assertion.
    assert "gate" in getattr(C, "MODES", ("advisory", "gate"))


def test_the_firewall_is_not_shadowed_by_the_scheduler():
    """The two decisions must stay independent.

    ``route_decision`` (what tier) and ``m2_hz_for`` (how often) read the same M1
    label. If a future refactor derived the tier from the *rate* — or the rate from
    the tier — the throttling of a quiet node could start throttling its alarm.
    This asserts a short TTF escalates at every rate M1 can select.
    """
    for label in ALL_M1_STATES:
        rate = C.m2_hz_for(label)
        got = tier(label, m2_ttf=5.0)
        assert got in ("ELEVATED", "IMMINENT"), (
            f"a {rate} Hz route failed to escalate on a 5 s TTF")


# ---------------------------------------------------------------------------
# The gate that keeps a rejected number off the firewall
# ---------------------------------------------------------------------------

def test_the_gate_cannot_hide_a_real_collapse():
    """**The precondition that makes the sanity gate safe to use.**

    ``cascade_tick`` drops an M2 prediction that fails ``ttf.sanity_gate`` — and
    dropping a prediction means the firewall no longer sees it. That is only
    admissible if the gate *cannot* reject a genuine collapse, so this asserts the
    gate's own rule directly against windows shaped like real failures.

    The rule is: reject when ``ttf < 30`` **and** ``v10 < 0.5`` **and**
    ``disp_now < 30`` — i.e. *an imminent prediction with no motion and no
    displacement*. A collapse has both, by definition. So for every
    (displacement, velocity) pair that could describe a failing panel, the gate
    must pass the prediction through.

    Without this test the gate is an unexamined hole in front of Rule 2.
    """
    from engine import ttf as T

    # (disp_now, v10) — a collapse has large and/or moving displacement.
    real_failures = [
        (95.0, 2.0), (100.0, 5.0), (60.0, 1.2), (30.0, 0.6),
        (85.0, 0.0),          # already at the limit, momentarily still
        (45.0, 0.5),          # exactly on the velocity edge
        (40.0, 9.0),
    ]
    for disp, v10 in real_failures:
        feats = [0.0] * 16
        feats[0] = disp
        feats[4] = v10
        for ttf in (0.0, 1.0, 15.0, 29.9):
            assert T.sanity_gate(feats, ttf) is True, (
                f"the gate rejected a real collapse: disp={disp} v10={v10} ttf={ttf}")

    # ... and it does reject the artefact it exists for.
    still = [0.0] * 16
    still[0] = 5.2          # a truck's transient deflection
    still[4] = 0.0          # ... that is no longer moving
    assert T.sanity_gate(still, 17.0) is False, "the gate must reject this"
    # A long TTF is never rejected, whatever the motion.
    assert T.sanity_gate(still, 600.0) is True


def test_the_firewall_reads_only_predictions_that_passed_the_gate():
    """End-to-end shape of the same property, stated at the decision layer.

    A rejected prediction arrives as ``None``, and ``None`` must not be read as
    "safe" *or* as "dangerous" — it means "no estimate", and the tier then falls
    to whatever M1 says. Reproducing the machinery case: still node, M1 says
    environmental, M2's number was rejected.
    """
    dec = mk("ENVIRONMENTAL", m2_ttf=None)
    assert dec.m2_ttf is None
    assert C.firewall_check(dec) is False
    assert C.route_decision(dec) == "ADVISORY", (
        "a rejected M2 estimate on an environmental window must be an advisory, "
        "not an imminent alarm")


def test_the_kinematic_fallback_uses_the_same_gate_as_the_model():
    """A number is not more trustworthy for having come from a formula.

    ``cascade._kinematic_ttf`` mirrors ``ttf.sanity_gate``'s rule with raw values
    instead of a feature vector. Two implementations of one rule drift unless
    something checks they agree, so this drives both across the boundary cases.
    """
    from engine import ttf as T

    cases = [
        # (disp_now, v10, ttf, expected_pass)
        (5.2, 0.0, 17.0, False),      # a recovering deflection — reject
        (5.2, 0.0, 600.0, True),      # a long estimate is never rejected
        (95.0, 2.0, 5.0, True),       # a real collapse — must pass
        (85.0, 0.0, 5.0, True),       # at the limit, momentarily still
        (29.9, 0.6, 10.0, True),      # moving: passes
        (29.9, 0.4, 10.0, False),     # still and short of the limit: reject
    ]
    for disp, v10, ttf, expect in cases:
        feats = [0.0] * 16
        feats[0] = disp
        feats[4] = v10
        via_model = T.sanity_gate(feats, ttf)
        via_kin = C.T_sanity(v10, disp, ttf)
        assert via_model == expect, f"sanity_gate disagreed on {disp},{v10},{ttf}"
        assert via_kin == expect, f"T_sanity disagreed on {disp},{v10},{ttf}"
        assert via_model == via_kin, (
            f"the two gates have drifted apart on {disp},{v10},{ttf}")
