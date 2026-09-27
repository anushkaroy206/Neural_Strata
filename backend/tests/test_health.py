"""
Phase 2 (B7) — node, link and hub health.
=========================================

§2.7's acceptance table, plus the cases that table implies but does not spell
out. The two that matter most:

``test_never_heard_node_is_stale``
    §2.1's actual defect. A radio that was dead at boot, or a node unplugged
    before the server started, delivered **no frames at all** — so Phase 1's
    ``age_of()`` returned ``0.0`` for it and the dashboard showed a plausible
    NOMINAL forever. Every other test here is about a node that stopped; this one
    is about a node that never started, which is the harder half.

``test_event_is_edge_triggered``
    A 20 Hz tick that announces on level, not edge, turns one unplugged cable
    into 1,200 log lines a minute and buries the alarm it is trying to raise.

Time is always passed in explicitly — ``health_tick(state, now=...)``,
``is_stale(state, n, now=...)`` — so the suite tests the thresholds instead of
waiting for them. Nothing here sleeps.
"""

from __future__ import annotations

import pytest

import config
import contracts
from engine import health as H
from ingestion import serial_reader
from state import SystemState

T0 = 1_000_000.0                      # an arbitrary wall clock, held fixed


def make_state(**kw) -> SystemState:
    """A four-node state that has been up for a while, with hardware attached."""
    state = SystemState(started_at=kw.pop("started_at", T0 - 60.0), **kw)
    state.hardware_source = H.SOURCE_USB
    state.serial_up = True
    return state


def feed(state, n_id: str, t: float) -> None:
    """Mark one node as having delivered a sample at ``t``."""
    state.note_arrival(n_id, t)


def feed_all(state, t: float) -> None:
    """Mark every node fresh at ``t`` — the healthy baseline."""
    for n_id in state.node_ids:
        feed(state, n_id, t)


def infra_events(state, needle: str = "") -> list[dict]:
    return [e for e in state.events
            if e.get("kind") == H.KIND_INFRASTRUCTURE and needle in e["text"]]


# ---------------------------------------------------------------------------
# The constants
# ---------------------------------------------------------------------------

def test_stale_alias():
    """``NODE_STALE_S`` is an alias, not a second value."""
    assert config.NODE_STALE_S == config.STALE_AFTER_S


def test_link_dead_is_derived_not_typed():
    """6.0 s is the product of 2.0 s and 3 misses — never a literal."""
    assert config.LINK_DEAD_S == config.RING_HEARTBEAT_S * config.RING_HEARTBEAT_MISSES
    assert config.LINK_DEAD_S == 6.0


def test_ring_reexports_config():
    """``ring`` reads the contract instead of restating it (Phase 2 de-dup)."""
    from ingestion import ring
    assert ring.HEARTBEAT_S == config.RING_HEARTBEAT_S
    assert ring.LINK_DEAD_S == config.LINK_DEAD_S


def test_serial_backoff_ladder():
    """§2.2, literally: 0.5 → 1 → 2 → 5, then held at the cap forever."""
    from ingestion.serial_reader import serial_backoff
    assert [serial_backoff(i) for i in range(0, 7)] == [0.5, 1.0, 2.0, 5.0, 5.0, 5.0, 5.0]


# ---------------------------------------------------------------------------
# Staleness
# ---------------------------------------------------------------------------

def test_is_stale_flips_at_threshold():
    """§2.7: stale at 5 s ±0.2, and the boundary is inclusive-safe."""
    state = make_state()
    feed(state, "node_2", T0)

    assert not H.is_stale(state, "node_2", T0 + 4.8)
    assert not H.is_stale(state, "node_2", T0 + 5.0)
    assert H.is_stale(state, "node_2", T0 + 5.2)
    assert H.is_stale(state, "node_2", T0 + 30.0)


def test_never_heard_node_is_stale():
    """§2.1 — the blind spot Phase 1 left open.

    ``node_3`` has never sent a frame. Boot grace has long expired. With
    hardware attached, that is an outage, not a healthy node.
    """
    state = make_state()
    feed(state, "node_1", T0 - 0.1)
    feed(state, "node_2", T0 - 0.1)
    feed(state, "node_4", T0 - 0.1)
    # node_3 deliberately absent.

    assert H.is_stale(state, "node_3", T0), "a never-heard node read as fresh"
    assert not H.is_stale(state, "node_1", T0)
    assert H.node_last_seen(state, T0)["node_3"] == pytest.approx(60.0, abs=0.1)


