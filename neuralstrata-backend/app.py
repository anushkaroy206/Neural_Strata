"""
Neural Strata — hardware edge server (v4.4: AI TTF model)
==========================================================

Implements the dashboard API contract and serves the frontend from the
sibling `neuralstrata-frontend/` folder.

    pip install -r requirements.txt
    python3 app.py                          # http://localhost:5000  (dashboard + API)
    python3 app.py --no-serial              # API + UI without USB
    python3 app.py --port 5000

The dashboard ticker flips SIM → LIVE once GET /api/telemetry succeeds.

If you open index.html from another origin (e.g. `python3 -m http.server 8080`),
load it as  index.html?api=http://127.0.0.1:5000  — this server already sends CORS.

PHASE 4 (v4.4) — TTF model integration
---------------------------------------
* Loads models/ttf_model_v1.json (+ scaler.json, model_meta.json) at startup.
  Missing/broken model => the server still boots and uses the kinematic
  (v/a) fallback exactly as before. Demo-proofing: the API never breaks.
* The ticker keeps a 60 s rolling buffer per node (sampled every 0.5 s, the
  same cadence and values the model was trained on) and, at 1 Hz, overwrites
  telemetry_state["ttf_seconds"] with the model's forecast. The dashboard
  pill and AI view read that same channel — zero frontend changes.
* Guards: WARMING UP until the 60 s buffer is full (kinematic drives the
  pill meanwhile); if the model is stale (>5 s) or contradicts the physics
  (claims imminent failure with no motion), the kinematic value is used.
* /api/ai reports the real model name, status (ACTIVE / WARMING UP /
  FALLBACK), confidence (from the held-out MAE) and trained_at timestamp.
"""

import argparse
import json
import math
import os
import threading
import time
from collections import deque
from copy import deepcopy
from pathlib import Path

import numpy as np
import psycopg2
import serial
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

try:
    import xgboost as xgb
except ImportError:          # Phase 4 is optional — the server must boot without it
    xgb = None

app = Flask(__name__)
CORS(app)

ROOT = Path(__file__).resolve().parent
FRONTEND_DIR = ROOT.parent / "neuralstrata-frontend"


def _load_dotenv():
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


_load_dotenv()

# --- CONFIGURATION ---
DB_HOST = os.environ.get("NS_DB_HOST", "localhost")
DB_NAME = os.environ.get("NS_DB_NAME", "neural_strata")
DB_USER = os.environ.get("NS_DB_USER", "postgres")
DB_PASS = os.environ.get("NS_DB_PASS", "your_password_here")
SERIAL_PORT = os.environ.get("NS_SERIAL_PORT", "/dev/cu.usbserial-0001")
BAUD_RATE = int(os.environ.get("NS_BAUD_RATE", "115200"))

ACC_CRITICAL = 3.0
ACC_WARNING = 0.25
HISTORY_MAX = 240
HISTORY_INTERVAL = 0.5

# --- PHASE 4: TTF MODEL (optional — kinematic fallback when absent) ---
# Feature order MUST match neuralstrata-ai/notebooks/02_training.py
AI_FEATURES = [
    "disp_now", "d10", "d30", "d60",
    "v10", "v30", "v60", "disp_max60",
    "accel_now", "accel_mean60", "accel_std60", "accel_max60", "jerk",
    "tilt_now", "tilt_d30", "ttf_fuk",
]
AI_FUK_V_MIN = 1.0          # mm/s — Fukuzono fit uses only v10 > this (as in training)
MODEL_FRESH_S = 5.0         # model predictions older than this => treat as stale
MODEL_BUF = 121             # 60 s of 2 Hz samples (+1)

model_info = {"available": False, "model": None, "scaler": None, "meta": {}}
model_ttf = {"node_1": None, "node_2": None}
model_last_ok = 0.0
node_buffers = {
    "node_1": deque(maxlen=MODEL_BUF),
    "node_2": deque(maxlen=MODEL_BUF),
}

state_lock = threading.RLock()

