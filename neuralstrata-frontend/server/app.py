"""
Neural Strata — reference backend
=================================

A dependency-free Python server that implements the exact API contract the
dashboard expects. Run it, open http://localhost:8000, and the dashboard's
status ticker flips from SIM to LIVE.

    python3 server/app.py                # port 8000
    python3 server/app.py --port 9000
    python3 server/app.py --no-sim       # serve only what you POST in

Endpoints
---------
    GET  /api/telemetry   500 ms poll — node readings, routes, RFID log
    GET  /api/extended   1500 ms poll — velocities, muster counts, event log
    GET  /api/history               — backfill for the charts, on load
    GET  /api/ai         5000 ms poll — model metadata card
    POST /api/sim/trigger           — {"node": "node_1", "severity": "warning"}
    POST /api/reset                 — clear all injections

Everything else is served as a static file from the project root, so index.html
is available at /.

Replacing the simulation with real hardware
-------------------------------------------
The physics lives in `MineModel`. To feed real sensors instead, keep the JSON
shapes in `telemetry()` / `extended()` identical and fill them from your data
source (Modbus, MQTT, LoRa gateway, a database...). Nothing else has to change.
"""

import argparse
import json
import math
import os
import random
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------
# thresholds — these mirror the front-end's own classification exactly
# --------------------------------------------------------------------------
ACC_CRITICAL = 3.0     # m/s^2 — node is failing
ACC_WARNING = 0.25     # m/s^2 — node is creeping
DISPLACEMENT_LIMIT_MM = 100.0

CREW = [
    ("001", "Deepak",  "Tunnel A"),
    ("002", "Anil",    "Tunnel A"),
    ("003", "Meera",   "Tunnel B"),
    ("004", "Ravi",    "Tunnel A"),
    ("005", "Sunita",  "Tunnel B"),
    ("006", "Vikram",  "Safe Exit"),
    ("007", "Pooja",   "Headframe"),
    ("008", "Arjun",   "Tunnel B"),
    ("009", "Isha",    "Safe Exit"),
    ("010", "Kunal",   "Surface Camp"),
]


def clock() -> str:
    return datetime.now().strftime("%H:%M:%S")


class Node:
    """One instrumented mesh cell (node_1 = E5, node_2 = E8)."""

    def __init__(self, base_disp, base_tilt, phase):
        self.base = base_disp
        self.tilt_base = base_tilt
        self.phase = phase
        self.d = base_disp        # displacement, mm
        self.tilt = base_tilt     # tilt, degrees
        self.v = 0.0              # velocity, mm/s
        self.a = 0.0              # acceleration, m/s^2
        self.status = "NOMINAL"
        self.scenario = None      # None | "warning" | "critical"
        self.t = 0.0              # seconds since the injection started
        self.peak = 0.02

    def acceleration_from_scenario(self):
        if not self.scenario:
            return 0.0
        t = self.t
        if self.scenario == "warning":
            # a bump that rises fast, decays, and settles out by ~46 s
            taper = 1.0 if t < 34 else max(0.0, 1 - (t - 34) / 8)
            return 0.95 * (1 - math.exp(-t / 1.6)) * math.exp(-t / 10) * taper
        if self.scenario == "critical":
            # runaway creep with a slight oscillation, plateauing near 3.6
            return 3.6 * (1 - math.exp(-t / 1.4)) * (1 + 0.12 * math.sin(t * 2.1))
        return 0.0

    def step(self, h, T, events, name):
        if self.scenario:
            self.t += h
            a = self.acceleration_from_scenario()
        else:
            a = 0.012 * math.sin(T * 1.7 + self.phase) + (random.random() - 0.5) * 0.02

        self.v += a * h
        if not self.scenario:
            self.v *= 0.995
        self.v = max(-0.4, min(12.0, self.v))
        self.d = max(0.0, min(DISPLACEMENT_LIMIT_MM, self.d + self.v * h))
        self.a += (a - self.a) * 0.55
        self.tilt = (self.tilt_base
                     + 0.5 * math.sin(T * 0.07 + self.phase)
                     + self.a * 0.9
                     + 0.1 * math.sin(T * 0.21 + self.phase * 2))

        if self.a >= ACC_CRITICAL:
            status = "CRITICAL"
        elif self.a > ACC_WARNING:
            status = "WARNING"
        else:
            status = "NOMINAL"

        # a warning-grade creep event settles on its own
        if self.scenario == "warning" and self.t > 46:
            self.scenario = None
            self.v *= 0.3
            self.d = min(self.d, self.base + 2.5)
            status = "NOMINAL"
            events.append(("NOMINAL", f"{name.upper()} creep settled — node back to NOMINAL."))

        if self.status != "CRITICAL" and self.a < 2.2 and self.a < ACC_WARNING:
            status = "NOMINAL"

        self.status = status
        self.peak = max(self.peak * 0.999, abs(self.a))


