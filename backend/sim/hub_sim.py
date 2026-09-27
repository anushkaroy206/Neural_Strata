"""Fake Central Hub: emits the frozen protocol so the backend can be built and
tested with no hardware on the desk. Same bytes the ESP32 will send.

`stream()` is a hub that only **talks**. `SimHub` is a hub that also **listens** —
it keeps a card, answers `STATE?` / `REPLAY` / `ACK_DELETE` the way
`main_hub/protocol_out.cpp` does, and can be told to *lose rows on the wire while
still writing them to the card*. That last ability is the whole point: it is the
only way to exercise §5.7 without a board on the bench, because a gap that REPLAY
can repair is, by definition, a row the hub has and the laptop does not.

usage:  python sim/hub_sim.py --chunks 3 --rows 40 | python -m ingest
"""
from __future__ import annotations

import argparse
import json
import queue
import random
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def row(chunk_id: int, seq: int, ts_ms: int, node: int) -> str:
    level = 1 if node in (1, 2) else 2
    return json.dumps({
        "chunk_id": chunk_id, "seq": seq, "ts_ms": ts_ms, "node": node, "level": level,
        "tilt": round(random.uniform(-2, 2), 3),
        "disp_mm": round(random.uniform(0, 12), 3),
        "accel": round(random.uniform(0, 0.08), 4),
        "vib": random.choice([0, 0, 0, 1]),
        "micro": 0.0, "macro": round(random.uniform(0, 60), 2), "flags": 0,
    }, separators=(",", ":"))


def stream(chunks=3, rows_per_chunk=40, first_chunk=1, drop_in_chunk=None,
           drop_count=0, rate_hz=0.0):
    """Yields protocol lines in the exact order the hub will produce them."""
    ts = 142850
    yield "BLACKBOX:OK,3712"
    cid = first_chunk
    for _ in range(chunks):
        yield f"CHUNK_OPEN:{cid:04d}"
        for seq in range(1, rows_per_chunk + 1):
            for node in (1, 2, 3, 4):
                yield row(cid, seq * 4 + node - 4, ts, node)
                ts += 125
            if rate_hz:
                time.sleep(1.0 / (rate_hz * 4))
        dropped = drop_count if cid == drop_in_chunk else 0
        if dropped:
            yield f"DROP:{cid:04d},{dropped}"
        yield f"CHUNK_CLOSE:{cid:04d},{rows_per_chunk * 4},{dropped}"
        cid += 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--chunks", type=int, default=3)
    ap.add_argument("--rows", type=int, default=40, help="rows per node per chunk")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--rate", type=float, default=0.0, help="0 = as fast as possible")
    a = ap.parse_args()
    try:
        for line in stream(chunks=a.chunks, rows_per_chunk=a.rows, first_chunk=a.first,
                           rate_hz=a.rate):
            print(line, flush=True)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()


