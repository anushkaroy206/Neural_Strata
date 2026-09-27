#!/usr/bin/env python3
"""
Neural Strata — the cascade and its safety firewall (B10 · B11, Phase 5)
=========================================================================

**Model 1 may control how often we compute and how loudly we shout. It may not
control whether we warn.**

That sentence is the entire design, and everything in this file exists to make it
true by construction rather than by good intentions. ``tests/test_cascade_safety.py``
was written *before* this module — the plan asks for that explicitly, and the
reason is that a test written first tests the claim while a test written second
tends to test the implementation's shape.

Why the reference architecture is inverted
-------------------------------------------
§5.2 of the build doc describes the pitch's cascade as a **hard gate**: Model 1
says Pass, or Model 2 never runs. Neural Strata runs it the other way round, and
the argument is one sentence:

    A gatekeeper classifier can only ever remove information from the safety
    system, and every removal it gets wrong is a fatality. So Model 1 is given
    authority over how often we compute and how loudly we shout, and zero
    authority over whether we warn.

The asymmetry is real and it is not a rhetorical device. A classifier that may
*suppress* an alarm can only be wrong in one direction — the silent one — and the
cost of that direction is unbounded. A classifier that may only *add* alarms is
wrong in the direction of a nuisance call, whose cost is bounded and known. Two
systems with identical accuracy can differ enormously in expected harm, purely by
which side of that line they sit on.

So the firewall below is not a safety net bolted onto a gate. There is no gate. M2
always runs (§5.2's table routes but never disables it), and the firewall is the
last statement about what the operator is told — it reads the verdicts and can
only ever *raise* the tier.

The two ladders, kept apart
----------------------------
There are two severity vocabularies in this system and confusing them would be
easy, so they are named differently on purpose:

* **Alert tier** — this module. ``NOMINAL < ADVISORY < ELEVATED < IMMINENT``.
  How loudly the system is speaking *right now*.
* **Severity tier** — Phase 6 (``engine/severity.py``). ``WATCH / ELEVATED /
  HIGH / CRITICAL``, derived from the TTF bands ``(0, 30, 120, 600)``. How much
  time is left, as a description of the event.

A tier is a decision; a severity is a measurement. They move together, but a
disagreement between the models moves the tier and does not move the severity,
which is exactly why they are not the same field.

``UNAVAILABLE`` vs ``UNKNOWN`` — the one distinction worth spelling out
-----------------------------------------------------------------------
These look alike and behave oppositely:

* ``UNKNOWN`` is a label **Model 1 produced**. M1 ran, looked at the window, and
  could not tell. That is information — specifically, information that we do not
  know whether this is geological — so it escalates.
* ``UNAVAILABLE`` is the state of **having no classifier**. M1 did not run. This
  is §5.13's documented rollback: delete the artifact and the system degrades to
  exactly the Phase 4 behaviour it already shipped and rehearsed.

Escalating on ``UNAVAILABLE`` would mean the system alerts louder when a model is
missing than when it is present and reports a real crack, which inverts the entire
point. Degrading to Phase 4 is the correct and tested response. The distinction is
also what makes the monotonicity property in the test file hold: ``UNAVAILABLE``
is the *floor* — the tier with no Model 1 at all — and every label M1 can emit is
checked to produce a tier at or above it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import config

__all__ = [
    "CRITICAL_BAND_S", "MODES", "TIERS", "CascadeDecision", "firewall_check",
    "route_decision", "decide", "m2_hz_for", "cascade_tick", "last_decisions",
    "cascade_status", "reset",
]

#: Rule 2's band, in seconds. **The most important number in the repository.**
#: Any TTF at or below this forces an alert regardless of Model 1's opinion.
CRITICAL_BAND_S: float = float(config.CRITICAL_BAND_S)

#: ``advisory`` is shipped. ``gate`` exists so the off-line experiment can measure
#: what suppression would have cost — and so a judge who asks to see the reference
#: architecture can be shown it, live, with one environment variable. It is
#: **still subject to the firewall**: a gate may stop *computing*, never *forget*.
MODES = ("advisory", "gate")

#: Weakest first. ``route_decision`` must never return a lower index with more
#: information than it had without it — see the monotonicity test.
TIERS = ("NOMINAL", "ADVISORY", "ELEVATED", "IMMINENT")

#: The M1 labels that escalate on their own, with no TTF pressure at all.
#: ``UNKNOWN`` is here deliberately: "I cannot tell" is not "all clear".
_ESCALATING_LABELS = ("STRUCTURAL", "UNKNOWN")

#: The label meaning "M1 did not run". Distinct from ``UNKNOWN``; see the module
#: docstring. Also the key into ``config.M2_HZ``.
M1_UNAVAILABLE = "UNAVAILABLE"


# ---------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------

@dataclass
class CascadeDecision:
    """Everything the system concluded about one window, in one place.

    Carries the *inputs* alongside the verdict (``m1_label`` next to ``m2_ttf``,
    both untouched) so that a disagreement stays visible all the way to the
    dashboard. A system that resolves a conflict before displaying it has decided
    the operator does not need to know something true.
    """

    t: float = 0.0
    node: str = ""
    m1: dict[str, Any] | None = None
    m1_label: str = M1_UNAVAILABLE
    m2_ttf: float | None = None
    m2_band: tuple[float, float] | None = None
    kin_ttf: float | None = None
    # **Unset, not "NOMINAL".** These default to the empty string so that a
    # decision which never went through ``decide()`` — a hand-built one from a
    # replay, a test, or a future API caller — cannot present itself as all-clear.
    # It read ``"NOMINAL"`` until the scheduler tests caught it: ``route_decision``
    # short-circuits when the requested mode matches ``dec.mode``, so a hand-built
    # decision with a 0-second TTF returned NOMINAL while the *same* decision
    # asked about the other mode returned IMMINENT. A silent all-clear is the one
    # wrong answer this class must not be able to give, so the default now has to
    # be filled in by ``_compute_tier`` and ``route_decision`` recomputes whenever
    # it is missing.
    tier: str = ""
    route: str = ""
    m2_hz: float = 2.0
    disagreement: bool = False
    firewalled: bool = False
    reason: str = ""
    mode: str = "advisory"
    extra: dict[str, Any] = field(default_factory=dict)

    # -- convenience, used by app.py's pill and by the tests ----------------

    @property
    def disruption_index(self) -> float:
        return float((self.m1 or {}).get("disruption_index") or 0.0)

    @property
    def structural_prob(self) -> float:
        return float((self.m1 or {}).get("structural_prob") or 0.0)

    @property
    def ttf(self) -> float | None:
        """The best TTF available, in the fallback chain's order."""
        return self.m2_ttf if self.m2_ttf is not None else self.kin_ttf

    def as_api(self) -> dict[str, Any]:
        """The ``cascade`` block of ``/api/ai`` (§5.3)."""
        return {
            "mode": self.mode,
            "m2_hz": self.m2_hz,
            "disagreement": self.disagreement,
            "reason": self.reason,
        }