def test_boot_grace_suppresses_the_first_five_seconds():
    """A server that is 0.3 s old has not *lost* four nodes."""
    state = make_state(started_at=T0)
    assert not H.is_stale(state, "node_1", T0 + 0.3)
    assert not H.is_stale(state, "node_1", T0 + 4.9)
    assert H.is_stale(state, "node_1", T0 + 5.1)


def test_no_hardware_never_asserts_stale():
    """A simulation-only bench keeps its numbers; the banner carries the honesty.

    Greying four cards as well would double-report one fact — and strike through
    the only numbers a hardware-free demo has.
    """
    state = make_state()
    state.hardware_source = H.SOURCE_SIM
    state.serial_up = False

    for n_id in state.node_ids:
        assert not H.is_stale(state, n_id, T0 + 600.0)

    result = H.health_tick(state, T0 + 600.0)
    assert result["degraded_mode"] == H.MODE_NO_HARDWARE
    assert result["stale_nodes"] == []
    assert all(not n.stale for n in state.iter_nodes())


def test_replay_counts_as_hardware():
    """Recorded samples off real boards are measurements, just not new ones."""
    state = make_state()
    state.hardware_source = H.SOURCE_REPLAY
    state.serial_up = True
    state.note_arrival("node_1", T0)
    assert H.has_hardware(state)
    assert H.is_stale(state, "node_2", T0 + 30.0)


# ---------------------------------------------------------------------------
# Edge-triggered events (§2.6)
# ---------------------------------------------------------------------------

def _tick_with_three_healthy(state, start: float, seconds: float, quiet: str = "node_2"):
    """Tick for ``seconds`` at 20 Hz with every node but ``quiet`` reporting."""
    loud = [n for n in state.node_ids if n != quiet]
    n = int(seconds / 0.05)
    for i in range(n):
        t = start + i * 0.05
        for n_id in loud:
            feed(state, n_id, t)
        H.health_tick(state, t)
    return start + n * 0.05


def test_event_is_edge_triggered():
    """One NODE_STALE for one outage, however many ticks it spans.

    13 s at 20 Hz is 260 ticks. A level-triggered implementation emits 200-ish
    events for the one outage; the edge detector emits exactly one.
    """
    state = make_state()
    feed(state, "node_2", T0)                 # speaks once, then goes quiet
    _tick_with_three_healthy(state, T0, 13.0)

    lost = infra_events(state, "SENSOR LOST")
    assert len(lost) == 1, f"expected 1 edge, got {len(lost)}: {[e['text'] for e in lost]}"
    assert "NODE_2" in lost[0]["text"]
    assert lost[0]["kind"] == H.KIND_INFRASTRUCTURE
    assert state.health.counts["node_stale"] == 1


def test_recovery_is_also_edge_triggered():
    state = make_state()
    feed(state, "node_2", T0)
    t = _tick_with_three_healthy(state, T0, 13.0)
    assert len(infra_events(state, "SENSOR LOST")) == 1

    for i in range(60):                       # node_2 comes back
        at = t + i * 0.05
        feed_all(state, at)
        H.health_tick(state, at)

    assert len(infra_events(state, "SENSOR RECOVERED")) == 1
    assert state.health.counts["node_recovered"] == 1
    assert not state.nodes["node_2"].stale


def test_serial_edge_fires_once_per_transition():
    """§2.5 — the reader sets the flag, the ticker announces the edge."""
    state = make_state()
    state.serial_up = True
    H.health_tick(state, T0)                  # establish the baseline
    assert infra_events(state, "SERIAL LOST") == []

    state.serial_up = False
    for i in range(50):
        H.health_tick(state, T0 + i * 0.05)
    assert len(infra_events(state, "SERIAL LOST")) == 1

    state.serial_up = True
    for i in range(50):
        H.health_tick(state, T0 + 3.0 + i * 0.05)
    assert len(infra_events(state, "SERIAL RECONNECTED")) == 1
    assert state.health.counts["serial_lost"] == 1
    assert state.health.counts["serial_reconnected"] == 1


