"""
Neural Strata — persistence: pooled connections, schema init, migrations.
=========================================================================

Phase 0 · closes the *storage* half of defect **D11** — the old code opened a
fresh PostgreSQL connection for every packet at 2 Hz and had no schema file at
all, so ``sensor_telemetry`` was written to by code but created by nobody.

Three things this module guarantees
-----------------------------------
1. **The schema exists.** ``python3 -m persistence.db --init`` applies every
   pending migration in ``persistence/migrations/`` in filename order and records
   each in ``schema_migrations``. It is idempotent: run it ten times, nothing
   changes and nothing is lost.
2. **Migrations are additive-only.** No ``DROP``, no ``DELETE``, no rewriting of
   existing rows. The database on the demo laptop is a *living* database with
   rehearsal data in it, and it must still be that database afterwards.
3. **The pipeline never dies because the database is down.** Every call degrades
   to a logged message. A rehearsal with PostgreSQL stopped still runs; it just
   stops accumulating history.

Connection pooling
------------------
``ThreadedConnectionPool`` because the server runs a ticker thread, a serial
reader thread and Flask's worker threads at once. Connections are validated
before use and discarded when the server has gone away, so a PostgreSQL restart
does not leave the process holding four dead sockets.

CLI
---
    python3 -m persistence.db --status        # what is applied, what is pending
    python3 -m persistence.db --init          # apply everything pending
    python3 -m persistence.db --dump-schema   # regenerate persistence/schema.sql
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

try:
    import psycopg2
    from psycopg2.extras import execute_values
    from psycopg2.pool import ThreadedConnectionPool
except Exception:  # pragma: no cover - psycopg2 is a hard dependency in practice
    psycopg2 = None  # type: ignore[assignment]
    execute_values = None  # type: ignore[assignment]
    ThreadedConnectionPool = None  # type: ignore[assignment]

import config          # Phase 9 — the outbox cap is read at call time
from config import (
    DB_CONNECT_TIMEOUT_S, DB_HOST, DB_NAME, DB_PASS, DB_POOL_MAX, DB_POOL_MIN,
    DB_POOL_WAIT_S,
    DB_PORT, DB_USER, MIGRATIONS_DIR, PERSISTENCE_DIR,
)
# The INSERT column list is derived from the contract, not typed again here.
from contracts import TELEMETRY_DB_COLUMNS

SCHEMA_SQL_PATH = PERSISTENCE_DIR / "schema.sql"

_MIGRATION_RE = re.compile(r"^(\d{3,})_([A-Za-z0-9_]+)\.sql$")


class DatabaseUnavailable(RuntimeError):
    """Raised when a connection cannot be obtained. Callers may catch it freely."""


# ---------------------------------------------------------------------------
# Error logging that does not drown the console
# ---------------------------------------------------------------------------

_log_lock = threading.Lock()
_last_logged: dict[str, float] = {}
_suppressed: dict[str, int] = {}
LOG_THROTTLE_S = 60.0


def _log_db_error(key: str, message: str) -> None:
    """Print a database error, at most once per ``LOG_THROTTLE_S`` per key.

    The old server printed a full traceback for every failed insert at 2 Hz —
    ~7,000 lines an hour — which is how a *real* database problem hides. The
    first occurrence is printed in full; repeats are counted and summarised.
    """
    now = time.time()
    with _log_lock:
        last = _last_logged.get(key, 0.0)
        if now - last < LOG_THROTTLE_S:
            _suppressed[key] = _suppressed.get(key, 0) + 1
            return
        n = _suppressed.pop(key, 0)
        _last_logged[key] = now
    suffix = f"  (+{n} similar suppressed)" if n else ""
    print(f"{message}{suffix}")


def _clear_error(key: str) -> None:
    with _log_lock:
        _last_logged.pop(key, None)
        _suppressed.pop(key, None)


# ---------------------------------------------------------------------------
# Pool
# ---------------------------------------------------------------------------

_pool = None
_pool_lock = threading.Lock()
_pool_broken_until = 0.0          # back-off after a failed pool construction
_pool_exhausted = 0               # times a caller had to wait for a free connection


def _dsn() -> dict:
    return {
        "host": DB_HOST, "port": DB_PORT, "dbname": DB_NAME,
        "user": DB_USER, "password": DB_PASS,
        "connect_timeout": DB_CONNECT_TIMEOUT_S,
        "application_name": "neural-strata-edge",
    }


def _get_pool():
    """Build the pool on first use. Returns ``None`` while the DB is unreachable."""
    global _pool, _pool_broken_until
    if psycopg2 is None or ThreadedConnectionPool is None:
        return None
    if _pool is not None:
        return _pool
    if time.time() < _pool_broken_until:
        return None
    with _pool_lock:
        if _pool is not None:
            return _pool
        if time.time() < _pool_broken_until:
            return None
        try:
            _pool = ThreadedConnectionPool(DB_POOL_MIN, DB_POOL_MAX, **_dsn())
            _clear_error("pool")
            return _pool
        except Exception as e:
            _pool_broken_until = time.time() + 10.0
            _log_db_error("pool", f"DB Conn Error: {e}")
            return None


def _alive(conn) -> bool:
    if conn is None or getattr(conn, "closed", 1):
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        return True
    except Exception:
        return False


def get_conn():
    """Borrow a live connection. Raises :class:`DatabaseUnavailable` if none.

    Use this when you *need* the database. Use :func:`try_get_conn` when the
    pipeline should carry on without it (which is the normal case here).
    """
    pool = _get_pool()
    if pool is None:
        raise DatabaseUnavailable("no connection pool (database unreachable)")

    # ── Phase 9 — a free connection is worth waiting a moment for ────────────
    # `ThreadedConnectionPool` raises `PoolError` the *instant* it is exhausted,
    # and every caller in this project reads a failure to borrow as "there is no
    # database" — which is a different fact with a different response. The wait is
    # bounded and small: it absorbs the ordinary case (a burst of requests, or a
    # test suite sharing the box with a running server) and still fails fast when
    # the database really is gone, because then `_get_pool()` never returns a pool
    # in the first place.
    deadline = time.time() + max(0.0, float(DB_POOL_WAIT_S))
    waited = False
    while True:
        try:
            conn = pool.getconn()
            break
        except Exception as e:
            global _pool_exhausted
            _pool_exhausted += 1
            if time.time() >= deadline:
                _log_db_error("db", f"connection pool exhausted after "
                                    f"{float(DB_POOL_WAIT_S):.1f}s wait: {e}"
                                    f" (stats: {pool_stats()})")
                raise DatabaseUnavailable(str(e)) from e
            waited = True
            time.sleep(0.02)
    if waited:
        _log_db_error("db", "connection pool was briefly exhausted — borrowed "
                            "after a short wait")
    if not _alive(conn):
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        try:
            conn = pool.getconn()
        except Exception as e:
            raise DatabaseUnavailable(str(e)) from e
        if not _alive(conn):
            raise DatabaseUnavailable("pooled connection failed its ping")
    return conn


def pool_stats() -> dict:
    """How the pool is doing: borrowed, free, and how many times it ran dry.

    Phase 9 added this after a test run reported 652 passes and six skips in the
    same breath: the skips said "database unreachable" while the database was
    perfectly reachable, and the only way to tell the two apart was to look inside
    the pool. A diagnostic that answers "is it the database or is it *us*" earns
    its twenty lines, and `/api/health` can report it for the same reason.
    """
    pool = _pool
    if pool is None:
        return {"pool": False, "min": DB_POOL_MIN, "max": DB_POOL_MAX,
                "borrowed": 0, "free": 0, "exhausted": _pool_exhausted}
    try:
        free = len(getattr(pool, "_pool", []) or [])
        used = len(getattr(pool, "_used", {}) or {})
    except Exception:                                           # noqa: BLE001
        free, used = -1, -1
    return {"pool": True, "min": DB_POOL_MIN, "max": DB_POOL_MAX,
            "borrowed": used, "free": free, "exhausted": _pool_exhausted}


def try_get_conn():
    """Borrow a live connection, or ``None`` — never raises."""
    try:
        return get_conn()
    except DatabaseUnavailable:
        return None


@contextmanager
def borrowed():
    """Borrow for the duration of a block, and give it back on every path.

    Phase 9 added this after the pool went dry mid-suite and told the world "the
    database is unreachable" — 2 749 failed borrows and four skipped tests, from
    one missing `release()`. Two facts make that bug worth a helper rather than a
    code review rule:

    * psycopg2's pool is keyed by **thread**, so a connection borrowed inside a
      short-lived thread (a Flask request, a worker) is only returned by an
      explicit `release()` — the thread dying does not return it;
    * the pool is four wide by design, so four misses and the *whole process*
      looks like it has no database.

    ``with borrowed() as conn:`` makes "did I give it back" a property of the
    indentation instead of a property of the author's memory.
    """
    conn = try_get_conn()
    try:
        yield conn
    finally:
        release(conn)


def release(conn) -> None:
    """Return a connection to the pool (safe to call with ``None``)."""
    if conn is None:
        return
    pool = _pool
    if pool is None:
        try:
            conn.close()
        except Exception:
            pass
        return
    try:
        if getattr(conn, "closed", 0):
            pool.putconn(conn, close=True)
        else:
            pool.putconn(conn)
    except Exception:
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def connection():
    """``with connection() as conn:`` — borrows, commits, releases. Silent when down.

    Yields ``None`` if the database is unreachable, so callers can simply guard
    with ``if not conn: return``.
    """
    conn = try_get_conn()
    if conn is None:
        yield None
        return
    try:
        yield conn
        conn.commit()
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("conn", f"DB error: {e}")
    finally:
        release(conn)


def close_pool() -> None:
    """Close every pooled connection (tests, shutdown)."""
    global _pool
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.closeall()
            except Exception:
                pass
            _pool = None


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

def discover_migrations() -> list[tuple[int, str, Path]]:
    """Every migration on disk as ``(version, name, path)``, ordered by version."""
    if not MIGRATIONS_DIR.exists():
        return []
    found: list[tuple[int, str, Path]] = []
    for p in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(p.name)
        if m:
            found.append((int(m.group(1)), m.group(2), p))
    return sorted(found, key=lambda t: t[0])


def applied_versions() -> set[int]:
    """Versions already recorded in ``schema_migrations`` (empty if unreachable)."""
    conn = try_get_conn()
    if conn is None:
        return set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations")
            return {int(r[0]) for r in cur.fetchall()}
    except Exception:
        return set()
    finally:
        release(conn)


def _ensure_ledger(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version    INTEGER PRIMARY KEY,
                name       TEXT,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)


def migrate(verbose: bool = True) -> list[int]:
    """Apply every pending migration in version order. Returns the versions applied.

    Each migration runs inside its own transaction: a failure rolls back that
    migration alone and is reported, leaving the ledger honest about what is in.
    """
    migrations = discover_migrations()
    if not migrations:
        if verbose:
            print(f"[db] no migrations found in {MIGRATIONS_DIR}")
        return []

    conn = try_get_conn()
    if conn is None:
        if verbose:
            print("[db] database unreachable — nothing applied")
        return []

    applied: list[int] = []
    try:
        _ensure_ledger(conn)
        conn.commit()
        done = applied_versions()

        for version, name, path in migrations:
            if version in done:
                if verbose:
                    print(f"[db] {version:03d} {name:<24} already applied")
                continue
            sql = path.read_text()
            try:
                with conn.cursor() as cur:
                    cur.execute(sql)
                    cur.execute(
                        "INSERT INTO schema_migrations (version, name) VALUES (%s, %s) "
                        "ON CONFLICT (version) DO NOTHING",
                        (version, name),
                    )
                conn.commit()
                applied.append(version)
                if verbose:
                    print(f"[db] {version:03d} {name:<24} APPLIED")
            except Exception as e:
                conn.rollback()
                if verbose:
                    print(f"[db] {version:03d} {name:<24} FAILED — {e}")
                raise
    except Exception as e:
        _log_db_error("migrate", f"DB migration error: {e}")
    finally:
        release(conn)
    return applied


def init_schema(verbose: bool = True) -> list[int]:
    """Bring the database up to date. The name the runbook and ``run.sh`` call."""
    return migrate(verbose=verbose)


def status() -> dict:
    """Ledger state for ``--status`` and (from Phase 2) ``/api/health``."""
    conn = try_get_conn()
    on_disk = discover_migrations()
    if conn is None:
        return {
            "connected": False, "database": DB_NAME, "host": DB_HOST,
            "applied": [], "pending": [v for v, _, _ in on_disk],
            "tables": [], "error": "unreachable",
        }
    try:
        done = applied_versions()
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [r[0] for r in cur.fetchall()]
        return {
            "connected": True, "database": DB_NAME, "host": DB_HOST,
            "applied": sorted(done),
            "pending": [v for v, _, _ in on_disk if v not in done],
            "tables": tables,
        }
    except Exception as e:
        return {"connected": True, "database": DB_NAME, "host": DB_HOST,
                "applied": [], "pending": [v for v, _, _ in on_disk],
                "tables": [], "error": str(e)}
    finally:
        release(conn)


def dump_schema(out_path: Path | None = None, verbose: bool = True) -> Path:
    """Regenerate ``persistence/schema.sql`` from the migrations.

    The migrations are the single source of truth, so the schema file is
    *generated* rather than maintained by hand — two hand-written copies of the
    same DDL is exactly how a plan and a database drift apart.
    """
    out_path = out_path or SCHEMA_SQL_PATH
    migrations = discover_migrations()
    parts = [
        "-- ==========================================================================",
        "-- Neural Strata — schema.sql",
        "-- ==========================================================================",
        "-- GENERATED FILE — do not edit by hand.",
        "-- Produced by:  python3 -m persistence.db --dump-schema",
        "-- Source of truth:  backend/persistence/migrations/*.sql",
        "--",
        "-- This is the full schema for a FRESH database (every migration, in order).",
        "-- The demo laptop already has a database, so apply the migrations instead:",
        "--     python3 -m persistence.db --init",
        "-- ==========================================================================",
        "",
    ]
    for version, name, path in migrations:
        parts.append(f"-- ── migration {version:03d} · {name} " + "─" * max(0, 40 - len(name)))
        parts.append(path.read_text().rstrip())
        parts.append("")
    out_path.write_text("\n".join(parts))
    if verbose:
        print(f"[db] wrote {out_path} ({len(migrations)} migrations, "
              f"{out_path.stat().st_size} bytes)")
    return out_path


# ---------------------------------------------------------------------------
# Writes used by the live pipeline
# ---------------------------------------------------------------------------

#: Two spellings of the same insert, because psycopg2 wants them differently:
#: `execute_values` needs ONE %s (it expands the whole row tuple list itself),
#: `executemany` needs the per-column placeholders.
#: Column list for the telemetry INSERT. **Derived from the contract, never
#: typed twice.** `contracts.TELEMETRY_DB_COLUMNS` is the same tuple that
#: `NodeState.to_db_tuple()` writes, so migration 002 widening the row from four
#: columns to ten needed no edit here at all — and cannot be half-applied by a
#: later phase that edits only one of the two.
_TELEMETRY_COLUMNS = tuple(TELEMETRY_DB_COLUMNS)
_TELEMETRY_COLS = "(" + ", ".join(_TELEMETRY_COLUMNS) + ")"
#: `executemany` path needs one placeholder per column.
_TELEMETRY_PLACEHOLDERS = ", ".join(["%s"] * len(_TELEMETRY_COLUMNS))
#: `execute_values` path takes the *single* `%s` that psycopg2 expands into a
#: VALUES list. Do not "fix" this to the placeholder string — that is the bug
#: that made every multi-row insert fail silently in Phase 0.
INSERT_TELEMETRY_SQL = f"INSERT INTO sensor_telemetry {_TELEMETRY_COLS} VALUES ({_TELEMETRY_PLACEHOLDERS})"
INSERT_TELEMETRY_VALUES = f"INSERT INTO sensor_telemetry {_TELEMETRY_COLS} VALUES %s"
INSERT_RFID_SQL = (
    "INSERT INTO rfid_evacuation_log (miner_uid, current_zone) VALUES (%s, %s)"
)


def insert_telemetry(rows: list[tuple]) -> int:
    """Insert ``NodeState.to_db_tuple()`` rows (10 columns since migration 002).

    Returns the number of rows written; ``0`` when the database is unavailable —
    which is a normal, survivable condition, not an error the caller must handle.
    """
    if not rows:
        return 0
    conn = try_get_conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            if execute_values is not None:
                execute_values(cur, INSERT_TELEMETRY_VALUES, rows)
            else:  # pragma: no cover
                cur.executemany(INSERT_TELEMETRY_SQL, rows)
        conn.commit()
        _clear_error("insert")
        return len(rows)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("insert", f"DB log error: {e}")
        return 0
    finally:
        release(conn)


def insert_rfid(miner_uid: str, current_zone: str) -> bool:
    """Record one tag read in the muster audit trail."""
    conn = try_get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute(INSERT_RFID_SQL, (str(miner_uid), str(current_zone)))
        conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("rfid", f"DB rfid error: {e}")
        return False
    finally:
        release(conn)


# ---------------------------------------------------------------------------
# Phase 8 (B4) — the alert store
# ---------------------------------------------------------------------------
# Six functions, and they are here rather than in `alerts/` on purpose: the alert
# *policy* (when to speak, to whom, how loudly) belongs to `alerts/`, and the SQL
# belongs next to the schema file that declares the columns. Splitting them any
# other way puts the column list in two files, which is how a schema and its
# writer drift apart.

INSERT_ALERT_SQL = """
    INSERT INTO alerts_history (
        ts, level_id, node_id, severity, event_type, progression_stage,
        ttf_seconds, ttf_q10, ttf_q90, disruption_label, disruption_index,
        geo_lat, geo_lon, recommended_action, state,
        sms_dispatched, email_dispatched, local_dispatched, dash_dispatched,
        attempts, last_error, dedupe_key, firewalled, escalated,
        suppressed, suppress_reason, reference, updated_at
    ) VALUES (
        to_timestamp(%(ts)s), %(level_id)s, %(node_id)s, %(severity)s, %(event_type)s,
        %(progression_stage)s, %(ttf_seconds)s, %(ttf_q10)s, %(ttf_q90)s,
        %(disruption_label)s, %(disruption_index)s, %(geo_lat)s, %(geo_lon)s,
        %(recommended_action)s, %(state)s,
        %(sms_dispatched)s, %(email_dispatched)s, %(local_dispatched)s, %(dash_dispatched)s,
        %(attempts)s, %(last_error)s, %(dedupe_key)s, %(firewalled)s, %(escalated)s,
        %(suppressed)s, %(suppress_reason)s, %(reference)s, NOW()
    )
    RETURNING id
