"""B4 — `/api/alerts`, `/api/alerts/<id>/ack`, `/api/alerts/<id>/deliveries`.

Phase 8 (§8.3). The shape is deliberately small, because the console has to be
able to trust it under stress:

    GET  /api/alerts?limit=50&severity=HIGH&since=2026-09-23T10:00:00Z
    POST /api/alerts/<id>/ack        {"operator": "Safety Officer"}
    GET  /api/alerts/<id>/deliveries
    GET  /api/alerts/policy          (what the system may and may not suppress)

Two things this module insists on:

**The list never shows less than the system knows.** Database rows are the record,
but an alert that exists in memory and failed to persist — the database was busy,
the disk was full, the write raced the read — must still be on screen. Live alerts
that have no row yet are merged in and marked ``live: true``, so the panel has no
silent gap exactly when the site is in trouble.

**Acknowledgement goes through the dispatcher, not straight to SQL.** `acks` that
bypass the machine leave an open alert in memory that keeps escalating while the
database says ACKED — the console would show an alert that never stops re-notifying.
`ack_alert` updates both, and it refuses a blank operator (SRS §5.5).

State is **injected** by `app.py` via :func:`bind`, never imported from it. Phase 7
learned that lesson the expensive way: `from app import STATE` inside a request
handler imports a *second* copy of the module under `python3 app.py`, with a state
object that never sees a telemetry frame.
"""

from __future__ import annotations

import time

from flask import Blueprint, jsonify, request

import config
from alerts import channels, dispatcher
from persistence import db
from routing import dijkstra as routing

bp = Blueprint("alerts", __name__)

_STATE = None
_PERSIST = None


def bind(state, persist=db.persist_alert) -> None:
    """Inject the live state (and the persistence callback) once, at boot."""
    global _STATE, _PERSIST
    _STATE = state
    _PERSIST = persist


def _state():
    return _STATE


def _live_rows():
    """Open alerts held in memory — the ones the database may not have yet."""
    out = []
    for event in dispatcher.MACHINE.open_alerts.values():
        row = event.as_public()
        row["live"] = True
        out.append(row)
    return out


def _norm(ts: str) -> str:
    """`2026-09-23T10:00:00Z` → `2026-09-23T10:00:00+00:00` for psycopg2."""
    return ts.replace("Z", "+00:00") if ts.endswith("Z") else ts


@bp.route("/api/alerts")
def get_alerts():
    """The alert log, newest first. Filterable by tier, state and start time."""
    limit = request.args.get("limit", "50")
    try:
        limit_n = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": f"limit must be an integer, got {limit!r}"}), 400

    since = request.args.get("since")
    severity = request.args.get("severity")
    state = request.args.get("state")

    rows = db.fetch_alerts(limit=limit_n,
                           severity=severity,
                           since=_norm(since) if since else None,
                           state=state)
    known = {r.get("id") for r in rows}
    live = [r for r in _live_rows() if r.get("id") not in known]
    # Live rows go in front: they are the newest thing that happened, and an
    # operator scrolling a list is looking for exactly that.
    merged = (live + rows)[:limit_n]

    open_states = {"NEW", "ACTIVE"}
    return jsonify({
        "ok": True,
        "alerts": merged,
        "count": len(merged),
        "open": sum(1 for r in merged if str(r.get("state")) in open_states),
        "db_available": bool(rows) or db.count_alerts() is not None,
        "generated": _iso_now(),
        # Phase 8, second pass — **the lamps, not the messages.** An alert whose
        # local channel reports a failure is only half the story: what the operator
        # needs on the same screen is what each junction was last *told* and whether
        # the line took it. The inbox is written at the one door every down-link
        # goes through (`routing.push_hub_command`), so it cannot drift from the
        # wire — and it is served here, in the same poll, because a second request
        # per tick is a second thing that can be down.
        "downlink": {
            "attached": routing.downlink_attached(),
            "tick_s": float(config.HUB_LED_TICK_S),
            "reassert_s": float(config.HUB_REASSERT_S),
            "silence_s": float(config.HUB_SILENCE_S),
            **routing.inbox_stats(),
            "junction_inbox": routing.junction_inbox(),
        },
    })


