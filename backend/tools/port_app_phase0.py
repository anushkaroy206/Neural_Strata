#!/usr/bin/env python3
"""
Phase 0 — the app.py port (one-shot migration record).
=====================================================

`backend/app.py` is the v4.4 edge server with the Phase-0 extractions applied.
This script is the record of exactly what moved and why, so the change is
auditable rather than archaeological:

    python3 tools/port_app_phase0.py --source <v4.4/app.py> --out backend/app.py

Every replacement asserts it matched **exactly once**. If the source file changes,
the script fails loudly instead of silently producing a half-ported app.

What moved, and what deliberately did not
-----------------------------------------
Moved out (implementation now in its own module, name kept here where the rest of
the file — or the runbook — calls it):

    _load_dotenv()            → config.load_dotenv()
    the constant block        → config.py
    AI_FEATURES               → engine.features.FEATURES         (16, was mislabelled 14)
    _live_val_at()            → engine.features.live_val_at()
    compute_live_features()   → engine.features.compute_live_features()
    load_model()              → engine.ttf.load_ttf_model()
    model_ttf_current()       → engine.ttf.model_fresh()
    run_model_inference()     → engine.ttf.run_inference()
    get_ai() body             → engine.ttf.model_status()
    get_db_connection()       → persistence.db.get_conn() (pooled)
    log_telemetry_rows()      → persistence.db.insert_telemetry()

Extracted (not moved) so two callers cannot drift apart:

    the serial reader's per-line body → ingest_line(), now shared by
    hardware_reader_thread() and the new replay_reader_thread().

Deliberately NOT changed — this phase must not alter behaviour:

  * `telemetry_state` still carries **two** nodes. The four-node contract lives in
    `config.NODE_IDS`; expanding the live state is Phase 1 (B1). Changing it here
    would change `/api/telemetry` and the dashboard would be the first to know.
  * node 3/4 frames still map onto node_2, exactly as v4.4 did. Wrong, but it is
    Phase 1's to fix, and "fixing" it here would be an unannounced behaviour change.
  * `apply_injections(0.05)` still assumes a 50 ms tick (defect D14, Phase 1).
  * No route was added, removed or renamed.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 1. Module docstring — record the phase in the file itself
# ---------------------------------------------------------------------------

DOC_OLD = """* /api/ai reports the real model name, status (ACTIVE / WARMING UP /
  FALLBACK), confidence (from the held-out MAE) and trained_at timestamp.
\"\"\""""

DOC_NEW = """* /api/ai reports the real model name, status (ACTIVE / WARMING UP /
  FALLBACK), confidence (from the held-out MAE) and trained_at timestamp.

PHASE 0 (v6.0) — this file is now bootstrap, not a junk drawer
--------------------------------------------------------------
Configuration, the frozen contracts, the feature extractor, MODEL 2 and the
database layer all moved into their own modules; this server imports them and
keeps the old names where the runbook or the dashboard still calls them:

    config.py            every constant, env-parsed, validated before Flask starts
    contracts.py         schema version, node ids, frame validators, session.csv
    engine/features.py   the 16-feature rolling-window extractor
    engine/ttf.py        MODEL 2 — regressor, freshness rule, physics sanity gate
    persistence/db.py    pooled connections, schema init, migrations

Two new capabilities, no route changes:

  --replay FILE        feed a recorded session.csv through the *same* ingest path
                       the USB reader uses (10x or faster), so every later phase
                       is developable without the rig on the desk
  boot validation      `validate_config()` exits non-zero on nonsense thresholds,
                       a node on two levels, or a port outside 1-65535 — a missing
                       model or map still only degrades, never crashes
\"\"\""""


# ---------------------------------------------------------------------------
# 2. Imports — drop what moved, import the new homes
# ---------------------------------------------------------------------------

IMPORTS_OLD = """import numpy as np
import psycopg2
import serial
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS"""