class MineModel:
    """
    The whole sector: two instrumented nodes, two egress routes, ten crew.

    Thread-safe; a background thread advances it at 50 Hz so the numbers move
    smoothly regardless of how often clients poll.
    """

    def __init__(self, simulate=True):
        self.lock = threading.RLock()
        self.simulate = simulate
        self.nodes = {
            "node_1": Node(5.2, 1.1, 1.3),
            "node_2": Node(4.1, 0.8, 4.1),
        }
        self.rfid = [
            {"id": c[0], "name": c[1], "zone": c[2],
             "time": datetime.fromtimestamp(time.time() - (4 + i * 11)).strftime("%H:%M:%S"),
             "_t": time.time() - (4 + i * 11)}
            for i, c in enumerate(CREW)
        ]
        self.events = [{"time": clock(), "level": "NOMINAL",
                        "text": "System initialised — all cells stable, routes open."}]
        self.history = {"node_1": [], "node_2": []}
        self.T = 0.0
        self.evac_stage = 0
        self.evac_t = 0.0
        self.route_latch = {"A": False, "B": False}
        self._last_hist = 0.0

    # ------------------------------------------------------------------ log
    def log(self, level, text):
        self.events.insert(0, {"time": clock(), "level": level, "text": text})
        del self.events[40:]

    def swipe(self, badge_id, zone):
        for r in self.rfid:
            if r["id"] == badge_id and r["zone"] != zone:
                r["zone"] = zone
                r["time"] = clock()
                r["_t"] = time.time()
                break
        self.rfid.sort(key=lambda r: r["_t"], reverse=True)

    # ----------------------------------------------------------------- step
    def step(self, h):
        with self.lock:
            self.T += h
            pending = []
            for name, node in self.nodes.items():
                node.step(h, self.T, pending, name)
            for level, text in pending:
                self.log(level, text)

            sa = self.nodes["node_1"].status
            sb = self.nodes["node_2"].status
            ra = "BLOCKED" if sa in ("WARNING", "CRITICAL") else "OPEN"
            rb = "BLOCKED" if sb in ("WARNING", "CRITICAL") else "OPEN"

            if ra == "BLOCKED" and not self.route_latch["A"]:
                self.route_latch["A"] = True
                self.log(sa, "ROUTE A BLOCKED — Level 1 drift LEDs red.")
            if rb == "BLOCKED" and not self.route_latch["B"]:
                self.route_latch["B"] = True
                self.log(sb, "ROUTE B BLOCKED — Level 2 drift LEDs red.")
            if ra == "OPEN":
                self.route_latch["A"] = False
            if rb == "OPEN":
                self.route_latch["B"] = False

            self._evacuation(sa == "CRITICAL" or sb == "CRITICAL")
            self._record_history()
            return ra, rb

    def _evacuation(self, critical):
        if not critical:
            return
        if self.evac_stage == 0:
            self.evac_stage = 1
            self.evac_t = self.T
            self.log("CRITICAL", "MUSTER CALL — crews in tunnels must evacuate.")
        elif self.evac_stage == 1 and self.T - self.evac_t > 1.5:
            for r in list(self.rfid):
                if r["zone"] == "Tunnel A":
                    self.swipe(r["id"], "Evacuating A")
                elif r["zone"] == "Tunnel B":
                    self.swipe(r["id"], "Evacuating B")
            self.evac_stage = 2
            self.evac_t = self.T
            self.log("CRITICAL", "Crews evacuating — RFID gates live.")
        elif self.evac_stage == 2 and self.T - self.evac_t > 6.5:
            for r in list(self.rfid):
                if r["zone"].startswith("Evacuating"):
                    self.swipe(r["id"], "Safe Exit")
            self.evac_stage = 3
            self.log("WARNING", "All underground crews at Safe Exit.")

    def _record_history(self):
        # one sample per node per second, capped at 240 (the chart's MAXPTS)
        if self.T - self._last_hist < 1.0:
            return
        self._last_hist = self.T
        for name, n in self.nodes.items():
            self.history[name].append({
                "status": n.status,
                "tilt_x": round(n.tilt, 2),
                "displacement_mm": round(n.d, 1),
                "acceleration": round(n.a, 3),
            })
            del self.history[name][:-240]

    # ------------------------------------------------------------- payloads
    def _worst_status(self):
        s = [n.status for n in self.nodes.values()]
        if "CRITICAL" in s:
            return "CRITICAL"
        if "WARNING" in s:
            return "WARNING"
        return "NOMINAL"

    def _ttf(self):
        best = None
        for n in self.nodes.values():
            if n.scenario and n.v > 0.05 and n.a > 0.5:
                t = max(3, min(999, n.v / n.a))
                best = round(t) if best is None else min(best, round(t))
        return best if best is not None else "N/A"

    def telemetry(self):
        with self.lock:
            sa = self.nodes["node_1"].status
            sb = self.nodes["node_2"].status
            return {
                "system_status": self._worst_status(),
                "ttf_seconds": self._ttf(),
                "nodes": {
                    name: {
                        "status": n.status,
                        "tilt_x": round(n.tilt, 2),
                        "displacement_mm": round(n.d, 1),
                        "acceleration": round(n.a, 3),
                    } for name, n in self.nodes.items()
                },
                "underground": {
                    "route_A_status": "BLOCKED" if sa in ("WARNING", "CRITICAL") else "OPEN",
                    "route_B_status": "BLOCKED" if sb in ("WARNING", "CRITICAL") else "OPEN",
                },
                "rfid_log": [
                    {"id": r["id"], "name": r["name"], "zone": r["zone"], "time": r["time"]}
                    for r in self.rfid[:10]
                ],
            }

    def extended(self):
        with self.lock:
            underground = sum(1 for r in self.rfid
                              if r["zone"] in ("Tunnel A", "Tunnel B")
                              or r["zone"].startswith("Evacuating"))
            safe = sum(1 for r in self.rfid if r["zone"] == "Safe Exit")
            return {
                "mode": "LIVE",
                "nodes": {
                    name: {"velocity": round(n.v, 2),
                           "peak_acceleration": round(n.peak, 2)}
                    for name, n in self.nodes.items()
                },
                "muster": {"total": len(self.rfid), "underground": underground, "safe": safe},
                "events": self.events[:24],
            }

    def history_payload(self):
        with self.lock:
            return {k: list(v) for k, v in self.history.items()}

    def ai(self):
        with self.lock:
            worst = max(n.a for n in self.nodes.values())
            confidence = 0.93 if worst < ACC_WARNING else 0.78 if worst < ACC_CRITICAL else 0.66
            return {
                "model": "strata-lstm v2.4",
                "status": "ONLINE",
                "horizon": "45 min",
                "last_train": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "confidence": f"{confidence:.0%}",
                "note": "Forecast from displacement, velocity and tilt across the mesh.",
            }

    # ------------------------------------------------------------- controls
    def trigger(self, node, severity):
        severity = (severity or "").lower()
        if node not in self.nodes or severity not in ("warning", "critical"):
            return False
        with self.lock:
            n = self.nodes[node]
            n.scenario = severity
            n.t = 0.0
            n.v = max(n.v, 0.0)
            label = "NODE 1" if node == "node_1" else "NODE 2"
            cell = "E5" if node == "node_1" else "E8"
            self.log("CRITICAL" if severity == "critical" else "WARNING",
                     f"{label} ({cell}) — {severity.upper()} injection armed.")
        return True

    def reset(self):
        with self.lock:
            for name, n in self.nodes.items():
                n.scenario = None
                n.t = 0.0
                n.v = 0.0
                n.a = 0.0
                n.d = n.base
                n.tilt = n.tilt_base
                n.status = "NOMINAL"
                n.peak = 0.02
            for i, c in enumerate(CREW):
                self.rfid[i] = {"id": c[0], "name": c[1], "zone": c[2],
                                "time": clock(), "_t": time.time() - i}
            self.evac_stage = 0
            self.route_latch = {"A": False, "B": False}
            self.log("NOMINAL", "System re-armed — all injections cleared.")


