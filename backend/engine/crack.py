"""
Neural Strata — B2 · the two-stage crack engine.
================================================

> *"A crack does two different things. Damage has started — that is the fine
> channel. The seam is opening — that is the coarse channel. And the gap in time
> between those two sentences is the entire early warning."*

Phase 3. §3.1 looked at the state and found no crack channel anywhere:
``sensor_telemetry`` had ``tilt_x, displacement_mm, acceleration`` and nothing
else. This module is the missing half of the H2 hardware — the half that turns
two numbers into a *decision about what kind of event is happening*.

**The two channels, in the units they are actually measured in**
-----------------------------------------------------------------

===========  ==========  =======================  ==============================
channel      unit        instrument               resolution
===========  ==========  =======================  ==============================
``micro``    µm          YZC-131 load cell in     ≈ 1 µm  (calibrated against a
                         tension across the seam  feeler gauge, H2)
                         (spring + HX711)
``macro``    mm          10 kΩ rotary pot on a    ≈ 24 µm (100 mm travel over
                         lever                    4096 ADC counts)
===========  ==========  =======================  ==============================

Both measure the **same physical quantity** — how far the seam has opened. They
differ by a factor of ~1000 in resolution, and that is the whole two-stage story:

* the fine channel crosses its threshold after ~1.5 µm of opening;
* the coarse channel crosses its own after 1.5 mm of opening = 1500 µm.

Same event, same motion, and the fine channel fires **first** — because it is
finer, not because anything invented a lead time for it. That sentence is the
demo: lean on the seam, watch ``MICRO`` move, wait, watch ``CRACK`` follow.

**The unit decision (Phase 3, taken deliberately, stated once)**
-----------------------------------------------------------------

The plan was written for a **BF350 foil gauge** bonded to the rock face and says
the fine channel is micro-strain, µε. The build uses a **load cell in tension**,
and a load cell measures *force*. Publishing force-derived numbers as µε would be
a unit no instrument on the rig produces — plausible and wrong, which is the worst
combination in a safety system.

So ``micro`` is redefined as **crack-mouth opening displacement in µm**: the force
through the spring, divided by the spring stiffness, calibrated end-to-end with a
feeler gauge (``NS_MICRO_UM_PER_COUNT`` in the firmware, H2). One physical
quantity, honestly named, measurable on the bench with a shim.

The thresholds keep the plan's physics. Its ``>15 µε/s`` was strain *rate*; strain
× gauge length = elongation, so at the plan's own 100 mm crack-mouth gauge that is
``15e-6 × 100 mm = 1.5 µm``, i.e. ``MICRO_RATE_THRESHOLD = 1.5 µm/s``. The number
changed because the unit did, not because the physics did.

**The five rules this module implements (§3.2)**
--------------------------------------------------

1. **Baseline, not absolute.** Raw counts are meaningless without a zero. 15 s of
   samples are averaged and subtracted; until then the channel reports ``null``
   and ``flags & FLAG_BASELINE_UNSETTLED`` is set. *Zero is taken from whoever
   has it* — the node settles its own gauge at boot (H2.4), so when the node says
   it is settled the server takes the node's zero and does not average a second
   one on top of it. Two zeros fighting is how a channel ends up reading half.
2. **Median-filtered rate.** Noise on a 24-bit ADC is not a signal. The rate over
   the 5 s window is computed between the *medians* of the two ends of the window,
   so one wild sample cannot manufacture a spike in either direction.
3. **Hysteresis, not a bare threshold.** A crossing must persist for 3 consecutive
   samples to raise, and must fall below **70 %** of the threshold to clear.
   §3.7's acceptance row has the two sides of that line swapped; the rule in
   §3.2.3 is what is implemented, and the test asserts **1 % either side of the
   clear line** rather than 69 %/71 % of a threshold it would contradict.
4. **Two named events, plus a third for the terminal case** — the plan's own
   wording: ``Micro-crack initiated`` and ``Crack propagating rapidly``, and
   ``CRACK_CRITICAL`` when the seam has opened past ``CRACK_CRITICAL_MM``.
5. **A stage machine** ``NONE → INITIATION → PROPAGATION → CRITICAL``, with
   ``UNKNOWN`` as the fifth answer — because "we cannot see the seam" is not the
   same as "the seam is fine", and a five-state machine that cannot say *unknown*
   will always say *fine* instead. In ``STAGE_RANK`` it sits *above* ``INITIATION``,
   because this ranking is what Phase 7 routes on: a blind node is not a safe
   node.

**One distinction that decides the whole design**
--------------------------------------------------

* **Events are about rates.** They fire, they clear, they can fire again.
* **Stages are about state.** A crack does not heal: once the seam has opened
  4 mm, it stays open, so ``PROPAGATION`` persists on the *level* after the
  30 s delta that raised it has decayed back to zero. Without that rule the
  dashboard would calm down on its own while the hazard stood unchanged.

**What this module deliberately does not do**
----------------------------------------------

``cross_sensor_coherence`` is computed and published, and it suppresses **nothing**.
The plan's Rule 2 is explicit: suppression is a decision for Phase 5's Model 1,
which also sees duration, recovery and spatial extent. A physics scalar that
decides on its own is a physics scalar that will one day decide wrong on its own.

**One known defect in §3.6, recorded here rather than quietly patched**
------------------------------------------------------------------------

``coherence = 1 − |structural − nonspecific|`` reproduces all six rows of the
plan's table exactly, so it is implemented as written — but read the two cases
where the two families *disagree completely*:

===============  ===========  =============  ===========
case             structural   nonspecific   coherence
===============  ===========  =============  ===========
truck passing    0.02         0.90           **0.12**
pure micro-crack **1.00**     **0.00**       **0.00**
===============  ===========  =============  ===========

A strain signal with *no* vibration at all is the single most structural reading
the rig can produce, and the formula scores it exactly as incoherent as a truck.
The table's own "micro-crack initiating" row dodges this because it assumes
``nonspecific = 0.30`` (a crack tickling the SW-420) — that is what makes the row
come out at 0.50. The acceptance test in §3.7 was therefore written against the
table's numbers, not against a vibration-free onset.

The repair is a denominator, not a rewrite — ``structural / max(structural,
nonspecific)`` says *"of the evidence there is, how much of it is structural"* and
returns 1.0 for the pure case while still returning 0.02/0.90 → 0.02 for the
truck. **That is a Phase 5 decision**, because it changes a documented formula and
Phase 5 is where the feature set and the model that consumes it are built. Phase 3
implements the frozen formula, tests the frozen table, and flags the defect.

Threading: every function here is called from inside ``state_lock`` (the serial
reader thread and the ticker both hold it), so there is no lock in this module.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from config import (
    CRACK_CLEAR_FRACTION, CRACK_CONFIRM_N, CRACK_CRITICAL_HOLD_S, CRACK_CRITICAL_MM,
    MACRO_ADC_HI, MACRO_ADC_LO, MACRO_DELTA_WINDOW_S, MACRO_JUMP_THRESHOLD,
    MACRO_MM_HI, MACRO_MM_LO, MICRO_MEDIAN_N, MICRO_RATE_THRESHOLD,
    MICRO_RATE_WINDOW_S, MICRO_SETTLE_S, STALE_AFTER_S, TILT_REFERENCE, VIB_REFERENCE,
)
from contracts import (
    CRACK_NODE_IDS, FLAG_BASELINE_UNSETTLED, FLAG_MACRO_AT_RAIL, FLAG_MICRO_SATURATED,
    FLAG_MICRO_UNLOADED,
)

# ---------------------------------------------------------------------------
# The alphabet
# ---------------------------------------------------------------------------

#: Stage names. ``UNKNOWN`` is a first-class answer, not an error state: it is
#: what the stage becomes when the channel cannot be trusted (unsettled, railed,
#: slack, or the node has gone quiet). The dashboard renders it grey with the
#: reason beside it — never as ``NONE``, which would read as "no crack".
STAGE_NONE = "NONE"
STAGE_INITIATION = "INITIATION"
STAGE_PROPAGATION = "PROPAGATION"
STAGE_CRITICAL = "CRITICAL"
STAGE_UNKNOWN = "UNKNOWN"
STAGES = (STAGE_NONE, STAGE_INITIATION, STAGE_PROPAGATION, STAGE_CRITICAL, STAGE_UNKNOWN)

#: Rank for "which stage is worse", used when two levels, two zones or two routes
#: are compared. Two properties matter, and both are deliberate:
#:
#: 1. ``UNKNOWN`` is **above ``NONE``** — a node we cannot see is not a node that
#:    is fine.
#: 2. ``UNKNOWN`` is also **above ``INITIATION``** — this ranking is read by Phase
#:    7's router, where the question is *"which way is safer"*, and the honest
#:    answer is that a node with no reading must never be preferred to one with a
#:    reading. A route through a blind node is not a safer route; it is an
#:    unverified one.
STAGE_RANK = {
    STAGE_NONE: 0,
    STAGE_INITIATION: 1,
    STAGE_UNKNOWN: 2,
    STAGE_PROPAGATION: 3,
    STAGE_CRITICAL: 4,
}

#: Event types (§3.3). Fixed strings — the alert dispatcher, the runbook and the
#: viva script all quote them.
EV_MICRO_INITIATION = "CRACK_MICRO_INITIATION"
EV_MACRO_PROPAGATION = "CRACK_MACRO_PROPAGATION"
EV_CRITICAL = "CRACK_CRITICAL"
EV_SENSOR_FAULT = "SENSOR_FAULT"
CRACK_EVENT_TYPES = (EV_MICRO_INITIATION, EV_MACRO_PROPAGATION, EV_CRITICAL)

#: Infrastructure events carry this in ``kind`` beside an unchanged ``level``
#: (the Phase 2 rule): a dead channel must be visible without being painted as a
#: geological severity tier. A cracked seam is geological — ``kind`` stays ``None``.
KIND_INFRASTRUCTURE = "INFRASTRUCTURE"

#: Event severity levels. Initiation is WARNING — damage has started, nothing has
#: moved. Propagation is WARNING — it is moving, and the TTF countdown is the
#: escalation. CRITICAL is CRITICAL.
LEVEL_MICRO = "WARNING"
LEVEL_MACRO = "WARNING"
LEVEL_CRITICAL = "CRITICAL"
LEVEL_FAULT = "WARNING"

#: Fault names, in the order they are reported. Each maps to a flag bit the
#: firmware sets; the server never guesses a fault from the value alone, because
#: a slack rig and a healthy quiet rig look identical in the number.
FAULT_NAMES = {
    FLAG_MICRO_SATURATED: "micro_saturated",
    FLAG_MICRO_UNLOADED: "micro_unloaded",
    FLAG_MACRO_AT_RAIL: "macro_at_rail",
}

FAULT_DETAIL = {
    "micro_saturated": "load cell at its rail — open gauge or broken lead",
    "micro_unloaded": "load cell unloaded — spring slack, seam closed, or cell unhooked",
    "macro_at_rail": "macro pot at its rail — wiper off the track or lead open",
}

#: Event wording. The two structural sentences are the plan's (§3.2.4) with the
#: node and the number attached, so the log reads as a story rather than a list.
TEXT_MICRO = "{node} micro-crack initiated — crack-mouth opening rising at {rate:.1f} µm/s."
TEXT_MACRO = "{node} crack propagating rapidly — seam opened {d30:.2f} mm in {win:.0f} s."
#: Same event type, a different trigger: the seam was *already* open past the
#: threshold when the channel came up (a re-arm mid-incident, or the first frames
#: from a node that had been dark). Reporting "opened 2.10 mm in 30 s" there would
#: be a number nobody measured; the opening itself is the honest claim.
TEXT_MACRO_LEVEL = "{node} crack propagation confirmed — seam open {macro:.2f} mm (limit {limit:.1f} mm)."
TEXT_CRITICAL = "{node} CRACK CRITICAL — seam opening {macro:.2f} mm (limit {limit:.1f} mm)."
#: Two routes into CRITICAL, so two sentences. Reporting "opening 1.92 mm (limit
#: 5.0 mm)" for a critical stage reached by the *hold* rule would read as a
#: contradiction on the operator's screen — the log has to say which rule fired.
TEXT_CRITICAL_HOLD = ("{node} CRACK CRITICAL — propagation has not arrested in "
                      "{held:.0f} s (seam at {macro:.2f} mm).")
TEXT_FAULT = "{node} {channel} channel FAULT — {detail}. Channel reads UNKNOWN, not safe."
TEXT_FAULT_CLEAR = "{node} {channel} channel recovered — readings are being trusted again."

#: How many samples one node's history keeps. 240 samples at the 2 Hz cadence is
#: two minutes: enough for the 15 s settle, the 30 s macro delta and the 5 s rate
#: window, with room for a slow replay to arrive out of order.
HISTORY_N = 240

#: How much history a window keeps *beyond* its own length before it is trimmed.
#:
#: A delta over a window needs both ends of the window to exist in the history.
#: Filtering the history to exactly the window length is the classic way to build
#: a detector that can never fire: every sample left is *younger* than the
#: window's far end, so the far end is never found and the delta is always None.
#: Phase 3 shipped that bug for twenty minutes — ``macro_d30`` read ``None`` on a
#: drill that had been running for a minute — so the headroom is now explicit.
WINDOW_HEADROOM = 1.25

#: Cap on how many events one sample may raise. Three is the structural truth
#: (initiation, propagation, critical); the fourth is a fault notice.
MAX_EVENTS_PER_SAMPLE = 4


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def median(values) -> float:
    """Median of a small list. ``n`` is 3 in production; the helper is general
    because the tests feed it 1 and 5 as well."""
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0:
        raise ValueError("median() of an empty sequence")
    mid = n // 2
    return xs[mid] if n % 2 else 0.5 * (xs[mid - 1] + xs[mid])


def _at(hist, t: float, idx: int, tol_s: float):
    """The sample nearest to ``t`` in ``hist`` (indexed ``idx``), or ``None``.

    Same rule the feature extractor uses for its offsets: step back to the last
    sample at or before the target and reject it if it is further away than
    ``tol_s``. A value from the wrong side of a gap is not "close enough" — it is
    a different moment in the history of a moving seam.
    """
    best = None
    for row in reversed(hist):
        if row[0] <= t:
            best = row
            break
    if best is None:
        return None
    return best if (t - float(best[0])) <= tol_s else None


# ---------------------------------------------------------------------------
# The event object
# ---------------------------------------------------------------------------

@dataclass
class CrackEvent:
    """One thing the crack engine has decided to say.

    The engine *returns* these; it never writes to the event log itself. One
    writer (``state.note_event``) keeps one order and one cap, and it means a
    test can assert on exactly what the engine would have said without a server.
    """

    node_id: str
    event_type: str
    level: str
    text: str
    detail: dict = field(default_factory=dict)
    #: ``kind`` travels beside ``level`` so an infrastructure fault is never
    #: painted as a geological tier (the Phase 2 rule). A cracked seam is
    #: geological — ``kind`` stays ``None``; a dead channel is ``INFRASTRUCTURE``.
    kind: str | None = None

    def to_public(self) -> dict:
        return {"node": self.node_id, "event_type": self.event_type, "level": self.level,
                "text": self.text, "kind": self.kind, "detail": dict(self.detail)}

    def publish(self, state) -> None:
        """Append to the operator log. The only call in this module that writes."""
        state.note_event(self.level, self.text, kind=self.kind)


# ---------------------------------------------------------------------------
# Per-node memory
# ---------------------------------------------------------------------------

@dataclass
class CrackState:
    """Everything the engine must remember about one node's crack column.

    A *memo*, not a second source of truth: every field is either a previous
    answer (the hysteresis latches, the fault edge memory) or an input the state
    already has elsewhere (the sample history is the same frames the model window
    is built from). Deleting this object loses no history — it loses only the
    accumulated zero, which is exactly what a re-arm is supposed to lose.
    """

    node_id: str
    #: ``(t, micro_um, macro_mm, tilt_deg, vib)``, oldest first. ``micro``/``macro``
    #: are ``None`` when the channel has no value for that sample.
    hist: deque = field(default_factory=lambda: deque(maxlen=HISTORY_N))

    # -- the zero -----------------------------------------------------------
    #: Sum and count of the *settling* window. Frozen once the baseline lands.
    settle_sum: float = 0.0
    settle_n: int = 0
    settle_t0: float | None = None
    #: The baseline in µm, or ``None`` while the channel is unsettled.
    baseline_um: float | None = None
    #: Who supplied the zero: ``"node"`` (the gauge settled its own zero at boot
    #: and the arriving number is already relative to it) or ``"server"`` (the
    #: server averaged its own 15 s) or ``None``.
    baseline_source: str | None = None
    settled: bool = False

    # -- hysteresis latches -------------------------------------------------
    micro_armed: bool = False
    micro_confirm: int = 0
    micro_clear_confirm: int = 0
    macro_armed: bool = False
    macro_confirm: int = 0
    macro_clear_confirm: int = 0
    #: "This event has already been announced for the current episode." Events are
    #: raised on the *transition*, so a stage that holds for ten minutes produces
    #: one line in the log rather than one per sample — and a stage that falls back
    #: and rises again produces a second line, because that is a second event.
    micro_announced: bool = False
    prop_announced: bool = False
    #: When the propagation event was raised and it has not cleared. This is the
    #: input to the ``CRACK_CRITICAL_HOLD_S`` route.
    #:
    #: "Arrested" is defined by the same hysteresis as everything else: the latch
    #: releases only when the 30 s delta **and** the absolute opening are both below
    #: ``CRACK_CLEAR_FRACTION`` of their thresholds. So "the latch is still armed 20
    #: seconds later" is exactly §3.2.5's *"propagation has not arrested"* — and it
    #: is deliberately monotone: a seam that is still open past 1.5 mm twenty
    #: seconds on is CRITICAL and stays there until the operator re-arms or the
    #: ground genuinely closes. A stage that could decay on its own would show a
    #: calming dashboard on top of an unchanged hazard.
    propagation_since: float | None = None

    # -- stage --------------------------------------------------------------
    stage: str = STAGE_UNKNOWN
    stage_since: float | None = None
    #: The macro opening when the stage first reached PROPAGATION. Used for the
    #: disagreement readout (macro says the seam moved; displacement says it did
    #: not), which is *published*, never used to suppress.
    prop_open_mm: float | None = None
    #: The node's displacement when propagation began, for the same comparison.
    prop_disp_mm: float | None = None

    # -- faults -------------------------------------------------------------
    #: Active fault names on the last sample (edge memory, so each fault is
    #: announced once).
    faults: tuple[str, ...] = ()
    #: ``None`` = the last sample was a measurement; otherwise why it was not.
    fault_reason: str | None = None

    # -- last answers, for the projections ----------------------------------
    rate_um_s: float | None = None
    macro_d30: float | None = None
    coherence: float | None = None
    last_t: float | None = None
    last_flags: int = 0
    #: Set when the macro channel claims movement the node's own displacement
    #: channel does not show. Published as a readout; never acted on here.
    disagreement: bool = False

    # -- counters (read by /api/health, the tests and Phase 10's metrics) ----
    counts: dict = field(default_factory=lambda: {
        "samples": 0, "settle_samples": 0, "rejected": 0,
        "micro_initiation": 0, "macro_propagation": 0, "critical": 0,
        "faults": 0, "recoveries": 0, "unknown": 0,
    })

    def clear_baseline(self) -> None:
        self.settle_sum = 0.0
        self.settle_n = 0
        self.settle_t0 = None
        self.baseline_um = None
        self.baseline_source = None
        self.settled = False

    def clear_latches(self) -> None:
        self.micro_armed = False
        self.micro_confirm = 0
        self.micro_clear_confirm = 0
        self.micro_announced = False
        self.macro_armed = False
        self.macro_confirm = 0
        self.macro_clear_confirm = 0
        self.prop_announced = False
        self.propagation_since = None

    def to_public(self) -> dict:
        """The block ``/api/extended`` publishes under ``nodes[node].crack``.

        Every value here is JSON-scalar: this dict goes straight into a Flask
        response, and Phase 2 learned that the hard way (an ``XGBRegressor`` in a
        payload is a 500, not a debug message).
        """
        last = self.hist[-1] if self.hist else None
        return {
            "stage": self.stage,
            "settled": bool(self.settled),
            "baseline_um": None if self.baseline_um is None else round(float(self.baseline_um), 1),
            "baseline_source": self.baseline_source,
            "micro_um": None if last is None or last[1] is None else round(float(last[1]), 1),
            "macro_mm": None if last is None or last[2] is None else round(float(last[2]), 3),
            "rate_um_s": None if self.rate_um_s is None else round(float(self.rate_um_s), 2),
            "macro_d30_mm": None if self.macro_d30 is None else round(float(self.macro_d30), 3),
            "coherence": None if self.coherence is None else round(float(self.coherence), 3),
            "fault": self.fault_reason,
            "faults": list(self.faults),
            "disagreement": bool(self.disagreement),
            "since_s": None if self.stage_since is None else round(float(self.stage_since), 3),
            "counts": dict(self.counts),
        }


# ---------------------------------------------------------------------------
# The ledger
# ---------------------------------------------------------------------------

@dataclass
class CrackLedger:
    """One ``CrackState`` per instrumented node, plus process-wide counters.

    Owned by ``SystemState`` (``state.crack``) so ``state.reset()`` can clear it
    and a test can build one without a server. Named ``crack`` and not
    ``crack_engine`` on purpose: Phase 2 lost an afternoon to a ledger field named
    ``health`` shadowing the ``engine.health`` module, and the fix there was to
    give the ledger the module's noun and the module an explicit alias.
    """

    nodes: dict = field(default_factory=dict)
    counts: dict = field(default_factory=lambda: {
        "samples": 0, "events": 0, "faults": 0, "unknown_samples": 0,
    })

    def for_node(self, n_id: str) -> CrackState:
        cs = self.nodes.get(n_id)
        if cs is None:
            cs = CrackState(node_id=n_id)
            self.nodes[n_id] = cs
        return cs

    def clear(self) -> None:
        """Full re-arm: every zero, latch, stage and fault memory goes.

        The counters survive. An initiation that was announced happened, and a
        re-arm is the operator saying "start from here", not "nothing occurred".
        """
        for cs in self.nodes.values():
            cs.clear_baseline()
            cs.clear_latches()
            cs.hist.clear()
            cs.stage = STAGE_UNKNOWN
            cs.stage_since = None
            cs.prop_open_mm = None
            cs.prop_disp_mm = None
            cs.faults = ()
            cs.fault_reason = None
            cs.rate_um_s = None
            cs.macro_d30 = None
            cs.coherence = None
            cs.last_t = None
            cs.disagreement = False

    def prune(self, node_ids) -> None:
        live = set(node_ids)
        for n_id in [k for k in self.nodes if k not in live]:
            del self.nodes[n_id]


def _ledger(state) -> CrackLedger:
    """The state's ledger, created on first use.

    Lazy for the same reason ``health._ledger`` is: hand-built states and the
    frozen JSON fixtures must still tick.
    """
    ledger = getattr(state, "crack", None)
    if not isinstance(ledger, CrackLedger):
        ledger = CrackLedger()
        state.crack = ledger
    return ledger


def crack_state(state, n_id: str) -> CrackState:
    """This node's ``CrackState``, created on first use."""
    return _ledger(state).for_node(n_id)