# ---------------------------------------------------------------------------
# Rule 2 — the firewall
# ---------------------------------------------------------------------------

def firewall_check(dec: CascadeDecision) -> bool:
    """Rule 2. True ⇒ this decision REQUIRES an alert, whatever Model 1 said.

    Three independent reasons to shout, and any one of them is sufficient:

    1. **Model 2 says the roof has seconds left.** A contradiction is never
       allowed to silence us. This is the whole point: M1 being confidently wrong
       must not be the reason nobody was warned.
    2. **The physics fallback says so.** ``v/a`` or Fukuzono have *equal*
       authority to the trained model, because the fallback is the same estimator
       the model was trained to imitate. If the artifact is missing the system
       becomes less precise, never less safe.
    3. **Model 1 itself says the event is structural, or that it cannot tell.**
       An unclassifiable window escalates; see the module docstring.

    Note what is *not* here: no branch reads the mode, no branch can be disabled
    by a flag, and no branch can lower a tier. That is deliberate — the function
    is monotone by having no downward path at all, which is easier to verify than
    a promise that it will not take one.
    """
    if dec.m2_ttf is not None and dec.m2_ttf <= CRITICAL_BAND_S:
        return True
    if dec.kin_ttf is not None and dec.kin_ttf <= CRITICAL_BAND_S:
        return True
    if dec.m1 is not None and dec.m1_label in _ESCALATING_LABELS:
        return True
    return False


def m2_hz_for(label: str) -> float:
    """§5.2's routing table: how often Model 2 runs, given M1's verdict.

    **This is the only function in the system that a classifying error can make
    cheaper.** An error here costs latency and precision; it cannot cost an
    alert, because the firewall does not read this number. Keeping the two
    decisions in separate functions — rather than deriving one from the other — is
    what makes that guarantee inspectable.
    """
    key = label if label in config.M2_HZ else "UNKNOWN"
    return float(config.M2_HZ[key])


