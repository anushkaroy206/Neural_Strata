"""B6 — the outbox: the queue that makes "offline-first" a mechanism, not a slogan.

The rule this file exists to keep
---------------------------------
Every payload that must leave the site is written **in the same transaction as the
local record it describes**. If the process dies between the two writes, either both
are there or neither is — that is the whole difference between a transactional
outbox and a `try/except` around an HTTP call. Nothing in this module ever opens a
connection of its own: every function takes the caller's connection, so the caller
owns the transaction boundary.

Three decisions worth reading before changing anything
------------------------------------------------------
1. **`attempts` is bumped on FAILURE, never at claim time.** This is the single
   most common way a home-grown outbox silently loses data: bump at claim, and a
   three-hour outage (twelve cycles) exhausts a ten-attempt budget, after which the
   affected rows are *never* sent even though the link came back. Claiming is
   therefore free; only a real failure costs an attempt.

2. **Claiming is a lease, not a lock held across the network.** `claim_outbox_batch`
   takes `FOR UPDATE SKIP LOCKED` for the duration of one short transaction and then
   pushes the claimed rows' `next_try_at` into the future by `OUTBOX_LEASE_S`. Two
   daemons can therefore never see the same row, and a daemon that is killed between
   claim and send leaves rows that come back by themselves when the lease expires —
   no `claimed_at` column, no reaper thread, and the queue is still correct.

3. **The queue is allowed to fill, but never to overflow silently.** At
   `OUTBOX_MAX_ROWS` pending, `enqueue_outbox` refuses and returns `None`; the
   caller raises an `OUTBOX_FULL` infrastructure alert. The same rule as the SMS rate
   cap: a drop that nobody can see is worse than a refusal that somebody can.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import psycopg2.extras

import config

#: How long a claim protects a row from a second claimant. Must exceed the
#: transport's own worst case (connect timeout + read timeout + retries), or a slow
#: but healthy send would be duplicated by the next cycle. 120 s against an 8 s
#: client timeout is deliberately generous — the cost of a wide margin is a slower
#: recovery from a *crashed* daemon, and a slow recovery loses nothing.
OUTBOX_LEASE_S = 120


def _priority(kind: str) -> int:
    return int(config.OUTBOX_PRIORITY.get(str(kind), 50))


def enqueue_outbox(conn, kind: str, payload, *, ref: str | None = None,
                   priority: int | None = None, now: float | None = None) -> int | None:
    """Queue one payload **inside the caller's transaction**. Returns the row id.

    Returns `None` — never raises — when the queue is full, so a caller can decide
    what to shout about while its own transaction still commits. A refusal is
    visible; a drop is not.

    Idempotent on `ref` (migration 008's unique partial index): re-enqueueing a
    reference that is already queued returns **that row's id** instead of adding a
    second copy, because "ensure this is queued" is the contract callers actually
    want. `None` therefore means exactly one thing: the queue refused the row.
    """
    now_ts = datetime.fromtimestamp(float(now or time.time()), tz=timezone.utc)
    body = payload if isinstance(payload, str) else json.dumps(payload, default=str)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM outbox WHERE status = 'PENDING'")
        pending = cur.fetchone()[0]
        if pending >= int(config.OUTBOX_MAX_ROWS):
            return None
        # `ON CONFLICT (ref) … DO NOTHING` + the partial unique index
        # (`ux_outbox_ref`, migration 008) are the two halves of one guarantee:
        # **enqueueing the same `ref` twice queues one row.** Either half alone is
        # a comment — an index without the clause raises, and the clause without
        # the index does nothing at all (which is what the first version of this
        # function did, while the docstring promised idempotence).
        cur.execute(
            """INSERT INTO outbox (ts, kind, payload, status, priority, queued_at,
                                   next_try_at, site_id, ref)
               VALUES (%s, %s, %s::jsonb, 'PENDING', %s, %s, %s, %s, %s)
               ON CONFLICT (ref) WHERE ref IS NOT NULL AND status = 'PENDING'
               DO NOTHING
               RETURNING id""",
            (now_ts, str(kind), body, _priority(kind) if priority is None else int(priority),
             now_ts, now_ts, config.SITE_ID, ref))
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        if ref is None:
            return None                     # cannot happen: a NULL ref never conflicts
        # Already queued: return the row that is already there, so a caller sees
        # "this ref is in the queue" rather than "this ref is not in the queue".
        # The distinction matters: the alert path treats `None` as *the cap was
        # hit* and shouts about it, and shouting on an idempotent re-enqueue would
        # be a false alarm on the one path that must never cry wolf.
        cur.execute("SELECT id FROM outbox WHERE ref = %s ORDER BY id LIMIT 1", (ref,))
        existing = cur.fetchone()
        return int(existing[0]) if existing else None


def claim_outbox_batch(conn, limit: int | None = None, *, now: float | None = None,
                       kind: str | None = None, lease_s: int = OUTBOX_LEASE_S) -> list[dict]:
    """Lease the next batch: **priority first, then age**. Returns rows as dicts.

    `FOR UPDATE SKIP LOCKED` is what makes two daemons safe; the lease is what makes
    one *crashed* daemon safe. `attempts` is deliberately not touched (see the
    module docstring) — a row that is claimed and never sent costs nothing.
    """
    limit = int(limit or config.SYNC_BATCH)
    now_ts = datetime.fromtimestamp(float(now or time.time()), tz=timezone.utc)
    until = now_ts + timedelta(seconds=int(lease_s))
    where = "status = 'PENDING' AND next_try_at <= %s"
    args = [now_ts]
    if kind:
        where += " AND kind = %s"
        args.append(str(kind))
    args.append(limit)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            f"""SELECT * FROM outbox
                 WHERE {where}
                 ORDER BY priority ASC, id ASC
                 LIMIT %s
                 FOR UPDATE SKIP LOCKED""", args)
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            cur.execute(
                "UPDATE outbox SET next_try_at = %s WHERE id = ANY(%s)",
                (until, [r["id"] for r in rows]))
    conn.commit()            # the lease is what survives this commit — not a lock
    return rows


def mark_outbox_sent(conn, ids) -> int:
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute("""UPDATE outbox
                          SET status = 'SENT', sent_at = NOW(), last_error = NULL
                        WHERE id = ANY(%s)""", (ids,))
        n = cur.rowcount
    conn.commit()
    return n


def backoff_s(attempts: int) -> int:
    """The delay before attempt `attempts + 1`: 1, 2, 4, 8, 16, 30 … capped.

    The cap matters more than the curve. An uncapped doubling is an hour by the
    tenth attempt, which reads on a console as "sync died" when the truth is "the
    link is having an afternoon".
    """
    table = list(config.SYNC_BACKOFF_S)
    idx = max(0, min(int(attempts), len(table) - 1))
    return int(table[idx])


def mark_outbox_failed(conn, ids, error: str, *, now: float | None = None) -> dict:
    """Record a failure: bump the attempt, keep the error, schedule the retry.

    A row that reaches `SYNC_MAX_ATTEMPTS` becomes `FAILED` and **stays**, with its
    last error, for inspection (§9.2 rule 4). Deliberately not deleted: a queue that
    forgets what it could not deliver cannot answer the only question anyone asks
    about it.
    """
    ids = [int(i) for i in (ids or [])]
    if not ids:
        return {"failed": 0, "exhausted": 0}
    now_ts = datetime.fromtimestamp(float(now or time.time()), tz=timezone.utc)
    exhausted = 0
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT id, attempts FROM outbox WHERE id = ANY(%s)", (ids,))
        rows = [dict(r) for r in cur.fetchall()]
        for row in rows:
            attempts = int(row.get("attempts") or 0) + 1
            give_up = attempts >= int(config.SYNC_MAX_ATTEMPTS)
            exhausted += 1 if give_up else 0
            cur.execute(
                """UPDATE outbox
                      SET attempts = %s, last_error = %s,
                          status = %s,
                          next_try_at = %s
                    WHERE id = %s""",
                (attempts, str(error)[:400], "FAILED" if give_up else "PENDING",
                 now_ts + timedelta(seconds=backoff_s(attempts)), row["id"]))
    conn.commit()
    return {"failed": len(rows), "exhausted": exhausted}


def requeue_failed(conn, *, now: float | None = None, limit: int = 1000) -> int:
    """Put FAILED rows back on the queue — the operator's last resort.

    Exists because "the row is FAILED forever" is a dead end on a bench where the
    cause was a laptop's Wi-Fi being off for an afternoon. It is *not* automatic:
    a row that exhausted ten attempts with real errors should be looked at, not
    silently retried on every boot.
    """
    now_ts = datetime.fromtimestamp(float(now or time.time()), tz=timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""UPDATE outbox SET status = 'PENDING', next_try_at = %s
                        WHERE id IN (SELECT id FROM outbox WHERE status = 'FAILED'
                                      ORDER BY id LIMIT %s)""", (now_ts, int(limit)))
        n = cur.rowcount
    conn.commit()
    return n


