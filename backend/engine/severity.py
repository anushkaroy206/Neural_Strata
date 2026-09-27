#!/usr/bin/env python3
"""
B3 — severity, progression stage, confidence, recommended actions.
=================================================================

Phase 6. Turns the system's single number — "TTF = 7 s" — into the sentence a
control room can act on:

    Node 3 · TERTIARY creep · CRITICAL · TTF 6–11 s (80% band) · confidence HIGH
              (window complete, models agree)

Four questions, four functions, and one rule that shapes all of them: **the
system must never appear more certain than it is.** A bare countdown looks
rigorous; a countdown with a stage, a band and a stated reason is honest.

Why the stage exists at all (§6.9). Time-to-failure already tells an operator
*how long*. It does not tell them *what is happening*, and those are different
decisions: 40 s of steady creep and 40 s of accelerating creep call for the same
clock and very different responses. Geotechnical engineers already have the
vocabulary — primary, secondary, tertiary creep — and tertiary is the one that
means "evacuate now". Report in the language the receiver already thinks in.

Severity tiers
--------------
``TTF_BANDS = (0, 30, 120, 600)`` are the **lower edges** of four tiers, read in
order:

===========  ==========  ==================================================
TTF (s)      Tier        What it means operationally
===========  ==========  ==================================================
``< 30``     CRITICAL    evacuate — SMS + email + strobe/siren
``30–120``   HIGH        withdraw non-essential staff, prepare evacuation
``120–600``  ELEVATED    restrict entry, monitor continuously
``>= 600``   NOMINAL     nothing to do; the calm end of "no estimate"
no estimate  WATCH       we do not know yet — dashboard and log only
===========  ==========  ==================================================

**WATCH is the no-estimate tier and that direction is deliberate.** A node whose
window has not filled, or whose link has just come back, has *no* TTF. The two
wrong answers are CRITICAL (alarm the mine because a radio restarted) and
NOMINAL (call an unknown condition safe). WATCH sits above NOMINAL and below
everything that can reach a pager, so an unknown state is visible, never silent,
and never wakes anyone up. It is also what makes the plan's acceptance rule
(*"severity never rises on stale data"*) true by construction rather than by
convention: a node with no fresh data can only ever land here, so staleness can
never promote a quiet node into an alerting tier.

The doc's own worked example agrees with this table — §6.3 shows
``{"tier":"HIGH","ttf":47}`` — which is why ``30–120`` is HIGH. §6.7's test row
carries ``ttf=100 ⇒ ELEVATED``, which would need ``100–600`` to be ELEVATED and
leave HIGH unreachable; that single row contradicts both the ordered comment at
``Build_Phases.md:179`` and the payload example, so it is treated as the typo. The
three boundary assertions that all sources agree on (25 → CRITICAL, 900 →
NOMINAL, exact at 30/120/600) are pinned in ``tests/test_severity.py``.

Progression stages
------------------
``STABLE`` is not one of §6.2's four creep stages, and it is here anyway: a seam
that is not moving is not in *any* stage of creep, and labelling a quiet node
PRIMARY would be the system inventing motion so the chip has something to say.
The four creep stages are reported when there is creep to report.

Hysteresis (§6.8) is why this module keeps state. A stage that flipped back and
forth on one noisy window would make the event log unreadable and train the
operator to ignore it, so TERTIARY needs 1.25x to *enter* and 10 s of sustained
acceleration, but only 1.05x to *leave* — the same asymmetric rule the crack
engine uses.
"""

from __future__ import annotations

import math
import time
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#: Lower edges of the four severity tiers. Lives in ``config`` (§A.4) and is
#: re-exported here so a caller can write ``severity.TTF_BANDS``.
TTF_BANDS: tuple[float, ...] = tuple(getattr(config, "TTF_BANDS", (0, 30, 120, 600)))

#: Least severe to most severe. Phase 8 compares with ``severity_rank`` to
#: express "any tier, if ``firewall_check``, is never below HIGH".
SEVERITY_ORDER: tuple[str, ...] = ("NOMINAL", "WATCH", "ELEVATED", "HIGH", "CRITICAL")

#: The four creep stages §6.2 names, plus the honest fifth.
STAGES: tuple[str, ...] = ("STABLE", "PRIMARY", "SECONDARY", "TERTIARY", "FAILURE")

#: Relative velocity gain that opens the TERTIARY door, and the smaller one that
#: closes it. Asymmetry is the hysteresis: 1.25x in, 1.05x out.
TERTIARY_ENTER_RATIO = 1.25
TERTIARY_EXIT_RATIO = 1.05

