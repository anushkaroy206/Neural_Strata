"""
§5.7 (B15) — the reconnect handshake and the **monotonic** count merge.
=========================================================================

The problem this file exists for, in one paragraph
--------------------------------------------------
A laptop that loses its USB link mid-chunk comes back holding a truth store that
disagrees with the card in an underground box. Some rows were written to the card
and never reached the laptop; some rows reached the laptop and were never written
to the card; and neither side knows which without asking. So on **every** reconnect
the laptop sends `STATE?` and the hub answers with what it actually has:

    CHUNK_STATE:<open>,<rows>,<dropped>          — the chunk being written now
    CHUNK_DONE:<chunk>,<rows>,<dropped>          — one per closed, unacknowledged file

The replies are the hub's *record*, not a correction, and the rule that makes them
safe to accept is §5.7's:

    Counts are monotonic — a stale reconciliation may never lower a number we
    already hold, un-close a chunk, or erase rows.

That rule is why replies are merged by `store.note_chunk_done()` and **not** through
the live `CHUNK_CLOSE` path. A reply describing a chunk as it was ninety seconds ago
will name a smaller row count than the laptop holds, because the hub has written
more since; merging it live would shrink `expected_rows`, make a real gap look
complete, and release the file that holds the only copy of the difference. The
monotonic merge refuses to move any number backwards. `CHUNK_CLOSE` keeps the
opposite policy on purpose — a live close is the hub's word *now* and may correct
anything, because nothing has superseded it.

What this module adds on top of that merge
------------------------------------------
The merge is in `store.py` (one write path, no exceptions). This module owns the
*conversation*:

  · when to ask (`on_reconnect`, rate-limited so a flapping cable cannot spam the
    hub's UART),
  · what each reply meant (`note_reply` → per-chunk "new fact" bookkeeping),
  · and the resulting **gap inventory** (`gaps()`) — every closed chunk whose rows
    are not all here — which is what `recovery.py` turns into REPLAY requests and
    what the deck's ledger shows.

`gaps()` is the seam between mitigation and repair. Keeping it here rather than in
recovery.py is deliberate: "what is missing" is a fact about the conversation with
the hub, and "ask for it again" is a decision. Facts outlive decisions, and the
dashboard needs to show the first whether or not anyone acts on the second.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import protocol as P
from store import Store

#: Minimum spacing between two `STATE?` asks. A reconnect is an event, but a
#: connector on a windy bench can produce several in a few seconds, and every ask
#: makes the hub compose a reply per unacknowledged file. Five seconds is under the
#: firmware's own 10 s upkeep cadence, so no reply is ever skipped — the limit is
#: there to stop a *storm*, not to pace normal use.
HANDSHAKE_MIN_GAP_S = 5.0


@dataclass(frozen=True)
class Gap:
    """One closed chunk whose rows are not all in the truth store.

    `missing` is what REPLAY can actually bring back, and it is worth being precise
    about that because the two ways a chunk can be short are **not** the same:

        expected_rows   rows the hub wrote to the card        (its own count)
        received_rows   rows that reached this laptop
        missing         expected - received  → on the card, replayable
        dropped_rows    rows that never reached the card at all → gone for good

    Drops are not part of `expected_rows`: they were lost in the hub's queue before
    a file ever saw them. So a chunk with drops still has a perfectly replayable
    `missing` — the two facts are independent, and a recovery path that conflated
    them would refuse to repair damage it can repair. (It also means a chunk that
    has been fully back-filled still fails the delete gate while `dropped_rows > 0`,
    which is correct: the *file* is not deletable, but its *rows* are still worth
    having on the surface.)
    """
    chunk_id: int
    expected: int
    received: int
    dropped: int
    missing: int
    closed_at: Optional[str]

    @property
    def replayable(self) -> bool:
        return self.missing > 0

    @property
    def note(self) -> str:
        if self.dropped:
            return (f"{self.missing} row(s) replayable from the card · "
                    f"{self.dropped} lost in the hub's queue and not on it at all")
        return f"{self.missing} row(s) on the card, not here"


@dataclass
class Handshake:
    """What the last reconnect exchange looked like. Shown on the deck."""
    asked_at: Optional[float] = None
    asks: int = 0
    state_replies: int = 0
    done_replies: int = 0
    last_reply_at: Optional[float] = None
    new_facts: int = 0                              # replies that told us something new
    merged: dict = field(default_factory=dict)      # chunk_id -> store.note_chunk_done result


class Reconciler:
    """The laptop's half of §5.7's handshake, plus the gap inventory it produces."""

    def __init__(self, store: Store, *, log: Optional[Callable] = None,
                 min_gap_s: float = HANDSHAKE_MIN_GAP_S,
                 clock: Callable[[], float] = time.monotonic):
        self.store = store
        self.log = log or (lambda *_: None)
        self.min_gap_s = float(min_gap_s)
        self._clock = clock
        self.handshake = Handshake()
        #: Every reply we have merged, newest last — the audit trail a reviewer asks
        #: for ("the hub said what, when?") without having to re-run the demo.
        self.replies: list[dict] = []

    # ------------------------------------------------------------------- asking
    def on_reconnect(self, emit: Callable[[str], None], *, force: bool = False) -> bool:
        """Send `STATE?` — once per reconnect, rate-limited. Returns True if sent.

        Called from the one place that knows the line came back
        (`Gateway.attach_writer`, which the serial reader invokes when the port
        opens). `force=True` is the manual path — `POST /api/blackbox/recover`
        before a demo, or an operator saying "ask it again".
        """
        now = self._clock()
        if not force and self.handshake.asked_at is not None \
                and (now - self.handshake.asked_at) < self.min_gap_s:
            return False
        self.handshake.asked_at = now
        self.handshake.asks += 1
        emit(P.format_state_query())
        self.log(f"STATE? sent (#{self.handshake.asks}) — reconciling with the hub")
        return True

    # ----------------------------------------------------------------- answering
    def note_reply(self, msg, merge: Optional[dict] = None) -> dict:
        """Record one `CHUNK_STATE` / `CHUNK_DONE` reply.

        `merge` is the `store.note_chunk_done` result when the Ingestor merged a
        `CHUNK_DONE`; it is what lets this method distinguish *"the hub answered"*
        from *"the hub told us something we did not have"*. The second is the only
        one worth a log line, and the distinction is why the store returns a dict
        instead of None.
        """
        now = self._clock()
        self.handshake.last_reply_at = now
        if isinstance(msg, P.ChunkDone):
            self.handshake.done_replies += 1
        elif isinstance(msg, P.ChunkState):
            self.handshake.state_replies += 1

        fact = {"kind": type(msg).__name__, "chunk_id": msg.chunk_id, "rows": msg.rows,
                "dropped": getattr(msg, "dropped", 0), "at": now,
                "closed": getattr(msg, "closed", True), "new": False}
        if merge is not None:
            self.handshake.merged[msg.chunk_id] = merge
            fact["new"] = bool(merge.get("closed_now") or merge.get("expected_raised")
                               or merge.get("drops_raised"))
            fact["created"] = bool(merge.get("created"))
        if fact["new"]:
            self.handshake.new_facts += 1
            self.log(f"reconciled chunk {msg.chunk_id}: the hub had "
                     f"{msg.rows} rows we did not know about")
        self.replies.append(fact)
        del self.replies[:-64]                      # a rolling window, not a log file
        return fact

    # -------------------------------------------------------------------- gaps
    def gaps(self, *, include_open: bool = False) -> list[Gap]:
        """Closed chunks that are short of rows, newest last.

        Not here, on purpose:

          · **open** chunks — the hub is still writing; a count taken now is a
            snapshot of something in motion, and asking for a replay of a file that
            is being appended to would race the writer for no benefit.
          · chunks with **no `expected_rows`** — nothing to measure a gap against.
            Absence of a count is not evidence of completeness (§5.6 says the same
            thing about the delete gate).
          · chunks already **acknowledged** — the file is gone from the card, so
            there is nothing left to replay.
        """
        out: list[Gap] = []
        for row in self.store.chunks():
            if not include_open and row["state"] != "CLOSED":
                continue
            if row["ack_sent_at"]:
                continue
            expected = row["expected_rows"]
            if expected is None:
                continue
            received = int(row["received_rows"] or 0)
            if received >= int(expected):
                continue
            out.append(Gap(chunk_id=int(row["chunk_id"]), expected=int(expected),
                           received=received, dropped=int(row["dropped_rows"] or 0),
                           missing=int(expected) - received,
                           closed_at=row["closed_at"]))
        return out

    def total_missing(self) -> int:
        return sum(g.missing for g in self.gaps())

    # ------------------------------------------------------------------ surface
    def report(self) -> dict:
        """The handshake part of the `recovery` block (§5.8's ledger feeds on it)."""
        h = self.handshake
        now = self._clock()
        return {
            "asks": h.asks,
            "state_replies": h.state_replies,
            "done_replies": h.done_replies,
            "new_facts": h.new_facts,
            "last_ask_s": (round(now - h.asked_at, 1) if h.asked_at is not None else None),
            "last_reply_s": (round(now - h.last_reply_at, 1)
                             if h.last_reply_at is not None else None),
            "gaps": [{"chunk_id": g.chunk_id, "expected": g.expected,
                      "received": g.received, "missing": g.missing,
                      "dropped": g.dropped, "note": g.note} for g in self.gaps()],
            "missing_rows": self.total_missing(),
        }

    def reconcile_summary(self) -> str:
        """One sentence for a log line or a viva answer."""
        g = self.gaps()
        if not g:
            return "no gaps — the surface holds every row the hub reported"
        return (f"{len(g)} chunk(s) short of {self.total_missing()} row(s) "
                f"({', '.join(str(x.chunk_id) for x in g[:4])}"
                f"{'…' if len(g) > 4 else ''})")
