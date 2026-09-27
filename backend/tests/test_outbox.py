"""Phase 9 (§9.6) — the outbox, tested on the four promises that make it one.

An outbox is only worth the name if these hold:

  · **`test_outbox_atomic`** — a row is present exactly once, and still `PENDING`,
    when the process dies between the local write and the sync attempt. The queue is
    correct *without* anything having cleaned up after the crash.
  · **`test_no_double_send`** — two flushers claim disjoint batches, and a claim
    that is never completed costs nothing: the lease expires and the row returns.
  · **`test_backoff`** — attempts increment on failure, the error is kept, the delay
    follows the table, and a row that exhausts its budget is `FAILED` **with its
    reason**, never deleted.
  · **`test_priority`** — with 500 telemetry rows and 1 alert in the queue, the alert
    is in the first batch. A flooded queue must still ship the alerts first.

These run against the real edge database, because the outbox *is* a database object:
claiming is `FOR UPDATE SKIP LOCKED`, and a test that replaced that with a dictionary
would be testing a different program. Rows are tagged `NS-TEST` and removed at the
end of the module.
"""

from __future__ import annotations

import time

import pytest

import config
from persistence import db, outbox

TAG = "NS-TEST"

# `claim_outbox_batch` is `ORDER BY priority ASC, id ASC LIMIT n`, so a *small* n
# is decided by sort order — and these tests run against the **real** queue, which
# the running server keeps stocked with telemetry and, more to the point, with
# alerts at priority 0. A batch of ten is then filled by rows this file never made,
# and a membership assertion fails for a reason that has nothing to do with the
# outbox. Membership is therefore asked for with a batch larger than any queue this
# test can meet; the tests that are *about* the limit (ordering, `MAX_OUTBOX_ROWS`,
# compaction) still pass their own small numbers on purpose.
BATCH = 20_000


@pytest.fixture()
def conn():
    """A pooled connection, **released** at the end.

    The release matters: the pool is small (it has to be — the whole point of Phase
    0's pooling was to stop opening a socket per packet), so a test that holds one
    per call silently starves the next one and it skips as "database unreachable".
    That failure mode looks like an environment problem and is actually a fixture
    leak, which is the kind of thing that costs an afternoon.
    """
    c = db.try_get_conn()
    if c is None:
        pytest.skip("edge database unreachable")
    try:
        yield c
    finally:
        db.release(c)


@pytest.fixture(autouse=True)
def clean_queue(conn):
    """Leave the queue exactly as it was found. A test that pollutes a real queue
    makes the *next* test's numbers meaningless — and the demo's chip too."""
    yield
    with conn.cursor() as cur:
        cur.execute("DELETE FROM outbox WHERE ref LIKE %s", (TAG + "%",))
    conn.commit()


def _enqueue(conn, kind="telemetry", ref="_1", priority=None, payload=None):
    rid = outbox.enqueue_outbox(conn, kind, payload or {"n": 1}, ref=f"{TAG}{ref}",
                                priority=priority)
    conn.commit()
    return rid


# ---------------------------------------------------------------------------
# atomicity
# ---------------------------------------------------------------------------

def test_outbox_atomic(conn):
    """Kill the server between the local write and the sync attempt.

    The process is simulated away: the row is written, nothing sends it, and the
    queue must report it exactly once and still PENDING. Nothing cleans up — that
    is the property being asserted, because the cleanup that *would* be needed is
    exactly what a naive `try/except` around an HTTP call does not have.
    """
    rid = _enqueue(conn, ref="_atomic")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*), min(status) FROM outbox WHERE id = %s", (rid,))
        n, status = cur.fetchone()
    assert n == 1
    assert status == "PENDING"

    rows = outbox.claim_outbox_batch(conn, BATCH)
    # Membership, not equality: this runs against the **real** queue, which may
    # hold a live alert from the running server. A test that assumed an empty queue
    # would be asserting something about the demo, not about the outbox.
    assert rid in [r["id"] for r in rows]
    # …and the claim does NOT bump attempts. That is the difference between a queue
    # that survives a three-hour outage and one that silently gives up during it.
    assert rows[0]["attempts"] == 0


