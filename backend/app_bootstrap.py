"""
Legacy file — the actual bootstrap is now backend/app.py.
The monolithic app.py is split as specified in the Phase 12A plan.
"""

import sys
print("[WARNING] app_bootstrap.py is no longer needed — the split is in backend/app.py")
sys.exit(1)

import argparse
import signal
import sys
import threading
import time
from flask import Flask, jsonify
from flask_cors import CORS

import config
from config import env_bool, validate_config
from contracts import flags_to_list, read_session_csv
from engine import cascade as cascade_engine
from engine import crack as crack_engine
from engine import geomech
from engine import health
from engine import severity as severity_engine
from engine import ttf as ttf_model
from ingestion import ring, serial_reader, simulator
from persistence import db
from api import routes_telemetry, routes_ai, routes_admin, routes_blackbox, routes_cloud, routes_map, routes_alerts
from routing import dijkstra as routing
from alerts import dispatcher as alerts_dispatcher
from state import SystemState

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Initialize system state
STATE = SystemState()

def load_model():
    """Load the TTF model at startup."""
    return ttf_model.load_ttf_model()

def load_pattern():
    """Load Model 1 (Phase 5)."""
    from engine import pattern as pattern_engine
    ok = pattern_engine.load_pattern_model()
    if ok:
        meta = pattern_engine.PATTERN_INFO.get("meta") or {}
        print(f"[ai] pattern model loaded: {meta.get('model', 'pattern_model_v1')} "
              f"(threshold {meta.get('operating_threshold')}, "
              f"{len(meta.get('features') or [])} features)")
    else:
        print(f"[ai] pattern model unavailable — cascade runs M2-only "
              f"({pattern_engine.PATTERN_INFO.get('reason')})")
    return ok

def setup_routing():
    """Setup routing subsystem."""
    from api import map_payload
    SITE_META = map_payload.load_site_meta()
    TUNNEL_MAP_PATH = config.ASSET_FILES["tunnel_map"]

    try:
        SITE_MAP = routing.load_tunnel_map(TUNNEL_MAP_PATH)
        print(f"[map] tunnel map loaded: {len(SITE_MAP.nodes)} nodes, "
              f"{len(SITE_MAP.junctions)} junctions, {len(SITE_MAP.exits)} exits, "
              f"{len(SITE_MAP.links)} links")
    except Exception as exc:
        SITE_MAP = None
        print(f"[map] TUNNEL MAP INVALID — /api/map will answer 503. {type(exc).__name__}: {exc}")

    STATE.site_map = SITE_MAP
    STATE.site_meta = SITE_META

    # Bind routes
    app.register_blueprint(routes_map.bp)
    routes_map.bind(SITE_MAP, SITE_META, STATE)

def setup_alerts():
    """Setup alert dispatcher."""
    app.register_blueprint(routes_alerts.bp)
    routes_alerts.bind(STATE, persist=db.persist_alert)

def setup_api_blueprints():
    """Setup all API blueprints."""
    # Each API module is imported above, now register them
    app.register_blueprint(routes_telemetry.bp)
    app.register_blueprint(routes_ai.bp)
    app.register_blueprint(routes_admin.bp)
    app.register_blueprint(routes_blackbox.bp)
    app.register_blueprint(routes_cloud.bp)
    app.register_blueprint(routes_map.bp)
    app.register_blueprint(routes_alerts.bp)

def start_blackbox(port=None, card_mb=None, external_feed=False, sim_skip=None):
    """Bring up the ingest + gate + cloud gateway."""
    from pathlib import Path
    BLACKBOX_DIR = Path(__file__).parent / "blackbox"

    if not (BLACKBOX_DIR / "gateway.py").exists():
        print(f"[blackbox] {BLACKBOX_DIR} not found - /api/extended will omit it")
        return None

    try:
        if str(BLACKBOX_DIR) not in sys.path:
            sys.path.insert(0, str(BLACKBOX_DIR))
        from gateway import Gateway

        BLACKBOX = Gateway(
            port=port, card_mb=card_mb, external_feed=external_feed,
            sim_skip=sim_skip, db_path=config.RUNTIME_DIR / "blackbox.sqlite",
            log=lambda m: print(f"[blackbox] {m}"),
        ).start()

        if BLACKBOX.mode == "SIM":
            why = ("waiting for the serial reader to open the hub's line"
                   if external_feed else "simulator feeding it, no hub attached")
            print(f"[blackbox] gateway up - mode=SIM ({why})")
        else:
            print(f"[blackbox] gateway up - mode={BLACKBOX.mode}")
    except Exception as exc:
        print(f"[blackbox] gateway unavailable ({type(exc).__name__}: {exc})")
        BLACKBOX = None

    return BLACKBOX

def setup_database():
    """Initialize database."""
    db.init_schema(db.get_connection())

def start_ticker():
    """Start the main ticker thread."""
    threading.Thread(target=ticker_thread, daemon=True).start()

def ticker_thread():
    """Main 50ms ticker - Phase 12 replacement for app.py's original ticker."""
    while True:
        t0 = time.time()
        dt = t0 - getattr(ticker_thread, 'prev_t', t0)
        ticker_thread.prev_t = t0

        with STATE.lock:
            apply_injections(dt)
            for node in STATE.iter_nodes():
                heard = STATE.last_arrival.get(node.node_id) is not None
                if heard and STATE.age_of(node.node_id, t0) <= 1.0:
                    continue
                if t0 < getattr(ticker_thread, 'crack_next', {}).get(node.node_id, 0.0):
                    continue
                ticker_thread.crack_next[node.node_id] = t0 + config.MODEL_DT
                for event in crack_engine.update_node_crack(STATE, node.node_id, node, t0):
                    event.publish(STATE)
            crack_engine.crack_tick(STATE, t0)
            update_status_logic()
            STATE.timestamp = iso_now()
            health.health_tick(STATE, t0)

        time.sleep(0.05)

