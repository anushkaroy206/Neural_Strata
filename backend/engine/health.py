"""
Neural Strata — B7 · node, link and hub health. The watchdog.
============================================================

> *"The most dangerous state for a safety system is not 'alert' — it's 'I don't
> know, and I look fine'."*

Phase 1 built the plumbing this module was shaped for: ``last_seen_s`` and
``stale`` are already real fields the deck reads, and ``health_tick`` already ran
from the ticker. What Phase 1 deliberately left out is everything that makes
silence *decidable* — the four-rung degraded ladder, edge-triggered events, the
serial watchdog, and ``tests/test_health.py``.

The three rules this module exists to enforce
---------------------------------------------
**1 · A quiet node and a dead node must not look alike.** §2.1's defect is
specific: *"If node 2's radio dies, ``node_2`` simply freezes at its last value
and the dashboard shows a perfectly plausible NOMINAL."* Everything below is in
service of that one sentence.

**2 · Edge detection, not level detection.** ``health_tick`` runs at 20 Hz. An
event per tick is a 20 Hz event log and an unreadable alert history, so every
transition is announced **once**, on the edge, and held in ``HealthLedger``
(§2.6). This is also why ``health_tick`` — not the serial reader — is the single
announcer of ``SERIAL_LOST`` / ``SERIAL_RECONNECTED``: one edge detector, in one
place, beats two that disagree.

**3 · Never-heard is not the same as fresh.** ``state.age_of()`` returns ``0.0``
for a node that has never spoken, which makes an unplugged-at-boot radio look
infinitely healthy. Phase 2 therefore asks a second question — *has this server
ever heard from it, and have we been up long enough to say so?* — via
``_boot_grace_elapsed``. See ``is_stale`` for why that question is only asked
when there is hardware to ask it about.

Degraded mode is a first-class state, not an absence of alarms
--------------------------------------------------------------
``FULL`` means every node is reporting and every known link is up. Anything else
names *which* thing is missing, so the operator reads a diagnosis rather than a
mood:

===============  =====================================================
``FULL``         serial up, all nodes fresh, all known links up
``SENSOR_PARTIAL``  1–n nodes stale (routing treats them as unknown, not safe)
``RING_SPLIT``   one relay leg dark while another is live
``NO_HARDWARE``  no frames arriving from anywhere real — simulation only
===============  =====================================================

``RING_SPLIT`` outranks ``SENSOR_PARTIAL`` on purpose: a dark leg *is* what made
those nodes go stale, and naming the cause is more useful than counting the
symptom.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from config import LINK_DEAD_S, STALE_AFTER_S

# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

MODE_FULL = "FULL"
MODE_SENSOR_PARTIAL = "SENSOR_PARTIAL"
MODE_RING_SPLIT = "RING_SPLIT"
MODE_NO_HARDWARE = "NO_HARDWARE"

#: Every mode the payload may carry. ``/api/telemetry``'s ``degraded_mode`` is
#: one of these strings and nothing else.
MODES = (MODE_FULL, MODE_SENSOR_PARTIAL, MODE_RING_SPLIT, MODE_NO_HARDWARE)

#: Phase-1 spellings. Phase 1 published a three-rung ladder
#: (``FULL`` / ``DEGRADED`` / ``OFFLINE``); a recording or a frozen fixture taken
#: before Phase 2 still says ``DEGRADED``, and ``contracts.py`` still defaults the
#: field to ``FULL``. The map is read-only translation — it exists so an old
#: payload can be *read*, never so a new one can be written in the old alphabet.
MODE_ALIASES = {"DEGRADED": MODE_SENSOR_PARTIAL, "OFFLINE": MODE_NO_HARDWARE}

#: ``serial_up`` provenance. ``"none"`` is what makes NO_HARDWARE mean something
#: precise: no frames are arriving from anywhere real. A **replay is real** —
#: those are recorded samples off actual hardware, re-driven through the live
#: pipeline — so it counts as hardware even though no USB cable is attached.
SOURCE_USB = "usb"
SOURCE_REPLAY = "replay"
SOURCE_SIM = "sim"

#: The watchdog banner's tag. §2.2: infrastructure alarms are "visible, but never
#: mixed with a geological severity tier", so they travel as ``kind`` beside the
#: level, not as a fourth level.
KIND_INFRASTRUCTURE = "INFRASTRUCTURE"


# ---------------------------------------------------------------------------
# Edge-detector memory
# ---------------------------------------------------------------------------

@dataclass
class HealthLedger:
    """What ``health_tick`` must remember between ticks to fire each event once.

    Owned by ``SystemState`` (``state.health``), so a ``state.reset()`` can clear
    it and a test can construct one without a running server. Every field is
    *previous tick's answer* — nothing here is derived state, which is what keeps
    this a memo and not a second source of truth.
    """

    #: n_id → was stale on the previous tick.
    stale_prev: dict[str, bool] = field(default_factory=dict)
    #: level → was the link up on the previous tick.
    link_prev: dict[int, bool] = field(default_factory=dict)
    #: ``serial_up`` on the previous tick. ``None`` means "no opinion yet", which
    #: is deliberately distinct from ``False``: the first tick of a fresh process
    #: must not announce SERIAL_LOST for a port that was never opened.
    serial_prev: bool | None = None
    #: Was the watchdog banner raised on the previous tick.
    alarm_prev: bool = False
    #: When the current outage began, per node / link — the banner's "since".
    stale_since: dict[str, float] = field(default_factory=dict)
    link_since: dict[int, float] = field(default_factory=dict)
    #: Counters. Read by ``/api/metrics`` (Phase 10) and by the tests.
    counts: dict[str, int] = field(default_factory=lambda: {
        "node_stale": 0, "node_recovered": 0,
        "link_down": 0, "link_up": 0,
        "serial_lost": 0, "serial_reconnected": 0,
        "alarm_raised": 0, "alarm_cleared": 0,
    })

    def clear_edges(self) -> None:
        """Forget every previous-tick answer, keep the counters.

        Called by ``SystemState.reset()``. A Re-arm is the operator saying *"treat
        this as the new baseline"*, so the next tick must be free to announce a
        stale node or a dark link from scratch. Leaving ``stale_prev`` set would
        swallow the first real alarm after a Re-arm — the worst possible moment
        to drop one, since the operator has just told the system everything is
        fine. Counters survive: an outage that happened, happened.
        """
        self.stale_prev.clear()
        self.link_prev.clear()
        self.stale_since.clear()
        self.link_since.clear()
        self.serial_prev = None
        self.alarm_prev = False

    def prune(self, node_ids) -> None:
        """Drop memory for nodes that are no longer in the registry.

        ``NS_ACTIVE_NODES`` can shrink a live process at boot, and ``reset()``
        reuses the object. Without this the ledger would keep announcing a
        recovery for a node that no longer exists.
        """
        live = set(node_ids)
        for book in (self.stale_prev, self.stale_since):
            for n_id in [k for k in book if k not in live]:
                del book[n_id]


def _ledger(state) -> HealthLedger:
    """The state's ledger, created on first use.

    Lazy rather than declared in ``SystemState.__init__`` so that pickled or
    hand-built states from Phase 1 fixtures — including the JSON snapshots in
    ``tests/fixtures/`` — still tick without an ``AttributeError``.
    """
    ledger = getattr(state, "health", None)
    if not isinstance(ledger, HealthLedger):
        ledger = HealthLedger()
        state.health = ledger
    return ledger


def _boot_grace_elapsed(state, now: float) -> bool:
    """Has the process been up long enough for silence to mean something?

    A server that has been running for 0.3 s has not "lost" four nodes — it has
    not met them yet. Without this the dashboard flashes four red cards on every
    cold start, which trains the operator to ignore the colour.
    """
    started = getattr(state, "started_at", None)
    if started is None:
        return True
    return (now - float(started)) >= STALE_AFTER_S


def _cross_link_seen(state):
    """Wall-clock arrival of the last J2↔J3 cross-link heartbeat, or ``None``.

    The mesh realises this link in **H3**; until a hub reports one, the link is
    UNKNOWN — not DOWN. Same rule Phase 1 set for the UART links: a link we have
    never heard from is not an outage, and calling it one puts a warning on the
    dashboard every time the server starts.
    """
    return getattr(state, "cross_link_seen", None)


# ---------------------------------------------------------------------------
# The questions the rest of the system asks
# ---------------------------------------------------------------------------

def has_hardware(state) -> bool:
    """Are frames arriving from anywhere real?

    ``"none"`` is the hardware-free bench (``--no-serial`` with no replay): the
    scenario console is the only source of motion and the server must say so
    rather than present injected values as measurements.
    """
    return getattr(state, "hardware_source", SOURCE_SIM) != SOURCE_SIM


def node_last_seen(state, now: float | None = None) -> dict[str, float]:
    """``{node_id: seconds_since_last_sample}`` for every node in the registry.

    A node that has never spoken reports its **age since boot**, not ``0.0``.
    ``age_of`` cannot express the difference — it answers "how long since the
    last sample" and has no last sample to subtract from — so the distinction is
    made here, where the display and the staleness rule both read it.
    """
    now = time.time() if now is None else float(now)
    seen = getattr(state, "last_arrival", {}) or {}
    started = float(getattr(state, "started_at", now))
    ages: dict[str, float] = {}
    for n_id in state.node_ids:
        if n_id in seen:
            ages[n_id] = max(0.0, now - float(seen[n_id]))
        else:
            ages[n_id] = max(0.0, now - started)
    return ages


def is_stale(state, n_id: str, now: float | None = None) -> bool:
    """Is this node's silence an outage?

    Three conditions, and all three have to hold:

    1. **There is hardware.** On a simulation-only bench there is no sensor link
       to be stale *from*. The banner already says the numbers are injected, so
       greying every card as well would double-report one fact and strike
       through the only numbers the bench has.
    2. **Boot grace has elapsed.** See ``_boot_grace_elapsed``.
    3. **The node has been silent past ``STALE_AFTER_S``** — which, for a node
       that has *never* spoken, is a statement about the age of the process.

    Condition 3 is the one that closes §2.1's blind spot: an unplugged-at-boot
    radio is now STALE rather than a plausible NOMINAL.
    """
    if not has_hardware(state):
        return False
    now = time.time() if now is None else float(now)
    if not _boot_grace_elapsed(state, now):
        return False
    return node_last_seen(state, now).get(n_id, 0.0) > STALE_AFTER_S


def link_state(state, now: float | None = None) -> list[dict]:
    """Every link, spelled the way §2.3's data contract spells it.

    Two UART relay legs and the ``J2↔J3`` cross-link. ``state`` is one of
    ``UP`` / ``DOWN`` / ``UNKNOWN``, and ``last_seen_s`` is ``None`` — not ``0``
    — when the link has never beaten, because a zero there reads as "perfect
    signal" in every consumer that forgets to check ``state`` first.
    """
    now = time.time() if now is None else float(now)
    rows: list[dict] = []

    for level in sorted(getattr(state.ring, "links", {})):
        link = state.ring.links[level]
        last = link.last_heartbeat_t
        if last is None:
            age, lid, st = None, f"lvl{level}→hub", "UNKNOWN"
        else:
            age = max(0.0, now - float(last))
            # A known link that has gone quiet is DOWN; health_tick's ring tick
            # already flipped ``up``. Deriving the display from the same field
            # the mode ladder reads is what keeps the two from disagreeing.
            lid, st = f"lvl{level}→hub", ("UP" if link.up else "DOWN")
        rows.append({
            "id": lid,
            "level": int(level),
            "uart": int(link.uart),
            "pins": link.pins,
            "state": st,
            "last_seen_s": None if age is None else round(age, 1),
            "beats": int(link.beats),
            "missing": int(link.missing),
            "relayed": int(link.relayed),
        })

    seen = _cross_link_seen(state)
    if seen is None:
        rows.append({"id": "J2↔J3", "level": None, "state": "UNKNOWN",
                     "last_seen_s": None, "note": "mesh cross-link — H3"})
    else:
        age = max(0.0, now - float(seen))
        rows.append({
            "id": "J2↔J3", "level": None,
            "state": "UP" if age <= LINK_DEAD_S else "DOWN",
            "last_seen_s": round(age, 1),
        })
    return rows


def ring_cut_detect(state, now: float | None = None) -> bool:
    """§2.2 ``RING_SPLIT`` — one relay leg dark while another is live.

    "Known" is doing real work in that sentence. A link that has never beaten is
    **not** dark, it is unknown, so a server that has not yet seen either hub is
    not split — it is simply early. Only a link that has beaten at least once and
    then stopped counts as dark.
    """
    links = list(getattr(state.ring, "links", {}).values())
    known = [link for link in links if link.beats > 0]
    if not known:
        return False
    dark = [link for link in known if not link.up]
    live = [link for link in known if link.up]
    return bool(dark) and bool(live)


def degraded_mode(state, now: float | None = None) -> str:
    """The ladder, in precedence order.

    ``RING_SPLIT`` before ``SENSOR_PARTIAL`` (a cut leg is the *cause* of the
    stale nodes behind it), ``NO_HARDWARE`` before everything (with no hardware
    the other rungs describe a link that does not exist).
    """
    now = time.time() if now is None else float(now)
    if not has_hardware(state):
        return MODE_NO_HARDWARE
    if ring_cut_detect(state, now):
        return MODE_RING_SPLIT
    for n_id in state.node_ids:
        if is_stale(state, n_id, now):
            return MODE_SENSOR_PARTIAL
    return MODE_FULL


def watchdog_alarm(state, now: float | None = None) -> dict | None:
    """The persistent banner, or ``None`` when there is nothing to say.

    §2.2: *"While armed, any dark node/link raises a persistent banner."*
    "Persistent" is the load-bearing word — this is not an event, it is a
    condition, and it stays raised until the condition clears. Phase 8 turns the
    raise/clear edges into ``alerts_history`` rows of ``kind =
    INFRASTRUCTURE``; Phase 2 owns the edges and the operator log.
    """
    if not getattr(state, "armed", True):
        return None

    now = time.time() if now is None else float(now)
    ledger = _ledger(state)

    stale_nodes = [n for n in state.node_ids if is_stale(state, n, now)]
    dark_links = [link for link in getattr(state.ring, "links", {}).values()
                  if link.beats > 0 and not link.up]
    serial_down = has_hardware(state) and not getattr(state, "serial_up", True)

    if not (stale_nodes or dark_links or serial_down):
        return None

    source = getattr(state, "hardware_source", SOURCE_SIM)

    parts: list[str] = []
    if serial_down:
        # Name the feed that actually stopped. A finished replay and an
        # unplugged cable both mean "no telemetry", but only one of them is
        # fixed by reseating a connector, and an operator reading "USB link
        # down" on a machine with no cable in it learns to distrust the banner.
        parts.append("replay exhausted — no telemetry" if source == SOURCE_REPLAY
                     else "USB link down — no telemetry")
    if dark_links:
        cut = ", ".join(f"lvl{link.level}" for link in sorted(dark_links, key=lambda l: l.level))
        parts.append(f"ring leg dark ({cut})")
    if stale_nodes:
        parts.append("silent: " + ", ".join(n.upper() for n in stale_nodes))

    # "Since" is when the silence **began**, not when a tick got round to
    # noticing it. Those are the same number only if the ticker happens to run at
    # the instant the threshold is crossed; in practice the tick that first sees
    # a node go stale is up to one tick late, and on a freshly booted server it
    # can be minutes late. Reporting `now` there would print "silent for 0.0 s"
    # beside a node that has been gone for ten minutes — the exact class of
    # reassuring lie this phase exists to delete. So: the node's last known
    # arrival, or the process start for a node that has never spoken at all.
    silent_starts: list[float] = []
    for n_id in stale_nodes:
        seen = (getattr(state, "last_arrival", {}) or {}).get(n_id)
        silent_starts.append(float(seen) if seen is not None
                             else float(getattr(state, "started_at", now)))
    for link in dark_links:
        t = ledger.link_since.get(link.level)
        if t is not None:
            silent_starts.append(float(t))

    since = min(silent_starts) if silent_starts else None

    return {
        "kind": KIND_INFRASTRUCTURE,
        "severity": KIND_INFRASTRUCTURE,
        "text": "SENSOR LOST — " + " · ".join(parts),
        "nodes": stale_nodes,
        "links": [f"lvl{link.level}" for link in sorted(dark_links, key=lambda l: l.level)],
        "serial_down": bool(serial_down),
        "since": round(since, 1) if since is not None else round(now, 1),
        "elapsed_s": round(now - since, 1) if since is not None else 0.0,
        # §2.2: with no hardware, hardware-derived claims are suppressed rather
        # than asserted. The consumer reads this flag; it never has to guess.
        "claims_suppressed": not has_hardware(state),
    }


# ---------------------------------------------------------------------------
# The tick
# ---------------------------------------------------------------------------

def health_tick(state, now: float | None = None) -> dict:
    """Age every node and link, announce every transition once, pick the mode.

    Called from the ticker every 50 ms. Returns a small summary so
    ``/api/metrics`` (Phase 10) and the tests can read it without reaching into
    state.
    """
    now = time.time() if now is None else float(now)
    ledger = _ledger(state)
    ledger.prune(state.node_ids)

    # -- nodes ------------------------------------------------------------
    stale_now: list[str] = []
    ages = node_last_seen(state, now)
    for node in state.iter_nodes():
        n_id = node.node_id
        age = ages.get(n_id, 0.0)
        node.last_seen_s = round(age, 1)

        stale = is_stale(state, n_id, now)
        node.stale = stale
        if stale:
            stale_now.append(n_id)
            ledger.stale_since.setdefault(n_id, now)

        prev = ledger.stale_prev.get(n_id, False)
        if stale and not prev:
            ledger.counts["node_stale"] += 1
            ledger.stale_since[n_id] = now
            state.note_event(
                "WARNING",
                f"SENSOR LOST — {n_id.upper()} silent for {age:.1f} s"
                f"{state.geo_tag(n_id)}. Value held, not live.",
                kind=KIND_INFRASTRUCTURE)
        elif prev and not stale:
            ledger.counts["node_recovered"] += 1
            ledger.stale_since.pop(n_id, None)
            state.note_event(
                "NOMINAL",
                f"SENSOR RECOVERED — {n_id.upper()} reporting again"
                f"{state.geo_tag(n_id)}.",
                kind=KIND_INFRASTRUCTURE)
        ledger.stale_prev[n_id] = stale

    # -- ring links -------------------------------------------------------
    for level, kind in state.ring.tick(now):
        # ring.tick already decided the transition; health only announces it and
        # keeps the ledger's own edge flag in step so the two cannot disagree.
        ledger.counts["link_down" if kind == "down" else "link_up"] += 1
        ledger.link_since[level] = now
        state.note_event(
            "WARNING" if kind == "down" else "NOMINAL",
            f"RING {kind.upper()} — UART link on level {level} "
            f"{'stopped beating' if kind == 'down' else 'is beating again'}"
            f" (lvl{level}→hub).",
            kind=KIND_INFRASTRUCTURE)

    for level, link in getattr(state.ring, "links", {}).items():
        prev = ledger.link_prev.get(level)
        if prev is not None and prev != link.up:
            ledger.counts["link_down" if prev else "link_up"] += 1
            ledger.link_since[level] = now
        ledger.link_prev[level] = bool(link.up)

    # -- the serial link --------------------------------------------------
    serial_up = bool(getattr(state, "serial_up", False))
    if ledger.serial_prev is not None and ledger.serial_prev != serial_up:
        if serial_up:
            ledger.counts["serial_reconnected"] += 1
            state.note_event("NOMINAL",
                             "SERIAL RECONNECTED — telemetry resumed on the USB link.",
                             kind=KIND_INFRASTRUCTURE)
        else:
            ledger.counts["serial_lost"] += 1
            state.note_event("WARNING",
                             "SERIAL LOST — USB link dropped; the watchdog is "
                             "retrying with backoff.",
                             kind=KIND_INFRASTRUCTURE)
    ledger.serial_prev = serial_up

    # -- mode -------------------------------------------------------------
    state.degraded_mode = degraded_mode(state, now)

    # -- the banner -------------------------------------------------------
    alarm = watchdog_alarm(state, now)
    raised = alarm is not None
    if raised and not ledger.alarm_prev:
        ledger.counts["alarm_raised"] += 1
    elif ledger.alarm_prev and not raised:
        ledger.counts["alarm_cleared"] += 1
        state.note_event("NOMINAL", "All sensors and links reporting — alarm cleared.",
                         kind=KIND_INFRASTRUCTURE)
    ledger.alarm_prev = raised
    state.watchdog = alarm

    known = [link for link in getattr(state.ring, "links", {}).values() if link.beats > 0]
    return {
        "degraded_mode": state.degraded_mode,
        "stale_nodes": stale_now,
        "nodes": len(state.node_ids),
        "hub": hub_state(state),
        "links_up": all(link.up for link in known),
        "links_unknown": not known,
        "alarm": raised,
        "counts": dict(ledger.counts),
    }


# ---------------------------------------------------------------------------
# Hub health (§2.4 "main-hub health")
# ---------------------------------------------------------------------------

def hub_state(state) -> str:
    """``UP`` / ``PARTIAL`` / ``DOWN`` / ``UNKNOWN`` for the main hub itself.

    Phase 1 could see four nodes and two links but had no opinion about the board
    they all hang off. A hub that is alive but not forwarding is a different
    fault from a hub that is dead, and only one of them is fixed by reseating the
    USB cable.
    """
    links = list(getattr(state.ring, "links", {}).values())
    known = [link for link in links if link.beats > 0]
    if not getattr(state, "serial_up", False):
        return "DOWN" if known else "UNKNOWN"
    if not known:
        return "UNKNOWN"
    live = [link for link in known if link.up]
    if len(live) == len(known):
        return "UP"
    if live:
        return "PARTIAL"
    return "DOWN"


def health_payload(state, now: float | None = None, model: dict | None = None,
                   db: dict | None = None) -> dict:
    """The ``GET /api/health`` body — node, link, hub, model and DB in one object.

    §2.3 asks for all five so the operator strip and Phase 10's metrics read one
    endpoint instead of five. ``model`` and ``db`` are **injected** by the
    caller: this module owns health *reasoning*, and neither the TTF model nor
    the connection pool belongs in its import graph. A caller that passes
    nothing gets an honest ``{"status": "UNKNOWN"}`` rather than a hollow claim.
    """
    now = time.time() if now is None else float(now)
    ledger = _ledger(state)

    return {
        "schema": 1,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "degraded_mode": state.degraded_mode,
        "hub": {"status": hub_state(state)},
        "serial": {
            "up": bool(getattr(state, "serial_up", False)),
            "source": getattr(state, "hardware_source", SOURCE_SIM),
        },
        "nodes": {
            n_id: {
                "last_seen_s": round(node_last_seen(state, now).get(n_id, 0.0), 1),
                "stale": is_stale(state, n_id, now),
            }
            for n_id in state.node_ids
        },
        "links": link_state(state, now),
        "alarm": watchdog_alarm(state, now),
        "events": dict(ledger.counts),
        "thresholds": {"stale_after_s": STALE_AFTER_S, "link_dead_s": LINK_DEAD_S},
        "model": model if model is not None else {"status": "UNKNOWN"},
        "db": db if db is not None else {"status": "UNKNOWN"},
    }


__all__ = [
    "health_tick", "health_payload", "hub_state",
    "node_last_seen", "is_stale", "link_state", "ring_cut_detect",
    "degraded_mode", "has_hardware", "watchdog_alarm", "HealthLedger",
    "MODE_FULL", "MODE_SENSOR_PARTIAL", "MODE_RING_SPLIT", "MODE_NO_HARDWARE",
    "MODE_ALIASES", "MODES", "KIND_INFRASTRUCTURE",
    "SOURCE_USB", "SOURCE_REPLAY", "SOURCE_SIM",
]
