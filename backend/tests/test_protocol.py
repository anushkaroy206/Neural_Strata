"""Protocol tests — the wire format is a contract, so it gets the strictest tests."""
import sys
from pathlib import Path

for _p in (Path(__file__).resolve().parent.parent,          # backend/
           Path(__file__).resolve().parent.parent / "blackbox"):  # the gateway modules
    sys.path.insert(0, str(_p))

import protocol as P


def test_telemetry_round_trip():
    line = ('{"chunk_id":42,"seq":450,"ts_ms":142850,"node":2,"level":1,"tilt":0.14,'
            '"disp_mm":5.42,"accel":0.035,"vib":1,"micro":12.5,"macro":33.0,"flags":0}')
    m = P.parse_line(line)
    assert isinstance(m, P.TelemetryRow), m
    assert (m.chunk_id, m.seq, m.ts_ms, m.node) == (42, 450, 142850, 2)
    assert m.disp_mm == 5.42 and m.micro == 12.5
    # the payload we hand the cloud is stable and sorted
    p = m.to_payload()
    assert p.startswith('{"accel":') and '"chunk_id":42' in p


def test_legacy_short_json_still_parses():
    """The addendum's example frame used 'disp' not 'disp_mm'. Accept both."""
    m = P.parse_line('{"chunk_id":42,"seq":7,"ts_ms":1000,"node":2,"disp":5.42,'
                     '"tilt":0.14,"accel":0.035,"vib":0}')
    assert isinstance(m, P.TelemetryRow)
    assert m.disp_mm == 5.42
    assert m.level == 1          # inferred from node 2


def test_level_inferred_for_higher_nodes():
    m = P.parse_line('{"chunk_id":1,"seq":1,"ts_ms":1,"node":4}')
    assert m.level == 2


def test_control_lines():
    assert P.parse_line("CHUNK_OPEN:43") == P.ChunkOpen(43)
    c = P.parse_line("CHUNK_CLOSE:42,1000,0")
    assert (c.chunk_id, c.rows, c.dropped) == (42, 1000, 0)
    s = P.parse_line("CHUNK_STATE:42,450,0")
    assert (s.chunk_id, s.rows, s.closed) == (42, 450, False)
    s2 = P.parse_line("CHUNK_STATE:42,1000,0,CLOSED")
    assert s2.closed is True
    b = P.parse_line("BLACKBOX:OK,3712")
    assert (b.state, b.free_mb) == ("OK", 3712)
    assert P.parse_line("BLACKBOX:NOSD").state == "NOSD"
    d = P.parse_line("DROP:42,3")
    assert (d.chunk_id, d.count) == (42, 3)


def test_outbound_commands_are_zero_padded():
    assert P.format_ack_delete(42) == "ACK_DELETE:0042\n"
    assert P.format_replay(42, 57) == "REPLAY:0042:0057\n"
    assert P.format_state_query() == "STATE?\n"


def test_bad_lines_become_unknown_not_exceptions():
    bad = [
        "",                                   # empty
        "{not json",                          # broken JSON
        '{"chunk_id":1}',                     # missing required fields
        '{"chunk_id":1,"seq":1,"ts_ms":1,"node":9}',   # node out of range
        "CHUNK_CLOSE:42",                     # too few numbers
        "WAT:1",                              # unknown verb
        "CHUNK_OPEN:abc",                     # non-numeric
    ]
    for line in bad:
        m = P.parse_line(line)
        assert isinstance(m, P.Unknown), f"{line!r} parsed as {m!r}"
        assert m.reason


def test_pure_line_parsing_never_raises():
    import random
    import string
    rnd = random.Random(7)
    for _ in range(500):
        s = "".join(rnd.choice(string.printable) for _ in range(rnd.randint(0, 60)))
        P.parse_line(s)          # must not raise


# ── the stamped row the fielded hub sends (Phase 10, H4) ─────────────────────
# The hub splices its stamp onto the node's own frame, and two of the three names
# it needs are already taken: `seq` is the node's monotonic counter (the ring's gap
# watchdog counts it) and `ms` is the node's uptime (which is how the server
# measures the node's own clock, D9). So the hub writes `chunk_seq` and `ts_ms`
# beside them. Both spellings must parse — a `hub_sim` stream and a hub on a bench
# are the same protocol, and a parser that only understood the simulator would
# have passed every test we own and failed on the first real board.

