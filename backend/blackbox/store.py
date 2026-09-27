"""Truth store + outbox for the v5 backend. SQLite, portable to Postgres."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

SCHEMA = Path(__file__).with_name("schema.sql")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Store:
    def __init__(self, path: str = ":memory:"):
        self.conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA.read_text(encoding="utf-8"))

    def close(self):
        self.conn.close()

    def telemetry_ts_ms(self, chunk_id: int, seq: int) -> Optional[int]:
        """The stored clock for this stamp, or None if the pair was never kept."""
        row = self.conn.execute(
            "SELECT ts_ms FROM sensor_telemetry WHERE chunk_id=? AND seq=?",
            (int(chunk_id), int(seq)),
        ).fetchone()
        return None if row is None else int(row[0])

    def allocate_chunk_id(self) -> int:
        """The next chunk id this database has never used.

        A hub that restarts its counter must not land new samples on an id the
        cloud already has. Replay still uses the id on the wire.
        """
        row = self.conn.execute(
            "SELECT COALESCE(MAX(chunk_id), 0) FROM blackbox_chunks"
        ).fetchone()
        nxt = max(int(row[0]) + 1, int(getattr(self, "_alloc_floor", 0)) + 1)
        self._alloc_floor = nxt
        return nxt

    # ------------------------------------------------------------------ ingest
    def ingest_row(self, row, source: str = "live") -> bool:
        """Insert a telemetry row and enqueue it for the cloud, atomically.

        Returns True only when this row was genuinely new. The UNIQUE(chunk_id, seq)
        constraint is what makes a blackbox replay idempotent: replaying a chunk
        that is already in the DB inserts nothing and re-queues nothing.
        """
        now = utcnow()
        with self.conn:
            cur = self.conn.execute(
                """INSERT OR IGNORE INTO sensor_telemetry
                   (chunk_id, seq, ts_ms, received_at, source, node_id, level,
                    tilt_x, displacement_mm, acceleration, vib, micro, macro, flags)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (row.chunk_id, row.seq, row.ts_ms, now, source, row.node, row.level,
                 row.tilt, row.disp_mm, row.accel, row.vib, row.micro, row.macro,
                 row.flags),
            )
            fresh = cur.rowcount == 1

            self.conn.execute(
                """INSERT OR IGNORE INTO blackbox_chunks (chunk_id, state, opened_at)
                   VALUES (?, 'OPEN', ?)""",
                (row.chunk_id, now),
            )
            if fresh:
                # Count DISTINCT rows only. A replay must not inflate this number,
                # or the gate would refuse a chunk that is actually complete.
                self.conn.execute(
                    """UPDATE blackbox_chunks
                       SET received_rows = received_rows + 1,
                           opened_at = COALESCE(opened_at, ?)
                       WHERE chunk_id = ?""",
                    (now, row.chunk_id),
                )
            if fresh:
                self.conn.execute(
                    """INSERT OR IGNORE INTO cloud_outbox (chunk_id, seq, payload, status)
                       VALUES (?,?,?, 'PENDING')""",
                    (row.chunk_id, row.seq, row.to_payload()),
                )
        return fresh

    # ------------------------------------------------------------ chunk events
    def note_chunk_open(self, chunk_id: int):
        self.conn.execute(
            """INSERT OR IGNORE INTO blackbox_chunks (chunk_id, state, opened_at)
               VALUES (?, 'OPEN', ?)""",
            (chunk_id, utcnow()),
        )

    def note_chunk_close(self, chunk_id: int, expected_rows: int, dropped: int = 0):
        self.conn.execute(
            """UPDATE blackbox_chunks
               SET state = 'CLOSED', expected_rows = ?, dropped_rows = ?,
                   closed_at = COALESCE(closed_at, ?)
               WHERE chunk_id = ?""",
            (expected_rows, dropped, utcnow(), chunk_id),
        )
        self.conn.execute(
            """INSERT OR IGNORE INTO blackbox_chunks
               (chunk_id, state, expected_rows, dropped_rows, closed_at)
               VALUES (?, 'CLOSED', ?, ?, ?)""",
            (chunk_id, expected_rows, dropped, utcnow()),
        )

    def note_chunk_state(self, chunk_id: int, rows: int, dropped: int = 0,
                         closed: bool = False):
        """Reconciliation reply. Never lowers a number we already hold higher —
        a stale hub count must not un-close a chunk or erase rows."""
        self.conn.execute(
            """INSERT OR IGNORE INTO blackbox_chunks (chunk_id, state, opened_at)
               VALUES (?, 'OPEN', ?)""",
            (chunk_id, utcnow()),
        )
        if closed:
            self.conn.execute(
                """UPDATE blackbox_chunks
                   SET state='CLOSED', expected_rows=MAX(COALESCE(expected_rows,0), ?),
                       dropped_rows=MAX(dropped_rows, ?),
                       closed_at=COALESCE(closed_at, ?)
                   WHERE chunk_id = ?""",
                (rows, dropped, utcnow(), chunk_id),
            )
        else:
            self.conn.execute(
                "UPDATE blackbox_chunks SET dropped_rows = MAX(dropped_rows, ?) WHERE chunk_id = ?",
                (dropped, chunk_id),
            )

    def note_chunk_done(self, chunk_id: int, rows: int, dropped: int = 0) -> dict:
        """Merge a **reconciliation** reply. Monotonic: it may only ever add.

        §5.7: *"Counts are monotonic — a stale reconciliation may never lower a
        number we already hold, un-close a chunk, or erase rows."* This is not
        `note_chunk_close` with different plumbing; it is the opposite policy:

        * `expected_rows = MAX(existing, reply)`. A reply relaying an older, smaller
          count must not shrink the target the gate measures against — and a reply
          composed before the hub's final flush genuinely *is* smaller. Shrinking it
          would make a 12-row gap look complete and the file would be unlinked with
          those rows still only on the card.
        * `state` only ever moves OPEN -> CLOSED. A reply for a chunk we hold closed
          is the *normal* case on reconnect, not a correction.
        * `closed_at` is COALESCEd, so the 60 s safety margin counts from when we
          first learned the chunk was closed, not from the latest echo of it.
        * `dropped_rows = MAX(existing, reply)` — drops are permanent, and a hub
          that has rebooted must not be able to clear them by forgetting.

        Returns what actually changed: "the hub replied" and "the hub told us
        something new" are different facts, and the ledger wants the second.
        """
        before = self.chunk(chunk_id)
        created = before is None
        self.conn.execute(
            """INSERT OR IGNORE INTO blackbox_chunks (chunk_id, state, opened_at)
               VALUES (?, 'OPEN', ?)""",
            (chunk_id, utcnow()),
        )
        self.conn.execute(
            """UPDATE blackbox_chunks
               SET state = 'CLOSED',
                   expected_rows = MAX(COALESCE(expected_rows, 0), ?),
                   dropped_rows  = MAX(dropped_rows, ?),
                   closed_at     = COALESCE(closed_at, ?)
               WHERE chunk_id = ?""",
            (rows, dropped, utcnow(), chunk_id),
        )
        after = self.chunk(chunk_id)
        old_expected = 0 if created else int(before["expected_rows"] or 0)
        old_drops = 0 if created else int(before["dropped_rows"] or 0)
        return {
            "chunk_id": chunk_id,
            "created": created,
            "closed_now": bool(not created and before["state"] != "CLOSED"),
            "expected_raised": int(after["expected_rows"] or 0) > old_expected,
            "drops_raised": int(after["dropped_rows"] or 0) > old_drops,
            "expected_rows": after["expected_rows"],
        }

    def note_drop(self, chunk_id: int, count: int):
        self.conn.execute(
            """INSERT OR IGNORE INTO blackbox_chunks (chunk_id, state, opened_at)
               VALUES (?, 'OPEN', ?)""",
            (chunk_id, utcnow()),
        )
        self.conn.execute(
            "UPDATE blackbox_chunks SET dropped_rows = dropped_rows + ? WHERE chunk_id = ?",
            (count, chunk_id),
        )
        self.conn.execute(
            "UPDATE hub_status SET dropped_total = dropped_total + ? WHERE id = 1",
            (count,),
        )

    def note_hub_status(self, state: str, free_mb: Optional[int]):
        self.conn.execute(
            "UPDATE hub_status SET last_seen=?, blackbox_state=?, free_mb=? WHERE id=1",
            (utcnow(), state, free_mb),
        )

    def mark_hub_seen(self, **kw):
        sets, vals = ["last_seen=?"], [utcnow()]
        for k, v in kw.items():
            sets.append(f"{k}=?")
            vals.append(v)
        self.conn.execute(f"UPDATE hub_status SET {', '.join(sets)} WHERE id=1", vals)

    # ------------------------------------------------------------------- reads
    def chunk(self, chunk_id: int) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM blackbox_chunks WHERE chunk_id=?", (chunk_id,)
        ).fetchone()

    def chunks(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM blackbox_chunks ORDER BY chunk_id"
        ).fetchall()

    def count_pending(self, chunk_id: int) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM cloud_outbox WHERE chunk_id=? AND status != 'SENT'",
            (chunk_id,),
        ).fetchone()[0]

    def pending_total(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM cloud_outbox WHERE status != 'SENT'"
        ).fetchone()[0]

    def count_telemetry(self, chunk_id: Optional[int] = None) -> int:
        if chunk_id is None:
            return self.conn.execute("SELECT COUNT(*) FROM sensor_telemetry").fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM sensor_telemetry WHERE chunk_id=?", (chunk_id,)
        ).fetchone()[0]

    def count_outbox(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM cloud_outbox").fetchone()[0]

    def sent_total(self) -> int:
        """Rows the cloud has actually confirmed. Drives the deck's 'sent' counter."""
        return self.conn.execute(
            "SELECT COUNT(*) FROM cloud_outbox WHERE status = 'SENT'"
        ).fetchone()[0]

    def last_batch(self) -> Optional[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cloud_batches ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

    def claim_pending(self, limit: int, claim_id: str) -> list[sqlite3.Row]:
        """Take a slice of the outbox for one upload attempt.

        Claims are stamped so that a crash mid-upload leaves the rows claimable
        again rather than silently stuck in PENDING forever.
        """
        rows = self.conn.execute(
            "SELECT * FROM cloud_outbox WHERE status = 'PENDING' "
            "ORDER BY chunk_id, seq LIMIT ?", (limit,)
        ).fetchall()
        if rows:
            self.conn.executemany(
                "UPDATE cloud_outbox SET claimed_at=?, claim_id=?, attempts=attempts+1 "
                "WHERE id=?",
                [(utcnow(), claim_id, r["id"]) for r in rows],
            )
        return rows

    def mark_sent(self, outbox_ids: list[int], batch_id: str):
        now = utcnow()
        self.conn.executemany(
            "UPDATE cloud_outbox SET status='SENT', sent_at=?, batch_id=?, claim_id=NULL "
            "WHERE id=?",
            [(now, batch_id, i) for i in outbox_ids],
        )

    def mark_failed(self, outbox_ids: list[int], error: str):
        self.conn.executemany(
            "UPDATE cloud_outbox SET status='PENDING', claim_id=NULL, last_error=? "
            "WHERE id=?",
            [(error, i) for i in outbox_ids],
        )

    def record_batch(self, batch_id: str, chunk_ids: list[int], rows: int,
                     raw: int, gzipd: int, status: str,
                     http_status: Optional[int] = None, error: Optional[str] = None):
        now = utcnow()
        self.conn.execute(
            """INSERT OR REPLACE INTO cloud_batches
               (batch_id, chunk_ids, row_count, bytes_raw, bytes_gzip, status,
                attempts, created_at, completed_at, http_status, last_error)
               VALUES (?,?,?,?,?,?,1,?,?,?,?)""",
            (batch_id, ",".join(str(c) for c in chunk_ids), rows, raw, gzipd, status,
             now, utcnow() if status in ("SENT", "FAILED") else None,
             http_status, error),
        )

    def mark_ack_sent(self, chunk_id: int, reason: str):
        self.conn.execute(
            "UPDATE blackbox_chunks SET ack_sent_at=?, ack_reason=? WHERE chunk_id=?",
            (utcnow(), reason, chunk_id),
        )

    def mark_recover_requested(self, chunk_ids: Iterable[int]):
        now = utcnow()
        for cid in chunk_ids:
            self.conn.execute(
                "UPDATE blackbox_chunks SET recover_at=? WHERE chunk_id=?", (now, cid)
            )

    def hub(self) -> sqlite3.Row:
        return self.conn.execute("SELECT * FROM hub_status WHERE id=1").fetchone()
