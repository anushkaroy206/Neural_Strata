"""Phase 8 (§8.7) — the alert layer, tested on its own promises.

Six of these tests exist because the *sentence* they pin is a safety claim rather
than a behaviour:

  · **one alert per incident** — twenty observations, one message, which is the
    difference between an alerting system and a phone turned off at 3 a.m.
  · **escalation is on the clock** — an unacknowledged alert adds a channel and
    says so, and an acknowledged one stops, because "we told them once and nobody
    answered" is the fact a post-mortem needs.
  · **CRITICAL is never auto-suppressed** — SRS §5.5, tested by *trying* to
    suppress it three different ways.
  · **the firewall floors the tier at HIGH** — Rule 2 of §8.2, in the alert layer
    where a second author of severity could otherwise undo it.
  · **the SMS says where** — 160 characters is a *format*, not a target: a
    location-free warning is a phone call, not a notification.
  · **an unacknowledged alert survives a restart of the process's memory** — the
    delivery ledger is written per attempt, so a queued channel is visibly queued.

Everything runs against a real `SystemState` wired to the real tunnel map, with a
fake clock and a fake down-link. No database is required for any of it: the alert
*policy* and the alert *store* are separable by design, and this file is what
keeps them that way.
"""

from __future__ import annotations

from pathlib import Path

import json

import pytest

import config
from api import map_payload
from alerts import channels, dispatcher, templates
from contracts import NodeState
from persistence import db
from routing import dijkstra as R
from state import SystemState

SITE = R.load_tunnel_map(Path(config.ASSET_FILES["tunnel_map"]))
T0 = 1_770_000_000.0        # a fixed wall clock: these tests never sleep


@pytest.fixture(autouse=True)
def clean_machine(monkeypatch):
    """Every test gets a fresh machine, a fresh SMS window and no down-link."""
    dispatcher.MACHINE.reset()
    channels.reset_rate_window()
    monkeypatch.setattr(R, "_DOWNLINK", None)
    monkeypatch.setattr(config, "ALERT_SMS_TO", "")
    monkeypatch.setattr(config, "SMTP_HOST", "")
    monkeypatch.setattr(config, "ALERT_EMAIL_TO", "")
    yield
    dispatcher.MACHINE.reset()
    channels.reset_rate_window()
    R._DOWNLINK = None


def make_state() -> SystemState:
    state = SystemState()
    state.site_map = SITE
    state.site_meta = map_payload.load_site_meta()
    return state


def put(state, node_id, *, severity="CRITICAL", stage="TERTIARY", status="CRITICAL",
        disp=120.0, level=1):
    node = state.nodes.get(node_id) or NodeState(node_id=node_id)
    node.severity = severity
    node.stage = stage
    node.status = status
    node.displacement_mm = disp
    node.level = level
    state.nodes[node_id] = node
    return node


def event_for(state, node_id="node_2", **kw) -> dispatcher.AlertEvent:
    put(state, node_id, **{k: v for k, v in kw.items()
                           if k in ("severity", "stage", "status", "disp", "level")})
    event = dispatcher.event_from_node(state, node_id, event_type="SEVERITY")
    assert event is not None, "the fixture node should have produced an alert"
    return event


def stub_send(monkeypatch, ok_channels=("local", "dashboard", "sms", "email")):
    """Replace the real channels with a recorder, so tests assert on *policy*."""
    sent: list[tuple[str, str]] = []

    def fake_send(state, event, chosen):
        out = []
        for name in channels.CHANNELS:
            if name in chosen:
                ok = name in ok_channels
                sent.append((name, event.dedupe_key))
                out.append(channels.Delivery(name, ok, "stub", queued=not ok))
        return out

    monkeypatch.setattr(channels, "send", fake_send)
    return sent


# ---------------------------------------------------------------------------
# dedup — one incident, one message
# ---------------------------------------------------------------------------

def test_twenty_observations_produce_one_alert():
    """The ticker runs at 2 Hz; a condition that persists must not become 2 SMS/s."""
    state = make_state()
    persisted = []
    for i in range(20):
        event = event_for(state, "node_2")
        dispatcher.dispatch(state, event, persist=lambda e, d: persisted.append(e) or 1,
                            now=T0 + i * 0.5)
    assert len(persisted) == 1, "dedup must collapse a repeating condition to one alert"
    assert len(dispatcher.MACHINE.open_alerts) == 1