# ---------------------------------------------------------------------------
# §3.4 — the functions the plan names
# ---------------------------------------------------------------------------

def update_micro_baseline(state, n_id: str, value: float | None, t: float,
                          settled_by_node: bool = False) -> float | None:
    """Take the zero for ``micro``. Returns the baseline in µm, or ``None``.

    Three rules, and the first two are the ones that bite:

    **1 · Zero is taken from whoever has it.** The node settles its own gauge for
    15 s after power-up and transmits a number already relative to that zero
    (H2.4 — *"the node owns its own zero, the server just trusts the flag"*). If
    the node says settled, the server's baseline is ``0.0`` immediately. Averaging
    a second zero on top of the node's would subtract the same offset twice and
    quietly halve every reading.

    **2 · No zero is better than a wrong one.** §3.4 says "returns 0.0 until
    settled"; §3.2 says the channel reports ``null`` until settled. They disagree,
    and the disagreement is not cosmetic: returning ``0.0`` means *subtract
    nothing*, which publishes raw counts as though they were opening — a channel
    that has been on for three seconds would show 388 µm of crack that does not
    exist. This returns ``None``, the channel stays unknown, and the flag says why.

    **3 · Idempotent.** Once the baseline lands it never moves again until a
    re-arm. A baseline that keeps re-averaging is a high-pass filter, and it will
    filter away the very creep the channel exists to detect.
    """
    cs = crack_state(state, n_id)
    if cs.settled:
        return cs.baseline_um

    if settled_by_node:
        # The node's own 15 s window has closed; its number is already relative.
        cs.baseline_um = 0.0
        cs.baseline_source = "node"
        cs.settled = True
        return cs.baseline_um

    if value is None:
        # No channel, or a channel that is not producing a number. Nothing to
        # average — and do not start the settle clock, or the window would be
        # declared complete on three seconds of data spread over fifteen.
        return None

    if cs.settle_t0 is None:
        cs.settle_t0 = float(t)
    cs.settle_sum += float(value)
    cs.settle_n += 1
    cs.counts["settle_samples"] = cs.settle_n

    if (float(t) - float(cs.settle_t0)) < MICRO_SETTLE_S:
        return None

    cs.baseline_um = cs.settle_sum / max(1, cs.settle_n)
    cs.baseline_source = "server"
    cs.settled = True
    return cs.baseline_um