# --- GLOBAL STATE (Matches Dashboard Contract) ---
telemetry_state = {
    "system_status": "NOMINAL",
    "ttf_seconds": "N/A",
    "timestamp": "",
    "nodes": {
        "node_1": {"status": "NOMINAL", "tilt_x": 0.0, "displacement_mm": 5.0, "acceleration": 0.0, "velocity": 0.0},
        "node_2": {"status": "NOMINAL", "tilt_x": 0.0, "displacement_mm": 5.0, "acceleration": 0.0, "velocity": 0.0},
    },
    "underground": {
        "route_A_status": "OPEN",
        "route_B_status": "OPEN",
    },
    "rfid_log": [],
}

events_log = [{"time": time.strftime("%H:%M:%S"), "level": "NOMINAL", "text": "System initialised — awaiting hardware telemetry."}]
peak_accel = {"node_1": 0.0, "node_2": 0.0}
system_armed = True
history = {
    "node_1": deque(maxlen=HISTORY_MAX),
    "node_2": deque(maxlen=HISTORY_MAX),
}
# Scenario console overlay. Hardware updates skip a node while it is injected.
injections = {"node_1": None, "node_2": None}
injection_t = {"node_1": 0.0, "node_2": 0.0}

MINERS = {
    "001": "Mohneesh",
    "002": "Anuran",
    "003": "Swayam",
    "004": "Arti",
}


def get_db_connection():
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
        print(f"DB log error: {e}")


def push_event(level, text):
    events_log.insert(0, {"time": time.strftime("%H:%M:%S"), "level": level, "text": text})
    if len(events_log) > 64:
        events_log.pop()


def snapshot_history():
    for n_id in ("node_1", "node_2"):
        node = telemetry_state["nodes"][n_id]
        history[n_id].append({
            "status": node["status"],
            "tilt_x": float(node["tilt_x"]),
            "displacement_mm": float(node["displacement_mm"]),
            "acceleration": float(node["acceleration"]),
        })


def calculate_derivatives(node_id, current_disp):
    """Calculates velocity (dx/dt) and acceleration (dv/dt) from displacement."""
    node = telemetry_state["nodes"][node_id]
    prev_disp = node["displacement_mm"]
    dt = 0.5  # 500ms loop

    v = (current_disp - prev_disp) / dt
    a = (v - node["velocity"]) / dt

    node["velocity"] = (node["velocity"] * 0.3) + (v * 0.7)
    node["acceleration"] = (node["acceleration"] * 0.3) + (a * 0.7)
    node["displacement_mm"] = current_disp

    peak_accel[node_id] = max(peak_accel[node_id], abs(node["acceleration"]))

    return node["acceleration"], node["velocity"]


# ---------------------------------------------------------------------------
# PHASE 4 — TTF model loading, live feature extraction, inference
# ---------------------------------------------------------------------------

def load_model():
    """Load the Phase-4 TTF model at startup. Missing/broken => kinematic fallback."""
    if xgb is None:
        print("[ai] xgboost not installed — kinematic fallback only")
        return
    model_path = ROOT / "models" / "ttf_model_v1.json"
    scaler_path = ROOT / "models" / "scaler.json"
    meta_path = ROOT / "models" / "model_meta.json"
    if not (model_path.exists() and scaler_path.exists()):
        print(f"[ai] model files not found ({model_path.parent.name}/) — kinematic fallback only")
        return
    try:
        m = xgb.XGBRegressor()
        m.load_model(str(model_path))
        sc = json.loads(scaler_path.read_text())
        if list(sc.get("features", [])) != AI_FEATURES:
            raise ValueError("scaler feature order does not match AI_FEATURES")
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        model_info.update(available=True, model=m, scaler=sc, meta=meta)
        print(f"[ai] TTF model loaded: {meta.get('model', 'ttf_model_v1')} "
              f"(trained {meta.get('trained_at', '?')})")
    except Exception as e:
        model_info["available"] = False
        print(f"[ai] failed to load model ({e}) — kinematic fallback only")


def model_ttf_current():
    """True when the model has a recent, valid prediction to trust."""
    if not model_info["available"]:
        return False
    if (time.time() - model_last_ok) > MODEL_FRESH_S:
        return False
    return any(v is not None for v in model_ttf.values())


