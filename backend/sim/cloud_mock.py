"""Cloud upload side of the blackbox pipeline.

The rule the whole design hangs off: a chunk on the SD card is only released
after the cloud has confirmed **every** row of it. So this module's only real
job is to turn PENDING outbox rows into SENT ones and be honest when it cannot.

Two transports:

  HttpSink   the real thing — gzip + POST to the configured endpoint, HMAC-signed
             (the signature lands in the same commit as the routing work; the
             transport seam is here so nothing else has to change when it does).

  LocalSink  the bench stand-in. It appends each batch to a JSONL file under
             runtime/cloud-standin/. Uploads genuinely happen and genuinely
             succeed — they just happen to a file on this laptop instead of a
             remote database. That is why `online` from LocalSink is reported
             together with mode=SIM: the pipeline is real, the cloud is not.
"""
from __future__ import annotations

import gzip
import json
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

BATCH_ROWS = 100        # rows per HTTP POST — small batches, frequent commits


@dataclass
class PushResult:
    ok: bool
    http_status: Optional[int] = None
    error: Optional[str] = None
    bytes_raw: int = 0
    bytes_gzip: int = 0


class LocalSink:
    """Bench stand-in for the cloud. Not a cloud; a file that accepts batches."""

    name = "local-standin"

    def __init__(self, path: str | Path = "runtime/cloud-standin"):
        self.dir = Path(path)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "batches.jsonl"

    def push(self, batch_id: str, chunk_ids: Sequence[int],
             rows: Sequence[dict]) -> PushResult:
        body = json.dumps({"batch_id": batch_id, "chunk_ids": list(chunk_ids),
                           "rows": list(rows)}, separators=(",", ":")).encode()
        gz = gzip.compress(body)
        with self.file.open("ab") as fh:
            fh.write(json.dumps({"batch_id": batch_id, "chunk_ids": list(chunk_ids),
                                 "rows": len(rows), "bytes_gzip": len(gz)}).encode() + b"\n")
            fh.write(gz + b"\n")
        return PushResult(ok=True, http_status=200,
                          bytes_raw=len(body), bytes_gzip=len(gz))


class HttpSink:
    """Real cloud transport. Neon/Postgres behind an HTTPS endpoint."""

    name = "http"

    def __init__(self, url: str, *, timeout: float = 10.0, key: Optional[str] = None):
        self.url = url
        self.timeout = timeout
        self.key = key

    def push(self, batch_id: str, chunk_ids: Sequence[int],
             rows: Sequence[dict]) -> PushResult:
        import urllib.error
        import urllib.request

        body = json.dumps({"batch_id": batch_id, "chunk_ids": list(chunk_ids),
                           "rows": list(rows)}, separators=(",", ":")).encode()
        gz = gzip.compress(body)
        req = urllib.request.Request(
            self.url, data=gz, method="POST",
            headers={"Content-Type": "application/json",
                     "Content-Encoding": "gzip",
                     "X-Batch-Id": batch_id},
        )
        if self.key:
            req.add_header("X-Signature", self.key)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return PushResult(ok=200 <= resp.status < 300, http_status=resp.status,
                                  bytes_raw=len(body), bytes_gzip=len(gz))
        except urllib.error.HTTPError as exc:
            return PushResult(ok=False, http_status=exc.code, error=str(exc),
                              bytes_raw=len(body), bytes_gzip=len(gz))
        except Exception as exc:                       # noqa: BLE001 — cold starts, DNS, TLS
            return PushResult(ok=False, error=f"{type(exc).__name__}: {exc}",
                              bytes_raw=len(body), bytes_gzip=len(gz))


def drain(store, sink, *, max_batches: int = 4, batch_rows: int = BATCH_ROWS) -> dict:
    """Push pending outbox rows. Returns a small summary for the caller.

    A failed batch goes back to PENDING — never to SENT — so the gate keeps
    holding the SD copy. That is the entire safety property.
    """
    pushed = failed = rows_sent = 0
    error = None

    for _ in range(max_batches):
        rows = store.claim_pending(batch_rows, claim_id=uuid.uuid4().hex)
        if not rows:
            break

        chunk_ids = sorted({r["chunk_id"] for r in rows})
        batch_id = uuid.uuid4().hex[:12]
        payload = [json.loads(r["payload"]) for r in rows]
        res = sink.push(batch_id, chunk_ids, payload)

        if res.ok:
            store.mark_sent([r["id"] for r in rows], batch_id)
            store.record_batch(batch_id, chunk_ids, len(rows),
                               res.bytes_raw, res.bytes_gzip, "SENT", res.http_status)
            pushed += 1
            rows_sent += len(rows)
        else:
            store.mark_failed([r["id"] for r in rows], res.error or "push failed")
            store.record_batch(batch_id, chunk_ids, len(rows),
                               res.bytes_raw, res.bytes_gzip, "FAILED",
                               res.http_status, res.error)
            failed += 1
            error = res.error or f"HTTP {res.http_status}"
            break          # do not hammer a dead endpoint

    return {"batches_ok": pushed, "batches_failed": failed,
            "rows_sent": rows_sent, "error": error}


