"""
Phase 10 (B13 · B14) — the blackbox's own API: `/api/blackbox/status` and
`/api/blackbox/chunks`.
================================================================================

Why these two endpoints exist when `/api/extended` already carries a `blackbox`
and a `cloud` block
--------------------------------------------------------------------------------
`/api/extended` is the *deck's* payload: two blocks, shaped for the chips, polled
at 2 Hz by a page that is already talking to a dozen endpoints. It answers "what
should the panel say right now".

This module answers the question a person asks **about the data**: *which chunks
exist, what is in each one, and — if it is being held — why.* That is the chunk
ledger, it is a read of the gate's own decisions, and §5.6 says the refusal reason
is *displayed*. A reason that lives only in a log line is a reason nobody reads, so
it is served here, per chunk, next to the numbers it was derived from.

The two rules that shape every response
---------------------------------------
1. **Nothing is invented.** Every number comes from the gateway's truth store —
   what the hub actually reported, what actually landed, what the cloud actually
   confirmed. Where a field is genuinely unknown it is `null`, never 0, because
   `expected_rows: null` and `expected_rows: 0` mean opposite things to the gate.
2. **A missing gateway is a state, not an error.** With no serial port and no
   configured source the panel must still render — it says `gateway: false` and the
   panel shows its own "no hub attached" state. Returning 503 for a perfectly
   normal bench configuration would teach every caller to ignore 503.

`/api/blackbox/recover` (§B15) is the third, and it is a **command**, not a
projection: it makes the gateway ask the hub what it has and then ask for whatever
is still missing. It is the manual half of a mechanism that also runs on its own —
the handshake fires on every reconnect and the recovery driver follows gaps on the
upkeep tick — because the automatic path is exactly the thing you want to be able
to trigger by hand at 20:00 on a stage.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

# The gate is the authority on which chunks may be released; the store is the
# authority on what is in them. This module only joins the two.
from blackbox import gate as gate_mod

bp = Blueprint("blackbox", __name__)

#: Set once at boot to a **callable** returning the live gateway (app.py's
#: `BLACKBOX` global is rebound when the gateway finally starts, so handing over the
#: object itself would freeze `None` into this module).
_GATEWAY = None


def bind(gateway) -> None:
    """Inject the gateway source — a callable, not the gateway.

    `start_blackbox()` runs after the blueprints are registered and may also be
    called again later (a serial reconnect), so the route must ask "where is the
    gateway *now*" rather than remember what it was at import time.
    """
    global _GATEWAY
    _GATEWAY = gateway


def _gw():
    if _GATEWAY is None:
        return None
    return _GATEWAY() if callable(_GATEWAY) else _GATEWAY


def _absent(why: str):
    """The honest shape when there is no gateway behind this API."""
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


# --------------------------------------------------------------------------- status
@bp.route("/api/blackbox/status", methods=["GET"])
def blackbox_status():
    """The chips' source of truth, plus the gate's summary.

    `held` is the count the operator actually needs: chunks that are **on the card
    and not releasable**. It is not a warning about storage — it is the number of
    chunks whose rows exist in exactly one place, and it should fall when the cloud
    confirms and the gate releases. A `held` that only grows is the signature of a
    sync that is not confirming, which is why `reasons` travels with it.
    """
    gw = _gw()
    if gw is None:
        return jsonify(_absent("no blackbox gateway (no hub attached and no "
                               "configured source)"))

    with gw._lock:                                          # noqa: SLF001 — one lock
        blocks = gw.extended_blocks()
        rows = gw.store.chunks()
        # Three verdicts, and the difference between them is the whole point of
        # this ledger. A chunk we have *not* asked the hub to delete is either
        # ##releasable## (every condition met, waiting for the next gate sweep) or
        # ##held## (refused, with a reason). A chunk we *have* asked about is
        # ##acked## — and an acknowledged chunk is not "held" just because
        # `evaluate()` now refuses it: it refuses it precisely *because* we already
        # asked, which is a released chunk, not a stuck one. Counting those as held
        # would make the operator's "why is nothing deleting" number grow every
        # time something deleted.
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
    return jsonify(out)


# --------------------------------------------------------------------------- chunks
@bp.route("/api/blackbox/chunks", methods=["GET"])
def blackbox_chunks():
    """The chunk ledger, newest first, with each chunk's gate decision attached.

        GET /api/blackbox/chunks?limit=50&state=CLOSED&held=1

    The gate decision is computed **per request** rather than stored, because it is
    a function of the world changing underneath it: a chunk that is held for
    "350 rows not yet confirmed" becomes releasable the moment the cloud answers,
    and a stored verdict would be a snapshot that ages into a lie. That is also why
    the reason travels as text — the operator reads *why*, not a status code.
    """
    gw = _gw()
    if gw is None:
        return jsonify(_absent("no blackbox gateway"))

    try:
        limit = max(1, min(int(request.args.get("limit", "50")), 500))
    except (TypeError, ValueError):
        return jsonify({"ok": False,
                        "error": f"limit must be an integer, got "
                                 f"{request.args.get('limit')!r}"}), 400

    want_state = (request.args.get("state") or "").upper() or None
    if want_state and want_state not in ("OPEN", "CLOSED"):
        return jsonify({"ok": False,
                        "error": f"state must be OPEN or CLOSED, got {want_state!r}"}), 400
    only_held = request.args.get("held") in ("1", "true", "yes")

    with gw._lock:                                          # noqa: SLF001
        entries = []
        for row in gw.store.chunks():
            cid = row["chunk_id"]
            if want_state and row["state"] != want_state:
                continue
            decision = gate_mod.evaluate(
                gate_mod.from_row(row, gw.store.count_pending(cid),
                                  db_rows=gw.store.count_telemetry(cid)))
            # `held=1` means *stuck*, not *refused*: a chunk the hub has already
            # been asked to unlink is on its way out, whatever the gate would say
            # about it now (it says "already acknowledged", and always will).
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

    entries.reverse()                                       # newest chunk first
    return jsonify({
        "ok": True,
        "gateway": True,
        "chunks": entries[:limit],
        "count": min(len(entries), limit),
        "total": len(entries),
        "generated": _iso_now(),
    })


# --------------------------------------------------------------------------- recover
@bp.route("/api/blackbox/recover", methods=["POST"])
def blackbox_recover():
    """Ask the hub for its counts, then ask it to re-stream what is missing.

        POST /api/blackbox/recover            → STATE? + a REPLAY burst for every gap
        POST /api/blackbox/recover?wait=10    → …and block up to N seconds, then report
                                                 what actually arrived

    The reply is the recovery block, not a promise: `replay_lines` are the asks that
    went out, `missing_rows` is what was short when they did, and `summary` is the
    same sentence the operator will read on the ledger. What the burst *recovers* is
    reported by `/api/blackbox/status` afterwards, because it takes time for a card
    to be re-read and pretending otherwise would be the one thing this API never
    does.

    **409, not 503, when there is no gateway.** 503 says "try again later"; this says
    "there is nothing to ask", which is true on a bench with no hub attached and will
    still be true in an hour. A command that cannot be carried out must not be
    reported as a success, which is why this is the one blackbox route that does not
    answer `gateway: false` with a 200.
    """
    gw = _gw()
    if gw is None:
        return jsonify({"ok": False, "gateway": False,
                        "reason": "no blackbox gateway — there is no hub to ask"}), 409

    wait = request.args.get("wait", type=float) or 0.0
    out = gw.force_reconcile()
    if wait > 0:
        import time
        time.sleep(min(wait, 60.0))            # bounded: this is an HTTP handler
        with gw._lock:                          # noqa: SLF001
            out["after"] = {
                "missing_rows": gw.reconciler.total_missing(),
                "recovered": gw.recovery.block()["recovered"],
                "active": gw.recovery.block()["active"],
            }
    out["gateway"] = True
    return jsonify(out)


def _iso_now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