def apply_injections(dt):
    """Advance scenario profiles."""
    return simulator.apply_injections(STATE, dt)

def update_status_logic():
    """Update node and level status."""
    return STATE.update_status_logic()

def iso_now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def setup_serial_handling(args):
    """Setup serial communication handling."""
    global SERIAL_ACTIVE
    SERIAL_ACTIVE = True
    STATE.hardware_source = "usb"

    from ingestion import serial_reader
    serial_reader.start_serial_watchdog(
        STATE, hardware_reader_thread,
        kwargs={"stop_event": STOP_EVENT}, on_status=push_event)

def start_blackbox_if_needed(args):
    """Start blackbox gateway if not disabled."""
    if not args.no_blackbox:
        start_blackbox(card_mb=args.sd_card_mb, sim_skip=args.bbx_sim_skip,
                      external_feed=not (args.no_serial or args.replay))

def handle_replay(args):
    """Handle replay mode."""
    if args.replay:
        SERIAL_ACTIVE = True
        STATE.hardware_source = "replay"
        threading.Thread(target=replay_reader_thread,
                        args=(args.replay, args.speed), daemon=True).start()

def replay_reader_thread(csv_path, speed=1.0):
    """Feed replay data through the pipeline."""
    speed = max(0.01, float(speed or 1.0))
    try:
        rows = read_session_csv(csv_path)
    except Exception as e:
        print(f"[replay] cannot read {csv_path}: {e}")
        return

    if not rows:
        print(f"[replay] {csv_path} contains no rows")
        return

    print(f"[replay] {csv_path} — {len(rows)} rows at {speed:g}x")
    with STATE.lock:
        STATE.hardware_source = "replay"
        STATE.serial_up = True
        push_event("NOMINAL", f"REPLAY attached — {len(rows)} recorded rows at {speed:g}x.")

    t_prev = float(rows[0].get("t_ms") or 0.0)
    for row in rows:
        t_now = float(row.get("t_ms") or 0.0)
        gap = max(0.0, t_now - t_prev) / 1000.0 / speed
        if gap:
            time.sleep(min(gap, 2.0))
        t_prev = t_now
        try:
            ingest_line(json.dumps(row))
        except Exception as e:
            print(f"[replay] row skipped ({e})")

    print("[replay] complete")
    with STATE.lock:
        STATE.serial_up = False
        push_event("NOMINAL", "REPLAY complete — recorded session exhausted.")

def ingest_line(line: str) -> None:
    """Handle one line from the hub."""
    frame = serial_reader.parse_frame(line)
    if frame is None:
        return

    rfid_row = None
    with STATE.lock:
        STATE.timestamp = iso_now()

        if frame.get("uid"):
            rfid_row = serial_reader.handle_rfid_frame(STATE, frame)

        if "node" in frame or "frames" in frame:
            if frame.get("via") or "frames" in frame:
                ring.handle_ring_frame(STATE, frame)
            else:
                serial_reader.handle_node_frame(STATE, frame)

    if rfid_row:
        db.insert_rfid(*rfid_row)

def hardware_reader_thread(stop_event=None):
    """USB serial reader thread."""
    return serial_reader.read_serial_forever(
        STATE, ingest_line, on_status=push_event, stop_event=stop_event)

def push_event(level, text, kind=None):
    """Push event to operator log."""
    STATE.note_event(level, text, kind=kind)

def main():
    """Main application entry point."""
    parser = argparse.ArgumentParser(description="Neural Strata edge server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-serial", action="store_true", help="Skip USB serial (API + dashboard only)")
    parser.add_argument("--bbx-sim-skip", metavar="CHUNK:ROWS", default=None,
                        help="SIMULATOR ONLY: make the stand-in hub lose ROWS rows")
    parser.add_argument("--no-blackbox", action="store_true",
                        help="Do not start the blackbox gateway (/api/extended omits it)")
    parser.add_argument("--sd-card-mb", type=int, default=None,
                        help="Capacity of the microSD in the hub, MB")
    parser.add_argument("--replay", metavar="FILE",
                        help="Replay a recorded session.csv through the live pipeline")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier")

    args = parser.parse_args()

    # Validate configuration before starting
    problems = validate_config()
    if problems:
        print("FATAL — this configuration is not bootable:")
        for problem in problems:
            print(f"  · {problem}")
        if not env_bool("NS_ALLOW_BAD_CONFIG", False):
            sys.exit(2)

    # Setup system components
    load_model()
    load_pattern()
    setup_database()
    setup_routing()
    setup_alerts()
    setup_api_blueprints()
    start_blackbox_if_needed(args)
    handle_replay(args)
    setup_serial_handling(args)
    start_ticker()

    # Setup shutdown handling
    def _shutdown(_signum, _frame):
        if STOP_EVENT.is_set():
            sys.exit(1)
        STOP_EVENT.set()
        print("\nShutting down — closing the serial link…")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Print startup information
    print(f"Neural Strata Edge Server  →  http://127.0.0.1:{args.port}/")
    print("  GET  /api/telemetry  /api/extended  /api/history  /api/ai")
    print("  GET  /api/map  /api/site  /api/health")
    print("  POST /api/sim/trigger  /api/reset  /api/cloud/force_sync")
    print("  GET  /api/sync/status  (outbox depth, last success)")

    app.run(host=args.host, port=args.port, debug=False, threaded=True)

# Global variables
STOP_EVENT = threading.Event()
SERIAL_ACTIVE = False
BLACKBOX = None

if __name__ == "__main__":
    main()