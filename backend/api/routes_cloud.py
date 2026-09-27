"""
Phase 12 — Blackbox Observability API
====================================

The `/api/cloud/{status,force_sync,trace}` endpoints provide observability for
the underground recovery plane. This is Phase 12's contribution to the three-plane
architecture:

1. **Live plane** — USB serial at 2 Hz, in-memory state, alert dispatcher
2. **Durable plane** — truth store + cloud outbox on the laptop
3. **Recovery plane** — SD blackbox, delete gate, and REPLAY (this module)

These endpoints are required for:
- The `frontend/src/js/13-blackbox-cloud.js` blackbox panel
- Phase 12's drop accounting and failure rehearsal
- The `backend/tools/failure_suite.py` disaster scenario runner

Defect D1 was the reason these endpoints existed at all: `gis-map.js` has polled
`/api/map` every two seconds since before there was a server answering it, and
the frontend's own header comment claimed a `/<path:asset>` route that was not
in the code. Both are now true statements.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from api import map_payload
from backend.state import SystemState
from blackbox import gate as gate_mod

bp = Blueprint("cloud", __name__)

#: Injected at boot — a callable returning the live gateway
_GATEWAY = None


def bind(gateway_callable):
    """Inject the gateway source.

    The gateway is created (or re-created) after this line runs, so the route asks
    where it is at request time rather than remembering what it was at import.
    """
    global _GATEWAY
    _GATEWAY = gateway_callable


def _gw():
    """Return the live gateway or None if not available."""
    if _GATEWAY is None:
        return None
    return _GATEWAY() if callable(_GATEWAY) else _GATEWAY


def _absent(why: str):
    """Honest shape when there is no gateway."""
    return {
        "ok": True,
        "gateway": False,
        "mode": "OFF",
        "reason": why,
        "blackbox": None,
        "cloud": None,
        "chunks": [],
        "count": 0,
    }


@bp.route("/api/cloud/status", methods=["GET"])
def get_cloud_status():
    """The cloud status endpoint — part of Phase 12's observability.

    Returns the status of the underground gateway, the cloud sync state, and
    the number of pending chunks. This is what the blackbox panel displays as
    "CLOUD STATUS".

    Note: This endpoint is required by `frontend/src/js/13-blackbox-cloud.js`.
    Without it, the Phase 12 blackbox panel cannot function.
    """
    gw = _gw()
    if gw is None:
        return jsonify(_absent("no blackbox gateway (no hub attached and no configured source)"))

    with gw._lock:
        blocks = gw.extended_blocks()
        rows = gw.store.chunks()

        held, releasable = [], 0
        for row in rows:
            if row["ack_sent_at"]:
                continue

            decision = gate_mod.evaluate(
                gate_mod.from_row(row, gw.store.count_pending(row["chunk_id"]),
                                  db_rows=gw.store.count_telemetry(row["chunk_id"])),
            )
            if decision.allowed:
                releasable += 1
            else:
                held.append({"chunk_id": decision.chunk_id, "reason": decision.reason})

        out = {
            "ok": True,
            "gateway": True,
            "mode": blocks["mode"],
            "blackbox": blocks["blackbox"],
            "cloud": blocks["cloud"],
            "ledger": {
                "chunks": len(rows),
                "held": len(held),
                "releasable": releasable,
                "acked": sum(1 for r in rows if r["ack_sent_at"]),
                "dropped_rows": sum(int(r["dropped_rows"] or 0) for r in rows),
                "reasons": held[:20],
            },
        }
    # #region agent log
    try:
        import json as _json, time as _time
        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "sessionId": "83ae1d", "hypothesisId": "I",
                "location": "routes_cloud.py:get_cloud_status",
                "message": "cloud status served",
                "data": {"gateway": True, "mode": out.get("mode")},
                "timestamp": int(_time.time() * 1000),
            }) + "\n")
    except Exception:
        pass
    # #endregion
    return jsonify(out)


import os

def _check_api_auth(req) -> bool:
    expected = os.getenv("NS_API_KEY")
    if not expected:
        return True
    provided = req.headers.get("X-API-Key") or req.headers.get("Authorization", "").replace("Bearer ", "")
    return provided == expected


@bp.route("/api/cloud/force_sync", methods=["POST"])
def cloud_force_sync():
    """Force an immediate cloud sync — Phase 12's disaster rehearsal endpoint."""
    if not _check_api_auth(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    gw = _gw()
    if gw is None:
        return jsonify({
            "ok": False,
            "gateway": False,
            "reason": "no blackbox gateway — there is no hub to ask"
        }), 409

    res = gw.force_sync()
    ok = res["batches_failed"] == 0
    return jsonify({"ok": ok, **res}), (200 if ok else 502)


@bp.route("/api/cloud/trace", methods=["GET"])
def get_cloud_trace():
    """The cloud trace endpoint — Phase 12's diagnostic tool.

    Used by the blackbox panel's "TRACE" button to show what the cloud sync
    actually did: which chunks were attempted, which succeeded, which failed.
    This is essential for the Phase 12 failure rehearsal and debugging.

    The trace is computed **per request** rather than stored, because it is
    a function of the world changing underneath it: a chunk that is held for
    "350 rows not yet confirmed" becomes releasable the moment the cloud answers,
    and a stored verdict would be a snapshot that ages into a lie.
    """
    gw = _gw()
    if gw is None:
        return jsonify(_absent("no blackbox gateway"))

    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 500))
    except (TypeError, ValueError):
        return jsonify({
            "ok": False,
            "error": f"limit must be an integer, got {request.args.get('limit')!r}"
        }), 400

    want_state = (request.args.get("state") or "").upper() or None
    if want_state and want_state not in ("OPEN", "CLOSED"):
        return jsonify({
            "ok": False,
            "error": f"state must be OPEN or CLOSED, got {want_state!r}"
        }), 400

    only_held = request.args.get("held") in ("1", "true", "yes")

    with gw._lock:
        entries = []
        for row in gw.store.chunks():
            cid = row["chunk_id"]
            if want_state and row["state"] != want_state:
                continue

            decision = gate_mod.evaluate(
                gate_mod.from_row(row, gw.store.count_pending(cid),
                                  db_rows=gw.store.count_telemetry(cid)))

            if only_held and (decision.allowed or row["ack_sent_at"]):
                continue

            entries.append({
                "chunk_id": cid,
                "state": row["state"],
                "expected_rows": row["expected_rows"],
                "received_rows": row["received_rows"],
                "dropped_rows": row["dropped_rows"],
                "pending": gw.store.count_pending(cid),
                "in_db": gw.store.count_telemetry(cid),
                "opened_at": row["opened_at"],
                "closed_at": row["closed_at"],
                "ack_sent_at": row["ack_sent_at"],
                "ack_reason": row["ack_reason"],
                "recover_at": row["recover_at"],
                "gate": {"allowed": decision.allowed, "reason": decision.reason},
            })

    entries.reverse()
    return jsonify({
        "ok": True,
        "gateway": True,
        "chunks": entries[:limit],
        "count": min(len(entries), limit),
        "total": len(entries),
        "generated": _iso_now(),
    })


def _iso_now() -> str:
    """ISO-8601 UTC timestamp for generated fields."""
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["bp", "bind", "get_cloud_status", "cloud_force_sync", "get_cloud_trace"]