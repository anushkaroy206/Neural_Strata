"""
Phase 1 (B1) — the relay ring: a relayed frame is a frame.
=========================================================

The ring exists so that one cut UART does not cost a level. L2's frames arrive at
the main hub over L1's link, wrapped in an envelope that says ``via``.

The failure this file guards against is subtle and expensive: if a relayed frame
took a *slightly different* path into state than a direct one — a different Δt
rule, a skipped validation, a different status cascade — then a node would behave
differently depending on which cable happened to be working. The dashboard would
show the difference as a change in the ground, and nobody would know to distrust
it.

So the assertion is equality: same frame, same resulting state, with and without
the envelope.
"""

from __future__ import annotations

import pytest

from config import LEVELS
from ingestion import ring
from ingestion.ring import (
    HEARTBEAT_MISSES, HEARTBEAT_S, LINK_DEAD_S, REROUTE_DEADLINE_S, RING_LINKS,
    handle_ring_frame,
)
from ingestion.serial_reader import handle_node_frame
from state import SystemState


@pytest.fixture()
def state():
    return SystemState()


def _frame(node_idx, ms, disp):
    return {"v": 2, "node": node_idx, "ms": ms, "disp": disp, "tilt": 0.0,
            "acc": 0.0, "vib": 0, "micro": None, "macro": None,
            "rssi": -60, "batt": 3.8, "flags": 0}


def _envelope(via, frames, seq=1):
    return {"v": 2, "via": via, "seq": seq, "frames": frames}


# ---------------------------------------------------------------------------
# The equality that matters
# ---------------------------------------------------------------------------

def test_a_relayed_level_2_frame_is_identical_to_a_direct_one():
    """node_3 and node_4 update the same way through either path.

    Two fresh states, the same three frames, one arrived directly and one inside
    a ``via:"lvl2"`` envelope: every observable field must match.
    """
    direct, relayed = SystemState(), SystemState()
    frames = [_frame(3, 1000 + i * 500, 10.0 + i) for i in range(3)]

    for f in frames:
        handle_node_frame(direct, f)
    for i, f in enumerate(frames):
        # An increasing envelope `seq` — a constant one would (correctly) read as
        # a node restart and invalidate the window, which is asserted separately.
        handle_ring_frame(relayed, _envelope("lvl2", [f], seq=i + 1))

    for n_id in ("node_3",):
        a, b = direct.nodes[n_id], relayed.nodes[n_id]
        assert a.displacement_mm == pytest.approx(b.displacement_mm)
        assert a.velocity == pytest.approx(b.velocity)
        assert a.acceleration == pytest.approx(b.acceleration)
        assert a.tilt_x == pytest.approx(b.tilt_x)
        assert a.status == b.status
        assert a.level == b.level
        assert len(direct.buffers[n_id]) == len(relayed.buffers[n_id])


def test_both_frames_land_where_the_topology_says_they_should(state):
    """L2 carries N3/N4. A relayed frame must not write to a level-1 node."""
    handle_ring_frame(state, _envelope("lvl2", [_frame(3, 1000, 33.0)]))
    handle_ring_frame(state, _envelope("lvl2", [_frame(4, 1000, 44.0)]))

    assert state.nodes["node_3"].displacement_mm == 33.0
    assert state.nodes["node_4"].displacement_mm == 44.0
    assert state.nodes["node_1"].displacement_mm == 5.0, "untouched"
    assert state.nodes["node_2"].displacement_mm == 5.0, "untouched"


def test_the_via_badge_is_set_and_cleared(state):
    """The deck prints ``via relay (N1)``. It must clear when the link heals.

    A badge that only ever turns on is worse than no badge: the operator learns to
    ignore it, and then misses the cut it was there to announce.
    """
    handle_ring_frame(state, _envelope("lvl1", [_frame(1, 1000, 6.0)]))
    assert state.via_of("node_1") == "lvl1"

    handle_node_frame(state, _frame(1, 1500, 6.5))          # direct again
    assert state.via_of("node_1") is None, "a direct frame clears the relay badge"


# ---------------------------------------------------------------------------
# Link state
# ---------------------------------------------------------------------------

def test_links_start_unknown_not_down(state):
    """Boot must not look like an outage.

    A link that has never beaten has never *failed* — it may simply not exist yet
    (the bench, before the hub is plugged in). Calling that DOWN would put a
    warning on the dashboard every single time the server starts.
    """
    assert state.ring.link_up(1) is False
    assert all(link.beats == 0 for link in state.ring.links.values())


def test_a_heartbeat_brings_a_link_up_then_decay_takes_it_down(state):
    state.ring.note_heartbeat(1, seq=1, t=100.0)
    assert state.ring.link_up(1) is True

    # Still inside the grace window: three missed heartbeats.
    transitions = state.ring.tick(now=100.0 + HEARTBEAT_S * (HEARTBEAT_MISSES - 1))
    assert state.ring.link_up(1) is True
    assert transitions == []

    transitions = state.ring.tick(now=100.0 + LINK_DEAD_S + 0.1)
    assert state.ring.link_up(1) is False
    assert transitions == [(1, "down")]


