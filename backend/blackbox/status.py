"""Live blackbox + cloud status, projected off the ingest + gate truth store.

The operator deck reads both blocks out of GET /api/extended:

    raw.blackbox = {sd_ok, usb_ok, power_ok, chunk_id, row, rows_per_chunk,
                    free_mb, card_mb, ram_ring, retained:[{chunk_id, rows}]}
    raw.cloud    = {online, pending, sent, next_upload_s,
                    last_upload:{ts, rows, batches, ok}, last_ack:{chunk_id, ts}}

Every number here is a projection of what the hub actually reported over the
serial line and what the gate actually released. Nothing is seeded, nothing is
invented.

Fields the hub cannot report are OMITTED rather than guessed. The deck guards
every one of them (`isFinite(...)` / `!== undefined`), so an absent key leaves
the panel's own default in place instead of showing a number we cannot
stand behind:

    ram_ring   protocol v1 carries no RAM-ring depth on the wire
    card_mb    SD capacity is a build-time fact, not a hub reading. It is
               reported only when CARD_MB is configured (the card size is still
               an open decision), otherwise the deck keeps its own default.

power_ok is taken from link liveness: the hub is locally powered and sends no
separate power telemetry in v1, so a talking hub is a powered hub and a silent
hub is reported as not-ok rather than assumed fine.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from gate import evaluate, from_row

ROWS_PER_CHUNK = 1000     # the hub rolls chunk_XXXX.csv every 1000 rows
RETAINED_MAX = 50         # newest N held chunks sent to the deck
HUB_ALIVE_S = 20.0        # no line for this long and the serial link is called down


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _hub_alive(hub_row, alive_s: float, now: Optional[datetime]) -> bool:
    seen = _parse_iso(hub_row["last_seen"] if hub_row else None)
    if seen is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - seen).total_seconds() <= alive_s


def _hhmmss(ts: Optional[str]) -> Optional[str]:
    d = _parse_iso(ts)
    return d.astimezone().strftime("%H:%M:%S") if d else None


# --------------------------------------------------------------------- blackbox
def blackbox_status(store, *, card_mb: Optional[int] = None,
                    rows_per_chunk: int = ROWS_PER_CHUNK,
                    hub_alive_s: float = HUB_ALIVE_S,
                    now: Optional[datetime] = None,
                    recovery: Optional[dict] = None,
                    laptop: Optional[dict] = None) -> dict:
    hub = store.hub()
    alive = _hub_alive(hub, hub_alive_s, now)
    state = ((hub["blackbox_state"] if hub else None) or "OK").upper()

    chunks = store.chunks()
    open_chunks = [c for c in chunks if c["state"] == "OPEN"]
    current = open_chunks[-1] if open_chunks else (chunks[-1] if chunks else None)

    out = {
        # BLACKBOX:NOSD / FULL / FAULT are the three failure words the hub sends
        "sd_ok": state not in ("NOSD", "FAULT"),
        "usb_ok": alive,          # is the serial link delivering lines right now
        "power_ok": alive,        # talking == powered (no separate power telemetry in v1)
        "rows_per_chunk": rows_per_chunk,
    }

    if current is not None:
        out["chunk_id"] = current["chunk_id"]
        out["row"] = current["received_rows"]

    free_mb = hub["free_mb"] if hub else None
    if free_mb is not None:
        out["free_mb"] = free_mb
    if card_mb:
        out["card_mb"] = card_mb

    # What is still sitting on the SD card: closed, complete, not yet released by
    # the gate. This is the number that must fall when ACK_DELETE goes out.
    held = [c for c in chunks if c["state"] == "CLOSED" and not c["ack_sent_at"]]
    # Capped so a cloud outage cannot grow the payload without bound. The true
    # count rides alongside, so nothing is hidden — a UI can say "50 of 320".
    # …and **why each of them is still there**, in the gate's own words.
    #
    # §5.6 requires the refusal reason to be displayed, and this is where the deck
    # reads it: the alternative was a second poll of /api/blackbox/chunks, which
    # would put the ledger one request out of step with the numbers beside it — a
    # panel showing "0 pending" and a reason from three seconds ago is a panel that
    # argues with itself. `evaluate()` is a pure function over the row plus two
    # counts, so computing it here costs a few microseconds and can never be stale.
    retained, releasable = [], 0
    for c in held[-RETAINED_MAX:]:
        d = evaluate(from_row(c, store.count_pending(c["chunk_id"]),
                              db_rows=store.count_telemetry(c["chunk_id"])))
        if d.allowed:
            releasable += 1
        retained.append({"chunk_id": c["chunk_id"], "rows": c["received_rows"],
                         "reason": d.reason, "releasable": d.allowed})
    out["retained"] = retained
    out["retained_total"] = len(held)
    out["retained_releasable"] = releasable

    # A hub-level refusal outranks any per-chunk reason: when the card is out or
    # the hub has gone quiet, *every* chunk is held for the same reason, and the
    # reason an operator needs is the one about the card, not about row counts.
    # (This is `ingest.Ingestor.hub_delete_ok()`'s decision, restated here because
    # the projection must not import the writer.)
    if laptop and laptop.get("mounted"):
        # The archive for this run is the card in the laptop. The hub's own
        # socket can stay NOSD; the panel follows the volume that is actually
        # receiving rows.
        out["sd_ok"] = True
        out["free_mb"] = laptop.get("free_mb")
        if laptop.get("card_mb"):
            out["card_mb"] = laptop["card_mb"]
        out["archive"] = laptop.get("path")
        # The deck's chunk number is the file on this volume, not the newest
        # OPEN row in sqlite. A restarted hub reuses small ids while older
        # open chunks are still in the database.
        if laptop.get("chunk_id") is not None:
            out["chunk_id"] = int(laptop["chunk_id"])
            out["row"] = int(laptop.get("row") or 0)
        if laptop.get("on_card") is not None:
            out["on_card"] = int(laptop["on_card"])
        # #region agent log
        try:
            _left = getattr(blackbox_status, "_dbg_fe", 4)
            if _left > 0:
                blackbox_status._dbg_fe = _left - 1
                import json as _json, time as _time
                with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({
                        "sessionId": "83ae1d", "hypothesisId": "FE",
                        "runId": "post-fix",
                        "location": "status.py:blackbox_status",
                        "message": "deck chunk follows the laptop card",
                        "data": {"chunk_id": out.get("chunk_id"), "row": out.get("row"),
                                 "on_card": out.get("on_card"), "archive": out.get("archive"),
                                 "sqlite_open": current["chunk_id"] if current is not None else None},
                        "timestamp": int(_time.time() * 1000),
                    }) + "\n")
        except Exception:
            pass
        # #endregion
    elif state != "OK":
        out["delete_blocked"] = f"hub reports {state} — no delete sent"
    elif not alive:
        out["delete_blocked"] = "hub silent — no delete sent"

    # Phase 11: the recovery plane's own state, passed in by the gateway rather than
    # derived here. `status.py` is a *projection of the store* and must stay one —
    # the handshake and the in-flight REPLAY live in objects that talk to the hub,
    # and reaching into them from a read-only projection is how a status module
    # starts making decisions. Passing the block in keeps the dependency pointing
    # one way: the gateway knows about status, status knows about nothing.
    if recovery is not None:
        out["recovery"] = recovery
    return out


# ------------------------------------------------------------------------ cloud
def cloud_status(store, *, online: bool, next_upload_s: float = 0.0) -> dict:
    pending = store.pending_total()
    sent = store.sent_total()

    last_upload = None
    batch = store.last_batch()
    if batch is not None:
        last_upload = {
            "ts": _hhmmss(batch["completed_at"] or batch["created_at"]),
            "rows": batch["row_count"],
            "batches": 1,
            "ok": batch["status"] == "SENT",
        }

    # last_ack is the last ACK_DELETE that actually went out to the hub. It can
    # only have been sent after the cloud confirmed every row of that chunk.
    last_ack = None
    acked = [c for c in store.chunks() if c["ack_sent_at"]]
    if acked:
        newest = max(acked, key=lambda c: c["ack_sent_at"])
        last_ack = {"chunk_id": newest["chunk_id"], "ts": _hhmmss(newest["ack_sent_at"])}

    out = {
        "online": bool(online),
        "pending": pending,
        "sent": sent,
        "next_upload_s": max(0, int(round(next_upload_s))),
    }
    if last_upload:
        out["last_upload"] = last_upload
    if last_ack:
        out["last_ack"] = last_ack
    return out