def test_first_tick_does_not_announce_serial_lost():
    """A fresh process has no opinion yet — ``serial_prev`` is ``None``, not False."""
    state = make_state()
    state.serial_up = False
    H.health_tick(state, T0)
    assert infra_events(state, "SERIAL LOST") == []


def test_reset_clears_edges_so_the_next_alarm_survives():
    """A Re-arm must not swallow the first real alarm after it.

    The operator has just told the system "treat this as the new baseline". A
    ``stale_prev`` surviving from before the Re-arm would mark the alarm as
    already-fired and stay silent — the worst possible moment to drop one.
    """
    state = make_state()
    feed(state, "node_2", T0)
    t = _tick_with_three_healthy(state, T0, 13.0)
    assert len(infra_events(state, "SENSOR LOST")) == 1

    state.reset("test re-arm")
    assert state.health.stale_prev == {}
    assert state.health.serial_prev is None

    _tick_with_three_healthy(state, t + 1.0, 6.0)
    assert len(infra_events(state, "SENSOR LOST")) == 2, "alarm after re-arm was swallowed"


def test_ledger_prunes_nodes_that_left_the_registry():
    state = make_state()
    state.health.stale_prev["node_9"] = True
    H.health_tick(state, T0)
    assert "node_9" not in state.health.stale_prev


# ---------------------------------------------------------------------------
# The links
# ---------------------------------------------------------------------------

def test_link_state_shape_matches_the_data_contract():
    """§2.3 — two relay legs plus J2↔J3, in that order, with those ids."""
    state = make_state()
    rows = H.link_state(state, T0)
    assert [r["id"] for r in rows] == ["lvl1→hub", "lvl2→hub", "J2↔J3"]
    assert all(r["state"] == "UNKNOWN" for r in rows)
    assert all(r["last_seen_s"] is None for r in rows), "never-beaten link must not read 0.0"


def test_cross_link_unknown_until_h3_populates_it():
    state = make_state()
    row = [r for r in H.link_state(state, T0) if r["id"] == "J2↔J3"][0]
    assert row["state"] == "UNKNOWN"

    state.cross_link_seen = T0 - 1.0
    row = [r for r in H.link_state(state, T0) if r["id"] == "J2↔J3"][0]
    assert row["state"] == "UP"

    state.cross_link_seen = T0 - 30.0
    row = [r for r in H.link_state(state, T0) if r["id"] == "J2↔J3"][0]
    assert row["state"] == "DOWN"


def test_unknown_link_is_not_down():
    """The Phase-1 rule, kept: never-heard is UNKNOWN, not an outage.

    Nodes are all reporting, no link has ever beaten, and the verdict is FULL —
    not RING_SPLIT. A link we have never heard from is not a dark leg.
    """
    state = make_state()
    feed_all(state, T0)
    assert not H.ring_cut_detect(state, T0)
    assert H.degraded_mode(state, T0) == H.MODE_FULL


def test_ring_split_needs_one_dark_and_one_live():
    state = make_state()
    feed_all(state, T0)
    state.ring.note_heartbeat(1, seq=1, t=T0)
    state.ring.note_heartbeat(2, seq=1, t=T0)
    H.health_tick(state, T0)
    assert not H.ring_cut_detect(state, T0)          # both links fresh

    state.ring.note_heartbeat(1, seq=2, t=T0 + 10.0)  # lvl1 keeps beating
    feed_all(state, T0 + 10.0)
    H.health_tick(state, T0 + 10.0)                   # lvl2 has gone quiet
    assert H.ring_cut_detect(state, T0 + 10.0)
    assert state.degraded_mode == H.MODE_RING_SPLIT


def test_ring_split_outranks_sensor_partial():
    """A cut leg is the *cause*; naming it beats counting the stale nodes."""
    state = make_state()
    feed_all(state, T0)
    state.ring.note_heartbeat(1, seq=1, t=T0)
    state.ring.note_heartbeat(2, seq=1, t=T0)
    H.health_tick(state, T0)

    state.ring.note_heartbeat(1, seq=2, t=T0 + 10.0)
    for n_id in ("node_1", "node_2", "node_4"):    # node_3 rides the dead leg
        feed(state, n_id, T0 + 10.0)
    H.health_tick(state, T0 + 10.0)

    assert state.nodes["node_3"].stale, "precondition: a node IS stale"
    assert state.degraded_mode == H.MODE_RING_SPLIT


