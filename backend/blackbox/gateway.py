"""The running gateway: one serial line in, truth store + gate + cloud out, and
the two status blocks the operator deck renders.

This is the Option-A wiring. GET /api/extended serves `blackbox` and `cloud`
computed from *here* — from what the hub actually reported and what the gate
actually released — so the deck's BBX panel is a readout of the real pipeline
rather than a box of constants.

Threading is deliberately dull. Two threads:

  reader  owns the serial line: line -> Ingestor -> store (+ outbox)
  upkeep  on a timer: gate sweep (may emit ACK_DELETE), cloud drain, counters

They share one sqlite connection, so both take the same lock. At 8 rows/s this
is nowhere near contended, and correctness beats throughput on a safety path.
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
for _p in (str(_HERE), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import status as st                                    # noqa: E402
# The cloud stand-in lives in backend/sim/ with the hub simulator (tree of record
# §2). It was `cloud.py` beside this file while the gateway was a standalone
# package; Phase 10 folds both into the recovery plane.
from sim.cloud_mock import LocalSink, drain            # noqa: E402
import protocol as P                                  # noqa: E402
from ingest import Ingestor                            # noqa: E402
from laptop_card import LaptopCard                     # noqa: E402
from reader import open_source                         # noqa: E402
from reconciliation import Reconciler                  # noqa: E402
from recovery import Recovery                          # noqa: E402
from store import Store                                # noqa: E402

DEFAULT_DB = _HERE.parent / "runtime" / "blackbox.sqlite"
UPLOAD_EVERY_S = 30.0
TICK_S = 1.0


class Gateway:
    def __init__(self, *, db_path: Optional[str | Path] = None, port: Optional[str] = None,
                 card_mb: Optional[int] = None, sink=None, log=None,
                 sim_rows_per_chunk: int = 1000, sim_rows_per_sec: float = 8.0,
                 external_feed: bool = False, sim_skip: Optional[str] = None):
        if db_path is None:
            db_path = DEFAULT_DB
        # The parent directory is created whether the path was defaulted or passed
        # in -- sqlite will not create it, and a missing runtime/ used to make the
        # whole gateway fail to start.
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.store = Store(str(db_path))
        self.laptop = LaptopCard(log=log or (lambda *_: None))
        self.ingestor = Ingestor(self.store, log=log or (lambda *_: None), card=self.laptop)
        self.source = open_source(port, rows_per_chunk=sim_rows_per_chunk,
                                  rows_per_sec=sim_rows_per_sec)
        self.sink = sink or LocalSink(Path(db_path).parent / "cloud-standin")
        self.card_mb = card_mb
        self.log = log or (lambda *_: None)
        # §5.7's two halves, built after `self.log` exists and before any thread can
        # call them. The reconciler owns the conversation with the hub (`STATE?` out,
        # replies merged monotonically in) and the gap inventory; the recovery driver
        # turns that inventory into `REPLAY:` asks and follows them. Both log, because
        # a recovery that happens silently is a recovery nobody can debug.
        self.reconciler = Reconciler(self.store, log=self.log)
        self.recovery = Recovery(self.store, self.reconciler, log=self.log)
        # A serial port can only have one reader. When the edge server already
        # owns the line (its own reader thread parses the hub's telemetry and
        # RFID packets), it hands us each line through feed() instead of letting
        # us open the same port and race it for bytes.
        self._external = bool(external_feed)
        self._writer = None
        # A **bench lever**, simulator only: "CHUNK:ROWS" makes the stand-in hub write
        # rows to its card and never put them on the wire — the fault §5.7 exists to
        # repair. A constructor argument rather than a method call, so a demo or a
        # failure drill cannot leave it armed halfway through a run.
        #
        # Armed last, and only for a line this gateway actually reads. The first
        # version sat three lines above `self.log`, and when the resulting
        # `AttributeError` was swallowed by the caller's `except` the gateway came up
        # looking perfectly healthy with no gap in it — the worst possible failure for
        # a switch whose entire job is to make a system fail on purpose.
        self.sim_skip = self._arm_sim_skip(sim_skip)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._next_upload = UPLOAD_EVERY_S
        self._cloud_online = True      # optimistic until a push actually fails
        self._last_drain: Optional[dict] = None
        self._acks_sent = 0

    def _arm_sim_skip(self, spec: Optional[str]) -> Optional[str]:
        """`"1:20"` → chunk 1 loses its first twenty rows on the way to the laptop."""
        if not spec:
            return None
        if self._external:
            self.log(f"sim_skip {spec!r} ignored: this gateway does not own the line, "
                     f"so its source is not the hub the surface is reading")
            return None
        try:
            chunk_s, rows_s = spec.split(":", 1)
            chunk, rows = int(chunk_s), int(rows_s)
        except ValueError:
            self.log(f"sim_skip {spec!r} is not CHUNK:ROWS — ignored")
            return None
        skip = getattr(self.source, "skip_rows", None)
        if skip is None:
            self.log(f"sim_skip {spec!r} ignored: the source is not the simulator")
            return None
        planned = skip(chunk, rows)
        self.log(f"sim_skip armed: chunk {chunk} will lose {planned} row(s) on the wire "
                 f"(the card keeps them — that is the point)")
        return spec

    # ------------------------------------------------------------------ runtime
    @property
    def mode(self) -> str:
        """LIVE only for a real hub on a real line. The simulator never claims it."""
        if self._external:
            return "LIVE" if self._writer is not None else "SIM"
        return "LIVE" if getattr(self.source, "live", False) else "SIM"

    def attach_writer(self, write):
        """Hand over the write side of the externally-owned line (ACK_DELETE, etc.).

        Called once the edge server's own reader has the port open. Until then the
        mode reads SIM, because nothing is actually on the line yet.

        **This is also the reconnect hook.** §5.7 wants `STATE?` sent on *every*
        reconnect, and this call is the one place in the program that knows a line
        came back: the serial reader opens the port, then hands us the writer. The
        reconciler rate-limits the ask, so a connector that makes contact three
        times in two seconds still produces one handshake.
        """
        was = self.mode
        self._writer = write
        if was != self.mode:
            self.log(f"line attached - mode now {self.mode}")
            self.store.mark_hub_seen()
        self.reconciler.on_reconnect(self._emit)

    def feed(self, line: str):
        """One line from a reader this class does not own — same path as _read_loop."""
        with self._lock:
            self._absorb(self.ingestor.handle(line))
            for cmd in self.ingestor.stats.to_hub:
                self._emit(cmd)
            self.ingestor.stats.to_hub.clear()

    def _absorb(self, msg) -> None:
        """Let the reconciler see a reply the ingest path has already stored.

        Order matters: the store write happens in `Ingestor` (one write path, no
        exceptions — P10's rule), and the reconciler is told *afterwards*, mostly so
        it can tell "the hub answered" from "the hub told us something we did not
        have". Splitting the write between two objects is how a truth store ends up
        with two truths.
        """
        if isinstance(msg, (P.ChunkState, P.ChunkDone)):
            self.reconciler.note_reply(
                msg, self.ingestor.last_merge if isinstance(msg, P.ChunkDone) else None)

    def _emit(self, cmd: str):
        """Send a downlink command over whichever line we have."""
        try:
            if self._external:
                if self._writer is not None:
                    self._writer((cmd + "\n").encode("utf-8"))
            else:
                self.source.write(cmd)
        except Exception as exc:                       # noqa: BLE001
            self.log(f"downlink failed: {type(exc).__name__}: {exc}")

    def start(self) -> "Gateway":
        self._threads = []
        if not self._external:
            self._threads.append(threading.Thread(target=self._read_loop, name="bbx-reader", daemon=True))
        self._threads.append(threading.Thread(target=self._upkeep_loop, name="bbx-upkeep", daemon=True))
        for t in self._threads:
            t.start()
        src = "edge-server line" if self._external else ("serial" if self.mode == "LIVE" else "simulator")
        self.log(f"gateway up · source={src}")
        if not self._external:
            # §5.7 asks on **every** reconnect, and this is one: when this class owns
            # the line, the line comes up here. (The edge-server path is the other
            # way round — the reader opens the port and then calls `attach_writer`,
            # which asks instead.) Without this the *owning* deployment would never
            # hand-shake at all: it would start talking, receive telemetry, and never
            # ask the hub what it had been holding while the laptop was away.
            self.reconciler.on_reconnect(self._emit)
        return self

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        if not self._external:
            self.source.close()

    # -------------------------------------------------------------------- loops
    def _read_loop(self):
        try:
            for line in self.source.lines():
                if self._stop.is_set():
                    break
                with self._lock:
                    self._absorb(self.ingestor.handle(line))
                    for cmd in self.ingestor.stats.to_hub:
                        self._emit(cmd)
                    self.ingestor.stats.to_hub.clear()
        except Exception as exc:                       # noqa: BLE001 — never kill the app
            self.log(f"reader stopped: {type(exc).__name__}: {exc}")

    def _upkeep_loop(self):
        while not self._stop.wait(TICK_S):
            try:
                self.upkeep(TICK_S)
            except Exception as exc:                   # noqa: BLE001
                self.log(f"upkeep error: {type(exc).__name__}: {exc}")

    def upkeep(self, elapsed: float = TICK_S):
        """Gate sweep, recovery follow-up, cloud drain, countdown. Safe to call by hand.

        Order is deliberate: **release first, then repair.** A chunk that is
        complete and confirmed can be unlinked immediately, and asking the hub to
        replay a file we are about to delete would be wasted traffic on the one link
        that also carries live telemetry.
        """
        with self._lock:
            for cmd in self.ingestor.sweep_acks():
                self._emit(cmd)
                self._acks_sent += 1
                if cmd.startswith("ACK_DELETE:"):
                    try:
                        self.recovery.forget(int(cmd.split(":", 1)[1]))
                    except ValueError:
                        pass

            for cmd in self.recovery.tick():
                self._emit(cmd)

            self._next_upload -= elapsed
            if self._next_upload <= 0:
                self.force_sync()
            self._release_synced_card()

    # ------------------------------------------------------------------ actions
    def force_reconcile(self, emit: bool = True) -> dict:
        """`POST /api/blackbox/recover` lands here.

        Two things, in this order: **ask the hub what it has** (`STATE?`, forced —
        an operator pressing the button means now), then plan the REPLAY burst for
        whatever is still short. Asking first is not ceremony: the hub may already
        hold rows we never received *and* have closed a chunk we still think is
        open, and a replay planned against a stale picture asks for the wrong files.
        """
        with self._lock:
            asked = self.reconciler.on_reconnect(self._emit, force=True) if emit else False
            lines = self.recovery.plan(force=True)
            for cmd in lines:
                self._emit(cmd)
            return {
                "ok": True,
                "state_query_sent": asked,
                "replay_lines": lines,
                "chunks_requested": len(lines),
                "gaps": [g.chunk_id for g in self.reconciler.gaps()],
                "missing_rows": self.reconciler.total_missing(),
                "summary": self.reconciler.reconcile_summary(),
            }

    def force_sync(self) -> dict:
        """POST /api/cloud/force_sync lands here: push the outbox now.

        Reply shape follows the frontend's contract (docs/API.md):
        `{"ok":true,"uploaded":187,"acked":[41]}` -- `uploaded` is the row count
        and `acked` lists the chunk ids this call released, so the backend can be
        diffed straight against mock/server.js. The richer counters are kept
        alongside them for operators and tests.
        """
        with self._lock:
            res = drain(self.store, self.sink)
            self._last_drain = res
            self._cloud_online = res["batches_failed"] == 0
            self._next_upload = UPLOAD_EVERY_S
            # a drain may have just completed a chunk — let the gate look again
            acked: list[int] = []
            for cmd in self.ingestor.sweep_acks():
                self._emit(cmd)
                self._acks_sent += 1
                if cmd.startswith("ACK_DELETE:"):
                    try:
                        acked.append(int(cmd.split(":", 1)[1]))
                    except ValueError:
                        pass
            self._release_synced_card()
        self.log(f"force_sync: +{res['rows_sent']} rows, {res['batches_ok']} batch(es)"
                 + (f", FAILED: {res['error']}" if res["error"] else ""))
        return {"ok": res["batches_failed"] == 0,
                "uploaded": res["rows_sent"],
                "acked": acked,
                **res}

    def _release_synced_card(self) -> list[int]:
        """Drop laptop-card files whose rows the cloud already has.

        The hub's ACK_DELETE path unlinks a file on the hub's own socket. This
        run's archive is the card mounted in the laptop, and that socket stays
        NOSD, so the hub never closes those chunks and the gate never sends
        ACK_DELETE. The CSV is only a copy of rows already ingested: once every
        one of them is cloud-confirmed and a newer chunk is the one being
        written, the file is no longer the live copy.
        """
        chunks = list(self.store.chunks())
        # The file still growing is the highest chunk this process has
        # appended. Older sqlite rows can still be OPEN from a previous run,
        # so the live file is not "the largest open id".
        live = self.laptop.writing
        if live is None:
            return []
        removed: list[int] = []
        for c in chunks:
            cid = int(c["chunk_id"])
            if cid >= live:
                continue
            if int(c["dropped_rows"] or 0) > 0:
                continue
            # A hub close is not required. A remapped chunk stays OPEN because
            # the hub never closes the id we assigned, and its rows are still
            # fully uploaded once nothing is pending.
            if (c["state"] == "CLOSED" and c["expected_rows"] is not None
                    and int(c["received_rows"] or 0) != int(c["expected_rows"])):
                continue
            if int(c["received_rows"] or 0) <= 0:
                continue
            if self.store.count_pending(cid) > 0:
                continue
            if self.laptop.release(cid) == "removed":
                removed.append(cid)
                self.log(f"laptop card released chunk {cid} — cloud already has its rows")
                # #region agent log
                try:
                    _left = getattr(self, "_dbg_rel", 6)
                    if _left > 0:
                        self._dbg_rel = _left - 1
                        import json as _json, time as _time
                        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps({
                                "sessionId": "83ae1d", "hypothesisId": "CARD",
                                "runId": "post-fix",
                                "location": "gateway.py:_release_synced_card",
                                "message": "uploaded chunk file removed from the laptop card",
                                "data": {"chunk": cid, "live": live, "state": c["state"]},
                                "timestamp": int(_time.time() * 1000),
                            }) + "\n")
                except Exception:
                    pass
                # #endregion
        return removed

    # ------------------------------------------------------------------- status
    def extended_blocks(self) -> dict:
        """The `blackbox` and `cloud` blocks for GET /api/extended."""
        with self._lock:
            return {
                "mode": self.mode,
                "blackbox": st.blackbox_status(
                    self.store,
                    card_mb=self.card_mb,
                    now=datetime.now(timezone.utc),
                    # §5.7's recovery state rides in the same block the deck already
                    # polls at 2 Hz, so the recovery trace cannot be a second behind
                    # the row counts it explains.
                    recovery=self.recovery.block(),
                    laptop=self.laptop.snapshot(),
                ),
                "cloud": st.cloud_status(
                    self.store,
                    online=self._cloud_online,
                    next_upload_s=self._next_upload,
                ),
            }

    def health(self) -> dict:
        with self._lock:
            return {
                "mode": self.mode,
                "hub": dict(self.store.hub()),
                "rows": self.store.count_telemetry(),
                "outbox": self.store.count_outbox(),
                "pending": self.store.pending_total(),
                "acks_sent": self._acks_sent,
                "chunks": [dict(c) for c in self.store.chunks()],
                "last_drain": self._last_drain,
                "recovery": self.recovery.block(),
            }


if __name__ == "__main__":                                # tiny manual check
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default=None, help="serial port; omit for the simulator")
    ap.add_argument("--card-mb", type=int, default=None, help="microSD capacity, MB")
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--rate", type=float, default=8.0, help="simulator rows/sec")
    ap.add_argument("--chunk-rows", type=int, default=40)
    args = ap.parse_args()

    gw = Gateway(port=args.port, sim_rows_per_chunk=args.chunk_rows,
                 sim_rows_per_sec=args.rate, log=lambda m: print(f"[gw] {m}", file=sys.stderr))
    gw.start()
    time.sleep(args.seconds)
    print(json.dumps(gw.extended_blocks(), indent=2))
    print(json.dumps(gw.health(), indent=2, default=str), file=sys.stderr)
    gw.stop()