def _live_val_at(ts, arr, i, off_s):
    """Value of arr at (t_i - off_s) using the nearest sample (same rule as training)."""
    target = ts[i] - off_s
    j = int(np.searchsorted(ts, target, side="right")) - 1
    if j < 0:
        return float("nan")
    tol = max(1.0, off_s * 0.05)
    if (ts[i] - ts[j]) > off_s + tol:
        return float("nan")
    return float(arr[j])


def compute_live_features(buf):
    """Build the 16 training features from a node's 60 s buffer.

    Mirrors 01_data_extraction.py exactly (same offsets, same Fukuzono rule).
    Returns a list in AI_FEATURES order, or None if the window is incomplete.
    """
    if len(buf) < MODEL_BUF:
        return None
    ts = np.array([s[0] for s in buf], dtype=float)
    disp = np.array([s[1] for s in buf], dtype=float)
    tilt = np.array([s[2] for s in buf], dtype=float)
    acc = np.array([s[3] for s in buf], dtype=float)
    i = len(ts) - 1

    d10 = disp[i] - _live_val_at(ts, disp, i, 10.0)
    d30 = disp[i] - _live_val_at(ts, disp, i, 30.0)
    d60 = disp[i] - _live_val_at(ts, disp, i, 60.0)
    if math.isnan(d60):
        return None

    lo = int(np.searchsorted(ts, ts[i] - 60.0, side="left"))
    win_disp, win_acc = disp[lo:i + 1], acc[lo:i + 1]

    # Fukuzono 1/v fit over the last 30 s (same as training: v10 > 1 mm/s only)
    lo30 = int(np.searchsorted(ts, ts[i] - 30.0, side="left"))
    xs, ys = [], []
    for j in range(lo30, i + 1):
        if j < 21:                      # need 10 s of history for a v10
            continue
        k = int(np.searchsorted(ts, ts[j] - 10.0, side="right")) - 1
        if k < 0 or (ts[j] - ts[k]) > 10.5:
            continue
        v = (disp[j] - disp[k]) / 10.0
        if v > AI_FUK_V_MIN:
            xs.append(ts[j] - ts[i])
            ys.append(1.0 / v)
    ttf_fuk = float("nan")
    if len(xs) >= 8:
        A = np.column_stack([np.array(xs), np.ones(len(xs))])
        (a_fit, b_fit), *_ = np.linalg.lstsq(A, np.array(ys), rcond=None)
        if a_fit < -1e-12:
            t0 = -b_fit / a_fit
            if 0.0 <= t0 <= 3600.0:
                ttf_fuk = float(t0)

    return [
        float(disp[i]), float(d10), float(d30), float(d60),
        d10 / 10.0, d30 / 30.0, d60 / 60.0, float(win_disp.max()),
        float(acc[i]), float(win_acc.mean()), float(win_acc.std()), float(win_acc.max()),
        float(acc[i]) - _live_val_at(ts, acc, i, 1.0),
        float(tilt[i]), float(tilt[i]) - _live_val_at(ts, tilt, i, 30.0),
        ttf_fuk,
    ]


def run_model_inference():
    """1 Hz: predict per-node TTF from the rolling buffers (ticker thread)."""
    global model_last_ok
    if not model_info["available"]:
        return
    sc = model_info["scaler"]
    mean = np.asarray(sc["mean"], dtype=float)
    scale = np.asarray(sc["scale"], dtype=float)
    got_any = False
    for n_id in ("node_1", "node_2"):
        feats = compute_live_features(node_buffers[n_id])
        if feats is None:
            continue
        try:
            x = ((np.asarray(feats, dtype=float) - mean) / scale).reshape(1, -1)
            ttf = float(model_info["model"].predict(x)[0])
        except Exception as e:
            print(f"[ai] inference error ({n_id}): {e}")
            continue
        if math.isnan(ttf):
            continue
        ttf = max(0.0, min(3600.0, ttf))
        # Physics sanity: "imminent failure" with no motion is not credible.
        if ttf < 30.0 and feats[4] < 0.5 and feats[0] < 30.0:
            continue
        model_ttf[n_id] = ttf
        got_any = True
    if got_any:
        model_last_ok = time.time()


