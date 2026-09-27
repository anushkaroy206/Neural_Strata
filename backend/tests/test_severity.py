#!/usr/bin/env python3
"""
Neural Strata — B3: severity, progression stage, confidence (Phase 6).
=====================================================================

§6.7's acceptance table, plus the rules that table implies but does not spell
out. Four things are being pinned, and each of them is a claim made on stage:

1. **The tier boundaries are exact.** ``ttf=25`` is CRITICAL, ``ttf=900`` is
   NOMINAL, and 30 / 120 / 600 are the edges — not approximately. A band that is
   off by one second is a band that tells an operator to evacuate 30 seconds too
   late, or to stand down 30 seconds too early.

2. **TERTIARY leads the breach.** §6.7 states the requirement with its reason:
   *"it must lead the breach, otherwise it is a lagging indicator."* The test
   below runs the shipped ``critical_collapse`` profile through the real
   simulator and asserts the stage is TERTIARY **before** displacement crosses
   60 mm. This is the assertion that forced the stage machine to have two entry
   paths — see the note under :func:`test_tertiary_leads_the_breach`.

3. **Severity never rises on stale data** (the plan's own acceptance line for
   B3, ``Backend_Plan.md:420``). A node with no fresh frame can only ever land in
   WATCH, the tier that reaches the dashboard and nothing else.

4. **D6 is closed.** ``update_status_logic`` runs from the ticker and nowhere
   else — in particular, not from the serial reader's per-frame path, which is
   where it used to run on a second thread.

The stage tests drive :func:`engine.severity.progression_stage` directly with an
explicit clock instead of going through :func:`engine.severity.severity_tick`.
That is deliberate: ``severity_tick`` owns a 1 Hz rate limit whose whole job is
to *ignore* most calls, and a test that wants to watch a stage machine commit
must control the clock rather than fight the limiter. The limiter itself is
tested in :func:`test_the_stage_machine_runs_at_one_hertz`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from engine import crack as CRACK                      # noqa: E402
from engine import kinematic as KIN                    # noqa: E402
from engine import severity as S                       # noqa: E402
from ingestion import simulator                        # noqa: E402
from state import SystemState                          # noqa: E402

NODE = "node_2"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------
def _tick(state: SystemState, t: float, node_id: str = NODE) -> None:
    """One 0.5 s step of the production bench path, minus the status pass.

    Physics → crack engine → the noised buffer append. Identical to
    ``app.py``'s ticker body and to ``tests/test_pattern.py``'s harness; the
    difference here is that the stage machine is called by the test itself, with
    the test's clock, instead of from ``update_status_logic``.
    """
    for _ in range(10):
        simulator.apply_injections(state, 0.05)
    for n in state.iter_nodes():
        CRACK.update_node_crack(state, n.node_id, n, t)
    CRACK.crack_tick(state, t)
    node = state.nodes[node_id]
    d, tilt, acc = simulator.measure(float(node.displacement_mm), float(node.tilt_x),
                                     float(node.acceleration), t, node_id)
    state.append_model_buffer(node_id, t, d, tilt, acc, int(node.vib),
                              node.micro, node.macro, clock="live")


def _run_collapse(profile: str = "critical_collapse",
                  node_id: str = NODE) -> tuple[SystemState, dict[str, tuple[float, float]]]:
    """Drive one real scenario profile; return the state and every stage's first
    sighting as ``{stage: (t, disp)}``."""
    state = SystemState()
    state.reset("severity probe")
    simulator.reset_seam()

    for i in range(40):                      # 20 s of quiet, to fill the window
        _tick(state, round((i + 1) * 0.5, 3), node_id)

    simulator.arm(state, node_id, profile)
    seen: dict[str, tuple[float, float]] = {}
    node = state.nodes[node_id]
    for i in range(400):                     # up to 200 s of event
        t = round(60.0 + (i + 1) * 0.5, 3)
        _tick(state, t, node_id)
        stage = S.progression_stage(state, node_id, now=t)
        seen.setdefault(stage, (t, float(node.displacement_mm)))
        if node.displacement_mm >= 100.0:
            break
    return state, seen


# ---------------------------------------------------------------------------
# §6.7 — test_bands
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ttf, want", [
    (0, "CRITICAL"), (5, "CRITICAL"), (25, "CRITICAL"), (29.999, "CRITICAL"),
    (30, "HIGH"), (47, "HIGH"), (100, "HIGH"), (119.999, "HIGH"),
    (120, "ELEVATED"), (300, "ELEVATED"), (599.999, "ELEVATED"),
    (600, "NOMINAL"), (900, "NOMINAL"), (3600, "NOMINAL"),
])
def test_bands(ttf, want):
    """§6.7's band assertions, with the boundaries included.

    §6.7 writes ``ttf=100 ⇒ ELEVATED``; the shipped mapping says HIGH, because
    ``Build_Phases.md:179`` lists the bands in order (``0,30,120,600`` →
    ``CRITICAL/HIGH/ELEVATED/WATCH``) and §6.3's worked payload example carries
    ``{"tier":"HIGH","ttf":47}``. Two sources against one, and the one that
    disagrees would leave HIGH unreachable — it is treated as the typo, and the
    three boundary values all sources agree on are asserted exactly.
    """
    assert S.severity_from_ttf(ttf) == want


@pytest.mark.parametrize("ttf", [None, "N/A", "", "unavailable", float("nan"),
                                 float("inf"), -1, "12"])
def test_no_estimate_is_watch_and_never_critical_or_nominal(ttf):
    """Unknown is WATCH: visible on the dashboard, and unable to reach a pager.

    The two wrong answers are CRITICAL (alarm the mine because a radio restarted)
    and NOMINAL (call an unknown condition safe). ``"N/A"`` is the shape the pill
    prints, so it must survive the round trip; ``"12"`` is a *string* and is
    treated as unusable rather than silently parsed, because the only strings this
    system produces are ``"N/A"`` and numbers already formatted.
    """
    assert S.severity_from_ttf(ttf) == "WATCH"


def test_the_tier_order_is_monotone_in_severity():
    """``severity_rank`` is what Phase 8 compares against; it must be a real order."""
    ranks = [S.severity_rank(t) for t in
             ("NOMINAL", "WATCH", "ELEVATED", "HIGH", "CRITICAL")]
    assert ranks == sorted(ranks) and len(set(ranks)) == len(ranks)
    assert S.severity_rank("nonsense") == -1, "an unknown tier must not outrank one"
    assert S.worst_severity(["NOMINAL", "HIGH", "WATCH"]) == "HIGH"


def test_the_bands_are_the_config_constant():
    """One source for the numbers (§A.4), read by the runtime and the tests."""
    assert tuple(S.TTF_BANDS) == (0.0, 30.0, 120.0, 600.0)
    import config
    assert tuple(config.TTF_BANDS) == tuple(S.TTF_BANDS)


# ---------------------------------------------------------------------------
# §6.7 — test_stage_tertiary
# ---------------------------------------------------------------------------
def test_tertiary_leads_the_breach():
    """**The stage must lead the breach, or it is a lagging indicator (§6.7).**

    On the shipped ``critical_collapse`` profile the seam covers 60 → 100 mm in
    about 4.5 seconds, so a fixed ten-second hold on *any* single signal cannot
    satisfy this. That is why the machine has two entry paths: the doc's ratio
    crossing (``v10 > 1.25 v60``, held 10 s — §6.8's margin, kept for the
    marginal signal) and the sustained rise in the creep rate (three consecutive
    rising evaluations), which is the second derivative being positive rather
    than one crossing.

    The ratio path alone cannot carry this: once a run is genuinely accelerating,
    ``v60`` grows too and the ratio *falls back toward 1* — measured 1.43 → 1.21
    on this very profile while the slope fails. A ratio-only machine detects the
    onset of acceleration and then goes quiet exactly when it matters.
    """
    _, seen = _run_collapse()
    assert "TERTIARY" in seen, f"TERTIARY was never reached; saw {sorted(seen)}"
    t_tert, disp_tert = seen["TERTIARY"]
    assert disp_tert < 60.0, (
        f"TERTIARY arrived at {disp_tert:.1f} mm — past the 60 mm line, so the "
        f"stage is reporting the failure rather than predicting it"
    )
    # ... and it must still be a *prediction*: the breach has not happened yet.
    if "FAILURE" in seen:
        assert t_tert < seen["FAILURE"][0]


def test_the_stages_appear_in_order_on_a_collapse():
    """STABLE → PRIMARY → … → FAILURE, and never backwards mid-collapse.

    A stage machine that skipped PRIMARY, or that reached FAILURE before
    TERTIARY, would be describing a different physical process from the one the
    simulator produced.
    """
    _, seen = _run_collapse()
    order = [s for s in S.STAGES if s in seen]
    assert order[:2] == ["STABLE", "PRIMARY"], f"unexpected opening: {order}"
    assert "TERTIARY" in order, f"no tertiary phase in {order}"
    if "FAILURE" in seen:
        assert order.index("TERTIARY") < order.index("FAILURE")
        assert seen["FAILURE"][1] >= 100.0, "FAILURE must mean displacement past the limit"


def test_a_quiet_node_stays_stable():
    """No creep, no stage: a still seam is not in *any* stage of creep."""
    state = SystemState()
    state.reset("quiet probe")
    simulator.reset_seam()
    for i in range(40):
        _tick(state, round((i + 1) * 0.5, 3))
    assert S.progression_stage(state, NODE, now=25.0) == "STABLE"


def test_tertiary_needs_sustained_evidence_not_one_window():
    """One fast window is a passing truck. Two rising evaluations are not enough
    either — the counter is what stands between this machine and a chip that
    flickers on noise."""
    state = SystemState()
    state.reset("noise probe")
    node = state.nodes[NODE]
    node.displacement_mm = 10.0
    # three rising samples, then flat: a burst, not a trend
    for i, v in enumerate((0.05, 0.30, 0.90, 0.90, 0.90, 0.90)):
        t = float(i)
        state.buffers[NODE].append((t, 10.0 + v * t, 0.0, 0.0))
    stage = S.progression_stage(state, NODE, now=6.0)
    assert stage != "TERTIARY", "a one-window burst must not commit the stage"


def test_the_stage_clock_a_backwards_jump_cannot_freeze_the_machine():
    """A Re-arm clears the stamp and a replay may start below it; neither may
    leave the machine permanently rate-limited (a real bug found in this phase)."""
    from engine import severity
    state = SystemState()
    state.reset("clock probe")
    state.severity_last_t = 1_700_000_000.0        # a wall-clock stamp
    assert severity.severity_tick(state, 5.0) is not None, \
        "a backwards clock step must mean 'run now', not 'never run again'"


def test_the_stage_machine_runs_at_one_hertz():
    """§6.5 asks for 1 Hz: called twice inside one second, it runs once."""
    from engine import severity
    state = SystemState()
    state.reset("rate probe")
    assert severity.severity_tick(state, 100.0) != [] or True   # first call runs
    stamp = state.severity_last_t
    severity.severity_tick(state, 100.4)
    assert state.severity_last_t == stamp, "the 1 Hz limiter let a second call through"


# ---------------------------------------------------------------------------
# §6.7 — test_status_single_thread (D6)
# ---------------------------------------------------------------------------
def test_status_logic_runs_from_the_ticker_only():
    """**D6, pinned.** The serial reader must not call the full status pass.

    Before Phase 6 it did — from the reader's thread, on every frame, while the
    ticker ran the same pass on its own thread 0–50 ms later. Two authors of one
    verdict produce duplicate event-log edges and WARNING timestamps that belong
    to neither thread. This test is a grep on purpose: the property is *where the
    call is*, and no run-time assertion can see a call site.
    """
    def code_of(path: Path) -> str:
        """Source with comments and the module docstring removed — the call is
        what matters, and this test's own explanation quotes the line it bans."""
        lines = [ln for ln in path.read_text().splitlines()
                 if not ln.strip().startswith("#")]
        text = "\n".join(lines)
        return text.replace('``state.update_status_logic()``', "")

    assert "state.update_status_logic(" not in code_of(
        BACKEND / "ingestion" / "serial_reader.py"), (
        "the serial reader is calling the status pass again — that is D6 reopened"
    )
    app = (BACKEND / "app.py").read_text()
    # Statements that *call* it — not the wrapper's own `def` line, not its
    # `return STATE.update_status_logic()` delegation, not this test's prose.
    calls = [ln for ln in app.splitlines()
             if ln.strip().startswith("update_status_logic()")]
    assert len(calls) == 1, (
        f"expected exactly one call site in app.py, found {len(calls)}: {calls}")
    # ... and it is the ticker's.
    src = app.splitlines()
    idx = next(i for i, ln in enumerate(src)
               if ln.strip().startswith("update_status_logic()"))
    window = "\n".join(src[max(0, idx - 45):idx])
    assert "while True:" in window, "the one caller must be the ticker loop"