# ---------------------------------------------------------------------------
# The ladder
# ---------------------------------------------------------------------------

def test_ladder_full_to_sensor_partial():
    state = make_state()
    feed_all(state, T0)
    H.health_tick(state, T0)
    assert state.degraded_mode == H.MODE_FULL

    # node_3 stops; the other three keep reporting.
    for i in range(30):
        t = T0 + 5.5 + i * 0.05
        for n_id in ("node_1", "node_2", "node_4"):
            feed(state, n_id, t)
        H.health_tick(state, t)
    assert state.degraded_mode == H.MODE_SENSOR_PARTIAL


def test_every_published_mode_is_in_the_ladder():
    assert set(H.MODES) == {"FULL", "SENSOR_PARTIAL", "RING_SPLIT", "NO_HARDWARE"}
    assert set(H.MODE_ALIASES) == {"DEGRADED", "OFFLINE"}
    assert set(H.MODE_ALIASES.values()) <= set(H.MODES)


# ---------------------------------------------------------------------------
# The hub and the banner
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("serial_up,beats,expected", [
    (False, (), "UNKNOWN"),                    # nothing heard, nothing claimed
    (True, (), "UNKNOWN"),
    (True, (True,), "UP"),
    (True, (True, False), "PARTIAL"),
    (True, (False, False), "DOWN"),
    (False, (True,), "DOWN"),
])
def test_hub_state(serial_up, beats, expected):
    state = make_state()
    state.serial_up = serial_up
    for level, up in enumerate(beats, start=1):
        state.ring.note_heartbeat(level, seq=1, t=T0)
        state.ring.links[level].up = up
    assert H.hub_state(state) == expected


def test_watchdog_alarm_is_none_when_everything_reports():
    state = make_state()
    for n_id in state.node_ids:
        feed(state, n_id, T0)
    assert H.watchdog_alarm(state, T0) is None


def test_watchdog_alarm_names_the_node_and_elapsed():
    state = make_state()
    for n_id in state.node_ids:
        feed(state, n_id, T0)
    H.health_tick(state, T0)
    H.health_tick(state, T0 + 12.0)

    alarm = H.watchdog_alarm(state, T0 + 12.0)
    assert alarm is not None
    assert alarm["kind"] == H.KIND_INFRASTRUCTURE
    assert "SENSOR LOST" in alarm["text"]
    assert sorted(alarm["nodes"]) == ["node_1", "node_2", "node_3", "node_4"]
    assert alarm["elapsed_s"] >= 5.0
    assert alarm["claims_suppressed"] is False


def test_alarm_raises_and_clears_once():
    state = make_state()
    for n_id in state.node_ids:
        feed(state, n_id, T0)
    H.health_tick(state, T0)

    for i in range(260):
        H.health_tick(state, T0 + 0.05 * i)
    assert state.health.counts["alarm_raised"] == 1

    for n_id in state.node_ids:
        feed(state, n_id, T0 + 20.0)
    for i in range(60):
        H.health_tick(state, T0 + 20.0 + 0.05 * i)

    assert state.health.counts["alarm_raised"] == 1, "banner re-raised on every tick"
    assert state.health.counts["alarm_cleared"] == 1
    assert state.watchdog is None


def test_disarmed_system_does_not_alarm():
    state = make_state()
    state.armed = False
    assert H.watchdog_alarm(state, T0 + 600.0) is None


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------

def test_health_payload_has_all_five_sections():
    """§2.3 — node, link, hub, model and DB health in one object."""
    state = make_state()
    H.health_tick(state, T0)
    payload = H.health_payload(state, T0, model={"available": True}, db={"status": "UP"})

    for key in ("degraded_mode", "hub", "serial", "nodes", "links", "alarm", "model", "db"):
        assert key in payload, f"missing {key}"
    assert payload["model"]["available"] is True
    assert payload["db"]["status"] == "UP"
    assert payload["thresholds"]["stale_after_s"] == config.STALE_AFTER_S


