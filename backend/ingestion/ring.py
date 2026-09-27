"""
Neural Strata — the ring: relay frames, heartbeats, link state.
===============================================================

The **ring** is the UART ring between the two level hubs and the main hub — not a
RAM buffer. (The rolling per-node sample buffers are ``SystemState.buffers``; the
two things share a word and nothing else.)

Physical layout, from the pin freeze (§A.2.2):

===========  =========  ===========  ==========================================
Link         UART       Main-hub     Carries
                        GPIO
===========  =========  ===========  ==========================================
Level 1 L1   UART2      16 / 17      N1, N2, J1, J2
Level 2 L2   UART1      4 / 13       N3, N4, J3, J4
===========  =========  ===========  ==========================================

What this module guarantees
---------------------------
1. **Heartbeats every 2.0 s per link**, whether or not rows are flowing. A quiet
   link and a dead link must not look the same.
2. **Relayed frames are indistinguishable from direct ones.** A frame that
   arrived inside a ``{"via": "lvl2"}`` envelope updates ``node_3``/``node_4``
   through the *same* ``handle_node_frame`` path, with the same Δt logic, as a
   frame straight off the USB cable. The only difference is the badge: the node
   carries ``via="lvl2"`` so the deck can print ``via relay``.
3. **A cut does not lose the ring.** One link down degrades that level's *route*,
   never the whole system — the frames keep coming over the surviving link, which
   is the entire reason the ring exists instead of a star.

Timing
------
``HEARTBEAT_S`` 2.0 s is the contract (B1). ``LINK_DEAD_S`` is deliberately
several heartbeats, because a link that flaps every time a packet is late is
worse than one that reports a real outage late.
"""

from __future__ import annotations

import time

from config import LEVELS, LINK_DEAD_S, RING_HEARTBEAT_MISSES, RING_HEARTBEAT_S
from dataclasses import dataclass, field

from ingestion.serial_reader import handle_node_frame, node_id_for_index
from contracts import coerce_int, migrate_payload, validate_ring_frame

#: Heartbeat period per link (B1: "Heartbeats every 2.0 s per link"). **Phase 2
#: moved the value into ``config``** — re-exported here so every Phase-1 import
#: (``from ingestion.ring import HEARTBEAT_S``) still resolves. ``config`` is the
#: single source of every tunable; this module now reads the contract instead of
#: restating it, which is what stops ``ring.py`` and ``health.py`` from
#: disagreeing about when a link is dead.
HEARTBEAT_S = RING_HEARTBEAT_S

#: Missed heartbeats before a link is declared down. Three, not one: a single
#: late packet is not an outage. ``LINK_DEAD_S`` is the product of the two and is
#: never written as a literal anywhere.
HEARTBEAT_MISSES = RING_HEARTBEAT_MISSES

#: The design budget for data continuity after a cut. The mesh realises it in H3;
#: this constant is what the acceptance test measures against.
REROUTE_DEADLINE_S = 1.0

#: A per-node sequence gap wider than this is worth asking a neighbour to relay.
SEQ_GAP_RELAY = 4

#: The two UART links, exactly as §A.2.2 freezes them.
RING_LINKS: dict[int, dict] = {
    1: {"uart": 2, "pins": "16/17", "nodes": LEVELS[1]},
    2: {"uart": 1, "pins": "4/13", "nodes": LEVELS[2]},
}


@dataclass
class LinkState:
    """One UART link's health. Owned by ``SystemState``, updated by heartbeats."""

    level: int
    uart: int
    pins: str
    nodes: tuple[str, ...] = ()
    last_heartbeat_t: float | None = None
    last_heartbeat_seq: int | None = None
    beats: int = 0
    missing: int = 0
    up: bool = False
    relayed: int = 0
    last_event: str = ""

    def to_public(self) -> dict:
        return {
            "level": self.level,
            "uart": self.uart,
            "pins": self.pins,
            "up": bool(self.up),
            "beats": self.beats,
            "missing": self.missing,
            "relayed": self.relayed,
            "last_event": self.last_event,
        }