IMPORTS_NEW = """import serial
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

# --- Phase 0: these used to be defined further down this file ----------------
from config import (                                                    # noqa: E402
    ACC_CRITICAL, ACC_WARNING, ACTIVE_NODES, ASSET_FILES, BAUD_RATE, BLACKBOX_DIR,
    FRONTEND_DIR, HISTORY_INTERVAL_S, HISTORY_MAX, MODEL_BUF, RUNTIME_DIR,
    SERIAL_PORT, VEL_WARNING, env_bool, validate_config,
)
from contracts import migrate_payload, read_session_csv                 # noqa: E402
# imported as ttf_model on purpose: update_status_logic() has a *local* named
# `ttf` (the kinematic v/a estimate). A bare `import ttf` would be shadowed by it
# and every model call inside that function would raise UnboundLocalError.
from engine import ttf as ttf_model                                     # noqa: E402
from persistence import db                                              # noqa: E402"""


# ---------------------------------------------------------------------------
# 3. The xgboost import block moves into engine.ttf
# ---------------------------------------------------------------------------

XGB_OLD = """try:
    import xgboost as xgb
except ImportError:          # Phase 4 is optional — the server must boot without it
    xgb = None

"""


# ---------------------------------------------------------------------------
# 4. The state block that replaces ROOT…state_lock
# ---------------------------------------------------------------------------

STATE_NEW = """# ---------------------------------------------------------------------------
# Phase 0 — everything that used to sit above this line is now imported:
# the .env loader, the database/serial/threshold constants, the 16-feature list,
# and the model globals. What remains here is live process state, unchanged.
# ---------------------------------------------------------------------------

#: 60 s rolling model buffers, one per live node (same cadence as training).
node_buffers = {n: deque(maxlen=MODEL_BUF) for n in ACTIVE_NODES}

state_lock = threading.RLock()"""


# ---------------------------------------------------------------------------
# 5. The black-box gateway path
# ---------------------------------------------------------------------------

BBX_COMMENT_OLD = """# --- blackbox gateway (Option A: the real ingest + gate path) ---------------
# GET /api/extended serves the deck's `blackbox` and `cloud` blocks from the
# truth store the hub feeds over the serial line -- see neuralstrata-backend-v5.
# If that package is not importable the server still boots; the blocks are simply
# absent and the deck keeps its own defaults rather than showing invented numbers."""

BBX_COMMENT_NEW = """# --- blackbox gateway (Option A: the real ingest + gate path) ---------------
# GET /api/extended serves the deck's `blackbox` and `cloud` blocks from the
# truth store the hub feeds over the serial line -- see backend/blackbox/.
# If that package is not importable the server still boots; the blocks are simply
# absent and the deck keeps its own defaults rather than showing invented numbers."""

BBX_DIR_OLD = '_BBX_DIR = ROOT.parent / "neuralstrata-backend-v5"'
BBX_DIR_NEW = '_BBX_DIR = BLACKBOX_DIR'

BBX_DB_OLD = '            db_path=ROOT.parent / "runtime" / "blackbox.sqlite",'
BBX_DB_NEW = '            db_path=RUNTIME_DIR / "blackbox.sqlite",'

MAP_OLD = 'TUNNEL_MAP_PATH = ROOT / "tunnel_map.json"'
MAP_NEW = 'TUNNEL_MAP_PATH = ASSET_FILES["tunnel_map"]'


# ---------------------------------------------------------------------------
# 6. The node-status hook for engine.ttf (no import cycle)
# ---------------------------------------------------------------------------

HOOK_OLD = """events_log = [{"time": time.strftime("%H:%M:%S"), "level": "NOMINAL", "text": "System initialised — awaiting hardware telemetry."}]"""

HOOK_NEW = """# engine.ttf reads node status through this hook, so it never has to import
# this module (which would be a cycle).
ttf_model.set_node_provider(lambda n_id: telemetry_state["nodes"][n_id])

events_log = [{"time": time.strftime("%H:%M:%S"), "level": "NOMINAL", "text": "System initialised — awaiting hardware telemetry."}]"""


# ---------------------------------------------------------------------------
# 7. Database access — pooled, and quiet when the database is down
# ---------------------------------------------------------------------------