def micro_rate(cs: CrackState, t: float | None = None) -> float | None:
    """``d(micro)/dt`` over the last ``MICRO_RATE_WINDOW_S`` seconds, in µm/s.

    Median-filtered **at both ends**: the value now is the median of the last
    ``MICRO_MEDIAN_N`` samples, the value one window ago is the median of the
    samples around that moment, and the rate is the difference over the real
    elapsed time between them.

    Why both ends and not just the series: a 3-point median of the *series* is a
    filter you have to apply in arrival order and carry; the median of the
    samples at the two ends of a 5 s window is the same arithmetic applied where
    it matters, and it degrades honestly — if the older endpoint is missing, the
    answer is ``None`` (warming), never a rate computed over a window that was
    not actually five seconds long.

    Returns ``None`` when the channel is unsettled, when there are not two
    endpoints, or when the endpoints are further apart than the window allows.
    """
    if not cs.settled:
        return None
    t = float(cs.last_t if t is None else t)
    recent = [row for row in cs.hist if row[1] is not None and (t - row[0]) <= MICRO_RATE_WINDOW_S]
    if len(recent) < 2:
        return None
    now_block = recent[-MICRO_MEDIAN_N:]
    target = t - MICRO_RATE_WINDOW_S
    old_block = [row for row in cs.hist
                 if row[1] is not None and abs(row[0] - target) <= MICRO_RATE_WINDOW_S * 0.25]
    if not old_block:
        return None
    old_block = old_block[-MICRO_MEDIAN_N:]
    dt = float(now_block[-1][0]) - float(old_block[-1][0])
    if dt <= 0.0:
        return None
    return (median(r[1] for r in now_block) - median(r[1] for r in old_block)) / dt


