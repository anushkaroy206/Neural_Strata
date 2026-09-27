"""
§5.7 (B15) — reconciliation monotonicity, and REPLAY back-fill.
================================================================

Two halves, and they are tested separately on purpose:

**Reconciliation** is a *merge policy*, and its whole content is the set of things
a reply is not allowed to do: lower a count, un-close a chunk, erase rows, forget a
drop. Every one of those is a test here, because each of them is a way a safety
system can lose data while reporting that everything is fine.

**Recovery** is the beat §5.7 calls *"the demo that wins the room"*: rows the hub
wrote and the link did not deliver, re-streamed off the card and back-filled. The
tests run it against `sim.hub_sim.SimHub` — a stand-in hub that keeps a card and
answers `STATE?` / `REPLAY` / `ACK_DELETE` — so what is exercised is the real
pipeline (ingest → store → reconciler → recovery → store), not a mock of itself.

The rig
-------
    SimHub (has a card, can lose rows)  ⇄  _Wire (the USB cable)  ⇄  Gateway

`_Wire.write` is what the edge server's serial reader does for real: put the bytes
on the line. Here "the line" is a function call into the hub, which is what makes
the handshake and the replay observable without hardware.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

for _p in (Path(__file__).resolve().parent.parent,          # backend/
           Path(__file__).resolve().parent.parent / "blackbox"):  # the gateway modules
    sys.path.insert(0, str(_p))

import protocol as P                                    # noqa: E402
import reconciliation as recon                          # noqa: E402
import recovery as rec_mod                              # noqa: E402
from gateway import Gateway                             # noqa: E402
from ingest import Ingestor                             # noqa: E402
from sim.hub_sim import SimHub                          # noqa: E402
from store import Store                                 # noqa: E402

CHUNK = 40                      # rows per chunk, four nodes per sequence step


# ══════════════════════════════════════════════════════════ the rig
class _Wire:
    """The USB cable: what the laptop writes goes to the hub, and is recorded."""

    def __init__(self, hub):
        self.hub = hub
        self.lines: list[str] = []

    def write(self, data) -> None:
        text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
        for line in text.splitlines():
            if not line:
                continue
            self.lines.append(line)
            self.hub.write(line)                    # the cable, not a log file

    def since(self, n: int) -> list[str]:
        return self.lines[n:]


class Rig:
    """A gateway wired to a hub that has a card."""

    def __init__(self, *, chunks: int = 3, rows: int = CHUNK, **hub_kw):
        self.td = tempfile.TemporaryDirectory()
        self.gw = Gateway(db_path=Path(self.td.name) / "bbx.sqlite",
                          external_feed=True, card_mb=8000,
                          sim_rows_per_chunk=rows, sim_rows_per_sec=10_000.0)
        self.hub = SimHub(rows_per_chunk=rows, chunks=chunks, **hub_kw)
        # the gateway's own source object is a SimSource in external-feed mode; swap
        # its hub for ours so the stream carries the faults this test wants.
        self.gw.source.hub = self.hub
        self.wire = _Wire(self.hub)
        self.gw.attach_writer(self.wire.write)

    # ---------------------------------------------------------------- plumbing
    def push_one(self) -> bool:
        """Deliver one line if the hub has one. False means "nothing right now"."""
        line = self.hub.poll()
        if line is None:
            return False
        self.gw.feed(line)
        return True

    def pull(self, count: int) -> int:
        """Deliver up to `count` lines, stopping early if the hub has gone quiet."""
        rows = 0
        for _ in range(count):
            line = self.hub.poll()
            if line is None:
                break
            if line.startswith("{"):
                rows += 1
            self.gw.feed(line)
        return rows

    def run_until(self, pred, limit: int = 5000, what: str = "condition") -> None:
        """Deliver lines until `pred()` holds — and **fail**, rather than hang.

        Every wait in this file is a bounded pull on a non-blocking hub (`poll()`),
        so a bug shows up as an assertion in under a second instead of a test run
        that never returns. `time.sleep(0)` is not a delay: it is the yield that
        keeps a tight loop from starving the rest of the suite.
        """
        for _ in range(limit):
            if pred():
                return
            if not self.push_one():
                time.sleep(0.001)
        raise AssertionError(f"timed out waiting for {what}: {self.hub.summary()}")

    def run_until_chunk(self, cid: int) -> None:
        self.run_until(lambda: (self.gw.store.chunk(cid) or {"state": None})["state"] == "CLOSED",
                       what=f"chunk {cid} to close")

    def drain(self, limit: int = 400) -> int:
        """Deliver everything the hub has queued right now. Never blocks."""
        sent = 0
        for _ in range(limit):
            if not self.push_one():
                break
            sent += 1
        return sent

    def ask(self) -> None:
        """Ask the hub what it has (`STATE?`) and deliver everything available."""
        self.gw.reconciler.on_reconnect(self.wire.write, force=True)
        self.drain()

    def close(self) -> None:
        self.gw.stop()
        self.td.cleanup()


def _fresh_ingestor():
    store = Store(":memory:")
    return store, Ingestor(store, log=lambda *_: None)


def _closed(store, cid, rows=CHUNK, dropped=0, received=CHUNK, acked=False):
    """Write a chunk row directly, for the merge-policy tests."""
    store.note_chunk_open(cid)
    for seq in range(1, received + 1):
        store.ingest_row(P.TelemetryRow(chunk_id=cid, seq=seq, ts_ms=seq * 125, node=1,
                                        level=1, tilt=0.1, disp_mm=5.0, accel=0.01,
                                        vib=0, micro=0.0, macro=1.0, flags=0))
    store.note_chunk_close(cid, rows, dropped)
    if acked:
        store.mark_ack_sent(cid, "test")
    return store.chunk(cid)


# ══════════════════════════════════════════════════════════ the wire
def test_chunk_done_parses_and_round_trips():
    msg = P.parse_line(P.format_chunk_done(7, 1000, 3).strip())
    assert isinstance(msg, P.ChunkDone)
    assert (msg.chunk_id, msg.rows, msg.dropped) == (7, 1000, 3)
    state = P.parse_line(P.format_chunk_state(7, 412, 0, closed=False).strip())
    assert isinstance(state, P.ChunkState) and state.closed is False
    assert isinstance(P.parse_line("CHUNK_STATE:0007,412,0,CLOSED"), P.ChunkState)
    assert P.parse_line("CHUNK_STATE:0007,412,0,CLOSED").closed is True
    assert isinstance(P.parse_line("CHUNK_DONE:"), P.Unknown), "never fatal"


def test_a_file_report_is_a_message_and_never_an_unknown():
    """The real hub sends `CHUNK_FILE` behind every `CHUNK_CLOSE`.

    A parser that counted it as *unknown* would put a number on the operator's
    screen that climbs by one every rotation on a perfectly healthy hub — and the
    unknown counter is the one that has to mean "the hub sent something I could not
    read", or nothing.
    """
    store, ing = _fresh_ingestor()
    ing.handle(P.ChunkClose(chunk_id=8, rows=40, dropped=0))
    ing.handle(P.ChunkFile(chunk_id=8, bytes=1843, dropped=0))
    assert ing.stats.messages == 1 and ing.stats.unknown == 0
    assert ing.stats.last_file == (8, 1843, 0)
    assert P.parse_line("CHUNK_FILE:0008,1843,0").bytes == 1843
    assert isinstance(P.parse_line("CHUNK_FILE:broken"), P.Unknown)


# ══════════════════════════════════════════════════════ reconciliation: no going back
def test_a_reply_never_lowers_a_count():
    """The reply is a *stale* picture, not a correction.

    A hub that has written 40 rows and replies "32" — because the reply was composed
    before the last flush — must not drag our target down to 32. If it did, the
    eight rows that really are missing would look present, the chunk would look
    complete, and the file holding them would be unlinked.
    """
    store, ing = _fresh_ingestor()
    _closed(store, 1, rows=40, received=40)
    ing.handle(P.ChunkDone(chunk_id=1, rows=32, dropped=0))
    c = store.chunk(1)
    assert c["expected_rows"] == 40, "the reply must not shrink the target"
    assert c["received_rows"] == 40
    assert c["state"] == "CLOSED"


def test_a_reply_cannot_un_close_a_chunk():
    store, ing = _fresh_ingestor()
    _closed(store, 2)
    before = dict(store.chunk(2))
    ing.handle(P.ChunkState(chunk_id=2, rows=10, dropped=0, closed=False))
    after = dict(store.chunk(2))
    assert after["state"] == "CLOSED"
    assert after["expected_rows"] == before["expected_rows"] == CHUNK
    assert after["closed_at"] == before["closed_at"], "the margin counts from the first close"


def test_a_reply_cannot_erase_rows():
    store, ing = _fresh_ingestor()
    _closed(store, 3)
    rows_before = store.count_telemetry(3)
    for msg in (P.ChunkState(chunk_id=3, rows=0, dropped=0, closed=False),
                P.ChunkDone(chunk_id=3, rows=1, dropped=0)):
        ing.handle(msg)
    assert store.count_telemetry(3) == rows_before == CHUNK


def test_a_reply_may_raise_a_count_and_then_there_is_a_gap():
    """The other direction, which must work: the hub had more than it said."""
    store, ing = _fresh_ingestor()
    _closed(store, 4, rows=40, received=40)
    merge = ing.handle(P.ChunkDone(chunk_id=4, rows=52, dropped=0))
    assert merge is not None
    assert store.chunk(4)["expected_rows"] == 52
    rc = recon.Reconciler(store)
    gaps = rc.gaps()
    assert [g.chunk_id for g in gaps] == [4] and gaps[0].missing == 12
    assert "12 row(s) on the card" in gaps[0].note


def test_a_reply_creates_a_chunk_we_never_saw():
    """The wiped-laptop case: we hold nothing, the hub holds a closed file."""
    store, ing = _fresh_ingestor()
    fact = None
    ing.handle(P.ChunkDone(chunk_id=9, rows=40, dropped=0))
    fact = ing.last_merge
    assert fact["created"] is True and fact["closed_now"] is False
    c = store.chunk(9)
    assert c["state"] == "CLOSED" and c["expected_rows"] == 40 and c["received_rows"] == 0
    assert recon.Reconciler(store).gaps()[0].missing == 40


def test_drops_survive_a_reply_that_forgot_them():
    """A rebooted hub cannot clear a drop by no longer mentioning it."""
    store, ing = _fresh_ingestor()
    _closed(store, 5, rows=40, dropped=7, received=40)
    ing.handle(P.ChunkDone(chunk_id=5, rows=40, dropped=0))
    assert store.chunk(5)["dropped_rows"] == 7
    ing.handle(P.ChunkDone(chunk_id=5, rows=40, dropped=9))
    assert store.chunk(5)["dropped_rows"] == 9, "…but more is more"


def test_merging_the_same_reply_three_times_changes_nothing():
    store, ing = _fresh_ingestor()
    _closed(store, 6)
    ing.handle(P.ChunkDone(chunk_id=6, rows=40, dropped=0))
    snap = dict(store.chunk(6))
    for _ in range(2):
        ing.handle(P.ChunkDone(chunk_id=6, rows=40, dropped=0))
    assert dict(store.chunk(6)) == snap


def test_the_reconciler_reports_replies_that_told_us_something_new():
    store, ing = _fresh_ingestor()
    rc = recon.Reconciler(store)
    _closed(store, 7)
    rc.note_reply(P.ChunkDone(7, 40, 0), merge=store.note_chunk_done(7, 40, 0))
    assert rc.handshake.done_replies == 1
    assert rc.handshake.new_facts == 0, "a reply about a chunk we already know is not news"
    rc.note_reply(P.ChunkDone(8, 40, 0), merge=store.note_chunk_done(8, 40, 0))
    assert rc.handshake.new_facts == 1
    assert "1 chunk(s) short of 40 row(s)" in rc.reconcile_summary()


# ══════════════════════════════════════════════════════ the handshake
def test_the_handshake_is_rate_limited_but_force_asks_anyway():
    store, _ = _fresh_ingestor()
    clock = [0.0]
    rc = recon.Reconciler(store, clock=lambda: clock[0], min_gap_s=5.0)
    sent: list[str] = []
    assert rc.on_reconnect(sent.append) is True
    assert sent == ["STATE?\n"]
    clock[0] = 1.0
    assert rc.on_reconnect(sent.append) is False, "a flapping cable must not spam the hub"
    assert len(sent) == 1
    clock[0] = 6.0
    assert rc.on_reconnect(sent.append) is True
    assert len(sent) == 2
    assert rc.on_reconnect(sent.append, force=True) is True, "a human pressing the button"
    assert len(sent) == 3 and rc.handshake.asks == 3


def test_gaps_ignore_what_cannot_be_recovered():
    """Four chunks, four reasons not to ask — none of them may become a candidate."""
    store, _ = _fresh_ingestor()
    store.note_chunk_open(11)                                   # open: still being written
    _closed(store, 12, rows=40, received=40)                    # complete
    _closed(store, 13, rows=40, received=20)                    # ← the only real gap
    _closed(store, 14, rows=40, received=20, acked=True)        # released: file is gone
    store.note_chunk_open(15)                                   # no close: no expected count
    store.conn.execute("UPDATE blackbox_chunks SET state='OPEN', expected_rows=NULL"
                       " WHERE chunk_id=15")
    rc = recon.Reconciler(store)
    assert [g.chunk_id for g in rc.gaps()] == [13]
    assert [g.chunk_id for g in rc.gaps(include_open=True)] == [13], (
        "chunk 15 has no expected count even when open chunks are considered — "
        "absence of a count is not a licence to guess one")


# ══════════════════════════════════════════════════════ recovery: REPLAY
def test_a_lost_link_is_repaired_from_the_card():
    """§5.7's beat, end to end: the link drops 20 rows, the card still has them."""
    rig = Rig(chunks=2)
    try:
        rig.hub.skip_rows(1, 20)                    # written to the card, never sent
        rig.run_until_chunk(2)
        c1 = rig.gw.store.chunk(1)
        assert (c1["received_rows"], c1["expected_rows"]) == (20, 40)
        assert rig.hub.card_rows(1) == 40, "the card kept the rows the link lost"
        assert [g.chunk_id for g in rig.gw.reconciler.gaps()] == [1]

        out = rig.gw.force_reconcile()
        assert out["state_query_sent"] is True
        assert out["replay_lines"] == ["REPLAY:0001:0001\n"], out["replay_lines"]
        assert out["missing_rows"] == 20

        rig.drain()                                  # the hub re-streams the file
        rig.run_until(lambda: rig.gw.store.chunk(1)["received_rows"] >= 40,
                      what="the back-filled rows")

        c1 = rig.gw.store.chunk(1)
        assert c1["received_rows"] == 40 and rig.gw.store.count_telemetry(1) == 40
        replayed = rig.gw.store.conn.execute(
            "SELECT COUNT(*) FROM sensor_telemetry WHERE chunk_id=1 AND source='replay'"
        ).fetchone()[0]
        assert replayed == 20, "the back-filled rows are labelled, not disguised as live"
        assert rig.gw.reconciler.gaps() == [], "the gap is closed"

        rig.gw.recovery.tick()
        blk = rig.gw.recovery.block()
        assert blk["recovered"] == {"chunks": 1, "rows": 20}, blk["recovered"]
        assert blk["in_flight"] == 0 and blk["awaiting"] == 0
        assert any(h["state"] == "RECOVERED" and h["chunk_id"] == 1 for h in blk["history"])
    finally:
        rig.close()