def _compute_tier(dec: CascadeDecision, mode: str) -> str:
    """The ladder, weakest branch first, so every branch is reachable."""
    fired = firewall_check(dec)

    if not fired:
        # No reason to escalate. Is there anything to report?
        if dec.m1_label == "ENVIRONMENTAL":
            # A loader drove past. Not a collapse — but not nothing either, and
            # §5.12 requires the nuisance call to be *made and labelled*, not
            # swallowed. Reporting NOMINAL would satisfy "did not alarm" while
            # discarding the narration this phase exists to produce.
            return "ADVISORY"
        return "NOMINAL"

    # The firewall fired. How loudly? Driven by *time*, not by the model that
    # noticed: a confirmed structural event with an hour to spare is a different
    # situation from one with twenty seconds, and that difference is a TTF.
    for ttf in (dec.m2_ttf, dec.kin_ttf):
        if ttf is not None and ttf <= CRITICAL_BAND_S:
            return "IMMINENT"
    return "ELEVATED"


def _reason_for(dec: CascadeDecision, tier: str, mode: str) -> str:
    """A sentence an operator could act on. Never a bare tier name."""
    parts: list[str] = []

    if dec.m1 is None:
        parts.append("pattern model unavailable — degraded to Model 2 and the "
                     "kinematic fallback")
    else:
        parts.append(f"pattern: {dec.m1_label.lower()}")
        di = dec.disruption_index
        parts.append(f"disturbance {di:.2f}")
        if dec.m1_label == "STRUCTURAL":
            parts.append(f"structural probability {dec.structural_prob:.2f}")

    if dec.m2_ttf is not None:
        parts.append(f"M2 TTF {dec.m2_ttf:.0f} s")
    elif dec.kin_ttf is not None:
        parts.append(f"kinematic TTF {dec.kin_ttf:.0f} s")
    else:
        parts.append("no TTF estimate")

    if dec.disagreement:
        parts.append("**MODELS DISAGREE — the alert stands**")

    if mode == "gate":
        parts.append("gated mode: M2 rate follows the pattern verdict only")

    return " · ".join(parts)


def decide(m1: dict[str, Any] | None = None, m2_ttf: float | None = None,
           kin_ttf: float | None = None, mode: str | None = None,
           node: str = "", t: float | None = None,
           m2_band: tuple[float, float] | None = None) -> CascadeDecision:
    """Fuse the two models into one decision. **Pure** — no I/O, no state.

    Pure on purpose: the safety tests drive this function directly over its whole
    input space, which is only possible because there is nothing else in the way.
    A decision function that reads a global cannot be exhaustively verified, and
    an unverifiable safety property is a preference.
    """
    mode = mode or config.CASCADE_MODE
    if mode not in MODES:
        mode = "advisory"

    if m1 is None:
        label = M1_UNAVAILABLE
    else:
        label = str(m1.get("label") or "UNKNOWN")

    dec = CascadeDecision(
        t=float(t if t is not None else time.time()),
        node=node,
        m1=m1,
        m1_label=label,
        m2_ttf=m2_ttf,
        m2_band=m2_band,
        kin_ttf=kin_ttf,
        mode=mode,
        m2_hz=m2_hz_for(label),
    )

    dec.firewalled = firewall_check(dec)
    dec.tier = _compute_tier(dec, mode)
    dec.route = dec.tier

    # **A disagreement is a fact, not an error to be resolved.** M1 says the
    # ground is fine, M2 says it has seconds left. Both are reported. The failure
    # mode this prevents is the quiet one: a system that picks the number that
    # looks tidier and thereby hides a contradiction from the person who has to
    # decide whether to clear the tunnel.
    #
    # The predicate lives in ``disagreement_flag`` (§5.8) and is *called* here so
    # the flag on the decision and the published rule are one implementation. A
    # second copy of these three clauses is how a dashboard ends up saying "no
    # disagreement" while the alert log says the models contradict each other.
    dec.disagreement = disagreement_flag(dec)

    dec.reason = _reason_for(dec, dec.tier, mode)
    return dec