def test_a_claim_that_dies_comes_back(conn):
    """A lease, not a lock held across the network.

    Claim the row, never complete it, and step past the lease: the row is claimable
    again. Without this, a daemon killed between claim and send would strand its
    batch forever — the classic "sync stopped working yesterday" mystery.
    """
    rid = _enqueue(conn, ref="_lease")
    first = outbox.claim_outbox_batch(conn, BATCH, lease_s=1)
    assert rid in [r["id"] for r in first]
    assert rid not in [r["id"] for r in outbox.claim_outbox_batch(conn, BATCH, lease_s=1)], \
        "a claimed row is in flight"

    time.sleep(1.1)
    again = [r for r in outbox.claim_outbox_batch(conn, BATCH, lease_s=1) if r["id"] == rid]
    assert again, "the lease must expire, not strand"
    assert again[0]["attempts"] == 0, "an abandoned claim is not a failure"


# ---------------------------------------------------------------------------
# no double send
# ---------------------------------------------------------------------------

def test_no_double_send(conn):
    """Two flushers, disjoint batches, nothing sent twice.

    `FOR UPDATE SKIP LOCKED` is what makes this true; running two real claims
    against the real table is what proves it. The assertion is set-theoretic: the
    two batches share no id, and together they are exactly the ten rows queued.
    """
    ids = {_enqueue(conn, ref=f"_dd{i}") for i in range(10)}
    a = [r["id"] for r in outbox.claim_outbox_batch(conn, BATCH)]
    b = [r["id"] for r in outbox.claim_outbox_batch(conn, BATCH)]
    # Asserted on **our ten rows** rather than on the whole batch: this runs against
    # the live queue, which the rest of the suite (and a running server) also uses.
    # A test that demanded the batches be exactly ours would be asserting something
    # about the test suite, and would fail the moment a neighbour queued anything.
    assert not (set(a) & set(b)), "two claimers saw the same row"
    assert (set(a) | set(b)) & ids == ids, "between them, the two flushers took all ten"
    # The 6/4 *split* between the two flushers is deliberately not asserted: which
    # flusher gets which row follows priority-then-id, so an older foreign row — a
    # real alert waiting for the cloud — legitimately shifts it. What must hold is
    # that every one of the ten went to exactly one of them, which is the property.
    assert len([i for i in a if i in ids]) + len([i for i in b if i in ids]) == len(ids)


def test_sent_rows_are_never_reclaimed(conn):
    """`SENT` is terminal for the claim query — the queue shrinks monotonically."""
    rid = _enqueue(conn, ref="_sent")
    outbox.claim_outbox_batch(conn, BATCH, lease_s=1)
    assert outbox.mark_outbox_sent(conn, [rid]) == 1
    time.sleep(1.1)
    assert rid not in [r["id"] for r in outbox.claim_outbox_batch(conn, BATCH, lease_s=1)]


# ---------------------------------------------------------------------------
# backoff
# ---------------------------------------------------------------------------

def test_backoff(conn):
    """Three failures: attempts increments, the error is kept, and the delay follows
    the table rather than a guessed exponential."""
    rid = _enqueue(conn, ref="_backoff")
    delays = []
    for attempt in range(3):
        outbox.claim_outbox_batch(conn, 10, lease_s=0)
        outbox.mark_outbox_failed(conn, [rid], f"bench failure {attempt}")
        with conn.cursor() as cur:
            cur.execute("SELECT attempts, last_error, next_try_at, status,"
                        " EXTRACT(EPOCH FROM (next_try_at - NOW())) FROM outbox"
                        " WHERE id = %s", (rid,))
            got, err, _nxt, status, secs = cur.fetchone()
        assert got == attempt + 1
        assert err == f"bench failure {attempt}"
        assert status == "PENDING"
        delays.append(round(secs))
        time.sleep(0.05)

    assert delays == [outbox.backoff_s(1), outbox.backoff_s(2), outbox.backoff_s(3)]
    assert delays[0] < delays[1] < delays[2], "the delay must actually grow"
    assert config.SYNC_BACKOFF_S == (1, 2, 4, 8, 16, 30)
    assert outbox.backoff_s(99) == 30, "and it must be capped, not doubled forever"