def test_a_duplicate_refreshes_the_incident_and_keeps_it_active(monkeypatch):
    """**Regression.** The first draft replaced the live record on every tick.

    That discarded the ledger, reset the state to NEW, and — because escalation
    only looks at ACTIVE alerts — meant an unacknowledged alert would escalate
    exactly never. A duplicate is the same incident still happening.
    """
    state = make_state()
    first = event_for(state, "node_2", severity="HIGH", stage="SECONDARY")
    live = dispatcher.dispatch(state, first, persist=None, now=T0)
    assert live.state == "ACTIVE"

    put(state, "node_2", severity="HIGH", stage="TERTIARY", disp=140.0)
    again = dispatcher.event_from_node(state, "node_2", event_type="SEVERITY")
    second = dispatcher.dispatch(state, again, persist=None, now=T0 + 5)

    assert second is live, "the live incident is refreshed, not replaced"
    assert live.state == "ACTIVE", "a repeat must not demote the alert to NEW"
    assert live.progression_stage == "TERTIARY", \
        "the newest stage must reach the live record"
    assert live.deliveries, "the delivery ledger must survive a repeat"


def test_a_new_tier_is_a_new_alert():
    """ELEVATED → CRITICAL is a new fact, not a repeat, and must get through."""
    state = make_state()
    put(state, "node_2", severity="ELEVATED", stage="PRIMARY")
    dispatcher.dispatch(state, dispatcher.event_from_node(state, "node_2"), now=T0)
    put(state, "node_2", severity="CRITICAL", stage="TERTIARY")
    dispatcher.dispatch(state, dispatcher.event_from_node(state, "node_2"), now=T0 + 1)
    assert len(dispatcher.MACHINE.open_alerts) == 2


def test_nominal_nodes_raise_nothing():
    """Silence is a feature: an alert log full of NOMINAL trains people to ignore it."""
    state = make_state()
    put(state, "node_2", severity="NOMINAL", stage="STABLE", status="NOMINAL", disp=4.0)
    assert dispatcher.event_from_node(state, "node_2") is None


# ---------------------------------------------------------------------------
# escalation — on the clock, and only for an unacknowledged alert
# ---------------------------------------------------------------------------

def test_escalation_fires_after_the_timer_and_adds_a_channel(monkeypatch):
    state = make_state()
    sent = stub_send(monkeypatch)
    event = event_for(state, "node_2", severity="ELEVATED", stage="PRIMARY")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    baseline = len(sent)

    assert dispatcher.escalate_due(state, now=T0 + 5) == [], "not yet due"
    due = dispatcher.escalate_due(state, now=T0 + config.ALERT_ESCALATE_S + 1)
    assert len(due) == 1, "an unacknowledged alert past the timer must re-notify"
    assert due[0].escalated is True
    assert len(sent) > baseline, "escalation adds channels"
    assert ("sms", event.dedupe_key) in sent, "ELEVATED escalates to SMS"


def test_escalation_does_not_fire_twice():
    state = make_state()
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    assert len(dispatcher.escalate_due(state, now=T0 + 999)) == 1
    assert dispatcher.escalate_due(state, now=T0 + 1999) == [], "escalate once per alert"


def test_acknowledgement_stops_escalation():
    state = make_state()
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    dispatcher.ack_alert(state, event.id, "Safety Officer",
                         persist=lambda *a: {"ok": True, "state": "ACKED",
                                             "acked_at": T0 + 10}, now=T0 + 10)
    assert event.state == "ACKED"
    assert dispatcher.escalate_due(state, now=T0 + 9999) == []


def test_acknowledgement_requires_a_named_operator():
    """SRS §5.5 — an empty string is exactly how an automatic caller would look
    like a person."""
    state = make_state()
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    for blank in ("", "   ", None):
        res = dispatcher.ack_alert(state, event.id, blank, persist=None, now=T0 + 1)
        assert res["ok"] is False
        assert "operator" in res["error"]
    assert event.state == "ACTIVE", "a refused ack must leave the alert open"


# ---------------------------------------------------------------------------
# suppression — never silent, never for CRITICAL
# ---------------------------------------------------------------------------

def test_critical_is_never_suppressed_by_any_rule():
    """SRS §5.5, tried three ways."""
    critical = dispatcher.AlertEvent(severity="CRITICAL", node_id="node_2",
                                     firewalled=True, disruption_label="STRUCTURAL")
    for rule in ("auto", "ratelimit", "quiet_hours"):
        suppress, reason = dispatcher.suppression_guard(critical, rule)
        assert suppress is False, f"CRITICAL was suppressed by {rule!r}"
        assert reason == ""
    chosen, suppressed, reason = dispatcher.channels_for(critical)
    assert suppressed is False
    assert "sms" in chosen and "email" in chosen