STAMPED = ('{"v":2,"node":3,"ms":142000,"disp":41.2,"tilt":1.4,"acc":0.031,'
           '"vib":22,"micro":18,"macro":7.5,"rssi":-61,"batt":87,"flags":0,'
           '"seq":991,"chunk_id":42,"chunk_seq":17,"ts_ms":142850}')


def test_the_hubs_own_spelling_of_the_stamp_parses():
    row = P.parse_line(STAMPED)
    assert isinstance(row, P.TelemetryRow)
    assert (row.chunk_id, row.seq, row.ts_ms) == (42, 17, 142850)
    assert row.node == 3 and row.level == 2
    assert row.source == "live"


def test_the_nodes_own_counter_is_not_lost_by_the_blackbox():
    """Two different numbers, two different consumers, one line.

    The blackbox keys idempotency on the hub's per-chunk counter; the ring's gap
    watchdog reads the *node's* own counter. Choosing one for the store must not
    take the other off the line — the edge server parses the same JSON with a
    different reader, and if the splice had overwritten `seq`, every gap
    calculation on the deck would silently become nonsense.
    """
    from ingestion import serial_reader

    assert P.parse_line(STAMPED).seq == 17         # the chunk's row index (the key)
    frame = serial_reader.parse_frame(STAMPED)
    assert frame is not None and frame["seq"] == 991, "the node's counter survives"


def test_the_hub_counter_is_the_unique_key_across_nodes_in_one_chunk():
    """The defect this ordering exists to prevent.

    Four nodes each count from their own power-on, so node 1 and node 2 both emit
    `seq` 991 in the same chunk. Keyed on the node's counter the second row is a
    duplicate of the first and is *silently dropped* — a blackbox losing data on a
    path nobody would think to test. Keyed on the hub's counter, the two rows are
    17 and 18 and both land.
    """
    import store as store_mod

    st = store_mod.Store(":memory:")
    try:
        for node, chunk_seq in ((1, 17), (2, 18), (3, 19), (4, 20)):
            line = STAMPED.replace('"node":3', f'"node":{node}') \
                          .replace('"chunk_seq":17', f'"chunk_seq":{chunk_seq}')
            assert st.ingest_row(P.parse_line(line)), f"node {node} was deduped away"
        assert st.count_telemetry() == 4
        assert [r[0] for r in st.conn.execute(
            "SELECT node_id FROM sensor_telemetry WHERE chunk_id=42 ORDER BY seq")] == [1, 2, 3, 4]
        assert st.conn.execute("SELECT COUNT(*) FROM sensor_telemetry WHERE source='live'"
                               ).fetchone()[0] == 4
    finally:
        st.close()


def test_the_other_accepted_spellings():
    for key in ("ts_ms", "hub_ms", "ms"):
        line = ('{"chunk_id":7,"seq":3,"node":1,"ts_ms":1,"disp":0,"tilt":0,'
                '"acc":0,"vib":0,"micro":0,"macro":0,"flags":0}')
        line = line.replace('"ts_ms":1', f'"{key}":1234')
        row = P.parse_line(line)
        assert isinstance(row, P.TelemetryRow), key
        assert row.ts_ms == 1234, key


def test_a_row_with_no_count_at_all_is_protocol_error_and_is_counted():
    bad = ('{"chunk_id":7,"node":1,"ts_ms":1,"disp":0,"tilt":0,"acc":0,'
           '"vib":0,"micro":0,"macro":0,"flags":0}')
    msg = P.parse_line(bad)              # never raises
    assert isinstance(msg, P.Unknown)
    assert "seq" in msg.reason


def test_source_is_live_or_replay_and_nothing_else():
    for value, want in (("replay", "replay"), ("live", "live"),
                        ("REPLAY", "live"), ("banana", "live")):
        line = STAMPED.replace('"chunk_id":42', f'"source":"{value}","chunk_id":42')
        assert P.parse_line(line).source == want, value
    assert P.parse_line(STAMPED).source == "live", "default is live"


def test_replay_rows_still_round_trip_through_to_payload():
    row = P.parse_line(STAMPED.replace('"chunk_id":42', '"source":"replay","chunk_id":42'))
    back = P.parse_line(row.to_payload())
    assert back.source == "replay"
    assert (back.chunk_id, back.seq, back.ts_ms) == (42, 17, 142850)
    assert back.micro == row.micro and back.flags == row.flags
