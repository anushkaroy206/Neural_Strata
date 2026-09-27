"""B6 — the sync daemon: one thread, two paths, and no way to lose a row.

What this module is
-------------------
The *uploader*. It owns the only outbound network traffic the site has, and it is
deliberately the most paranoid file in the project, because it is the only one whose
failure is silent: a dropped telemetry row looks exactly like a quiet hour.

Design rules, each one a defect if dropped
------------------------------------------
* **Its own thread** — never blocks the 2 Hz ingest path. HTTP inside the ingest path
  means ingest inherits the network's latency and failure modes, which is precisely
  the mistake that makes "offline-first" systems stall when the link flaps.
* **Never raises into the caller** — every exception is captured, logged and written
  to the row it belongs to. An exception that escapes the daemon kills the only
  thread that can ever deliver anything.
* **`sync_attempts` is bumped on FAILURE only, never at claim** — a three-hour outage
  is twelve cycles; bump at claim and a ten-attempt budget is gone before the link
  returns, after which those hours are *never* sent. This is the most common way a
  hand-rolled outbox silently loses data.
* **Rows are marked delivered *after* the far side commits** — the recoverable
  failure is "sent but marked unsent" (a harmless re-send of an idempotent upsert).
  The other order gives "marked sent but never arrived", which nothing can recover.
* **No work ⇒ no connection** — on a quiet site this daemon opens **zero** sockets
  for hours, which is what keeps a free tier from being suspended for idleness.
* **`stats` is zeroed when the far side rejects the batch** — a failed COMMIT rolls
  back work the upsert already counted, and reporting those counts would make
  `/api/sync` tell the demo console a success that the cloud never received.

Two paths, and the console is told which one is live:

    outbox → HTTP (gzip + HMAC, idempotent on batch_id)   discrete events, alerts first
    watermark → Postgres (execute_values + ON CONFLICT)   bulk hourly rollups

There is no third path and no fallback between them: a batch that the HTTP endpoint
refused is retried over HTTP, because sending an alert through the bulk channel would
mean the alert tier's ordering guarantees stopped at the laptop.
"""

from __future__ import annotations

import logging
import threading
import time

import psycopg2.extras

import config
from persistence import cloud_client, outbox, rollup

log = logging.getLogger("sync")

#: Column lists — **not** `SELECT *` order. The cloud schema is a different shape on
#: purpose (§E.1 rule 1), so the mapping from edge row to cloud column is written
#: once, here, where the two schemas can be read side by side.
ROLLUP_COLS = ["site_id", "node_id", "hour_start", "n_samples",
               "disp_mean", "disp_min", "disp_max", "disp_last",
               "tilt_mean", "tilt_max", "acc_mean", "acc_max",
               "vib_sum", "micro_max", "macro_max", "severity_max"]

ALERT_COLS = ["site_id", "ts", "level_id", "node_id", "severity", "event_type",
              "progression_stage", "ttf_seconds", "ttf_q10", "ttf_q90",
              "disruption_label", "disruption_index",
              "geo_lat", "geo_lon", "recommended_action"]

_last_ok: float = 0.0
_last_error: str | None = None
_last_stats: dict = {}
_started_at: float = 0.0
_cycles = 0

#: Re-exported so `from persistence.sync import sign_payload, probe_backhaul` works —
#: §9.4 names them as sync.py's, and they are: this is where the policy lives. The
#: implementation sits in `cloud_client` because that is what talks to the socket.
sign_payload = cloud_client.sign_payload
probe_backhaul = cloud_client.probe_backhaul


def enabled() -> bool:
    """Is *either* path configured? The two are independent on purpose."""
    return bool(config.CLOUD_ENABLED or cloud_client.configured())


def status() -> dict:
    """Backs `/api/sync/status` and the dashboard chip."""
    return {
        "cloud_enabled": enabled(),
        "cloud_db": bool(config.CLOUD_ENABLED),
        "cloud_http": cloud_client.configured(),
        "signed": bool(config.CLOUD_HMAC_KEY),
        "site_id": config.SITE_ID,
        "site_name": config.SITE_NAME,
        "interval_s": config.SYNC_INTERVAL_S,
        "batch": config.SYNC_BATCH,
        "max_attempts": config.SYNC_MAX_ATTEMPTS,
        "last_success_s_ago": (round(time.time() - _last_ok) if _last_ok else None),
        "last_error": _last_error,
        "last_stats": dict(_last_stats),
        "cycles": _cycles,
        "daemon_started_s_ago": (round(time.time() - _started_at) if _started_at else None),
        "backoff_s": list(config.SYNC_BACKOFF_S),
    }