"""

_UPDATE_ALERT_SQL = """
    UPDATE alerts_history SET
        state = %(state)s, attempts = %(attempts)s, last_error = %(last_error)s,
        sms_dispatched = %(sms_dispatched)s, email_dispatched = %(email_dispatched)s,
        local_dispatched = %(local_dispatched)s, dash_dispatched = %(dash_dispatched)s,
        escalated = %(escalated)s, suppressed = %(suppressed)s,
        suppress_reason = %(suppress_reason)s, closed_reason = %(closed_reason)s,
        updated_at = NOW()
    WHERE id = %(id)s
"""


def _alert_params(event, deliveries=()) -> dict:
    """One alert row's parameters, from either a public dict or an AlertEvent.

    Reads attributes and mapping keys both, because the dispatcher hands over
    objects and `/api/alerts` hands over their `as_public()` dicts — and a second
    serializer for the second shape is a second place for the two to disagree.
    """
    def get(key, default=None):
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    ok = {d.get("channel") if isinstance(d, dict) else d.channel
          for d in deliveries
          if (d.get("ok") if isinstance(d, dict) else d.ok)}
    from alerts import templates
    return {
        "id": get("id"),
        # A row must never be un-insertable because its clock was not set. The
        # dispatcher stamps every event, so this is the second line of defence —
        # and `ts` is NOT NULL, so "no timestamp" is not an option the schema
        # offers.
        "ts": float(get("ts") or 0.0) or time.time(),
        "level_id": str(get("level_id") or ""),
        "node_id": str(get("node_id") or ""),
        "severity": str(get("severity") or "WATCH").upper(),
        "event_type": str(get("event_type") or "ALERT"),
        "progression_stage": get("progression_stage"),
        "ttf_seconds": get("ttf_seconds"),
        "ttf_q10": get("ttf_q10"),
        "ttf_q90": get("ttf_q90"),
        "disruption_label": get("disruption_label"),
        "disruption_index": get("disruption_index"),
        "geo_lat": float(get("geo_lat") or 0.0),
        "geo_lon": float(get("geo_lon") or 0.0),
        "recommended_action": str(get("recommended_action") or "")[:2000],
        "state": str(get("state") or "NEW"),
        "sms_dispatched": "sms" in ok,
        "email_dispatched": "email" in ok,
        "local_dispatched": "local" in ok,
        "dash_dispatched": "dashboard" in ok,
        "attempts": int(get("attempts") or 1),
        "last_error": (get("last_error") or None),
        "dedupe_key": get("dedupe_key") or "",
        "firewalled": bool(get("firewalled")),
        "escalated": bool(get("escalated")),
        "suppressed": bool(get("suppressed")),
        "suppress_reason": get("suppression_reason") or get("suppress_reason"),
        # The machine's own close (condition cleared) must land on the row exactly
        # the way a re-arm's does. Without this the two closes were indistinguishable
        # in the database — and the first live re-arm is what showed it.
        "closed_reason": get("closed_reason"),
        "reference": templates.reference(event) if get("node_id") else None,
    }


def insert_alert(event, deliveries=()) -> int | None:
    """Insert one alert and its delivery rows. Returns the new id, or None.

    Insert-then-update rather than upsert: the row is written the moment the alert
    exists, so an alert that fails to *persist* is still in memory and still on
    screen. Losing the alert because the database was slow is not a trade this
    system makes.
    """
    conn = try_get_conn()
    if conn is None:
        return None
    try:
        params = _alert_params(event, deliveries)
        with conn.cursor() as cur:
            cur.execute(INSERT_ALERT_SQL, params)
            alert_id = cur.fetchone()[0]
            for d in deliveries:
                row = d.as_row() if hasattr(d, "as_row") else d
                cur.execute(
                    "INSERT INTO alert_deliveries (alert_id, channel, ok, detail, queued, verified)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (alert_id, row.get("channel", "?"), bool(row.get("ok")),
                     (row.get("detail") or "")[:400],
                     bool(getattr(d, "queued", False)), bool(getattr(d, "verified", False))))
            # ── Phase 9 (§9.2 rule 1): the outbox row is written **in this same
            # transaction** as the alert itself. That is the entire difference
            # between a transactional outbox and a `try/except` around an HTTP
            # call: if the process dies here, either both writes happened or
            # neither did — the queue can never be missing an alert that exists,
            # nor hold a phantom that never did.
            #
            # `enqueue_outbox` returns None when the queue is at its cap. That is
            # a refusal, not a drop, and it says so out loud: a silent one would
            # be the only way this system could lose an alert end-to-end.
            try:
                from persistence import outbox as _outbox
                # `alert_id` is passed so the queue reference is unique to *this*
                # alert row (`NS-976A#41`), not merely to the incident reference —
                # see `alert_outbox_row`. A collision would merge two alerts into
                # one queued item.
                _row = _outbox.alert_outbox_row(event, alert_id)
                _ref = _row.get("ref")
                if _outbox.enqueue_outbox(conn, "alert", _row, ref=_ref) is None:
                    print(f"[outbox] FULL — alert {_ref} kept locally, NOT queued "
                          f"(cap {config.OUTBOX_MAX_ROWS}); raise it or force a sync")
            except Exception as _exc:                           # noqa: BLE001
                # An outbox failure must never lose the alert row itself: the
                # local record is written, so the incident is on screen and in
                # the database. The queue is behind; the console says so.
                print(f"[outbox] enqueue failed for alert: {type(_exc).__name__}: {_exc}")
        conn.commit()
        _clear_error("alerts")
        return int(alert_id)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("alerts", f"DB alert error: {e}")
        return None
    finally:
        release(conn)


def update_alert(event, deliveries=()) -> bool:
    """Re-write an alert's mutable columns (state, ledger flags, attempts)."""
    conn = try_get_conn()
    if conn is None:
        return False
    try:
        params = _alert_params(event, deliveries)
        if not params.get("id"):
            return False
        with conn.cursor() as cur:
            cur.execute(_UPDATE_ALERT_SQL, params)
            for d in deliveries:
                row = d.as_row() if hasattr(d, "as_row") else d
                cur.execute(
                    "INSERT INTO alert_deliveries (alert_id, channel, ok, detail, queued, verified)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (params["id"], row.get("channel", "?"), bool(row.get("ok")),
                     (row.get("detail") or "")[:400],
                     bool(getattr(d, "queued", False)), bool(getattr(d, "verified", False))))
        conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("alerts", f"DB alert update error: {e}")
        return False
    finally:
        release(conn)


