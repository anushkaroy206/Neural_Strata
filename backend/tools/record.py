#!/usr/bin/env python3
"""
Record a live session to ``session.csv`` — once, then never need the rig again.
=============================================================================

Polls a running edge server's ``/api/telemetry`` at the training cadence (2 Hz)
and writes one row per node per tick, in the frozen ``session.csv`` format
(``contracts.SESSION_CSV_FIELDS``). RFID reads are written as uid rows with an
empty node column.

    python3 tools/record.py --seconds 120 --out /tmp/session.csv
    python3 tools/record.py --seconds 120 --out /tmp/session.csv --url http://localhost:5000
    python3 tools/record.py --seconds 600 --tag breach-run-1      # into recordings/

Why poll the API rather than read the serial port directly
----------------------------------------------------------
Because the API is the same surface the dashboard uses, so what you record is
what the operator saw. It also works against a rig, a simulator *and* a replay —
one recorder for all three.

What this buys every later phase
--------------------------------
A recorded session replayed through the real pipeline is the regression test for
Phases 1–11: same inputs, same code path, no hardware, ten times faster.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import urllib.error
import urllib.request

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from contracts import SESSION_CSV_FIELDS, format_session_row   # noqa: E402

DEFAULT_URL = "http://127.0.0.1:5000"
DEFAULT_HZ = 2.0
RECORDINGS = BACKEND.parent / "recordings"


def fetch_json(url: str, timeout: float = 2.0) -> dict | None:
    """GET a JSON endpoint. Returns ``None`` on any failure — a recorder that
    dies because one poll timed out would lose the whole session."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            import json
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Record /api/telemetry to session.csv")
    ap.add_argument("--url", default=DEFAULT_URL, help="edge server base URL")
    ap.add_argument("--out", default=None, help="output CSV path")
    ap.add_argument("--tag", default=None,
                    help="name the recording (writes recordings/<tag>.csv)")
    ap.add_argument("--seconds", type=float, default=120.0, help="how long to record")
    ap.add_argument("--hz", type=float, default=DEFAULT_HZ, help="poll rate (train cadence is 2)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if args.out:
        out = Path(args.out)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = RECORDINGS / f"{args.tag or 'session'}-{stamp}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)

    interval = 1.0 / max(0.1, args.hz)
    t0 = time.time()
    rows_written = 0
    polls_ok = 0
    polls_failed = 0
    seen_rfid: set[tuple] = set()
    nodes_seen: set[str] = set()

    with out.open("w") as fh:
        fh.write(",".join(SESSION_CSV_FIELDS) + "\n")
        while (time.time() - t0) < args.seconds:
            tick = time.time()
            payload = fetch_json(f"{args.url.rstrip('/')}/api/telemetry")

            if payload is None:
                polls_failed += 1
            else:
                polls_ok += 1
                t_ms = int((time.time() - t0) * 1000)
                for n_id, node in (payload.get("nodes") or {}).items():
                    idx = int(n_id.split("_")[-1]) if n_id.rsplit("_", 1)[-1].isdigit() else 0
                    if idx:
                        nodes_seen.add(n_id)
                    fh.write(format_session_row(
                        t_ms=t_ms, node=idx,
                        disp=node.get("displacement_mm", 0.0),
                        tilt=node.get("tilt_x", 0.0),
                        acc=node.get("acceleration", 0.0),
                        vib=node.get("vib", 0) or 0,
                        micro=node.get("micro"), macro=node.get("macro"),
                        source="hardware",
                    ) + "\n")
                    rows_written += 1

                # RFID reads → uid rows. Deduped, because /api/telemetry keeps the
                # last 10 entries and would otherwise re-record them every tick.
                for entry in (payload.get("rfid_log") or []):
                    key = (entry.get("id"), entry.get("time"), entry.get("zone"))
                    if key in seen_rfid:
                        continue
                    seen_rfid.add(key)
                    fh.write(format_session_row(
                        t_ms=t_ms, node=None, uid=str(entry.get("id", "")),
                        source="hardware",
                    ) + "\n")
            fh.flush()

            sleep_for = interval - (time.time() - tick)
            if sleep_for > 0:
                time.sleep(sleep_for)

    if not args.quiet:
        print(f"[record] {rows_written} rows → {out}")
        print(f"[record] {polls_ok} polls ok, {polls_failed} failed, "
              f"nodes {sorted(nodes_seen) or '—'}, {len(seen_rfid)} RFID events")
        if polls_ok == 0:
            print(f"[record] NOTHING RECORDED — is the server up at {args.url}?")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
