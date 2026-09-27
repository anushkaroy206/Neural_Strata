"""The message itself — what the Safety Officer actually reads.  Phase 8 (B4).

Three rules shape everything in this file:

1. **Fit in 160 characters.** An SMS that is split into two messages is a message
   that arrives out of order. The body is assembled from a fixed budget, and the
   two facts that must never be cut are the **severity** and the **coordinates** —
   the problem statement asks for a located warning, and a warning without a
   "where" is a phone call, not a notification.
2. **Advise, do not merely alarm.** §8.6 is blunt about this: *"CRITICAL"* makes
   the receiver do the thinking under stress, while *"Route A BLOCKED. Use Exit 2."*
   is a decision. The actions come from `engine.severity.recommended_actions()`,
   which is Phase 6's ranked list — this module only chooses how many of them fit.
3. **Say where the numbers came from.** Every message carries a short reference
   (`NS-A7F3`) that ties the message to the `alerts_history` row, so a regulator
   holding a printed SMS can ask the database what the model saw at that second.

The reference is derived from the alert's own fields, so re-sending the same alert
re-sends the same reference: a message that changes identity on retry cannot be
matched to its ledger row.
"""

from __future__ import annotations

import hashlib

#: A single GSM-7 SMS. Not 160 *competing* things — the budget is the format.
SMS_MAX_LEN = 160

#: What an SMS is allowed to spend its characters on, in order of sacrifice.
#: The first two are never dropped; the rest fill what is left.
MUST_KEEP = ("severity", "where")

_TIER_WORD = {
    "CRITICAL": "CRITICAL",
    "HIGH": "HIGH",
    "ELEVATED": "ELEVATED",
    "WATCH": "WATCH",
    "NOMINAL": "NOMINAL",
    # the advisory form is spelled out because it is the *point* of the message
    "ADVISORY": "ADVISORY (environmental)",
}


def reference(alert) -> str:
    """The short id that ties a printed message to its ledger row.

    Deterministic from the alert's identity — node, tier, event type and the
    second it opened — so a retry, an escalation and a re-send all quote the same
    reference, and the operator can read one out loud to another human.
    """
    seed = "|".join(str(x) for x in (
        getattr(alert, "node_id", ""), getattr(alert, "severity", ""),
        getattr(alert, "event_type", ""), int(getattr(alert, "ts", 0) or 0),
    ))
    return "NS-" + hashlib.sha1(seed.encode()).hexdigest()[:4].upper()


def _band(alert) -> str:
    """`(31-88)` — the TTF interval, when the alert carries one."""
    q10, q90 = getattr(alert, "ttf_q10", None), getattr(alert, "ttf_q90", None)
    if q10 is None or q90 is None:
        return ""
    return f"({int(q10)}-{int(q90)})"


def action_list(alert, limit: int | None = None) -> list[str]:
    """The ranked actions, trimmed to `limit` (None = all).

    Ranked by Phase 6 and passed through unchanged: this module's only decision is
    how many of them fit in a message, never which ones matter more. If the list
    is empty — an alert raised before the severity layer had an estimate — the
    fallback is the honest one for the tier rather than silence.
    """
    actions = list(getattr(alert, "actions", None) or [])
    if not actions:
        actions = [{
            "CRITICAL": "EVACUATE. Do not approach. Account for all personnel at muster.",
            "HIGH": "Withdraw non-essential personnel. Prepare evacuation. Verify route.",
            "ELEVATED": "Restrict entry to zone. Increase monitoring to continuous.",
            "WATCH": "Monitor. No action required.",
        }.get(str(getattr(alert, "severity", "")).upper(),
              "Monitor. No action required.")]
    return actions if limit is None else actions[:limit]