@dataclass
class SegmentState:
    """One of the triangle's three edges, as the *system* sees it.

    Separate from :class:`LinkState` on purpose. A link is "can I hear that level
    at all" — a level-liveness question. A segment is "is that wire carrying what
    it should" — a topology question. They disagree in exactly the interesting
    case: cutting **W4** (L2's transmit into L1, hop 2) faults the ``L1-L2``
    segment while *both* levels stay up, because L2 still reaches the server over
    hop 3. Collapsing the two would make that the one failure the system could not
    describe, and describing it is the whole point of the triangle.

    Filled from the hub's own edge-triggered frames (§H5.5), so the server's view
    of a cut is the hardware's report and not an inference from silence — an
    inference that cannot tell a cut cable from a hub that lost power.
    """

    name: str
    direction: str = ""
    reroute: str = ""
    faulted: bool = False
    since: float | None = None
    down_ms: int = 0
    faults: int = 0
    restores: int = 0
    last_text: str = ""
    last_event_t: float | None = None

    def to_public(self) -> dict:
        return {
            "name": self.name,
            "faulted": bool(self.faulted),
            "direction": self.direction,
            "reroute": self.reroute,
            "down_ms": int(self.down_ms),
            "faults": self.faults,
            "restores": self.restores,
            "last_text": self.last_text,
            "last_event_s_ago": (round(time.time() - self.last_event_t)
                                 if self.last_event_t else None),
        }


#: The three edges, named the way the firmware names them. Two of them carry a
#: level's uplink; the third is the cross-link that makes the ring a ring.
SEGMENT_NAMES = ("MAIN-L1", "MAIN-L2", "L1-L2")

#: Which level's uplink a faulted segment takes with it. `L1-L2` appears in
#: neither: cutting it degrades the *relay* path, not a level (that is §H5.2
#: point 2, and the reason the demo lands).
SEGMENT_LEVEL = {"MAIN-L1": 1, "MAIN-L2": 2}