# ---------------------------------------------------------------------------

def update_status_logic():
    global telemetry_state
    if not system_armed:
        return

    worst_status = "NOMINAL"
    ttf_lowest = 999999

    for n_id in ["node_1", "node_2"]:
        node = telemetry_state["nodes"][n_id]
        a = node["acceleration"]
        v = node["velocity"]

        if a >= ACC_CRITICAL:
            node["status"] = "CRITICAL"
        elif a > ACC_WARNING:
            node["status"] = "WARNING"
        elif node["status"] != "CRITICAL":
            node["status"] = "NOMINAL"

        if node["status"] == "CRITICAL":
            worst_status = "CRITICAL"
        elif node["status"] == "WARNING" and worst_status != "CRITICAL":
            worst_status = "WARNING"

        if a > ACC_WARNING and v > 0.03:
            ttf = v / a
            if ttf < ttf_lowest:
                ttf_lowest = ttf

    telemetry_state["system_status"] = worst_status
    kinematic_ttf = int(ttf_lowest) if ttf_lowest != 999999 else "N/A"
    # Phase 4: the model forecast drives the pill when it has a fresh prediction
    if model_ttf_current():
        vals = [v for v in model_ttf.values() if v is not None]
        telemetry_state["ttf_seconds"] = int(min(vals)) if vals else kinematic_ttf
    else:
        telemetry_state["ttf_seconds"] = kinematic_ttf

    prev_rA = telemetry_state["underground"]["route_A_status"]
    prev_rB = telemetry_state["underground"]["route_B_status"]

    telemetry_state["underground"]["route_A_status"] = (
        "BLOCKED" if telemetry_state["nodes"]["node_1"]["status"] in ["WARNING", "CRITICAL"] else "OPEN"
    )
    telemetry_state["underground"]["route_B_status"] = (
        "BLOCKED" if telemetry_state["nodes"]["node_2"]["status"] in ["WARNING", "CRITICAL"] else "OPEN"
    )

    if prev_rA == "OPEN" and telemetry_state["underground"]["route_A_status"] == "BLOCKED":
        push_event(worst_status, "ROUTE A BLOCKED — Level 1 drift compromised.")
    if prev_rB == "OPEN" and telemetry_state["underground"]["route_B_status"] == "BLOCKED":
        push_event(worst_status, "ROUTE B BLOCKED — Level 2 drift compromised.")


def apply_injections(dt):
    """Drive a node from the dashboard scenario console until reset / warning expiry."""
    for n_id in ("node_1", "node_2"):
        sev = injections[n_id]
        if not sev:
            continue
        injection_t[n_id] += dt
        t = injection_t[n_id]
        node = telemetry_state["nodes"][n_id]
        if sev == "warning":
            taper = 1.0 if t < 34 else max(0.0, 1 - (t - 34) / 8)
            a = 0.95 * (1 - math.exp(-t / 1.6)) * math.exp(-t / 10) * taper
            if t > 46:
                injections[n_id] = None
                injection_t[n_id] = 0.0
                node["velocity"] *= 0.3
                node["acceleration"] = 0.0
                node["status"] = "NOMINAL"
                push_event("NOMINAL", f"{n_id.upper()} creep settled — node back to NOMINAL.")
                continue
        else:
            a = 3.6 * (1 - math.exp(-t / 1.4)) * (1 + 0.12 * math.sin(t * 2.1))

        node["acceleration"] = a
        node["velocity"] = max(-0.4, min(12.0, node["velocity"] + a * dt))
        node["displacement_mm"] = max(0.0, min(100.0, node["displacement_mm"] + node["velocity"] * dt))
        node["tilt_x"] = node["tilt_x"] * 0.92 + a * 0.9
        peak_accel[n_id] = max(peak_accel[n_id], abs(a))