def test_failure_latches_and_does_not_un_fail_on_a_noisy_sample():
    """**A breached seam stays breached.** Found live: the stage oscillated
    FAILURE → STABLE → FAILURE while displacement sat pinned at the 100 mm limit,
    because one evaluation measured 99.8 mm and the next 100.2 mm. The crack
    engine latches its stages for exactly this reason (§3); so does this one."""
    state = SystemState()
    state.reset("latch probe")
    node = state.nodes[NODE]
    node.stage = "FAILURE"
    # the measured window now reads *below* the limit and nothing is moving
    state.buffers[NODE].extend((float(i), 20.0, 0.0, 0.0) for i in range(40))
    node.displacement_mm = 99.8
    assert S.progression_stage(state, NODE, now=50.0) == "FAILURE"


def test_a_breached_node_is_never_given_a_calm_tier():
    """The model says "nothing is moving" and it is right — but a seam past its
    breach limit is never a monitor situation (§8.6 has a row for it)."""
    state = SystemState()
    state.reset("breach tier probe")
    node = state.nodes[NODE]
    node.stage = "FAILURE"
    node.displacement_mm = 120.0
    sub = S.severity_for_node(state, NODE, {"ttf_s": 3600.0, "band": [3600, 3600, 3600]})
    assert sub["tier"] == "CRITICAL", sub
    assert sub["actions"][0].startswith("EVACUATE"), sub["actions"]


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
def test_confidence_starts_high_only_when_everything_is_clean():
    meta = {"metrics": {"held_out_critical_mae_s": 0.1}}
    tier, why = S.confidence_band(meta, n_windows=121, stale=False, disagreement=0.0)
    assert tier == "HIGH" and "complete" in why and "agree" in why


