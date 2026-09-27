"""
Phase 12 — Admin API: `/api/sim/*`, `/api/metrics`, `/api/health`
===========================================================

The admin endpoints power the **recovery plane** and **observability**:
- ``/api/sim/*`` — scenario injection, blackbox recovery
- ``/api/metrics`` — live metrics, health checks
- ``/api/health`` — comprehensive health dashboard

All return the same JSON contract, but each serves a different audience:

dashboard chip: ``/api/health`` → node‑level snapshot + external services
debug panel: ``/api/metrics`` → raw metrics + diagnostic traces
operator API: ``/api/sim/*`` → *actions* on the system

Each route is **injected** (no globals), **side‑effect free**, and answers
``gateway: false`` with a state shape rather than a 503, so a bench without
a hub can still manage a full demo.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from backend.state import SystemState
from api import map_payload

bp = Blueprint("admin", __name__)

#: The live system state, injected once at bootstrap.
_STATE = None


def bind(state: SystemState) -> None:
    """Inject the system state — the only thing this API needs.

    Phase 1 moved the global node set into ``SystemState``, and this module
    now joins the rule that all route state is injected rather than imported.
    """
    global _STATE
    _STATE = state


def _state():
    return _STATE


# --------------------------------------------------------------------------- sim *
@bp.route("/api/sim/trigger", methods=["POST"])
def sim_trigger():
    """Arm a scenario profile on one node.

    Accepts both shapes for backward compatibility:
    ```json
    {"node": "node_2", "profile": "machinery_pass"}
    ```
    ```json
    {"node": "node_2", "severity": "warning"|"critical"}
    ```

    The runbook's existing curl commands must keep working verbatim.
    """
    if _STATE is None:
        return jsonify({"ok": False, "error": "state not bound"}), 503

    body = request.get_json(silent=True) or {}
    node = body.get("node")
    profile = body.get("profile")
    severity = body.get("severity")

    if node not in _STATE.node_ids:
        return jsonify({"ok": False, "error": f"unknown node {node!r}"}), 400

    from ingestion import simulator
    with _STATE.lock:
        scenario = simulator.arm(_STATE, node, profile, severity)
        if scenario is None:
            return jsonify({"ok": False,
                            "error": f"unknown profile {profile or severity!r}",
                            "profiles": sorted(simulator.SCENARIOS)}), 400
        level = "CRITICAL" if scenario.severity == "critical" else "WARNING"
        # Operator event log
        from engine import severity as severity_engine
        push_event(level, f"{node.upper()} {scenario.name} injection armed by operator.")

    return jsonify({"ok": True,
                    "profile": scenario.name,
                    "verdict": scenario.verdict,
                    "duration_s": scenario.duration_s})


@bp.route("/api/reset", methods=["POST"])
def sim_reset():
    """Clear the system and close every open alert.

    Phase 8 (§8.5) — a re‑arm CLOSES every open alert; it never deletes one.
    The rows stay in the database with ``state='CLOSED'`` and the reason attached.

    The operation is atomic on the alert store, but the response time is
    measured from the first row deletion to the last, and alerts that take
    time to deliver are still reported as closed.
    """
    if _STATE is None:
        return jsonify({"ok": False, "error": "state not bound"}), 503

    with _STATE.lock:
        _STATE.reset("operator re‑arm")

    from persistence import db
    from alerts import dispatcher as alerts_dispatcher

    # Database first, then in‑memory
    closed_db = db.close_open_alerts("manual re‑arm")
    closed_mem = alerts_dispatcher.close_all(_STATE, reason="manual re‑arm",
                                             persist=db.persist_alert)

    from engine import severity as severity_engine
    push_event("NOMINAL", f"re‑arm closed {closed_mem} in‑memory, {closed_db} database row(s)")

    return jsonify({"ok": True,
                    "warming_up_s": 60,
                    "alerts_closed": closed_mem,
                    "alerts_closed_db": closed_db,
                    "note": "closed, never deleted — see /api/alerts?state=CLOSED"})


@bp.route("/api/cloud/force_sync", methods=["POST"])
def sim_cloud_force_sync():
    """Force an immediate cloud sync — Phase 12 disaster rehearsal.

    Used by the ``failure_suite.py`` to test sync under stress, and by the
    blackbox panel's ``FORCE SYNC`` button. Returns ``409`` when there is no
    gateway, because ``409`` says "try again later" and this says "there is
    nothing to ask", which is true on a bench with no hub attached.

    The response shape mirrors the original ``/api/cloud/force_sync`` from
    the monolithic ``app.py``, but the protocol lives in this admin module
    now that the server is split.
    """
    from backend.blackbox import gateway as gateway_mod

    gateway = gateway_mod.GATEWAY if hasattr(gateway_mod, "GATEWAY") else None
    if gateway is None:
        return jsonify({"ok": False, "error": "no blackbox gateway — there is no hub to ask"}), 409

    from backend.blackbox.gateway import Gateway
    # Start the gateway if not running
    if gateway is None:
        gateway = Gateway(sim_skip=None).start()

    res = gateway.force_sync()
    ok = res["batches_failed"] == 0
    return jsonify({"ok": ok, **res}), (200 if ok else 502)


# --------------------------------------------------------------------------- metrics
@bp.route("/api/metrics", methods=["GET"])
def get_metrics():
    """Live metrics for the dashboard and debugging.

    Returns:
    * ``nodes`` — per‑node counts (samples, pending, in_db)
    * ``chunks`` — per‑chunk gate decision ledger
    * ``alerts`` — count of open alerts by tier
    * ``sync`` — outbox depth and last success time
    """
    if _STATE is None:
        return jsonify({"ok": False, "error": "state not bound"}), 503

    with _STATE.lock:
        nodes_payload = {}
        for n_id in _STATE.node_ids:
            nodes_payload[n_id] = {
                "samples": len(getattr(_STATE, "history", {}).get(n_id, [])),
                "pending": _STATE.nodes[n_id].pending if hasattr(_STATE.nodes[n_id], "pending") else 0,
                "in_db": _STATE.nodes[n_id].in_db if hasattr(_STATE.nodes[n_id], "in_db") else 0,
            }

        # Alert counts
        from alerts import dispatcher as alerts_dispatcher
        alert_counts = {}
        for tier in ("WATCH", "ELEVATED", "HIGH", "CRITICAL"):
            alert_counts[tier] = len(alerts_dispatcher.MACHINE.open_alerts)

        # Sync metrics
        from persistence import sync as sync_mod
        from persistence import db

        sync_stats = sync_mod.status()
        with db.borrowed() as conn:
            if conn is not None:
                sync_stats.update(sync_mod.pending_counts(conn))

        response = {
            "nodes": nodes_payload,
            "alerts": alert_counts,
            "sync": sync_stats,
            "timestamp": _iso_now(),
        }

        # Add chunk ledger if gateway exists
        from backend.blackbox import gateway as gateway_mod
        gateway = getattr(gateway_mod, "GATEWAY", None)
        if gateway is not None:
            chunks = gateway.store.chunks() if hasattr(gateway, "store") else []
            response["chunks"] = chunks

    return jsonify({"ok": True, **response})


# --------------------------------------------------------------------------- health
@bp.route("/api/health", methods=["GET"])
def get_health():
    """Comprehensive health dashboard — nodes + external services.

    The health endpoint is **bound to the gateway** at Phase 10 and Phase 12.
    It answers the dashboard's health chip with a flat structure:

    * ``schema`` — contract version
    * ``system_status`` — system‑wide health banner
    * ``cascade_mode`` — M1/M2 scheduling mode
    * ``ttf_seconds`` — model forecast (integer ms)
    * ``ttf_band`` — confidence band (q10, q50, q90)
    * ``nodes`` — one entry per node: status, tilt_x, displacement_mm, etc.
    * ``levels`` — per‑level status and route summary
    * ``underground`` — route_A/B status and junction health
    * ``ai`` — model_2 status and model_1 verdict
    * ``rfid_log`` — recent miner check‑ins

    Each external service (DB, cloud, blackbox) answers ``gateway: false``
    when not configured rather than raising 503, so a bench without hardware
    can still serve a full demo.
    """
    if _STATE is None:
        return jsonify({"ok": False, "error": "state not bound"}), 503

    # Model health from engine.ttf
    from engine import ttf as ttf_model
    model_health = {
        "available": bool(ttf_model.MODEL_INFO.get("available")),
        "loaded": ttf_model.MODEL_INFO.get("model") is not None,
        "scaler": "present" if ttf_model.MODEL_INFO.get("scaler") else "absent",
        "name": ttf_model.MODEL_INFO.get("meta", {}).get("model"),
        "algorithm": ttf_model.MODEL_INFO.get("meta", {}).get("algorithm"),
        "trained_at": ttf_model.MODEL_INFO.get("meta", {}).get("trained_at"),
    }

    # Database health
    from persistence import db
    conn = db.try_get_conn()
    db_health = {"status": "DOWN", "error": "no connection"}
    if conn is not None:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            db_health = {"status": "UP"}
        except Exception as e:
            db_health = {"status": "DOWN", "error": str(e)[:200]}
        finally:
            db.release(conn)

    # Blackbox gateway health
    from backend.blackbox import gateway as gateway_mod
    gateway_health = {"gateway": False, "mode": "OFF"}
    gateway = getattr(gateway_mod, "GATEWAY", None)
    if gateway is not None:
        gateway_health = {
            "gateway": True,
            "mode": gateway.mode if hasattr(gateway, "mode") else "UNKNOWN",
        }

    # Compose health payload
    response = {
        "schema": "health-v1",
        "system_status": _STATE.system_status if hasattr(_STATE, "system_status") else "NOMINAL",
        "cascade_mode": _STATE.cascade_mode if hasattr(_STATE, "cascade_mode") else "M2",
        "ttf_seconds": _STATE.ttf_seconds if hasattr(_STATE, "ttf_seconds") else "N/A",
        "ttf_band": _STATE.ttf_band if hasattr(_STATE, "ttf_band") else [],
        "nodes": _STATE.nodes_public() if hasattr(_STATE, "nodes_public") else {},
        "levels": getattr(_STATE, "levels", {}),
        "underground": getattr(_STATE, "underground", {}),
        "ai": _ai_health_block(),
        "rfid_log": [],  # Would come from RFID service
        "services": {
            "database": db_health,
            "blackbox": gateway_health,
        },
    }

    return jsonify({"ok": True, **response})


def _ai_health_block():
    """Compose the AI health block for /api/health."""
    from engine import ttf as ttf_model
    from engine import pattern as pattern_engine

    info = ttf_model.MODEL_INFO
    meta = info.get("meta") or {}

    def scalar(v):
        return v if isinstance(v, (str, int, float, bool)) or v is None else None

    out = {
        "available": bool(info.get("available")),
        "loaded": info.get("model") is not None,
        "scaler": "present" if info.get("scaler") else "absent",
        "name": scalar(meta.get("model")),
        "algorithm": scalar(meta.get("algorithm")),
        "trained_at": scalar(meta.get("trained_at")),
    }

    fed = [n.node_id for n in _STATE.iter_nodes() if hasattr(_STATE, "window_complete") and _STATE.window_complete(n.node_id)]
    out["fed_nodes"] = fed
    out["fed_fresh"] = bool(fed)

    return out


def _iso_now() -> str:
    """ISO‑8601 UTC timestamp for generated fields."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def push_event(level, text, kind=None):
    """Push an operator event to the log.

    The event log is the single source of truth for the ``/api/extended``
    block that the dashboard reads at 2 Hz.
    """
    if _STATE is not None:
        _STATE.note_event(level, text, kind=kind)


__all__ = ["bp", "bind", "get_metrics", "get_health", "sim_trigger", "sim_reset",
           "sim_cloud_force_sync", "push_event"]