def test_replaying_the_same_chunk_again_adds_nothing():
    rig = Rig(chunks=2)
    try:
        rig.hub.skip_rows(1, 12)
        rig.run_until_chunk(2)
        rig.gw.force_reconcile()
        rig.drain()
        rig.run_until(lambda: rig.gw.store.count_telemetry(1) >= 40,
                      what="the first back-fill")
        before = (rig.gw.store.count_telemetry(1), rig.gw.store.count_outbox(),
                  rig.gw.store.pending_total())

        # ask again, by hand, and let the hub re-stream the whole file
        rig.wire.write("REPLAY:0001:0001\n")
        rig.drain()
        assert (rig.gw.store.count_telemetry(1), rig.gw.store.count_outbox(),
                rig.gw.store.pending_total()) == before, "replay is idempotent"
    finally:
        rig.close()


def test_contiguous_gaps_coalesce_into_one_line():
    store, _ = _fresh_ingestor()
    for cid in (21, 22, 23):
        _closed(store, cid, rows=40, received=10)
    rc = recon.Reconciler(store)
    rec = rec_mod.Recovery(store, rc)
    assert rec.plan() == ["REPLAY:0021:0023\n"]
    assert len(rec.requests) == 3, "one line, three files, three sets of bookkeeping"
    assert store.chunk(21)["recover_at"] is not None, "the store records that we asked"