def pending_counts(edge_conn) -> dict:
    """Cheap read for the chip — safe to call from the API thread.

    Counts **rows with `synced_at IS NULL`**, i.e. the truth, rather than a number
    the daemon remembered. A chip that prints a remembered number is a chip that
    disagrees with the database exactly when it matters.
    """
    counts = {"rollups": 0, "alerts": 0, "outbox": 0}
    if edge_conn is None:
        return counts
    with edge_conn.cursor() as cur:
        cur.execute("""SELECT
            (SELECT count(*) FROM node_hourly
              WHERE synced_at IS NULL AND sync_attempts < %s) AS rollups,
            (SELECT count(*) FROM alerts_history
              WHERE synced_at IS NULL AND sync_attempts < %s) AS alerts,
            (SELECT count(*) FROM outbox WHERE status = 'PENDING') AS outbox""",
            (config.SYNC_MAX_ATTEMPTS, config.SYNC_MAX_ATTEMPTS))
        row = cur.fetchone()
    counts.update({"rollups": int(row[0]), "alerts": int(row[1]), "outbox": int(row[2])})
    counts["total"] = counts["rollups"] + counts["alerts"] + counts["outbox"]
    return counts


# ---------------------------------------------------------------------------
# the bulk path — hourly rollups and alert history, through watermarks
# ---------------------------------------------------------------------------

def _cloud_sslmode(url: str) -> str:
    """SSL for a remote cloud, off for the local docker Postgres.

    A hosted URL (Neon and the like) is reached with ``sslmode=require``. The
    compose database on localhost has no TLS, and forcing require there makes
    every ``/api/sync`` fail before a row moves. An explicit ``sslmode`` in the
    URL wins either way.
    """
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(url or "")
    chosen = parse_qs(parsed.query).get("sslmode")
    if chosen:
        return chosen[-1]
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1"):
        return "disable"
    return "require"


def _cloud_conn():
    """A short-lived cloud connection.

    Deliberately **not** pooled: an idle connection is exactly what a free tier
    suspends, and a pool full of dead sockets is the classic cause of "sync worked
    yesterday". Open, work, close.
    """
    sslmode = _cloud_sslmode(config.CLOUD_DB_URL)
    conn = psycopg2.connect(
        config.CLOUD_DB_URL,
        connect_timeout=config.CLOUD_TIMEOUT_S,
        sslmode=sslmode,
        application_name=f"ns-{config.SITE_ID}",
    )
    conn.autocommit = False
    return conn


def _fetch_pending(edge_conn) -> tuple[list, list]:
    """Read the next batch of unsynced rows. Locks, but does not mutate.

    `FOR UPDATE SKIP LOCKED` costs nothing with a single daemon and is what makes a
    manual `/api/sync` safe to press *while* the daemon is mid-cycle — two flushers
    see disjoint batches instead of racing.
    """
    with edge_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM node_hourly
            WHERE synced_at IS NULL AND sync_attempts < %s
            ORDER BY hour_start ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (config.SYNC_MAX_ATTEMPTS, config.SYNC_BATCH))
        rollups = [dict(r) for r in cur.fetchall()]

        cur.execute("""
            SELECT * FROM alerts_history
            WHERE synced_at IS NULL AND sync_attempts < %s
            ORDER BY ts ASC
            LIMIT %s
            FOR UPDATE SKIP LOCKED
        """, (config.SYNC_MAX_ATTEMPTS, config.SYNC_BATCH))
        alerts = [dict(r) for r in cur.fetchall()]
    edge_conn.commit()          # releases the row locks; every write is an upsert
    return rollups, alerts


def _upsert(cloud_conn, table: str, cols: list, rows: list, conflict_cols: list) -> int:
    if not rows:
        return 0
    values = [tuple(r.get(c) for c in cols) for r in rows]
    set_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in conflict_cols)
    sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES %s "
           f"ON CONFLICT ({', '.join(conflict_cols)}) DO UPDATE SET {set_clause}")
    with cloud_conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=200)
    return len(values)


def _mark_synced(edge_conn, rollups: list, alerts: list) -> None:
    """Mark rows delivered — keyed **exactly**, so nothing is marked that was not sent."""
    with edge_conn.cursor() as cur:
        if rollups:
            psycopg2.extras.execute_values(cur, """
                UPDATE node_hourly AS n
                   SET synced_at = now(), last_error = NULL
                  FROM (VALUES %s) AS v(site_id, node_id, hour_start)
                 WHERE n.site_id    = v.site_id::text
                   AND n.node_id    = v.node_id::text
                   AND n.hour_start = v.hour_start::timestamptz
            """, [(r["site_id"], r["node_id"], r["hour_start"]) for r in rollups])
        if alerts:
            cur.execute("""UPDATE alerts_history
                              SET synced_at = now(), last_error = NULL
                            WHERE id = ANY(%s)""", ([a["id"] for a in alerts],))
    edge_conn.commit()


