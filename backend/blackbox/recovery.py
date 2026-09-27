"""
§5.7 (B15) — REPLAY orchestration: turning a gap inventory into requests.
==========================================================================

The demo this file is for
-------------------------
The laptop was unplugged for a while. The hub kept writing — that is what it is
for — so the surface holds a chunk with a hole in it and the deck shows a flat gap
in the timeline. `REPLAY:<first>:<last>` makes the hub re-stream those files off the
SD card; the rows arrive through **the same ingest path** as live ones (P10 built
that deliberately), `UNIQUE(chunk_id, seq)` makes re-streaming idempotent, and the
gap fills in. Rescue teams retrieving a card is an incident report; this is twenty
seconds on a stage.

What this module is, and what it is not
---------------------------------------
It is the **bookkeeping of asking**: which chunks are short, which ones we have
already asked for, how long we have been waiting, how many rows have actually come
back, and when to stop asking. It does not talk to the hub — it returns lines and
the caller emits them (`Gateway.upkeep`), exactly like the delete gate returns an
`ACK_DELETE` and the caller sends it. One direction of control, one place that
touches the wire.

Three rules, each of which exists because the obvious version of it is wrong
----------------------------------------------------------------------------
1. **Only closed chunks are candidates.** A chunk the hub is still writing to
   cannot be replayed — the file is open, its row count is a snapshot of something
   in motion, and asking for it would race the writer for no benefit.
2. **A gap that has not moved is a failed attempt, not a patient one.** The hub's
   REPLAY can fail silently from this side: wrong chunk range, card removed
   mid-stream, UART noise. So a request with no new rows for `NO_PROGRESS_S` is
   retried (`MAX_ATTEMPTS` times) and then marked `EXHAUSTED` — a terminal state the
   ledger shows, because a recovery that retries forever is indistinguishable from
   one that never starts, and it hides a real fault behind a spinner.
3. **Drops are not a replay problem.** Rows that never reached the card
   (`dropped_rows`) cannot be recovered by any amount of asking — they do not exist
   on the hub. They are counted separately and reported as lost, while the rows that
   *are* on the card are still recovered. A recovery path that refused to act
   because a chunk also had drops would abandon data it could have saved.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import protocol as P
from reconciliation import Gap, Reconciler
from store import Store

#: How many chunks one plan may ask for. The firmware accepts a range and caps a
#: single line at 32 chunks (`ns_serve_verb`); eight keeps a recovery burst bounded
#: to something a 115200 UART and a 4 MB/s SD card comfortably overlap, and leaves
#: room for live rows in the same second. Recovery may take several passes — that is
#: what the cooldown is for — and a smaller burst that completes beats a large one
#: that starves the live path.
MAX_CHUNKS_PER_PLAN = 8

#: Minimum spacing between two asks for the *same* chunk.
REPLAY_COOLDOWN_S = 30.0

#: Attempts per chunk before it is marked EXHAUSTED and left alone.
MAX_ATTEMPTS = 3

#: A request that has moved no rows in this long is treated as failed.
NO_PROGRESS_S = 90.0

#: Terminal states, for readers of the ledger and for the tests.
REQUESTED, IN_FLIGHT, RECOVERED, EXHAUSTED, GONE = (
    "REQUESTED", "IN_FLIGHT", "RECOVERED", "EXHAUSTED", "GONE")

_TERMINAL = (RECOVERED, EXHAUSTED, GONE)


@dataclass
class Request:
    """One ask, for one chunk. `first`/`last` are the line that carried it."""
    chunk_id: int
    first: int
    last: int
    asked_at: float
    baseline: int                    # received_rows when we asked
    expected: int
    dropped: int
    attempts: int = 1
    state: str = REQUESTED
    rows_recovered: int = 0
    last_progress_at: Optional[float] = None
    note: str = ""

    def age_s(self, now: float) -> float:
        return now - self.asked_at


class Recovery:
    """Turns `Reconciler.gaps()` into `REPLAY:` lines and follows them to ground."""

    def __init__(self, store: Store, reconciler: Reconciler, *,
                 log: Optional[Callable] = None,
                 clock: Callable[[], float] = time.monotonic,
                 cooldown_s: float = REPLAY_COOLDOWN_S,
                 max_attempts: int = MAX_ATTEMPTS,
                 max_chunks: int = MAX_CHUNKS_PER_PLAN,
                 no_progress_s: float = NO_PROGRESS_S):
        self.store = store
        self.reconciler = reconciler
        self.log = log or (lambda *_: None)
        self._clock = clock
        self.cooldown_s = float(cooldown_s)
        self.max_attempts = int(max_attempts)
        self.max_chunks = int(max_chunks)
        self.no_progress_s = float(no_progress_s)

        self.requests: dict[int, Request] = {}
        self.history: list[dict] = []
        self.recovered_rows = 0
        self.recovered_chunks = 0
        #: `REPLAY:` lines actually planned (so `block()` can report the traffic
        #: without asking the wire what happened).
        self.lines_sent = 0

    # ------------------------------------------------------------------- asking
    def _verdict(self, gap: Gap, now: float, *, force: bool) -> tuple[bool, str]:
        """May we ask for this gap, and in what capacity.

        `force` is an operator pressing the button: it overrides the cooldown *and*
        the terminal states, because the whole point of a manual control is to do the
        thing the automatic path has decided not to do again. It does **not** override
        the reason accounting — a forced retry still counts as an attempt, so the
        ledger cannot be made to look healthier by pressing the button repeatedly.
        """
        req = self.requests.get(gap.chunk_id)
        if req is None:
            if not force and any(e["chunk_id"] == gap.chunk_id and e["state"] == EXHAUSTED
                                 for e in self.history):
                return False, "exhausted"        # terminal until someone forces it
            return True, "new"
        if req.state == EXHAUSTED:
            return (True, "forced") if force else (False, "exhausted")
        if req.state not in (REQUESTED, IN_FLIGHT):
            return (True, "forced") if force else (False, req.state.lower())
        if force:
            return True, "forced"
        waited = now - req.asked_at
        if waited < self.cooldown_s:
            return False, f"asked {waited:.0f}s ago, cooling down"
        if req.attempts >= self.max_attempts:
            # Out of attempts and no progress: this is where an automatic recovery
            # stops, and where it says so instead of asking forever.
            return False, "exhausted"
        return True, "retry"

    def plan(self, *, force: bool = False) -> list[str]:
        """Return the `REPLAY:` lines to send. Records the asks it plans.

        Contiguous chunks coalesce into one line (`REPLAY:0007:0009`): the hub streams
        the range, this side carries one request for three files, and each file keeps
        its own progress. The burst is capped, oldest gap first — a recovery that
        starts with the oldest missing data shrinks the deck's flat gap from the left,
        which is the order a human reads a timeline in.
        """
        now = self._clock()
        wants: list[tuple[Gap, str]] = []
        for gap in self.reconciler.gaps():
            ok, why = self._verdict(gap, now, force=force)
            if ok:
                wants.append((gap, why))
            elif why == "exhausted":
                req = self.requests.get(gap.chunk_id)
                if req is not None:
                    self._finish(req, EXHAUSTED,
                                 f"no progress after {req.attempts} ask(s)")
        if not wants:
            return []
        wants.sort(key=lambda pair: pair[0].chunk_id)
        burst = wants[:self.max_chunks]

        lines: list[str] = []
        asked: list[int] = []
        run_start = run_last = None
        for gap, verdict in burst:
            if run_start is None:
                run_start = run_last = gap.chunk_id
            elif gap.chunk_id == run_last + 1:
                run_last = gap.chunk_id
            else:
                lines.append(P.format_replay(run_start, run_last))
                run_start = run_last = gap.chunk_id
            req = self.requests.get(gap.chunk_id)
            if req is None or verdict in ("new", "forced"):
                req = Request(chunk_id=gap.chunk_id, first=run_start, last=run_last,
                              asked_at=now, baseline=gap.received, expected=gap.expected,
                              dropped=gap.dropped, attempts=1)
                self.requests[gap.chunk_id] = req
            else:
                req.attempts += 1
                req.asked_at = now
                req.baseline = gap.received
                req.expected = gap.expected
                req.note = ""
            asked.append(gap.chunk_id)
        if run_start is not None:
            lines.append(P.format_replay(run_start, run_last))

        self.store.mark_recover_requested(asked)
        self.lines_sent += len(lines)
        waiting = len(wants) - len(burst)
        self.log(f"REPLAY requested: {len(asked)} chunk(s) — {', '.join(str(c) for c in asked)}"
                 + (f" · {waiting} more waiting" if waiting > 0 else ""))
        return lines

    # ----------------------------------------------------------------- following
    def tick(self) -> list[str]:
        """Follow every open request: resolve it, retry it, or give up on it.

        Called from the gateway's upkeep tick. Returns lines the caller must emit —
        normally empty, occasionally a retry.
        """
        now = self._clock()
        for cid, req in list(self.requests.items()):
            if req.state in _TERMINAL:
                continue
            row = self.store.chunk(cid)
            if row is None:
                self._finish(req, GONE, "the chunk vanished from the store")
                continue
            if row["ack_sent_at"]:
                self._finish(req, GONE, "file already released by the gate")
                continue
            received = int(row["received_rows"] or 0)
            expected = row["expected_rows"]
            if expected is not None and received >= int(expected):
                req.rows_recovered = received - req.baseline
                self._finish(req, RECOVERED,
                             f"all {received} rows present" if req.rows_recovered == 0
                             else f"{req.rows_recovered} row(s) back-filled")
                continue
            if received > req.baseline:
                req.rows_recovered = received - req.baseline
                req.baseline = received
                req.last_progress_at = now
                req.state = IN_FLIGHT
                self.log(f"REPLAY chunk {cid}: {req.rows_recovered} row(s) back-filled, "
                         f"{int(expected) - received if expected else 0} still missing")
                continue
            idle = now - (req.last_progress_at or req.asked_at)
            if idle >= self.no_progress_s and req.attempts >= self.max_attempts:
                # Asked `max_attempts` times and moved nothing: stop, and say so.
                # Retrying forever would look identical to a working recovery from
                # the outside, and would hide the fault behind a spinner.
                self._finish(req, EXHAUSTED,
                             f"no rows in {idle:.0f}s after {req.attempts} ask(s)")
        # Whatever is still short and still askable goes out on this tick — a gap
        # can appear long after the handshake (the link only has to drop once), and
        # `plan()` returns [] when everything is cooling down or complete.
        return self.plan()

    def _finish(self, req: Request, state: str, note: str) -> None:
        req.state = state
        req.note = note
        if state == RECOVERED:
            self.recovered_chunks += 1
            self.recovered_rows += req.rows_recovered
            self.log(f"chunk {req.chunk_id} recovered — {note}")
        elif state == EXHAUSTED:
            self.log(f"chunk {req.chunk_id} recovery EXHAUSTED — {note}")
        self.history.append({"chunk_id": req.chunk_id, "state": state,
                             "attempts": req.attempts, "rows_recovered": req.rows_recovered,
                             "note": note})
        del self.history[:-32]
        self.requests.pop(req.chunk_id, None)

    # ------------------------------------------------------------------ surface
    def state_of(self, chunk_id: int) -> str:
        """QUEUED (short, not yet asked) · the live states · or '' when complete."""
        req = self.requests.get(chunk_id)
        if req is not None:
            return req.state
        for entry in reversed(self.history):
            if entry["chunk_id"] == chunk_id:
                return entry["state"]
        return "EXHAUSTED" if any(e["chunk_id"] == chunk_id and e["state"] == EXHAUSTED
                                  for e in self.history) else ""

    def block(self) -> dict:
        """The `recovery` block: handshake, what is in flight, what it has recovered.

        Two audiences, one object. The deck renders `active`/`recovered` as the
        recovery trace; an operator diagnosing "why is the timeline still flat"
        reads `exhausted` and `awaiting`; and the tests assert on the whole thing.
        """
        now = self._clock()
        gaps = {g.chunk_id: g for g in self.reconciler.gaps()}
        active = []
        for cid, req in sorted(self.requests.items()):
            gap = gaps.get(cid)
            active.append({
                "chunk_id": cid,
                "line": f"REPLAY:{req.first:04d}:{req.last:04d}",
                "state": req.state,
                "attempts": req.attempts,
                "age_s": round(req.age_s(now), 1),
                "missing": gap.missing if gap else 0,
                "expected": req.expected,
                "rows_recovered": req.rows_recovered,
                "note": req.note,
            })
        terminal = [e for e in self.history if e["state"] in (RECOVERED, EXHAUSTED, GONE)]
        return {
            "handshake": self.reconciler.report(),
            "awaiting": len([c for c in gaps if c not in self.requests]),
            "active": active,
            "in_flight": len(active),
            "recovered": {"chunks": self.recovered_chunks, "rows": self.recovered_rows},
            "exhausted": [e["chunk_id"] for e in terminal if e["state"] == EXHAUSTED],
            "history": terminal[-8:],
            "replay_lines_sent": self.lines_sent,
        }

    def forget(self, chunk_id: int) -> None:
        """Drop a chunk's recovery history — called when its file is released.

        A released file has no rows left to ask for, and leaving its history in the
        ledger would keep "recovered" entries for chunks that no longer exist on any
        card. The *counters* stay: "142 rows recovered since boot" is a fact about
        the session, not about the chunk.
        """
        self.requests.pop(chunk_id, None)
        self.history = [e for e in self.history if e["chunk_id"] != chunk_id]


def planned_lines(rec: Recovery) -> list[str]:
    """Convenience for callers that just want the next burst and to send it."""
    return rec.plan()
