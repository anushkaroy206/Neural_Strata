"""
=============================================================================
 THIS IS THE FILE YOU EDIT.
=============================================================================

Everything else in backend/ is plumbing that already works. To go live you
replace the bodies of the functions below with reads from your real source --
a LoRa gateway, Modbus poller, MQTT subscriber, or a time-series database.

Right now every function returns placeholder data so the dashboard has
something to draw the moment you press F5. Each one is marked TODO.

Rules of thumb:
  * Return the shapes in models.py. If it validates, the dashboard renders it.
  * Never raise. Return the last known good value instead -- a missing field
    makes the frontend reuse its previous sample, which reads as "stable"
    rather than "sensor dead". Use status to signal a dead sensor.
  * Don't block for long. These are called on a 500 ms poll; if your source is
    slow, cache it in a background thread and return the cache here.
"""

from datetime import datetime
from typing import Dict, List

from .models import (
    AiInfo,
    Event,
    Extended,
    HistorySample,
    Muster,
    NodeExtended,
    NodeReading,
    RfidEntry,
    Telemetry,
    Underground,
)

# The two nodes the dashboard renders. node_1 is mesh cell E5, node_2 is E8.
NODE_IDS = ("node_1", "node_2")

# --- demo state -----------------------------------------------------------
# Lets the scenario buttons actually do something before you have hardware,
# so you can watch the whole alarm path light up. Delete this and the two
# functions at the bottom once you are reading real sensors.
_INJECTED: Dict[str, str] = {}

_BASELINE = {
    "node_1": {"tilt_x": 1.1, "displacement_mm": 5.2},
    "node_2": {"tilt_x": 0.8, "displacement_mm": 4.1},
}

# What an injected fault looks like: acceleration drives the status, and
# displacement is pushed up so the charts visibly move.
_SEVERITY = {
    "warning": {"acceleration": 0.9, "extra_mm": 6.0, "extra_tilt": 0.8},
    "critical": {"acceleration": 3.6, "extra_mm": 22.0, "extra_tilt": 3.1},
}


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


def worst_status(*statuses: str) -> str:
    """Helper: CRITICAL beats WARNING beats NOMINAL."""
    order = {"NOMINAL": 0, "WARNING": 1, "CRITICAL": 2}
    return max(statuses, key=lambda s: order.get(s, 0)) if statuses else "NOMINAL"


def classify(acceleration: float) -> str:
    """
    Map a raw acceleration to a status using the same thresholds as the
    reference physics. Adjust these to match your own sensors.
    """
    if acceleration >= 3.0:
        return "CRITICAL"
    if acceleration > 0.25:
        return "WARNING"
    return "NOMINAL"


# ---------------------------------------------------------------------------
# TODO 1 -- REQUIRED. Everything else is optional.
# ---------------------------------------------------------------------------
def read_telemetry() -> Telemetry:
    """
    GET /api/telemetry, polled every 500 ms.

    This is the only endpoint the dashboard needs to leave SIM mode.
    Replace the placeholder readings with your sensor values.
    """
    nodes: Dict[str, NodeReading] = {}
    for node_id in NODE_IDS:
        base = _BASELINE[node_id]
        fault = _SEVERITY.get(_INJECTED.get(node_id, ""), None)
        accel = fault["acceleration"] if fault else 0.0
        nodes[node_id] = NodeReading(
            # Derive status from the reading rather than setting it directly,
            # so the number on screen always agrees with the colour.
            status=classify(accel),
            tilt_x=base["tilt_x"] + (fault["extra_tilt"] if fault else 0.0),
            displacement_mm=base["displacement_mm"]
            + (fault["extra_mm"] if fault else 0.0),
            acceleration=accel,
        )

    # A route closes when the node covering it is not NOMINAL. The frontend
    # treats anything that isn't the literal string BLOCKED as OPEN.
    underground = Underground(
        route_A_status="BLOCKED" if nodes["node_1"].status != "NOMINAL" else "OPEN",
        route_B_status="BLOCKED" if nodes["node_2"].status != "NOMINAL" else "OPEN",
    )

    # TODO: replace with your RFID / tag reader feed.
    rfid_log: List[RfidEntry] = [
        RfidEntry(id="C-101", name="A. Mahato", zone="Tunnel A", time=_now()),
        RfidEntry(id="C-102", name="R. Singh", zone="Tunnel B", time=_now()),
        RfidEntry(id="C-103", name="P. Das", zone="Headframe", time=_now()),
    ]

    return Telemetry(
        # Leave this derived rather than hardcoded -- the banner must never
        # read NOMINAL while a node reports CRITICAL.
        system_status=worst_status(*(n.status for n in nodes.values())),
        ttf_seconds="N/A",
        nodes=nodes,
        underground=underground,
        rfid_log=rfid_log,
    )


# ---------------------------------------------------------------------------
# TODO 2 -- optional. Without it those panels just stay at their defaults.
# ---------------------------------------------------------------------------
def read_extended() -> Extended:
    """GET /api/extended, polled every 1500 ms."""
    total = 10
    # Demo: crew evacuate when something is wrong. Replace with real counts.
    underground_count = 0 if _INJECTED else 7

    node_ext: Dict[str, NodeExtended] = {}
    for node_id in NODE_IDS:
        fault = _SEVERITY.get(_INJECTED.get(node_id, ""), None)
        accel = fault["acceleration"] if fault else 0.0
        node_ext[node_id] = NodeExtended(velocity=accel * 0.6, peak_acceleration=accel)

    events = [
        Event(
            time=_now(),
            level="crit" if severity == "critical" else "warn",
            text=f"{node_id} reporting {severity.upper()}",
        )
        for node_id, severity in _INJECTED.items()
    ] or [Event(time=_now(), level="info", text="Backend online")]

    return Extended(
        mode="LIVE",
        nodes=node_ext,
        muster=Muster(
            total=total, underground=underground_count, safe=total - underground_count
        ),
        events=events,
    )


# ---------------------------------------------------------------------------
# TODO 3 -- optional. Without it charts start empty and fill as data arrives.
# ---------------------------------------------------------------------------
def read_history() -> Dict[str, List[HistorySample]]:
    """
    GET /api/history, called once at page load.

    Return up to 240 past samples per node, oldest first. Anything longer is
    truncated to the most recent 240 by the frontend.
    """
    return {node_id: [] for node_id in NODE_IDS}


# ---------------------------------------------------------------------------
# TODO 4 -- optional. Pure metadata for the AI card.
# ---------------------------------------------------------------------------
def read_ai() -> AiInfo:
    """GET /api/ai, polled every 5 s."""
    return AiInfo(
        model="—",
        status="AWAITING_INTEGRATION",
        horizon="—",
        last_train="—",
        confidence="—",
        note="No model connected. Forecasts fall back to kinematic projection.",
    )


# ---------------------------------------------------------------------------
# TODO 5 -- test hooks. Delete both in production.
# ---------------------------------------------------------------------------
def trigger(node: str, severity: str) -> bool:
    """POST /api/sim/trigger. Return False for an unknown node/severity."""
    if node not in NODE_IDS or severity not in _SEVERITY:
        return False
    _INJECTED[node] = severity
    return True


def reset() -> None:
    """POST /api/reset. Clear any injected state."""
    _INJECTED.clear()
