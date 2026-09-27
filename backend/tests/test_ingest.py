"""Ingest tests — the three invariants, end to end over a simulated hub."""
import sys
from pathlib import Path

for _p in (Path(__file__).resolve().parent.parent,          # backend/
           Path(__file__).resolve().parent.parent / "blackbox"):  # the gateway modules
    sys.path.insert(0, str(_p))

import protocol as P
from ingest import Ingestor
from sim.hub_sim import stream
from store import Store

CHUNKS, ROWS_PER_NODE = 3, 40
ROWS_PER_CHUNK = ROWS_PER_NODE * 4          # 4 nodes


def fresh():
    store = Store(":memory:")
    ing = Ingestor(store, log=lambda *_: None)
    return store, ing


AGED = "2000-01-01T00:00:00.000+00:00"


def age_chunks(store, chunk_id=None):
    """Move closed_at far into the past so the 60 s safety margin is satisfied."""
    if chunk_id is None:
        store.conn.execute("UPDATE blackbox_chunks SET closed_at=?", (AGED,))
    else:
        store.conn.execute("UPDATE blackbox_chunks SET closed_at=? WHERE chunk_id=?",
                           (AGED, chunk_id))


def mark_synced(store, chunk_id=None):
    if chunk_id is None:
        store.conn.execute("UPDATE cloud_outbox SET status='SENT'")
    else:
        store.conn.execute("UPDATE cloud_outbox SET status='SENT' WHERE chunk_id=?", (chunk_id,))


# ------------------------------------------------------------------ ingest
def test_ingest_lands_every_row():
    store, ing = fresh()
    stats = ing.run(stream(chunks=CHUNKS, rows_per_chunk=ROWS_PER_NODE))
    assert stats.unknown == 0, stats.last_unknown
    assert stats.rows == CHUNKS * ROWS_PER_CHUNK
    assert store.count_telemetry() == CHUNKS * ROWS_PER_CHUNK
    assert store.count_outbox() == CHUNKS * ROWS_PER_CHUNK
    assert store.pending_total() == CHUNKS * ROWS_PER_CHUNK
    assert store.hub()["blackbox_state"] == "OK"


def test_chunk_registry_closes_correctly():
    store, ing = fresh()
    ing.run(stream(chunks=CHUNKS, rows_per_chunk=ROWS_PER_NODE))
    chunks = store.chunks()
    assert len(chunks) == CHUNKS
    for c in chunks:
        assert c["state"] == "CLOSED"
        assert c["expected_rows"] == ROWS_PER_CHUNK
        assert c["received_rows"] == ROWS_PER_CHUNK, "distinct-row count must match the hub"
        assert c["dropped_rows"] == 0


# --------------------------------------------------------------- invariant 1
def test_replay_is_idempotent():
    """The zero-loss promise: replaying a chunk must add nothing and re-queue nothing."""
    store, ing = fresh()
    lines = list(stream(chunks=2, rows_per_chunk=10))
    ing.run(lines)
    before = (store.count_telemetry(), store.count_outbox(), store.pending_total())
    ing.run(lines)                       # the hub re-sends everything
    ing.run(lines)                       # and again
    after = (store.count_telemetry(), store.count_outbox(), store.pending_total())
    assert before == after, f"replay changed state: {before} -> {after}"
    assert ing.stats.duplicates == 2 * 2 * 10 * 4
    for c in store.chunks():
        assert c["received_rows"] == 40, "replay must not inflate the row count"


# --------------------------------------------------------------- invariant 2
def test_nothing_is_deletable_while_the_cloud_is_behind():
    store, ing = fresh()
    ing.run(stream(chunks=CHUNKS, rows_per_chunk=ROWS_PER_NODE))
    assert ing.sweep_acks() == []
    reasons = ing.sweep_reasons()
    assert len(reasons) == CHUNKS
    assert all("not yet confirmed" in r for r in reasons.values())