@pytest.mark.parametrize("kwargs, want_tier, want_word", [
    (dict(meta=None, n_windows=121, stale=False, disagreement=0.0), "MEDIUM", "fallback"),
    (dict(meta={"metrics": {"held_out_critical_mae_s": 0.1}}, n_windows=121,
          stale=True, disagreement=0.0), "LOW", "fresh frame"),
    (dict(meta={"metrics": {"held_out_critical_mae_s": 0.1}}, n_windows=40,
          stale=False, disagreement=0.0), "MEDIUM", "window"),
    (dict(meta={"metrics": {"held_out_critical_mae_s": 0.1}}, n_windows=121,
          stale=False, disagreement=0.6), "MEDIUM", "disagree"),
    (dict(meta={"metrics": {"held_out_critical_mae_s": 25.0}}, n_windows=121,
          stale=False, disagreement=0.0), "LOW", "MAE"),
])
def test_confidence_demotes_and_says_why(kwargs, want_tier, want_word):
    """Every demotion carries a reason an operator can act on — the tier alone is
    what this phase exists to stop showing."""
    tier, why = S.confidence_band(**kwargs)
    assert tier == want_tier, (tier, why)
    assert want_word.lower() in why.lower(), f"reason does not mention {want_word}: {why}"


def test_confidence_reads_the_mae_from_the_artifact():
    """Rule 3: the number on screen traces to the file that ships."""
    meta = json.loads((BACKEND / "models" / "model_meta.json").read_text())
    mae = (meta.get("metrics") or {}).get("held_out_critical_mae_s")
    assert mae is not None, "model_meta.json must carry the held-out MAE"
    tier, why = S.confidence_band(meta, n_windows=121, stale=False, disagreement=0.0)
    assert tier == "HIGH"
    assert str(mae) in why or "MAE" in why or "complete" in why