#: TERTIARY must be *sustained* before the stage changes (§6.2). Two durations,
#: for two signals of very different strength, and the reason is worth stating.
#:
#: ``TERTIARY_HOLD_S`` is §6.8's number and it governs the **ratio** entry: the
#: moment ``v10`` first exceeds ``1.25 v60`` is a marginal one — a passing truck
#: produces it too — so ten seconds of it are required before the stage moves.
#:
#: ``TERTIARY_RISE_HOLD_S`` governs the **sustained-rise** entry: the creep rate
#: has increased at every consecutive one-second evaluation. That is a strictly
#: stronger statement (it is the second derivative being positive, not a single
#: crossing), and the ratio test cannot substitute for it: once a run is truly
#: accelerating, ``v60`` grows too and the *ratio* falls back toward 1 — on the
#: shipped ``critical_collapse`` profile it decays 1.43 → 1.21 while the slope is
#: failing. A ratio-only machine therefore detects the *onset* of acceleration and
#: then goes quiet exactly when the acceleration matters most.
#:
#: Which number wins when they disagree is decided by §6.7: ``TERTIARY`` must
#: arrive **before** ``disp`` crosses 60 mm or the stage is a lagging indicator,
#: and on that same profile the seam covers 60 → 100 mm in 4.5 s. A fixed 10 s
#: hold on the strong signal cannot satisfy that, so 10 s stays where §6.8 put it
#: — on the weak signal — and the strong one commits after three consecutive
#: rising evaluations, which still cannot be produced by a single noisy window.
TERTIARY_HOLD_S = 10.0
TERTIARY_RISE_HOLD_S = 3.0

#: A rise has to clear this relative step to count as one. One percent is far
#: above the sensor noise on a velocity estimated over a 10 s window and far
#: below the ~15 %/s a failing slope shows.
RISE_MIN_STEP = 0.01

#: The curvature route into TERTIARY (§6.2's OR branch) and its exit.
CURVATURE_ENTER = 0.5
CURVATURE_EXIT = 0.35

#: Stage evaluation is a 1 Hz job (§6.5). It is a slow variable; running it at
#: the ticker's 20 Hz would only add noise to the streaks it counts.
STAGE_EVAL_HZ = 1.0

#: Reference jerk for the curvature term — 5 mm/s³ is a lively event on this rig.
JERK_REF = 5.0

#: Confidence is its own scale, *not* a severity scale. An earlier revision of
#: this module demoted through ``severity_rank`` and a LOW demotion silently did
#: nothing, because "LOW" is not in ``SEVERITY_ORDER`` and both it and "MEDIUM"
#: ranked -1: a stale node reported MEDIUM confidence with a reason that said
#: "no fresh frame from this node". Two scales, two orders, no shared vocabulary.
CONFIDENCE_ORDER: tuple[str, ...] = ("LOW", "MEDIUM", "HIGH")

#: Relative M1/M2 gap above which confidence is demoted.
DISAGREEMENT_MEDIUM = 0.35

#: Held-out MAE (s) thresholds for confidence.
MAE_GOOD_S = 2.0
MAE_POOR_S = 10.0

#: The speed below which a seam is not creeping at all. Read from the calibration
#: file when it is loadable (``steady_creep_velocity_threshold_mm_s``), because
#: that is the same 0.05 mm/s the Fukuzono estimator uses to decide a run is
#: converging — one threshold, one meaning.
_DEFAULT_MIN_CREEP_MM_S = 0.05

#: Displacement at which the seam has breached (mm). Also the calibration's value.
_DEFAULT_BREACH_MM = 100.0


# ---------------------------------------------------------------------------
# Severity
# ---------------------------------------------------------------------------
def severity_from_ttf(ttf: float | int | str | None,
                      bands: Sequence[float] = TTF_BANDS) -> str:
    """``CRITICAL / HIGH / ELEVATED / NOMINAL`` from a TTF, ``WATCH`` if unknown.

    Accepts the two shapes the system actually carries: the integer the pill
    prints, and the ``"N/A"`` it prints when there is no estimate. Anything that
    is not a usable number — ``None``, NaN, a string, a negative — is *unknown*,
    and unknown is WATCH: visible, never alarming, never silent.
    """
    if ttf is None or isinstance(ttf, str):
        return "WATCH" if isinstance(ttf, str) else "WATCH"
    try:
        t = float(ttf)
    except (TypeError, ValueError):
        return "WATCH"
    if not math.isfinite(t) or t < 0:
        return "WATCH"
    # Band 0 is the floor of the table (0 s) and is not consulted: everything
    # below `high` is CRITICAL, including negative values, which the caller has
    # already rejected. Named rather than dropped so the indexing stays legible.
    _floor, high, elev, _watch = (float(bands[0]), float(bands[1]),
                                  float(bands[2]), float(bands[3]))
    if t < high:
        return "CRITICAL"
    if t < elev:
        return "HIGH"
    if t < _watch:
        return "ELEVATED"
    return "NOMINAL"