def disagreement_flag(dec: CascadeDecision) -> bool:
    """M1 says environment, M2 says failure ⇒ escalate and flag (§5.8).

    The contradiction is *reported*, never resolved: the label stays what M1
    said, the countdown stays what M2 said, and the operator is told the two
    disagree. Silencing either one is the suppression the firewall exists to
    prevent, and averaging them would be worse — an average of "a truck" and
    "eight seconds" is not a number, it is a tidy-looking falsehood.

    Only ``QUIET`` and ``ENVIRONMENTAL`` can disagree. A ``STRUCTURAL`` M1 beside
    a short M2 is not a contradiction — it is two sensors agreeing, which is the
    case the alarm is *for*; ``UNKNOWN`` likewise escalates through the firewall
    rather than here.
    """
    return bool(
        dec.m1 is not None
        and dec.m1_label in ("QUIET", "ENVIRONMENTAL")
        and dec.m2_ttf is not None
        and dec.m2_ttf <= CRITICAL_BAND_S
    )


def explain_decision(dec: CascadeDecision) -> str:
    """One line for the alert log: what happened, in the order it was decided.

    Deliberately built from the same fields the dashboard renders, and finished
    with :func:`_reason_for`'s sentence rather than a second version of it, so
    the log line and the panel cannot describe the same decision differently.

    Sentence-shaped and not a dict because its destination is a log a human reads
    at 3 a.m. and the runbook greps. The machine-readable form is
    :meth:`CascadeDecision.as_api`.
    """
    ttf = "no TTF estimate" if dec.m2_ttf is None else (
        f"M2 median {dec.m2_ttf:.0f} s"
        + (f" (q10–q90 {dec.m2_band[0]:.0f}–{dec.m2_band[2]:.0f} s)"
           if dec.m2_band else ""))
    kin = "" if dec.kin_ttf is None else f", kinematic fallback {dec.kin_ttf:.0f} s"
    return (f"{dec.node or 'node'}: MODEL 1 {dec.m1_label} · {ttf}{kin} · "
            f"tier {dec.tier or route_decision(dec)}"
            + (" · FIREWALL ESCALATION" if dec.firewalled else "")
            + f" · {dec.reason or _reason_for(dec, dec.tier, dec.mode)}")


def route_decision(dec: CascadeDecision, mode: str | None = None) -> str:
    """The alert tier for a decision. Recomputes if the mode is overridden.

    ``route_decision(dec, mode="gate")`` must not be able to return lower than
    ``route_decision(dec)``; the gate changes *compute cadence*, and the firewall
    is mode-independent — see ``test_m1_can_never_suppress_m2``, which the plan
    originally specified for ``advisory`` only and which is run in both modes here
    because the gated form is the one that would otherwise silence a collapse.
    """
    if dec.tier and (mode is None or mode == dec.mode):
        return dec.tier
    # Recomputed when the tier is missing as well as when the mode differs. The
    # cost is a few microseconds on a decision that was never through ``decide``;
    # the alternative is trusting an empty field to mean "nothing to report".
    return _compute_tier(dec, (mode or dec.mode) if (mode or dec.mode) in MODES
                         else "advisory")


# ---------------------------------------------------------------------------
# The live tick
# ---------------------------------------------------------------------------

_DECISIONS: dict[str, CascadeDecision] = {}

#: Per-node time of the next scheduled M2 run (see ``inference_scheduler``).
_M2_NEXT: dict[str, float] = {}


def reset() -> None:
    _DECISIONS.clear()
    _M2_NEXT.clear()


def last_decisions() -> dict[str, CascadeDecision]:
    """The most recent decision per node — what ``/api/ai`` publishes."""
    return dict(_DECISIONS)