def hardware_reader_thread():
    """Reads USB Serial JSON from ESP32 Hub, updates state, and logs to PostgreSQL."""
    global telemetry_state

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=1)
        print(f"Connected to ESP32 Hub on {SERIAL_PORT}")
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
                data = json.loads(line)
                rfid_row = None
                with state_lock:
                    telemetry_state["timestamp"] = time.strftime("%H:%M:%S")

                    if "uid" in data and data["uid"]:
                        uid = str(data["uid"])
                        miner_name = MINERS.get(uid, "Personnel Tag")
                        zone = "Tunnel A" if telemetry_state["underground"]["route_A_status"] == "OPEN" else "Safe Exit"
                        rfid_row = (uid, zone)

                        log_entry = {"id": uid, "name": miner_name, "zone": zone, "time": time.strftime("%H:%M:%S")}
                        telemetry_state["rfid_log"].insert(0, log_entry)
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
                    conn = get_db_connection()
                    if conn:
                        cur = conn.cursor()
                        cur.execute(
                            "INSERT INTO rfid_evacuation_log (miner_uid, current_zone) VALUES (%s, %s)",
                            rfid_row,
                        )
                        conn.commit()
                        cur.close()
                        conn.close()

        except json.JSONDecodeError:
            pass
        except Exception as e:
            print(f"Read error: {e}")

        time.sleep(0.05)


def ticker_thread():
    """Advance scenario injections, keep rolling chart history, and log state to the DB."""
    last_hist = 0.0
    last_infer = 0.0
    while True:
        t0 = time.time()
        db_rows = None
        with state_lock:
            apply_injections(0.05)
            update_status_logic()
            telemetry_state["timestamp"] = time.strftime("%H:%M:%S")
            if t0 - last_hist >= HISTORY_INTERVAL:
                snapshot_history()
                last_hist = t0
                db_rows = [
                    (n_id, float(node["tilt_x"]), float(node["displacement_mm"]), float(node["acceleration"]))
                    for n_id, node in telemetry_state["nodes"].items()
                ]
                # Phase 4: feed the model's 60 s rolling buffers (same 2 Hz cadence as training)
                for n_id in ("node_1", "node_2"):
                    node = telemetry_state["nodes"][n_id]
                    node_buffers[n_id].append(
                        (t0, float(node["displacement_mm"]), float(node["tilt_x"]), float(node["acceleration"]))
                    )
        if db_rows is not None:
            log_telemetry_rows(db_rows)
        # Phase 4: 1 Hz model inference (buffers are ticker-local — no lock needed)
        if model_info["available"] and t0 - last_infer >= 1.0:
            last_infer = t0
            run_model_inference()
        time.sleep(0.05)


def public_telemetry():
    with state_lock:
        payload = deepcopy(telemetry_state)
    payload.pop("timestamp", None)
    for n_id, node in payload.get("nodes", {}).items():
        node.pop("velocity", None)
    ttf = payload.get("ttf_seconds")
    if ttf is None:
        payload["ttf_seconds"] = "N/A"
    elif isinstance(ttf, float):
        payload["ttf_seconds"] = int(ttf)
    return payload


# --- API ENDPOINTS ---

@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    return jsonify(public_telemetry())


@app.route("/api/extended", methods=["GET"])
def get_extended():
    with state_lock:
        ug_count = len([m for m in telemetry_state["rfid_log"] if "Tunnel" in m.get("zone", "") or "Evacuating" in m.get("zone", "")])
        safe_count = len([m for m in telemetry_state["rfid_log"] if m.get("zone") == "Safe Exit"])
        ext = {
            "mode": "LIVE",
            "nodes": {
                "node_1": {
                    "velocity": telemetry_state["nodes"]["node_1"]["velocity"],
                    "peak_acceleration": peak_accel["node_1"],
                },
                "node_2": {
                    "velocity": telemetry_state["nodes"]["node_2"]["velocity"],
                    "peak_acceleration": peak_accel["node_2"],
                },
            },
            "muster": {"total": len(telemetry_state["rfid_log"]), "underground": ug_count, "safe": safe_count},
            "events": list(events_log),
        }
    return jsonify(ext)


@app.route("/api/history", methods=["GET"])
def get_history():
    with state_lock:
        payload = {n: list(history[n]) for n in ("node_1", "node_2")}
    return jsonify(payload)