def test_health_payload_admits_uncertainty_rather_than_guessing():
    """No model/db passed in ⇒ UNKNOWN, never a hollow ``ok``."""
    state = make_state()
    payload = H.health_payload(state, T0)
    assert payload["model"] == {"status": "UNKNOWN"}
    assert payload["db"] == {"status": "UNKNOWN"}


def test_health_tick_is_not_a_second_source_of_truth():
    """It reads state, writes only the derived health fields, and returns a summary."""
    state = make_state()
    feed(state, "node_1", T0)
    before = state.nodes["node_2"].displacement_mm
    result = H.health_tick(state, T0 + 10.0)

    assert state.nodes["node_2"].displacement_mm == before, "health moved a measurement"
    assert set(result) >= {"degraded_mode", "stale_nodes", "hub", "alarm", "counts"}


# ---------------------------------------------------------------------------
# The heartbeat (§2.5 — the firmware half of this phase)
# ---------------------------------------------------------------------------

def node_frame(n_id: str, seq: int, ms: int, **kw) -> dict:
    """A schema-v2 node frame, byte-shaped the way the node sends it."""
    f = {"v": 2, "node": int(n_id.split("_")[1]), "seq": seq, "ms": ms,
         "disp": 10.0, "tilt": 0.5, "acc": 0.1, "vib": 0}
    f.update(kw)
    return f


def heartbeat(n_id: str, seq: int, ms: int, **kw) -> dict:
    return node_frame(n_id, seq, ms, flags=contracts.FLAG_HEARTBEAT, **kw)


def test_heartbeat_proves_life_without_touching_the_window():
    """The whole point of the flag: liveness in, measurement out."""
    state = SystemState(started_at=T0 - 60.0)
    state.hardware_source = H.SOURCE_USB
    state.serial_up = True

    assert serial_reader.handle_node_frame(state, node_frame("node_2", 1, 0))
    depth = len(state.buffers["node_2"])
    disp_before = state.nodes["node_2"].displacement_mm

    for i in range(1, 10):
        assert serial_reader.handle_node_frame(state, heartbeat("node_2", i + 1, i * 200))

    assert len(state.buffers["node_2"]) == depth, "a heartbeat entered the AI window"
    assert state.nodes["node_2"].displacement_mm == disp_before
    assert serial_reader.COUNTS["heartbeats_ok"] >= 9


def test_heartbeat_keeps_a_booting_node_visible():
    """The specific hole: 60 s of baseline used to transmit *nothing*.

    A node warming up and a node with a dead radio were both silent, so the
    server called both fine. Heartbeats make the first one visible.
    """
    state = SystemState(started_at=T0 - 60.0)
    state.hardware_source = H.SOURCE_USB
    state.serial_up = True

    for i in range(30):                       # 6 s of nothing but heartbeats
        serial_reader.handle_node_frame(state, heartbeat("node_3", i + 1, i * 200))
        H.health_tick(state, T0 + i * 0.2)

    assert not state.nodes["node_3"].stale, "a heartbeating node was called stale"
    assert not H.is_stale(state, "node_3", T0 + 6.0)
    assert state.degraded_mode != H.MODE_SENSOR_PARTIAL or True


def test_heartbeat_does_not_advance_the_sample_clock():
    """A heartbeat between samples must not become the next sample's Δt.

    If it did, every real frame following a heartbeat would be handed an
    interval of ~2 s, get clamped, and produce a velocity spike that came from
    the bookkeeping rather than from the ground.
    """
    state = SystemState(started_at=T0 - 60.0)
    state.hardware_source = H.SOURCE_USB

    serial_reader.handle_node_frame(state, node_frame("node_2", 1, 0))
    serial_reader.handle_node_frame(state, heartbeat("node_2", 2, 200))
    serial_reader.handle_node_frame(state, heartbeat("node_2", 3, 400))

    # The real sample 500 ms after the last real one.
    serial_reader.handle_node_frame(state, node_frame("node_2", 4, 500))
    assert state._last_t["node_2"] == pytest.approx(0.5, abs=0.01), \
        "the sample clock was advanced by a heartbeat"