def test_only_the_fully_synced_chunk_is_released():
    store, ing = fresh()
    ing.run(stream(chunks=CHUNKS, rows_per_chunk=ROWS_PER_NODE))
    mark_synced(store, 2)
    age_chunks(store, 2)
    cmds = ing.sweep_acks()
    assert cmds == ["ACK_DELETE:0002\n"], cmds
    assert ing.sweep_acks() == [], "an acknowledged chunk must not be acknowledged twice"
    assert store.chunk(2)["ack_sent_at"]


def test_rows_lost_in_transit_block_the_delete():
    """The realistic failure: lines never reached the laptop. The hub still
    reports 160 in CHUNK_CLOSE. This is the case the addendum's single-condition
    gate would have deleted."""
    store, ing = fresh()
    lines = list(stream(chunks=1, rows_per_chunk=ROWS_PER_NODE))
    telemetry = [l for l in lines if l.startswith("{")]
    keep = set(range(len(telemetry))) - set(range(5, 17))     # 12 lines vanish
    poisoned = [l for i, l in enumerate(lines)
                if not l.startswith("{") or (telemetry.index(l) in keep)]
    ing.run(poisoned)
    mark_synced(store)
    age_chunks(store)
    assert ing.sweep_acks() == []
    assert "12 rows would be lost" in ing.sweep_reasons()[1]
    assert store.chunk(1)["expected_rows"] == ROWS_PER_CHUNK
    assert store.count_telemetry() == ROWS_PER_CHUNK - 12


def test_the_mismatch_case_never_deletes():
    """Rows vanish from the table itself, not from the wire. Our own counter and
    the table disagree, so the gate refuses even though the cloud is caught up.
    Neither the addendum's gate nor a single-counter check would have caught this."""
    store, ing = fresh()
    ing.run(stream(chunks=1, rows_per_chunk=ROWS_PER_NODE))
    store.conn.execute("DELETE FROM sensor_telemetry WHERE chunk_id=1 AND seq=1")
    mark_synced(store)
    age_chunks(store)
    assert ing.sweep_acks() == []
    reason = ing.sweep_reasons()[1]
    assert "internal inconsistency" in reason and "1" in reason, reason
    assert store.count_telemetry() == ROWS_PER_CHUNK - 1


def test_safety_margin_alone_holds_a_finished_chunk():
    store, ing = fresh()
    ing.run(stream(chunks=1, rows_per_chunk=10))
    mark_synced(store)
    assert ing.sweep_acks() == []                       # closed seconds ago
    assert "safety margin" in ing.sweep_reasons()[1]
    age_chunks(store)
    assert ing.sweep_acks() == ["ACK_DELETE:0001\n"]


def test_dropped_chunk_is_never_deleted_even_after_sync():
    store, ing = fresh()
    ing.run(stream(chunks=1, rows_per_chunk=10, drop_in_chunk=1, drop_count=3))
    mark_synced(store)
    age_chunks(store)
    assert ing.sweep_acks() == []
    assert "only record of them" in ing.sweep_reasons()[1]
    assert store.chunk(1)["dropped_rows"] == 3
    assert store.hub()["dropped_total"] == 3


# --------------------------------------------------------------- invariant 3
def test_garbage_on_the_wire_does_not_stop_ingest():
    store, ing = fresh()
    lines = list(stream(chunks=1, rows_per_chunk=5))
    poisoned = lines[:5] + ["", "{broken", "WAT:1", "CHUNK_CLOSE:"] + lines[5:]
    ing.run(poisoned)
    assert ing.stats.unknown == 4
    assert store.count_telemetry() == 20
    assert store.chunk(1)["expected_rows"] == 20