def compact_outbox(conn, *, keep_days: int | None = None,
                   now: float | None = None) -> int:
    """Prune **only** confirmed-delivered rows older than `keep_days` (§9.2 rule 6).

    The `status = 'SENT'` in the WHERE clause is the entire safety argument of this
    function: a PENDING row is the only copy of something that has arrived nowhere,
    and a FAILED row is the only record of something that could not. Neither is ever
    pruned, and `last_error`/`ts` survive long enough to be read.
    """
    days = int(config.OUTBOX_KEEP_DAYS if keep_days is None else keep_days)
    now_ts = datetime.fromtimestamp(float(now or time.time()), tz=timezone.utc)
    cutoff = now_ts - timedelta(days=days)
    with conn.cursor() as cur:
        cur.execute("""DELETE FROM outbox
                        WHERE status = 'SENT' AND sent_at IS NOT NULL
                          AND sent_at < %s""", (cutoff,))
        n = cur.rowcount
    conn.commit()
    return n


def pending_count(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM outbox WHERE status = 'PENDING'")
        return int(cur.fetchone()[0])


def outbox_status(conn) -> dict:
    """The numbers the dashboard chip prints — read from the queue, never invented."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT status, count(*) FROM outbox GROUP BY status""")
        by_status = {r[0]: int(r[1]) for r in cur.fetchall()}
        cur.execute("""
            SELECT kind, count(*) FROM outbox
             WHERE status = 'PENDING' GROUP BY kind ORDER BY count(*) DESC""")
        by_kind = {r[0]: int(r[1]) for r in cur.fetchall()}
        cur.execute("SELECT min(queued_at), max(attempts) FROM outbox WHERE status = 'PENDING'")
        oldest, worst = cur.fetchone()
    pending = by_status.get("PENDING", 0)
    return {
        "pending": pending,
        "sent": by_status.get("SENT", 0),
        "failed": by_status.get("FAILED", 0),
        "by_kind": by_kind,
        "oldest_pending_age_s": (round(time.time() - oldest.timestamp()) if oldest else None),
        "max_attempts_pending": int(worst or 0),
        "max_rows": int(config.OUTBOX_MAX_ROWS),
        "full": pending >= int(config.OUTBOX_MAX_ROWS),
    }


