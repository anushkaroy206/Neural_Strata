"""Ingest loop: one USB serial line at a time, hub -> truth store + outbox."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Iterable, Optional

import protocol as P
from gate import ChunkView, Decision, evaluate, from_row
from status import HUB_ALIVE_S
from store import Store, utcnow

#: How long a hub may be silent before we stop releasing anything. The same
#: window `status.py` shows as "hub alive", so the panel and the gate can never
#: disagree about how dead a hub is.
HUB_STALE_S = HUB_ALIVE_S


@dataclass
class IngestStats:
    lines: int = 0
    rows: int = 0
    duplicates: int = 0
    unknown: int = 0
    done_replies: int = 0                          # CHUNK_DONE merges seen
    state_replies: int = 0                         # CHUNK_STATE replies seen
    messages: int = 0                              # recognised informational lines
    last_file: Optional[tuple] = None              # (chunk_id, bytes, dropped)
    last_unknown: Optional[str] = None
    to_hub: list = field(default_factory=list)     # lines the caller must write back


class Ingestor:
    """Deliberately single-threaded and synchronous: the reader thread owns it.

    Every branch here is boring on purpose. A safety ingest path that throws is
    worse than one that counts a bad line and carries on.
    """

    def __init__(self, store: Store, *, ack_delete: bool = True, log=None, card=None):
        self.store = store
        self.ack_delete = ack_delete
        self.card = card
        self.log = log or (lambda *_: None)
        self.stats = IngestStats()
        #: Hub chunk id -> a fresh id, once the hub's number is already taken.
        self._live_chunk: dict[int, int] = {}
        #: The last merge a `CHUNK_DONE` produced (`store.note_chunk_done`) — the
        #: reconciler reads it to tell "replied" from "told us something new".
        self.last_merge: Optional[dict] = None

    # ---------------------------------------------------------------- one line
    def handle(self, raw) -> P.Message:
        """Accepts a raw wire line or an already-parsed message."""
        self.stats.lines += 1
        msg = raw if not isinstance(raw, str) else P.parse_line(raw)

        if isinstance(msg, P.TelemetryRow):
            # The row says where it came from (`protocol.TelemetryRow.source`), and
            # the store keeps that: a back-filled row is a different fact from a
            # live one, and §5.7's acceptance test asks exactly that question.
            msg = self._place_live_chunk(msg)
            fresh = self.store.ingest_row(msg, source=msg.source)
            if fresh:
                self.stats.rows += 1
                if self.card is not None:
                    self.card.append(msg)
            else:
                self.stats.duplicates += 1
            # #region agent log
            seen = getattr(self, "_dbg_row_left", 8)
            if seen > 0:
                self._dbg_row_left = seen - 1
                try:
                    import json as _json, time as _time
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d", "hypothesisId": "SD2",
                            "runId": "post-fix",
                            "location": "ingest.py:handle",
                            "message": "telemetry row reached the card writer",
                            "data": {"fresh": fresh, "chunk": int(msg.chunk_id),
                                     "seq": int(msg.seq), "node": int(msg.node),
                                     "source": msg.source},
                            "timestamp": int(_time.time() * 1000),
                        }) + "\n")
                except Exception:
                    pass
            # #endregion
            self.store.mark_hub_seen()
            return msg

    def _place_live_chunk(self, msg: P.TelemetryRow) -> P.TelemetryRow:
        """Keep a restarted hub off chunk numbers the cloud already stored.

        A replay of the same clock is still a duplicate. A live sample with a
        new clock on a used `(chunk, seq)` is a new reading, so it moves onto
        the next unused chunk id and can be uploaded.
        """
        if msg.source == "replay":
            return msg
        hub = int(msg.chunk_id)
        target = self._live_chunk.get(hub, hub)
        seen = self.store.telemetry_ts_ms(target, msg.seq)
        if seen is not None and seen != int(msg.ts_ms):
            target = self.store.allocate_chunk_id()
            self._live_chunk[hub] = target
        if target == int(msg.chunk_id):
            return msg
        return replace(msg, chunk_id=target)

        if isinstance(msg, P.ChunkOpen):
            self.store.note_chunk_open(msg.chunk_id)
            self.log(f"chunk {msg.chunk_id} opened")

        elif isinstance(msg, P.ChunkClose):
            self.store.note_chunk_close(msg.chunk_id, msg.rows, msg.dropped)
            self.log(f"chunk {msg.chunk_id} closed: {msg.rows} rows, {msg.dropped} dropped")

        elif isinstance(msg, P.ChunkDone):
            # A reconciliation reply: merged monotonically by the store, never
            # treated as a live close (§5.7).
            self.stats.done_replies += 1
            self.last_merge = self.store.note_chunk_done(msg.chunk_id, msg.rows, msg.dropped)
            self.log(f"chunk {msg.chunk_id} DONE reply: {msg.rows} rows, "
                     f"{msg.dropped} dropped")

        elif isinstance(msg, P.ChunkState):
            self.stats.state_replies += 1
            self.store.note_chunk_state(msg.chunk_id, msg.rows, msg.dropped, msg.closed)
            self.log(f"chunk {msg.chunk_id} state: {msg.rows} rows"
                     f"{' closed' if msg.closed else ' open'}")

        elif isinstance(msg, P.ChunkFile):
            # A recognised informational line — see `protocol.ChunkFile`. Counted
            # apart from `unknown` on purpose: the whole value of the unknown
            # counter is that it stays at zero on a healthy hub.
            self.stats.messages += 1
            self.stats.last_file = (msg.chunk_id, msg.bytes, msg.dropped)
            self.log(f"chunk {msg.chunk_id} file closed: {msg.bytes} B, "
                     f"{msg.dropped} dropped")

        elif isinstance(msg, P.BlackboxStatus):
            self.store.note_hub_status(msg.state, msg.free_mb)
            self.log(f"blackbox {msg.state} ({msg.free_mb} MB free)")

        elif isinstance(msg, P.DropNotice):
            self.store.note_drop(msg.chunk_id, msg.count)
            self.log(f"hub DROPPED {msg.count} rows in chunk {msg.chunk_id}")

        elif isinstance(msg, P.Unknown):
            self.stats.unknown += 1
            self.stats.last_unknown = f"{msg.reason} :: {msg.raw[:80]}"
            # #region agent log
            raw = msg.raw or ""
            if '"node"' in raw:
                left = getattr(self, "_dbg_node_left", 6)
                if left > 0:
                    self._dbg_node_left = left - 1
                    try:
                        import json as _json, time as _time
                        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps({
                                "sessionId": "83ae1d", "hypothesisId": "SD",
                                "location": "ingest.py:handle",
                                "message": "node line not stored on the laptop card",
                                "data": {"reason": msg.reason, "has_chunk": "chunk_id" in raw,
                                         "head": raw[:90]},
                                "timestamp": int(_time.time() * 1000),
                            }) + "\n")
                    except Exception:
                        pass
            # #endregion

        return msg

    # ------------------------------------------------------------- stream loop
    def run(self, lines: Iterable[str], *, auto_ack_every: Optional[int] = None) -> IngestStats:
        for i, line in enumerate(lines, 1):
            self.handle(line)
            if auto_ack_every and i % auto_ack_every == 0:
                for cmd in self.sweep_acks():
                    self.stats.to_hub.append(cmd)
        return self.stats

    # ------------------------------------------------------------- the hub guard
    def hub_delete_ok(self) -> tuple[bool, str]:
        """May we ask *that hub, now* to unlink a file?

        **This is not one of the five conditions, and it is deliberately kept
        outside `gate.py`.** The gate answers "is this chunk's data safe
        everywhere it needs to be"; it is a pure function over one chunk and knows
        nothing about radios, cards or clocks. This answers a different question —
        "is the card we would be deleting from even there" — and it is about the
        *hub*, not the chunk. Folding it in would have made the five conditions
        six, and the whole point of §5.6 is that a reviewer can check the list.

        Why it has to exist at all: `sweep_acks` marks a chunk acknowledged the
        moment it emits the line, and the gate refuses an acknowledged chunk
        forever after ("already acknowledged"). If the hub has no card — or has
        stopped answering — the line goes nowhere, the file stays on the card, and
        the store now believes it is gone. The card fills up, and the chunk that
        was never removed is *never offered again*. One unplugged demo SD card is
        enough to plant that, permanently, in the truth store. So: nothing is
        asked of a hub that is not reporting OK, and nothing is marked until the
        request has actually gone out on the wire.

        Returns (ok, why) — the reason is shown, like every other refusal.
        """
        row = self.store.hub()
        state = (row["blackbox_state"] or "UNKNOWN") if row is not None else "UNKNOWN"
        if state != "OK":
            return False, (f"hub reports {state} — no delete sent "
                           f"(a card we cannot see is not a backup)")
        seen = row["last_seen"] if row is not None else None
        if not seen:
            return False, "hub has not reported yet — no delete sent"
        try:
            age = (datetime.fromisoformat(utcnow())
                   - datetime.fromisoformat(str(seen))).total_seconds()
        except ValueError:
            return False, f"hub clock unreadable ({seen!r}) — no delete sent"
        if age < 0:
            age = 0.0                    # a row stamped in the future is not stale
        if age > HUB_STALE_S:
            return False, (f"hub silent for {age:.0f}s — no delete sent "
                           f"(a stale hub cannot confirm a delete)")
        return True, "ok"

    # ----------------------------------------------------------------- the gate
    def sweep_acks(self) -> list[str]:
        """Ask the gate about every chunk; return the ACK_DELETE lines we are
        allowed to send. Called on a timer, never from the row hot path.

        Two gates in series, in this order: **the five conditions**, then the hub
        guard above. A chunk only becomes `ack_sent` when a line actually leaves
        the machine.
        """
        out: list[str] = []
        hub_ok, hub_why = self.hub_delete_ok()
        for row in self.store.chunks():
            cid = row["chunk_id"]
            view: ChunkView = from_row(row, self.store.count_pending(cid),
                                       db_rows=self.store.count_telemetry(cid))
            d: Decision = evaluate(view)
            if not d.allowed:
                continue
            if not hub_ok:
                # Held, not released — and *not* marked, so the moment the card
                # comes back this chunk is offered again.
                self.log(f"ACK_DELETE chunk {cid} withheld — {hub_why}")
                continue
            self.store.mark_ack_sent(d.chunk_id, d.reason)
            out.append(P.format_ack_delete(d.chunk_id))
            self.log(f"ACK_DELETE chunk {d.chunk_id} — {d.reason}")
        return out

    def sweep_reasons(self) -> dict[int, str]:
        """Why each held chunk is being held. This is what the dashboard shows.

        A chunk that passes all five conditions while the hub is unreachable is
        reported as `HUB: …` — it is held by the guard, not by the gate, and an
        operator debugging "why is nothing deleting" needs to be told which of the
        two is refusing. A reason that conflates them sends people to the wrong
        subsystem every time.
        """
        hub_ok, hub_why = self.hub_delete_ok()
        out: dict[int, str] = {}
        for row in self.store.chunks():
            cid = row["chunk_id"]
            d = evaluate(from_row(row, self.store.count_pending(cid),
                                  db_rows=self.store.count_telemetry(cid)))
            if d.allowed and not hub_ok:
                out[cid] = f"HUB: {hub_why}"
            else:
                out[cid] = d.reason
        return out
