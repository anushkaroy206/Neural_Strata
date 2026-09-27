"""
Pytest bootstrap.
=================

Puts ``backend/`` on ``sys.path`` so the tests import the modules exactly the way
``app.py`` does (``import config``, ``from engine import ttf``), and points the
process at the repo's ``runtime/`` folder so a test run never writes elsewhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
# `backend/` for config/contracts/engine, `backend/blackbox/` for the interim
# gateway modules (protocol, gate, ingest, store, reader, status) that the
# relocated black-box tests import by bare name. The repo root is required too:
# route modules say `from backend.state import SystemState`.
for _p in (BACKEND, BACKEND / "blackbox", ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Deterministic test environment — set before `config` is imported.
os.environ.setdefault("NS_RUNTIME_DIR", str(BACKEND.parent / "runtime"))
# A card in /Volumes must not become the archive for a unit test. The live
# server discovers that volume on purpose; a test Gateway must not read or
# delete its chunk files.
os.environ.setdefault("NS_BBX_LAPTOP_VOLUME", str(BACKEND.parent / "runtime" / "no-such-card"))
os.environ.setdefault("NS_CASCADE_MODE", "advisory")
#: The suite runs against the same database as a live edge server, so the
#: connection pool can be busy for a moment. Phase 9 added a bounded wait for
#: exactly this, and the suite asks for a generous one: without it six sync tests
#: skipped themselves as "edge database unreachable" inside a run that reported
#: 652 passes — a green result with a hole in it, which is the worst kind.
os.environ.setdefault("NS_DB_POOL_WAIT_S", "5")


def pytest_sessionfinish(session, exitstatus):          # noqa: ARG001
    """Say how the pool ended the run.

    A suite that skips tests because it could not borrow a connection *looks*
    green, and the reason it could not borrow is either "the database is down"
    (fine to skip) or "we leaked" (not fine). Printing the pool at the end is what
    tells the two apart without a debugger.
    """
    try:
        sys.path.insert(0, str(BACKEND))
        from persistence import db
        stats = db.pool_stats()
        print(f"\n[dbtest] pool at exit: {stats}")
        if stats.get("borrowed"):
            # Which *backend* is each held connection, and what did it last do?
            # A leak is invisible from the Python side — `_used` has no owner
            # recorded — but postgres knows every query it ever ran, which is
            # enough to name the call site.
            import psycopg2
            pool = db._pool
            used = getattr(pool, "_used", {}) or {}
            # `_used` is keyed by **thread**, not by connection: psycopg2's pool
            # hands a thread its own connection and expects the same thread to
            # give it back. So the key names the leak: a short-lived thread that
            # borrowed and never returned.
            print(f"[dbtest] held by threads: {[getattr(k, 'name', k) for k in used]}")
            pids = []
            for c in used.values():
                try:
                    pids.append(c.get_backend_pid())
                except Exception:                       # noqa: BLE001
                    pass
            if pids:
                conn = psycopg2.connect(host=db.DB_HOST, dbname=db.DB_NAME,
                                        user="postgres", password=db.DB_PASS,
                                        port=5432)
                with conn.cursor() as cur:
                    cur.execute("SELECT pid, state, left(query, 90) FROM pg_stat_activity"
                                " WHERE pid = ANY(%s)", (pids,))
                    for row in cur.fetchall():
                        print(f"[dbtest] held conn pid={row[0]} state={row[1]} last={row[2]!r}")
                conn.close()
    except Exception as exc:                            # noqa: BLE001
        print(f"[dbtest] pool probe failed: {type(exc).__name__}: {exc}")