def test_advisory_is_quiet_but_never_invisible():
    """The double-AI payoff: no SMS, and a row that *says why* it stayed quiet."""
    state = make_state()
    event = event_for(state, "node_2", severity="ELEVATED", stage="PRIMARY")
    event.disruption_label = "ENVIRONMENTAL"
    event.firewalled = False
    event = dispatcher.event_from_node(state, "node_2") or event
    event.severity = "ADVISORY"

    chosen, suppressed, reason = dispatcher.channels_for(event)
    assert suppressed is True
    assert "ADVISORY" in reason and "no SMS" in reason
    assert "sms" not in chosen and "email" not in chosen
    assert "dashboard" in chosen, "a suppression that cannot be seen is a lie"
    # Phase 9 corrected this expectation, and the correction is the point of the
    # ADVISORY tier. §8.2 is explicit — "ADVISORY: no SMS, no email, **LEDs
    # breathe amber**" — so the lamps are not part of what gets suppressed here;
    # only the *messages* are. What must not happen is a siren, and that is a
    # different channel decision: `notify_local` renders ADVISORY as
    # `advisory_amber` with `{"cmd":"advisory","on":true}` and `{"cmd":"siren",
    # "on":false}`, so an amber breather can never sound an evacuation.
    assert "local" in chosen, "the amber breathe *is* the ADVISORY tier"
    assert "sms" not in chosen and "email" not in chosen


def test_watch_and_elevated_stay_on_site():
    for tier in ("WATCH", "ELEVATED"):
        chosen, suppressed, _ = dispatcher.channels_for(dispatcher.AlertEvent(severity=tier))
        assert suppressed is True
        assert "sms" not in chosen and "email" not in chosen
    chosen, suppressed, _ = dispatcher.channels_for(dispatcher.AlertEvent(severity="HIGH"))
    assert suppressed is False and "sms" in chosen and "email" not in chosen


def test_high_firewall_floors_the_tier_in_the_alert_layer(monkeypatch):
    """Rule 2 (§8.2): an environmental label with the firewall engaged is at
    least HIGH — M1 saying "it is only rain" is exactly when M2's disagreement
    must win."""
    state = make_state()
    put(state, "node_2", severity="ELEVATED", stage="PRIMARY")
    # The cascade's *public* surface is monkeypatched, not its private decision
    # dict: a test that writes into `_DECISIONS` leaves a broken object behind for
    # every test that runs after it, and that is a defect in the test, not a
    # finding about the code.
    import engine.cascade as cascade_engine
    monkeypatch.setattr(cascade_engine, "cascade_status", lambda: {
        "nodes": {"node_2": {"label": "ENVIRONMENTAL", "tier": "ELEVATED",
                             "disruption_index": 0.9, "structural_prob": 0.8,
                             "ttf_s": None, "band": None, "m2_hz": 1.0,
                             "disagreement": True, "firewalled": True}}})
    event = dispatcher.event_from_node(state, "node_2")
    assert event is not None
    assert dispatcher.tier_rank(event.severity) <= dispatcher.tier_rank("HIGH"), \
        "a firewalled node must never be below HIGH"
    assert event.firewalled is True
    chosen, suppressed, _ = dispatcher.channels_for(event)
    assert suppressed is False and "sms" in chosen


# ---------------------------------------------------------------------------
# the message itself
# ---------------------------------------------------------------------------

def test_sms_is_within_one_message_and_carries_the_coordinates():
    """A split SMS arrives out of order; a location-free SMS is a phone call."""
    event = dispatcher.AlertEvent(
        node_id="node_2", node_label="N2 — Tunnel A · collapse face", level_id="1",
        severity="CRITICAL", event_type="SEVERITY", text="x", ttf_seconds=47,
        ttf_q10=31, ttf_q90=88, progression_stage="TERTIARY",
        geo_lat=23.64321, geo_lon=85.31804, disruption_label="STRUCTURAL",
        actions=["EVACUATE Level 1 via Exit 2. Do not use Route B."])
    body = templates.sms_body(event)
    assert len(body) <= templates.SMS_MAX_LEN
    assert "23.64321,85.31804" in body, "the coordinates must always survive"
    assert "CRITICAL" in body
    assert "N2 Tunnel A" in body
    assert "31-88" in body, "the TTF interval is printed beside the point estimate"


def test_the_reference_is_stable_across_a_retry():
    """A message whose identity changes on retry cannot be matched to its row."""
    event = dispatcher.AlertEvent(node_id="node_1", severity="HIGH",
                                  event_type="SEVERITY", ts=T0)
    first = templates.reference(event)
    event.attempts, event.last_error = 7, "SMTP timeout"
    assert templates.reference(event) == first