def sms_body(alert) -> str:
    """The 160-character message. Severity and coordinates are never cut.

    Shape, and why each field earned its characters:

        NEURAL STRATA CRITICAL | N2 Tunnel A | TTF 47s (31-88) | TERTIARY
        Route A BLOCKED. Use Exit 2. 23.64321,85.31804 | NS-A7F3

    The tier word leads, because a lock screen shows the first line. The node and
    its tunnel follow, because "CRITICAL" without a place is a siren. The TTF with
    its interval is the model's own answer to *how long*, and printing the band
    beside the point estimate is what keeps a 2.5 s median honest. The stage says
    *how far along*. Then the action, then the coordinates, then the reference.
    """
    severity = _TIER_WORD.get(str(getattr(alert, "severity", "")).upper(),
                              str(getattr(alert, "severity", "WATCH")).upper())
    where = getattr(alert, "where", "") or _where_from_alert(alert)
    head = f"NEURAL STRATA {severity} | {where}".rstrip(" |")

    parts = [head]
    ttf = getattr(alert, "ttf_seconds", None)
    if ttf is not None:
        band = _band(alert)
        parts.append(f"TTF {int(ttf)}s {band}".strip())
    stage = getattr(alert, "progression_stage", None)
    if stage and stage != "STABLE":
        parts.append(str(stage))
    line1 = " | ".join(parts)

    tail = []
    actions = action_list(alert, limit=2)
    if actions:
        tail.append(" ".join(actions))
    coords = f"{float(getattr(alert, 'geo_lat', 0.0)):.5f},{float(getattr(alert, 'geo_lon', 0.0)):.5f}"
    ref = reference(alert)

    body = line1 + "\n" + " ".join(tail)
    budget = SMS_MAX_LEN - len(coords) - len(ref) - 4      # " | " and a space
    if len(body) > budget:
        # Trim the *actions*, not the facts: a truncated instruction is still an
        # instruction, a truncated coordinate is a wrong place.
        body = body[:budget - 1].rstrip(" ,.;") + "…"
    return f"{body} {coords} | {ref}"


_EXIT_LABEL = {"EXIT1": "Exit 1 (west)", "EXIT2": "Exit 2 (east)"}


def locality_message(alert) -> str:
    """The text the people near the mine actually receive.

    This is longer than one 160-character segment on purpose. The laptop's
    Messages app sends it as one message. It names the mine, the ground that
    is moving, and what to do, and it only quotes a time-to-failure or an exit
    when the alert itself carries one.
    """
    where = getattr(alert, "where", "") or _where_from_alert(alert) or "the working panel"
    lat = float(getattr(alert, "geo_lat", 0.0) or 0.0)
    lon = float(getattr(alert, "geo_lon", 0.0) or 0.0)
    if lat == 0.0 and lon == 0.0:
        lat, lon = 23.643, 85.318
    level = str(getattr(alert, "level_id", "") or "").strip()
    place = f"Level {level}, {where}" if level else where

    lines = [
        "NEURAL STRATA RED WARNING. Ground subsidence.",
        f"Mine: Jharia panel, {lat:.5f} N, {lon:.5f} E.",
        f"Area affected: {place}, and the tunnels that meet that ground. Stay out of it.",
    ]
    actions = action_list(alert, limit=2)
    if actions:
        lines.append(" ".join(a.strip().rstrip(".") + "." for a in actions if str(a).strip()))
    route = getattr(alert, "route", None) or {}
    exit_id = str(route.get("exit") or "") if isinstance(route, dict) else ""
    if exit_id:
        lines.append(f"Leave by {_EXIT_LABEL.get(exit_id, exit_id)} if that way is still open.")
    ttf = getattr(alert, "ttf_seconds", None)
    if ttf is not None:
        q10, q90 = getattr(alert, "ttf_q10", None), getattr(alert, "ttf_q90", None)
        span = (f", likely between {int(q10)} and {int(q90)} seconds"
                if q10 is not None and q90 is not None else "")
        lines.append(f"Estimated time to failure: {int(ttf)} seconds{span}.")
    lines.append(f"Quote {reference(alert)} if you call the control room.")
    return "\n".join(lines)


