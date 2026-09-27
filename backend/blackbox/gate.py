"""The delete gate — the single most dangerous decision in the system.

A remote file on an SD card inside a mine is the last copy of data that exists
nowhere else. Asking the hub to unlink it is irreversible. So the gate is
deliberately paranoid and it is a pure function: nothing here touches the hub,
it only decides whether the caller is allowed to send ACK_DELETE.

Five conditions, all required. The addendum had only #5.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

SAFETY_MARGIN_S = 60.0   # a chunk must be closed for this long before we unlink


@dataclass(frozen=True)
class ChunkView:
    chunk_id: int
    state: str                      # OPEN | CLOSED
    expected_rows: Optional[int]    # hub's own count, from CHUNK_CLOSE
    received_rows: int              # what actually landed in the DB
    dropped_rows: int               # hub-reported queue overflow
    closed_at: Optional[str]
    ack_sent_at: Optional[str]
    pending_outbox: int             # rows not yet confirmed by the cloud
    db_rows: Optional[int] = None   # live COUNT(*) of the telemetry table


@dataclass(frozen=True)
class Decision:
    chunk_id: int
    allowed: bool
    reason: str


def _age_seconds(closed_at: Optional[str], now: Optional[datetime] = None) -> float:
    if not closed_at:
        return 0.0
    now = now or datetime.now(timezone.utc)
    closed = datetime.fromisoformat(closed_at)
    if closed.tzinfo is None:
        closed = closed.replace(tzinfo=timezone.utc)
    return (now - closed).total_seconds()


def evaluate(chunk: ChunkView, *, safety_margin_s: float = SAFETY_MARGIN_S,
             now: Optional[datetime] = None) -> Decision:
    cid = chunk.chunk_id

    if chunk.ack_sent_at:
        return Decision(cid, False, "already acknowledged")
    if chunk.state != "CLOSED":
        return Decision(cid, False, "chunk is still open — the hub is writing to it")
    if chunk.expected_rows is None:
        return Decision(cid, False, "no CHUNK_CLOSE seen — row count unknown")
    if chunk.received_rows != chunk.expected_rows:
        return Decision(
            cid, False,
            f"row count mismatch: hub says {chunk.expected_rows}, we hold "
            f"{chunk.received_rows} — {chunk.expected_rows - chunk.received_rows} "
            f"rows would be lost",
        )
    if chunk.db_rows is not None and chunk.db_rows != chunk.received_rows:
        return Decision(
            cid, False,
            f"internal inconsistency: ingest counted {chunk.received_rows} rows but "
            f"the table holds {chunk.db_rows} — refusing to delete until reconciled",
        )
    if chunk.dropped_rows > 0:
        return Decision(
            cid, False,
            f"hub dropped {chunk.dropped_rows} rows in this chunk — the SD copy is "
            f"the only record of them, keep the file",
        )
    if chunk.pending_outbox > 0:
        return Decision(cid, False, f"{chunk.pending_outbox} rows not yet confirmed by the cloud")
    age = _age_seconds(chunk.closed_at, now)
    if age < safety_margin_s:
        return Decision(cid, False, f"closed {age:.0f}s ago, safety margin is {safety_margin_s:.0f}s")
    return Decision(cid, True, "closed, complete, undropped, fully synced")


def evaluate_all(chunks: Sequence[ChunkView], **kw) -> list[Decision]:
    return [evaluate(c, **kw) for c in chunks]


def from_row(row, pending_outbox: int, db_rows: Optional[int] = None) -> ChunkView:
    """Adapt a blackbox_chunks DB row (sqlite3.Row or dict) into a ChunkView."""
    g = (row.__getitem__ if hasattr(row, "__getitem__") else lambda k: None)
    return ChunkView(
        chunk_id=g("chunk_id"),
        state=g("state"),
        expected_rows=g("expected_rows"),
        received_rows=g("received_rows"),
        dropped_rows=g("dropped_rows"),
        closed_at=g("closed_at"),
        ack_sent_at=g("ack_sent_at"),
        pending_outbox=pending_outbox,
        db_rows=db_rows,
    )