def test_a_burst_is_bounded_and_the_rest_waits():
    store, _ = _fresh_ingestor()
    for cid in range(31, 31 + 12):
        _closed(store, cid, rows=40, received=10)
    rc = recon.Reconciler(store)
    rec = rec_mod.Recovery(store, rc, max_chunks=8)
    lines = rec.plan()
    assert lines == ["REPLAY:0031:0038\n"], lines[:3]
    assert len(rec.requests) == 8
    blk = rec.block()
    assert blk["awaiting"] == 4, "the other four are queued, not forgotten"


def test_a_silent_hub_exhausts_after_three_asks():
    """A recovery that retries forever is indistinguishable from one that works."""
    store, _ = _fresh_ingestor()
    _closed(store, 41, rows=40, received=10)
    clock = [0.0]
    rc = recon.Reconciler(store, clock=lambda: clock[0])
    rec = rec_mod.Recovery(store, rc, clock=lambda: clock[0], cooldown_s=10.0,
                           no_progress_s=30.0, max_attempts=3)
    assert rec.plan() == ["REPLAY:0041:0041\n"]           # ask 1
    clock[0] = 11.0
    assert rec.plan() == ["REPLAY:0041:0041\n"]           # ask 2 (cooled down)
    clock[0] = 22.0
    assert rec.plan() == ["REPLAY:0041:0041\n"]           # ask 3
    assert rec.requests[41].attempts == 3
    clock[0] = 55.0
    assert rec.tick() == [], "no fourth ask"
    blk = rec.block()
    assert blk["exhausted"] == [41] and blk["in_flight"] == 0
    assert rec.state_of(41) == "EXHAUSTED"


