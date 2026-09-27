#!/usr/bin/env python3
"""led_test.py — fire one indication at a level hub and watch it land. Phase 8.

    python3 tools/led_test.py --list
    python3 tools/led_test.py --port /dev/cu.usbserial-XXXX --pattern chase_red J1
    python3 tools/led_test.py --port /dev/cu.usbserial-XXXX --siren on --level 2
    python3 tools/led_test.py --port COM7 --advisory on --level 1
    python3 tools/led_test.py --port /dev/cu.usbserial-XXXX --demo      # the tour

Why this exists, and why it is not a `printf` in a shell script:

* **It sends the server's own bytes.** The command is built by the very
  `HubCommand.to_dict()` the router uses (`routing/dijkstra.py`), so this tool
  cannot drift from the wire format — and if someone changes the format in the
  server, this tool changes with it or stops building. A hand-written `printf`
  in a runbook is a copy of the protocol, and a copy of a protocol is a second
  source of truth.
* **It is the acceptance test for H6/H7 that a person runs with their eyes.**
  The backend's tests prove the *server* emits the right pattern; only a human
  watching a strip can prove the strip lit up in the right colour.
* **`--dry-run` is the default when there is no port.** A tool that refuses to
  show you what it would send, just because no board is plugged in, is a tool
  that gets skipped during a rehearsal.

Nothing here decides anything: it is the same one-way command path the dispatcher
uses (`#CMD {json}`), just driven by a hand instead of by an alert.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from routing.dijkstra import PATTERNS, HubCommand          # noqa: E402

#: Which junctions belong to which level hub — §H6.6: L1 carries J1/J2, L2 J3/J4.
LEVEL_JUNCTIONS = {1: ("J1", "J2"), 2: ("J3", "J4")}


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Fire a level-hub indication by hand")
    ap.add_argument("--port", help="the hub's USB serial port (omit for a dry run)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--level", type=int, choices=(1, 2), default=1,
                    help="which level hub (1 = J1/J2, 2 = J3/J4)")
    ap.add_argument("--pattern", choices=sorted(PATTERNS), help="junction pattern")
    ap.add_argument("--level-pattern", choices=sorted(PATTERNS),
                    help="the `pattern` command: one pattern on **both** junctions "
                         "of --level (no junction addressed)")
    ap.add_argument("junction", nargs="?", default=None,
                    help="J1..J4 (default: both junctions of --level)")
    ap.add_argument("--siren", choices=("on", "off"), help="siren, continuous tone")
    ap.add_argument("--advisory", choices=("on", "off"),
                    help="§5.9 advisory: the amber breathe, never a siren")
    ap.add_argument("--repeat", type=int, default=1, help="send it this many times")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between repeats")
    ap.add_argument("--demo", action="store_true",
                    help="the tour: every pattern in order, then clear")
    ap.add_argument("--list", action="store_true", help="print the vocabulary and exit")
    ap.add_argument("--dry-run", action="store_true", help="print, do not send")
    return ap.parse_args(argv)


def show_vocabulary() -> int:
    """Print the pattern table **from the server's own definition**."""
    print("Neural Strata — the indication vocabulary (routing/dijkstra.py · PATTERNS)\n")
    for name, spec in PATTERNS.items():
        buzzer = "· buzzer" if spec["buzzer"] else ""
        print(f"  {name:16s} {spec['colour']:6s} {spec['period_ms']:5d} ms  "
              f"{spec['meaning']} {buzzer}")
    print("\n  Junctions:  L1 → J1, J2      L2 → J3, J4")
    print("  A hub that has received no command shows the slow amber breathe")
    print("  (NO_COMMAND), never green — see src/level_hub/strips.cpp.")
    return 0


def lines_for(args) -> list[str]:
    """Every `#CMD` line this invocation should send, in order."""
    out: list[str] = []

    if args.demo:
        for junction in LEVEL_JUNCTIONS[args.level]:
            for name in PATTERNS:
                out.append(HubCommand(hub=f"L{args.level}", junction=junction,
                                      pattern=name, reason="led_test --demo").to_line())
        out.append(HubCommand(hub=f"L{args.level}",
                              junction=LEVEL_JUNCTIONS[args.level][0],
                              pattern="advisory_amber",
                              reason="led_test --demo end").to_line())
        return out

    if args.pattern:
        junctions = [args.junction] if args.junction else list(LEVEL_JUNCTIONS[args.level])
        for junction in junctions:
            if junction not in LEVEL_JUNCTIONS[args.level]:
                raise SystemExit(f"junction {junction} is not on level {args.level} "
                                 f"(it carries {', '.join(LEVEL_JUNCTIONS[args.level])})")
            out.append(HubCommand(hub=f"L{args.level}", junction=junction,
                                  pattern=args.pattern,
                                  reason="led_test").to_line())

    if args.level_pattern:
        # The row's fourth command, exercised end to end. Built from `HubCommand`
        # like every other frame here, so the bench cannot drift from the server.
        out.append(HubCommand(hub=f"L{args.level}", cmd="pattern",
                              pattern=args.level_pattern,
                              reason="led_test --level-pattern").to_line())

    if args.siren:
        cmd = {"cmd": "siren", "level": args.level, "on": args.siren == "on"}
        out.append("#CMD " + json.dumps(cmd, separators=(",", ":")))

    if args.advisory:
        cmd = {"cmd": "advisory", "level": args.level, "on": args.advisory == "on"}
        out.append("#CMD " + json.dumps(cmd, separators=(",", ":")))

    return out


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.list:
        return show_vocabulary()

    try:
        lines = lines_for(args)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 2

    if not lines:
        print("nothing to send — try --list, --demo, --pattern, --level-pattern, "
              "--siren or --advisory",
              file=sys.stderr)
        return 2

    port = None
    if args.port and not args.dry_run:
        try:
            import serial                                  # pyserial
        except ImportError:
            print("pyserial is not installed: pip install pyserial", file=sys.stderr)
            return 2
        try:
            port = serial.Serial(args.port, args.baud, timeout=1)
        except Exception as exc:                            # noqa: BLE001
            print(f"could not open {args.port}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 2
        # The hub speaks on its USB console too; print what it says while we drive it,
        # because "the strip did not light" and "the hub refused the command" are two
        # different afternoons of debugging.
        time.sleep(0.4)
        port.reset_input_buffer()
    else:
        print("(dry run — no port, nothing is transmitted)\n")

    try:
        for _ in range(max(1, args.repeat)):
            for line in lines:
                print(f"  → {line}")
                if port is not None:
                    port.write((line + "\n").encode())
                    port.flush()
            if args.repeat > 1:
                time.sleep(max(0.05, args.interval))
    except KeyboardInterrupt:
        pass
    finally:
        if port is not None:
            deadline = time.time() + 1.5
            while time.time() < deadline:
                raw = port.readline()
                if not raw:
                    continue
                print(f"  ← {raw.decode(errors='replace').rstrip()}")
            port.close()

    if port is not None:
        print("\nLook at the strip: the first LED of the chased pattern is the head.")
    print("Done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