def fetch_alerts(limit: int = 50, severity: str | None = None,
                 since: str | None = None, state: str | None = None) -> list[dict]:
    """The alert log, newest first. Paginated by ``limit`` (clamped to 1..500)."""
    conn = try_get_conn()
    if conn is None:
        return []
    limit = max(1, min(int(limit or 50), 500))
    where, params = [], []
    if severity:
        where.append("severity = %s")
        params.append(str(severity).upper())
    if state:
        where.append("state = %s")
        params.append(str(state).upper())
    if since:
        where.append("ts >= %s")
        params.append(since)
    sql = ("SELECT id, ts, level_id, node_id, severity, event_type, progression_stage,"
           " ttf_seconds, ttf_q10, ttf_q90, disruption_label, disruption_index,"
           " geo_lat, geo_lon, recommended_action, state, acked_by, acked_at,"
           " sms_dispatched, email_dispatched, local_dispatched, dash_dispatched,"
           " attempts, last_error, dedupe_key, firewalled, escalated, suppressed,"
           " suppress_reason, reference, closed_reason"
           " FROM alerts_history")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC, id DESC LIMIT %s"
    params.append(limit)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, tuple(params))
            cols = [c.name for c in cur.description]
            out = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                for key in ("ts", "acked_at"):
                    if rec.get(key) is not None:
                        rec[key] = rec[key].isoformat()
                out.append(rec)
            return out
    except Exception as e:
        _log_db_error("alerts", f"DB alert fetch error: {e}")
        return []
    finally:
        release(conn)