DB_OLD = '''def get_db_connection():
    try:
        return psycopg2.connect(host=DB_HOST, database=DB_NAME, user=DB_USER, password=DB_PASS)
    except Exception as e:
        print(f"DB Conn Error: {e}")
        return None


def log_telemetry_rows(rows):
    """Phase 4 — persist both nodes' state to PostgreSQL (called from ticker @ 2 Hz)."""
    try:
        conn = get_db_connection()
        if not conn:
            return
        cur = conn.cursor()
        cur.executemany(
            "INSERT INTO sensor_telemetry (node_id, tilt_x, displacement_mm, acceleration) VALUES (%s, %s, %s, %s)",
            rows,
        )
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"DB log error: {e}")'''

DB_NEW = '''def get_db_connection():
    """Borrow a pooled connection, or None when the database is unreachable.

    Phase 0 (D11): the body moved to ``persistence.db.get_conn()`` — one pooled
    connection per call instead of one TCP connect per packet at 2 Hz. The name
    and the "returns None, never raises" contract are unchanged, so every caller
    in this file is untouched.
    """
    return db.try_get_conn()


def log_telemetry_rows(rows):
    """Phase 4 — persist both nodes' state to PostgreSQL (called from ticker @ 2 Hz).

    Phase 0: the SQL moved to ``persistence.db.insert_telemetry`` so the column
    list lives next to the schema file that defines it.
    """
    return db.insert_telemetry(rows)'''


# ---------------------------------------------------------------------------
# 8. MODEL 2 — the whole block becomes four thin delegates
# ---------------------------------------------------------------------------

MODEL_NEW = '''# ---------------------------------------------------------------------------
# Phase 0 — MODEL 2 delegates. The implementation lives in engine/ttf.py; these
# names are kept because the rest of this file, and the runbook, call them.
# ---------------------------------------------------------------------------

def load_model():
    """Load the TTF model at startup. Missing/broken => kinematic fallback.

    Implementation: ``engine.ttf.load_ttf_model()``. The printed lines
    ("[ai] TTF model loaded: …") are unchanged, so the runbook still matches.
    """
    return ttf_model.load_ttf_model()


def model_ttf_current():
    """True when the model has a recent, valid prediction to trust."""
    return ttf_model.model_fresh()


def run_model_inference():
    """1 Hz: predict per-node TTF from the rolling buffers (ticker thread).

    Implementation: ``engine.ttf.run_inference()``, which owns the scaler, the
    clamp and the physics sanity gate. The buffers stay here — this process owns
    its node registry.
    """
    ttf_model.run_inference(node_buffers)


'''


# ---------------------------------------------------------------------------
# 9. Ingestion — one path, two sources
# ---------------------------------------------------------------------------

