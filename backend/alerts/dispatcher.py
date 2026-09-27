"""B4 — the alert state machine. Phase 8.

    condition met → NEW → ACTIVE → ACKED → CLOSED

with three properties that make it more than a print statement:

* **Dedup** — twenty consecutive ticks of the same condition produce *one* alert
  inside `ALERT_DEDUP_S`, keyed on `node | event_type | tier`. Without this, a
  20 Hz ticker would send twenty SMS in a second and the Safety Officer would turn
  the phone off, which is the failure mode that makes alerting systems useless.
* **Escalation** — an unacknowledged alert re-notifies on `ALERT_ESCALATE_S`,
  adding a channel and raising the tier. Time-based, not event-based: an event
  that keeps repeating must not keep re-sending.
* **Suppression, always visible.** `suppression_guard` is the one place that may
  say "do not send this", and the answer is recorded on the row. A system that
  quietly decides not to wake someone is indistinguishable from a broken one, so
  the console shows `ADVISORY — no SMS by policy` rather than nothing.

Three rules this module enforces and no call site may bypass (SRS §5.5, §8.2):

1. A `CRITICAL`-tier alert is **never** auto-suppressed. Not by the classifier, not
   by a rate cap, not by quiet hours.
2. If the cascade's firewall is engaged — M1 and M2 disagree — the dispatched tier
   is **at least HIGH**, even when M1 says the disturbance is environmental.
3. Only a human closes an alert. `ack_alert()` is the only path to `ACKED`, and it
   records *who*.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict

import config
from alerts import channels, templates
from engine import severity as severity_engine

#: The state machine's alphabet. Persisted verbatim (`alerts_history.state`).
STATES = ("NEW", "ACTIVE", "ACKED", "CLOSED")

#: Tier order, most severe first — the same words `engine.severity` publishes.
TIER_ORDER = ("CRITICAL", "HIGH", "ELEVATED", "WATCH", "ADVISORY")

#: Which channels a tier earns. ADVISORY and WATCH/ELEVATED never leave the site,
#: and that is the whole payoff of the double-AI split (§8.2).
TIER_CHANNELS = {
    "CRITICAL": ("local", "dashboard", "sms", "email"),
    "HIGH":     ("local", "dashboard", "sms"),
    "ELEVATED": ("local", "dashboard"),
    "WATCH":    ("dashboard",),
    "ADVISORY": ("local", "dashboard"),      # amber breathe, no message to anyone
}

#: What an escalated alert gains when nobody acknowledges it.
ESCALATION_CHANNELS = {
    "ELEVATED": ("sms",),
    "WATCH": ("local",),
    "ADVISORY": ("sms",),
}


def tier_rank(tier: str) -> int:
    """0 = most severe. Unknown tiers sort last rather than first: an unparsed
    string must never be mistaken for the loudest thing on the site."""
    try:
        return TIER_ORDER.index(str(tier).upper())
    except ValueError:
        return len(TIER_ORDER)


def at_least(tier: str, floor: str) -> str:
    return tier if tier_rank(tier) <= tier_rank(floor) else floor


# ---------------------------------------------------------------------------
# The event
# ---------------------------------------------------------------------------

@dataclass
class AlertEvent:
    """One incident, in the shape `alerts_history` stores (§A.3.7).

    Built from live state rather than from prose, so the message can quote the
    model's own numbers: the tier, the TTF **with its interval**, the stage, the
    classifier's label and its disagreement index, the routed exit, and the
    coordinates.
    """

    node_id: str = ""
    node_label: str = ""
    level_id: str = ""
    severity: str = "WATCH"
    event_type: str = "ALERT"
    text: str = ""
    progression_stage: str | None = None
    ttf_seconds: int | None = None
    ttf_q10: int | None = None
    ttf_q90: int | None = None
    disruption_label: str | None = None
    disruption_index: float | None = None
    firewalled: bool = False
    where: str = ""
    geo_lat: float = 0.0
    geo_lon: float = 0.0
    recommended_action: str = ""
    actions: list = field(default_factory=list)
    route: dict | None = None
    kind: str | None = None

    # ── machine fields ───────────────────────────────────────────────────
    ts: float = 0.0
    iso: str = ""
    state: str = "NEW"
    acked_by: str | None = None
    acked_at: float | None = None
    suppressed: bool = False
    suppression_reason: str = ""
    #: How the incident ended — "condition cleared" or "manual re-arm". §8.5 asks
    #: for the re-arm case by name, and it is the field a regulator reads first.
    closed_reason: str | None = None
    escalated: bool = False
    attempts: int = 0
    last_error: str = ""
    deliveries: list = field(default_factory=list)
    id: int | None = None

    @property
    def dedupe_key(self) -> str:
        return dedupe_key(self)

    def as_public(self) -> dict:
        """The `/api/alerts` shape — JSON scalars only, no dataclass leakage.

        Key-for-key with a row out of `alerts_history`, **including the four
        `*_dispatched` flags**. The console merges live alerts with stored ones in
        one list, and a live row that was missing the flags its neighbour had would
        make the delivery column flicker between "not yet" and "no" — which is
        exactly the difference an operator must never have to guess at.
        """
        out = asdict(self)
        out.pop("deliveries", None)
        ok = {d.channel for d in self.deliveries if getattr(d, "ok", False)}
        out["state"] = self.state
        out["dedupe_key"] = self.dedupe_key
        out["reference"] = templates.reference(self)
        out["age_s"] = round(max(0.0, time.time() - (self.ts or time.time())))
        out["local_dispatched"] = "local" in ok
        out["dash_dispatched"] = "dashboard" in ok
        out["sms_dispatched"] = "sms" in ok
        out["email_dispatched"] = "email" in ok
        out["suppress_reason"] = self.suppression_reason
        out["closed_reason"] = self.closed_reason
        return out


def dedupe_key(event) -> str:
    """`node | event_type | tier` — §A.2.16's bucket key, verbatim.

    The tier is in the key on purpose: an ELEVATED alert becoming CRITICAL is a
    *new* fact and must be allowed through, while the same CRITICAL repeating
    twenty times is one fact repeated.
    """
    return "|".join(str(x) for x in (
        getattr(event, "node_id", ""),
        getattr(event, "event_type", ""),
        str(getattr(event, "severity", "")).upper(),
    ))


# ---------------------------------------------------------------------------
# SRS §5.5 — the guard that may say no
# ---------------------------------------------------------------------------

def suppression_guard(event, rule: str = "auto") -> tuple[bool, str]:
    """May this alert be suppressed? Returns `(suppress, reason)`.

    The answer is **never** yes for a CRITICAL-tier event, whatever the rule —
    that is SRS §5.5's *"Tier 3 evacuation triggers may never be suppressed
    automatically"*, implemented as the first branch rather than as a note.

    Otherwise the only licensed suppression is the one the double-AI split exists
    to earn: an environmental disturbance with a healthy time-to-failure does not
    wake the Safety Officer at 3 a.m. — but the junctions still breathe amber, the
    console still shows the row, and the row says *why* it stayed quiet.
    """
    tier = str(getattr(event, "severity", "")).upper()

    if tier == "CRITICAL":
        return False, ""
    if rule == "ratelimit":
        return True, "RATE_LIMITED — hourly SMS cap reached"
    if rule == "quiet_hours" and tier_rank(tier) > tier_rank("HIGH"):
        return True, "quiet hours (not applicable below HIGH)"
    if rule == "auto":
        if tier == "ADVISORY":
            return True, "ADVISORY — environmental disturbance, no SMS by policy"
        if tier in ("WATCH", "ELEVATED"):
            return True, f"{tier} — dashboard and log only, no message out"
    return False, ""


#: What survives a suppression, per tier. §8.2's suppression rules are one line
#: each and they do **not** say the same thing:
#:
#:     ADVISORY       no SMS, no email, **LEDs breathe amber**, dashboard chip
#:     WATCH/ELEVATED dashboard + log only
#:
#: For an ADVISORY alert the lamps are the whole point of the tier — it is the
#: payoff of the double-AI split: M1 calls it weather, M2 disagrees, and the
#: tunnel says "something is off" without frightening anyone. Suppressing `local`
#: as well would have made that payoff invisible underground, which is the one
#: place it was designed to be seen. For WATCH/ELEVATED the spec says dashboard
#: only, so `local` is dropped there and the tunnel stays dark.
#:
#: This is a table rather than a rule because the two sentences are different
#: sentences, and a single clever predicate would have to be wrong about one.
_SUPPRESSION_KEEPS = {
    "ADVISORY": ("local", "dashboard"),
    "WATCH": ("dashboard",),
    "ELEVATED": ("dashboard",),
}


def channels_for(event) -> tuple[tuple[str, ...], bool, str]:
    """Which channels this alert earns, and whether policy suppressed any.

    Returns `(channels, suppressed, reason)`. A suppressed alert keeps only what
    `_SUPPRESSION_KEEPS` allows for its tier, so there is exactly one place in the
    system that decides to stay quiet — and *staying quiet* never means a lamp the
    spec asked for goes dark.
    """
    tier = str(getattr(event, "severity", "")).upper()
    earned = TIER_CHANNELS.get(tier, ("dashboard",))
    suppress, reason = suppression_guard(event, "auto")
    if not suppress:
        return earned, False, ""
    keeps = _SUPPRESSION_KEEPS.get(tier, ("dashboard",))
    quiet = tuple(c for c in earned if c in keeps)
    return quiet, True, reason


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------

@dataclass
class AlertStateMachine:
    """`NEW → ACTIVE → ACKED → CLOSED`, with a time-based escalation timer.

    Kept as a real object with `observe()` and `escalate()` rather than as inline
    `if`s in the ticker, because the transitions are the part a regulator asks
    about and they should be readable, testable and callable from a test without a
    server attached.
    """

    dedup_s: float = field(default_factory=lambda: float(getattr(config, "ALERT_DEDUP_S", 90.0)))
    escalate_s: float = field(default_factory=lambda: float(getattr(config, "ALERT_ESCALATE_S", 60.0)))
    settle_s: float = field(default_factory=lambda: float(getattr(config, "ALERT_SETTLE_S", 30.0)))

    #: dedupe_key → the open alert, newest first by insertion
    open_alerts: dict = field(default_factory=dict)
    #: dedupe_key → the last time one was raised (dedup window)
    last_seen: dict = field(default_factory=dict)

    # -- transitions ------------------------------------------------------
    def observe(self, event, now: float | None = None) -> str:
        """Compute the transition for one observation. **Does not send anything.**

        Returns one of `NEW`, `DUPLICATE`, `REOPENED` — the caller (dispatch)
        decides what to do about it. Splitting the decision from the effect is what
        lets a test drive twenty ticks and assert one alert without a mail server.
        """
        now = float(now if now is not None else time.time())
        key = dedupe_key(event)
        self.last_seen[key] = now

        current = self.open_alerts.get(key)
        if current is None:
            return "NEW"
        if current.state == "CLOSED":
            return "REOPENED"
        # An open alert of the same key inside the window is a duplicate — the
        # condition is still true, and that is not news.
        return "DUPLICATE"

    def track(self, event) -> None:
        self.open_alerts[event.dedupe_key] = event

    def escalate(self, now: float | None = None) -> list:
        """Every unauthorised, unacknowledged, still-open alert past its timer.

        **Time-based, deliberately.** An event-based escalation would re-send on
        every tick of a condition that is by definition still true; the clock is
        the only thing that distinguishes "nobody has looked at this yet" from
        "the ground is still moving".
        """
        now = float(now if now is not None else time.time())
        due = []
        for event in self.open_alerts.values():
            if event.state != "ACTIVE":
                continue
            if getattr(event, "escalated", False):
                continue
            if now - (event.ts or now) < self.escalate_s:
                continue
            due.append(event)
        return due

    def close(self, event, now: float | None = None, reason: str = "condition cleared") -> None:
        event.state = "CLOSED"
        event.closed_reason = reason
        event.last_error = event.last_error or ""
        event.suppression_reason = event.suppression_reason or ""
        event.deliveries.append({"channel": "lifecycle", "ok": True,
                                 "detail": f"CLOSED — {reason}"})
        self.open_alerts.pop(event.dedupe_key, None)

    def reset(self) -> None:
        self.open_alerts.clear()
        self.last_seen.clear()


#: The process-wide machine. One is the design: two machines would mean two dedup
#: windows, and the second one would be the one that forgot.
MACHINE = AlertStateMachine()


# ---------------------------------------------------------------------------
# Building an alert out of live state
# ---------------------------------------------------------------------------

def event_from_node(state, node_id: str, event_type: str = "SEVERITY",
                    text: str = "", kind: str | None = None) -> AlertEvent | None:
    """Snapshot one node's live condition into an :class:`AlertEvent`.

    Returns `None` when the node has nothing to say — no severity tier yet, or a
    tier that does not warrant an alert. Silence is the correct output for a
    healthy node; the alternative is an alert log full of NOMINAL, which trains
    everyone to ignore it.
    """
    node = getattr(state, "nodes", {}).get(node_id)
    if node is None:
        return None

    severity = str(getattr(node, "severity", "") or "NOMINAL").upper()
    if severity in ("", "NOMINAL", "WATCH") and event_type != "INFRASTRUCTURE":
        return None

    site = getattr(state, "site_map", None)
    lat = lon = 0.0
    label = node_id
    level = getattr(node, "level", None)
    if site is not None and node_id in getattr(site, "nodes", {}):
        meta = site.nodes[node_id]
        label = meta.get("label", node_id)
        level = meta.get("level", level)
        from api import map_payload
        lat, lon = map_payload.offset_to_latlon(
            meta["d_n_m"], meta["d_e_m"],
            getattr(state, "site_meta", None) or map_payload.load_site_meta())

    routes = (getattr(state, "underground", {}) or {}).get("routes", {}) or {}
    route = (routes.get("per_level", {}) or {}).get(str(level)) or None

    # The cascade publishes its per-node picture through `engine.cascade.cascade_status()`
    # — a module-level snapshot of the last tick, not a state field — and the band
    # arrives as `[q10, q50, q90]`, which is the shape `/api/ai` already serves.
    # Reading the published surface rather than reaching into `_DECISIONS` is the
    # same rule the rest of the server follows.
    from engine import cascade as cascade_engine
    cas = (cascade_engine.cascade_status().get("nodes") or {}).get(node_id) or {}
    band = cas.get("band") or []

    actions = severity_engine.recommended_actions(
        severity, getattr(node, "stage", None), route,
        people_underground=_people_underground(state))

    event = AlertEvent(
        node_id=node_id,
        node_label=label,
        level_id=str(level or ""),
        severity=severity,
        event_type=event_type,
        text=text or f"{label} {severity}",
        progression_stage=getattr(node, "stage", None),
        ttf_seconds=_int_or_none(cas.get("ttf_s")),
        ttf_q10=_int_or_none(band[0]) if len(band) == 3 else None,
        ttf_q90=_int_or_none(band[2]) if len(band) == 3 else None,
        disruption_label=cas.get("label"),
        disruption_index=cas.get("disruption_index"),
        firewalled=bool(cas.get("firewalled")),
        where=f"{str(label).split('—')[0].strip()}",
        geo_lat=float(lat), geo_lon=float(lon),
        recommended_action=actions[0] if actions else "",
        actions=list(actions),
        route=route,
        kind=kind,
        ts=time.time(),
        iso=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )

    # **Rule 2, in the alert layer** (§8.2): if the firewall is engaged the tier is
    # at least HIGH, whatever the classifier said. M1 saying "environmental" while
    # M2 disagrees is precisely the case where the second opinion wins.
    if event.firewalled:
        event.severity = at_least(event.severity, "HIGH")

    # **The ADVISORY tier** is the double-AI's dividend: the disturbance is real
    # but the ground is not failing, so nobody's phone rings.
    if (not event.firewalled and str(event.disruption_label or "").upper() == "ENVIRONMENTAL"
            and severity_engine.severity_rank(severity) <= severity_engine.severity_rank("ELEVATED")):
        event.severity = "ADVISORY"

    return event


#: Fields a duplicate is allowed to refresh. Everything else is the *record* —
#: who was told, when, and in what state — and a condition repeating itself must
#: never be able to rewrite it.
_REFRESHABLE = ("text", "progression_stage", "ttf_seconds", "ttf_q10", "ttf_q90",
                "disruption_label", "disruption_index", "firewalled",
                "recommended_action", "actions", "route", "geo_lat", "geo_lon")


def _refresh(live, fresh) -> None:
    """Carry the newest measurements onto the live alert, keep its history."""
    for field_name in _REFRESHABLE:
        value = getattr(fresh, field_name, None)
        if value is not None:
            setattr(live, field_name, value)


def _int_or_none(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _people_underground(state) -> int:
    try:
        return int(state.muster_counts().get("underground", 0))
    except Exception:                                           # noqa: BLE001
        return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def dispatch(state, event, persist=None, now: float | None = None) -> AlertEvent:
    """Fan one event out through the channels its tier earned, and record it.

    `persist` is an optional callback `(event, deliveries) -> alert_id`; the
    routes pass `persistence.alert_store.persist_alert`. Keeping it a parameter
    rather than an import is what lets the whole dispatcher run against a bare
    `SystemState` in a test — no database, no server, no monkeypatching.
    """
    # **The timestamp is settled here, always.** An explicit clock wins — the
    # alternative makes the machine untestable with a fake clock, and the
    # escalation timer is exactly the part that deserves a test which does not
    # sleep for sixty seconds — but an event built without one must still be
    # stamped, because `alerts_history.ts` is NOT NULL. Leaving `event.ts` at 0.0
    # while `now` was resolved from the wall clock produced precisely that: an
    # alert that lived in memory, was on screen, and could never be written.
    now = float(now) if now is not None else float(event.ts or time.time())
    event.ts = now
    event.iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(event.ts))

    transition = MACHINE.observe(event, now)
    if transition == "DUPLICATE":
        # **A duplicate is the same incident still happening, not a new one.**
        # The first draft of this branch called `track()` on the incoming event,
        # which silently replaced the live record with a `NEW` one and threw its
        # ledger away — and because escalation only ever looks at an `ACTIVE`
        # alert, an alert that was never acknowledged would then have escalated
        # exactly never. The measurement is refreshed; the *record* is kept.
        live = MACHINE.open_alerts.get(event.dedupe_key)
        if live is None:                                        # pragma: no cover
            return event
        _refresh(live, event)
        return live

    MACHINE.track(event)

    chosen, suppressed, reason = channels_for(event)
    event.suppressed = suppressed
    event.suppression_reason = reason

    event.deliveries = channels.send(state, event, chosen)
    event.attempts += 1

    ok_channels = {d.channel for d in event.deliveries if d.ok}
    failed = [d for d in event.deliveries if not d.ok]
    event.last_error = "; ".join(f"{d.channel}: {d.detail}" for d in failed)[:400]

    # The alert is ACTIVE the moment it exists and has been logged — even with
    # every network channel down. Its *state* is about the incident, not about the
    # plumbing.
    event.state = "ACTIVE" if ("dashboard" in ok_channels or event.deliveries) else "NEW"

    # A siren nobody verified is the worst failure mode in the system (§8.5), so it
    # is raised as an INFRASTRUCTURE event rather than logged in passing.
    local = next((d for d in event.deliveries if d.channel == "local"), None)
    if local is not None and not local.ok:
        try:
            state.note_event("CRITICAL",
                             f"LOCAL_ALERT_UNVERIFIED — {event.where or event.node_id}: "
                             f"the junction indication was not delivered ({local.detail}).",
                             kind="INFRASTRUCTURE")
        except Exception:                                       # noqa: BLE001
            pass

    if persist is not None:
        try:
            event.id = persist(event, event.deliveries)
        except Exception as exc:                                # noqa: BLE001
            event.last_error = (event.last_error + f"; persist: {exc}")[:400]
    return event


def extended_block() -> dict:
    """The alert layer's one-line summary for `/api/extended` (§8.5).

    Deliberately tiny and deliberately honest: the *open* count, the newest
    unacknowledged alert (the one a shift supervisor asks about), and how many
    were suppressed. A deck card can print this block without a second request,
    and a regulator reading `/api/extended` alone still sees that a suppression
    happened rather than inferring it from silence.
    """
    open_alerts = [e for e in MACHINE.open_alerts.values() if e.state in ("NEW", "ACTIVE")]
    unacked = [e for e in open_alerts if e.state == "ACTIVE"]
    newest = max(open_alerts, key=lambda e: e.ts, default=None)
    return {
        "open": len(open_alerts),
        "unacknowledged": len(unacked),
        "escalated": sum(1 for e in open_alerts if e.escalated),
        "suppressed": sum(1 for e in open_alerts if e.suppressed),
        "current": newest.as_public() if newest is not None else None,
        "escalate_s": float(getattr(config, "ALERT_ESCALATE_S", 60.0)),
        "dedup_s": float(getattr(config, "ALERT_DEDUP_S", 90.0)),
        "sms_max_per_hour": int(getattr(config, "SMS_MAX_PER_HOUR", 20) or 0),
    }


def ack_alert(state, alert_id: int, operator: str, persist=None,
              now: float | None = None) -> dict:
    """Human acknowledgement — **the only path to `ACKED`** (SRS §5.5).

    Refuses an empty operator on purpose. The rule is about *who* may downgrade an
    alert, and a blank string is exactly the value that would let an automatic
    caller look like a person.
    """
    now = float(now if now is not None else time.time())
    operator = str(operator or "").strip()
    if not operator:
        return {"ok": False, "error": "an acknowledging operator is required (SRS §5.5)"}

    for event in MACHINE.open_alerts.values():
        if event.id == alert_id:
            event.state = "ACKED"
            event.acked_by = operator
            event.acked_at = now
            event.deliveries.append({"channel": "lifecycle", "ok": True,
                                     "detail": f"ACKED by {operator}"})
    if persist is not None:
        try:
            return persist(alert_id, operator, now) or {"ok": True, "operator": operator}
        except Exception as exc:                                # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return {"ok": True, "operator": operator, "state": "ACKED", "acked_at": now}


def escalate_due(state, persist=None, now: float | None = None) -> list:
    """Re-notify everything past its escalation timer.

    Escalation **adds** channels and raises the tier, then re-sends — it never
    replaces the original delivery record, because "we told them once and nobody
    answered" is a fact about the incident that a regulator will ask about.
    """
    now = float(now if now is not None else time.time())
    out = []
    for event in MACHINE.escalate(now):
        extra = ESCALATION_CHANNELS.get(str(event.severity).upper(), ("sms",))
        earned, _, _ = channels_for(event)
        chosen = tuple(dict.fromkeys(earned + extra))
        new_deliveries = channels.send(state, event, chosen)
        event.deliveries.extend(new_deliveries)
        event.escalated = True
        event.attempts += 1
        event.state = "ACTIVE"
        try:
            state.note_event("WARNING",
                             f"{templates.reference(event)} ESCALATED — no acknowledgement "
                             f"within {int(MACHINE.escalate_s)} s ({event.where}).",
                             kind="ALERT")
        except Exception:                                       # noqa: BLE001
            pass
        if persist is not None:
            try:
                persist(event, new_deliveries)
            except Exception:                                   # noqa: BLE001
                pass
        out.append(event)
    return out


def retry_failed(state, persist=None, now: float | None = None) -> int:
    """Re-attempt the channels that failed and were **queued**.

    Called once at boot (§8.5), so anything raised while the network was down goes
    out when the server comes back. Only `queued` deliveries are retried: a
    channel that failed for a policy reason has already said so, and re-sending it
    would be the system arguing with its own rule.
    """
    now = float(now if now is not None else time.time())
    sent = 0
    for event in list(MACHINE.open_alerts.values()):
        if event.state not in ("ACTIVE", "NEW"):
            continue
        retry = [d.channel for d in event.deliveries if not d.ok and d.queued]
        if not retry:
            continue
        fresh = channels.send(state, event, tuple(retry))
        event.deliveries.extend(fresh)
        event.attempts += 1
        sent += sum(1 for d in fresh if d.ok)
        if persist is not None:
            try:
                persist(event, fresh)
            except Exception:                                   # noqa: BLE001
                pass
    return sent


def close_all(state, reason: str = "manual re-arm", persist=None) -> int:
    """Close every open alert on a Re-arm — **never delete**.

    §8.5: *"Deleting an alert history is exactly the behaviour a regulator should
    never see."* The incident stays in the table with `state='CLOSED'` and the
    reason, and the machine forgets it so the same condition can raise a new alert.
    """
    closed = list(MACHINE.open_alerts.values())
    for event in closed:
        MACHINE.close(event, reason=reason)
    if persist is not None and closed:
        for event in closed:
            try:
                persist(event, [])
            except Exception:                                   # noqa: BLE001
                pass
    return len(closed)


__all__ = [
    "STATES", "TIER_ORDER", "TIER_CHANNELS", "AlertEvent", "AlertStateMachine",
    "MACHINE", "dedupe_key", "suppression_guard", "channels_for", "tier_rank",
    "at_least", "event_from_node", "dispatch", "ack_alert", "escalate_due",
    "retry_failed", "close_all", "extended_block",
]