# ══════════════════════════════════════════════════════════════════════════════
# SimHub — a hub with a card
# ══════════════════════════════════════════════════════════════════════════════
class SimHub:
    """A protocol-speaking hub that answers the down-link verbs — and has a card.

    The card is the part that matters. A real hub writes every row to
    `chunk_NNNN.csv` and can re-read it later; a simulator that only emits never
    has anything to re-read, so REPLAY cannot be tested against it and the recovery
    plane ends up verified by nothing but mocks of itself. So this class records
    every row it "wrote" per chunk, and serves `REPLAY:` from that record.

    **Losing rows on purpose.** `skip(chunk_id, first_seq, count)` writes rows to the
    card and does *not* put them on the wire — the exact fault the recovery plane
    exists for (the hub was fine, the link was not, and the only copy of those rows
    is on the SD card). `drop_in_chunk` is the *other* fault: rows the hub lost in
    its own queue, which are not on the card at all and which no replay can bring
    back. Keeping the two separate in the simulator is what keeps them separate in
    the tests.
    """

    def __init__(self, *, rows_per_chunk: int = 40, first_chunk: int = 1,
                 chunks: Optional[int] = None, rate_hz: float = 0.0,
                 drop_in_chunk: Optional[int] = None, drop_count: int = 0,
                 free_mb: int = 3712, present: bool = True):
        #: **Total** rows per chunk — the unit the store, the gate and the ledger all
        #: use ("chunk 42 · 1000 rows"). Four nodes report per step, so the stream is
        #: `rows_per_chunk // 4` steps of four. `stream()` above takes *rows per node*,
        #: which is what its own CLI documents; this class deliberately does not,
        #: because every number a test asserts here comes from the other side of the
        #: link, and a silent factor of four between the two halves of one protocol
        #: is exactly the kind of bug this phase exists to prevent.
        self.rows_per_chunk = int(rows_per_chunk)
        self.steps = max(1, self.rows_per_chunk // 4)
        self.first_chunk = int(first_chunk)
        self.chunks = chunks                      # None = stream forever
        self.rate_hz = float(rate_hz)
        self.drop_in_chunk = drop_in_chunk
        self.drop_count = int(drop_count)
        self.free_mb = int(free_mb)
        self.present = present                    # False = the card is out (NOSD)

        # ── the card ──
        self.card: dict[int, list[str]] = {}       # chunk_id -> every row it wrote
        self.dropped: dict[int, int] = {}
        self.open_chunk: Optional[int] = None
        self.closed: list[int] = []
        self.acked: list[int] = []
        self.refused: dict[int, str] = {}
        #: (chunk_id, seq) → written to the card, never transmitted. The fault.
        self.skipped: set[tuple[int, int]] = set()

        # ── the wire ──
        self._out = queue.Queue()                 # replies owed to the laptop
        self.sent: list[str] = []                 # what the laptop asked for
        self._closed_since_relay: list[int] = []
        self._rows_written = 0
        self._live_it = self._live()

    # ------------------------------------------------------------------ the card
    def skip_rows(self, chunk_id: int, count: int, *, from_seq: int = 1) -> int:
        """Write `count` **rows** to the card and never put them on the wire.

        Counted in rows, not sequence steps, because the number a test asserts is
        the number a human reads on the ledger ("12 rows would be lost" in §5.6 is
        twelve rows, not three steps of four). The hub writes four nodes per step,
        so a step-based API would have quietly made every gap four times larger than
        it looked. Returns how many rows were actually planned for skipping.
        """
        ordered = [(seq, node) for seq in range(from_seq, self.steps + 1)
                   for node in (1, 2, 3, 4)]
        for seq, node in ordered[:count]:
            self.skipped.add((chunk_id, seq * 4 + node - 4))
        return min(count, len(ordered))

    def card_rows(self, chunk_id: int) -> int:
        return len(self.card.get(chunk_id, []))

    def file_bytes(self, chunk_id: int) -> int:
        """The size of the CSV this stand-in would have written, in bytes.

        A real `chunk_0042.csv` is the same twelve columns the wire carries, so the
        line length is the row's JSON minus its punctuation plus a newline — near
        enough that the deck's storage arithmetic is exercised with realistic
        numbers instead of a constant that hides a unit bug.
        """
        return sum(len(line) + 1 for line in self.card.get(chunk_id, []))

    def files(self) -> list[int]:
        """Chunk ids on the card, ascending — including ones never transmitted."""
        return sorted(self.card)

    def summary(self) -> dict:
        return {
            "card_files": self.files(),
            "rows_on_card": sum(len(v) for v in self.card.values()),
            "open_chunk": self.open_chunk,
            "acked": sorted(self.acked),
            "refused": dict(self.refused),
            "drops": dict(self.dropped),
            "skipped_rows": len(self.skipped),
        }

    # ------------------------------------------------------------- hub -> laptop
    def _write_card(self, line: str) -> None:
        row = json.loads(line)
        self.card.setdefault(row["chunk_id"], []).append(line)
        self._rows_written += 1

    def lines(self) -> Iterator[str]:
        """The live stream, with replies spliced in ahead of the next live row.

        Replies are drained **before** each live row, so a `REPLAY:` asked for at
        t=0 has started arriving by the next row instead of waiting for the current
        chunk to finish. That ordering is not cosmetic: a hub that only answered
        between chunks would make recovery look lossy on a short demo when it was
        merely queued behind a thousand rows.

        This generator **blocks** when there is nothing to say — it is what the
        edge server's reader thread pulls from, and a reader thread blocks on a
        quiet serial port by design. Tests use `poll()` instead, which never blocks
        and returns `None`; a test that blocks on a hub with nothing left to send is
        a test that hangs, and there is no wall-clock timeout long enough to make
        that pleasant to debug.
        """
        while True:
            line = self.poll()
            if line is None:
                time.sleep(0.005)
                continue
            yield line

    def poll(self) -> Optional[str]:
        """One line if there is one: a queued reply first, then the next live row.

        `None` means "nothing right now" — and unlike the blocking generator, that
        is a real answer a caller can act on. It is also the honest shape of a hub
        that has finished: the telemetry has stopped and no question is pending.
        """
        while not self._out.empty():
            return self._out.get_nowait()
        try:
            return next(self._live_it)
        except StopIteration:
            return None

    def _live(self) -> Iterator[str]:
        """The telemetry stream alone — the hub's own output, in protocol order."""
        yield self.relay("BLACKBOX:OK" if self.present else "BLACKBOX:NOSD",
                         self.free_mb)
        cid = self.first_chunk
        made = 0
        while self.chunks is None or made < self.chunks:
            yield self.relay(f"CHUNK_OPEN:{cid:04d}")
            self.open_chunk = cid
            for seq in range(1, self.steps + 1):
                for node in (1, 2, 3, 4):
                    if not self.present:
                        continue                          # no card: nothing is written
                    wire_seq = seq * 4 + node - 4
                    line = row(cid, wire_seq, 142850 + self._rows_written * 125, node)
                    self._write_card(line)
                    if (cid, wire_seq) not in self.skipped:
                        yield line
                if self.rate_hz:
                    time.sleep(1.0 / (self.rate_hz * 4))
            dropped = self.drop_count if cid == self.drop_in_chunk else 0
            if dropped:
                self.dropped[cid] = dropped
                yield self.relay(f"DROP:{cid:04d},{dropped}")
            self.open_chunk = None
            self.closed.append(cid)
            rows_on_card = len(self.card.get(cid, []))
            yield self.relay(f"CHUNK_CLOSE:{cid:04d},{rows_on_card},{dropped}")
            # …and the file report the archivist sends behind it: the *size* of what
            # was written. Measured from the bytes it actually holds, not estimated.
            yield self.relay(f"CHUNK_FILE:{cid:04d},{self.file_bytes(cid)},{dropped}")
            cid += 1
            made += 1
            self.free_mb = max(0, self.free_mb - 1)
            yield self.relay("BLACKBOX:OK", self.free_mb)
        # A finished run still has to be **askable** — the whole point of a hub with a
        # card is that it answers questions after the telemetry stops. The live
        # sequence simply ends here; replies keep flowing through `poll()` (and
        # therefore through `lines()`), which is what a hub that has stopped
        # reporting but is still listening actually looks like.

    def relay(self, line: str, *args) -> str:
        """Emit a line, and queue it for inspection. (`sent` is for the *laptop's* lines.)"""
        if args:
            line = line + "," + ",".join(str(a) for a in args)
        return line

    # ------------------------------------------------------------- laptop -> hub
    def write(self, line: str) -> None:
        """Receive one down-link line and answer it — the firmware's vocabulary.

        Mirrors `ns_serve_verb()` in `main_hub/main.cpp`, including the two
        refusals: a chunk that is still open cannot be unlinked, and a chunk that
        lost rows is never unlinked (its file is the only record of them).
        """
        line = line.strip()
        self.sent.append(line)
        if line.startswith("ACK_DELETE:") or line.startswith("#CMD ACK_DELETE:"):
            cid = _trailing_int(line)
            self._ack(cid)
        elif line.startswith("REPLAY:"):
            body = line.split(":", 1)[1]
            first_s, _, last_s = body.partition(":")
            first, last = int(first_s), int(last_s or first_s)
            self._replay(first, last)
        elif line.startswith("STATE?"):
            self._state()
        elif line.startswith("PING"):
            self._out.put(f"PONG:{int(time.time() * 1000) % 100000}\n")

    def _ack(self, chunk_id: int) -> None:
        if self.open_chunk == chunk_id:
            self.refused[chunk_id] = "chunk is open"
            self._out.put(f"ACK_REFUSED:{chunk_id:04d},chunk is open\n")
            return
        if self.dropped.get(chunk_id):
            self.refused[chunk_id] = "chunk dropped rows"
            self._out.put(f"ACK_REFUSED:{chunk_id:04d},dropped rows are only on the card\n")
            return
        if chunk_id in self.card:
            del self.card[chunk_id]
            self.acked.append(chunk_id)
            self._out.put(f"ACK_OK:{chunk_id:04d}\n")
        else:
            self.refused[chunk_id] = "no such chunk"
            self._out.put(f"ACK_REFUSED:{chunk_id:04d},no such chunk\n")

    def _replay(self, first: int, last: int) -> None:
        """Re-stream the card's copy of the range, marked `source: replay`.

        The marker is what makes a replay idempotent *and* legible: `(chunk_id, seq)`
        is UNIQUE so nothing is inserted twice, and `source` records that these rows
        arrived by recovery rather than by radio — which is how the deck can say
        "142 rows back-filled" without guessing.
        """
        for cid in sorted(self.card):
            if first <= cid <= last:
                for line in self.card[cid]:
                    self._out.put(line.replace('{"chunk_id"',
                                               '{"source":"replay","chunk_id"', 1) + "\n")

    def _state(self) -> None:
        """§5.7's reply: the open chunk now, plus every closed unacknowledged file.

        What the *card* holds, not what the laptop was sent — which is the entire
        value of the handshake. A hub that replied with "what I think you have" would
        be agreeing with the laptop instead of correcting it, and a reconciliation
        that cannot correct anything is a very elaborate way of doing nothing.
        """
        open_ids = [self.open_chunk] if self.open_chunk else []
        for cid in open_ids:
            self._out.put(f"CHUNK_STATE:{cid:04d},{len(self.card.get(cid, []))},"
                          f"{self.dropped.get(cid, 0)},OPEN\n")
        for cid in self.closed:
            if cid in self.acked:
                continue
            self._out.put(f"CHUNK_DONE:{cid:04d},{len(self.card.get(cid, []))},"
                          f"{self.dropped.get(cid, 0)}\n")


def _trailing_int(line: str) -> int:
    digits = "".join(c for c in line.split(":")[-1] if c.isdigit())
    return int(digits or 0)