def macro_opening(adc, adc_lo: float = MACRO_ADC_LO, mm_lo: float = MACRO_MM_LO,
                  adc_hi: float = MACRO_ADC_HI, mm_hi: float = MACRO_MM_HI) -> float:
    """Millimetres of seam opening from a raw macro-pot ADC count.

    A two-point calibration, because a pot is a resistor with a wiper and no two
    mountings are the same: ``adc_lo``/``mm_lo`` and ``adc_hi``/``mm_hi`` are
    measured on the bench (H2.6 step 8 — *"measure the seam travel for a full
    sweep, write the mm-per-count on the node's label"*).

    Clamped at 0: mechanical backlash must never subtract from a delta (§3.8).
    The firmware clamps at the same place with the same rule; this copy exists
    for the raw-ADC path — the hub simulator, a bench capture, and any frame that
    carries counts instead of millimetres.
    """
    span_adc = float(adc_hi) - float(adc_lo)
    if span_adc == 0.0:
        return 0.0
    frac = (float(adc) - float(adc_lo)) / span_adc
    return max(0.0, float(mm_lo) + frac * (float(mm_hi) - float(mm_lo)))


def macro_d30(cs: CrackState, t: float | None = None) -> float | None:
    """The macro channel's change over ``MACRO_DELTA_WINDOW_S`` (30 s), in mm.

    The plan's ``macro_d30``, recomputed on read and never stored. On a node with
    no macro channel every sample is ``None`` and so is this — which is the
    difference between "the seam is not moving" and "there is no instrument on
    the seam".
    """
    t = float(cs.last_t if t is None else t)
    rows = [row for row in cs.hist if row[2] is not None
            and (t - row[0]) <= MACRO_DELTA_WINDOW_S * WINDOW_HEADROOM]
    if len(rows) < 2:
        return None
    now = rows[-1]
    old = _at(rows, now[0] - MACRO_DELTA_WINDOW_S, 2, MACRO_DELTA_WINDOW_S * 0.2)
    if old is None:
        # Not enough coverage yet: the window's far end did not exist. Report
        # None rather than differencing against the oldest sample we happen to
        # have, which would report a 4 s delta as a 30 s one — the classic way a
        # channel "detects" a jump on the first sample after boot.
        return None
    return float(now[2]) - float(old[2])