def test_email_carries_actions_classification_and_a_georeferenced_link():
    event = dispatcher.AlertEvent(
        node_id="node_2", node_label="N2 — Tunnel A · collapse face", level_id="1",
        severity="CRITICAL", event_type="SEVERITY", iso="2026-09-23T05:00:00Z",
        ttf_seconds=47, ttf_q10=31, ttf_q90=88, progression_stage="TERTIARY",
        disruption_label="STRUCTURAL", disruption_index=0.71,
        geo_lat=23.64321, geo_lon=85.31804,
        actions=["EVACUATE Level 1 via Exit 2.", "Account for all personnel at muster."],
        route={"path": ["node_2", "J2", "EXIT2"], "exit": "EXIT2", "base_m": 145.1,
               "eta_s": 121})
    body = templates.email_body(event, base="http://10.0.0.5:5068")
    assert "EVACUATE Level 1 via Exit 2." in body
    assert "STRUCTURAL" in body and "0.71" in body
    assert "node_2 → J2 → EXIT2" in body
    assert "http://10.0.0.5:5068/#gis-map?alert=NS-" in body
    assert "lat=23.64321" in body and "lon=85.31804" in body
    assert templates.email_subject(event).startswith("[CRITICAL]")


# ---------------------------------------------------------------------------
# offline queue, rate cap, and the boot retry
# ---------------------------------------------------------------------------

def test_offline_dispatch_queues_and_the_boot_retry_delivers(monkeypatch):
    """The rig with no 4G: the SMS is *queued*, visibly, and goes out when the
    modem comes back. A silent drop is the failure mode this pins."""
    state = make_state()
    monkeypatch.setattr(config, "ALERT_SMS_TO", "+919000000000")
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)

    sms = next(d for d in event.deliveries if d.channel == "sms")
    assert sms.ok is False and sms.queued is True, "no hub ⇒ queued, not dropped"
    assert event.state == "ACTIVE", "the incident is active even with every channel down"

    sent_lines: list[str] = []
    R.attach_downlink(sent_lines.append)
    delivered = dispatcher.retry_failed(state, persist=None, now=T0 + 30)
    assert delivered >= 1, "the boot retry must deliver what the outage queued"
    assert any("#CMD" in line and '"cmd":"sms"' in line.replace(" ", "")
               for line in sent_lines), "the modem frame is a #CMD line"
    retried = {d.channel: d for d in event.deliveries if d.ok}
    assert "sms" in retried, "the queued SMS must be the one the retry delivers"


def test_a_red_warning_texts_every_configured_number(monkeypatch):
    """HIGH and CRITICAL are the red strips. Each local number gets one text,
    and nothing is handed to a modem the bench does not have."""
    state = make_state()
    monkeypatch.setattr(config, "SMS_TRANSPORT", "messages")
    monkeypatch.setattr(config, "ALERT_SMS_TO", "+911111111111, +912222222222")
    channels._SMS_TIMES.clear()
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(channels, "_messages_send",
                        lambda number, body: sent.append((number, body)) or (True, "sent"))
    event = event_for(state, "node_2", severity="CRITICAL")
    result = channels.notify_sms(state, event)
    assert result.ok is True
    assert [n for n, _ in sent] == ["+911111111111", "+912222222222"]
    assert "RED WARNING" in sent[0][1]
    assert "Jharia panel" in sent[0][1]
    assert dispatcher.TIER_CHANNELS["HIGH"] and "sms" in dispatcher.TIER_CHANNELS["HIGH"]
    assert "sms" in dispatcher.TIER_CHANNELS["CRITICAL"]
    channels._SMS_TIMES.clear()


def test_sms_rate_cap_refuses_loudly(monkeypatch):
    """§8.8 — `SMS_MAX_PER_HOUR` with a RATE_LIMITED line, never a silent drop."""
    state = make_state()
    monkeypatch.setattr(config, "SMS_MAX_PER_HOUR", 2)
    monkeypatch.setattr(config, "ALERT_SMS_TO", "+919000000000")
    monkeypatch.setattr(R, "_DOWNLINK", lambda line: None)

    # The sliding window is compared against the wall clock, so it is seeded with
    # real timestamps — a fake clock is right for the *machine* and wrong for a
    # rate limiter that exists to protect a human's phone.
    import time as _time
    for i in range(3):
        channels._SMS_TIMES.append(_time.time() - i)
    event = event_for(state, "node_2")
    result = channels.notify_sms(state, event)
    assert result.ok is False
    assert "RATE_LIMITED" in result.detail
    assert result.queued is False, "a refused send is not a queued one"