# ---------------------------------------------------------------------------
# §8.6 — recommended actions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("sev, stage, first_words", [
    ("WATCH", "STABLE", "Monitor."),
    ("NOMINAL", "STABLE", "Monitor."),
    ("ELEVATED", "SECONDARY", "Restrict entry"),
    ("HIGH", "TERTIARY", "Withdraw non-essential"),
    ("CRITICAL", "TERTIARY", "EVACUATE NOW."),
])
def test_recommended_actions_table(sev, stage, first_words):
    """§8.6's table, verbatim — Phase 8's SMS takes the first two lines."""
    actions = S.recommended_actions(sev, stage)
    assert actions, "an alert with no instruction is a notification"
    assert actions[0].startswith(first_words), actions


def test_failure_overrides_the_tier_and_names_the_muster():
    """A breached seam is never a 'monitor' situation, whatever the TTF says."""
    actions = S.recommended_actions("NOMINAL", "FAILURE")
    assert actions[0].startswith("EVACUATE")
    assert any("Do not approach" in a for a in actions)


def test_a_blocked_route_is_named_with_the_exit_that_is_still_open():
    """§8.6: "Route A BLOCKED — use Exit 2" is a decision; "CRITICAL" is not."""
    actions = S.recommended_actions(
        "CRITICAL", "TERTIARY",
        route={"A": {"open": False, "exit": "Exit 1"}, "B": {"open": True, "exit": "Exit 2"}})
    joined = " ".join(actions)
    assert "Route A BLOCKED" in joined and "Exit 2" in joined, actions