def cross_sensor_coherence(state, n_id: str, node=None) -> float | None:
    """§3.6 — do the structural and the non-specific channels agree? ``[0, 1]``.

    The insight in one line: **a passing truck moves the accelerometer but does
    not strain the roof.**

    ::

        micro_z  = clamp(rate      / MICRO_RATE_THRESHOLD, 0, 1)   structural
        macro_z  = clamp(macro_d30 / MACRO_JUMP_THRESHOLD, 0, 1)   structural
        vib_z    = clamp(vib       / VIB_REFERENCE,        0, 1)   non-specific
        tilt_z   = clamp(|tilt_d30|/ TILT_REFERENCE,       0, 1)   non-specific

        coherence = 1 - |max(micro_z, macro_z) - max(vib_z, tilt_z)|

    **``None`` when the structural evidence is unknown.** A node with no crack
    column cannot make a structural claim, and returning a number anyway would be
    the worst kind of wrong: with ``structural = 0`` the formula returns
    ``1 - nonspecific``, so *the more the ground shakes, the more coherent it
    looks*. Unknown in, unknown out.

    This is also why the function is not used to suppress anything. It is a
    feature for Model 1 (Phase 5), and the suppression decision belongs to a
    model that also sees duration, recovery and spatial extent — Rule 2.
    """
    cs = crack_state(state, n_id)
    node = node if node is not None else (state.nodes.get(n_id) if state else None)
    if getattr(node, "micro", None) is None and getattr(node, "macro", None) is None:
        return None

    micro_z = None if cs.rate_um_s is None else clamp(cs.rate_um_s / MICRO_RATE_THRESHOLD, 0.0, 1.0)
    macro_z = None if cs.macro_d30 is None else clamp(cs.macro_d30 / MACRO_JUMP_THRESHOLD, 0.0, 1.0)
    known = [z for z in (micro_z, macro_z) if z is not None]
    if not known:
        return None
    structural = max(known)

    tilt_d30 = _tilt_d30(cs)
    vib = float(getattr(node, "vib", 0) or 0)
    vib_z = clamp(vib / VIB_REFERENCE, 0.0, 1.0)
    tilt_z = 0.0 if tilt_d30 is None else clamp(abs(tilt_d30) / TILT_REFERENCE, 0.0, 1.0)
    nonspecific = max(vib_z, tilt_z)

    return clamp(1.0 - abs(structural - nonspecific), 0.0, 1.0)


def _tilt_d30(cs: CrackState) -> float | None:
    """Tilt change over 30 s from the engine's own history (the coherence input)."""
    t = cs.last_t
    if t is None:
        return None
    rows = [row for row in cs.hist if (t - row[0]) <= MACRO_DELTA_WINDOW_S * WINDOW_HEADROOM]
    if len(rows) < 2:
        return None
    old = _at(rows, float(t) - MACRO_DELTA_WINDOW_S, 3, MACRO_DELTA_WINDOW_S * 0.2)
    if old is None:
        return None
    return float(rows[-1][3]) - float(old[3])


def fault_reason(flags: int) -> str | None:
    """Which channel fault the frame's flag byte is reporting, or ``None``.

    Read from the flags and never guessed from the value: a slack spring and a
    healthy quiet rig produce the same number, and only the firmware's own
    ``NS_FLAG_MICRO_UNLOADED`` distinguishes them.
    """
    for bit, name in FAULT_NAMES.items():
        if int(flags or 0) & bit:
            return name
    return None


def _structural_z(cs: CrackState) -> float | None:
    """``max(micro_z, macro_z)`` — the structural evidence in ``[0, 1]``."""
    zs = []
    if cs.rate_um_s is not None:
        zs.append(clamp(cs.rate_um_s / MICRO_RATE_THRESHOLD, 0.0, 1.0))
    if cs.macro_d30 is not None:
        zs.append(clamp(cs.macro_d30 / MACRO_JUMP_THRESHOLD, 0.0, 1.0))
    return max(zs) if zs else None