def severity_rank(tier: str) -> int:
    """Index in :data:`SEVERITY_ORDER`; unknown tiers rank below NOMINAL.

    Phase 8 needs ``rank(tier) >= rank("HIGH")`` to express the firewall rule.
    An unrecognised string must not accidentally outrank a real emergency, so
    unknown input returns ``-1`` rather than raising.
    """
    try:
        return SEVERITY_ORDER.index(str(tier).upper())
    except ValueError:
        return -1


def worst_severity(tiers: Iterable[str]) -> str:
    """The most severe of a set — what a system-wide banner should show."""
    best = "NOMINAL"
    for t in tiers:
        if severity_rank(t) > severity_rank(best):
            best = str(t).upper()
    return best


# ---------------------------------------------------------------------------
# Curvature
# ---------------------------------------------------------------------------
def curvature_score(feats: Mapping[str, Any] | Sequence[float] | None) -> float:
    """Second-derivative energy: how much the creep is *accelerating*, in [0, 1].

    The primary term is the relative velocity gain ``(v10 - v60) / v60``, mapped
    through ``r / (1 + r)`` so the scale is bounded and comparable across nodes:
    0 is steady, 0.5 is "the last 10 s is twice as fast as the last 60 s", and
    it approaches — but never reaches — 1. A positive jerk term adds up to 0.25,
    which is enough to lift an already-rising window into §6.2's ``> 0.5``
    TERTIARY branch without letting jerk alone declare an emergency.

    Accepts a feature mapping (``v10``/``v60``, optional ``jerk``) or the
    ``(v10, v60, jerk)`` triple. Missing inputs give 0.0: no evidence of
    acceleration is not evidence of acceleration.
    """
    v10 = v60 = jerk = None
    if isinstance(feats, Mapping):
        v10, v60, jerk = feats.get("v10"), feats.get("v60"), feats.get("jerk")
    elif isinstance(feats, Sequence) and len(feats) >= 2:
        v10, v60 = feats[0], feats[1]
        jerk = feats[2] if len(feats) > 2 else None
    try:
        v10 = float(v10)                                   # type: ignore[arg-type]
        v60 = float(v60)                                   # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(v10) and math.isfinite(v60)):
        return 0.0

    base = 0.0
    if v60 > 0.0 and v10 > v60:
        r = (v10 - v60) / v60
        base = r / (1.0 + r)

    extra = 0.0
    try:
        j = float(jerk)                                    # type: ignore[arg-type]
        if math.isfinite(j) and j > 0.0:
            extra = 0.25 * (1.0 - math.exp(-j / JERK_REF))
    except (TypeError, ValueError):
        pass
    return float(min(1.0, max(0.0, base + extra)))


def _min_creep_mm_s() -> float:
    """The calibration's convergence threshold, or the documented default."""
    try:
        from engine import kinematic as kin
        return float(kin.calibration_info().get("breach_limit_mm") is not None) * 0.0 \
            + float((kin.calibration() or {}).get("steady_creep_velocity_threshold_mm_s",
                                                  _DEFAULT_MIN_CREEP_MM_S))
    except Exception:                                     # noqa: BLE001
        return _DEFAULT_MIN_CREEP_MM_S


def breached(state, node) -> bool:
    """Has this node's seam opened past the limit?

    Takes the **larger** of the node's own displacement and the newest buffered
    sample. The buffer holds ``simulator.measure()``'s noised reading while the
    node holds the integrated value, and on the bench they straddle the limit by
    a few tenths of a millimetre: a breached seam was seen reporting 99.8 mm on
    one 1 Hz evaluation and 100.2 mm on the next, which un-latched FAILURE and
    flipped the stage back to STABLE. A failed slope does not un-fail because a
    noisy sample reads 0.2 mm short.
    """
    if node is None:
        return False
    limit = _breach_mm()
    own = float(getattr(node, "displacement_mm", 0.0) or 0.0)
    buf = (getattr(state, "buffers", {}) or {}).get(
        getattr(node, "node_id", "")) or []
    last = float(buf[-1][1]) if buf else 0.0
    return max(own, last) >= limit