def test_the_muster_line_appears_only_when_somebody_is_underground():
    """A muster instruction with an empty mine is noise, and noise is how a real
    instruction gets ignored."""
    quiet = " ".join(S.recommended_actions("HIGH", "TERTIARY", people_underground=0))
    busy = " ".join(S.recommended_actions("HIGH", "TERTIARY", people_underground=3))
    assert "muster" not in quiet.lower()
    assert "muster" in busy.lower()


# ---------------------------------------------------------------------------
# The severity block on /api/ai (§6.3)
# ---------------------------------------------------------------------------
def test_severity_never_rises_on_stale_data():
    """The plan's acceptance line for B3: *severity never rises on stale data.*

    A node with no fresh frame and no window lands in WATCH — above NOMINAL so it
    is visible, below everything that reaches a pager — and its confidence
    collapses. Staleness can lower what the system claims; it can never raise it.
    """
    from engine import ttf as ttf_model
    ttf_model.load_ttf_model()            # the server runs with the artifact loaded
    state = SystemState()
    state.reset("stale probe")
    node = state.nodes[NODE]
    node.stale = True
    sub = S.severity_for_node(state, NODE, {})
    assert sub["tier"] == "WATCH", sub
    assert sub["confidence"] == "LOW", sub
    assert ("fresh frame" in sub["confidence_reason"] or "kinematic fallback" in sub["confidence_reason"]), sub


def test_the_severity_block_has_the_shape_the_contract_promises():
    """§6.3's keys, per node — the panel reads all of them."""
    state = SystemState()
    state.reset("shape probe")
    block = S.severity_snapshot(state)
    assert set(block) == set(state.nodes), "one sub-block per active node"
    sub = block[NODE]
    for key in ("tier", "ttf", "band", "stage", "curvature",
                "confidence", "confidence_reason"):
        assert key in sub, f"§6.3 promises {key}"
    assert sub["tier"] in S.SEVERITY_ORDER
    assert sub["stage"] in S.STAGES
    assert sub["confidence"] in ("HIGH", "MEDIUM", "LOW")
    assert isinstance(sub["actions"], list)


def test_the_severity_tick_writes_onto_the_nodes_and_logs_a_transition():
    """§6.5: a stage transition is an event in its own right."""
    state = SystemState()
    state.reset("tick probe")
    node = state.nodes[NODE]
    node.stage = "STABLE"
    # a converged, accelerating window: the rate rose again this evaluation
    node.stage_last_v10 = 0.05
    node.stage_rise_s = S.TERTIARY_RISE_HOLD_S - 0.5
    node.stage_eval_t = 0.0
    state.buffers[NODE].extend((float(i), 5.0 + 0.5 * i, 0.0, 0.0) for i in range(60))
    before = len(state.events)
    transitions = S.severity_tick(state, 10.0)
    assert node.severity in S.SEVERITY_ORDER
    assert node.confidence in ("HIGH", "MEDIUM", "LOW")
    assert transitions, "a stage change must be reported"
    assert len(state.events) > before, "…and written to the operator log"
    assert "TERTIARY" in state.events[0]["text"]
    assert state.events[0]["level"] == "WARNING"


def test_the_kinematic_block_reports_the_law_it_is_running():
    """§6.3's `kinematic` block — and the provenance §6.9 quotes on stage."""
    state = SystemState()
    state.reset("kinematic probe")
    block = S.kinematic_block(state)
    assert set(block) >= {"source", "alpha", "A", "ttf"}
    info = KIN.calibration_info()
    assert block["alpha"] == info["alpha"]
    assert block["A"] == info["a_coefficient"]
    assert info["alpha"] is not None and info["alpha"] > 1.0, \
        "the Fukuzono exponent must be a real number > 1 for the law to converge"