def _kinematic_ttf(state, node_id: str) -> float | None:
    """The physics fallback, in the plan's order of preference.

    ``engine/kinematic.py`` owns both estimators and the order: a Fukuzono 1/v
    estimate when the window converges, the legacy ``v/a`` when there is motion
    but no usable fit, ``None`` when there is nothing at all. It ships in Phase 5
    (§2 assigns ``kinematic.py`` to the Phase-5 row; §4.5 assigns B11 there with
    it), so this hook is live rather than dormant — and it is still written as a
    hook, because the module is optional on disk and the cascade must survive its
    absence exactly as it survives a missing model artifact.

    Two properties matter and neither is the caller's to check: the module
    applies its own `T_sanity` gate before returning, and it never invents a
    number — ``None`` routes to the alert tier, and a fake number does not.
    """
    try:
        from engine import kinematic as KIN            # Phase 6; may not exist yet
        fn = getattr(KIN, "kinematic_ttf", None)
        if callable(fn):
            v = fn(state, node_id)
            if v is not None:
                return float(v)
    except Exception:  # noqa: BLE001 - absence is a supported state
        pass

    try:
        node = state.nodes.get(node_id)
        if node is None:
            return None
        v = float(getattr(node, "velocity", 0.0) or 0.0)
        a = float(getattr(node, "acceleration", 0.0) or 0.0)
        if v <= 0 or a <= 0:
            return None
        est = float(v / a)
        # **The legacy `v/a` must pass the same gate as the model.**
        #
        # §6.1 is blunt about this estimator: *"`v/a` is 'time for velocity to
        # reach zero if deceleration is constant' — a plausible-sounding number
        # that is not a time-to-failure."* It is kept as a last resort because
        # something is better than nothing before a 60 s window exists, and
        # Phase 6 replaces it with the Fukuzono 1/v estimator the model was
        # actually trained against.
        #
        # Until then it needs the same discipline as M2, for the same reason and
        # with the same consequence. A loader pass gives `a` ≈ 4.6 m/s² against a
        # small `v`, so `v/a` collapses to a few seconds on a node that is about
        # to spring back — and unlike M2, an ungated fallback reached the firewall
        # on *every* environmental profile at once. The gate's rule (no motion
        # below 30 mm of displacement means no collapse) applies to a physics
        # estimate exactly as it does to a learned one. A number is not more
        # trustworthy for having been computed from a formula.
        disp = float(getattr(node, "displacement_mm", 0.0) or 0.0)
        if not T_sanity(v, disp, est):
            return None
        return est
    except Exception:  # noqa: BLE001
        return None


def T_sanity(v10: float, disp_now: float, ttf: float) -> bool:
    """The sanity gate's rule, applied to a kinematic estimate.

    Mirrors ``ttf.sanity_gate``'s condition — ``ttf < 30 s`` **and** ``v10 <
    0.5`` **and** ``disp_now < 30`` is an artefact, not a collapse — but takes raw
    values instead of a feature vector, because the fallback does not have a
    16-element row to index into. Kept deliberately as the same three numbers so
    the two gates cannot drift apart; ``tests/test_cascade_safety.py`` asserts
    both reject and accept the same cases.
    """
    if ttf >= 30.0:
        return True
    return not (v10 < 0.5 and disp_now < 30.0)


def inference_scheduler(state, now: float | None = None) -> list[str]:
    """Which nodes run M2 **this tick** (§5.8) — the 0.2 / 1 / 2 Hz table.

    Read-only: it reports who is *due* and never advances anything. The due-time
    is only moved by :func:`run_cascade` when M2 actually runs, so calling this
    from a dashboard, a test or a second thread cannot change the cadence. A
    scheduler with a side effect would make "how fast is this machine thinking?"
    unanswerable without running it.

    The rate itself comes from ``m2_hz_for``, which is a pure function of M1's
    label — ``test_scheduler_rates`` asserts that, and this function adds only a
    clock to it. The consequence worth stating plainly is the *safety* one: a node
    that starts looking suspicious is promoted to a faster cadence by its label,
    so the scheduler never has to be told that something is wrong, and M2 is
    already running at 2 Hz on the tick after M1's verdict changes.

    A node with no entry yet is due immediately — the first window of a node's
    life must always be measured, whatever the *previous* label was.
    """
    now = float(now if now is not None else time.time())
    due: list[str] = []
    for n_id in list(getattr(config, "ACTIVE_NODES", ()) or []):
        last = _DECISIONS.get(n_id)
        label = last.m1_label if last is not None else M1_UNAVAILABLE
        hz = m2_hz_for(label)
        if hz <= 0.0 or now >= _M2_NEXT.get(n_id, 0.0):
            due.append(n_id)
    return due