def _stage_for(cs: CrackState, node, t: float, faults: str | None) -> str:
    """Derive the stage (§3.2.5) from the **two confirmed latches**, and nothing else.

    Reading the whole machine from two booleans is what makes the stage and the
    event log impossible to disagree with: the propagation event fires on the
    transition into ``PROPAGATION``, and ``PROPAGATION`` happens when — and only
    when — ``cs.macro_armed`` is true. There is no third path into a stage, so
    there is no path by which the dashboard can show a stage the log never
    announced.

    ``CRITICAL`` has two independent routes, both physical:

    * the seam has opened past ``CRACK_CRITICAL_MM`` — a fall hazard in its own
      right, whatever the rate; or
    * propagation has not arrested within ``CRACK_CRITICAL_HOLD_S`` — a crack whose
      latch is still armed 20 s on is not arresting by itself. "Arrested" is the
      hysteresis clear (both the delta *and* the opening below 70 % of threshold),
      so this route fires for a seam that is both moving and still open past
      1.5 mm, and it is monotone by design.

    The propagation *latch* is what persists across a decaying ``macro_d30``: the
    delta falls back to zero once the seam stops moving, but the seam stays open,
    so the stage holds on the level until the seam closes below
    ``CRACK_CLEAR_FRACTION`` of the threshold or the operator re-arms.
    """
    if faults is not None or not cs.settled:
        return STAGE_UNKNOWN
    if not cs.macro_armed:
        return STAGE_INITIATION if cs.micro_armed else STAGE_NONE
    macro = getattr(node, "macro", None)
    if macro is not None and float(macro) >= CRACK_CRITICAL_MM:
        return STAGE_CRITICAL
    if (cs.propagation_since is not None
            and (float(t) - float(cs.propagation_since)) >= CRACK_CRITICAL_HOLD_S):
        return STAGE_CRITICAL
    return STAGE_PROPAGATION


def classify_crack(state, n_id: str, node, t: float, flags: int = 0) -> list[CrackEvent]:
    """One sample's verdict: the events it raises, in severity order.

    Called once per delivered crack sample, *before* the node's status is
    recomputed, so an initiation raised by this frame can influence the same
    tick's status (§3.5).

    The order inside is load-bearing: **faults are decided first and they stop
    everything else**. A railed load cell produces a number that looks like a very
    fast opening; without this check the engine would announce a crack from a
    broken wire (§3.7 ``test_crack_rail``).
    """
    cs = crack_state(state, n_id)
    events: list[CrackEvent] = []
    faults_now = fault_reason(flags)
    cs.last_flags = int(flags or 0)

    # -- the fault edge -----------------------------------------------------
    #
    # The test is "has the *answer* changed", not "was it previously healthy". A
    # channel that was railed and is now unloaded has changed what is wrong with
    # it, and the operator needs that second line: the first fault's sentence
    # ("open gauge or broken lead") sends someone to the bridge, the second
    # ("spring slack, or cell unhooked") sends them to the spring. Only an
    # *unchanged* fault is suppressed — that is what keeps a permanently railed
    # cell from logging at 2 Hz.
    if faults_now is not None and faults_now != cs.fault_reason:
        detail = FAULT_DETAIL.get(faults_now, faults_now)
        events.append(CrackEvent(
            node_id=n_id, event_type=EV_SENSOR_FAULT, level=LEVEL_FAULT,
            text=TEXT_FAULT.format(node=n_id.upper().replace("_", " "), channel=faults_now,
                                   detail=detail),
            detail={"channel": faults_now, "flags": int(flags or 0),
                    "definition": "the channel is not measuring; it is not reporting zero"},
            kind=KIND_INFRASTRUCTURE))
        cs.counts["faults"] += 1
        _ledger(state).counts["faults"] += 1
    elif faults_now is None and cs.fault_reason is not None:
        events.append(CrackEvent(
            node_id=n_id, event_type=EV_SENSOR_FAULT, level="NOMINAL",
            text=TEXT_FAULT_CLEAR.format(node=n_id.upper().replace("_", " "),
                                         channel=cs.fault_reason),
            detail={"channel": cs.fault_reason, "recovered": True},
            kind=KIND_INFRASTRUCTURE))
        cs.counts["recoveries"] += 1
    cs.fault_reason = faults_now
    cs.faults = (faults_now,) if faults_now else ()

    if faults_now is not None:
        # A fault is not a measurement: nothing is armed, nothing is cleared,
        # and the stage is UNKNOWN (never NONE — "no crack" is a claim).
        cs.micro_confirm = cs.macro_confirm = 0
        cs.stage = STAGE_UNKNOWN
        cs.rate_um_s = None
        cs.macro_d30 = None
        cs.coherence = None
        cs.counts["unknown"] += 1
        return events[:MAX_EVENTS_PER_SAMPLE]

    if not cs.settled:
        # Unsettled: the plan's rule is that no crack event can fire while the
        # baseline is being taken (§3.7 test_crack_settle), and the channel
        # reports null rather than a raw count dressed as opening.
        cs.stage = STAGE_UNKNOWN
        cs.counts["unknown"] += 1
        return events[:MAX_EVENTS_PER_SAMPLE]

    # -- §3.2.3, one rule for both channels --------------------------------
    #
    # A crossing raises only after CRACK_CONFIRM_N consecutive samples; it clears
    # the moment the value falls below CRACK_CLEAR_FRACTION of the threshold. The
    # *same* rule governs the coarse channel's two triggers — its 30 s delta and
    # its absolute opening — because they are the same claim ("this seam is
    # moving") measured over two different windows, and giving them different
    # rules would let the dashboard's stage disagree with the log's events.
    rate = cs.rate_um_s
    micro_high = rate is not None and rate >= MICRO_RATE_THRESHOLD
    micro_low = rate is not None and rate < MICRO_RATE_THRESHOLD * CRACK_CLEAR_FRACTION
    if micro_high:
        cs.micro_confirm += 1
    elif micro_low or not cs.micro_armed:
        cs.micro_confirm = 0
    if cs.micro_confirm >= CRACK_CONFIRM_N:
        cs.micro_armed = True
    if cs.micro_armed and micro_low:
        cs.micro_armed = False

    macro = getattr(node, "macro", None)
    level_mm = None if macro is None else float(macro)
    d30 = cs.macro_d30
    prop_high = ((d30 is not None and d30 >= MACRO_JUMP_THRESHOLD)
                 or (level_mm is not None and level_mm >= MACRO_JUMP_THRESHOLD))
    prop_low = ((d30 is None or d30 < MACRO_JUMP_THRESHOLD * CRACK_CLEAR_FRACTION)
                and (level_mm is None or level_mm < MACRO_JUMP_THRESHOLD * CRACK_CLEAR_FRACTION))
    if prop_high:
        cs.macro_confirm += 1
    elif prop_low or not cs.macro_armed:
        cs.macro_confirm = 0
    if cs.macro_confirm >= CRACK_CONFIRM_N and not cs.macro_armed:
        cs.macro_armed = True
        cs.propagation_since = float(t)
        cs.prop_open_mm = level_mm
        cs.prop_disp_mm = float(getattr(node, "displacement_mm", 0.0) or 0.0)
    if cs.macro_armed and prop_low:
        cs.macro_armed = False
        cs.propagation_since = None
        cs.prop_announced = False      # the next propagation is a new event

    # -- the stage, then the events *on* its transitions -------------------
    previous = cs.stage
    stage = _stage_for(cs, node, float(t), faults_now)

    if cs.micro_armed and not cs.micro_announced:
        cs.micro_announced = True
        cs.counts["micro_initiation"] += 1
        _ledger(state).counts["events"] += 1
        events.append(CrackEvent(
            node_id=n_id, event_type=EV_MICRO_INITIATION, level=LEVEL_MICRO,
            text=TEXT_MICRO.format(node=n_id.upper().replace("_", " "),
                                   rate=0.0 if rate is None else rate),
            detail={"rate_um_s": None if rate is None else round(float(rate), 2),
                    "threshold_um_s": MICRO_RATE_THRESHOLD,
                    "confirm_samples": cs.micro_confirm,
                    "unit": "um (crack-mouth opening)"}))
    elif not cs.micro_armed:
        cs.micro_announced = False

    if stage == STAGE_PROPAGATION and not cs.prop_announced:
        cs.prop_announced = True
        cs.counts["macro_propagation"] += 1
        _ledger(state).counts["events"] += 1
        by_rate = d30 is not None and d30 >= MACRO_JUMP_THRESHOLD
        events.append(CrackEvent(
            node_id=n_id, event_type=EV_MACRO_PROPAGATION, level=LEVEL_MACRO,
            text=(TEXT_MACRO.format(node=n_id.upper().replace("_", " "), d30=d30,
                                    win=MACRO_DELTA_WINDOW_S) if by_rate
                  else TEXT_MACRO_LEVEL.format(node=n_id.upper().replace("_", " "),
                                               macro=0.0 if level_mm is None else level_mm,
                                               limit=MACRO_JUMP_THRESHOLD)),
            detail={"trigger": "rate" if by_rate else "opening",
                    "macro_d30_mm": None if d30 is None else round(float(d30), 3),
                    "macro_mm": None if level_mm is None else round(float(level_mm), 3),
                    "threshold_mm": MACRO_JUMP_THRESHOLD}))
    elif stage not in (STAGE_PROPAGATION, STAGE_CRITICAL) and cs.prop_announced:
        cs.prop_announced = False

    if stage == STAGE_CRITICAL and previous != STAGE_CRITICAL:
        cs.counts["critical"] += 1
        _ledger(state).counts["events"] += 1
        macro_now = 0.0 if level_mm is None else float(level_mm)
        held = None if cs.propagation_since is None else float(t) - float(cs.propagation_since)
        by_opening = macro_now >= CRACK_CRITICAL_MM
        events.append(CrackEvent(
            node_id=n_id, event_type=EV_CRITICAL, level=LEVEL_CRITICAL,
            text=(TEXT_CRITICAL.format(node=n_id.upper().replace("_", " "),
                                       macro=macro_now, limit=CRACK_CRITICAL_MM) if by_opening
                  else TEXT_CRITICAL_HOLD.format(node=n_id.upper().replace("_", " "),
                                                 held=0.0 if held is None else held,
                                                 macro=macro_now)),
            detail={"trigger": "opening" if by_opening else "hold",
                    "macro_mm": round(macro_now, 3),
                    "limit_mm": CRACK_CRITICAL_MM,
                    "hold_limit_s": CRACK_CRITICAL_HOLD_S,
                    "held_s": None if held is None else round(held, 1),
                    "stage": stage}))

    if stage != cs.stage:
        cs.stage = stage
        cs.stage_since = float(t)

    # -- the published disagreement readout (never a suppression rule) ------
    cs.disagreement = _disagreement(cs, node)

    return events[:MAX_EVENTS_PER_SAMPLE]


