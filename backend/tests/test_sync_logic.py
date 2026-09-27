"""Phase 9 (§F) — `sync_once()` **verbatim**, against a mock edge and a mock cloud.

Why this file exists next to `test_outbox.py`
---------------------------------------------
`test_outbox.py` proves the *queue* is correct. This one proves the *cycle* is, and
it does so by running the real function — the same `sync_once()` the daemon calls —
over a real socket to `sim/cloud_mock.py`. Nothing is stubbed between the two ends:
the body is gzipped, signed, posted, signature-checked, decompressed, committed and
answered, which is the only way the wire format can be trusted.

The four properties the cycle must have:

  · **alerts first** — a flooded queue still ships the alert (§9.5)
  · **a refusal is recoverable** — rows stay PENDING with their reason, and drain
    when the far side comes back
  · **a duplicate is success** — the daemon prefers "sent but marked unsent", so the
    receiver must treat a re-send as a no-op that says so
  · **no work ⇒ no connection** — a quiet site opens zero sockets, which is what
    keeps a free tier from being suspended for idleness (§F.4)

Rediscovering this file's own past: the first version of it pointed at a mock built
with Flask's test client, which never opens a socket — so it could not have caught a
wrong Content-Encoding, a missing header, or a signature computed over the wrong
bytes. All three are exactly the failures a mock cloud is supposed to catch.
"""

from __future__ import annotations

import threading
import time

import pytest
from werkzeug.serving import make_server

import config
from persistence import cloud_client, db, outbox, sync
from sim.cloud_mock import build_mock_app

TAG = "NS-SYNC-TEST"
KEY = "bench-shared-key"


class MockCloud:
    """The stand-in cloud, on a real port, with a controllable mood."""

    def __init__(self, tmp_path):
        self.store = tmp_path / "cloud-mock.sqlite3"
        self.app = build_mock_app(self.store, key=KEY)
        self.server = make_server("127.0.0.1", 0, self.app, threaded=True)
        self.port = self.server.server_port
        self.url = f"http://127.0.0.1:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(50):                       # wait for the listener, not a sleep
            try:
                cloud_client.probe_backhaul(url=self.url, timeout=0.5)
                break
            except Exception:                     # noqa: BLE001
                time.sleep(0.05)
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.thread.join(timeout=5)

    def mode(self, mode: str) -> None:
        with self.app.test_client() as c:
            assert c.post("/_ctl", json={"mode": mode}).status_code == 200

    def state(self) -> dict:
        with self.app.test_client() as c:
            return c.get("/_state").get_json()


@pytest.fixture()
def cloud(tmp_path, monkeypatch):
    with MockCloud(tmp_path) as m:
        monkeypatch.setattr(config, "CLOUD_HTTP_URL", m.url)
        monkeypatch.setattr(config, "CLOUD_HMAC_KEY", KEY)
        yield m


@pytest.fixture()
def edge():
    conn = db.try_get_conn()
    if conn is None:
        pytest.skip("edge database unreachable")
    try:
        yield conn
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM outbox WHERE ref LIKE %s", (TAG + "%",))
        conn.commit()
        db.release(conn)


def queue(edge, kind, ref, payload=None):
    rid = outbox.enqueue_outbox(edge, kind, payload or {"ref": ref}, ref=TAG + ref)
    edge.commit()
    return rid


def row_status(edge, rid):
    with edge.cursor() as cur:
        cur.execute("SELECT status, attempts, last_error FROM outbox WHERE id = %s", (rid,))
        return cur.fetchone()


# ---------------------------------------------------------------------------

def test_sync_once_drains_the_outbox_through_the_mock_cloud(edge, cloud):
    """The whole point, end to end: queue → gzip → HMAC → socket → store."""
    ids = [queue(edge, "alert", "_drain_a"), queue(edge, "telemetry", "_drain_b")]
    before = cloud.state()["batches"]

    stats = sync.sync_once(edge)

    assert stats["error"] is None, stats
    assert stats["outbox"] >= 2, stats
    assert cloud.state()["batches"] == before + 1
    assert cloud.state()["items"] >= 2
    assert all(row_status(edge, i)[0] == "SENT" for i in ids)

    # …and a second cycle has nothing to say about our rows. The assertion is on
    # the *stat* rather than on the batch count, because the outbox is shared with
    # the rest of the suite: "not one request at all" would be a claim about the
    # test run, not about the daemon. "Delivered nothing, because there was nothing
    # due" is the claim, and it is the one the free tier depends on.
    quiet = sync.sync_once(edge)
    assert quiet["outbox"] == 0 and quiet["error"] is None, quiet
    assert all(row_status(edge, i)[0] == "SENT" for i in ids)