READER_NEW = '''def ingest_line(line: str) -> None:
    """Handle one line from the hub — **the single ingestion path**.

    Phase 0 extracted this body out of ``hardware_reader_thread`` so the USB
    reader and the ``--replay`` source feed the same code. Two copies of the
    ingestion rules is exactly how a replay stops predicting what the live system
    does, and then stops being trusted.

    Order matters and is unchanged: the black-box pipeline sees every line first
    (protocol lines are consumed there), then the telemetry/RFID handling runs.
    An unparseable line is ignored, never fatal.
    """
    # every line goes to the blackbox pipeline first: protocol lines
    # (CHUNK_*, DROP, BLACKBOX) are consumed there, telemetry rows are
    # stored and gated. Unknown lines are ignored, never fatal.
    if BLACKBOX is not None:
        BLACKBOX.feed(line)
    data = json.loads(line)
    rfid_row = None
    with state_lock:
        telemetry_state["timestamp"] = iso_now()

        if "uid" in data and data["uid"]:
            uid = str(data["uid"])
            miner_name = MINERS.get(uid, "Personnel Tag")
            reader = (data.get("reader") or data.get("reader_id")
                      or ("L%d" % data["level"] if data.get("level") in (1, 2) else None))
            zone = zone_for(reader)
            rfid_row = (uid, zone)

            log_entry = {"id": uid, "name": miner_name, "zone": zone, "time": time.strftime("%H:%M:%S")}
            telemetry_state["rfid_log"].insert(0, log_entry)
            note_zone(uid, zone)
            if len(telemetry_state["rfid_log"]) > 10:
                telemetry_state["rfid_log"].pop()

            push_event("NOMINAL", f"RFID {uid} ({miner_name}) registered at Hub.")

        if "node" in data:
            n_id = "node_1" if str(data["node"]) == "1" else "node_2"
            node = telemetry_state["nodes"][n_id]
            if not injections.get(n_id):
                raw_disp = float(data.get("disp", node["displacement_mm"]))
                raw_tilt = float(data.get("tilt", node["tilt_x"]))
                node["tilt_x"] = raw_tilt
                calculate_derivatives(n_id, raw_disp)
                update_status_logic()

    if rfid_row:
        # Phase 0: pooled + throttled (persistence.db), still best-effort.
        db.insert_rfid(*rfid_row)


def hardware_reader_thread():
    """Reads USB Serial JSON from ESP32 Hub, updates state, and logs to PostgreSQL."""
    global telemetry_state

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to ESP32 Hub on {SERIAL_PORT}")
        if BLACKBOX is not None:
            # the only reader of this port is us; the gateway gets the write side
            BLACKBOX.attach_writer(ser.write)
        with state_lock:
            push_event("NOMINAL", "Hardware serial link established.")
    except Exception as e:
        print(f"Failed to connect to Serial ({SERIAL_PORT}): {e}")
        with state_lock:
            push_event("WARNING", "Hardware offline. Serial connection failed — API still live.")
        return

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode("utf-8").strip()
                ingest_line(line)
        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Read error: {e}")

        time.sleep(0.05)


def replay_reader_thread(csv_path, speed=1.0):
    """Feed a recorded session through ``ingest_line()`` — the same path as USB.

    Phase 0's whole point: record a live session once, then develop every later
    phase offline, deterministically, at 10x speed. The replay clock is the CSV's
    own ``t_ms`` column divided by ``speed``, so the sequence of states is the
    recorded one and only the wall-clock spacing changes.

    A gap longer than 2 s (after dividing by speed) is capped, so a session that
    contains a coffee break does not stall a rehearsal.
    """
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
    with state_lock:
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
    with state_lock:
        push_event("NOMINAL", "REPLAY complete — recorded session exhausted.")


'''


# ---------------------------------------------------------------------------
# 10. Thresholds from config, and /api/ai delegating to engine.ttf
# ---------------------------------------------------------------------------

VEL_OLD = "        if a > ACC_WARNING and v > 0.03:"
VEL_NEW = "        if a > ACC_WARNING and v > VEL_WARNING:"

TTF_VALS_OLD = "        vals = [v for v in model_ttf.values() if v is not None]"
TTF_VALS_NEW = "        vals = [v for v in ttf_model.MODEL_TTF.values() if v is not None]"

TICKER_OLD = '''            if t0 - last_hist >= HISTORY_INTERVAL:'''
TICKER_NEW = '''            if t0 - last_hist >= HISTORY_INTERVAL_S:'''

AVAIL_OLD = '        if model_info["available"] and t0 - last_infer >= 1.0:'
AVAIL_NEW = '        if ttf_model.MODEL_INFO["available"] and t0 - last_infer >= 1.0:'


# ---------------------------------------------------------------------------
# 11. main() — validate before boot, and --replay
# ---------------------------------------------------------------------------

MAIN_OLD = '''    parser.add_argument("--sd-card-mb", type=int, default=None,
                        help="Capacity of the microSD in the hub, MB (reported to the deck)")
    args = parser.parse_args()

    global SERIAL_ACTIVE
    load_model()
    threading.Thread(target=ticker_thread, daemon=True).start()

    # The gateway comes up *before* the serial reader: the reader hands it the
    # write side of the port the moment it connects, and passes it every line it
    # reads. Starting it after would mean the port opens while BLACKBOX is still
    # None, and the downlink (ACK_DELETE) would be lost for the whole session.
    if not args.no_blackbox:
        # with --no-serial there is no line to share, so the gateway runs its own
        # simulator; otherwise the serial reader below feeds it.
        start_blackbox(card_mb=args.sd_card_mb,
                       external_feed=not args.no_serial)

    if not args.no_serial:
        SERIAL_ACTIVE = True
        threading.Thread(target=hardware_reader_thread, daemon=True).start()
    else:
        with state_lock:
            push_event("NOMINAL", "Started without serial — waiting for hardware or scenario injection.")'''