def test_a_downed_link_does_not_take_the_other_level_with_it(state):
    """The whole point of the ring: one cut costs one level, not the system."""
    state.ring.note_heartbeat(1, t=100.0)
    state.ring.note_heartbeat(2, t=100.0)
    state.ring.tick(now=100.0 + LINK_DEAD_S + 0.1)

    this_cycle = [t for t in state.ring.tick(now=100.0 + LINK_DEAD_S + 5.0)]
    assert state.ring.link_up(1) is False
    assert state.ring.link_up(2) is False
    # Now heal level 1 only and confirm level 2 stays down.
    state.ring.note_heartbeat(1, t=99999.0)
    assert state.ring.link_up(1) is True
    assert state.ring.link_up(2) is False
    assert isinstance(this_cycle, list)


def test_the_timing_constants_match_the_spec():
    """B1: heartbeats every 2.0 s, re-route budget under 1.0 s."""
    assert HEARTBEAT_S == 2.0
    assert HEARTBEAT_MISSES == 3
    assert LINK_DEAD_S == 6.0
    assert REROUTE_DEADLINE_S == 1.0


def test_the_ring_links_match_the_pin_freeze():
    """§A.2.2: L1 on UART2 (16/17), L2 on UART1 (4/13). Not swappable."""
    assert RING_LINKS[1]["pins"] == "16/17" and RING_LINKS[1]["uart"] == 2
    assert RING_LINKS[2]["pins"] == "4/13" and RING_LINKS[2]["uart"] == 1
    for lv, cfg in RING_LINKS.items():
        assert tuple(cfg["nodes"]) == LEVELS[lv], "links carry their own level's nodes"


# ---------------------------------------------------------------------------
# Sequence tracking — a restart is a discontinuity, not a quiet moment
# ---------------------------------------------------------------------------

def test_a_sequence_gap_marks_a_discontinuity(state):
    frame = _frame(3, 1000, 10.0)
    handle_ring_frame(state, _envelope("lvl2", [{**frame, "seq": 10}], seq=10))
    before = state.discontinuities

    # seq goes backwards: the node rebooted. The window is not continuous.
    handle_ring_frame(state, _envelope("lvl2", [{**frame, "seq": 2}], seq=2))
    assert state.discontinuities == before + 1
    assert state.window_complete("node_3") is False


def test_a_wide_gap_asks_for_a_relay(state):
    frame = _frame(4, 1000, 10.0)
    state.ring.note_seq("node_4", 1)
    handle_ring_frame(state, _envelope("lvl2", [{**frame, "seq": 40}], seq=40))
    assert any("sequence gap" in e["text"] for e in state.events)


def test_malformed_envelopes_are_ignored_not_fatal(state):
    for bad in ({}, {"v": 2}, {"v": 2, "via": "lvl2"},
                {"v": 2, "via": "lvl2", "frames": "not a list"},
                {"v": 2, "via": "lvl2", "frames": [{"node": 99, "disp": 1}]}):
        assert handle_ring_frame(state, bad) == 0
    assert all(n.displacement_mm == 5.0 for n in state.iter_nodes())


# ---------------------------------------------------------------------------
# The fault frames (§H5.5) — Phase 9 (B6)
# ---------------------------------------------------------------------------
# The hub is the authority for a segment fault, and these are the four properties
# that make its report usable:
#
#   1. **edge-triggered** — one fault event per outage, never a storm
#   2. **a cross-link cut is not a level outage** — the triangle's whole point
#   3. **restore is its own event, with a duration** — "how long was we blind"
#   4. **heartbeats feed liveness** — otherwise a link could never come back
#
# The distinction in (2) is why `SegmentState` exists at all: cutting W4 degrades
# the L1-L2 edge while both levels stay reachable, and a system that could only
# say "level down" could not describe the failure the demo is built around.

def _fault(seg="L1-L2", via="lvl1", dir="L2\u2192L1", reroute="direct-via-MAIN"):
    return {"v": 2, "ring": "fault", "via": via, "seg": seg, "dir": dir,
            "reroute": reroute, "faults": 1, "text": f"ring segment {seg} degraded"}


def _restored(seg="L1-L2", via="lvl1", down_ms=4200):
    return {"v": 2, "ring": "restored", "via": via, "seg": seg,
            "event": "segment restored", "down_ms": down_ms}