@bp.route("/api/alerts/policy")
def get_policy():
    """What the dispatcher will and will not do — read by the console, not guessed.

    The frontend prints "ADVISORY — no SMS by policy" on suppressed rows. It must
    get that sentence from the server rather than hard-code it, or the panel will
    keep saying it after the policy changed.
    """
    return jsonify({
        "ok": True,
        "tiers": {tier: list(ch) for tier, ch in dispatcher.TIER_CHANNELS.items()},
        "escalation_adds": {t: list(c) for t, c in dispatcher.ESCALATION_CHANNELS.items()},
        "dedup_s": config.ALERT_DEDUP_S,
        "escalate_s": config.ALERT_ESCALATE_S,
        "settle_s": config.ALERT_SETTLE_S,
        "firewall_floor": config.FIREWALL_ALERT_FLOOR,
        "sms": {"max_per_hour": config.SMS_MAX_PER_HOUR,
                "configured": bool(channels.sms_recipients()),
                "transport": str(getattr(config, "SMS_TRANSPORT", "hub")),
                "recipients": len(channels.sms_recipients())},
        "email": {"configured": bool(config.SMTP_HOST and config.ALERT_EMAIL_TO)},
        "operators": list(config.ALERT_OPERATORS),
        "no_auto_suppress": ["CRITICAL"],
    })


@bp.route("/api/alerts/<int:alert_id>/ack", methods=["POST"])
def ack_alert(alert_id: int):
    """Human acknowledgement. Body: ``{"operator": "Safety Officer"}``."""
    body = request.get_json(silent=True) or {}
    operator = str(body.get("operator", "") or "").strip()
    if not operator:
        return jsonify({"ok": False,
                        "error": "operator is required — acknowledgement is a human act (SRS §5.5)"}), 400

    result = dispatcher.ack_alert(_state(), alert_id, operator, persist=_ack_persist)
    if not result.get("ok"):
        return jsonify(result), 404
    return jsonify({"ok": True, "id": alert_id, "state": result.get("state", "ACKED"),
                    "operator": operator, "acked_at": result.get("acked_at")})


def _ack_persist(alert_id, operator, now):
    """Bridge: the in-memory machine has already been updated by `ack_alert`.

    The database is asked second on purpose. A database that is down must not stop
    an operator from acknowledging the alert in front of them — the acknowledgement
    is real either way, and the row is reconciled by the next update.
    """
    res = db.ack_alert_row(alert_id, operator)
    if res.get("ok"):
        return res
    return {"ok": True, "state": "ACKED", "operator": operator,
            "acked_at": None, "db_error": res.get("error")}


def _delivery_row(d, live: bool = False) -> dict:
    """One ledger entry, from a `Delivery` object or a plain dict.

    The in-memory list is a mix of both — `channels.Delivery` for the sends and a
    small dict for the lifecycle notes the machine appends — so there is one
    normaliser here rather than a `getattr` chain at every call site.
    """
    row = d.as_row() if hasattr(d, "as_row") else dict(d)
    row.setdefault("detail", "")
    row["queued"] = bool(getattr(d, "queued", row.get("queued", False)))
    row["verified"] = bool(getattr(d, "verified", row.get("verified", False)))
    row["ts"] = row.get("ts")
    if live:
        row["live"] = True
    return row


@bp.route("/api/alerts/<int:alert_id>/deliveries")
def get_deliveries(alert_id: int):
    """The per-channel ledger — the 'prove it arrived' view (§8.3).

    An alert that exists but has *no* ledger rows answers 200 with an empty list:
    "we have no delivery record for this alert" is itself a finding, and a 404
    would let the console print "unknown alert" for a row it can plainly see.
    """
    rows = db.fetch_deliveries(alert_id)
    live = next((e for e in dispatcher.MACHINE.open_alerts.values() if e.id == alert_id), None)
    if not rows and live is not None:
        rows = [_delivery_row(d, live=True) for d in live.deliveries]
    if not rows and not db.alert_exists(alert_id):
        return jsonify({"ok": False, "error": f"no alert {alert_id}", "deliveries": []}), 404
    return jsonify({"ok": True, "id": alert_id, "deliveries": rows,
                    "channels": list(dict.fromkeys(r["channel"] for r in rows)),
                    "succeeded": [r["channel"] for r in rows if r.get("ok")],
                    "failed": [r["channel"] for r in rows if not r.get("ok")]})


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["bp", "bind"]