def test_local_failure_is_raised_as_an_infrastructure_event(monkeypatch):
    """A siren that did not sound is the loudest failure the system has, so it is
    not a log line — it is an INFRASTRUCTURE event (§8.5)."""
    state = make_state()
    seen: list[tuple] = []
    monkeypatch.setattr(state, "note_event",
                        lambda level, text, kind=None: seen.append((level, text, kind)), raising=False)
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    infra = [s for s in seen if s[2] == "INFRASTRUCTURE"]
    assert infra, "a failed local channel must be visible as an infrastructure fault"
    assert "LOCAL_ALERT_UNVERIFIED" in infra[0][1]
    assert infra[0][0] == "CRITICAL"


# ---------------------------------------------------------------------------
# persistence — the row, the ledger, and the close
# ---------------------------------------------------------------------------

def test_ack_persists_through_the_store_hook():
    """The route's `persist` callback is what writes ACKED; the policy object must
    not open its own database connection."""
    state = make_state()
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    calls: list[tuple] = []

    def fake_persist(alert_id, operator, now):
        calls.append((alert_id, operator, now))
        return {"ok": True, "state": "ACKED", "operator": operator, "acked_at": now}

    res = dispatcher.ack_alert(state, event.id, "Control Room",
                               persist=fake_persist, now=T0 + 3)
    assert res["ok"] is True and res["state"] == "ACKED"
    assert calls == [(event.id, "Control Room", T0 + 3)]
    assert event.acked_by == "Control Room" and event.acked_at == T0 + 3


def test_close_never_deletes_and_forgets_only_after_closing():
    """§8.5 — Re-arm closes alerts; it does not delete history. The machine then
    forgets them, which is how the same condition can raise a *new* alert."""
    state = make_state()
    persisted: list = []
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=lambda e, d: persisted.append(e) or 42,
                        now=T0)
    assert event.id == 42

    closed = dispatcher.close_all(state, reason="manual re-arm", persist=None)
    assert closed == 1
    assert event.state == "CLOSED"
    assert "manual re-arm" in event.deliveries[-1]["detail"]
    # The reason has to reach the *row*, not only the in-memory note: the first
    # live re-arm wrote CLOSED rows whose `closed_reason` was NULL, which made a
    # machine close and an operator re-arm indistinguishable in the database.
    assert event.closed_reason == "manual re-arm"
    assert db._alert_params(event, [])["closed_reason"] == "manual re-arm"
    assert dispatcher.MACHINE.open_alerts == {}, "closed alerts are no longer open"
    assert persisted, "the close is persisted, never a delete"


def test_the_database_states_match_the_machine():
    """The words the machine and the schema must agree on, in one place."""
    assert set(dispatcher.STATES) == {"NEW", "ACTIVE", "ACKED", "CLOSED"}
    assert "CLOSED" in dispatcher.STATES
    assert dispatcher.tier_rank("CRITICAL") == 0 and dispatcher.tier_rank("WATCH") > 0
    assert dispatcher.at_least("WATCH", "HIGH") == "HIGH"
    assert dispatcher.at_least("CRITICAL", "HIGH") == "CRITICAL"
    assert dispatcher.at_least("nonsense-tier", "HIGH") == "HIGH"


def test_a_dispatched_event_is_always_timestamped_and_identifiable():
    """**Regression, two defects at once.**

    An event built without a clock — the route-change alerts do exactly that —
    reached `insert_alert` with `ts = None`, and `alerts_history.ts` is NOT NULL.
    The alert lived in memory, was on screen, and could never be written; the
    console showed it with no id. So: every dispatched event leaves with a
    timestamp, and the store's parameter builder cannot produce a NULL one.
    """
    state = make_state()
    bare = dispatcher.AlertEvent(node_id="site", severity="CRITICAL",
                                 event_type="EVENT", text="route change")
    seen: list = []
    dispatcher.dispatch(state, bare, persist=lambda e, d: seen.append(e.ts) or 7, now=None)
    assert bare.ts > 0, "a dispatched event must carry a real timestamp"
    assert bare.iso.endswith("Z")
    assert seen and seen[0] > 0

    params = db._alert_params(bare, [])
    assert params["ts"] is not None, "the store must never be handed a NULL ts"
    assert db._alert_params(dispatcher.AlertEvent(node_id="node_1"), [])["ts"] is not None


def test_live_alerts_publish_the_same_keys_as_stored_ones():
    """The console merges both lists; a live row missing the delivery flags its
    neighbour has would make the ledger column flicker."""
    state = make_state()
    event = event_for(state, "node_2")
    dispatcher.dispatch(state, event, persist=None, now=T0)
    public = event.as_public()
    for key in ("id", "ts", "severity", "state", "local_dispatched",
                "dash_dispatched", "sms_dispatched", "email_dispatched",
                "suppress_reason", "dedupe_key", "reference", "ttf_seconds",
                "geo_lat", "geo_lon", "attempts", "text", "node_id", "event_type"):
        assert key in public, f"live alert rows must publish {key} too"
    assert public["dash_dispatched"] is True