def alert_outbox_row(alert, alert_id: int | None = None) -> dict:
    """The payload an alert puts on the queue — the same shape the cloud table wants.

    Built here rather than at the call site so that the local alert, the queued
    copy and the cloud row cannot drift apart: they are three views of one event.

    **`ref` carries the alert's row id** (`NS-976A#41`), and that suffix is not
    decoration. `templates.reference()` is a four-hex-character hash of the
    incident's identity — 65 536 values, chosen so a human can read it out loud —
    and two genuinely different alerts *will* collide on it. Without the id, the
    queue's new uniqueness rule would fold the second alert into the first's
    pending row and deliver one alert where two were raised: silent loss, in the
    one path where silent loss is least acceptable. With it, the reference stays
    the human handle it was designed to be and the queue key stays exact.
    """
    from alerts import templates
    get = (lambda k, d=None: alert.get(k, d)) if isinstance(alert, dict) \
        else (lambda k, d=None: getattr(alert, k, d))
    reference = str(get("reference") or templates.reference(alert))
    return {
        "ref": f"{reference}#{int(alert_id)}" if alert_id else reference,
        "site_id": config.SITE_ID,
        "node_id": str(get("node_id") or ""),
        "level_id": str(get("level_id") or ""),
        "severity": str(get("severity") or "WATCH").upper(),
        "event_type": str(get("event_type") or "ALERT"),
        "state": str(get("state") or "NEW"),
        "ts": float(get("ts") or time.time()),
        "ttf_seconds": get("ttf_seconds"),
        "recommended_action": str(get("recommended_action") or "")[:300],
        "geo_lat": get("geo_lat"),
        "geo_lon": get("geo_lon"),
    }