def test_recovery_never_asks_again_for_a_released_file():
    store, _ = _fresh_ingestor()
    _closed(store, 51, rows=40, received=10)
    rc = recon.Reconciler(store)
    rec = rec_mod.Recovery(store, rc)
    assert rec.plan() == ["REPLAY:0051:0051\n"]
    store.mark_ack_sent(51, "released while we were asking")
    assert rec.tick() == []
    assert rec.block()["in_flight"] == 0
    assert rec.state_of(51) == "GONE"
    rec.forget(51)
    assert rec.block()["history"] == []


def test_a_dropped_chunk_is_still_replayable():
    """The distinction the ledger has to keep straight.

    Five rows were lost in the hub's own queue — they were never written to the card,
    so no replay can bring them back. Twelve *other* rows are on the card and were
    never delivered. The twelve are recoverable; the five are gone. A recovery path
    that refused to act because the chunk also had drops would abandon the twelve.
    """
    store, _ = _fresh_ingestor()
    _closed(store, 61, rows=40, dropped=5, received=28)
    rc = recon.Reconciler(store)
    gap = rc.gaps()[0]
    assert (gap.missing, gap.dropped) == (12, 5)
    assert "12 row(s) replayable from the card" in gap.note
    assert "5 lost in the hub's queue" in gap.note

    rec = rec_mod.Recovery(store, rc)
    assert rec.plan() == ["REPLAY:0061:0061\n"]

    # the card's copy arrives: 12 rows back, and the five are still missing
    for seq in range(29, 41):
        store.ingest_row(P.TelemetryRow(chunk_id=61, seq=seq, ts_ms=seq * 125, node=1,
                                        level=1, tilt=0.1, disp_mm=5.0, accel=0.01,
                                        vib=0, micro=0.0, macro=1.0, flags=0),
                         source="replay")
    rec.tick()
    assert rec.block()["recovered"]["rows"] == 12
    assert store.chunk(61)["dropped_rows"] == 5, "and the loss is still on the record"
    assert store.chunk(61)["expected_rows"] == 40