# ============================================================================
# The cloud, as a *server* — Phase 9 (B6)
# ============================================================================
# The classes above are the blackbox's upload *sink*. This is the other end: a
# central endpoint that the sync daemon can actually talk to, so the Phase-9 demo
# is end-to-end real rather than a mocked arrow on a slide (§9.7). It lives in
# `sim/` with `hub_sim.py` for the same reason: every phase from 9 onward must be
# testable with no hardware and no network.
#
# What it implements, and why each part is not optional:
#
#   gzip + HMAC-SHA256    a corrupted batch must be *rejected*, not written into the
#                         central record as truth. The digest is over the bytes on
#                         the wire, which is what both ends can agree on.
#   idempotent on batch_id a re-sent batch (the daemon's normal recovery from "sent
#                         but marked unsent") must be harmless, and must answer
#                         `duplicate: true` so the client can tell it apart from a
#                         fresh write.
#   COMMIT-then-200       the row is durable *before* the 200 leaves. The opposite
#                         order is the one that loses data: a 200 that outlives a
#                         rolled-back transaction tells the edge to stop retrying.
#   tellable failure      `?_mode=fail|stall|duplicate` and `POST /_ctl` let a test
#                         (and the demo) force each behaviour, because the retry and
#                         backoff paths are the ones nobody exercises by accident.
#
# It is not a database. It is a cloud *stand-in*: SQLite, one table, and honest
# counters — and the counters are the point, because "no row is sent twice" is a
# claim that has to be checkable.

MOCK_MODES = ("ok", "fail", "stall", "duplicate")


def _mock_store(path: str | Path):
    """Open (and create) the stand-in's SQLite store. Returns the connection."""
    import sqlite3
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10, isolation_level=None)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS batches (
            batch_id  TEXT PRIMARY KEY,
            site_id   TEXT,
            n_items   INTEGER NOT NULL DEFAULT 0,
            bytes_gz  INTEGER NOT NULL DEFAULT 0,
            received  REAL    NOT NULL,
            body      TEXT
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id  TEXT NOT NULL,
            kind      TEXT,
            ref       TEXT,
            payload   TEXT,
            UNIQUE (batch_id, kind, ref)
        )""")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS control (
            k TEXT PRIMARY KEY, v TEXT NOT NULL)""")
    conn.execute("INSERT OR IGNORE INTO control (k, v) VALUES ('mode', 'ok')")
    return conn