def _breach_mm() -> float:
    try:
        from engine import kinematic as kin
        return float((kin.calibration() or {}).get("breach_limit_mm", _DEFAULT_BREACH_MM))
    except Exception:                                     # noqa: BLE001
        return _DEFAULT_BREACH_MM


def _velocities(state, nid: str) -> tuple[float, float, float]:
    """``(v10, v60, disp_now)`` for one node, from its own window.

    Read off the 60 s buffer rather than the node's instantaneous ``velocity``,
    because the stage rules are statements about *windows*: a second of silence
    in the velocity field is not a change of stage. Falls back to the node's live
    velocity for both windows when no buffer exists yet, which yields STABLE —
    the honest answer for a node that has not yet been watched for a minute.
    """
    buf = (getattr(state, "buffers", {}) or {}).get(nid) or []
    node = None
    try:
        node = (getattr(state, "nodes", {}) or {}).get(nid)
    except Exception:                                     # noqa: BLE001
        node = None
    if len(buf) < 2:
        v = float(getattr(node, "velocity", 0.0) or 0.0)
        disp = float(getattr(node, "displacement_mm", 0.0) or 0.0)
        return v, v, disp

    ts = np.array([float(s[0]) for s in buf], dtype=float)
    disp = np.array([float(s[1]) for s in buf], dtype=float)
    now_t, now_d = float(ts[-1]), float(disp[-1])

    def _vel(window_s: float) -> float:
        # The newest sample at or before now - window_s, so a burst of frames
        # arriving together cannot make a 10 s window span 0.2 s of wall time.
        want = now_t - window_s
        idx = np.searchsorted(ts, want, side="right") - 1
        if idx < 0:
            idx = 0
        dt = now_t - float(ts[idx])
        if dt <= 0.0:
            return 0.0
        return (now_d - float(disp[idx])) / dt

    return _vel(10.0), _vel(60.0), now_d