def fetch_deliveries(alert_id: int) -> list[dict]:
    """The per-channel ledger for one alert — the 'prove it arrived' view."""
    conn = try_get_conn()
    if conn is None:
        return []
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT channel, ok, detail, queued, verified, ts"
                        " FROM alert_deliveries WHERE alert_id = %s ORDER BY id", (int(alert_id),))
            cols = [c.name for c in cur.description]
            out = []
            for row in cur.fetchall():
                rec = dict(zip(cols, row))
                if rec.get("ts") is not None:
                    rec["ts"] = rec["ts"].isoformat()
                out.append(rec)
            return out
    except Exception as e:
        _log_db_error("alerts", f"DB delivery fetch error: {e}")
        return []
    finally:
        release(conn)


def ack_alert_row(alert_id: int, operator: str) -> dict:
    """Record a human acknowledgement. Returns the row's new state, or an error."""
    conn = try_get_conn()
    if conn is None:
        return {"ok": False, "error": "database unavailable"}
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE alerts_history SET state='ACKED', acked_by=%s, acked_at=NOW(),"
                        " updated_at=NOW() WHERE id=%s AND state <> 'CLOSED'"
                        " RETURNING state, acked_by, acked_at", (str(operator), int(alert_id)))
            row = cur.fetchone()
            if row is None:
                conn.rollback()
                return {"ok": False, "error": "no such open alert"}
            cur.execute("INSERT INTO alert_deliveries (alert_id, channel, ok, detail)"
                        " VALUES (%s, 'lifecycle', TRUE, %s)",
                        (int(alert_id), f"ACKED by {operator}"))
        conn.commit()
        return {"ok": True, "state": row[0], "acked_by": row[1],
                "acked_at": row[2].isoformat() if row[2] else None}
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("alerts", f"DB ack error: {e}")
        return {"ok": False, "error": str(e)}
    finally:
        release(conn)


