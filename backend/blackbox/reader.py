"""Where protocol lines come from: a real hub on a serial port, or the simulator.

Nothing downstream can tell the difference — both yield the same bytes. That is
the point: the ingest + gate + status path is exercised identically on the bench
and on the mine, so a passing bench run means something.

`source_is_live` is what decides mode=LIVE vs mode=SIM on the operator deck. It
reports True only for a real serial port, so the deck's LIVE/SIM badge cannot be
made to lie by feeding it the simulator.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Iterator, Optional

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent
for _p in (str(_HERE), str(_BACKEND)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from sim.hub_sim import SimHub                          # noqa: E402


class SerialSource:
    """The real hub. 115200 8N1, one line per message, '\n' terminated."""

    live = True

    def __init__(self, port: str, baud: int = 115200, timeout: float = 1.0):
        import serial                                    # pyserial
        self.port = port
        self.ser = serial.Serial(port, baud, timeout=timeout)

    def lines(self) -> Iterator[str]:
        while True:
            try:
                raw = self.ser.readline()
            except Exception:                            # cable pulled
                time.sleep(0.5)
                continue
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                yield line

    def write(self, line: str) -> None:
        """Downstream direction: ACK_DELETE / REPLAY / STATE? / PING."""
        self.ser.write((line.rstrip("\n") + "\n").encode("utf-8"))

    def close(self):
        try:
            self.ser.close()
        except Exception:                                # noqa: BLE001
            pass


class SimSource:
    """The bench stand-in: same bytes, no board. Loops chunks forever.

    Phase 11 gave it a **card and a voice**. It used to be a pure emitter — it
    recorded the down-link lines it received and could not answer any of them,
    which meant `ACK_DELETE` was unverifiable without hardware and `STATE?` /
    `REPLAY:` were unimplemented in the only environment this project has actually
    been run in. It is now a thin adapter over `sim.hub_sim.SimHub`, which keeps a
    card, answers the verbs the way `main_hub/protocol_out.cpp` does, and can be
    told to lose rows on the wire while still writing them down.

    The class kept its name and its public surface (`lines()`, `write()`, `live`,
    `sent`, `close()`) because everything else in the codebase — the gateway, the
    tests, `python3 -m blackbox` — hands it those exact names. What changed is that
    it now listens.
    """

    live = False

    def __init__(self, *, rows_per_chunk: int = 1000, rows_per_sec: float = 8.0,
                 first_chunk: int = 1, free_mb: int = 3712,
                 chunks: Optional[int] = None, present: bool = True):
        self.hub = SimHub(rows_per_chunk=rows_per_chunk, first_chunk=first_chunk,
                          chunks=chunks, rate_hz=rows_per_sec / 16.0,
                          free_mb=free_mb, present=present)
        #: Kept as attributes because callers and tests read them.
        self.rows_per_chunk = rows_per_chunk
        self.rate_hz = rows_per_sec / 16.0
        self.first_chunk = first_chunk
        self.free_mb = free_mb

    #: What the laptop asked for, in order — the trace the deck shows.
    @property
    def sent(self) -> list:
        return self.hub.sent

    def lines(self) -> Iterator[str]:
        # The hub stand-in lives in backend/sim/ (tree of record §2) and is
        # imported once at module level. It used to be imported here from
        # blackbox/sim/ after a path insert — moving the file without removing
        # that made this generator raise ModuleNotFoundError *inside a thread*,
        # which the reader swallowed: the gateway came up, reported SIM, and
        # produced no rows at all. Caught by tests/test_gateway_contract.py.
        yield from self.hub.lines()

    def write(self, line: str) -> None:
        """Hand a down-link line to the stand-in hub, which answers it."""
        self.hub.write(line)

    def skip_rows(self, chunk_id: int, count: int, *, from_seq: int = 1) -> int:
        return self.hub.skip_rows(chunk_id, count, from_seq=from_seq)

    def close(self):
        pass


def open_source(port: Optional[str] = None, **kw):
    """Serial when a port is given, simulator otherwise.

    A serial port that fails to open falls back to the simulator but keeps
    live=False, so the deck shows SIM rather than claiming a hub it does not have.
    """
    if port:
        try:
            return SerialSource(port)
        except Exception as exc:                         # noqa: BLE001
            print(f"[reader] {port} did not open ({exc}) — falling back to simulator",
                  file=sys.stderr)
    return SimSource(**kw)
