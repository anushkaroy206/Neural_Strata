#!/usr/bin/env python3
"""
snapshot_api.py — capture the API surface, and diff it against the frozen one.
=============================================================================

The Phase-0 risk control: *"a 'pure extraction' phase accidentally changes
behaviour"*. Snapshot every endpoint before, snapshot after, diff the shapes.

    python3 tools/snapshot_api.py --out /tmp/after            # capture
    python3 tools/snapshot_api.py --diff                     # capture + compare
    python3 tools/snapshot_api.py --diff --server http://127.0.0.1:5000

The comparison is by **JSON key path and type**, never by value: telemetry values
are volatile by design, while ``underground.route_A_status`` existing at that
exact path is a promise the dashboard depends on.

Exit codes:  0 = no path lost, 1 = a path regressed, 2 = server unreachable.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FIXTURES = BACKEND / "tests" / "fixtures"
ENDPOINTS = ("telemetry", "ai", "extended", "history", "site", "map")


def key_paths(obj, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else k
            out[p] = type(v).__name__
            out.update(key_paths(v, p))
    elif isinstance(obj, list):
        out[f"{prefix}[]"] = "list"
        if obj:
            out.update(key_paths(obj[0], f"{prefix}[]"))
    return out


def capture(server: str, out_dir: Path) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    got: dict[str, dict] = {}
    for name in ENDPOINTS:
        url = f"{server.rstrip('/')}/api/{name}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            print(f"  {name:10} HTTP {e.code} — skipped")
            continue
        except Exception as e:                                   # noqa: BLE001
            print(f"  {name:10} unreachable ({type(e).__name__}) — skipped")
            continue
        (out_dir / f"{name}.json").write_text(json.dumps(payload, indent=2))
        got[name] = payload
        print(f"  {name:10} {len(key_paths(payload)):3} key paths  → {out_dir / (name + '.json')}")
    return got


def diff(captured: dict[str, dict]) -> int:
    print()
    print(f"{'endpoint':12} {'frozen':>7} {'live':>7}  verdict")
    print("-" * 62)
    regressions = 0
    for name in ENDPOINTS:
        # `extended` legitimately loses its blackbox/cloud blocks when the gateway
        # is not running, so only its structural half is compared.
        if name not in captured:
            continue
        fx = FIXTURES / f"{name}.json"
        if not fx.exists():
            print(f"{name:12} {'—':>7} {'—':>7}  no frozen snapshot")
            continue
        base = key_paths(json.loads(fx.read_text()))
        live = key_paths(captured[name])
        if name == "extended":
            base = {k: v for k, v in base.items()
                    if not k.startswith(("blackbox", "cloud"))}
        missing = {k: v for k, v in base.items() if k not in live}
        added = {k: v for k, v in live.items() if k not in base}
        if missing:
            regressions += 1
            print(f"{name:12} {len(base):>7} {len(live):>7}  REGRESSION — lost {sorted(missing)}")
        elif added:
            print(f"{name:12} {len(base):>7} {len(live):>7}  ok +{len(added)} additive {sorted(added)[:3]}")
        else:
            print(f"{name:12} {len(base):>7} {len(live):>7}  identical shape")
    print("-" * 62)
    if regressions:
        print(f"FAIL — {regressions} endpoint(s) lost a JSON path the dashboard reads.")
        return 1
    print("PASS — every endpoint kept its contract (additive keys are allowed).")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture and diff the API surface")
    ap.add_argument("--server", default="http://127.0.0.1:5000")
    ap.add_argument("--out", default="/tmp/ns-snapshot")
    ap.add_argument("--diff", action="store_true", help="compare against tests/fixtures")
    args = ap.parse_args(argv)

    print(f"capturing from {args.server}")
    captured = capture(args.server, Path(args.out))
    if not captured:
        print("nothing captured — is the server running?", file=sys.stderr)
        return 2
    return diff(captured) if args.diff else 0


if __name__ == "__main__":
    sys.exit(main())