def test_chunk_state_reconciles_after_a_laptop_restart():
    """Laptop wiped, hub re-announces its open chunk. We must not invent a
    'closed and complete' state from a bare STATE line."""
    store, ing = fresh()
    ing.handle(P.ChunkState(chunk_id=42, rows=450, dropped=0, closed=False))
    assert store.chunk(42)["state"] == "OPEN"
    assert store.chunk(42)["expected_rows"] is None
    ing.handle(P.ChunkState(chunk_id=42, rows=450, dropped=0, closed=True))
    c = store.chunk(42)
    assert c["state"] == "CLOSED" and c["expected_rows"] == 450
    assert store.count_pending(42) == 0


def test_drop_total_accumulates_on_the_hub_status_row():
    store, ing = fresh()
    ing.handle(P.DropNotice(chunk_id=1, count=2))
    ing.handle(P.DropNotice(chunk_id=2, count=5))
    assert store.hub()["dropped_total"] == 7
    assert store.chunk(1)["dropped_rows"] == 2
    assert store.chunk(2)["dropped_rows"] == 5


def test_unknown_chunk_close_still_creates_the_record():
    store, ing = fresh()
    ing.handle(P.ChunkClose(chunk_id=99, rows=1000, dropped=0))
    c = store.chunk(99)
    assert c["state"] == "CLOSED" and c["expected_rows"] == 1000
    assert c["received_rows"] == 0            # and the gate will hold it
    assert ing.sweep_acks() == []


# ── Phase 10 acceptance: the beats, at the numbers the Plan states ───────────
def test_three_chunks_of_four_nodes_is_the_four_eighty_row_beat():
    """The acceptance sentence, counted: *480 rows over a 3-chunk simulated stream
    → 480 telemetry rows, 480 outbox rows, 0 unknown.*"""
    store, ing = fresh()
    stats = ing.run(stream(chunks=3, rows_per_chunk=40))
    assert (stats.unknown, stats.duplicates) == (0, 0), stats.last_unknown
    assert store.count_telemetry() == 480
    assert store.count_outbox() == 480
    assert store.pending_total() == 480
    for cid in (1, 2, 3):
        c = store.chunk(cid)
        assert (c["expected_rows"], c["received_rows"], c["dropped_rows"]) == (160, 160, 0)


def test_replaying_the_stream_twice_leaves_every_counter_alone():
    """Not just the row count: *nothing* may move — no new telemetry, no new
    outbox rows, no new pending, and every chunk still closed at 160."""
    store, ing = fresh()
    lines = list(stream(chunks=3, rows_per_chunk=40))
    ing.run(lines)
    snap = lambda: (store.count_telemetry(), store.count_outbox(), store.pending_total(),
                    store.count_telemetry(2), store.chunk(2)["received_rows"],
                    store.chunk(2)["closed_at"])
    before = snap()
    ing.run(lines)
    ing.run(lines)
    assert snap() == before
    assert ing.stats.duplicates == 2 * 480


# ── the hub guard: NOSD means nothing is ever released ──────────────────────
def _stream_without_the_ok_line(**kw):
    """A stream from a hub that has no card. Rows still flow — a missing SD card
    stops *archiving*, not telemetry — which is exactly why the guard has to be
    explicit rather than inferred from the absence of data."""
    lines = [l for l in stream(**kw) if not l.startswith("BLACKBOX:")]
    return ["BLACKBOX:NOSD"] + lines


def _releasable(store, ing, chunk_id):
    """Everything the five conditions need, so only the hub guard can refuse."""
    mark_synced(store, chunk_id)
    age_chunks(store, chunk_id)


def test_nosd_emits_no_ack_and_marks_nothing():
    store, ing = fresh()
    ing.run(_stream_without_the_ok_line(chunks=2, rows_per_chunk=10))   # 80 rows
    _releasable(store, ing, 2)
    assert store.hub()["blackbox_state"] == "NOSD"
    assert ing.sweep_acks() == []
    assert store.chunk(2)["ack_sent_at"] is None, "withheld, not acknowledged"
    assert "NOSD" in ing.sweep_reasons()[2]
    assert ing.sweep_acks() == []          # and again, and again