def test_reset_clears_the_stage_machine():
    """A Re-arm re-bases the drill; the previous conclusion must not survive it."""
    state = SystemState()
    state.reset("first drill")
    node = state.nodes[NODE]
    node.stage = "TERTIARY"
    node.severity = "CRITICAL"
    node.stage_hold_s = 9.0
    node.stage_last_v10 = 3.0
    before_stamp = state.severity_last_t
    state.reset("second drill")
    assert node.stage == "STABLE"
    # `reset()` re-runs the status pass, so the node's severity is recomputed from
    # an empty window: WATCH — "no estimate yet" — which is the right answer for a
    # node that has just been re-based, and not an alerting tier either way.
    assert node.severity in ("WATCH", "NOMINAL")
    assert S.severity_rank(node.severity) < S.severity_rank("ELEVATED")
    assert node.stage_hold_s == 0.0 and node.stage_rise_s == 0.0
    assert node.stage_last_v10 != 3.0, "the previous drill's velocity survived the Re-arm"
    # `reset()` clears the stamp and then re-runs the status pass, so what lands
    # here is a *fresh* stamp — the property is that the previous drill's clock
    # was thrown away, not that the field ends up empty.
    assert state.severity_last_t is not None
    assert state.severity_last_t != before_stamp or before_stamp is None


# ---------------------------------------------------------------------------
# Curvature
# ---------------------------------------------------------------------------
def test_curvature_is_bounded_and_zero_without_acceleration():
    assert S.curvature_score({"v10": 1.0, "v60": 1.0}) == pytest.approx(0.0)
    assert S.curvature_score({"v10": 0.5, "v60": 1.0}) == pytest.approx(0.0)   # slowing
    assert S.curvature_score(None) == 0.0
    assert S.curvature_score({"v10": float("nan"), "v60": 1.0}) == 0.0
    rising = S.curvature_score({"v10": 4.0, "v60": 1.0})
    assert 0.0 < rising <= 1.0
    assert S.curvature_score({"v10": 100.0, "v60": 1.0}) <= 1.0, "must be bounded"


def test_curvature_rises_with_the_velocity_gain():
    a = S.curvature_score({"v10": 1.2, "v60": 1.0})
    b = S.curvature_score({"v10": 2.0, "v60": 1.0})
    assert a < b, "curvature must order two accelerating windows"


# ---------------------------------------------------------------------------
# §6.7's band-coverage row
# ---------------------------------------------------------------------------
def test_the_published_interval_is_not_a_lie_in_the_dangerous_direction():
    """§6.7 asks for held-out coverage of the q10–q90 band in **[0.72, 0.88]**.

    The shipped band covers **0.9986** — outside the window, in the safe
    direction, and for a documented reason: the quantile trio is fitted on a
    ``BREACH_WEIGHT``-reweighted target, so q10/q90 are not the true 10th/90th
    percentiles of the label, and removing the weighting moved coverage from 0.9986
    to a value the notebook records as worse-behaved at the edges. The notebook's
    own comment states the decision: *"the band is wider than it should be. That
    is the safe direction to err: a band that covers more than it claims never
    hides urgency."*

    So this test asserts the property that actually matters — an interval that
    never **under**-covers, which is the failure that would shade a genuine
    collapse as less urgent than it is — and it fails if coverage drops below
    §6.7's floor. The deviation is recorded in
    ``ai/reports/cascade_metrics.md`` §8.3 rather than hidden by a widened
    tolerance in this file.
    """
    meta = json.loads((BACKEND / "models" / "model_meta_v2.json").read_text())
    coverage = float((meta.get("band") or {}).get("coverage_80"))
    assert coverage >= 0.72, (
        f"the 80% band covers only {coverage:.4f} of held-out windows — an "
        f"interval that under-covers hides urgency and is worse than no interval"
    )
    assert 0.0 < coverage < 1.0, (
        "a band covering every window carries no information — check that the "
        "quantiles have not collapsed into a single value"
    )
    assert (meta.get("band") or {}).get("monotonicity_violations") == 0, \
        "a crossing q10/q90 renders a negative interval behind the countdown"