# ---------------------------------------------------------------------------
# Progression stage
# ---------------------------------------------------------------------------
def progression_stage(state, nid: str, now: float | None = None) -> str:
    """The node's creep stage, with §6.8's hysteresis applied.

    Rules (§6.2), evaluated on the node's own window:

    ===========  ==========================================================
    ``FAILURE``  ``disp >= breach limit`` (100 mm) — the seam has opened
    ``TERTIARY`` ``v10 > 1.25 v60`` for 10 s, **or** ``curvature > 0.5`` for 10 s
    ``SECONDARY`` ``v60 > creep threshold`` and ``v10 ~= v60`` within 25%
    ``PRIMARY``  creeping and slowing — ``v10 < v60``
    ``STABLE``   not creeping at all (``v60 <=`` the 0.05 mm/s threshold)
    ===========  ==========================================================

    Order matters: FAILURE is checked first because a breached seam is not in a
    *stage of creep* any more, and TERTIARY is checked before the steady rules
    because an accelerating node also satisfies "creeping".
    """
    node = (getattr(state, "nodes", {}) or {}).get(nid)
    if node is None:
        return "STABLE"

    v10, v60, disp = _velocities(state, nid)
    min_creep = _min_creep_mm_s()

    # FAILURE is **latched**: once the seam has opened past the limit it stays
    # failed until a Re-arm, exactly as the crack engine's stages latch (§3).
    # Un-latching here was a real defect found on the live server: the stage
    # oscillated FAILURE → STABLE → FAILURE while displacement sat pinned at the
    # limit, because the measured value crossed back and forth across it.
    if getattr(node, "stage", None) == "FAILURE" or breached(state, node):
        return _settle(node, "FAILURE", now)

    cur = curvature_score({"v10": v10, "v60": v60})
    node.curvature = cur

    # The weak signal (§6.8's ratio), the second-derivative signal, and the
    # strong one: the creep rate rose again since the last evaluation.
    accelerating = (v60 > min_creep and v10 > TERTIARY_ENTER_RATIO * v60) \
        or (cur > CURVATURE_ENTER)

    prev_v10 = getattr(node, "stage_last_v10", None)
    rising = bool(
        v60 > min_creep and prev_v10 is not None
        and math.isfinite(float(prev_v10))
        and v10 > float(prev_v10) * (1.0 + RISE_MIN_STEP)
    )

    current = getattr(node, "stage", "STABLE") or "STABLE"
    if current in ("TERTIARY", "FAILURE"):
        # Exit test — the *smaller* ratio, so a node leaving TERTIARY has to
        # genuinely stop accelerating. This is the whole point of hysteresis.
        still = (v60 > min_creep and v10 > TERTIARY_EXIT_RATIO * v60) \
            or (cur > CURVATURE_EXIT) or rising
        if still:
            return _settle(node, "TERTIARY", now)
        # Falling out of TERTIARY does not jump back to STABLE: a slope that was
        # accelerating a moment ago is at least in secondary creep.

    # Sustained-hold accumulators, in wall-clock seconds between evaluations.
    hold = float(getattr(node, "stage_hold_s", 0.0) or 0.0)
    rise_s = float(getattr(node, "stage_rise_s", 0.0) or 0.0)
    last = getattr(node, "stage_eval_t", None)
    dt = 0.0 if last is None else max(0.0, float(now or time.time()) - float(last))
    # Cap the credit at one evaluation period: a paused server must not come back
    # and declare TERTIARY because 600 s passed while nothing was measured.
    dt = min(dt, 1.0 / STAGE_EVAL_HZ)
    node.stage_eval_t = float(now or time.time())
    node.stage_last_v10 = v10

    if accelerating or rising:
        if accelerating:
            hold += dt
        if rising:
            rise_s += dt
        else:
            # A single flat evaluation breaks the run: "rising for three
            # consecutive seconds" has to mean consecutive.
            rise_s = 0.0
        node.stage_hold_s, node.stage_rise_s = hold, rise_s
        if (accelerating and hold >= TERTIARY_HOLD_S) \
                or (rising and rise_s >= TERTIARY_RISE_HOLD_S):
            return _settle(node, "TERTIARY", now)
        # Held, but not yet long enough to commit. Report — and *record* — the
        # best stage the evidence supports today, which is at least PRIMARY.
        #
        # Recording it matters: the caller reads `node.stage`, so a stage that is
        # returned but never settled would leave the chip reading STABLE while
        # this function was answering PRIMARY, and the event log would miss the
        # entry into PRIMARY entirely. The hold counters are what protect TERTIARY
        # from flicker; the stage below it is not in doubt.
        reported = current if current in ("SECONDARY", "PRIMARY") else "PRIMARY"
        return _settle(node, reported, now)

    node.stage_hold_s = 0.0
    node.stage_rise_s = 0.0
    if v60 <= min_creep:
        return _settle(node, "STABLE", now)
    if v10 < v60:
        return _settle(node, "PRIMARY", now)
    if abs(v10 - v60) <= 0.25 * max(v60, 1e-9):
        return _settle(node, "SECONDARY", now)
    # Creeping, not clearly slowing, not yet sustained-accelerating: steady.
    return _settle(node, "SECONDARY", now)


def _settle(node, stage: str, now: float | None) -> str:
    """Write the stage onto the node, recording when it changed."""
    if getattr(node, "stage", None) != stage:
        node.stage = stage
        node.stage_since = float(now or time.time())
    return stage


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------
def confidence_band(meta: Mapping[str, Any] | None,
                    n_windows: int = 0,
                    stale: bool = False,
                    disagreement: float | bool | None = None) -> tuple[str, str]:
    """``(tier, reason)`` — how much the countdown should be trusted, and why.

    Four inputs, each able to demote, none able to promote (§6.2.4):

    ``n_windows``  completeness of the node's window against ``config.MODEL_BUF``
    ``stale``      no frame inside the staleness window
    ``meta``       the model artifact's own held-out MAE
    ``disagreement``  the relative M1/M2 gap, ``[0, 1]``

    Starts at HIGH and demotes once per failing condition, reporting the **first**
    cause — the operator's next action depends on the first reason, not on a
    paragraph of them. The MAE comes from ``model_meta.json`` rather than being
    typed here, so the number on screen traces back to the artifact (Rule 3).
    """
    reasons: list[str] = []
    tier = "HIGH"

    def demote(to: str, why: str) -> None:
        nonlocal tier
        if CONFIDENCE_ORDER.index(to) < CONFIDENCE_ORDER.index(tier):
            tier = to
        reasons.append(why)

    if not meta:
        demote("MEDIUM", "no model artifact — kinematic fallback")

    mae = None
    try:
        mae = ((meta or {}).get("metrics") or {}).get("held_out_critical_mae_s")
        mae = None if mae is None else float(mae)
    except (TypeError, ValueError):
        mae = None
    if mae is not None:
        if mae > MAE_POOR_S:
            demote("LOW", f"held-out MAE {mae:.1f} s is coarse")
        elif mae > MAE_GOOD_S:
            demote("MEDIUM", f"held-out MAE {mae:.1f} s")

    if stale:
        demote("LOW", "no fresh frame from this node")

    try:
        need = int(getattr(config, "MODEL_BUF", 0) or 0)
    except (TypeError, ValueError):
        need = 0
    if need and int(n_windows or 0) < need:
        demote("MEDIUM", f"window {int(n_windows or 0)}/{need} complete")

    if isinstance(disagreement, bool):
        gap = None
        if disagreement:
            demote("MEDIUM", "models disagree on this window")
    else:
        try:
            gap = None if disagreement is None else float(disagreement)
        except (TypeError, ValueError):
            gap = None
        if gap is not None and gap > DISAGREEMENT_MEDIUM:
            demote("MEDIUM", f"models disagree by {gap * 100:.0f}%")

    if not reasons:
        return "HIGH", "window complete, models agree"
    return tier, reasons[0]