def test_when_the_card_comes_back_the_same_chunk_is_released():
    """The reason the guard must *withhold* rather than mark.

    If `sweep_acks` had marked the chunk acknowledged while the card was out, the
    gate would refuse it forever ("already acknowledged"), the file would never be
    unlinked, and the chunk would never be offered again — the card fills up while
    the truth store insists the chunk is gone.
    """
    store, ing = fresh()
    ing.run(_stream_without_the_ok_line(chunks=2, rows_per_chunk=10))
    _releasable(store, ing, 2)
    assert ing.sweep_acks() == []

    ing.handle("BLACKBOX:OK,3712")          # the card is pushed back in
    assert ing.sweep_acks() == ["ACK_DELETE:0002\n"]
    assert store.chunk(2)["ack_sent_at"]


def test_a_silent_hub_is_not_asked_to_delete_anything():
    store, ing = fresh()
    ing.run(stream(chunks=1, rows_per_chunk=10))
    _releasable(store, ing, 1)
    store.conn.execute("UPDATE hub_status SET last_seen=? WHERE id=1",
                       ("2020-01-01T00:00:00.000+00:00",))
    assert ing.sweep_acks() == []
    assert store.chunk(1)["ack_sent_at"] is None
    assert "silent" in ing.sweep_reasons()[1]


def test_nothing_is_released_before_the_hub_has_ever_reported():
    """A store with no hub attached. The gate would happily release an aged,
    synced chunk — but there is no card to delete from and no hub to answer."""
    store = Store(":memory:")
    ing = Ingestor(store, log=lambda *_: None)
    ing.handle(P.ChunkOpen(chunk_id=5))
    for seq in range(1, 21):
        ing.handle(P.TelemetryRow(chunk_id=5, seq=seq, ts_ms=seq * 125, node=1, level=1,
                                  tilt=0.1, disp_mm=5.0, accel=0.01,
                                  vib=0, micro=0.0, macro=1.0, flags=0))
    ing.handle(P.ChunkClose(chunk_id=5, rows=20, dropped=0))
    _releasable(store, ing, 5)
    assert store.count_pending(5) == 0 and store.count_telemetry(5) == 20
    # Rows have arrived, so the store *has* seen a hub — but it has never said a
    # word about its card. Not knowing is a refusal: conditions being met is not
    # the same as the delete being safe.
    ok, why = ing.hub_delete_ok()
    assert not ok and "UNKNOWN" in why, why
    assert ing.sweep_acks() == []
    assert store.chunk(5)["ack_sent_at"] is None

    # …and the same for the branch below it, which is only reachable if a boot
    # ever wrote OK without a timestamp: crafted, because a real hub writes both
    # in one statement.
    store.conn.execute("UPDATE hub_status SET blackbox_state='OK', last_seen=NULL WHERE id=1")
    ok, why = ing.hub_delete_ok()
    assert not ok and "not reported" in why, why


# ── reconciliation may never un-close a chunk ───────────────────────────────
def test_chunk_state_cannot_un_close_a_chunk():
    """§5.7: counts are monotonic. A stale `CHUNK_STATE` — the hub rebooted and is
    describing its *open* chunk while we hold the closed one — must not reopen it,
    lower its expected count, or erase its rows. It may only add drops."""
    store, ing = fresh()
    ing.run(stream(chunks=1, rows_per_chunk=10))          # 40 rows, closed at 40
    assert store.chunk(1)["state"] == "CLOSED"

    ing.handle(P.ChunkState(chunk_id=1, rows=10, dropped=0, closed=False))
    c = store.chunk(1)
    assert c["state"] == "CLOSED", "a stale reconciliation must not un-close"
    assert c["expected_rows"] == 40 and c["received_rows"] == 40
    assert c["closed_at"] is not None
    assert c["dropped_rows"] == 0

    ing.handle(P.ChunkState(chunk_id=1, rows=7, dropped=3, closed=True))
    c = store.chunk(1)
    assert c["state"] == "CLOSED"
    assert c["expected_rows"] == 40, "a lower count must not overwrite the higher one"
    assert c["dropped_rows"] == 3, "drops are the one thing that may rise"
    assert ing.sweep_acks() == [], "and a chunk that lost rows is never released"


