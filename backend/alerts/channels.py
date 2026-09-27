"""The four channels, in priority order — and the ledger that proves them.

   1. local      junction LEDs + siren via the level hub   < 1 s   works offline
   2. dashboard  banner + event log + GIS flash            < 1 s   works offline
   3. sms        4G modem on the main hub's UART           < 5 s   buffered
   4. email      SMTP with a georeferenced deep link      < 30 s   buffered

**The local channel is attempted first, and its failure is a louder alarm than an
SMS failure.** An SMS that fails means a regulator learns late; a siren that fails
means people underground do not know. So a failed local push is not a log line —
it is an `INFRASTRUCTURE` event, because it is the worst failure mode the system
has (§8.5).

Every attempt returns a :class:`Delivery`, and the dispatcher writes each one to
`alert_deliveries`. That ledger is the difference between "we send alerts" and
"we can prove alerts were delivered", and it is on screen in the console.

Nothing here decides *whether* to alert. Tiering is `dispatcher`'s job and the
suppression rules live in `suppression_guard` — a channel that decided for itself
would be a second author of the alarm policy.
"""

from __future__ import annotations

import smtplib
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from email.message import EmailMessage

import config
from alerts import templates

CHANNELS = ("local", "dashboard", "sms", "email")

#: Sliding window for the SMS rate cap. An alert storm during a four-node drill
#: must not become twenty texts; §8.8 asks for the cap *and* for the refusal to be
#: visible — a dropped alert that nobody can see is worse than a loud one.
_SMS_TIMES: list[float] = []
#: Wall clock of the last red-alert text that actually went out. A second red
#: alert inside SMS_MIN_GAP_S is held so the same minute does not text again.
_SMS_ROUND_AT: float = 0.0


@dataclass
class Delivery:
    """One channel's attempt on one alert."""

    channel: str
    ok: bool
    detail: str = ""
    queued: bool = False          # accepted for retry (Phase 9's outbox does it)
    verified: bool = False        # the far end said so, rather than going quiet

    def as_row(self) -> dict:
        return {"channel": self.channel, "ok": bool(self.ok),
                "detail": self.detail[:400] if self.detail else ""}


# ---------------------------------------------------------------------------
# 1 · local — the one that must not fail
# ---------------------------------------------------------------------------

def notify_local(state, alert) -> Delivery:
    """Strobe the junction strips and sound the siren **through the level hub**.

    The server sends *intent*; the hub renders it. Junctions are passive WS2812B
    on a strip owned by the level hub, and there is no per-junction MCU — so this
    channel pushes a `HubCommand` per junction on the alert's level and one siren
    command, and records whether the hub's line accepted them.

    Deliberately **not** a route: an alert is not a reroute. The alert strobes;
    Phase 7's router separately decides where people should walk. Mixing them
    would mean the lights changed meaning with the message.
    """
    from routing import dijkstra as routing

    site = getattr(state, "site_map", None)
    if site is None:
        return Delivery("local", False,
                        "no tunnel map — the junctions cannot be addressed", queued=False)

    if not routing.downlink_attached():
        # No hub on the line (SIM, or the cable is out). Reported as a failure
        # rather than a success, because nothing physical happened — and the
        # dispatcher turns this into LOCAL_ALERT_UNVERIFIED.
        return Delivery("local", False,
                        "no hub attached — the strip command was not delivered",
                        queued=False)

    # Same pairing as the strip tick. Node 1 or 2 owns J1 and J3.
    # Node 3 or 4 owns J2 and J4. The alert must not paint a chase on top
    # of that, and a warning must not be promoted into a continuous siren.
    nodes = getattr(state, "nodes", {}) or {}

    def _status(node_id: str) -> str:
        node = nodes.get(node_id)
        return getattr(node, "status", "") if node is not None else ""

    pair_a = routing.alarm_from_status([_status("node_1"), _status("node_2")])
    pair_b = routing.alarm_from_status([_status("node_3"), _status("node_4")])
    junction_lamp = {"J1": pair_a[2], "J3": pair_a[2], "J2": pair_b[2], "J4": pair_b[2]}
    hub_of = {"J1": "L1", "J2": "L1", "J3": "L2", "J4": "L2"}
    _, mode, _lamp = routing.alarm_from_status(
        [_status("node_1"), _status("node_2"), _status("node_3"), _status("node_4")])
    sent, failed = 0, 0
    for j_id, pattern in junction_lamp.items():
        cmd = routing.HubCommand(
            hub=hub_of[j_id], junction=j_id, pattern=pattern,
            reason=f"{alert.severity} alert {templates.reference(alert)}")
        if routing.push_hub_command(cmd):
            sent += 1
        else:
            failed += 1
    for cmd in routing.sound_commands(mode):
        if routing.push_hub_command(cmd):
            sent += 1
        else:
            failed += 1
    # #region agent log
    try:
        import json as _json
        import time as _time
        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
            _f.write(_json.dumps({
                "sessionId": "83ae1d", "hypothesisId": "CMD",
                "runId": "post-fix",
                "location": "channels.py:notify_local",
                "message": "alert used the node pair",
                "data": {"node": getattr(alert, "node_id", ""),
                         "severity": str(getattr(alert, "severity", "")),
                         "mode": mode, "lamp": junction_lamp,
                         "status": {i: _status(i) for i in ("node_1", "node_2", "node_3", "node_4")}},
                "timestamp": int(_time.time() * 1000),
            }) + "\n")
    except Exception:
        pass
    # #endregion

    ok = sent > 0 and failed == 0
    return Delivery("local", ok,
                    f"{sent} command(s) pushed"
                    + (f", {failed} refused" if failed else ""),
                    verified=False)          # the hub's ACK is Phase 11's uplink report


