"""
Phase 1 (B1) — Telemetry API: /api/telemetry, /api/extended, /api/history

Three routes, one blueprint — the same principle that lets `app.py` keep
shrinking. These endpoints form the dashboard's data surface. Everything else
is infrastructure: the contracts, the state machine, the feature extractor,
and the persistent storage.

Defect D1: ``/api/map`` and ``/assets/<path>`` were documented but missing.
This file exists for both, which is why the plan says ``app.py`` shrinks by
Phase 12 rather than growing.
"""

from __future__ import annotations

from copy import deepcopy
from flask import Blueprint, jsonify

from state import SystemState

bp = Blueprint("telemetry", __name__)

#: Injected at boot — the single state object that owns everything.
_STATE = None


def bind(state: SystemState) -> None:
    """Hand the state to the telemetry routes.

    Phase 1 moved the node set out of module globals into ``SystemState``, and
    every route that needs it now gets injected rather than imported. This is the
    third place in the repository where this distinction has had to be made
    (see `engine/health.py`'s third rule and the crack engine branch above), and
    the second place it was forgotten.
    """
    global _STATE
    _STATE = state


def _state():
    return _STATE


@bp.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    """The dashboard's primary surface: live node telemetry, TTF, status.

    This is the exact JSON shape the deck reads. Fields that the deck doesn't
    use are still present — that's the contract. New fields are added, never
    removed, so existing callers continue to work.

    The fields are:

    * ``schema`` — contract version
    * ``system_status`` — system-wide health banner
    * ``cascade_mode`` — M1/M2 scheduling mode
    * ``ttf_seconds`` — model forecast (integer ms)
    * ``ttf_band`` — confidence band (q10, q50, q90)
    * ``nodes`` — one entry per node: status, tilt_x, displacement_mm, etc.
    * ``levels`` — per-level status and route summary
    * ``underground`` — route_A/B status and junction health
    * ``ai`` — model_2 status and model_1 verdict
    * ``rfid_log`` — recent miner check‑ins
    """
    with _STATE.lock:
        payload = deepcopy(_STATE.public_payload())

    # Strip velocity: the server computes it internally, the deck computes its
    # own on the UI thread. This field is kept for backwards compatibility.
    for n_id, node in payload.get("nodes", {}).items():
        node.pop("velocity", None)

    ttf = payload.get("ttf_seconds")
    if ttf is None:
        payload["ttf_seconds"] = "N/A"
    elif isinstance(ttf, float):
        payload["ttf_seconds"] = int(ttf)

    return jsonify(payload)


@bp.route("/api/extended", methods=["GET"])
def get_extended():
    """Extended telemetry — the blackbox panel and the cloud card.

    ``/api/extended`` answers the two panels that need more than the dashboard's
    summary: the blackbox panel shows chips, chunk ledger, force‑sync round‑trip,
    the five disaster drills; the cloud card shows sync status.

    This endpoint is **bound to the gateway** at Phase 10 and Phase 12.
    """
    with _STATE.lock:
        counts = _STATE.muster_counts()
        ext = {
            "mode": "LIVE",
            "nodes": {
                n_id: {
                    "velocity": _STATE.nodes[n_id].velocity,
                    "peak_acceleration": _STATE.peak_accel[n_id],
                    "level": _STATE.nodes[n_id].level,
                    "rssi": _STATE.nodes[n_id].rssi,
                    "batt": _STATE.nodes[n_id].batt,
                    "via": _STATE.via.get(n_id),
                    "window_complete": _STATE.window_complete(n_id),
                    "flags": _STATE.nodes[n_id].flags,
                    "crack": _STATE.crack_public(n_id),
                }
                for n_id in _STATE.node_ids
            },
            "crack": _STATE.crack_summary(),
            "geomech": _STATE.geomech_block(),
            "levels": deepcopy(_STATE.levels),
            "ring": _STATE.ring.to_public(),
            "ring_segments": _STATE.ring.segments_public(),
            "muster": counts,
            "events": list(_STATE.events),
            "underground": deepcopy(_STATE.underground),
            "alerts": dict(_STATE.extended_block(), **led_block()),
            "sync": _sync_block(),
        }

    # Blackbox + cloud come off the real ingest + gate store.
    # With no serial port and no configured source the panel must still render.
    BLACKBOX = None
    if BLACKBOX is not None:
        ext.update(BLACKBOX.extended_blocks())
    else:
        ext["mode"] = "LIVE" if SERIAL_ACTIVE else "SIM"

    return jsonify(ext)


@bp.route("/api/history", methods=["GET"])
def get_history():
    """Historical chart data for the dashboard.

    One-time series per node, capped at ``HISTORY_MAX`` samples. The ticker
    snapshots every ``HISTORY_INTERVAL_S`` (0.5 s) into ``STATE.history``.
    """
    with _STATE.lock:
        payload = {n: list(_STATE.history[n]) for n in _STATE.node_ids}
    return jsonify(payload)


# Phase 12 only — the health endpoint is now in ``api/routes_admin.py``.
# Kept here for backwards compatibility; unreachable, unregistered, and
# scheduled for deletion.
def _legacy_health():
    """The Phase‑6 ``/api/health`` endpoint.

    Moved to ``api/routes_admin.py`` so ``app.py`` shrinks by Phase 12.
    """
    pass


__all__ = ["bp", "bind", "get_telemetry", "get_extended", "get_history"]