@app.route("/api/ai", methods=["GET"])
def get_ai():
    with state_lock:
        n1 = telemetry_state["nodes"]["node_1"]
        n2 = telemetry_state["nodes"]["node_2"]
        t1, t2 = model_ttf["node_1"], model_ttf["node_2"]
        avail = model_info["available"]
        fresh = avail and (time.time() - model_last_ok) < MODEL_FRESH_S
        status = "ACTIVE" if (fresh and (t1 is not None or t2 is not None)) else (
            "WARMING UP" if avail else "FALLBACK")
        meta = model_info["meta"] if avail else {}
        mae = (meta.get("metrics") or {}).get("held_out_critical_mae_s")
        model_name = meta.get("model", "ttf_model_v1") if avail else "Kinematic Extrapolation Fallback"
        confidence = (f"{max(50, 100 - int(round((mae or 8.0) * 3)))}%" if avail else "85%")
        if not avail:
            note = "Using 1/v derived TTF calculations."
        elif not fresh:
            note = "Warming up: building the 60 s telemetry buffer."
        else:
            note = (f"XGBoost TTF forecast · held-out MAE {mae:.1f} s · "
                    "kinematic fallback armed." if mae is not None
                    else "XGBoost TTF forecast · kinematic fallback armed.")

        def fc(t, node):
            if fresh and t is not None:
                return t <= 300.0
            return node["status"] != "NOMINAL"

        ai_data = {
            "model": model_name,
            "status": status,
            "horizon": "1 Hour",
            "last_train": meta.get("trained_at", "Static"),
            "confidence": confidence,
            "note": note,
            "forecasts": {
                "node_1": fc(t1, n1),
                "node_2": fc(t2, n2),
            },
        }
    return jsonify(ai_data)


@app.route("/api/sim/trigger", methods=["POST"])
def trigger_scenario():
    body = request.get_json(silent=True) or {}
    node = body.get("node")
    severity = body.get("severity")
    if node not in ("node_1", "node_2") or severity not in ("warning", "critical"):
        return jsonify({"ok": False}), 400
    with state_lock:
        injections[node] = severity
        injection_t[node] = 0.0
        level = "CRITICAL" if severity == "critical" else "WARNING"
        push_event(level, f"{node.upper()} {severity} injection armed by operator.")
    return jsonify({"ok": True})


@app.route("/api/reset", methods=["POST"])
def reset_system():
    global system_armed
    with state_lock:
        system_armed = True
        for n_id in ("node_1", "node_2"):
            telemetry_state["nodes"][n_id]["status"] = "NOMINAL"
            telemetry_state["nodes"][n_id]["acceleration"] = 0.0
            telemetry_state["nodes"][n_id]["velocity"] = 0.0
            telemetry_state["nodes"][n_id]["displacement_mm"] = 5.0
            telemetry_state["nodes"][n_id]["tilt_x"] = 0.0
            peak_accel[n_id] = 0.0
            injections[n_id] = None
            injection_t[n_id] = 0.0
        update_status_logic()
        push_event("NOMINAL", "System manually re-armed by operator.")
    return jsonify({"ok": True})


@app.route("/")
def dashboard():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return jsonify({"error": "frontend not found", "expected": str(FRONTEND_DIR)}), 404
    return send_from_directory(FRONTEND_DIR, "index.html")


def main():
    parser = argparse.ArgumentParser(description="Neural Strata edge server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-serial", action="store_true", help="Skip USB serial (API + dashboard only)")
    args = parser.parse_args()

    load_model()
    threading.Thread(target=ticker_thread, daemon=True).start()
    if not args.no_serial:
        threading.Thread(target=hardware_reader_thread, daemon=True).start()
    else:
        with state_lock:
            push_event("NOMINAL", "Started without serial — waiting for hardware or scenario injection.")

    print(f"Neural Strata Edge Server  →  http://127.0.0.1:{args.port}")
    print("  GET  /api/telemetry  /api/extended  /api/history  /api/ai")
    print("  POST /api/sim/trigger  /api/reset")
    if FRONTEND_DIR.joinpath("index.html").exists():
        print(f"  Dashboard               →  http://127.0.0.1:{args.port}/")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