@dataclass
class RingLinks:
    """The link registry. One per ``SystemState``.

    Holds no locks of its own — ``SystemState.lock`` covers it, because every
    mutation of link state is already inside a state transaction.
    """

    links: dict[int, LinkState] = field(default_factory=dict)
    seq_expected: dict[str, int] = field(default_factory=dict)
    segments: dict[str, SegmentState] = field(default_factory=dict)

    #: The state this registry belongs to, so a transition can *narrate itself*
    #: instead of relying on every caller to check a return value it might ignore.
    #: Not part of equality or `repr` — it is a back-pointer, not data.
    owner: object | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.links:
            self.links = {
                lv: LinkState(level=lv, uart=cfg["uart"], pins=cfg["pins"],
                              nodes=cfg["nodes"])
                for lv, cfg in RING_LINKS.items()
            }
        if not self.segments:
            self.segments = {name: SegmentState(name=name) for name in SEGMENT_NAMES}

    # -- heartbeats ---------------------------------------------------------

    def note_heartbeat(self, level: int, seq: int | None = None,
                       t: float | None = None) -> LinkState | None:
        link = self.links.get(int(level))
        if link is None:
            return None
        link.last_heartbeat_t = time.time() if t is None else float(t)
        if seq is not None:
            link.last_heartbeat_seq = coerce_int(seq, 0)
        link.beats += 1
        if not link.up:
            link.up = True
            link.missing = 0
            link.last_event = "link up"
        return link

    def tick(self, now: float | None = None) -> list[tuple[int, str]]:
        """Age every link. Returns ``[(level, "up"|"down"), …]`` for transitions.

        Called from the status ticker, so a link that stops beating while the
        reader thread is idle still goes stale — a heartbeat that is only checked
        when data arrives is not a heartbeat.
        """
        now = time.time() if now is None else float(now)
        transitions: list[tuple[int, str]] = []
        for level, link in self.links.items():
            if link.last_heartbeat_t is None:
                continue
            age = now - link.last_heartbeat_t
            link.missing = int(age // HEARTBEAT_S)
            if link.up and age > LINK_DEAD_S:
                link.up = False
                link.last_event = "link down — relay expected"
                transitions.append((level, "down"))
                # Phase 9 — the transition is narrated where it happens, so the
                # caller cannot forget. `push_event` (via the state's event sink)
                # then turns this line into an operator event and, for a level
                # that has gone dark entirely, an infrastructure alert — which is
                # the case no hub can report about itself, because reporting it
                # requires the link that just died.
                state = getattr(self, "owner", None)
                if state is not None:
                    state.note_event("WARNING",
                                     f"Ring link L{level} silent for "
                                     f"{age:.1f}s — relay expected.", kind="INFRASTRUCTURE")
        return transitions

    def link_up(self, level: int) -> bool:
        link = self.links.get(int(level))
        return bool(link and link.up)

    # -- sequence tracking --------------------------------------------------

    def note_seq(self, node_id: str, seq: int | None) -> int:
        """Track a node's sequence. Returns the gap size (0 when continuous).

        A non-increasing ``seq`` is a node restart, which is a **discontinuity**:
        the model window must not span it (H3: "never reset ``seq`` on a soft
        restart except by design").
        """
        if seq is None:
            return 0
        seq = coerce_int(seq, 0)
        expected = self.seq_expected.get(node_id)
        self.seq_expected[node_id] = seq + 1
        if expected is None:
            return 0
        if seq < expected:
            return -1                      # restart / reordering
        return max(0, seq - expected)

    def to_public(self) -> dict:
        return {str(lv): link.to_public() for lv, link in self.links.items()}

    def segments_public(self) -> dict:
        """The three edges, for ``/api/extended``'s ring block.

        A separate key rather than folded into the links: a consumer written
        against the shipped ``{1: …, 2: …}`` shape keeps working untouched, and
        the deck can add the segment row when it wants it (Phase 9's frontend).
        """
        return {name: seg.to_public() for name, seg in self.segments.items()}


# ---------------------------------------------------------------------------
# The relay path
# ---------------------------------------------------------------------------

def _inner_frames(frame: dict) -> list[dict]:
    """Every frame inside an envelope, whatever shape the hub used.

    Accepts the shipped envelope (``{"frames": [...]}``) and the H3 level-hub
    relay form, where a hub forwards a single node frame wrapped in ``via``.
    A bare node frame with a ``via`` key is also accepted — that is what a level
    hub sends when it has exactly one frame to forward.
    """
    if isinstance(frame.get("frames"), list):
        return [f for f in frame["frames"] if isinstance(f, dict)]
    if "node" in frame:
        return [frame]
    return []


def _level_from_via(via) -> int | None:
    """``"lvl2"`` → 2. Anything else is not a hub this project has."""
    v = str(via or "")
    return int(v[-1]) if v.startswith("lvl") and v[-1:] in ("1", "2") else None


def apply_ring_event(state, frame: dict) -> dict | None:
    """Consume one ``{"ring":"fault"|"restored"}`` frame from a hub. §H5.5.

    Returns a small descriptor (or None when the frame names a segment this
    system does not have), so a caller can assert on it; the state's own event
    log is written here, because that is where every other link event is written
    and one incident must not appear twice in the log with two different wordings.

    Why the *hub* is the authority for a segment fault rather than the server
    inferring it from missing heartbeats: silence has two causes — a cut cable and
    a hub that lost power — and they call for opposite responses. A hub that is
    still running can tell the difference and says so; a server that guesses would
    have to pick one and be wrong half the time. The server still ages its links
    (``RingLinks.tick``) so a hub that dies *mid-fault* is caught too, but the
    fault's name, direction and reroute come from the board that saw it.
    """
    kind = str(frame.get("ring") or "").strip().lower()
    if kind not in ("fault", "restored"):
        return None

    seg_name = str(frame.get("seg") or "").strip()
    seg = state.ring.segments.get(seg_name)
    if seg is None:
        state.note_event("WARNING", f"Ring event for unknown segment {seg_name!r} — ignored.",
                         kind="INFRASTRUCTURE")
        return None

    now = time.time()
    via = str(frame.get("via") or "hub")
    text = str(frame.get("text") or "").strip()

    if kind == "fault":
        # Edge-triggered at the far end, and idempotent here: a second frame for a
        # segment already marked faulted updates the counters and **does not**
        # write a second log line. Belt and braces on §H5.5's "exactly one fault
        # event, not a storm" — the firmware is trusted, not assumed.
        first = not seg.faulted
        seg.faulted = True
        seg.since = now if first else (seg.since or now)
        seg.faults += 1
        seg.direction = str(frame.get("dir") or seg.direction or "")
        seg.reroute = str(frame.get("reroute") or seg.reroute or "")
        seg.last_text = text or (f"{seg_name} degraded — {seg.direction} "
                                 f"({seg.reroute or 'no reroute'})")
        seg.last_event_t = now
        level = SEGMENT_LEVEL.get(seg_name)
        if level is not None:
            link = state.ring.links.get(level)
            if link is not None:
                link.up = False
                link.last_event = f"segment {seg_name} faulted ({seg.direction})"
        if first:
            state.note_event("WARNING", f"Ring segment {seg.last_text}. "
                                        f"Reported by {via}.", kind="INFRASTRUCTURE")
        return {"kind": "fault", "segment": seg_name, "first": first,
                "level": SEGMENT_LEVEL.get(seg_name), "via": via,
                "text": seg.last_text, "to_public": seg.to_public()}

    # restored
    was = bool(seg.faulted)
    down_ms = int(frame.get("down_ms") or
                  (int((now - seg.since) * 1000) if seg.since else 0))
    seg.faulted = False
    seg.down_ms = down_ms
    if was:
        seg.restores += 1
    seg.since = None
    seg.last_text = text or f"segment restored {seg_name}"
    seg.last_event_t = now
    level = SEGMENT_LEVEL.get(seg_name)
    if level is not None:
        link = state.ring.links.get(level)
        if link is not None:
            link.up = True
            link.last_event = f"segment {seg_name} restored"
    if was:
        state.note_event("NOMINAL", f"Ring segment restored — {seg_name} after "
                                    f"{down_ms / 1000.0:.1f}s.", kind="INFRASTRUCTURE")
    return {"kind": "restored", "segment": seg_name, "was_faulted": was,
            "down_ms": down_ms, "via": via, "text": seg.last_text,
            "to_public": seg.to_public()}


def handle_ring_frame(state, frame: dict) -> int:
    """Apply a relay envelope. Returns how many node frames landed.

    The envelope is *transport*, not data: ``via`` and ``seq`` are stripped before
    the inner frames reach ``handle_node_frame``, so an inner frame is byte-equal
    to one off the USB cable. That is what makes ``test_ring_relay`` meaningful —
    if a relayed frame produced a different state change, the two paths would
    have diverged and the mesh would be lying.
    """
    # ── Phase 9 (B6): the two frames that are *not* envelopes ────────────────
    # Both are checked before the envelope validator, and deliberately: they are
    # different messages on the same wire, and running them through a validator
    # that requires `frames` would reject the ring's own fault report — which is
    # precisely how a consumer ends up never seeing one.
    if str(frame.get("ring") or "") in ("fault", "restored"):
        apply_ring_event(state, frame)
        return 0

    via_probe = str(frame.get("via") or "")
    # `hb` is accepted as JSON `true` **and** as `1`: the firmware shipped the
    # numeric spelling first, and a consumer that only understood the boolean
    # would silently stop refreshing link liveness on a board that had not been
    # reflashed — a failure that looks exactly like a dead cable.
    if frame.get("hb") in (True, 1) or (isinstance(frame.get("frames"), list)
                                        and not frame["frames"]):
        lv = _level_from_via(via_probe)
        if lv is not None:
            # A heartbeat's *whole job* is to refresh liveness, so this is where
            # the link stops being a guess. Without it, `LinkState.up` is only
            # ever set by the aging tick — a link could never come *back*.
            state.ring.note_heartbeat(lv, frame.get("seq"))
        return 0

    ok, _why = validate_ring_frame(frame)
    if not ok:
        return 0

    v = coerce_int(frame.get("v"), 1)
    if v < 2:
        frame = migrate_payload(frame, from_v=v)

    via = str(frame.get("via") or "hub")
    nodes_hit = 0

    for inner in _inner_frames(frame):
        # Copy: the envelope's transport keys must not leak into the frame.
        child = {k: val for k, val in inner.items()
                 if k not in ("via", "seq", "relay", "frames")}
        n_id = node_id_for_index(child.get("node"))
        if n_id is None or n_id not in state.nodes:
            continue

        seq = inner.get("seq", frame.get("seq"))
        gap = state.ring.note_seq(n_id, seq)
        if gap == -1:
            state.mark_discontinuity(n_id, "seq reset")
        elif gap > SEQ_GAP_RELAY:
            state.note_event("WARNING", f"{n_id.upper()} sequence gap {gap} — relay requested.")

        if handle_node_frame(state, child, via=via):
            nodes_hit += 1

    for link in state.ring.links.values():
        if via and link.nodes and all(n in via for n in ("lvl",)):
            if via.endswith(str(link.level)):
                link.relayed += nodes_hit
    return nodes_hit


__all__ = [
    "RING_LINKS", "HEARTBEAT_S", "LINK_DEAD_S", "REROUTE_DEADLINE_S",
    "SEQ_GAP_RELAY", "SEGMENT_NAMES", "SEGMENT_LEVEL", "LinkState", "SegmentState",
    "RingLinks", "handle_ring_frame", "apply_ring_event",
]
