#!/usr/bin/env python3
"""
Replay a recorded session through the real pipeline.
====================================================

    python3 tools/replay.py /tmp/session.csv --speed 10 --no-serial
    python3 tools/replay.py recordings/breach-run-1.csv            # 1x, own sim

This is a thin launcher: it boots the edge server with ``--replay``, which swaps
the *serial reader thread* for a replay reader. Everything downstream is
unchanged — the same ``ingest_line()``, the same derivative maths, the same status
logic, the same model buffers, the same database writes, the same endpoints. That
is the entire point: if the replay used a separate path, it would stop predicting
what the live system does, and then it would stop being trusted.

``--speed 10`` divides the recorded inter-row spacing by ten. The *sequence* of
states is the recorded one; only the wall-clock spacing changes.

The escape hatch for a bad session
----------------------------------
``--speed 0`` parses the file and prints what it found without booting anything.
Useful for checking a recording that a colleague sent you.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Replay session.csv through the edge server")
    ap.add_argument("csv", help="the recording to replay")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="speed multiplier (10 = ten times faster); 0 = inspect only")
    ap.add_argument("--port", type=int, default=5000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--no-serial", action="store_true",
                    help="accepted for compatibility; replay never opens the serial port")
    ap.add_argument("--sd-card-mb", type=int, default=None)
    ap.add_argument("--no-blackbox", action="store_true")
    args = ap.parse_args(argv)

    path = Path(args.csv)
    if not path.exists():
        print(f"[replay] no such file: {path}", file=sys.stderr)
        return 2

    if args.speed <= 0:
        from contracts import read_session_csv
        rows = read_session_csv(path)
        nodes = sorted({r["node"] for r in rows if "node" in r})
        rfids = [r for r in rows if "uid" in r]
        span = (rows[-1].get("t_ms", 0) - rows[0].get("t_ms", 0)) / 1000.0 if rows else 0.0
        print(f"[replay] {path}")
        print(f"[replay] {len(rows)} rows · {span:.1f} s · nodes {nodes or '—'} · {len(rfids)} RFID")
        return 0 if rows else 1

    # Hand over to the real server, with the replay source in place of USB.
    import app as edge

    argv_forward = ["--host", args.host, "--port", str(args.port),
                    "--replay", str(path), "--speed", str(args.speed),
                    "--no-serial"]
    if args.no_blackbox:
        argv_forward.append("--no-blackbox")
    if args.sd_card_mb is not None:
        argv_forward += ["--sd-card-mb", str(args.sd_card_mb)]

    sys.argv = ["app.py"] + argv_forward
    print(f"[replay] booting the edge server against {path} at {args.speed:g}x")
    edge.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