def run_cascade(state, nid: str, now: float | None = None,
                *, scheduled: bool | None = None) -> CascadeDecision | None:
    """One node, one tick: M1 always → M2 when scheduled → route (§5.8).

    §5.8 types this ``-> CascadeDecision``. It returns ``None`` in exactly two
    cases, both of which mean *no measurement exists* rather than *the verdict is
    fine*: the node's 60 s window is not full yet, or the 32-feature extraction
    refused the window. That narrowing is deliberate and is the same rule the
    phase enforces everywhere else — :class:`CascadeDecision` no longer defaults
    its tier to ``NOMINAL`` precisely so that a missing verdict cannot be rendered
    as an all-clear, and fabricating one here to satisfy a type annotation would
    put that default straight back.

    ``scheduled`` lets :func:`cascade_tick` compute the due list once per tick
    instead of per node; when it is ``None`` this function consults
    :func:`inference_scheduler` itself, so the two-argument form is the whole
    public behaviour.

    **M2 is carried forward between scheduled runs, and that is what makes the
    cadence safe to reduce.** A node measured at 0.2 Hz re-publishes the last
    credible TTF and band until its next slot rather than blanking: the firewall
    keeps seeing the number it saw, so a short TTF cannot be dropped merely
    because this tick was not a scheduled one. Nothing is *silently* stale either
    — ``extra["m2_ran"]`` records whether this tick's M2 number was measured or
    carried, and the age is visible in ``CascadeDecision.t``.
    """
    now = float(now if now is not None else time.time())
    mode = config.CASCADE_MODE if config.CASCADE_MODE in MODES else "advisory"

    try:
        from engine import features as F
        from engine import pattern as P
        from engine import ttf as T
    except Exception as exc:  # noqa: BLE001 - the cascade is optional; the app is not
        return CascadeDecision(node=nid, t=now, m1_label=M1_UNAVAILABLE, mode=mode,
                               reason=f"cascade unavailable: {type(exc).__name__}: {exc}")

    buf = (getattr(state, "buffers", {}) or {}).get(nid)
    if not buf or len(buf) < F.MODEL_BUF:
        # Not enough history to say anything. Publish the absence rather than a
        # default: an empty window is UNKNOWN, never flat.
        return None

    node_ids = list(getattr(config, "ACTIVE_NODES", ()) or [])
    peers = {p: list(state.buffers[p]) for p in node_ids
             if p != nid and state.buffers.get(p)}
    crack = _crack_dict(state, nid)
    feats = F.compute_extended_features(list(buf), peers=peers, crack=crack)
    if feats is None:
        return None

    m1 = P.classify(feats)

    if scheduled is None:
        scheduled = nid in inference_scheduler(state, now)

    prev = _DECISIONS.get(nid)
    m2_ttf: float | None = None
    m2_band: tuple[float, float] | None = None
    if scheduled:
        try:
            core16 = feats[:16]
            pred = T.predict_ttf(core16)
            # **The sanity gate is not optional, and leaving it out was a
            # real bug with a nasty signature.**
            #
            # ``ttf.predict_ttf`` returns a number for *any* window; the
            # gate is what decides whether that number is physically
            # credible. ``run_inference`` (the Phase 0 path this replaces)
            # applies it, and ``cascade_tick`` was written calling
            # ``predict_ttf`` directly — so an ungated Model 2 reached the
            # firewall.
            #
            # Model 2 has a measured 15% false-short rate on windows with no
            # motion at all: on the ``nominal`` profile — a tunnel sitting
            # perfectly still — its p10 prediction is 5.5 s. Every one of
            # those is ≤ CRITICAL_BAND, so Rule 2 does exactly what it is
            # supposed to do and escalates to IMMINENT. The firewall was not
            # wrong; it was being fed a number the project had already
            # decided to reject. §5.12's machinery row ("no tier above
            # ADVISORY") is only satisfiable with the gate in place.
            #
            # Rejecting is safe *because* of the gate's own rule: it fires
            # only when TTF < 30 s **and** v10 < 0.5 mm/s **and**
            # displacement < 30 mm. A genuine collapse has both motion and
            # displacement, so it can never be rejected — which is asserted
            # in tests/test_cascade_safety.py::test_the_gate_cannot_hide_a_real_collapse.
            if pred is not None and T.sanity_gate(core16, float(pred)):
                m2_ttf = float(pred)
                # The quantile trio's interval, when the artifact is on disk.
                # Carried on the decision rather than fetched by the
                # dashboard: the band has to describe the *same window* as
                # the countdown beside it, and a second call a moment later
                # would describe the next one.
                band = T.predict_ttf_band(core16)
                if band is not None:
                    m2_band = band
        except Exception:  # noqa: BLE001
            m2_ttf = None
            m2_band = None
        # The slot is consumed whether or not M2 produced a usable number: a
        # rejected prediction is a completed measurement, and retrying it every
        # tick would defeat the scheduler without changing the verdict.
        hz = m2_hz_for(m1.get("label") if isinstance(m1, dict) else None)
        _M2_NEXT[nid] = now + (1.0 / hz if hz > 0.0 else 0.0)
    elif prev is not None and prev.m2_ttf is not None:
        m2_ttf, m2_band = prev.m2_ttf, prev.m2_band

    # **The fallback chain, in the order §6.1 fixes it:**
    # cascade → M2 (fresh) → Fukuzono 1/v → `v/a` → "N/A".
    #
    # So the kinematic estimate is only *sourced* when M2 has nothing to
    # say. Computing it alongside M2 and letting the firewall consider
    # both looks more conservative and is actually wrong, because the
    # legacy `v/a` is not a usable number: §6.1 calls it "time for
    # velocity to reach zero if deceleration is constant — a
    # plausible-sounding number that is not a time-to-failure", and in
    # this simulator it is worse than that, dividing a velocity in mm/s by
    # an acceleration in m/s². The result is on the order of one second
    # for *any* moving node, so consulting it next to a healthy M2 answer
    # made every environmental profile look like an imminent collapse.
    #
    # The firewall is unchanged and still treats a short `kin_ttf` as
    # having equal authority — see
    # `test_the_physics_fallback_has_equal_authority`. What changes is
    # that the fallback is consulted when it is the *fallback*, which is
    # the only role §6.1 gives it. Phase 6 replaces the `v/a` rung with
    # the Fukuzono estimator the model was trained against.
    # **The cascade is now the only author of Model 2, so it must also be the
    # one that keeps ``ttf``'s freshness bookkeeping honest.**
    #
    # This closes a real bug, and it is the third of its family: after §5.9
    # deleted ``run_model_inference()`` the ticker called only ``cascade_tick``,
    # so nothing ever wrote ``MODEL_TTF`` or ``STATE["last_ok"]`` again. Both are
    # what ``ttf.model_fresh()`` reads, and ``ttf.model_status()`` turns that into
    # the ``status``/``note`` strings ``index.html`` displays — so the dashboard
    # sat on "WARMING UP · building the 60 s telemetry buffer" for the life of the
    # process while the cascade was fully live, and ``forecasts`` quietly fell
    # back to node status instead of the TTF horizon.
    #
    # A carried-forward value refreshes the clock too: the cascade *is* live, and
    # "the number is 3 s old" is precisely what the cadence promises. Freshness
    # is about the decision loop, not about whether this particular tick was a
    # scheduled M2 slot.
    if m2_ttf is not None:
        T.MODEL_TTF[nid] = float(m2_ttf)
        # **Wall clock, not the passed-in ``now``.** ``model_fresh()`` asks "has
        # this process completed a decision recently?", which is a fact about the
        # process, not about the simulated timeline — and ``now`` is simulated in
        # replay and in ``eval_cascade``. Stamping ``now`` there would make the
        # dashboard read WARMING UP for the whole of a replay, and the freshness
        # window would compare a simulated second against a wall-clock one.
        T.STATE["last_ok"] = time.time()

    kin = None if m2_ttf is not None else _kinematic_ttf(state, nid)
    dec = decide(m1=m1, m2_ttf=m2_ttf, kin_ttf=kin, mode=mode,
                 node=nid, t=now, m2_band=m2_band)
    # The 32 numbers this decision was actually made from, kept on the
    # decision itself. Not for the API (``as_api`` publishes the verdict,
    # not the inputs) — for **proving the live path and the training
    # corpus agree**. That parity is not checkable from the outside when
    # the features are a local variable, and it is the exact class of bug
    # this phase has already hit twice: the model is fitted on whatever
    # the generator produced, so any divergence here is a divergence
    # between the world the classifier learned and the world it is served.
    dec.extra["features"] = list(feats)
    dec.extra["m2_ran"] = bool(scheduled)
    # Novelty annotates a finished verdict. It is not an argument of decide().
    try:
        from engine import novelty as novelty_layer
        nv = novelty_layer.score(feats[:16])
    except Exception:
        nv = None
    if nv is not None:
        dec.extra["novelty"] = nv
    return dec