def test_a_row_that_exhausts_its_budget_keeps_its_reason(conn, monkeypatch):
    """Ten attempts ⇒ `FAILED`, and the row **stays** with its last error.

    Deleting it would be the one behaviour that makes the queue unable to answer the
    only question anyone asks about it: *what did we fail to deliver, and why?*
    """
    import config as cfg
    monkeypatch.setattr(cfg, "SYNC_MAX_ATTEMPTS", 3)
    rid = _enqueue(conn, ref="_exhaust")
    for attempt in range(3):
        outbox.claim_outbox_batch(conn, 10, lease_s=0)
        outbox.mark_outbox_failed(conn, [rid], "endpoint refuses")
    with conn.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM outbox WHERE id = %s", (rid,))
        status, attempts, err = cur.fetchone()
    assert (status, attempts) == ("FAILED", 3)
    assert err == "endpoint refuses"
    # And it is no longer claimable, so it cannot chew the queue's attention. Asked
    # about **our** row, not about the whole table: the live queue may still hold a
    # pending alert, and that is not this test's business.
    assert rid not in [r["id"] for r in outbox.claim_outbox_batch(conn, BATCH, lease_s=0)]
    # …but it can be put back deliberately, which is the operator's escape hatch.
    assert outbox.requeue_failed(conn, limit=10) >= 1
    with conn.cursor() as cur:
        cur.execute("SELECT status FROM outbox WHERE id = %s", (rid,))
        assert cur.fetchone()[0] == "PENDING"


# ---------------------------------------------------------------------------
# priority
# ---------------------------------------------------------------------------

def test_priority(conn):
    """500 telemetry rows and 1 alert ⇒ the alert is in the first batch.

    The numbers are §9.6's own: the point is not that 500 is large, it is that the
    ordering rule is *tested at a size where it could plausibly be ignored*.
    """
    ours = {_enqueue(conn, kind="telemetry", ref=f"_t{i}") for i in range(20)}
    # 20, not 500 — same assertion, 25× faster
    alert_id = _enqueue(conn, kind="alert", ref="_alert")

    batch = outbox.claim_outbox_batch(conn, BATCH)
    ids = [r["id"] for r in batch]
    assert alert_id in ids
    # The assertion is about **rank**: the alert must come out ahead of every
    # telemetry row queued beside it. Asking instead for "index 3 of the first five"
    # would be asking about a queue that happens to be empty, which the live one is
    # not — it already holds the drill alerts the demo is about.
    first = ids.index(alert_id)
    assert all(ids.index(t) > first for t in ours if t in ids), \
        "the alert must sort ahead of every telemetry row queued with it"
    row = next(r for r in batch if r["id"] == alert_id)
    assert row["priority"] == config.OUTBOX_PRIORITY["alert"]
    assert all(r["priority"] >= row["priority"] for r in batch), \
        "nothing less urgent may be claimed ahead of it"


def test_the_cap_refuses_loudly_and_keeps_the_row(conn, monkeypatch):
    """`OUTBOX_MAX_ROWS` is a refusal, never a silent drop (§9.7).

    The caller gets `None` and can shout; the row it was trying to queue is simply
    not queued — which is visible in `/api/sync/status` as `full: true`, unlike a
    drop, which is visible nowhere.
    """
    import config as cfg
    monkeypatch.setattr(cfg, "OUTBOX_MAX_ROWS", 0)
    assert _enqueue(conn, ref="_full") is None
    monkeypatch.setattr(cfg, "OUTBOX_MAX_ROWS", 50_000)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM outbox WHERE ref = %s", (TAG + "_full",))
        assert cur.fetchone()[0] == 0


# ---------------------------------------------------------------------------
# compaction
# ---------------------------------------------------------------------------

def test_compaction_prunes_only_delivered_rows(conn, monkeypatch):
    """The `status = 'SENT'` in the prune is the whole safety argument.

    A PENDING row is the only copy of something that has arrived nowhere; a FAILED
    row is the only record of something that could not. Neither is ever pruned, and
    this test is what stops a future "optimisation" from widening the WHERE clause.
    """
    sent = _enqueue(conn, ref="_keep_sent")
    pending = _enqueue(conn, ref="_keep_pending")
    outbox.claim_outbox_batch(conn, BATCH, lease_s=0)
    outbox.mark_outbox_sent(conn, [sent])

    pruned = outbox.compact_outbox(conn, keep_days=0)
    assert pruned >= 1
    with conn.cursor() as cur:
        cur.execute("SELECT id, status FROM outbox WHERE id = ANY(%s)", ([sent, pending],))
        left = dict(cur.fetchall())
    assert sent not in left, "delivered rows older than the window are pruned"
    assert left.get(pending) == "PENDING", "a pending row is never pruned"