def _disagreement(cs: CrackState, node) -> bool:
    """Does the coarse channel claim seam movement the node's own displacement
    channel does not show?

    A pot's finger slipping, a sticky wiper or a loose lever all look like an
    opening seam. The node's displacement pot measures the same block, so the two
    should move together. This is **published, not enforced**: suppression is
    Phase 5's decision, and a physics scalar that suppresses on its own will one
    day suppress the wrong thing (Rule 2).
    """
    if cs.prop_open_mm is None or cs.prop_disp_mm is None:
        return False
    macro = getattr(node, "macro", None)
    if macro is None:
        return False
    opened = float(macro) - float(cs.prop_open_mm)
    moved = abs(float(getattr(node, "displacement_mm", 0.0) or 0.0) - float(cs.prop_disp_mm))
    return opened >= MACRO_JUMP_THRESHOLD and moved < 0.5 * abs(opened)


def crack_stage(state, n_id: str) -> str:
    """This node's current stage.

    A node with **no crack column** answers ``NONE`` — the contract's own default
    — and not ``UNKNOWN``, because the two mean different things and only one of
    them is true here. ``UNKNOWN`` is reserved for a channel that exists and
    cannot be trusted: railed, slack, unsettled, or gone quiet. N1 and N3 have no
    second channel at all, so there is no reading to distrust, and
    ``crack_public()`` carries the note that says so. (Ranking them together
    would also break routing in Phase 7, where ``UNKNOWN`` deliberately outranks
    ``NONE`` so a blind node cannot be chosen as a safe exit.)
    """
    if n_id not in CRACK_NODE_IDS:
        return STAGE_NONE
    return crack_state(state, n_id).stage


def reset_crack_baseline(state, reason: str = "operator re-arm", n_ids=None) -> list[str]:
    """Drop every zero, latch and stage. Called by ``SystemState.reset()``.

    **What this does not do, deliberately:** it does not re-zero the *node*. The
    node owns its own gauge baseline (H2.4) and has no idea a re-arm happened —
    telling it would need the downlink command path, which arrives with the level
    hub in Phase 8. That asymmetry is harmless, and worth stating rather than
    hiding: a re-arm re-bases the *server's* view of the seam (its history, its
    rate window, its latches), while the physical zero stays where the gauge put
    it. Zeroing a physical gauge is a bench action — H2.6 step 7, after the glue
    cures — and no button on a dashboard should be able to fake one.
    """
    ledger = _ledger(state)
    n_ids = list(n_ids) if n_ids else [n for n in CRACK_NODE_IDS if n in getattr(state, "nodes", {})]
    ledger.clear()
    ledger.prune(getattr(state, "nodes", {}).keys() or ())
    for n_id in n_ids:
        node = state.nodes.get(n_id) if getattr(state, "nodes", None) else None
        if node is not None:
            node.micro_rate = None
            node.micro_baseline_um = None
            node.micro_settled = False
            node.macro_d30 = None
            node.cross_sensor_coherence = None
            node.crack_stage = STAGE_UNKNOWN
    return n_ids


# ---------------------------------------------------------------------------
# The one entry point the pipeline calls
# ---------------------------------------------------------------------------