def cascade_tick(state, now: float | None = None) -> dict[str, CascadeDecision]:
    """One cascade pass over every running node. Called from the ticker.

    Order per node: extract the 32 features → ask Model 1 what kind of event this
    is → ask Model 2 when it fails → fuse. Every stage degrades independently, so
    a missing classifier, a missing regressor and a cold buffer all produce a
    coherent decision rather than an exception.

    ``now`` is passed in rather than read, so the tick is reproducible from a
    replay — the same reason the simulator's noise is deterministic.

    The loop is a loop and nothing else: the per-node work is :func:`run_cascade`
    and the cadence is :func:`inference_scheduler`, which is what §5.8 asks for
    and what makes either half testable without a ticker. The scheduler is
    consulted **once** per tick for the whole fleet, not once per node.
    """
    now = float(now if now is not None else time.time())

    try:
        from engine import features as F  # noqa: F401 - presence check only
    except Exception:  # noqa: BLE001 - the cascade is optional; the app is not
        return {}

    out: dict[str, CascadeDecision] = {}
    node_ids = list(getattr(config, "ACTIVE_NODES", ()) or [])
    due = set(inference_scheduler(state, now)) if node_ids else set()

    for n_id in node_ids:
        try:
            dec = run_cascade(state, n_id, now, scheduled=(n_id in due))
            if dec is None:
                continue
            out[n_id] = dec
            _DECISIONS[n_id] = dec
        except Exception as exc:  # noqa: BLE001 - one node must never stop the tick
            _DECISIONS.setdefault(n_id, CascadeDecision(
                node=n_id, t=now, m1_label=M1_UNAVAILABLE,
                reason=f"cascade error on {n_id}: {type(exc).__name__}: {exc}"))

    return out