def test_alert_store_degrades_without_a_database(monkeypatch):
    """`insert_alert` returns None rather than raising — a database outage must
    not be able to stop the alert from being raised in memory."""
    monkeypatch.setattr(db, "try_get_conn", lambda: None)
    assert db.insert_alert(dispatcher.AlertEvent(node_id="node_1")) is None
    assert db.persist_alert(dispatcher.AlertEvent(node_id="node_1")) is None
    assert db.fetch_alerts() == []
    assert db.close_open_alerts("test") == 0


# ---------------------------------------------------------------------------
# the down-link — the server end of §7.6, and the row's `pattern` command
# ---------------------------------------------------------------------------

def _attach(monkeypatch, sink):
    """A fake hub line. Returns the list every `#CMD` frame lands in."""
    monkeypatch.setattr(R, "_DOWNLINK", lambda line: sink.append(line))
    return sink


def test_the_downlink_vocabulary_is_the_specs_own():
    """Four commands, and the bodies are quoted from §H6.6 rather than invented.

    `siren` and `advisory` used to leave the server as
    `{"cmd":"led","junction":"SIREN",…}` — a shape no hub in this project parses.
    The firmware refused it by name, so every CRITICAL alert reported the local
    channel as failed and raised LOCAL_ALERT_UNVERIFIED against a healthy cable.
    """
    led = R.HubCommand(hub="L1", junction="J2", pattern="chase_red").to_dict()
    assert led["cmd"] == "led" and led["junction"] == "J2" and led["colour"] == "red"
    assert {"pattern", "colour", "period_ms", "buzzer", "reason"} <= set(led)

    assert R.HubCommand(hub="L2", cmd="siren", on=True).to_dict() == \
        {"cmd": "siren", "level": 2, "on": True}
    assert R.HubCommand(hub="L1", cmd="advisory", on=False).to_dict() == \
        {"cmd": "advisory", "level": 1, "on": False}

    # `pattern` is the level-wide form: no junction, and the level still resolves.
    pat = R.HubCommand(hub="L1", junction="J4", cmd="pattern", pattern="flow_green").to_dict()
    assert pat["cmd"] == "pattern" and pat["pattern"] == "flow_green"
    assert R.HubCommand(hub="L1", junction="J4").level_of() == 2
    assert R.HubCommand(hub="L2", cmd="siren").level_of() == 2


def test_local_alert_sends_a_real_siren_command_and_never_a_fake_junction(monkeypatch):
    state = make_state()
    sent = _attach(monkeypatch, [])
    alert = event_for(state, "node_2", severity="CRITICAL")
    delivery = channels.notify_local(state, alert)

    assert delivery.ok, delivery.detail
    assert any('"cmd":"siren"' in line and '"on":true' in line for line in sent)
    assert any('"cmd":"advisory"' in line and '"on":false' in line for line in sent)
    assert not any("SIREN" in line for line in sent), \
        "the siren is its own command, not an LED pattern on a junction that does not exist"


def test_a_warning_paints_its_pair_and_beeps(monkeypatch):
    """Node 3 warning lights J2 and J4 amber and beeps. It must not siren,
    and J1 and J3 stay green."""
    state = make_state()
    sent = _attach(monkeypatch, [])
    channels.notify_local(state, event_for(state, "node_3", severity="HIGH", status="WARNING"))
    lamps = {}
    for line in sent:
        if '"cmd":"led"' not in line and '"cmd":"pattern"' not in line:
            continue
        body = json.loads(line.split(" ", 1)[1])
        if body.get("junction"):
            lamps[body["junction"]] = body["pattern"]
    assert lamps["J1"] == "flow_green" and lamps["J3"] == "flow_green"
    assert lamps["J2"] == "advisory_amber" and lamps["J4"] == "advisory_amber"
    assert any('"cmd":"advisory"' in line and '"on":true' in line for line in sent)
    assert any('"cmd":"siren"' in line and '"on":false' in line for line in sent)
    assert not any('"cmd":"siren"' in line and '"on":true' in line for line in sent)


