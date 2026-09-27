"""
Neural Strata — ``SystemState``: the one owner of live state.
=============================================================

Before Phase 1, live state was a module-level dict in ``app.py`` plus six other
module-level dicts beside it (``node_buffers``, ``peak_accel``, ``history``,
``injections``, ``injection_t``, ``model_ttf``), each written out twice for
``node_1`` and ``node_2``. The literal ``("node_1", "node_2")`` appeared in five
functions. Going to four nodes meant five coordinated edits in one file and a
sixth somewhere else (D10).

Now there is one object. ``config.NODE_IDS`` is the only place the node set is
written down, ``iter_nodes()`` is the only way to walk it, and adding a fifth
node is a one-line change.

What it owns
------------
================================  ===============================================
``lock``                          the reentrant lock every reader takes
``nodes``                         four ``NodeState`` records
``levels`` / ``underground``      per-level status, route and exits
``events``                        the operator event log
``rfid_log`` / ``zones``          tag scans and last-known zone per tag
``buffers``                       per-node 60 s model windows (7 channels)
``history``                       the chart series behind ``/api/history``
``ring``                          UART link state and sequence tracking
``injections``                    the scenario console's armed profiles
================================  ===============================================

The discontinuity rule (D13)
----------------------------
A model window must contain 60 s of **continuous** data. A Re-arm, a sensor
re-baseline, a sequence reset or a >5 s gap all call ``mark_discontinuity()``,
which moves that node's window origin forward. ``window_complete()`` then refuses
to report a window until 60 s of *new* samples have arrived.

That is the difference between a warm-up and a lie: before Phase 1, ``reset()``
set ``displacement_mm = 5.0`` and left the buffer untouched, so the first
forecast after a Re-arm was computed from 60 s of pre-reset samples stitched to a
handful of new ones. It produced a number. The number was meaningless.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from copy import deepcopy

from config import (
    ACC_CRITICAL,
    ACC_WARN_HOLD_S,
    ACC_WARNING,
    ACTIVE_NODES,
    HISTORY_MAX,
    LEVEL_EXITS,
    LEVEL_JUNCTIONS,
    LEVELS,
    MODEL_BUF,
    MODEL_DT,
    NODE_IDS,
)
from contracts import SCHEMA_VERSION, STATUS_RANK, NodeState, worst_status
from engine import crack as crack_engine
from engine import geomech as geomech_engine
from engine import kinematic as kinematic_engine
from engine import severity as severity_engine
from engine import ttf as ttf_model
from engine.health import SOURCE_SIM, HealthLedger, link_state
from ingestion.ring import RingLinks

#: A gap this long, or longer, means the samples on either side are not one
#: continuous record (B1 §1.6).
GAP_INVALIDATES_S = 5.0

#: The reader id → (nominal zone, evacuation zone) table. The deck's ZONES list
#: is exactly these six strings; anything else renders as Headframe, so the hub
#: must never send a zone outside this set.
READER_ZONES = {
    "L1": ("Tunnel A", "Evacuating A"),
    "L2": ("Tunnel B", "Evacuating B"),
    "SURF": ("Safe Exit", "Safe Exit"),
}
UNDERGROUND_ZONES = ("Tunnel A", "Tunnel B", "Evacuating A", "Evacuating B")

#: The roster. ``total`` in the roll call is a headcount from here, not the
#: length of the recent-scan log.
MINERS = {
    "001": "Mohneesh",
    "002": "Anuran",
    "003": "Swayam",
    "004": "Arti",
}

#: Phase 4 moved the status vocabulary into ``contracts`` (the dependency-free
#: leaf) because ``engine.geomech`` ranks the same three words for zones and
#: cannot import this module — this module imports *it*. Imported here and
#: re-exported, so every existing ``from state import worst_status`` keeps
#: working and there is still exactly one literal in the codebase.
# (imported above; kept as module attributes, so it is still a re-export)


def _cascade_pill_ttf() -> int | None:
    """The cascade's worst-node TTF, or ``None`` when it has no opinion yet.

    Deliberately narrow: it reads the one public surface (``cascade_status()``)
    rather than poking at ``_DECISIONS``, so this stays true if the cascade's
    internals move in Phase 6.

    The import is deferred and the whole call is inside ``try`` because
    ``state.py`` is imported by the app *and* by every test: a Phase-5 import
    cycle, or a cascade that is mid-Phase-6-refactor, must never be able to stop
    the state from updating — the pill would go blank while the system was
    otherwise healthy, which is precisely the failure the chain exists to avoid.
    """
    try:
        from engine import cascade as cascade_engine
        nodes = cascade_engine.cascade_status().get("nodes") or {}
    except Exception:                                        # noqa: BLE001
        return None
    vals = [n.get("ttf_s") for n in nodes.values() if n.get("ttf_s") is not None]
    return int(min(vals)) if vals else None


class SystemState:
    """Live state for the whole edge server. One instance, held by ``app``."""

    def __init__(self, node_ids=None, levels=None, started_at: float | None = None):
        self.lock = threading.RLock()
        self.started_at = time.time() if started_at is None else float(started_at)

        #: The registry. Every loop in the process walks this, and nothing else.
        self.node_ids: tuple[str, ...] = tuple(node_ids or ACTIVE_NODES)
        self.levels_map: dict[int, tuple[str, ...]] = dict(levels or LEVELS)

        self.nodes: dict[str, NodeState] = {n: NodeState(node_id=n) for n in self.node_ids}
        # Denormalised onto the node: every frame writes it to the database
        # (migration 002), and a query that has to join config to find out which
        # level a row came from is a query the database cannot answer.
        for _n_id, _node in self.nodes.items():
            _node.level = self.level_of(_n_id)
        self.armed = True
        #: Wall-clock time until which sensor samples must not raise status,
        #: alerts or route changes. Set by Re-arm. Tests leave this at 0.
        self.rearm_grace_until = 0.0

        self.system_status = "NOMINAL"
        self.timestamp = ""
        self.degraded_mode = "FULL"
        self.cascade_mode = "advisory"
        #: Phase 6 — when the stage machine last ran. It is a slow variable and
        #: §6.5 evaluates it at 1 Hz, so this is the rate limit rather than a
        #: timestamp of interest.
        self.severity_last_t: float | None = None

        self.levels: dict[str, dict] = {
            # `pair_warn` is Phase 4's additive key, present from boot so a
            # consumer never has to handle "missing before the first tick".
            str(lv): {"status": "NOMINAL", "route": "OPEN",
                      "exits": list(LEVEL_EXITS.get(lv, ())), "pair_warn": False}
            for lv in sorted(self.levels_map)
        }
        self.underground: dict = {
            "route_A_status": "OPEN",
            "route_B_status": "OPEN",
            # Phase 7 (B5). `routes` is the router's whole answer; `exit_A/exit_B`
            # are the chosen exits; `holds` is the fail-safe list. All four are
            # recomputed every status pass — nothing here is ever set by hand.
            "routes": {},
            "exit_A": None,
            "exit_B": None,
            "holds": [],
            "junctions": {j: "OK" for lv in sorted(LEVEL_JUNCTIONS) for j in LEVEL_JUNCTIONS[lv]},
        }

        self.events: list[dict] = [{
            "time": time.strftime("%H:%M:%S"),
            "level": "NOMINAL",
            "text": "System initialised — awaiting hardware telemetry.",
        }]
        self.rfid_log: list[dict] = []
        self.zones: dict[str, str] = {}
        #: Last report from each level-hub RC522. A swipe that never leaves the
        #: board otherwise looks identical to a reader nobody has used.
        self.rfid_readers: dict[str, dict] = {}

        #: Phase 9 (B6) — **the event funnel's second half.**
        #:
        #: `note_event` is already the single writer of the operator log, but until
        #: this hook existed the alert layer only saw the events that reached
        #: ``app.push_event()``. Every event raised *inside* the state machines —
        #: a dark node, a sequence reset, a ring segment fault — wrote to the log
        #: and never reached a channel. That is the quiet version of losing an
        #: alert: it is on the screen, in the log, and nowhere an operator is.
        #:
        #: A sink rather than an import, because `state` must not know about the
        #: alert layer (the dependency runs the other way), and a callable
        #: attribute rather than a class hierarchy because there is exactly one
        #: consumer and it is set once, at boot, by `app.py`.
        #:
        #: A sink that raises must never break the state machine that was logging
        #: something — hence the guard below, which is the same rule the dispatcher
        #: already follows for its channels.
        self.event_sink = None

        #: Sample-clock model windows, 7 channels each (B1 §1.4).
        self.buffers: dict[str, deque] = {n: deque(maxlen=MODEL_BUF) for n in self.node_ids}
        #: Per-node sample clock bookkeeping.
        self._last_t: dict[str, float | None] = {n: None for n in self.node_ids}
        self._first_arrival: dict[str, float] = {}
        self._window_origin: dict[str, float | None] = {n: None for n in self.node_ids}
        self._last_ms: dict[str, float | None] = {n: None for n in self.node_ids}
        #: Wall-clock arrival of the last sample per node. Distinct from the
        #: sample clock on purpose — one answers "what does the node think the
        #: time is", the other answers "is it still talking to me".
        self.last_arrival: dict[str, float] = {}
        #: Which clock a node's current window is built from: ``"sample"`` (frames
        #: carried it — the node's own ``ms``) or ``"local"`` (the server ticked
        #: it, because no frames are arriving). **A window may never mix the
        #: two.** Both are seconds, both look plausible, and a buffer stitched
        #: from them produces a forecast that is wrong in a way nothing can see.
        self._window_clock: dict[str, str | None] = {n: None for n in self.node_ids}
        self.clock_mismatches = 0
        self.window_clock_takeovers = 0

        self.peak_accel: dict[str, float] = {n: 0.0 for n in self.node_ids}
        self.history: dict[str, deque] = {n: deque(maxlen=HISTORY_MAX) for n in self.node_ids}

        self.injections: dict[str, str | None] = {n: None for n in self.node_ids}
        self.injection_t: dict[str, float] = {n: 0.0 for n in self.node_ids}

        self.via: dict[str, str | None] = {n: None for n in self.node_ids}
        self.ring = RingLinks()
        # A back-pointer, set once: the registry narrates its own
        # transitions through this state's event log (Phase 9).
        self.ring.owner = self
        #: Phase 2 — is the USB link to the main hub open *right now*. Set by the
        #: reader thread (``ingestion.serial_reader``); **announced** only by
        #: ``engine.health.health_tick``. The separation is the point: one edge
        #: detector, one announcer, so `SERIAL_LOST` cannot be emitted twice or
        #: missed once.
        self.serial_up: bool = False
        #: Where frames are coming from — ``"usb"`` / ``"replay"`` / ``"sim"``.
        #: ``"sim"`` is what makes ``NO_HARDWARE`` a precise claim rather than a
        #: guess: it means nothing real is feeding this process.
        self.hardware_source: str = SOURCE_SIM
        #: Arrival of the last ``J2↔J3`` cross-link heartbeat. H3 populates it;
        #: ``None`` reports UNKNOWN, never DOWN.
        self.cross_link_seen: float | None = None
        #: The health edge-detector memo. See ``engine.health.HealthLedger``.
        self.health = HealthLedger()
        #: The current watchdog banner (dict) or ``None``. Recomputed each tick.
        self.watchdog: dict | None = None

        self.derivative_skips = 0
        self.discontinuities = 0

        #: Injected by ``app.py``. Appends the node's georeference to status
        #: events ("· 23.64300°N 85.31800°E"). A callable rather than an import,
        #: because ``app`` imports ``state`` and not the other way round.
        self.geo_tag = lambda n_id: ""

    # -- iteration & lookup -------------------------------------------------

    def iter_nodes(self):
        """The only way to walk the node set. Replaces six tuple literals."""
        return (self.nodes[n] for n in self.node_ids)

    def node(self, n_id: str) -> NodeState:
        return self.nodes[n_id]

    def levels_of(self, n_id: str) -> list[int]:
        return [lv for lv, members in self.levels_map.items() if n_id in members]

    def level_of(self, n_id: str) -> int | None:
        lvs = self.levels_of(n_id)
        return lvs[0] if lvs else None

    def index_of(self, n_id: str) -> int:
        return NODE_IDS.index(n_id) + 1 if n_id in NODE_IDS else 0

    # -- link / relay bookkeeping ------------------------------------------

    def note_via(self, n_id: str, via: str | None) -> None:
        self.via[n_id] = via

    def via_of(self, n_id: str) -> str | None:
        return self.via.get(n_id)

    def injection_holds(self, n_id: str) -> bool:
        """True while the scenario console owns this node.

        A bench frame arriving mid-injection must not fight the operator: the
        console is a deliberate override, and a half-injected node is how you get
        a demo that changes its mind twice a second.
        """
        return bool(self.injections.get(n_id))

    # -- sample clock (D5) --------------------------------------------------

    def first_arrival(self, n_id: str) -> float:
        return self._first_arrival.setdefault(n_id, time.time())

    def measured_dt(self, n_id: str, ms, t_sample: float) -> float | None:
        """Frame-to-frame interval in **seconds, on the node's own clock**.

        ``t_sample`` already prefers the frame's ``ms``; this method only keeps
        the previous sample's value and subtracts. ``None`` means "no previous
        sample" — the first frame after boot, from a node that has just been
        reset, or after a declared gap.

        The raw interval is returned, **unclamped**. ``compute_derivatives``
        decides what to do with a bad one: clamping here would silently turn a
        4 s outage into a 2 s interval and hand the product a velocity spike.
        """
        self._window_origin  # touch: keeps the attribute visible to linters
        prev = self._last_t.get(n_id)
        self._last_t[n_id] = t_sample
        self._last_ms[n_id] = None if ms is None else float(ms)
        if prev is None:
            return None
        return float(t_sample) - float(prev)

    def note_arrival(self, n_id: str, t: float | None = None) -> None:
        self.last_arrival[n_id] = time.time() if t is None else float(t)

    def age_of(self, n_id: str, now: float | None = None) -> float:
        """Seconds since this node last delivered a sample (0.0 if never)."""
        now = time.time() if now is None else float(now)
        t = self.last_arrival.get(n_id)
        return max(0.0, now - t) if t is not None else 0.0

    def mark_discontinuity(self, n_id: str, reason: str) -> None:
        """Declare the sample stream broken. The window starts again from here.

        Called by Re-arm, by sensor re-baseline, by a sequence reset, and by a
        gap longer than ``GAP_INVALIDATES_S``. After this call
        ``window_complete()`` is ``False`` until 60 s of *new* samples land.
        """
        origin = self._last_t.get(n_id)
        self._window_origin[n_id] = float(origin) if origin is not None else 0.0
        self.buffers[n_id].clear()
        self._window_clock[n_id] = None
        self._last_t[n_id] = None
        self.discontinuities += 1
        self.note_event("WARNING", f"{n_id.upper()} window invalidated — {reason}.")

    def window_complete(self, n_id: str) -> bool:
        """True when this node has a full, **continuous** 60 s model window."""
        buf = self.buffers.get(n_id)
        if not buf or len(buf) < MODEL_BUF:
            return False
        origin = self._window_origin.get(n_id)
        if origin is None:
            return True
        return float(buf[0][0]) >= float(origin)

    # -- the model buffer (D13 + the replay-clock fix) ----------------------

    def append_model_buffer(self, n_id: str, t: float, disp: float, tilt: float,
                            acc: float, vib: int = 0, micro=None, macro=None,
                            clock: str = "sample") -> bool:
        """Append one sample to a node's 60 s window. Returns True if stored.

        **Seven channels**: ``t, disp, tilt, acc, vib, micro, macro`` (B1 §1.4).
        The feature extractor reads the first four today; Phase 5 uses the rest.
        The shape is written once here so the retrain has nothing to re-plumb.

        **Sample clock wins; the local tick is a revocable guess.** A window is
        rebuilt from real frames whenever it was only ever filled by the ticker —
        see the takeover branch below.

        **Keyed on the sample's clock, decimated to the training cadence.** The
        Phase 0 buffer was appended by the ticker thread on wall-clock 2 Hz ticks,
        which meant a recording played back at 10x produced one sample per 5 s of
        recording — a 60 s window would take 300 s of wall clock to fill, so a
        fast replay could never reach ``/api/ai`` ACTIVE. Appending on the sample
        clock makes the window depend on the *recording's* timeline, which is the
        same timeline the model was trained on.
        """
        buf = self.buffers.get(n_id)
        if buf is None:
            return False
        current_clock = self._window_clock.get(n_id)
        if current_clock is None:
            self._window_clock[n_id] = clock
        elif current_clock != clock:
            # Two clocks must never share a window — but "which one wins" is not a
            # symmetric question, and treating it as one was a real defect.
            #
            # At cold boot the ticker reaches every node within its first 0.5 s
            # tick, because no node has been heard from yet (age is unbounded) and
            # the bench case needs *some* window. That fill is a guess on the wall
            # clock. When the first real frame then arrives on the sample clock,
            # the old rule refused it — and refused every frame after it, for the
            # life of the process, so `/api/ai` could never go ACTIVE from real
            # data and quietly ran on wall-clock snapshots instead. On the rig that
            # is total: ESP-NOW frames would never reach the model.
            #
            # So the guess is revocable and the measurement is not: a sample-clock
            # frame discards a local-clock window and restarts it. A local tick
            # never displaces a window that already holds real samples, and the
            # two still never interleave. The counter makes the takeover visible
            # rather than silent.
            if clock == "sample" and current_clock == "local":
                buf.clear()
                self._window_origin[n_id] = None
                self._window_clock[n_id] = clock
                self.window_clock_takeovers += 1
            else:
                self.clock_mismatches += 1
                return False
        origin = self._window_origin.get(n_id)
        if origin is not None and float(t) < float(origin):
            return False                      # pre-discontinuity sample
        if buf and (float(t) - float(buf[-1][0])) < (MODEL_DT * 0.9):
            return False                      # faster than the training cadence
        buf.append((float(t), float(disp), float(tilt), float(acc),
                    int(vib or 0), micro, macro))
        return True

    # -- peaks, history, events --------------------------------------------

    def update_peak(self, n_id: str) -> None:
        node = self.nodes[n_id]
        self.peak_accel[n_id] = max(self.peak_accel.get(n_id, 0.0), abs(float(node.acceleration)))

    def note_event(self, level: str, text: str, kind: str | None = None) -> None:
        """Prepend one operator event.

        **Newest first, capped at 64** — the shipped deck renders index 0 as the
        latest line, so this order is part of the wire contract, not a style
        choice. The cap matches v4.4 exactly; a longer log would push the
        interesting line off a fixed-height panel.

        ``kind`` is Phase 2's addition and is **purely additive**: ``level`` stays
        in ``{NOMINAL, WARNING, CRITICAL}`` because the deck colours the event
        list from it, so widening that field would render an unknown-level line in
        a default colour on stage. ``kind="INFRASTRUCTURE"`` is how §2.2's
        "visible, but never mixed with a geological severity tier" is satisfied
        without touching the UI contract — the tag travels *beside* the level
        rather than replacing it. Phase 8's dispatcher reads ``kind``.
        """
        event = {
            "time": time.strftime("%H:%M:%S"),
            "level": level,
            "text": text,
        }
        if kind is not None:
            event["kind"] = kind
        self.events.insert(0, event)
        if len(self.events) > 64:
            self.events.pop()

        # Phase 9 — hand the line to whoever is listening (the alert layer, at
        # boot). A sink that throws is caught here: the log line is already
        # written, and "the alert channel is broken" must not turn into "the
        # state machine is broken".
        sink = self.event_sink
        if sink is not None:
            try:
                sink(level, text, kind)
            except Exception:                                   # noqa: BLE001
                pass

    def snapshot_history(self) -> None:
        """One chart sample per node. The dashboard's line series is this."""
        for node in self.iter_nodes():
            self.history[node.node_id].append({
                "status": node.status,
                "tilt_x": float(node.tilt_x),
                "displacement_mm": float(node.displacement_mm),
                "acceleration": float(node.acceleration),
            })

    # -- status cascade ----------------------------------------------------

    def rearm_grace_remaining(self, now: float | None = None) -> float:
        """Seconds left in the prototype hold. Zero when the hold is off or over."""
        now = time.time() if now is None else float(now)
        left = float(getattr(self, "rearm_grace_until", 0.0) or 0.0) - now
        return left if left > 0.0 else 0.0

    def update_status_logic(self, now: float | None = None) -> None:
        """Per-node status → per-level status → the two route keys (D7).

        The rule the dashboard reads is unchanged; what changed is *where it comes
        from*. Route A is Level 1's route and Route B is Level 2's, derived from
        the levels the nodes are actually on. Before Phase 1 those two keys were
        hardcoded to ``node_1`` and ``node_2``, so a Level-2 problem blocked a
        Level-1 tunnel. Phase 7 replaces the block/open verdict with real
        hazard-cost routing; the keys stay.
        """
        if not self.armed:
            return

        now = time.time() if now is None else float(now)
        grace = self.rearm_grace_remaining(now)
        if grace > 0.0:
            for node in self.iter_nodes():
                node.status = "NOMINAL"
                node.severity = "NOMINAL"
            self.system_status = "NOMINAL"
            for lv in self.levels_map:
                self.levels[str(lv)] = {
                    "status": "NOMINAL",
                    "route": "OPEN",
                    "exits": list(LEVEL_EXITS.get(lv, ())),
                    "pair_warn": False,
                }
            self._update_routes()
            return

        worst = "NOMINAL"
        ttf_lowest = float("inf")

        for node in self.iter_nodes():
            a = float(node.acceleration)

            prev = node.status
            # Same lines as the dashboard alert table. Critical is instant.
            # Warning has to stay above 0.25 for the hold, so one noisy
            # sample does not light the lamp.
            held = getattr(self, "_warn_since", None)
            if held is None:
                held = {}
                self._warn_since = held
            if a >= ACC_CRITICAL:
                node.status = "CRITICAL"
                # Keep the warning timer. This stretch is already above 0.25,
                # so a fall back under 3.0 must become a warning at once.
                # Clearing the timer here made that fall look nominal and
                # the hub was told to go green and silent for the hold.
            elif a > ACC_WARNING:
                started = held.get(node.node_id)
                if started is None and prev in ("WARNING", "CRITICAL"):
                    started = now - ACC_WARN_HOLD_S
                    held[node.node_id] = started
                elif started is None:
                    held[node.node_id] = now
                    started = now
                node.status = "WARNING" if (now - started) >= ACC_WARN_HOLD_S else "NOMINAL"
                # #region agent log
                if prev == "CRITICAL" and node.status == "WARNING":
                    try:
                        import json as _json
                        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps({
                                "sessionId": "83ae1d", "hypothesisId": "HOLD",
                                "runId": "post-fix",
                                "location": "state.py:update_status_logic",
                                "message": "critical fell into the warning band",
                                "data": {"node": node.node_id, "accel": round(a, 3),
                                         "status": node.status},
                                "timestamp": int(time.time() * 1000),
                            }) + "\n")
                    except Exception:
                        pass
                # #endregion
            else:
                held.pop(node.node_id, None)
                node.status = "NOMINAL"

            # Phase 3 (§3.5) — a hazard is not only made of vibration. A seam
            # that has opened past the critical limit is a fall hazard while the
            # accelerometer reads nothing at all, and a system that reports
            # NOMINAL through that is reporting the quiet, not the danger. The
            # escalation is *to WARNING only*: a critical acceleration is its
            # own verdict, and a second author of CRITICAL here would be a
            # second opinion about the same number. A reading back under the
            # warning line returns the node to nominal on the next pass.
            if (node.status != "CRITICAL"
                    and crack_engine.crack_stage(self, node.node_id) == crack_engine.STAGE_CRITICAL):
                node.status = "WARNING"

            # #region agent log
            if node.status in ("WARNING", "CRITICAL") and prev != node.status:
                try:
                    import json as _json
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d",
                            "hypothesisId": "R",
                            "location": "state.py:update_status_logic",
                            "message": "node left nominal",
                            "data": {"node": node.node_id, "status": node.status,
                                     "accel": round(a, 3),
                                     "crack": crack_engine.crack_stage(self, node.node_id)},
                            "timestamp": int(time.time() * 1000),
                        }) + "\n")
                except Exception:
                    pass
            if prev in ("WARNING", "CRITICAL") and node.status == "NOMINAL":
                try:
                    import json as _json
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d",
                            "hypothesisId": "S",
                            "location": "state.py:update_status_logic",
                            "message": "node returned to nominal",
                            "data": {"node": node.node_id, "prev": prev, "accel": a},
                            "timestamp": int(time.time() * 1000),
                        }) + "\n")
                except Exception:
                    pass
            # A fingertip used to cross the old 0.25 line. Log that band so a
            # touch can be checked against the new warning and critical lines.
            if a > 0.25:
                touched = getattr(self, "_touch_log_at", None)
                if touched is None:
                    touched = {}
                    self._touch_log_at = touched
                if now - touched.get(node.node_id, 0.0) >= 2.0:
                    touched[node.node_id] = now
                    try:
                        import json as _json
                        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps({
                                "sessionId": "83ae1d",
                                "hypothesisId": "T",
                                "runId": "post-fix",
                                "location": "state.py:update_status_logic",
                                "message": "accel above old touch line",
                                "data": {
                                    "node": node.node_id,
                                    "accel": round(a, 3),
                                    "status": node.status,
                                    "warning": ACC_WARNING,
                                    "critical": ACC_CRITICAL,
                                },
                                "timestamp": int(time.time() * 1000),
                            }) + "\n")
                    except Exception:
                        pass
            # #endregion

            if node.status == "CRITICAL":
                worst = "CRITICAL"
            elif node.status == "WARNING" and worst != "CRITICAL":
                worst = "WARNING"

            # Phase 6 (§6.2) — **this line is the D2 fix.** It used to compute
            # `ttf = v / a`: the time for velocity to reach zero under constant
            # deceleration, which sounds like a time-to-failure and is not one.
            # The training feature `ttf_fuk` is the intercept of a 1/v fit with
            # the time axis; on the day the model files go missing the two would
            # have disagreed about what "TTF" *means*. `kinematic_ttf` is the
            # same estimator the model was trained on, so the fallback is a
            # first-class predictor rather than a substitute — and `v/a` itself
            # still exists, one rung further down, for the case where no 60 s
            # window has been collected yet.
            kin = kinematic_engine.kinematic_ttf(self, node.node_id)
            if kin is not None and kin < ttf_lowest:
                ttf_lowest = float(kin)

        self.system_status = worst
        kinematic_ttf = int(ttf_lowest) if ttf_lowest != float("inf") else "N/A"

        # §5.9 fixes the pill's chain: **cascade → M2 fresh → kinematic → "N/A"**.
        #
        # A note on what "prefers `CascadeDecision.route`" means, because reading
        # it literally gives a type error: `route` is the *tier* — NOMINAL,
        # ADVISORY, ELEVATED, IMMINENT — and `ttf_seconds` is an integer number of
        # seconds (`test_ttf_seconds_is_int_or_na` pins that, and the deck prints
        # it straight into the countdown). The tier belongs in the AI card. So the
        # pill takes the cascade's **TTF** (`CascadeDecision.ttf`, which is M2 when
        # M2 is fresh and the kinematic estimate otherwise) and the tier is
        # published beside it. The chain's *order* is implemented exactly; only the
        # field taken from each rung is the one that fits the widget.
        #
        # Worst-node, not first-node: this is a single system-wide number, and a
        # fleet summary that reported one healthy node while another was about to
        # fail would be the most dangerous kind of correct.
        cascade_ttf = _cascade_pill_ttf()
        if cascade_ttf is not None:
            self.ttf_seconds = cascade_ttf
        elif ttf_model.model_fresh():
            vals = [x for x in ttf_model.MODEL_TTF.values() if x is not None]
            self.ttf_seconds = int(min(vals)) if vals else kinematic_ttf
        else:
            self.ttf_seconds = kinematic_ttf

        self._update_levels()
        self._update_routes()

        # Phase 6 (§6.5) — severity, creep stage and confidence per node. Rate
        # limited to 1 Hz inside the call: the stage is a slow variable and the
        # ticker runs this at 20 Hz.
        #
        # Deliberately last, and deliberately *not* wrapped in a retry: it reads
        # the levels and routes this method has just settled, and a failure in
        # the severity layer must not be able to stop the status pass that the
        # alarm depends on. `severity_tick` swallows per-node errors itself.
        severity_engine.severity_tick(self, now)

    def _update_levels(self) -> None:
        """Per-level status, and the one rule that is *about* the level.

        Phase 4 (§4.5) — a pair strain above ``PAIR_STRAIN_WARN`` raises the
        **level**, not the node. The rationale is the phase's whole point: the
        hazard is the ground *between* the nodes, so there is no individual node
        to blame and blaming one would be a lie the operator can check. The
        escalation is to WARNING only — CRITICAL stays the acceleration channel's
        verdict (Phase 3 made the same call for the crack stage), because severity
        tiers are Phase 6's and a second author of CRITICAL would be a second
        opinion about the same number.
        """
        for lv, members in self.levels_map.items():
            key = str(lv)
            present = [self.nodes[n].status for n in members if n in self.nodes]
            status = worst_status(present) if present else "NOMINAL"
            pair_warn = geomech_engine.level_is_warned(self, lv)
            if pair_warn and status == "NOMINAL":
                status = "WARNING"
            self.levels[key] = {
                "status": status,
                "route": "BLOCKED" if status in ("WARNING", "CRITICAL") else "OPEN",
                "exits": list(LEVEL_EXITS.get(lv, ())),
                # Additive (Phase 4): the deck may show *why* a level is WARNING,
                # which is the difference between "the level is amber" and "the
                # baseline between N1 and N2 is stretching".
                "pair_warn": bool(pair_warn),
            }

    def _update_routes(self) -> None:
        """Phase 7 (B5) — **D7b closed**: the two route booleans become derived.

        Until this phase `route_A_status` / `route_B_status` were "is the worst
        node on this level alarmed", hardwired to levels 1 and 2, and the
        dashboard's own legend called that a computed route. It is now what it
        says: the verdict of a hazard-weighted Dijkstra search from the worst node
        on the level to the nearest *safe* exit.

        The two keys survive with their old values because `index.html` reads them
        (§7.6) — they are **derived** now, never a second source of truth. A level
        whose search returns HOLD reads BLOCKED, which is what the deck means by
        it; a level with no map at all falls back to the old rule rather than
        pretending to route.
        """
        prev_a = self.underground["route_A_status"]
        prev_b = self.underground["route_B_status"]
        prev_exit_a = self.underground.get("exit_A")
        prev_exit_b = self.underground.get("exit_B")

        routes = self.compute_routes()

        self.underground["route_A_status"] = self._route_state(routes, 1)
        self.underground["route_B_status"] = self._route_state(routes, 2)
        self.underground["routes"] = routes
        self.underground["exit_A"] = (routes.get("per_level", {}).get("1") or {}).get("exit")
        self.underground["exit_B"] = (routes.get("per_level", {}).get("2") or {}).get("exit")
        self.underground["holds"] = [j for j in self.underground.get("holds", [])
                                     if j in self._hold_junctions(routes)]

        # The event text is the v4.4 string verbatim, with the georeference of a
        # node **on that level** appended (route A is level 1's route now, so the
        # tag is a level-1 node — in v4.4 route A was tagged with node_1 and
        # route B with node_2, which after the rewrite would have put a level-1
        # coordinate on a level-2 event).
        if prev_a == "OPEN" and self.underground["route_A_status"] == "BLOCKED":
            self.note_event(self.system_status,
                            "ROUTE A BLOCKED — Level 1 drift compromised. "
                            + self._route_reason(routes, 1)
                            + self.geo_tag(self._first_of_level(1)))
        if prev_b == "OPEN" and self.underground["route_B_status"] == "BLOCKED":
            self.note_event(self.system_status,
                            "ROUTE B BLOCKED — Level 2 drift compromised. "
                            + self._route_reason(routes, 2)
                            + self.geo_tag(self._first_of_level(2)))
        # The route *changing* is worth an event even when both states are OPEN:
        # "L1 now exits west instead of east" is exactly the thing an operator
        # needs to hear, and the pre-Phase-7 boolean could not express it.
        for lv, before, after in ((1, prev_exit_a, self.underground["exit_A"]),
                                  (2, prev_exit_b, self.underground["exit_B"])):
            if before and after and before != after:
                self.note_event(self.system_status,
                                f"ROUTE {chr(64 + lv)} RE-ROUTED — Level {lv} exit "
                                f"changed {before} → {after}."
                                + self.geo_tag(self._first_of_level(lv)))
        self.refresh_muster()

    def compute_routes(self) -> dict:
        """The router's answer for the whole site. Empty when there is no map."""
        site = getattr(self, "site_map", None)
        if site is None:
            return {}
        from routing import dijkstra as routing_engine
        return routing_engine.site_routes(site, self)

    def _route_state(self, routes: dict, level: int) -> str:
        per_level = (routes or {}).get("per_level", {})
        entry = per_level.get(str(level))
        if entry is None:
            # No map: the Phase-4 rule still applies, and the deck keeps working.
            return self.levels.get(str(level), {}).get("route", "OPEN")
        return "OPEN" if entry.get("state") in ("OPEN", "ARRIVED") else "BLOCKED"

    def _route_reason(self, routes: dict, level: int) -> str:
        entry = (routes or {}).get("per_level", {}).get(str(level)) or {}
        if entry.get("exit"):
            return f"Exits via {entry['exit']}."
        return (entry.get("reason") or "No safe exit.") + " "

    def _hold_junctions(self, routes: dict) -> set:
        """Junctions currently told to hold.

        Read from the **LED plan**, not from the routes, because the hold is a
        junction-level statement ("the junction with no way out") and the plan is
        where that judgement is already made. Deriving it twice is how the light
        and the log end up disagreeing.
        """
        site = getattr(self, "site_map", None)
        if site is None or not routes:
            return set()
        from routing import dijkstra as routing_engine
        plan = routing_engine.junction_led_plan(
            site, self, list(routes.get("per_level", {}).values()))
        return {cmd.junction for cmd in plan if cmd.pattern == "hold_amber"}

    def _first_of_level(self, level: int) -> str:
        members = self.levels_map.get(level, ())
        return members[0] if members else self.node_ids[0]

    def route_open(self, level) -> bool:
        """Is the route for this level open? Used by the RFID zone logic."""
        key = str(level).upper()
        if key in ("L1", "1"):
            return self.underground["route_A_status"] == "OPEN"
        return self.underground["route_B_status"] == "OPEN"

    # -- RFID & roll call --------------------------------------------------

    def note_rfid(self, uid: str, reader=None) -> tuple[str, str]:
        """Record one scan. Returns ``(uid, zone)`` for the persistence layer.

        One tag keeps one ledger row. A second swipe updates that row instead
        of stacking a duplicate. Level and tunnel are stored beside the zone
        so the muster board can say where the person is working.
        """
        uid = str(uid)
        level, tunnel, zone = self.place_for(reader)
        self.zones[uid] = zone
        self.rfid_log = [row for row in self.rfid_log if row.get("id") != uid]
        self.rfid_log.insert(0, {
            "id": uid,
            "name": MINERS.get(uid, "Personnel Tag"),
            "level": level,
            "tunnel": tunnel,
            "zone": zone,
            "time": time.strftime("%H:%M:%S"),
        })
        if len(self.rfid_log) > 10:
            self.rfid_log.pop()
        self.note_event("NOMINAL",
                        f"RFID {uid} ({MINERS.get(uid, 'Personnel Tag')}) registered at Hub.")
        self.refresh_muster()
        return uid, zone

    def note_rfid_reader(self, reader, present, reads) -> None:
        """A level hub's reader heartbeat. ``reads`` is how many swipes that
        board has accepted since boot, which is the number a muster board can
        check when the ledger itself is still empty."""
        key = str(reader or "").strip().lower() or "hub"
        try:
            count = int(reads)
        except (TypeError, ValueError):
            count = 0
        self.rfid_readers[key] = {
            "reader": key,
            "present": bool(present),
            "reads": count,
            "time": time.strftime("%H:%M:%S"),
        }

    def zone_for(self, reader) -> str:
        """Zone for one scan.

        A v1 hub that names no reader falls back to the old single-route rule, so
        nothing regresses; a hub that names its reader reaches all six zones.
        """
        key = str(reader or "").upper()
        # The level hub sends "lvl1" / "lvl2" (see firmware/src/level_hub/main.cpp).
        # The zone table is L1 / L2. Without this alias every level-2 swipe was
        # filed as Tunnel A and never appeared on the level-2 map.
        key = {"LVL1": "L1", "LVL2": "L2"}.get(key, key)
        if key not in READER_ZONES:
            return "Tunnel A" if self.route_open("L1") else "Safe Exit"
        nominal, evac = READER_ZONES[key]
        if key == "SURF":
            return nominal
        return nominal if self.route_open(key) else evac

    def place_for(self, reader) -> tuple[str, str, str]:
        """``(level, tunnel, zone)`` for one scan.

        Tunnel A is Level 1 and Tunnel B is Level 2. The zone still follows the
        route (working tunnel, or already evacuating if that route is blocked).
        """
        key = str(reader or "").upper()
        key = {"LVL1": "L1", "LVL2": "L2"}.get(key, key)
        zone = self.zone_for(reader)
        if key == "L1" or zone in ("Tunnel A", "Evacuating A"):
            return "Level 1", "Tunnel A", zone
        if key == "L2" or zone in ("Tunnel B", "Evacuating B"):
            return "Level 2", "Tunnel B", zone
        return "Surface", "—", zone

    def level_alarm_on(self, level: str) -> bool:
        """True when that level is WARNING or CRITICAL, or its route is blocked.

        ``level`` is ``L1`` or ``L2``. A blocked route with a still-nominal
        level status counts too: that is the state the board already treats as
        the drift being closed.
        """
        number = "1" if level == "L1" else "2"
        status = str((self.levels.get(number) or {}).get("status") or "NOMINAL").upper()
        if status in ("WARNING", "CRITICAL"):
            return True
        return not self.route_open(level)

    def site_alarm_on(self) -> bool:
        """True when the operator's alarm is on, not only one level's route.

        A Level 1 evacuation hold leaves Level 2's route open. The siren is
        still the site alarm, and the muster list the operator is looking at
        has to empty.
        """
        if str(self.system_status or "").upper() in ("WARNING", "CRITICAL"):
            return True
        for row in (self.levels or {}).values():
            if str((row or {}).get("status") or "").upper() in ("WARNING", "CRITICAL"):
                return True
        return bool((self.underground or {}).get("holds"))

    def refresh_muster(self) -> int:
        """Take tags off the working ledger when an alarm is on.

        A warning or critical on any level, the site, or an evacuation hold
        removes every underground tag, including people scanned on the other
        level. A blocked route with no warning still removes only that level.
        A later
        all-clear does not put them back; they have to swipe again. The
        database copy of each swipe stays as the audit trail.
        """
        pairs = {
            "Tunnel A": "L1",
            "Evacuating A": "L1",
            "Tunnel B": "L2",
            "Evacuating B": "L2",
        }
        tunnels = {"L1": "Tunnel A", "L2": "Tunnel B"}
        cleared = {"L1": 0, "L2": 0}
        site = self.site_alarm_on()
        for uid, zone in list(self.zones.items()):
            level = pairs.get(zone)
            if level is None or not (site or self.level_alarm_on(level)):
                continue
            self.zones.pop(uid, None)
            cleared[level] += 1
        for level in ("L1", "L2"):
            if not (site or self.level_alarm_on(level)):
                continue
            before = len(self.rfid_log)
            level_name = f"Level {level[-1]}"
            self.rfid_log = [
                row for row in self.rfid_log
                if pairs.get(row.get("zone")) != level
                and row.get("level") != level_name
            ]
            dropped = before - len(self.rfid_log)
            if dropped > cleared[level]:
                cleared[level] = dropped
        # #region agent log
        if cleared["L1"] or cleared["L2"]:
            try:
                import json as _json
                with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({
                        "sessionId": "83ae1d",
                        "hypothesisId": "M",
                        "location": "state.py:refresh_muster",
                        "message": "level alarm cleared the working ledger",
                        "data": {"cleared": cleared, "remaining": len(self.rfid_log),
                                 "site": site},
                        "timestamp": int(time.time() * 1000),
                    }) + "\n")
            except Exception:
                pass
        # #endregion
        total = cleared["L1"] + cleared["L2"]
        if site and total:
            self.note_event(
                "CRITICAL",
                f"MUSTER — {total} tag(s) evacuated. Ledger cleared.")
            return total
        for level, count in cleared.items():
            if not count:
                continue
            self.note_event(
                "CRITICAL",
                f"MUSTER — {count} tag(s) on Level {level[-1]} "
                f"({tunnels[level]}) evacuated. Ledger cleared.")
        return total

    def muster_counts(self) -> dict:
        """Roll call: how many accounted for, and where.

        ``total`` is the roster headcount, not ``len(rfid_log)`` — the log evicts
        its oldest rows, so using it made the deck read "3 / 10" and the counts
        sag every time a busy reader pushed one off.
        """
        zones = list(self.zones.values())
        return {
            "total": max(len(MINERS), len(zones)),
            "underground": sum(1 for z in zones if z in UNDERGROUND_ZONES),
            "safe": sum(1 for z in zones if z == "Safe Exit"),
        }

    # -- reset (D13) --------------------------------------------------------

    def reset(self, reason: str = "operator re-arm", grace_s: float = 0.0) -> None:
        """Re-arm. **Clears every node's model window** and invalidates it.

        The runbook used to say "wait ~65 s" after a Re-arm because the model
        buffer survived the reset and the forecast was built from pre-reset
        samples. Now the wait is a true 60 s and it is enforced, not advised.
        """
        self.armed = True
        for node in self.iter_nodes():
            node.status = "NOMINAL"
            node.acceleration = 0.0
            node.velocity = 0.0
            node.displacement_mm = 5.0
            node.tilt_x = 0.0
            node.vib = 0
            node.micro = None
            node.macro = None
            node.flags = 0
            node.stale = False
            node.last_seen_s = 0.0
            # Phase 6 — the creep stage is a statement about a window, and the
            # window has just been thrown away. Keeping TERTIARY through a
            # Re-arm would let the previous drill's conclusion colour the next
            # one, which is exactly the stale-state failure §6.5 is about.
            node.stage = "STABLE"
            node.stage_since = None
            node.stage_hold_s = 0.0
            node.stage_rise_s = 0.0
            node.stage_last_v10 = None
            node.stage_eval_t = None
            node.severity = "NOMINAL"
            node.curvature = 0.0
            node.confidence = "MEDIUM"
            node.confidence_reason = ""
            self.peak_accel[node.node_id] = 0.0
            self.injections[node.node_id] = None
            self.injection_t[node.node_id] = 0.0
            self.via[node.node_id] = None
            self.buffers[node.node_id].clear()
            self._last_t[node.node_id] = None
            self._last_ms[node.node_id] = None
            self._window_origin[node.node_id] = None
            self._window_clock[node.node_id] = None
        # Phase 3 — the crack column goes with it: every zero, every hysteresis
        # latch and every stage. A Re-arm is the operator saying *"treat this as
        # the new baseline"*, and a crack stage left standing would be a verdict
        # about the seam that the operator has just re-based. The engine's
        # counters survive for the same reason the health counters do.
        crack_engine.reset_crack_baseline(self, reason)
        # ... and the stage machine's clock, so the next drill's 1 Hz evaluation
        # starts from a clean stamp rather than inheriting the previous one.
        self.severity_last_t = None
        # ... and the simulator's own seam accumulator, so a bench drill starts
        # from a shut seam rather than from wherever the last drill left it.
        # ``reset_seam`` is a no-op on the mine: there is no simulator in the
        # ingest path, and the real seam is wherever the rock left it.
        from ingestion import simulator as _simulator
        _simulator.reset_seam()
        # Phase 4 — and the pair baselines. A Re-arm is the operator re-basing the
        # picture, and a baseline kept from before it would report the *previous*
        # drill's movement as if it had just happened.
        geomech_engine.reset_geomech_baseline(reason)
        # Phase 2 — the health edge memory is cleared so the first real alarm
        # after a Re-arm is announced rather than swallowed by a `stale_prev`
        # left over from before it. The counters are kept: an outage that
        # happened, happened.
        self.health.clear_edges()
        self.watchdog = None
        self.discontinuities += 1
        hold = float(grace_s or 0.0)
        self.rearm_grace_until = (time.time() + hold) if hold > 0.0 else 0.0
        self.update_status_logic()
        note = f"System manually re-armed by operator ({reason})."
        if hold > 0.0:
            note += f" Sensor triggers ignored for {int(hold)} s."
        self.note_event("NOMINAL", note)

    # -- projections --------------------------------------------------------

    def nodes_public(self) -> dict:
        return {n: self.nodes[n].to_public() for n in self.node_ids}

    def public_payload(self) -> dict:
        """The ``/api/telemetry`` body. Additive only — see §A.3.5.

        ``links`` and ``watchdog`` are Phase 2 additions and are purely additive:
        §2.3's data contract asks for the link list by name, and the ``SENSOR
        LOST`` banner has to arrive on the poll the deck already makes rather
        than on a second request it would have to be taught to send. Nothing
        existing is renamed, reordered or removed.
        """
        payload = {
            "schema": SCHEMA_VERSION,
            "system_status": self.system_status,
            "timestamp": self.timestamp,
            "degraded_mode": self.degraded_mode,
            "cascade_mode": self.cascade_mode,
            "ttf_seconds": getattr(self, "ttf_seconds", "N/A"),
            "rearm_grace_s": round(self.rearm_grace_remaining(), 1),
            "nodes": self.nodes_public(),
            "levels": deepcopy(self.levels),
            "underground": deepcopy(self.underground),
            "rfid_log": deepcopy(self.rfid_log),
            "rfid_readers": list(self.rfid_readers.values()),
            "links": link_state(self),
            "watchdog": deepcopy(self.watchdog),
        }
        return payload

    def nodes_db_rows(self) -> list[tuple]:
        """Rows for ``sensor_telemetry`` — 10 columns, migration 002."""
        return [n.to_db_tuple() for n in self.iter_nodes()]

    def buffer_depths(self) -> dict:
        return {n: len(self.buffers[n]) for n in self.node_ids}


__all__ = ["SystemState", "READER_ZONES", "UNDERGROUND_ZONES", "MINERS",
           "GAP_INVALIDATES_S", "worst_status", "STATUS_RANK"]