def test_a_second_close_with_a_lower_count_does_not_rewrite_the_first():
    store, ing = fresh()
    ing.handle(P.ChunkClose(chunk_id=8, rows=160, dropped=0))
    ing.handle(P.ChunkClose(chunk_id=8, rows=148, dropped=0))
    assert store.chunk(8)["expected_rows"] == 148, (
        "the hub's latest word is the hub's latest word — but the *rows* are "
        "already counted, so the gate sees mismatch and holds the file")


# ── REPLAY: the rows that never arrived, back-filled from the card ──────────
def test_a_replay_backfills_the_rows_that_never_arrived():
    """§5.7's demo, as a unit test.

    Twelve lines are lost on the USB link. The hub's card has them. The laptop
    sends REPLAY, the hub re-streams them, they arrive marked `replay`, and the
    chunk goes from un-deletable to released — without a single row being counted
    twice and without the live rows being touched.
    """
    store, ing = fresh()
    lines = list(stream(chunks=1, rows_per_chunk=40))       # 160 rows
    rows = [l for l in lines if l.startswith("{")]
    control = [l for l in lines if not l.startswith("{")]

    lost = rows[70:82]                                     # 12 rows, mid-chunk
    ing.run([l for l in rows if l not in lost] + control)
    assert store.count_telemetry() == 148
    assert store.chunk(1)["received_rows"] == 148
    _releasable(store, ing, 1)
    reasons = ing.sweep_reasons()
    assert "12 rows would be lost" in reasons[1], reasons

    # the card's copy arrives over the same ingest path, and says where it came from
    replayed = [l.replace('{"chunk_id"', '{"source":"replay","chunk_id"') for l in lost]
    ing.run(replayed)

    assert store.count_telemetry() == 160
    assert store.chunk(1)["received_rows"] == 160
    n_replay = store.conn.execute(
        "SELECT COUNT(*) FROM sensor_telemetry WHERE source='replay'").fetchone()[0]
    assert n_replay == 12, "the back-filled rows are labelled, not disguised as live"

    ing.run(replayed)                                      # REPLAY is idempotent too
    assert store.count_telemetry() == 160

    # Back-filled rows are new *rows*, so they are new outbox work: the file is
    # still held until the cloud has confirmed them too. Recovery restores the
    # data; it does not short-circuit the one condition that protects it.
    assert "not yet confirmed" in ing.sweep_reasons()[1]
    mark_synced(store, 1)
    assert ing.sweep_acks() == ["ACK_DELETE:0001\n"], "and now it may be released"


def test_a_reused_live_stamp_is_stored_as_a_new_chunk():
    """A restarted hub repeats chunk and seq. Same clock stays a duplicate.
    A new clock is a new reading and must be queued for the cloud."""
    store, ing = fresh()
    row = P.TelemetryRow(chunk_id=1, seq=4, ts_ms=1000, node=2, level=1,
                         tilt=0.1, disp_mm=0.0, accel=0.2, vib=0, micro=0.0, macro=0.0)
    ing.handle(row)
    ing.handle(row)
    assert store.count_telemetry() == 1
    assert store.pending_total() == 1
    ing.handle(replace_ts(row, 5000))
    assert store.count_telemetry() == 2
    assert store.pending_total() == 2
    ids = [c["chunk_id"] for c in store.chunks()]
    assert 1 in ids and max(ids) != 1


def replace_ts(row, ts):
    from dataclasses import replace
    return replace(row, ts_ms=ts)