def test_led_tick_pushes_the_plan_and_the_inbox_records_it(monkeypatch):
    """§7.6's server end. Before this, the only thing that ever pushed a `#CMD`
    was an alert: no `flow_green` on an escape route, no `chase_red` on a blocked
    leg, no `hold_amber` at a junction with nowhere to send anyone."""
    import app as app_module

    state = make_state()
    put(state, "node_1", severity="CRITICAL", level=1)
    state.underground["routes"] = R.site_routes(state.site_map, state)
    monkeypatch.setattr(app_module, "STATE", state)
    monkeypatch.setattr(app_module, "_LED_LAST", {})
    monkeypatch.setattr(app_module, "_LED_STATE",
                        {"last_push": 0.0, "changes": 0, "reasserts": 0, "refused": 0})
    R.reset_inbox()
    sent = _attach(monkeypatch, [])

    app_module.led_tick(T0)

    junctions = {e["key"] for e in R.junction_inbox()}
    assert {"J1", "J2", "J3", "J4"} <= junctions, junctions
    assert sent and all(line.startswith("#CMD ") for line in sent)
    assert all(e["delivered"] for e in R.junction_inbox())
    # Node 1 or 2 lights J1 and J3. Node 3 or 4 lights J2 and J4.
    by_key = {e["key"]: e["pattern"] for e in R.junction_inbox()}
    assert by_key.get("J1") == "strobe_red" and by_key.get("J3") == "strobe_red", by_key
    assert by_key.get("J2") == "flow_green" and by_key.get("J4") == "flow_green", by_key
    assert any('"cmd":"siren"' in line and '"on":true' in line for line in sent)
    assert any('"cmd":"advisory"' in line and '"on":false' in line for line in sent)
    assert not any('"cmd":"advisory"' in line and '"on":true' in line for line in sent)
    assert app_module._LED_STATE["changes"] == 1


def test_an_unchanged_plan_is_reasserted_but_not_resent_every_tick(monkeypatch):
    """The re-assert is what makes the hub's silence rule safe to state.

    Commands go out on **change**, so silence is the normal state of a quiet mine.
    Without a pulse, §H6.6's "commands have stopped arriving" would fire on every
    steady green and walk the whole site down to amber on its own.
    """
    import app as app_module

    state = make_state()
    monkeypatch.setattr(app_module, "STATE", state)
    monkeypatch.setattr(app_module, "_LED_LAST", {})
    monkeypatch.setattr(app_module, "_LED_STATE",
                        {"last_push": 0.0, "changes": 0, "reasserts": 0, "refused": 0})
    R.reset_inbox()
    sent = _attach(monkeypatch, [])

    app_module.led_tick(T0)
    first = len(sent)
    assert first > 0
    # Quiet sends the green lamps and both sound-off lines in this same call.
    # The gate must not stay on the previous state until a later tick.
    assert any('"cmd":"advisory"' in line and '"on":false' in line for line in sent)
    assert any('"cmd":"siren"' in line and '"on":false' in line for line in sent)
    app_module.led_tick(T0 + 5)
    assert len(sent) == first, "an unchanged plan must not be re-sent a few seconds later"
    app_module.led_tick(T0 + 6)
    assert len(sent) == first, "an unchanged plan must not be re-sent every tick"
    app_module.led_tick(T0 + float(config.HUB_REASSERT_S) + 1)
    assert len(sent) > first, "past HUB_REASSERT_S the plan is re-asserted"
    assert app_module._LED_STATE["reasserts"] == 1
    assert float(config.HUB_SILENCE_S) > float(config.HUB_REASSERT_S), \
        "the hub's patience must outlast the server's pulse, or every quiet mine decays to amber"


def test_a_refused_push_is_recorded_rather_than_hidden(monkeypatch):
    """No hub on the line: the inbox says so, per junction, instead of the panel
    having to infer it from a failed alert delivery."""
    import app as app_module

    state = make_state()
    monkeypatch.setattr(app_module, "STATE", state)
    monkeypatch.setattr(app_module, "_LED_LAST", {})
    monkeypatch.setattr(app_module, "_LED_STATE",
                        {"last_push": 0.0, "changes": 0, "reasserts": 0, "refused": 0})
    R.reset_inbox()
    monkeypatch.setattr(R, "_DOWNLINK", None)

    app_module.led_tick(T0)

    inbox = R.junction_inbox()
    assert inbox and not any(e["delivered"] for e in inbox)
    assert all("no hub attached" in e["detail"] for e in inbox)
    assert R.inbox_stats()["refused"] > 0
    assert app_module._LED_STATE["refused"] > 0