def close_open_alerts(reason: str = "manual re-arm") -> int:
    """Close every open alert. **Never deletes** — §8.5, and it is not negotiable.

    Deleting an alert history is exactly the behaviour a regulator should never
    see: a Re-arm is an operator saying "I have seen this and I am resetting the
    site", not a machine agreeing that nothing happened.
    """
    conn = try_get_conn()
    if conn is None:
        return 0
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE alerts_history SET state='CLOSED', closed_reason=%s,"
                        " updated_at=NOW() WHERE state IN ('NEW','ACTIVE','ACKED')",
                        (str(reason),))
            n = cur.rowcount
        conn.commit()
        return int(n or 0)
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        _log_db_error("alerts", f"DB close error: {e}")
        return 0
    finally:
        release(conn)


def alert_exists(alert_id: int) -> bool:
    """Does this alert id exist? Used to tell "no ledger rows" from "no alert"."""
    conn = try_get_conn()
    if conn is None:
        return False
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM alerts_history WHERE id = %s", (int(alert_id),))
            return cur.fetchone() is not None
    except Exception:
        return False
    finally:
        release(conn)


def count_alerts() -> int | None:
    """How many incidents are on record. Used by ``--status`` and the drill report."""
    return count_rows("alerts_history")


def persist_alert(event, deliveries=()) -> int | None:
    """The dispatcher's `persist` callback: insert, or update when it already has an id.

    One function so the dispatcher never has to know whether this is the first
    write of an alert or the fifth — it just hands the event over again.
    """
    if getattr(event, "id", None):
        update_alert(event, deliveries)
        return event.id
    return insert_alert(event, deliveries)