# ---------------------------------------------------------------------------
# 2 · dashboard
# ---------------------------------------------------------------------------

def notify_dashboard(state, alert) -> Delivery:
    """The banner, the event log and the GIS flash — all in-process, all instant.

    This channel cannot fail for a network reason, which is why it is the one an
    operator should trust for *what the system decided*. The alert is also kept on
    the state object as the current banner, so the console renders the live one
    from `/api/alerts` rather than re-deriving it from the event log.
    """
    try:
        setattr(state, "current_alert", alert.as_public())
        state.note_event(str(alert.severity), f"{templates.reference(alert)} — {alert.text}",
                         kind="ALERT")
        return Delivery("dashboard", True, "banner + log + GIS flash")
    except Exception as exc:                                    # noqa: BLE001
        return Delivery("dashboard", False, f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# 3 · sms
# ---------------------------------------------------------------------------

def _sms_number(raw: str) -> str:
    """A 10-digit Indian handset becomes +91. A number that already has a
    country code is left alone apart from spaces and dashes."""
    text = raw.strip()
    if not text or text.startswith("#"):
        return ""
    digits = "".join(ch for ch in text if ch.isdigit())
    if text.startswith("+"):
        return "+" + digits if digits else ""
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return text


def sms_recipients() -> list[str]:
    """Handsets that stand in for the people near the site.

    ``NS_ALERT_SMS_TO`` wins when it is set, so a test can name its own
    numbers. When it is empty, the list is ``assets/locality_phones.txt``,
    one number per line. A red warning (HIGH or CRITICAL) texts every one.
    """
    raw = str(getattr(config, "ALERT_SMS_TO", "") or "").strip()
    if not raw:
        path = Path(__file__).resolve().parent.parent / "assets" / "locality_phones.txt"
        raw = path.read_text(encoding="utf-8") if path.is_file() else ""
    out: list[str] = []
    for part in raw.replace(";", ",").replace("\n", ",").split(","):
        number = _sms_number(part)
        if number and number not in out:
            out.append(number)
    return out


def sms_minute_held(now: float | None = None) -> bool:
    """True when a text already went out inside the one-minute gap."""
    now = float(now if now is not None else time.time())
    gap = float(getattr(config, "SMS_MIN_GAP_S", 60.0) or 60.0)
    if gap <= 0 or _SMS_ROUND_AT <= 0:
        return False
    return (now - _SMS_ROUND_AT) < gap


def sms_rate_limited(now: float | None = None) -> bool:
    """Has the hourly SMS cap been reached? **Logs nothing; the caller does.**"""
    now = float(now if now is not None else time.time())
    cap = int(getattr(config, "SMS_MAX_PER_HOUR", 20) or 0)
    if cap <= 0:
        return False
    window = [t for t in _SMS_TIMES if now - t < 3600.0]
    _SMS_TIMES[:] = window
    return len(window) >= cap


def _messages_send(number: str, body: str) -> tuple[bool, str]:
    """Send one text through the Messages app signed in on this laptop.

    iMessage is what the bench can deliver with no modem. A number that is not
    on iMessage comes back as a failure from Messages; that failure is the
    delivery detail, not a silent drop.
    """
    script = (
        "on run argv\n"
        "  set targetNum to item 1 of argv\n"
        "  set msgText to item 2 of argv\n"
        "  tell application \"Messages\"\n"
        "    set smsService to missing value\n"
        "    set imService to missing value\n"
        "    repeat with i from 1 to (count of services)\n"
        "      try\n"
        "        set s to service i\n"
        "        if service type of s is SMS then set smsService to s\n"
        "        if service type of s is iMessage then set imService to s\n"
        "      end try\n"
        "    end repeat\n"
        "    if smsService is not missing value then\n"
        "      set chosen to smsService\n"
        "    else if imService is not missing value then\n"
        "      set chosen to imService\n"
        "    else\n"
        "      error \"no SMS or iMessage account is signed in on this laptop\"\n"
        "    end if\n"
        "    set theBuddy to buddy targetNum of chosen\n"
        "    send msgText to theBuddy\n"
        "  end tell\n"
        "end run\n"
    )
    try:
        proc = subprocess.run(
            ["osascript", "-e", script, number, body],
            capture_output=True, text=True, timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Messages refused the text").strip()
        return False, detail.splitlines()[-1][:240]
    return True, "sent"


def _sms_via_messages(numbers: list[str], body: str) -> Delivery:
    sent, failed = [], []
    for number in numbers:
        if sms_rate_limited():
            failed.append(f"{number}: RATE_LIMITED")
            continue
        ok, detail = _messages_send(number, body)
        if ok:
            _SMS_TIMES.append(time.time())
            sent.append(number)
        else:
            failed.append(f"{number}: {detail}")
    if sent and not failed:
        return Delivery("sms", True, f"texted {len(sent)} local number(s)")
    if sent:
        return Delivery("sms", False,
                        f"texted {len(sent)}; failed {len(failed)}: {failed[0]}")
    return Delivery("sms", False, failed[0] if failed else "no number was texted")


def notify_sms(state, alert) -> Delivery:
    """Text the locality. Red warnings use this channel.

    On a bench with no 4G modem, ``NS_SMS_TRANSPORT=messages`` sends through
    the Messages app on this laptop. The hub path remains for a modem on the
    main hub's UART: the AT-command sequence lives in the hub, and a missing
    modem queues the text instead of blocking the siren.
    """
    global _SMS_ROUND_AT
    from routing import dijkstra as routing

    number = str(getattr(config, "ALERT_SMS_TO", "") or "").strip()
    numbers = sms_recipients()
    if not numbers:
        return Delivery("sms", False, "no ALERT_SMS_TO configured", queued=True)
    if sms_minute_held():
        # #region agent log
        try:
            import json as _json
            with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps({
                    "sessionId": "83ae1d", "hypothesisId": "SMS", "runId": "post-fix",
                    "location": "channels.py:notify_sms",
                    "message": "red text held for one minute",
                    "data": {"severity": str(getattr(alert, "severity", "")),
                             "since_s": round(time.time() - _SMS_ROUND_AT, 1)},
                    "timestamp": int(time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        return Delivery("sms", False,
                        "held — a text already went out in the last minute",
                        queued=False)
    if sms_rate_limited():
        return Delivery("sms", False,
                        f"RATE_LIMITED — more than {config.SMS_MAX_PER_HOUR} SMS in the hour",
                        queued=False)
    _SMS_ROUND_AT = time.time()
    if str(getattr(config, "SMS_TRANSPORT", "hub") or "hub").lower() == "messages":
        return _sms_via_messages(numbers, templates.locality_message(alert))

    body = templates.sms_body(alert)

    if not routing.downlink_attached():
        return Delivery("sms", False,
                        "no hub on the serial line — queued for the outbox", queued=True)

    frame = {"cmd": "sms", "to": number, "body": body,
             "ref": templates.reference(alert)}
    if routing.send_downlink(frame):
        _SMS_TIMES.append(time.time())
        return Delivery("sms", True, f"handed to the hub for {number[:4]}…")
    return Delivery("sms", False, "the hub refused the modem frame — queued",
                    queued=True)


# ---------------------------------------------------------------------------
# 4 · email
# ---------------------------------------------------------------------------

def email_configured() -> bool:
    return bool(getattr(config, "SMTP_HOST", "") and getattr(config, "ALERT_EMAIL_TO", ""))


def notify_email(state, alert) -> Delivery:
    """SMTP with the georeferenced deep link.

    Unconfigured is not an error — it is the prototype's honest default, and it is
    reported as *queued* so the ledger reads "not sent yet" rather than "failed".
    A configured-but-broken server is a real failure and says so.
    """
    to = str(getattr(config, "ALERT_EMAIL_TO", "") or "").strip()
    if not email_configured():
        return Delivery("email", False,
                        "SMTP not configured (NS_SMTP_HOST / NS_ALERT_EMAIL_TO) — queued",
                        queued=True)

    msg = EmailMessage()
    msg["Subject"] = templates.email_subject(alert)
    msg["From"] = str(getattr(config, "SMTP_FROM", "") or getattr(config, "SMTP_USER", "neural-strata"))
    msg["To"] = to
    msg.set_content(templates.email_body(alert, base=str(getattr(config, "ALERT_LINK_BASE", ""))))

    host = str(getattr(config, "SMTP_HOST", ""))
    port = int(getattr(config, "SMTP_PORT", 587))
    try:
        with smtplib.SMTP(host, port, timeout=float(getattr(config, "SMTP_TIMEOUT_S", 8.0))) as s:
            if getattr(config, "SMTP_TLS", True):
                s.starttls()
            user = str(getattr(config, "SMTP_USER", "") or "")
            if user:
                s.login(user, str(getattr(config, "SMTP_PASSWORD", "") or ""))
            s.send_message(msg)
        return Delivery("email", True, f"sent to {to}")
    except Exception as exc:                                    # noqa: BLE001
        return Delivery("email", False, f"{type(exc).__name__}: {exc}", queued=True)


# ---------------------------------------------------------------------------
# the fan-out
# ---------------------------------------------------------------------------

def send(state, alert, channels) -> list[Delivery]:
    """Attempt the named channels **in priority order**, collecting the ledger.

    Order is the point: local, then dashboard, then the networked pair. A slow
    SMTP server must never delay the siren, and because each channel is attempted
    in turn with its own short timeout, it cannot.
    """
    out: list[Delivery] = []
    for name in CHANNELS:
        if name not in channels:
            continue
        try:
            if name == "local":
                out.append(notify_local(state, alert))
            elif name == "dashboard":
                out.append(notify_dashboard(state, alert))
            elif name == "sms":
                out.append(notify_sms(state, alert))
            elif name == "email":
                out.append(notify_email(state, alert))
        except Exception as exc:                                # noqa: BLE001
            out.append(Delivery(name, False, f"{type(exc).__name__}: {exc}"))
    return out


def reset_rate_window() -> None:
    """Test hook — clears the SMS sliding window and the one-minute hold."""
    global _SMS_ROUND_AT
    _SMS_ROUND_AT = 0.0
    _SMS_TIMES.clear()


__all__ = ["CHANNELS", "Delivery", "notify_local", "notify_dashboard", "notify_sms",
           "notify_email", "email_configured", "sms_rate_limited", "send",
           "reset_rate_window"]