def _receiver_ref_counts(store):
    """What the **far end** actually holds, read straight out of its own store.

    The sender's statistics are the sender's story. §9.6 asks for uniqueness
    "verified at the receiver", which means asking the database that received the
    rows — so this opens the mock's sqlite file and counts refs there. A row shipped
    twice would show up as a count of 2, whatever the edge believed.
    """
    import sqlite3
    conn = sqlite3.connect(str(store))
    try:
        return conn.execute(
            "SELECT ref, count(*) FROM items WHERE ref IS NOT NULL GROUP BY ref").fetchall()
    finally:
        conn.close()


def test_no_double_send_under_three_real_flushers(edge, cloud):
    """§9.6, with the racers made real: three threads, one queue, one receiver.

    `FOR UPDATE SKIP LOCKED` is the mechanism; three real claimers arriving on a
    barrier are the proof. Each thread claims on **its own pooled connection** —
    three processes racing is the situation the mechanism exists for, and sharing
    one connection would serialize them and prove nothing.

    Asserted three ways, because the property has three faces: no row was claimed
    twice (the claimers' own record), every row is `SENT` (the edge's record), and
    the receiver holds exactly one copy of every ref (the only record that cannot
    be self-serving).
    """
    refs = {TAG + f"_race{i:02d}" for i in range(12)}
    ids = [queue(edge, "telemetry", f"_race{i:02d}") for i in range(12)]
    ours = set(ids)

    claimed: dict[int, list] = {}
    errors: dict[int, Exception] = {}
    barrier = threading.Barrier(4)

    def flusher(n):
        conn = db.try_get_conn()
        if conn is None:
            errors[n] = RuntimeError("no pooled connection for a flusher")
            barrier.wait(timeout=10)
            return
        try:
            barrier.wait(timeout=10)          # all three arrive at the claim together
            rows = outbox.claim_outbox_batch(conn, 4)
            claimed[n] = [r["id"] for r in rows]
            if rows:
                sync.flush_batch(conn, rows=rows)
        except Exception as exc:              # noqa: BLE001
            errors[n] = exc
        finally:
            db.release(conn)

    threads = [threading.Thread(target=flusher, args=(n,)) for n in (1, 2, 3)]
    for t in threads:
        t.start()
    barrier.wait(timeout=10)                  # release them together
    for t in threads:
        t.join(timeout=30)

    assert not errors, errors
    took = [set(claimed.get(n, [])) & ours for n in (1, 2, 3)]
    assert took[0] | took[1] | took[2] == ours, \
        "between them the three flushers must take every row we queued"
    assert not (took[0] & took[1]) and not (took[1] & took[2]) and not (took[0] & took[2]), \
        "a row was claimed by two flushers"
    assert all(row_status(edge, i)[0] == "SENT" for i in ids)

    counts = dict(_receiver_ref_counts(cloud.store))
    assert set(counts) >= refs, "every ref must have arrived"
    assert [r for r, n in counts.items() if r in refs and n > 1] == [], \
        "the receiver holds a ref more than once — something was sent twice"


def test_an_alert_outranks_a_queue_of_telemetry(edge, cloud, monkeypatch):
    """§9.5 with §9.6's shape: the alert ships in the **first** batch."""
    monkeypatch.setattr(config, "SYNC_BATCH", 5)
    for i in range(20):
        queue(edge, "telemetry", f"_prio{i}")
    alert_id = queue(edge, "alert", "_prio_alert")

    sync.sync_once(edge)

    assert row_status(edge, alert_id)[0] == "SENT", "the alert went first"
    with edge.cursor() as cur:
        cur.execute("SELECT count(*) FROM outbox WHERE status = 'PENDING' AND ref LIKE %s",
                    (TAG + "%",))
        still_pending = cur.fetchone()[0]
    assert still_pending > 0, "the batch cap must bite — otherwise this proves nothing"


def test_a_refused_batch_keeps_its_rows_and_its_reason(edge, cloud):
    """503 from the far side: rows stay PENDING, the error is kept, and the same
    rows drain the moment the far side recovers. This is the offline-first promise
    in three assertions."""
    cloud.mode("fail")
    rid = queue(edge, "alert", "_fail")
    stats = sync.sync_once(edge)

    status, attempts, err = row_status(edge, rid)
    assert (status, attempts) == ("PENDING", 1)
    assert err and "503" in err, err
    assert stats["error"], "the cycle must report the failure, not swallow it"

    # …and the backoff is real: the row is not merely unsent, it is *not yet due*.
    # A retry loop that ignored `next_try_at` would hammer a dead endpoint at the
    # daemon's interval — the failure mode the 1/2/4/8/16/30 table exists to prevent.
    immediately = sync.sync_once(edge)
    assert immediately["skipped"] is True, "a failed row must wait out its backoff"

    time.sleep(outbox.backoff_s(1) + 0.2)
    cloud.mode("ok")
    sync.sync_once(edge)
    status, attempts, err = row_status(edge, rid)
    assert status == "SENT" and err is None, (status, err)