def sync_pending_counts() -> dict:
    """Phase 9 — the chip's numbers, read from the rows themselves.

    Never from a remembered counter: a chip that prints what the daemon last
    thought is a chip that disagrees with the database exactly when it matters.

    Borrows and **returns** the connection: the first version of this function
    didn't, and a borrowed-never-returned connection is a leak that shows up
    somewhere else entirely — as a test that skips itself, a telemetry batch that
    silently does not land, or a sync cycle that quietly does nothing. The pool is
    four connections wide; one leak is a quarter of the budget.
    """
    from persistence import sync
    conn = try_get_conn()
    if conn is None:
        return {"rollups": 0, "alerts": 0, "outbox": 0, "total": 0, "db": False}
    try:
        out = sync.pending_counts(conn)
        out["db"] = True
        return out
    finally:
        release(conn)


def count_rows(table: str = "sensor_telemetry") -> int | None:
    """Row count for one of the known tables (used by tests and ``--status``)."""
    if table not in ("sensor_telemetry", "rfid_evacuation_log", "node_hourly",
                     "schema_migrations", "alerts_history", "alert_deliveries",
                     "outbox"):
        raise ValueError(f"refusing to count unknown table {table!r}")
    conn = try_get_conn()
    if conn is None:
        return None
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")   # noqa: S608 - allow-list above
            return int(cur.fetchone()[0])
    except Exception:
        return None
    finally:
        release(conn)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Neural Strata — database maintenance")
    ap.add_argument("--init", action="store_true", help="apply every pending migration")
    ap.add_argument("--status", action="store_true", help="show applied / pending / tables")
    ap.add_argument("--dump-schema", action="store_true",
                    help="regenerate persistence/schema.sql from the migrations")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.dump_schema:
        dump_schema(verbose=not args.quiet)
        return 0

    if args.init:
        applied = init_schema(verbose=not args.quiet)
        st = status()
        if not args.quiet:
            print(f"[db] {len(applied)} applied · "
                  f"{len(st['applied'])} total in ledger · tables: {', '.join(st['tables']) or '—'}")
        return 0 if st["connected"] else 1

    if args.status or not any([args.init, args.dump_schema]):
        st = status()
        print(f"[db] {st['host']}/{st['database']}  connected={st['connected']}")
        print(f"[db] applied : {st['applied'] or '—'}")
        print(f"[db] pending : {st['pending'] or '—'}")
        print(f"[db] tables  : {', '.join(st['tables']) or '—'}")
        if st.get("error"):
            print(f"[db] error   : {st['error']}")
        return 0 if st["connected"] else 1
    return 0


if __name__ == "__main__":
    sys.exit(_main())