def test_the_recovery_block_carries_what_the_deck_renders():
    store, _ = _fresh_ingestor()
    _closed(store, 71, rows=40, received=20)
    rc = recon.Reconciler(store)
    rec = rec_mod.Recovery(store, rc)
    rec.plan()
    blk = rec.block()
    assert set(blk) >= {"handshake", "awaiting", "active", "in_flight", "recovered",
                        "exhausted", "history", "replay_lines_sent"}
    active = blk["active"][0]
    assert set(active) >= {"chunk_id", "line", "state", "attempts", "age_s", "missing",
                           "expected", "rows_recovered", "note"}
    assert active["line"] == "REPLAY:0071:0071" and active["missing"] == 20
    hs = blk["handshake"]
    assert set(hs) >= {"asks", "state_replies", "done_replies", "new_facts", "gaps",
                       "missing_rows"}
    assert hs["missing_rows"] == 20 and hs["gaps"][0]["chunk_id"] == 71


def test_recovery_and_the_gate_agree_after_a_backfill():
    """P10 meets P11: recovered rows are **new outbox work**.

    A back-filled row has not been confirmed by the cloud yet, so the gate must keep
    refusing the delete until it has been. Recovery restores the data; it does not
    short-circuit the condition that protects it.
    """
    rig = Rig(chunks=2)
    try:
        rig.hub.skip_rows(1, 10)
        rig.run_until_chunk(2)
        rig.gw.force_reconcile()
        rig.drain()
        rig.run_until(lambda: rig.gw.store.chunk(1)["received_rows"] >= 40,
                      what="the back-filled rows")
        rig.gw.store.conn.execute("UPDATE blackbox_chunks SET closed_at=?"
                                  " WHERE chunk_id=1", ("2020-01-01T00:00:00.000+00:00",))
        assert rig.gw.ingestor.sweep_acks() == [], "the ten recovered rows are unsynced"
        reasons = rig.gw.ingestor.sweep_reasons()
        assert "not yet confirmed" in reasons[1], reasons
        rig.gw.store.conn.execute("UPDATE cloud_outbox SET status='SENT' WHERE chunk_id=1")
        assert rig.gw.ingestor.sweep_acks() == ["ACK_DELETE:0001\n"]
        # The gate *returns* the line; the gateway is what puts it on the wire
        # (`Gateway.upkeep`), so the test does that step explicitly — and then the
        # stand-in hub really does unlink the file, which closes the loop with the
        # firmware's `ACK_DELETE` handler.
        rig.wire.write("ACK_DELETE:0001\n")
        rig.drain()
        assert 1 in rig.hub.acked and rig.hub.card_rows(1) == 0
    finally:
        rig.close()