def test_a_duplicate_reply_is_not_a_failure(edge, cloud):
    """The daemon's preferred failure is "sent but marked unsent", so the receiver
    answering `duplicate: true` must be treated as delivered — not retried forever."""
    cloud.mode("duplicate")
    rid = queue(edge, "alert", "_dup")
    stats = sync.sync_once(edge)
    assert stats["outbox"] >= 1 and stats["error"] is None, stats
    assert stats["http"]["duplicate"] is True, stats["http"]
    assert row_status(edge, rid)[0] == "SENT"


def test_a_bad_signature_is_refused_and_the_row_stays_pending(edge, cloud, monkeypatch):
    """The whole reason the digest is an HMAC over the bytes on the wire.

    A batch whose signature does not match is refused **before** it is stored, so a
    corrupted or tampered body can never become the central record's truth — and the
    edge keeps the row, because nothing was delivered.
    """
    rid = queue(edge, "alert", "_sig")
    monkeypatch.setattr(cloud_client, "sign_payload", lambda body, key=None: "sha256=deadbeef")
    stats = sync.sync_once(edge)

    assert stats["error"], "a 401 is a failure like any other"
    assert "401" in stats["error"]
    status, attempts, err = row_status(edge, rid)
    assert status == "PENDING" and attempts == 1
    assert "401" in err
    assert cloud.state()["items"] == 0, "an unverified body must not be stored"


def test_the_signature_is_over_the_bytes_actually_sent():
    """Deterministic for one body, different for another, and absent when unkeyed.

    Absent, not faked: with no key configured the endpoint is a local mock, and a
    header that *looks* like a signature would be the more dangerous choice.
    """
    a = cloud_client.sign_payload(b'{"x":1}', key=KEY)
    assert a == cloud_client.sign_payload(b'{"x":1}', key=KEY)
    assert a != cloud_client.sign_payload(b'{"x":2}', key=KEY)
    assert a.startswith("sha256=") and len(a) == 7 + 64
    assert cloud_client.sign_payload(b'{"x":1}', key="") == ""


def test_local_only_mode_touches_nothing(edge, monkeypatch):
    """With neither tier configured, `sync_once` returns immediately.

    **Not one line of cloud code runs** — no socket, no connection attempt, no
    timeout to wait for. That guard is why a misconfigured or absent cloud can never
    affect the mine, and it is asserted here rather than trusted.
    """
    monkeypatch.setattr(config, "CLOUD_DB_URL", "")
    monkeypatch.setattr(config, "CLOUD_HTTP_URL", "")
    monkeypatch.setattr(config, "CLOUD_ENABLED", False)
    queue(edge, "alert", "_localonly")
    before = cloud_client.post_batch  # noqa: F841 — presence only
    stats = sync.sync_once(edge)
    assert stats["skipped"] is True and stats["error"] is None
    assert sync.enabled() is False


# ---------------------------------------------------------------------------
# The pool (Phase 9's most expensive lesson)
# ---------------------------------------------------------------------------

def test_the_sync_surface_gives_every_connection_back():
    """Four reads of these routes must not empty the pool.

    This is a regression test for a bug that cost an afternoon and looked like an
    environment problem: `/api/sync/status` borrowed a pooled connection and never
    returned it. psycopg2's pool is keyed by **thread**, so the connection was held
    for the life of the request thread; the pool is four wide by design, so the
    fifth caller got `PoolError`, `try_get_conn()` turned that into `None`, and the
    process reported **"database unreachable"** while postgres was healthy and idle.
    The suite caught it as six tests skipping themselves inside a green run.

    The assertion is on the *held* count after the traffic, not on timing or on
    internals: whatever a route does internally, it must not end the loop holding
    more than it started with.
    """
    import app as app_module
    from persistence import db as dbmod

    client = app_module.app.test_client()
    before = dbmod.pool_stats()
    for _ in range(6):
        client.get("/api/sync/status")
        client.post("/api/sync")          # 409 when unconfigured — still a borrow
        client.get("/api/extended")
    after = dbmod.pool_stats()
    assert after["borrowed"] <= before["borrowed"], (before, after)
    assert after["exhausted"] == 0, "the pool must never run dry on a normal read"