def _where_from_alert(alert) -> str:
    """`N2 Tunnel A` — node short name plus its tunnel, when the alert knows it.

    The em dash in the map's label becomes a space rather than a cut: "N2 Tunnel A"
    tells a receiver standing in the west adit which level and which tunnel, and
    the four characters it costs are worth it.
    """
    label = str(getattr(alert, "node_label", "") or getattr(alert, "node_id", ""))
    # "N2 — Tunnel A · collapse face" → "N2 Tunnel A": the descriptor after the
    # interpunct is operator detail for the console, not something to spend SMS
    # characters on.
    words = " ".join(label.split("·")[0].replace("—", " ").split())
    return words


def email_subject(alert) -> str:
    """The severity is the **first word**, so it survives a phone's subject clip."""
    return (f"[{str(getattr(alert, 'severity', '')).upper()}] "
            f"{_where_from_alert(alert)} — {getattr(alert, 'event_type', 'alert')} "
            f"| Neural Strata {reference(alert)}")


def geo_link(alert, base: str | None = None) -> str:
    """A georeferenced deep link into the console's GIS card.

    §8.3 asks for a link to the map state at the time of the alert. The map is the
    *live* `/api/map`, and the alert's own coordinates are carried in the query so
    the receiver can open the exact point even when the live map has moved on —
    and so that opening the link later still says where this message was about.
    """
    base = (base or "").rstrip("/")
    lat = float(getattr(alert, "geo_lat", 0.0))
    lon = float(getattr(alert, "geo_lon", 0.0))
    ref = reference(alert)
    return f"{base}/#gis-map?alert={ref}&lat={lat:.5f}&lon={lon:.5f}"


def email_body(alert, base: str | None = None) -> str:
    """The full record: everything the SMS could not afford to say.

    Plain text on purpose — a regulator's mail client, a print-out and a paste
    into a report all survive plain text, and none of them reliably survive a
    table-based HTML mail.
    """
    sev = str(getattr(alert, "severity", "")).upper()
    ref = reference(alert)
    lat, lon = float(getattr(alert, "geo_lat", 0.0)), float(getattr(alert, "geo_lon", 0.0))
    band = _band(alert) or "(none — the model has no interval for this window)"

    lines = [
        f"NEURAL STRATA — {sev} ALERT  ·  {ref}",
        "=" * 64,
        "",
        f"Node        : {_where_from_alert(alert)}  ({getattr(alert, 'node_id', '')})",
        f"Level       : {getattr(alert, 'level_id', '')}",
        f"Event       : {getattr(alert, 'event_type', '')}",
        f"Raised at   : {getattr(alert, 'iso', '')}",
        f"Severity    : {sev}",
        f"Stage       : {getattr(alert, 'progression_stage', None) or '—'}",
        f"Time to fail: {getattr(alert, 'ttf_seconds', None) or '—'} s  {band}",
    ]
    label = getattr(alert, "disruption_label", None)
    if label:
        lines.append(f"M1 class    : {label}"
                     + (f"  (index {getattr(alert, 'disruption_index', 0):.2f})"
                        if getattr(alert, "disruption_index", None) is not None else ""))
    lines += [
        f"Position    : {lat:.5f}, {lon:.5f}",
        "",
        "RECOMMENDED ACTIONS",
    ]
    lines += [f"  {i}. {a}" for i, a in enumerate(action_list(alert), start=1)]

    route = getattr(alert, "route", None)
    lines += ["", "RECOMMENDED ROUTE"]
    if route and route.get("path"):
        lines.append(f"  {' → '.join(route['path'])}")
        lines.append(f"  exit {route.get('exit')} · {route.get('base_m')} m · "
                     f"~{route.get('eta_s')} s at 1.2 m/s")
    else:
        lines.append("  no open route at the time of this alert — HOLD (do not move)")

    lines += [
        "",
        f"Live map    : {geo_link(alert, base)}",
        "",
        "-- ",
        "Automatic message. Delivery for this alert is recorded per channel and can",
        "be audited at /api/alerts/<id>/deliveries. Acknowledging it in the console",
        "locks the row; only the Safety Officer or Control Room may do so (SRS §5.5).",
    ]
    return "\n".join(lines)


__all__ = ["SMS_MAX_LEN", "MUST_KEEP", "reference", "action_list", "sms_body",
           "locality_message", "email_subject", "email_body", "geo_link"]