def _crack_dict(state, node_id: str) -> dict[str, Any] | None:
    """The crack engine's public numbers, or ``None`` when it has none.

    ``None`` and not zeros: the extractor treats an absent crack channel as NaN
    (never measurable) and a *present* one as a measurement, so filling in zeros
    here would assert "the seam is shut" about a node whose gauge is simply not
    reporting. That is the most reassuring lie this instrument can tell.
    """
    try:
        from engine import crack as CK
        cs = CK.crack_state(state, node_id)
        if cs is None:
            return None
        node = state.nodes.get(node_id)
        return {
            "micro_rate": None if cs.rate_um_s is None else float(cs.rate_um_s),
            "macro_d30": None if cs.macro_d30 is None else float(cs.macro_d30),
            # `macro_total` is the opening since the node's own baseline — the
            # engine owns the baseline, so subtracting it here rather than
            # re-deriving it from `node.macro` keeps one definition of "since
            # baseline".
            #
            # **This block must stay byte-identical to
            # `tools/make_counterfactuals.py::_crack_dict`.** That is the whole
            # rule this phase keeps re-learning: the model is fitted on whatever
            # the generator produced, so any divergence here is a divergence
            # between the world the classifier learned and the world it is served.
            # It was written a third time from memory first, with a
            # `cs.macro_mm` attribute that does not exist — so `macro_total` was
            # `None` on the live path and a real number in training, silently.
            "macro_total": (None if cs.baseline_um is None or node is None
                            or node.macro is None
                            else float(node.macro) - float(cs.baseline_um) / 1000.0),
            "coherence": None if cs.coherence is None else float(cs.coherence),
        }
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Status, for /api/ai
# ---------------------------------------------------------------------------

def cascade_status() -> dict[str, Any]:
    """The fleet-level ``cascade`` summary plus the per-node picture.

    ``m2_hz`` is reported as the *slowest* rate across nodes, so the number on
    the dashboard is the cadence actually being sustained. Reporting a maximum
    would advertise a rate no node is achieving.
    """
    decs = list(_DECISIONS.values())
    if not decs:
        return {
            "mode": config.CASCADE_MODE,
            "m2_hz": m2_hz_for(M1_UNAVAILABLE),
            "disagreement": False,
            "reason": "no cascade decisions yet — waiting for a full 60 s window",
            "nodes": {},
        }
    worst = min(decs, key=lambda d: d.m2_hz)
    return {
        "mode": config.CASCADE_MODE,
        "m2_hz": worst.m2_hz,
        "disagreement": any(d.disagreement for d in decs),
        "reason": worst.reason if len(decs) == 1 else (
            f"{sum(1 for d in decs if d.disagreement)} node(s) disagreeing · "
            f"slowest cadence {worst.m2_hz} Hz on {worst.node}"),
        "nodes": {
            d.node: {
                "label": d.m1_label,
                "tier": d.tier,
                "disruption_index": d.disruption_index,
                "structural_prob": d.structural_prob,
                "ttf_s": d.ttf,
                "band": list(d.m2_band) if d.m2_band else None,
                "m2_hz": d.m2_hz,
                "disagreement": d.disagreement,
                "firewalled": d.firewalled,
                "novelty": (d.extra or {}).get("novelty"),
            }
            for d in decs
        },
    }