# ---------------------------------------------------------------------------
# Recommended actions
# ---------------------------------------------------------------------------
#: §8.6's table, verbatim. Phase 8 puts the first two lines in the 160-character
#: SMS and all of them in the email; keeping the wording here means the screen,
#: the log and the message cannot drift apart.
_ACTIONS: dict[str, list[str]] = {
    "NOMINAL": ["Monitor. No action required."],
    "WATCH": ["Monitor. No action required."],
    "ELEVATED": ["Restrict entry to zone.", "Increase monitoring to continuous."],
    "HIGH": ["Withdraw non-essential personnel.", "Prepare evacuation.",
             "Verify route."],
}
_CRITICAL_DEFAULT = ["EVACUATE NOW.", "Do not enter the zone."]
_FAILURE_ANY = ["EVACUATE. Do not approach.",
                "Account for all personnel at muster."]


def recommended_actions(sev: str, stage: str,
                        route: Mapping[str, Any] | str | None = None,
                        people_underground: int | bool | None = None) -> list[str]:
    """Ranked instructions for one alert — most urgent first.

    ``route`` may be the two route strings the state carries (``"OPEN"`` /
    ``"BLOCKED"``, keyed ``A``/``B``) or any mapping whose values expose ``open``;
    an unreachable route is named with the exit that is still open, because
    "Route A BLOCKED" without "use Exit 2" is a problem statement, not an
    instruction (§8.6). ``people_underground`` adds the muster line only when it
    is true or counts a positive number — a muster instruction when nobody is
    down there is noise, and noise is how a real instruction gets ignored.
    """
    sev, stage = str(sev).upper(), str(stage).upper()

    if stage == "FAILURE":
        # Overrides the tier: a breached seam is never a "monitor" situation.
        actions = list(_FAILURE_ANY)
    elif sev == "CRITICAL":
        actions = ["EVACUATE NOW."]
        blocked, open_exit = _route_state(route)
        if blocked:
            actions.append(f"Route {blocked} BLOCKED — use {open_exit}.")
        else:
            actions.append("All routes open — use the primary route.")
        actions.append("Do not enter the affected zone.")
    else:
        actions = list(_ACTIONS.get(sev, _ACTIONS["WATCH"]))

    if people_underground:
        line = "Account for all personnel at muster."
        if line not in actions:
            actions.append(line)
    return actions


def _route_state(route: Mapping[str, Any] | str | None) -> tuple[str | None, str]:
    """``(blocked_route_name_or_None, name_of_an_open_exit)`` from whatever the
    caller has. Never raises: an unreadable route yields the generic instruction.
    """
    blocked: str | None = None
    exits: dict[str, str] = {"A": "Exit 1", "B": "Exit 2"}
    try:
        if isinstance(route, Mapping):
            for name in ("A", "B"):
                entry = route.get(name)
                if isinstance(entry, Mapping):
                    is_open = entry.get("open")
                    if is_open is False:
                        blocked = name
                    ex = entry.get("exit")
                    if isinstance(ex, str) and ex:
                        exits[name] = ex
                elif isinstance(entry, str):
                    if entry.upper() in ("BLOCKED", "CLOSED", "DOWN"):
                        blocked = name
        elif isinstance(route, str):
            up = route.upper()
            if up in ("BLOCKED", "CLOSED", "DOWN"):
                blocked = "A"
    except Exception:                                     # noqa: BLE001
        return None, "Exit 2"
    other = "B" if blocked == "A" else "A"
    return blocked, exits.get(other, "Exit 2")