def test_a_gateway_that_owns_the_line_hand_shakes_at_start():
    """Both deployments must ask, and they ask from different places.

    The edge server's reader opens the port and then hands the gateway a writer
    (`attach_writer`); a gateway that owns the line itself has no such call — it
    comes up inside `start()`. Wiring only the first meant the *standalone*
    gateway talked forever without ever asking the hub what it had been holding
    while the laptop was away, which is the exact case §5.7 exists for.
    """
    td = tempfile.TemporaryDirectory()
    try:
        gw = Gateway(db_path=Path(td.name) / "bbx.sqlite", sim_rows_per_chunk=40,
                     sim_rows_per_sec=10_000.0)
        gw.start()
        try:
            assert gw.reconciler.handshake.asks == 1, "no STATE? on start"
            assert "STATE?" in gw.source.sent, gw.source.sent[:3]
        finally:
            gw.stop()
    finally:
        td.cleanup()


def test_the_skip_lever_makes_a_real_gap():
    """The bench lever, tested as a lever.

    `Gateway(sim_skip="1:12")` is how a gap is produced on purpose, and it is the
    switch every recovery demo and every failure drill stands on. It was silently
    inert for one revision — armed three lines above the `self.log` it needed, so the
    `AttributeError` was swallowed by the caller's `except` and the gateway came up
    looking perfectly healthy with no gap in it. A lever that does nothing is worse
    than no lever: it makes a working system look broken and a broken one look fine.
    """
    td = tempfile.TemporaryDirectory()
    try:
        gw = Gateway(db_path=Path(td.name) / "bbx.sqlite", sim_rows_per_chunk=40,
                     sim_rows_per_sec=10_000.0, sim_skip="1:12")
        assert gw.sim_skip == "1:12"
        it = gw.source.hub
        for _ in range(400):
            line = it.poll()
            if line is None:
                break
            gw.feed(line)
        c1 = gw.store.chunk(1)
        assert (c1["received_rows"], c1["expected_rows"]) == (28, 40)
        assert it.card_rows(1) == 40, "the card kept the twelve the link lost"
        assert [g.missing for g in gw.reconciler.gaps()] == [12]
    finally:
        td.cleanup()


def test_the_skip_lever_is_ignored_on_a_real_line():
    """A serial port has no `skip_rows`, and that has to be a no-op, not a crash."""
    td = tempfile.TemporaryDirectory()
    try:
        gw = Gateway(db_path=Path(td.name) / "bbx.sqlite", external_feed=True,
                     sim_skip="1:5")
        assert gw.sim_skip is None, "the lever must not arm against a live hub"
    finally:
        td.cleanup()