def _mark_failed(edge_conn, rollups: list, alerts: list, err: str) -> None:
    """Bump the attempt and record why — only on a real failure."""
    with edge_conn.cursor() as cur:
        if rollups:
            psycopg2.extras.execute_values(cur, """
                UPDATE node_hourly AS n
                   SET sync_attempts = n.sync_attempts + 1, last_error = v.err
                  FROM (VALUES %s) AS v(site_id, node_id, hour_start, err)
                 WHERE n.site_id    = v.site_id::text
                   AND n.node_id    = v.node_id::text
                   AND n.hour_start = v.hour_start::timestamptz
            """, [(r["site_id"], r["node_id"], r["hour_start"], err) for r in rollups])
        if alerts:
            cur.execute("""UPDATE alerts_history
                              SET sync_attempts = sync_attempts + 1, last_error = %s
                            WHERE id = ANY(%s)""", (err, [a["id"] for a in alerts]))
    edge_conn.commit()


# ---------------------------------------------------------------------------
# the outbox path — discrete events, alerts first, over signed HTTP
# ---------------------------------------------------------------------------

def flush_batch(edge_conn, rows=None, *, limit: int | None = None) -> dict:
    """Claim → send → mark, for discrete events. Returns a stats dict.

    The claim is a **lease** (see `outbox.claim_outbox_batch`): rows are pushed out
    of reach for `OUTBOX_LEASE_S` while they are in flight, so a daemon killed
    mid-send loses nothing — the lease expires and the rows come back.
    """
    stats = {"attempted": 0, "sent": 0, "failed": 0, "duplicate": False, "error": None}
    if rows is None:
        if not cloud_client.configured():
            return stats
        rows = outbox.claim_outbox_batch(edge_conn, limit or config.SYNC_BATCH)
    if not rows:
        return stats

    stats["attempted"] = len(rows)
    payload = {
        "batch_id": None,                       # cloud_client assigns one
        "kind": "outbox",
        "count": len(rows),
        "items": [{"id": r["id"], "kind": r["kind"], "ref": r.get("ref"),
                   "ts": (r["ts"].timestamp() if hasattr(r.get("ts"), "timestamp") else r.get("ts")),
                   "payload": (r["payload"] if isinstance(r["payload"], (dict, list))
                               else _maybe_json(r.get("payload")))}
                  for r in rows],
    }
    res = cloud_client.post_batch(payload)
    ids = [r["id"] for r in rows]
    if res.get("ok"):
        outbox.mark_outbox_sent(edge_conn, ids)
        stats["sent"] = len(ids)
        stats["duplicate"] = bool(res.get("duplicate"))
    else:
        outbox.mark_outbox_failed(edge_conn, ids, res.get("error") or "push failed")
        stats["failed"] = len(ids)
        stats["error"] = res.get("error")
    stats["bytes_gzip"] = res.get("bytes_gzip", 0)
    return stats


def _maybe_json(value):
    import json
    try:
        return json.loads(value)
    except Exception:                                       # noqa: BLE001
        return value


# ---------------------------------------------------------------------------
# one cycle, and the thread that runs it
# ---------------------------------------------------------------------------