# ---------------------------------------------------------------------------
# The /api/ai blocks
# ---------------------------------------------------------------------------
def _cascade_nodes() -> dict[str, dict]:
    """Per-node cascade verdicts, or ``{}`` — never raises (§6.5's rule that a
    blank card is worse than a degraded one)."""
    try:
        from engine import cascade as cascade_engine
        return dict((cascade_engine.cascade_status().get("nodes") or {}))
    except Exception:                                     # noqa: BLE001
        return {}


def _model_meta() -> dict:
    try:
        from engine import ttf as ttf_model
        return dict((ttf_model.MODEL_INFO.get("meta") or {}))
    except Exception:                                     # noqa: BLE001
        return {}


def severity_for_node(state, nid: str, cas: Mapping[str, Any] | None = None) -> dict:
    """One node's severity sub-block, in §6.3's shape."""
    node = (getattr(state, "nodes", {}) or {}).get(nid)
    cas = cas if cas is not None else (_cascade_nodes().get(nid) or {})

    ttf = cas.get("ttf_s")
    if ttf is None:
        try:
            from engine import kinematic as kin
            ttf = kin.kinematic_ttf(state, nid)
        except Exception:                                 # noqa: BLE001
            ttf = None

    tier = severity_from_ttf(ttf)
    stage = getattr(node, "stage", None) or "STABLE"
    # §8.6's table gives FAILURE its own row ("EVACUATE. Do not approach.") for
    # *any* severity, so the tier is floored to match: a node whose seam has
    # opened past 100 mm can never wear a NOMINAL chip next to an instruction to
    # evacuate. The model's TTF is honest — nothing is moving right now — but
    # "not moving" is not the question a breached seam poses.
    if stage == "FAILURE":
        tier = "CRITICAL"
    curvature = float(getattr(node, "curvature", 0.0) or 0.0)
    stale = bool(getattr(node, "stale", False))

    band = cas.get("band")
    if band is not None:
        try:
            band = [round(float(v), 1) for v in band][:3]
        except (TypeError, ValueError):
            band = None

    n_win = len((getattr(state, "buffers", {}) or {}).get(nid) or [])
    dis: float | bool | None = cas.get("disagreement")
    conf, why = confidence_band(_model_meta(), n_win, stale, dis)

    # §6.5's rule that a node must never look *more* certain than it is: a stale
    # node keeps its last tier (a hazard does not end because a radio did) but
    # its confidence has already collapsed, and `stale` is stated outright so the
    # panel can grey the number instead of printing it as if it were live.
    return {
        "tier": tier,
        "ttf": None if ttf is None else (int(round(float(ttf))) if float(ttf) < 3600 else 3600),
        "band": band if band else [None, None, None],
        "stage": stage,
        "curvature": round(curvature, 3),
        "confidence": conf,
        "confidence_reason": why,
        "actions": recommended_actions(
            tier, stage,
            {"A": getattr(state, "route_A_status", "OPEN"),
             "B": getattr(state, "route_B_status", "OPEN")},
            getattr(state, "people_underground", None),
        ),
        "stale": stale,
    }


def severity_snapshot(state) -> dict[str, dict]:
    """The whole ``severity`` block: one sub-block per active node."""
    cas = _cascade_nodes()
    out: dict[str, dict] = {}
    for node in (getattr(state, "nodes", {}) or {}).values():
        nid = getattr(node, "node_id", None)
        if not nid:
            continue
        out[nid] = severity_for_node(state, nid, cas.get(nid) or {})
    return out


def kinematic_block(state) -> dict:
    """The ``kinematic`` block: which rung is answering, and with what physics.

    ``ttf`` is the worst node's estimate from that rung, and ``node`` names which
    node it came from — a system-wide countdown with no attribution is the kind
    of number this phase exists to stop printing.
    """
    try:
        from engine import kinematic as kin
        info = kin.calibration_info()
    except Exception:                                     # noqa: BLE001
        info = {"source": "defaults", "alpha": None, "a_coefficient": None}

    worst_node, worst_ttf, worst_src = None, None, "none"
    for nid in (getattr(state, "nodes", {}) or {}):
        try:
            t = kin.kinematic_ttf(state, nid)
            src = kin.kinematic_source(state, nid)
        except Exception:                                 # noqa: BLE001
            continue
        if t is None:
            continue
        if worst_ttf is None or float(t) < float(worst_ttf):
            worst_node, worst_ttf, worst_src = nid, t, src
    return {
        "source": worst_src,
        "alpha": info.get("alpha"),
        "A": info.get("a_coefficient"),
        "ttf": None if worst_ttf is None else round(float(worst_ttf), 1),
        "node": worst_node,
        "calibration": info.get("source"),
    }