def test_the_inbox_is_published_on_both_alert_endpoints(monkeypatch):
    """One poll, not two: the panel reads the lamps and the messages together."""
    import app as app_module

    state = make_state()
    monkeypatch.setattr(app_module, "STATE", state)
    R.reset_inbox()
    _attach(monkeypatch, [])
    app_module.led_tick(T0)

    from api import routes_alerts
    routes_alerts.bind(state)
    client = app_module.app.test_client()
    body = client.get("/api/alerts?limit=5").get_json()
    assert "downlink" in body and "junction_inbox" in body["downlink"]
    assert body["downlink"]["junction_inbox"], "the lamps must be on the same poll"

    extended = client.get("/api/extended").get_json()
    assert "junction_inbox" in extended["alerts"]
    assert extended["alerts"]["silence_s"] == float(config.HUB_SILENCE_S)


# ---------------------------------------------------------------------------
# the ticker — lifetime, not policy
# ---------------------------------------------------------------------------

def test_alert_tick_closes_when_the_condition_clears(monkeypatch):
    """§8.5 — a condition that clears **closes** its alert, with the reason kept.

    The close lives here rather than in the machine because the alert layer owns
    the incident's lifetime: a node that crosses back under a threshold must not
    have to know an alert was ever raised about it. And the reason is written,
    because "closed" and "closed because the ground stopped moving" are different
    sentences in a post-mortem.
    """
    import app as app_module

    state = make_state()
    monkeypatch.setattr(app_module, "STATE", state)
    monkeypatch.setattr(app_module, "_ALERT_CLEAR_SINCE", {})
    saved = []
    monkeypatch.setattr(app_module.db, "persist_alert",
                        lambda e, ds=(): saved.append(e) or 1)

    put(state, "node_2", severity="HIGH", disp=90.0)
    app_module.alert_tick(T0)
    assert dispatcher.MACHINE.open_alerts, "the condition raised an alert"

    put(state, "node_2", severity="NOMINAL", status="NOMINAL", disp=1.0)
    app_module.alert_tick(T0 + 1)
    assert dispatcher.MACHINE.open_alerts, \
        "one quiet second is a sensor, not a recovery — the settle timer holds it"

    app_module.alert_tick(T0 + 1 + float(config.ALERT_SETTLE_S) + 1)
    assert not dispatcher.MACHINE.open_alerts, "settled: the incident is closed"
    assert saved and saved[-1].state == "CLOSED"
    assert saved[-1].closed_reason == "condition cleared"


# ---------------------------------------------------------------------------
# the event funnel (Phase 9, B6)
# ---------------------------------------------------------------------------

def test_an_infrastructure_event_is_never_louder_than_elevated():
    """A dark sensor is a cable, not a hazard. It must not text anyone.

    This is the assertion that would have caught the defect Phase 9 found: the
    clamp in `app.alert_raise` compared `tier_rank(...) > tier_rank("ELEVATED")`,
    and `tier_rank` counts **0 = loudest** — so the comparison never fired and
    every infrastructure event inherited HIGH, which is the tier that sends an SMS.
    A ring segment fault is the test case because that is how it was found: the
    alert was printed, and it said HIGH.
    """
    import app

    app.STATE.note_event("WARNING", "Ring segment L1-L2 degraded — L2→L1.",
                         kind="INFRASTRUCTURE")
    alerts = [e for e in dispatcher.MACHINE.open_alerts.values()
              if e.kind == "INFRASTRUCTURE"]
    assert alerts, "an infrastructure warning must reach the alert layer at all"
    event = alerts[-1]
    assert event.severity == "ELEVATED", event.severity
    chosen, _suppressed, _reason = dispatcher.channels_for(event)
    assert "sms" not in chosen and "email" not in chosen, chosen
    assert "dashboard" in chosen


def test_the_funnel_carries_events_raised_inside_a_state_machine():
    """`note_event` **is** the funnel — not `app.push_event`.

    Before Phase 9 the alert layer only saw events that happened to travel
    through `push_event`; anything raised inside `state` or `ingestion.ring`
    (a dark node, a sequence reset, a segment fault) wrote to the log and stopped
    there. The sink closes that, and this test pins it by raising the event the
    way the state machine does — no HTTP, no request context, nothing.
    """
    import app

    before = len(dispatcher.MACHINE.open_alerts)
    app.STATE.note_event("WARNING", "Ring link L1 silent for 7.0s — relay expected.",
                         kind="INFRASTRUCTURE")
    assert len(dispatcher.MACHINE.open_alerts) > before


def test_the_funnel_does_not_alert_on_its_own_delivery_lines():
    """No alert about an alert. The delivery notice is `kind="ALERT"`."""
    import app

    before = len(dispatcher.MACHINE.open_alerts)
    app.STATE.note_event("CRITICAL", "NS-0001 — CRITICAL alert delivered to dashboard.",
                         kind="ALERT")
    assert len(dispatcher.MACHINE.open_alerts) == before