MAIN_NEW = '''    parser.add_argument("--sd-card-mb", type=int, default=None,
                        help="Capacity of the microSD in the hub, MB (reported to the deck)")
    parser.add_argument("--replay", metavar="FILE", default=None,
                        help="Replay a recorded session.csv through the live pipeline "
                             "instead of the USB serial port")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier (10 = ten times faster than real time)")
    args = parser.parse_args()

    # Phase 0 — fail loudly on nonsense *before* Flask starts. Thresholds that
    # cannot mean anything, a node living on two levels, a port outside 1-65535.
    # A missing model or map is NOT fatal: those degrade (LOG + DEGRADE, NEVER CRASH).
    problems = validate_config()
    if problems:
        print("FATAL — this configuration is not bootable:")
        for problem in problems:
            print(f"  · {problem}")
        print("  (fix .env, or set NS_ALLOW_BAD_CONFIG=1 to boot anyway)")
        if not env_bool("NS_ALLOW_BAD_CONFIG", False):
            sys.exit(2)

    global SERIAL_ACTIVE
    load_model()
    threading.Thread(target=ticker_thread, daemon=True).start()

    # The gateway comes up *before* the serial reader: the reader hands it the
    # write side of the port the moment it connects, and passes it every line it
    # reads. Starting it after would mean the port opens while BLACKBOX is still
    # None, and the downlink (ACK_DELETE) would be lost for the whole session.
    if not args.no_blackbox:
        # With --no-serial or --replay there is no line to share, so the gateway
        # runs its own simulator; otherwise the serial reader below feeds it.
        start_blackbox(card_mb=args.sd_card_mb,
                       external_feed=not (args.no_serial or args.replay))

    if args.replay:
        SERIAL_ACTIVE = True
        threading.Thread(target=replay_reader_thread,
                         args=(args.replay, args.speed), daemon=True).start()
    elif not args.no_serial:
        SERIAL_ACTIVE = True
        threading.Thread(target=hardware_reader_thread, daemon=True).start()
    else:
        with state_lock:
            push_event("NOMINAL", "Started without serial — waiting for hardware or scenario injection.")'''


# ---------------------------------------------------------------------------
# The transform
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 12. /api/ai — the whole body becomes one delegate call
# ---------------------------------------------------------------------------

GET_AI_NEW = '''def get_ai():
    """Phase 0 — the body moved to ``engine.ttf.model_status()``, which owns the
    model, the freshness rule, the physics gate and the three status strings.

    The response is unchanged key-for-key: model, status, horizon, last_train,
    confidence, note, forecasts. The dashboard's AI card and its colour rule for
    ACTIVE / WARMING UP / FALLBACK read exactly what they read before.
    """
    with state_lock:
        return jsonify(ttf_model.model_status())


'''


#: Every identifier the extraction was supposed to remove. If one of these
#: survives the transform, the port is incomplete — and a stale reference to a
#: deleted global fails at *request* time, not at boot time, which is how a
#: "pure extraction" ships a 500 on /api/ai.
FORBIDDEN_AFTER_PORT = (
    "model_info", "model_ttf", "model_last_ok",
    "AI_FEATURES", "AI_FUK_V_MIN", "_live_val_at", "compute_live_features",
    "_load_dotenv", "psycopg2", "xgb.", "HISTORY_INTERVAL ", "ROOT.parent",
)