# ---------------------------------------------------------------------------
# The 1 Hz driver
# ---------------------------------------------------------------------------
def severity_tick(state, now: float | None = None) -> list[dict]:
    """Refresh every node's stage, severity and confidence. Returns transitions.

    Called from ``SystemState.update_status_logic()`` (itself called from the
    ticker), rate-limited to :data:`STAGE_EVAL_HZ` because the stage is a slow
    variable and computing it at the ticker's cadence would add nothing but
    streak noise.

    A stage transition is an event in its own right (§6.5) — ``SECONDARY →
    TERTIARY`` is the most quotable line the demo log will produce, and it is
    emitted here, once, at the moment the stage changes.
    """
    now = float(now or time.time())
    last = getattr(state, "severity_last_t", None)
    if last is not None:
        gap = now - float(last)
        # `0.0 <=` and not just `<`: a *negative* gap means the clock moved
        # backwards under us — a Re-arm that cleared the stamp, a replay whose
        # simulated clock starts below the wall clock that was there first, or a
        # test driving the state with its own time base. Freezing the stage
        # machine forever because of a clock change is the one failure this rate
        # limit must not be able to cause, so a backwards step means "run now".
        if 0.0 <= gap < (1.0 / STAGE_EVAL_HZ):
            return []
    state.severity_last_t = now

    cas = _cascade_nodes()
    transitions: list[dict] = []

    for node in list((getattr(state, "nodes", {}) or {}).values()):
        nid = getattr(node, "node_id", None)
        if not nid:
            continue
        before = getattr(node, "stage", "STABLE")
        try:
            stage = progression_stage(state, nid, now)
        except Exception:                                 # noqa: BLE001
            stage = before or "STABLE"
        sub = severity_for_node(state, nid, cas.get(nid) or {})
        node.severity = sub["tier"]
        node.confidence = sub["confidence"]
        node.confidence_reason = sub["confidence_reason"]

        if stage != before:
            transitions.append({
                "node": nid, "from": before, "to": stage,
                "tier": sub["tier"], "ttf": sub["ttf"], "at": now,
            })
            _publish_transition(state, nid, before, stage, sub)
    return transitions


_STAGE_ORDER = ("STABLE", "PRIMARY", "SECONDARY", "TERTIARY", "FAILURE")

#: Plain-language line per transition. Escalations are WARNING-level, FAILURE is
#: CRITICAL — so the event list colours them the way the deck already colours a
#: hazard, and no new level value has to reach the frontend.
_STAGE_TEXT = {
    "PRIMARY": "{n} entered PRIMARY creep — movement detected, decelerating.",
    "SECONDARY": "{n} entered SECONDARY creep — steady movement, no acceleration.",
    "TERTIARY": "{n} entered TERTIARY creep — acceleration sustained.",
    "FAILURE": "{n} has FAILED — displacement past the breach limit.",
    "STABLE": "{n} is stable — no creep measured.",
}


def _publish_transition(state, nid: str, before: str, after: str, sub: Mapping[str, Any]) -> None:
    """One operator line per stage change, at the level the change deserves."""
    label = nid.replace("node_", "Node ")
    text = _STAGE_TEXT.get(after, "{n} stage changed to " + after + ".").format(n=label)
    if after == "FAILURE":
        level = "CRITICAL"
    elif _STAGE_ORDER.index(after) > _STAGE_ORDER.index(before):
        level = "WARNING"
    else:
        level = "NOMINAL"
    if sub.get("ttf") is not None:
        text += f" TTF {int(sub['ttf'])} s."
    try:
        state.note_event(level, text)
    except Exception:                                     # noqa: BLE001
        pass


__all__ = [
    "TTF_BANDS", "SEVERITY_ORDER", "CONFIDENCE_ORDER", "STAGES", "breached", "TERTIARY_ENTER_RATIO",
    "TERTIARY_EXIT_RATIO", "TERTIARY_HOLD_S", "CURVATURE_ENTER", "CURVATURE_EXIT",
    "STAGE_EVAL_HZ", "severity_from_ttf", "severity_rank", "worst_severity",
    "curvature_score", "progression_stage", "confidence_band",
    "recommended_actions", "severity_for_node", "severity_snapshot",
    "kinematic_block", "severity_tick",
]