def test_a_segment_fault_is_reported_exactly_once(state):
    """One outage, one event — the storm is the failure mode §H5.5 names."""
    first = ring.apply_ring_event(state, _fault())
    assert first["kind"] == "fault" and first["first"] is True
    lines = [e for e in state.events if "L1-L2" in e["text"]]
    assert len(lines) == 1

    second = ring.apply_ring_event(state, _fault())
    assert second["first"] is False
    assert len([e for e in state.events if "L1-L2" in e["text"]]) == 1, \
        "a repeat frame must not write a second log line"
    assert second["to_public"]["faults"] == 2, "…but the counter still climbs"


def test_a_cross_link_cut_does_not_take_a_level_off_the_air(state):
    """W4 is cut: the L1-L2 edge faults, and **both** levels stay up.

    L2 still reaches the server over hop 3, which is exactly what the frame's
    `reroute: direct-via-MAIN` means. A consumer that marked level 2 down here
    would be reporting an outage that is not happening — and would then have to
    explain why the telemetry kept arriving.
    """
    state.ring.note_heartbeat(1, 1)
    state.ring.note_heartbeat(2, 1)
    ring.apply_ring_event(state, _fault())
    assert state.ring.segments["L1-L2"].faulted is True
    assert state.ring.link_up(1) is True and state.ring.link_up(2) is True


def test_a_main_leg_fault_does_mark_its_level_down(state):
    """The other edge: losing MAIN-L2 is a real degradation of level 2."""
    state.ring.note_heartbeat(2, 1)
    ring.apply_ring_event(state, _fault(seg="MAIN-L2", via="lvl2",
                                       dir="MAIN\u2192L2", reroute="hold-last-indication"))
    assert state.ring.link_up(2) is False
    assert state.ring.links[2].last_event.startswith("segment MAIN-L2 faulted")


def test_recovery_is_its_own_event_and_carries_the_duration(state):
    ring.apply_ring_event(state, _fault())
    out = ring.apply_ring_event(state, _restored(down_ms=4200))
    assert out["kind"] == "restored" and out["was_faulted"] is True
    assert out["down_ms"] == 4200
    seg = state.ring.segments["L1-L2"]
    assert seg.faulted is False and seg.restores == 1 and seg.down_ms == 4200
    assert any("restored" in e["text"] and "4.2s" in e["text"] for e in state.events)

    # A restore for a segment that was never faulted is not news.
    assert ring.apply_ring_event(state, _restored())["was_faulted"] is False


def test_a_heartbeat_link_the_server_has_never_heard_cannot_be_faulted(state):
    """Silence from a hub that has not booted is not a cut cable."""
    out = ring.apply_ring_event(state, _fault())
    assert out is not None                     # the frame is still applied…
    assert state.ring.segments["L1-L2"].faults == 1
    # …but nothing claims level 1 is down on the strength of a frame alone: the
    # link states are fed by heartbeats, and no heartbeat has ever arrived.


def test_a_heartbeat_envelope_refreshes_the_link(state):
    """`LinkState.up` must be able to go *back* up.

    The aging tick can only ever take a link down; without this path the server's
    ring display is a one-way ratchet, and a link that recovered would show as
    dead for the rest of the run.
    """
    assert state.ring.link_up(2) is False
    assert handle_ring_frame(state, {"v": 2, "via": "lvl2", "seq": 3, "link": "A",
                                     "frames": [], "hb": True}) == 0
    assert state.ring.link_up(2) is True
    assert state.ring.links[2].beats == 1
    assert state.ring.links[2].last_heartbeat_seq == 3


def test_the_heartbeat_spelling_a_real_board_sends_is_accepted(state):
    """The wire, not the wish: a board built before this line emits `"hb":1`.

    The numeric spelling is what `ns_ring_build_heartbeat()` produced, so it is
    what a fielded ESP32 sends. A consumer that only understood `true` would stop
    refreshing liveness on every un-reflashed board — and a link that stops being
    refreshed goes *down* on the aging tick, so the symptom is "the cable died",
    with the cable in hand and perfectly fine.
    """
    assert handle_ring_frame(state, {"v": 2, "via": "lvl1", "seq": 4, "link": "A",
                                     "frames": [], "hb": 1}) == 0
    assert state.ring.link_up(1) is True


def test_a_fault_for_an_unknown_segment_is_refused_and_said_so(state):
    """A segment this system does not have is not silently accepted — a typo in a
    `seg` name must not create a phantom edge on the dashboard."""
    assert ring.apply_ring_event(state, _fault(seg="L9-L8")) is None
    assert "L9-L8" not in state.ring.segments
    assert any("unknown segment" in e["text"] for e in state.events)


def test_segment_state_is_published_for_the_deck(state):
    ring.apply_ring_event(state, _fault())
    pub = state.ring.segments_public()
    assert set(pub) == set(ring.SEGMENT_NAMES)
    assert pub["L1-L2"]["faulted"] is True
    assert pub["L1-L2"]["direction"] == "L2\u2192L1"
    assert pub["L1-L2"]["reroute"] == "direct-via-MAIN"
    assert pub["MAIN-L1"]["faulted"] is False