REPLACEMENTS: list[tuple[str, str, str]] = [
    ("docstring",        DOC_OLD,          DOC_NEW),
    ("imports",          IMPORTS_OLD,      IMPORTS_NEW),
    ("xgboost import",   XGB_OLD,          ""),
    ("blackbox comment", BBX_COMMENT_OLD,  BBX_COMMENT_NEW),
    ("blackbox dir",     BBX_DIR_OLD,      BBX_DIR_NEW),
    ("blackbox db path", BBX_DB_OLD,       BBX_DB_NEW),
    ("tunnel map path",  MAP_OLD,          MAP_NEW),
    ("node hook",        HOOK_OLD,         HOOK_NEW),
    ("db access",        DB_OLD,           DB_NEW),
    ("velocity thresh",  VEL_OLD,          VEL_NEW),
    ("ttf values",       TTF_VALS_OLD,     TTF_VALS_NEW),
    ("ticker interval",  TICKER_OLD,       TICKER_NEW),
    ("ticker avail",     AVAIL_OLD,        AVAIL_NEW),
    ("main()",           MAIN_OLD,         MAIN_NEW),
]


def transform(src: str) -> str:
    out = src

    for label, old, new in REPLACEMENTS:
        n = out.count(old)
        if n != 1:
            raise SystemExit(f"FAIL [{label}]: expected exactly 1 match, found {n}")
        out = out.replace(old, new)
        print(f"  ok  {label}")

    # Slice 1: ROOT…state_lock  →  the state block
    start = out.index("ROOT = Path(__file__).resolve().parent")
    end_anchor = "state_lock = threading.RLock()"
    end = out.index(end_anchor) + len(end_anchor)
    out = out[:start] + STATE_NEW + out[end:]
    print("  ok  state block")

    # Slice 2: the MODEL 2 block → delegates
    start = out.index("def load_model():")
    end = out.index("def update_status_logic():")
    out = out[:start] + MODEL_NEW + out[end:]
    print("  ok  model delegates")

    # Slice 3: the serial reader → ingest_line + reader + replay reader
    start = out.index("def hardware_reader_thread():")
    end = out.index("def ticker_thread():")
    out = out[:start] + READER_NEW + out[end:]
    print("  ok  ingestion split")

    # Slice 4: /api/ai → delegate to engine.ttf.model_status()
    start = out.index("def get_ai():")
    end = out.index('@app.route("/api/site"')
    out = out[:start] + GET_AI_NEW + out[end:]
    print("  ok  /api/ai delegate")

    # Guard: nothing that moved may still be referenced, and every delegate must
    # exist. This is the check that catches a half-applied port.
    # Word-boundary matched, so the kept shim `model_ttf_current` is NOT flagged
    # by the rule for the removed global `model_ttf`.
    leftovers = [tok for tok in FORBIDDEN_AFTER_PORT
                 if re.search(r"\b" + re.escape(tok).replace(r"\.", r"\.") + r"\b", out)
                 or (tok.endswith(".") and tok in out)]
    if leftovers:
        raise SystemExit(f"FAIL [leftover references to moved code]: {leftovers}")
    for needed in ("ttf_model.load_ttf_model", "ttf_model.model_fresh", "ttf_model.run_inference",
                   "ttf_model.model_status", "ttf_model.set_node_provider", "ttf_model.MODEL_TTF",
                   "ttf_model.MODEL_INFO", "db.try_get_conn", "db.insert_telemetry",
                   "db.insert_rfid", "validate_config", "read_session_csv"):
        if needed not in out:
            raise SystemExit(f"FAIL [expected delegate missing]: {needed}")
    print("  ok  guard: no moved symbol is still referenced")

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--source", default="/home/user/neuralstrata-backend-app-v44/app.py")
    ap.add_argument("--out", default="app.py")
    ap.add_argument("--check", action="store_true",
                    help="verify the output matches what is already on disk")
    args = ap.parse_args()

    src_path = Path(args.source)
    src = src_path.read_text()
    print(f"port: {src_path} ({len(src.splitlines())} lines)")

    ported = transform(src)

    if args.check:
        current = Path(args.out).read_text()
        if current == ported:
            print(f"CHECK OK — {args.out} is exactly what this script produces")
            return 0
        print(f"CHECK FAILED — {args.out} differs from the scripted output")
        return 1

    Path(args.out).write_text(ported)
    print(f"wrote {args.out} ({len(ported.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