def sync_once(edge_conn) -> dict:
    """One sync cycle. Returns stats. **Never raises into the caller.**"""
    global _last_ok, _last_error, _last_stats, _cycles
    _cycles += 1
    stats = {"rollups": 0, "alerts": 0, "outbox": 0, "skipped": False,
             "error": None, "http": None, "db": None}

    if not enabled():
        stats["skipped"] = True
        _last_stats = dict(stats)
        return stats
    if edge_conn is None:
        stats["error"] = "edge database unreachable"
        _last_error = stats["error"]
        _last_stats = dict(stats)
        return stats

    # ── the outbox first: an alert waiting behind 500 telemetry rows is a
    #    regulator learning about a collapse tomorrow. ─────────────────────────
    if cloud_client.configured():
        try:
            http = flush_batch(edge_conn)
            stats["outbox"] = http.get("sent", 0)
            stats["http"] = {k: http.get(k) for k in ("attempted", "sent", "failed",
                                                      "duplicate", "error", "bytes_gzip")}
            if http.get("error"):
                stats["error"] = http["error"]
        except Exception as exc:                            # noqa: BLE001
            stats["error"] = f"outbox flush failed: {exc}"
            log.warning("outbox flush failed: %s", exc)

    # ── then the bulk watermark path ─────────────────────────────────────────
    if config.CLOUD_ENABLED:
        rollups: list = []
        alerts: list = []
        try:
            rollups, alerts = _fetch_pending(edge_conn)
        except Exception as exc:                            # noqa: BLE001
            stats["error"] = f"local read failed: {exc}"
            _last_error = stats["error"]
            _last_stats = dict(stats)
            return stats
        if rollups or alerts:
            cloud = None
            try:
                cloud = _cloud_conn()
                if rollups:
                    stats["rollups"] = _upsert(cloud, "node_hourly", ROLLUP_COLS, rollups,
                                               ["site_id", "node_id", "hour_start"])
                if alerts:
                    stats["alerts"] = _upsert(cloud, "alerts_history", ALERT_COLS, alerts,
                                              ["site_id", "ts", "node_id", "event_type"])
                with cloud.cursor() as cur:
                    cur.execute("""INSERT INTO sync_log
                                     (site_id, n_rollups, n_alerts, ok, detail)
                                   VALUES (%s, %s, %s, TRUE, %s)""",
                                (config.SITE_ID, stats["rollups"], stats["alerts"],
                                 '{"host": "edge"}'))
                cloud.commit()
                _mark_synced(edge_conn, rollups, alerts)
                stats["db"] = {"rollups": stats["rollups"], "alerts": stats["alerts"]}
            except Exception as exc:                        # noqa: BLE001
                # Zeroed on purpose: a failed COMMIT rolls back work `_upsert`
                # already counted, and reporting those numbers would have
                # `/api/sync` claim a success the cloud never received.
                stats["rollups"] = 0
                stats["alerts"] = 0
                stats["error"] = str(exc)[:200]
                _last_error = stats["error"]
                log.warning("bulk sync failed (%d rollups, %d alerts pending): %s",
                            len(rollups), len(alerts), stats["error"])
                try:
                    _mark_failed(edge_conn, rollups, alerts, stats["error"])
                except Exception as exc2:                   # noqa: BLE001
                    # Bookkeeping must never mask the real failure — and the rows
                    # stay unsynced, which is the safe direction.
                    log.error("could not record failure (rows remain pending): %s", exc2)
            finally:
                if cloud is not None:
                    try:
                        cloud.close()
                    except Exception:                       # noqa: BLE001
                        pass

    if not stats["error"]:
        _last_ok = time.time()
        _last_error = None
    else:
        _last_error = stats["error"]
    if stats["rollups"] or stats["alerts"] or stats["outbox"]:
        stats["skipped"] = False
    elif not stats["error"]:
        stats["skipped"] = True
    _last_stats = dict(stats)
    return stats


def sync_daemon(edge_conn, stop_event: threading.Event) -> None:
    """The background thread. Sleeps `SYNC_INTERVAL_S` between cycles.

    `stop_event.wait()` rather than `time.sleep()` so shutdown is immediate: a
    laptop being closed on the demo table should not wait fifteen minutes for a
    thread that is doing nothing.
    """
    global _started_at
    _started_at = time.time()
    log.info("sync daemon started (db=%s, http=%s, every %ds)",
             config.CLOUD_ENABLED, cloud_client.configured(), config.SYNC_INTERVAL_S)
    print(f"[sync] daemon started · interval {config.SYNC_INTERVAL_S}s · "
          f"db={'on' if config.CLOUD_ENABLED else 'off'} "
          f"http={'on' if cloud_client.configured() else 'off'}")
    while not stop_event.wait(config.SYNC_INTERVAL_S):
        try:
            sync_once(edge_conn)
        except Exception as exc:                            # noqa: BLE001
            log.warning("sync cycle error: %s", exc)


def compact(edge_conn) -> int:
    """Prune delivered rows (called once a day by the daemon's caller, or by hand)."""
    return outbox.compact_outbox(edge_conn)


def rollup_and_queue(edge_conn, state=None) -> dict:
    """Close the previous hour and queue nothing — the watermark *is* the queue.

    Kept next to `sync_once` so the two halves of the bulk path read together: this
    is what makes rows appear, `sync_once` is what moves them.
    """
    severity = "NOMINAL"
    try:
        severity = str(getattr(state, "system_status", "NOMINAL") or "NOMINAL")
    except Exception:                                       # noqa: BLE001
        pass
    from datetime import datetime, timedelta, timezone
    prev = rollup.hour_start(datetime.now(timezone.utc)) - timedelta(hours=1)
    n = rollup.rollup_hour(edge_conn, config.SITE_ID, prev, severity=severity)
    return {"hour": prev.isoformat(), "rows": n,
            "pending": rollup.pending_rollups(edge_conn)}