MODEL = MineModel()


def physics_thread():
    """Advance the model at a fixed 50 Hz."""
    h = 0.02
    nxt = time.perf_counter()
    while True:
        MODEL.step(h)
        nxt += h
        delay = nxt - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        else:
            nxt = time.perf_counter()


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # -------------------------------------------------------------- helpers
    def _send_json(self, obj, code=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, relpath):
        path = os.path.normpath(os.path.join(ROOT, relpath.lstrip("/")))
        if not path.startswith(ROOT) or not os.path.isfile(path):
            self.send_error(404, "Not found")
            return
        ext = os.path.splitext(path)[1].lower()
        ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css", ".json": "application/json",
                 ".png": "image/png", ".jpg": "image/jpeg",
                 ".svg": "image/svg+xml", ".ico": "image/x-icon",
                 ".md": "text/markdown; charset=utf-8"}.get(ext, "application/octet-stream")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- requests
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        route = urlparse(self.path).path
        if route == "/api/telemetry":
            return self._send_json(MODEL.telemetry())
        if route == "/api/extended":
            return self._send_json(MODEL.extended())
        if route == "/api/history":
            return self._send_json(MODEL.history_payload())
        if route == "/api/ai":
            return self._send_json(MODEL.ai())
        if route.startswith("/api/"):
            return self._send_json({"error": "unknown endpoint"}, 404)
        if route in ("/", ""):
            return self._send_file("index.html")
        return self._send_file(route)

    def do_POST(self):
        route = urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, TypeError):
            payload = {}

        if route == "/api/sim/trigger":
            ok = MODEL.trigger(payload.get("node"), payload.get("severity"))
            return self._send_json({"ok": ok}, 200 if ok else 400)
        if route == "/api/reset":
            MODEL.reset()
            return self._send_json({"ok": True})
        return self._send_json({"error": "unknown endpoint"}, 404)

    def log_message(self, fmt, *args):
        # keep the console readable — drop the per-poll noise.
        # note: log_error() routes here too, with an int status as args[0],
        # so stringify before testing.
        if "/api/" not in " ".join(str(a) for a in args):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description="Neural Strata reference backend")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--no-sim", action="store_true",
                    help="do not advance the physics model (feed it yourself)")
    args = ap.parse_args()

    if not args.no_sim:
        threading.Thread(target=physics_thread, daemon=True).start()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Neural Strata backend  →  http://localhost:{args.port}")
    print("  GET  /api/telemetry  /api/extended  /api/history  /api/ai")
    print("  POST /api/sim/trigger  /api/reset")
    print("Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