def update_node_crack(state, n_id: str, node, t: float, flags: int | None = None) -> list[CrackEvent]:
    """Fold one sample of a crack node into the engine. Returns events to publish.

    This is what ``ingestion.serial_reader`` calls for a delivered frame and what
    the ticker calls for a node the simulator is driving locally — one function,
    so the live path and the bench path cannot drift.

    Steps, in order (each one depends on the one before):

    1. append the sample to the node's history;
    2. take the zero (``update_micro_baseline``), from the node's flag if the node
       declares its own settle complete;
    3. publish the baseline-subtracted ``micro`` back onto the node, so the
       dashboard and the database see the same number the engine reasoned about;
    4. compute the rate, the 30 s macro delta and coherence;
    5. classify: raise/clear the two events, move the stage.

    Returns ``[]`` for a node with no crack column, having done nothing at all.
    """
    if n_id not in CRACK_NODE_IDS:
        return []
    cs = crack_state(state, n_id)
    flags = int(getattr(node, "flags", 0) if flags is None else flags)
    raw_micro = getattr(node, "micro", None)
    raw_macro = getattr(node, "macro", None)

    cs.hist.append((float(t),
                    None if raw_micro is None else float(raw_micro),
                    None if raw_macro is None else float(raw_macro),
                    float(getattr(node, "tilt_x", 0.0) or 0.0),
                    int(getattr(node, "vib", 0) or 0)))
    cs.last_t = float(t)
    cs.counts["samples"] += 1
    _ledger(state).counts["samples"] += 1

    node_settled = not bool(flags & FLAG_BASELINE_UNSETTLED)
    baseline = update_micro_baseline(state, n_id, raw_micro, float(t),
                                     settled_by_node=node_settled and raw_micro is not None)
    node.micro_baseline_um = baseline
    node.micro_settled = bool(cs.settled)

    # Baseline-subtracted value goes back onto the node: everything downstream
    # (telemetry, the dashboard, the database) must see the number the engine
    # reasoned about, not the raw one. Silently keeping two versions is how a
    # channel ends up 388 µm wrong on screen and right in the log.
    if raw_micro is not None and cs.settled and baseline is not None:
        node.micro = float(raw_micro) - float(baseline)
    elif raw_micro is not None and not cs.settled:
        node.micro = None                     # unknown, and it says so

    cs.rate_um_s = micro_rate(cs, float(t))
    cs.macro_d30 = macro_d30(cs, float(t))
    node.micro_rate = cs.rate_um_s
    node.macro_d30 = cs.macro_d30
    cs.coherence = cross_sensor_coherence(state, n_id, node)
    node.cross_sensor_coherence = cs.coherence

    events = classify_crack(state, n_id, node, float(t), flags=flags)
    node.crack_stage = cs.stage
    return events


def crack_tick(state, now: float | None = None) -> None:
    """20 Hz housekeeping: a node that has gone quiet cannot keep a stage.

    Why this exists separately from ``update_node_crack``: that function only runs
    when a sample arrives. A node whose radio died mid-propagation would keep
    ``PROPAGATION`` on the dashboard forever, with the last known seam opening
    beside it — a stale verdict presented as a current one. Phase 2 built exactly
    this lesson for node status (``last_seen_s``, ``stale``); this applies it to
    the crack column, and it says ``UNKNOWN`` rather than ``NONE``.
    """
    now = time.time() if now is None else float(now)
    ledger = _ledger(state)
    seen = getattr(state, "last_arrival", {}) or {}
    for n_id, cs in ledger.nodes.items():
        if n_id not in CRACK_NODE_IDS:
            continue
        last = seen.get(n_id)
        if last is None:
            # **Never heard a frame is not the same as gone quiet.** On the
            # hardware-free bench the scenario console drives this node and no
            # frame ever arrives, so ``last_arrival`` is empty for the whole
            # session — and reading that as silence wiped the rate, the coherence
            # and the stage on every 20 Hz tick, one tick after the engine had
            # computed them. It looked exactly like a detector that never fires.
            # Same trap the Phase 2 watchdog hit, same fix: fall back to the
            # engine's own sample clock, which is wall clock on the bench and is
            # the only thing that says whether *this* node's column is current.
            if cs.last_t is None or (now - float(cs.last_t)) <= STALE_AFTER_S:
                continue
        elif (now - float(last)) <= STALE_AFTER_S:
            continue
        if cs.stage != STAGE_UNKNOWN:
            cs.stage = STAGE_UNKNOWN
            cs.stage_since = None
            # The fault memory is left alone: a channel that was railed when the
            # radio died is still railed, and clearing it here would announce a
            # recovery nobody observed.
            ledger.counts["unknown_samples"] += 1
        cs.micro_armed = False
        cs.macro_armed = False
        cs.propagation_since = None
        cs.rate_um_s = None
        cs.coherence = None
        node = state.nodes.get(n_id) if getattr(state, "nodes", None) else None
        if node is not None:
            node.micro_rate = None
            node.cross_sensor_coherence = None
            node.crack_stage = STAGE_UNKNOWN


def crack_public(state, n_id: str) -> dict:
    """``/api/extended``'s per-node crack block (JSON scalars only)."""
    if n_id not in CRACK_NODE_IDS:
        # NONE, not UNKNOWN — see `crack_stage`. The note is what stops a reader
        # from mistaking "no instrument" for "instrument is fine".
        return {"stage": STAGE_NONE,
                "note": "no crack column on this node — micro/macro are null by design"}
    return crack_state(state, n_id).to_public()


def crack_summary(state) -> dict:
    """The whole column, for ``/api/health`` and the runbook's one-glance check."""
    ledger = _ledger(state)
    blocks = {n_id: crack_state(state, n_id).to_public()
              for n_id in CRACK_NODE_IDS if n_id in getattr(state, "nodes", {})}
    return {
        "nodes": blocks,
        "stages": {n_id: b["stage"] for n_id, b in blocks.items()},
        "counts": dict(ledger.counts),
        "worst_stage": worst_stage(b["stage"] for b in blocks.values()) if blocks else STAGE_UNKNOWN,
    }


def worst_stage(stages) -> str:
    """The most severe stage in an iterable, with ``UNKNOWN`` outranking ``NONE``."""
    worst = STAGE_NONE
    for s in stages:
        if STAGE_RANK.get(s, 0) > STAGE_RANK.get(worst, 0):
            worst = s
    return worst


__all__ = [
    "STAGE_NONE", "STAGE_INITIATION", "STAGE_PROPAGATION", "STAGE_CRITICAL",
    "STAGE_UNKNOWN", "STAGES", "STAGE_RANK",
    "EV_MICRO_INITIATION", "EV_MACRO_PROPAGATION", "EV_CRITICAL", "EV_SENSOR_FAULT",
    "CRACK_EVENT_TYPES", "FAULT_NAMES", "FAULT_DETAIL", "KIND_INFRASTRUCTURE",
    "CrackEvent", "CrackState", "CrackLedger",
    "crack_state", "crack_public", "crack_summary", "worst_stage",
    "update_micro_baseline", "micro_rate", "macro_opening", "macro_d30",
    "cross_sensor_coherence", "classify_crack", "crack_stage",
    "reset_crack_baseline", "update_node_crack", "crack_tick",
    "fault_reason", "clamp", "median",
]