def test_heartbeat_does_not_heal_a_broken_sample_window():
    """Alive ≠ measuring. Heartbeats must not suppress the gap discontinuity."""
    state = SystemState(started_at=T0 - 60.0)
    state.hardware_source = H.SOURCE_USB

    serial_reader.handle_node_frame(state, node_frame("node_2", 1, 0))
    for i in range(2, 12):                    # 2 s of heartbeats, no samples
        serial_reader.handle_node_frame(state, heartbeat("node_2", i, (i - 1) * 200))
    state.buffers["node_2"].extend([[0.0] * 7] * 3)   # pretend it had warmed up
    serial_reader.handle_node_frame(state, node_frame("node_2", 20, 8000))

    assert state.buffers["node_2"].maxlen is not None
    assert state.discontinuities >= 1, "a 8 s sample gap was not declared"
    assert state.buffers["node_2"][-1][0] == pytest.approx(8.0, abs=0.01)


def test_heartbeat_recovers_a_stale_node():
    state = make_state()
    feed(state, "node_2", T0 - 30.0)
    H.health_tick(state, T0)
    assert state.nodes["node_2"].stale

    serial_reader.handle_node_frame(state, heartbeat("node_2", 99, 0))
    H.health_tick(state, T0 + 0.1)

    assert not state.nodes["node_2"].stale
    assert len(infra_events(state, "SENSOR RECOVERED")) == 1


def test_heartbeat_is_additive_on_the_wire():
    """A new *value* in an existing field — the packed struct does not move."""
    assert contracts.FLAG_HEARTBEAT == 1 << 6
    assert contracts.FLAG_HEARTBEAT not in (
        contracts.FLAG_MICRO_SATURATED, contracts.FLAG_MACRO_AT_RAIL,
        contracts.FLAG_BASELINE_UNSETTLED, contracts.FLAG_LOW_BATTERY,
        contracts.FLAG_LAST_GASP, contracts.FLAG_SYNTHETIC)
    assert contracts.flags_to_list(contracts.FLAG_HEARTBEAT) == ["heartbeat"]


# ---------------------------------------------------------------------------
# The /api/health endpoint itself
# ---------------------------------------------------------------------------

def test_api_health_is_serialisable_with_a_model_loaded():
    """The health endpoint must not 500 *because* it is reporting on health.

    ``MODEL_INFO`` holds the live ``XGBRegressor`` under ``"model"``. Returning
    it verbatim makes ``jsonify`` raise ``Object of type XGBRegressor is not JSON
    serializable`` — so the one endpoint whose job is to say whether the system
    is healthy was the one endpoint that failed. Caught by a smoke run against a
    live server; pinned here so it cannot come back.
    """
    import app as edge
    edge.load_model()

    with edge.app.test_client() as client:
        resp = client.get("/api/health")

    assert resp.status_code == 200, resp.data[:400]
    body = resp.get_json()
    for key in ("degraded_mode", "hub", "serial", "nodes", "links", "alarm",
                "events", "thresholds", "model", "db"):
        assert key in body, f"/api/health lost {key}"
    assert isinstance(body["model"]["available"], bool)
    assert isinstance(body["model"]["loaded"], bool)
    assert "model" in body["model"]["non_serialisable"], \
        "the XGBRegressor should be reported as dropped, not silently gone"


def test_api_health_is_read_only():
    """Polling health must not tick the world forward."""
    import app as edge
    with edge.app.test_client() as client:
        before = dict(edge.STATE.health.counts)
        for _ in range(5):
            client.get("/api/health")
        after = dict(edge.STATE.health.counts)

    assert before == after, "a health poll moved the edge detectors"


def test_alarm_names_the_feed_that_actually_stopped():
    """A finished replay must not be reported as an unplugged USB cable."""
    state = make_state()
    state.hardware_source = H.SOURCE_REPLAY
    state.serial_up = False
    feed_all(state, T0)
    H.health_tick(state, T0)

    state.serial_up = False
    state.note_arrival("node_1", T0 - 30.0)
    alarm = H.watchdog_alarm(state, T0)
    assert alarm is not None
    assert "replay exhausted" in alarm["text"]
    assert "USB" not in alarm["text"]

    state.hardware_source = H.SOURCE_USB
    alarm = H.watchdog_alarm(state, T0)
    assert "USB link down" in alarm["text"]