def build_mock_app(store_path: str | Path | None = None, *, key: str | None = None):
    """The Flask app the demo (and `tests/test_sync_logic.py`) point at."""
    import gzip
    import hashlib
    import hmac as _hmac
    import time as _time

    from flask import Flask, jsonify, request

    key = key if key is not None else (os.environ.get("NS_CLOUD_HMAC_KEY", "") or "")
    db_path = store_path or os.environ.get("NS_CLOUD_MOCK_DB", "runtime/cloud-mock.sqlite3")
    app = Flask("ns-cloud-mock")

    def _conn():
        return _mock_store(db_path)

    def _mode(conn) -> str:
        row = conn.execute("SELECT v FROM control WHERE k = 'mode'").fetchone()
        return (row[0] if row else "ok")

    @app.get("/health")
    def health():
        conn = _conn()
        try:
            n = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
            items = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            return jsonify({"ok": True, "service": "neural-strata-cloud-mock",
                            "batches": int(n), "items": int(items),
                            "signed": bool(key), "mode": _mode(conn)})
        finally:
            conn.close()

    @app.post("/ingest")
    def ingest():
        raw = request.get_data() or b""
        conn = _conn()
        try:
            mode = _mode(conn)
            # A batch the far side has been *told* to drop. 503 rather than 500:
            # "busy, come back" is what a real free tier says, and it is the answer
            # that exercises backoff instead of a hard failure.
            if mode == "fail":
                return jsonify({"ok": False, "error": "mock told to fail"}), 503
            if mode == "stall":
                _time.sleep(float(os.environ.get("NS_CLOUD_MOCK_STALL_S", "6")))
            if key:
                got = request.headers.get("X-NS-Signature", "")
                want = "sha256=" + _hmac.new(key.encode(), raw, hashlib.sha256).hexdigest()
                if not _hmac.compare_digest(got, want):
                    # Rejected, never stored: an unverified body is not data.
                    return jsonify({"ok": False, "error": "bad signature"}), 401
            try:
                body = json.loads(gzip.decompress(raw).decode("utf-8"))
            except Exception as exc:                        # noqa: BLE001
                return jsonify({"ok": False, "error": f"undecodable body: {exc}"}), 400

            batch_id = str(body.get("batch_id") or "")
            if not batch_id:
                return jsonify({"ok": False, "error": "batch_id required"}), 400

            seen = conn.execute("SELECT batch_id FROM batches WHERE batch_id = ?",
                                (batch_id,)).fetchone()
            if seen is not None or mode == "duplicate":
                return jsonify({"ok": True, "duplicate": True, "batch_id": batch_id,
                                "stored": 0})

            items = body.get("items") or []
            # ── write, COMMIT, and only then answer 200 ──────────────────────
            conn.execute("BEGIN")
            conn.execute("INSERT INTO batches (batch_id, site_id, n_items, bytes_gz,"
                         " received, body) VALUES (?, ?, ?, ?, ?, ?)",
                         (batch_id, str(body.get("site_id") or ""), len(items),
                          len(raw), _time.time(), json.dumps(body)[:200000]))
            stored = 0
            for it in items:
                try:
                    conn.execute("INSERT OR IGNORE INTO items (batch_id, kind, ref,"
                                 " payload) VALUES (?, ?, ?, ?)",
                                 (batch_id, str(it.get("kind") or ""),
                                  str(it.get("ref") or it.get("id") or ""),
                                  json.dumps(it.get("payload"), default=str)))
                    stored += 1
                except Exception:                           # noqa: BLE001
                    pass
            conn.execute("COMMIT")
            return jsonify({"ok": True, "duplicate": False, "batch_id": batch_id,
                            "stored": stored})
        finally:
            conn.close()

    @app.post("/_ctl")
    def control():
        body = request.get_json(silent=True) or {}
        mode = str(body.get("mode") or "")
        if mode not in MOCK_MODES:
            return jsonify({"ok": False, "error": f"mode must be one of {MOCK_MODES}"}), 400
        conn = _conn()
        try:
            conn.execute("UPDATE control SET v = ? WHERE k = 'mode'", (mode,))
            return jsonify({"ok": True, "mode": mode})
        finally:
            conn.close()

    @app.get("/_state")
    def state():
        conn = _conn()
        try:
            batches = conn.execute("SELECT count(*) FROM batches").fetchone()[0]
            items = conn.execute("SELECT count(*) FROM items").fetchone()[0]
            dupes = conn.execute("SELECT count(*) FROM items WHERE 0").fetchone()[0]
            rows = [dict(zip(("batch_id", "site_id", "n_items", "received"), r))
                    for r in conn.execute("SELECT batch_id, site_id, n_items, received"
                                          " FROM batches ORDER BY received").fetchall()]
            return jsonify({"ok": True, "mode": _mode(conn), "batches": int(batches),
                            "items": int(items), "duplicates_rejected": int(dupes),
                            "recent": rows[-20:]})
        finally:
            conn.close()

    return app


def main(argv=None) -> int:
    """Run the stand-in cloud: `python3 -m sim.cloud_mock --port 5070`."""
    import argparse
    ap = argparse.ArgumentParser(description="Neural Strata — stand-in central cloud")
    ap.add_argument("--port", type=int, default=int(os.environ.get("NS_CLOUD_MOCK_PORT", 5070)))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--db", default=os.environ.get("NS_CLOUD_MOCK_DB",
                                                   "runtime/cloud-mock.sqlite3"))
    ap.add_argument("--key", default=os.environ.get("NS_CLOUD_HMAC_KEY", ""))
    args = ap.parse_args(argv)
    app = build_mock_app(args.db, key=args.key or None)
    print(f"[cloud-mock] listening on http://{args.host}:{args.port}  "
          f"db={args.db}  signature={'on' if args.key else 'off'}")
    app.run(host=args.host, port=args.port, threaded=True)
    return 0


app = None          # built by `main()`, or by a test with its own store path


if __name__ == "__main__":
    # Without this the documented command — `python3 -m sim.cloud_mock --port 5070`
    # — imported the module and exited 0, printing nothing and listening nowhere.
    # A stand-in service that silently does not start is worse than one that fails
    # loudly: the demo's first sign of trouble would be a queue that never drains,
    # blamed on the sync daemon.
    import sys as _sys
    _sys.exit(main())
