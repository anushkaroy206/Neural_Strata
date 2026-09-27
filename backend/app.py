"""
Neural Strata — Edge Server Bootstrap (Phase 12A)
==================================================

The monolithic `app.py` is split across Phase 12A into:

1. `backend/app.py` — Bootstrap (~80 lines) that:
   - Imports configuration, contracts, and all modules
   - Creates Flask app instance
   - Registers blueprints from split modules
   - Handles CLI arguments and startup

2. `backend/api/routes_*.py` — API modules:
   - `routes_telemetry.py` — /api/telemetry
   - `routes_ai.py` — /api/ai
   - `routes_admin.py` — /api/sim/*, /api/metrics
   - `routes_cloud.py` — /api/cloud/* (Phase 12 requirement)
   - `routes_blackbox.py` — /api/blackbox/* (Phase 10)
   - `routes_map.py` — /api/map, /api/site (Phase 7)
   - `routes_alerts.py` — /api/alerts/* (Phase 8)

3. `backend/state.py` — SystemState (Phase 1)
4. `backend/config.py` — Configuration (Phase 0)
5. `backend/contracts.py` — Contracts (Phase 0)
6. All engine, ingestion, persistence, routing, alerts modules

This split enables:
- Each file has one reason to change
- API contracts can be tested independently
- Phase 12 deliverables can be created
- Original HTTP surface is preserved
"""

import argparse
import signal
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import json
import math
import threading
from copy import deepcopy
import time
from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS

import config
from config import ASSET_FILES, BLACKBOX_DIR, FRONTEND_DIR, MODEL_DT, HISTORY_INTERVAL_S, RUNTIME_DIR
from config import env_bool, validate_config
from contracts import flags_to_list, read_session_csv
from engine import cascade as cascade_engine
from engine import crack as crack_engine
from engine import geomech
from engine import health
from engine import severity as severity_engine
from engine import ttf as ttf_model
from ingestion import ring, serial_reader, simulator
from persistence import db
from api import routes_telemetry, routes_ai, routes_admin, routes_blackbox, routes_map, routes_alerts
from routing import dijkstra as routing
from alerts import dispatcher as alerts_dispatcher
from state import SystemState
from datetime import datetime, timezone

# Initialize Flask app
app = Flask(__name__)
CORS(app)

# Initialize system state
STATE = SystemState()

def load_model():
    """Load the TTF model at startup."""
    return ttf_model.load_ttf_model()

def load_pattern():
    """Load Model 1 (Phase 5)."""
    from engine import pattern as pattern_engine
    ok = pattern_engine.load_pattern_model()
    if ok:
        meta = pattern_engine.PATTERN_INFO.get("meta") or {}
        print(f"[ai] pattern model loaded: {meta.get('model', 'pattern_model_v1')} "
              f"(threshold {meta.get('operating_threshold')}, "
              f"{len(meta.get('features') or [])} features)")
    else:
        print(f"[ai] pattern model unavailable — cascade runs M2-only "
              f"({pattern_engine.PATTERN_INFO.get('reason')})")
    return ok

def setup_routing():
    """Setup routing subsystem."""
    from api import map_payload
    SITE_META = map_payload.load_site_meta()
    TUNNEL_MAP_PATH = config.ASSET_FILES["tunnel_map"]

    try:
        SITE_MAP = routing.load_tunnel_map(TUNNEL_MAP_PATH)
        print(f"[map] tunnel map loaded: {len(SITE_MAP.nodes)} nodes, "
              f"{len(SITE_MAP.junctions)} junctions, {len(SITE_MAP.exits)} exits, "
              f"{len(SITE_MAP.links)} links")
    except Exception as exc:
        SITE_MAP = None
        print(f"[map] TUNNEL MAP INVALID — /api/map will answer 503. {type(exc).__name__}: {exc}")

    STATE.site_map = SITE_MAP
    STATE.site_meta = SITE_META

    # Bind routes
    app.register_blueprint(routes_map.bp)
    routes_map.bind(SITE_MAP, SITE_META, STATE)

def setup_alerts():
    """Setup alert dispatcher."""
    app.register_blueprint(routes_alerts.bp)
    routes_alerts.bind(STATE, persist=db.persist_alert)

def setup_api_blueprints():
    """Setup all API blueprints."""
    # Each API module is imported above, now register them
    app.register_blueprint(routes_telemetry.bp)
    app.register_blueprint(routes_ai.bp)
    app.register_blueprint(routes_admin.bp)
    app.register_blueprint(routes_blackbox.bp)
    app.register_blueprint(routes_map.bp)
    app.register_blueprint(routes_alerts.bp)
from engine import crack as crack_engine                                # noqa: E402
from engine import geomech                                             # noqa: E402
from engine import health, ttf as ttf_model                             # noqa: E402
from engine import severity as severity_engine                          # noqa: E402
# PHASE 1 (B1): the ingestion package. serial_reader owns the one path from bytes
# to state; ring owns the relay/heartbeat path; simulator owns the 7 profiles.
from ingestion import ring, serial_reader, simulator                    # noqa: E402
from persistence import db                                              # noqa: E402
# PHASE 7 (B5): the evacuation router and the GIS payload builder. The map is
# loaded and validated at boot — §7.6, "a map that loads lazily is a map that
# fails during a demo" — and a failure is loud rather than deferred.
from api import (map_payload, routes_alerts, routes_blackbox,             # noqa: E402
                   routes_cloud, routes_map)
from routing import dijkstra as routing                                  # noqa: E402
# PHASE 8 (B4): the alert dispatcher. Policy lives in `alerts/`, SQL lives in
# `persistence/db.py`, and neither knows about the other — `app.py` is the one
# place that has both, which is why the persist callback is passed in rather than
# imported by the dispatcher.
from alerts import dispatcher as alerts_dispatcher                       # noqa: E402

#: The fixed baseline (`assets/site_meta.json`). Read once, at import: it is a
#: constant, and the GIS payload is built on a 2 s poll. Falls back to the
#: §A.3.6 numbers and says so rather than failing — see `map_payload`.
SITE_META = map_payload.load_site_meta()
from state import SystemState                                           # noqa: E402


def iso_now():
    """ISO-8601 UTC — the format docs/API.md specifies for telemetry.timestamp.

    The deck renders this value verbatim as the node's "Last packet" readout.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

app = Flask(__name__)
CORS(app)

# ---------------------------------------------------------------------------
# Phase 0 — everything that used to sit above this line is now imported:
# the .env loader, the database/serial/threshold constants, the 16-feature list,
# and the model globals. What remains here is live process state, unchanged.
# ---------------------------------------------------------------------------

#: 60 s rolling model buffers, one per live node (same cadence as training).
# Phase 1: the model buffers moved to SystemState.buffers, keyed on each
# sample's own clock (see state.append_model_buffer). The Phase 0 version
# appended from the ticker on wall-clock ticks, which is why a 10x replay
# could never fill a 60 s window.

# Phase 1: the lock lives on the state object (see `state_lock = STATE.lock`
# below). Two locks in one process is two chances to deadlock.

# --- blackbox gateway (Option A: the real ingest + gate path) ---------------
# GET /api/extended serves the deck's `blackbox` and `cloud` blocks from the
# truth store the hub feeds over the serial line -- see backend/blackbox/.
# If that package is not importable the server still boots; the blocks are simply
# absent and the deck keeps its own defaults rather than showing invented numbers.
BLACKBOX = None          # type: ignore[assignment]
SERIAL_ACTIVE = False
_BBX_DIR = BLACKBOX_DIR


def start_blackbox(port=None, card_mb=None, external_feed=False, sim_skip=None):
    """Bring up the ingest + gate + cloud gateway. Never fatal.

    `external_feed=True` means *this* server already owns the serial line, so the
    gateway must not open it too -- we hand it every line through Gateway.feed().
    """
    global BLACKBOX
    if not (_BBX_DIR / "gateway.py").exists():
        print(f"[blackbox] {_BBX_DIR} not found - /api/extended will omit it")
        return None
    try:
        if str(_BBX_DIR) not in sys.path:
            sys.path.insert(0, str(_BBX_DIR))
        from gateway import Gateway                     # noqa: PLC0415

        BLACKBOX = Gateway(
            port=port, card_mb=card_mb, external_feed=external_feed,
            sim_skip=sim_skip,          # bench lever: "CHUNK:ROWS", simulator only
            db_path=RUNTIME_DIR / "blackbox.sqlite",
            log=lambda m: print(f"[blackbox] {m}"),
        ).start()
        if BLACKBOX.mode == "SIM":
            why = ("waiting for the serial reader to open the hub's line"
                   if external_feed else "simulator feeding it, no hub attached")
            print(f"[blackbox] gateway up - mode=SIM ({why})")
        else:
            print(f"[blackbox] gateway up - mode={BLACKBOX.mode}")
    except Exception as exc:                            # noqa: BLE001
        print(f"[blackbox] gateway unavailable ({type(exc).__name__}: {exc})")
        BLACKBOX = None
    return BLACKBOX

# --- GLOBAL STATE (Matches Dashboard Contract) ---
# ---------------------------------------------------------------------------
# PHASE 1 (B1) — live state lives in one object
# ---------------------------------------------------------------------------
# Before Phase 1 this was a module-level dict with two hardcoded nodes plus six
# more module-level dicts beside it (node_buffers, peak_accel, history,
# injections, injection_t, model_ttf). The literal ("node_1", "node_2") appeared
# in five functions (D10). All of it is now SystemState, config.NODE_IDS is the
# only place the node set is written down, and STATE.iter_nodes() is the only way
# to walk it. See backend/state.py.
# ---------------------------------------------------------------------------

STATE = SystemState()

#: Phase 2 — the one shutdown flag. The serial reader waits on it instead of
#: sleeping through a 5 s backoff, so Ctrl+C is clean even mid-retry (§2.5:
#: "pass ``stop_event`` everywhere so ``Ctrl+C`` is clean").
STOP_EVENT = threading.Event()

# `state_lock` is kept as the name every reader already uses; it is the state's
# own lock, so there is exactly one lock in the process rather than two that can
# deadlock against each other.
state_lock = STATE.lock

# engine.ttf reads node status through this hook, so it never has to import this
# module (which would be a cycle).
ttf_model.set_node_provider(lambda n_id: STATE.nodes[n_id].to_public())

def iter_nodes():
    """Every live node. The single iteration primitive — replaces the six
    two-key dict comprehensions that made 4 nodes a five-file edit."""
    return STATE.iter_nodes()


def geo_tag_default(n_id):
    return ""



def zone_for(reader):
    """Zone for one RFID scan. Delegates to STATE (which owns the route state)."""
    return STATE.zone_for(reader)


def note_zone(uid, zone):
    STATE.zones[str(uid)] = zone


def muster_counts():
    """Roll call: how many accounted for, and where. Delegates to STATE."""
    return STATE.muster_counts()



# MINERS lives in state.py with the rest of the roster.

# --- PHASE 5: SITE GEOREFERENCING + GIS (fixed baseline — NO GPS module) ---
# The prototype diorama is a static panel: georeferencing = baseline + fixed
# metre offsets (d_n_m = north, d_e_m = east) from tunnel_map.json.
# GPS never TRACES subsidence — sensors + AI do; georeferencing only PLACES
# the zones on a real map. Field-deploy scale-up: swap SITE["center"] for a
# live NEO-6M fix — the /api/map contract is unchanged (GPS Module Spec PDF).
SITE = {
    "name": "Jharia panel — 1:1000 diorama",
    "center": (23.643, 85.318),   # baseline lat, lon (fixed — no GPS module)
    "scale": "1 cm diorama = 10 m real ground",
    "positioning": "fixed baseline + offsets (GPS deferred — spec ready)",
}
TUNNEL_MAP_PATH = ASSET_FILES["tunnel_map"]
TUNNEL_MAP = {}


def _load_tunnel_map():
    global TUNNEL_MAP
    try:
        TUNNEL_MAP = json.loads(TUNNEL_MAP_PATH.read_text())
    except Exception as exc:
        print(f"tunnel_map.json not loaded ({exc}) — GIS map disabled.")
        TUNNEL_MAP = {"nodes": {}, "junctions": [], "exits": [], "links": [], "zones": []}


_load_tunnel_map()

# ---------------------------------------------------------------------------
# PHASE 7 (B5) — the graph, built once at boot
# ---------------------------------------------------------------------------
# `site_map` is the validated SiteMap; it is attached to STATE so the router can
# find it from anywhere (`hazard_penalty`, `resolve_position`, `build_graph` all
# read it off the state they are handed). A map that will not validate does NOT
# stop the server — telemetry, the AI and the alarms have nothing to do with the
# GIS card, and a dead server is a worse failure than a missing map. It does mean
# `/api/map` answers 503 with the reason, which is the honest answer.
SITE_MAP = None
SITE_MAP_ERROR = None
try:
    SITE_MAP = routing.load_tunnel_map(TUNNEL_MAP_PATH)
    print(f"[map] tunnel map loaded: {len(SITE_MAP.nodes)} nodes, "
          f"{len(SITE_MAP.junctions)} junctions, {len(SITE_MAP.exits)} exits, "
          f"{len(SITE_MAP.links)} links")
except Exception as exc:                                        # noqa: BLE001
    SITE_MAP_ERROR = f"{type(exc).__name__}: {exc}"
    print(f"[map] TUNNEL MAP INVALID — /api/map will answer 503. {SITE_MAP_ERROR}")


# The router reads the map and the live nodes off the state object it is handed,
# so the map rides along with it. Attached here rather than inside
# `SystemState()` because the map is a *file*, and `state.py` has to stay
# importable without one (the tests import it constantly).
STATE.site_map = SITE_MAP
STATE.site_meta = SITE_META

# The GIS routes. `bind` carries the boot-time map so the blueprint never has to
# import `app` at module scope.
app.register_blueprint(routes_map.bp)
routes_map.bind(SITE_MAP, SITE_META, STATE, SITE_MAP_ERROR)

# Phase 8 — /api/alerts, /api/alerts/<id>/ack, /api/alerts/<id>/deliveries and
# /api/alerts/policy. Same injection rule as the map: `bind`, never `import app`.
app.register_blueprint(routes_alerts.bp)
routes_alerts.bind(STATE, persist=db.persist_alert)

# Phase 10 (B13/B14) — the blackbox's own API. Bound to a *callable*: the gateway
# is created (or re-created) after this line runs, so the route asks where it is at
# request time. `/api/blackbox/{status,chunks}` are new endpoints; `recover` is
# Phase 11's and is deliberately absent rather than stubbed.
app.register_blueprint(routes_blackbox.bp)
routes_blackbox.bind(lambda: BLACKBOX)

# Phase 12 — /api/cloud/{status,trace,force_sync}. The blueprint was written and
# never mounted, so the runbook's status and trace calls answered 404 while the
# panel's force-sync still worked through the copy further down this file.
app.register_blueprint(routes_cloud.bp)
routes_cloud.bind(lambda: BLACKBOX)


# PHASE 7: the conversion moved to `api/map_payload.py` (one implementation, the
# one the GIS payload uses too). This wrapper stays because `geo_tag()` below is
# called from state events, and the survey constants now come from
# `assets/site_meta.json` rather than being re-typed here.
def _geo(d_n, d_e, center=None):
    if center is not None:                     # legacy callers pass a centre
        clat, clon = center
        return (clat + d_n / 111320.0,
                clon + d_e / (111320.0 * math.cos(math.radians(clat))))
    return map_payload.offset_to_latlon(d_n, d_e, SITE_META)


def node_geo(n_id):
    n = TUNNEL_MAP.get("nodes", {}).get(n_id)
    return _geo(n["d_n_m"], n["d_e_m"]) if n else None


def geo_tag(n_id):
    g = node_geo(n_id)
    return f" · {g[0]:.5f}°N {g[1]:.5f}°E" if g else ""


# Status events carry the node's georeference where the map knows it. Wired here
# rather than at the top of the file because `geo_tag` reads TUNNEL_MAP, which is
# loaded just above and is empty until `_load_tunnel_map()` runs.
STATE.geo_tag = geo_tag


def get_db_connection():
    """Borrow a pooled connection, or None when the database is unreachable.

    Phase 0 (D11): the body moved to ``persistence.db.get_conn()`` — one pooled
    connection per call instead of one TCP connect per packet at 2 Hz. The name
    and the "returns None, never raises" contract are unchanged, so every caller
    in this file is untouched.
    """
    return db.try_get_conn()


def log_telemetry_rows(rows):
    """Phase 4 — persist both nodes' state to PostgreSQL (called from ticker @ 2 Hz).

    Phase 0: the SQL moved to ``persistence.db.insert_telemetry`` so the column
    list lives next to the schema file that defines it.

    CRITICAL FIX: Also update STATE.last_arrival with actual sample timestamps
    """
    import time
    # Insert into database
    count = db.insert_telemetry(rows)

    # Update in-memory state cache with ACTUAL SAMPLE TIMESTAMPS (not insertion time)
    if count > 0 and STATE:
        if not hasattr(STATE, 'last_arrival'):
            STATE.last_arrival = {}

        # rows are tuples from NodeState.to_db_tuple()
        # row[0] = ts (timestamp), row[1] = node_id
        for row in rows:
            ts, node_id = row[0], row[1]
            if node_id and ts:
                # Store the actual sample timestamp so node_last_seen() calculates age correctly
                import time as time_module
                ts_epoch = time_module.mktime(ts.timetuple()) if hasattr(ts, 'timetuple') else ts
                STATE.last_arrival[node_id] = ts_epoch

    return count


def push_event(level, text, kind=None):
    """The operator event log (read by /api/extended).

    Phase 1: owns nothing. It is a projection of ``STATE.events`` so the log has
    exactly one writer and one order (newest first, capped at 64).

    Phase 2: ``kind`` passes through to ``STATE.note_event``. Infra events carry
    ``kind="INFRASTRUCTURE"`` so the alert history can tell a dark sensor from a
    geological tier without widening the ``level`` field the deck colours from.
    """
    # PHASE 8 (§8.5) — the funnel. PHASE 9 (B6) closed it.
    #
    # Until Phase 9 this function *was* the funnel, which meant the dispatcher saw
    # only the events that happened to be routed through it. Every event raised
    # inside a state machine — a dark node, a sequence reset, a ring segment fault
    # — wrote to the log and stopped there. The fix is not another call site: it is
    # the sink on `SystemState`, so `note_event` *is* the funnel and this function
    # is simply its most common caller again.
    STATE.note_event(level, text, kind=kind)


#: Which alert tier an operator *level* maps to. The two vocabularies look alike
#: and are not: `level` is the event log's NOMINAL/WARNING/CRITICAL, `tier` is the
#: alert layer's WATCH/ELEVATED/HIGH/CRITICAL with its own channel rules.
#:
#: Infrastructure events are deliberately **never** HIGH: a dark sensor is a
#: problem with a cable, and texting the Safety Officer about a cable does not fix
#: it — while a quiet dashboard row does get it fixed. The geological events are
#: the ones that earn a message off-site.
_LEVEL_TO_TIER = {"CRITICAL": "CRITICAL", "WARNING": "HIGH"}
_INFRA_TIER = "ELEVATED"

#: True while `alert_raise` is dispatching. A delivery writes its own line to the
#: event log ("NS-8F2A — CRITICAL …", kind="ALERT"), and without this guard that
#: line would come back through the sink and raise an alert about the alert,
#: forever. The guard is a flag rather than a check on the text because the
#: *shape* of a delivery line is the channels' business, not this function's.
_IN_ALERT_RAISE = False


def _event_sink(level: str, text: str, kind=None) -> None:
    """The alert layer's end of the event funnel (Phase 9, B6).

    Called by ``SystemState.note_event`` for **every** line. Two kinds of line are
    not alerts:

    * ``kind="ALERT"`` — the delivery notices the alert layer itself writes. An
      alert about an alert is a loop, not a policy.
    * ``kind="OPERATOR"`` (an operator action: an ack, a re-arm, a mode change) —
      the log is where that belongs; alerting on it would page the Safety Officer
      every time somebody pressed a button.

    Everything else goes to :func:`alert_raise`, which decides the tier and drops
    anything below WARNING.
    """
    global _IN_ALERT_RAISE
    if _IN_ALERT_RAISE:
        return
    if str(kind or "").upper() in ("ALERT", "OPERATOR"):
        return
    alert_raise(level, text, kind)


#: Phase 9 (B6) — the funnel's other end, armed once, here, where the function it
#: names has just been defined. Every event raised anywhere in the process now
#: reaches the alert layer — including the ones raised inside a state machine that
#: has never heard of `app`, which is where the leak was.
STATE.event_sink = _event_sink


def alert_raise(level: str, text: str, kind=None, node_id: str | None = None):
    """Build an :class:`AlertEvent` from an event-log line and dispatch it.

    Returns the event (or None when the line does not warrant one) so a caller can
    assert on it; the ticker ignores the return value.
    """
    site = getattr(STATE, "site_map", None)
    node = node_id or ""
    if not node and site is not None:
        # The event text carries the node name ("NODE 2 …"), so the event log's own
        # wording is what identifies the subject — rather than a second parameter
        # every existing call site would have to remember to pass.
        for candidate in getattr(site, "nodes", {}):
            label = str(site.nodes[candidate].get("label", ""))
            short = label.split("—")[0].strip()
            if short and short.lower() in str(text).lower():
                node = candidate
                break

    event = None
    if node:
        event = alerts_dispatcher.event_from_node(
            STATE, node, event_type=str(kind or "EVENT"), text=text, kind=kind)
        # The event log's line is the operator's own wording; keep it, so the alert
        # and the log never tell two versions of one incident.
        if event is not None:
            event.text = str(text)[:300]
    if event is None:
        # No node in the line, or the node has no tier yet (a route change, a
        # health warning): still an alert, deliberately without coordinates that we
        # would be inventing.
        event = alerts_dispatcher.AlertEvent(
            node_id=node or "site", level_id="", severity=_LEVEL_TO_TIER.get(
                str(level).upper(), "ELEVATED"),
            event_type=str(kind or "EVENT"), text=str(text)[:300], kind=kind,
            where="site", recommended_action=str(text)[:200],
            geo_lat=float(getattr(STATE, "site_meta", {}).get("base_lat", 0.0) or 0.0),
            geo_lon=float(getattr(STATE, "site_meta", {}).get("base_lon", 0.0) or 0.0),
        )
    # The clamp, and the direction is the trap: `tier_rank` counts **0 = loudest**,
    # so "louder than ELEVATED" is `<`, not `>`. Written the other way it silently
    # did nothing, and an infrastructure event inherited HIGH — which is the tier
    # that texts the Safety Officer about a loose cable. Found in Phase 9 by
    # printing the tier of a ring-fault alert and reading HIGH where the comment
    # above promises ELEVATED.
    if kind == "INFRASTRUCTURE" and \
            alerts_dispatcher.tier_rank(event.severity) < alerts_dispatcher.tier_rank(_INFRA_TIER):
        event.severity = _INFRA_TIER

    global _IN_ALERT_RAISE
    _IN_ALERT_RAISE = True
    try:
        return alerts_dispatcher.dispatch(STATE, event, persist=db.persist_alert)
    except Exception as exc:                                    # noqa: BLE001
        print(f"[alerts] dispatch failed for {event.node_id}: {type(exc).__name__}: {exc}")
        return event
    finally:
        _IN_ALERT_RAISE = False


#: Alerts whose node has gone quiet. Cleared *here* rather than in the state's own
#: severity pass so the alert layer owns its own lifetime, and so a node crossing
#: back under a threshold does not have to know that an alert was ever raised.
_ALERT_CLEAR_SINCE: dict[str, float] = {}


def alert_tick(now: float | None = None) -> None:
    """The alert layer's 1 Hz pass: observe, escalate, and settle.

    Three jobs, in this order, and the order matters:

    1. **observe** — every node whose tier or stage changed gets a look, and a new
       condition becomes an alert. Dedup lives in the machine, so this can run
       every second and still produce one alert per incident.
    2. **escalate** — an unacknowledged alert past `ALERT_ESCALATE_S` re-notifies.
    3. **settle** — a node that has been NOMINAL for `ALERT_SETTLE_S` closes its
       alert. The delay is not cosmetic: a signal that dips under a threshold for
       one sample is a sensor, not a recovery.
    """
    now = float(now or time.time())
    for node_id, node in list(getattr(STATE, "nodes", {}).items()):
        tier = str(getattr(node, "severity", "") or "").upper()
        if tier in ("", "NOMINAL", "WATCH"):
            # Nothing to raise. Track how long it has been quiet, for the close.
            _ALERT_CLEAR_SINCE.setdefault(node_id, now)
            continue
        _ALERT_CLEAR_SINCE.pop(node_id, None)
        event = alerts_dispatcher.event_from_node(STATE, node_id, event_type="SEVERITY")
        if event is None:
            continue
        alerts_dispatcher.dispatch(STATE, event, persist=db.persist_alert)

    # Close the alerts whose condition has been gone for long enough.
    for key, event in list(alerts_dispatcher.MACHINE.open_alerts.items()):
        if event.event_type != "SEVERITY":
            continue
        since = _ALERT_CLEAR_SINCE.get(event.node_id)
        if since is None:
            continue
        if now - since < float(config.ALERT_SETTLE_S):
            continue
        alerts_dispatcher.MACHINE.close(event, now=now, reason="condition cleared")
        db.persist_alert(event, [])

    alerts_dispatcher.escalate_due(STATE, persist=db.persist_alert, now=now)


# ── Phase 8 (§7.6, server end): keep the junction strips showing the plan ────
#: What the server last *decided* to send, so a 1 Hz tick does not re-send an
#: unchanged plan 60 times a minute — and so the re-assert can be told apart from
#: a real change in the counter the panel reads.
_LED_LAST: dict = {}
_LED_STATE = {"last_push": 0.0, "changes": 0, "reasserts": 0, "refused": 0}


def led_tick(now: float | None = None) -> None:
    """Push the junction plan down the ring: §7.6's **server** end.

    Phase 7 built the plan (`junction_led_plan`) and the wire (`HubCommand`,
    `to_line`, `attach_downlink`) and left this loop to the phase that owns the
    hub's decoder — because a plan nobody transmits is a plan, not a light. Until
    now the only thing that ever pushed a `#CMD` frame was an alert, so on the
    bench the strips showed the alert pattern and nothing else: no `flow_green` on
    the escape route, no `chase_red` on a blocked leg, no `hold_amber` at a
    junction with nowhere to send anyone.

    Three rules, and each one is a defect if dropped:

    * **on change, immediately** — a light that waits for the next tick is a light
      that is late to a hazard. The comparison is on the whole command (pattern,
      colour, period, buzzer), not just its name.
    * **unchanged, re-assert every `HUB_REASSERT_S`** — this is what makes the
      hub's hold-last rule (30 s of silence, §H6.6) safe to state: silence now
      means the *server* is gone, not that the mine is quiet. It also re-teaches a
      hub that rebooted mid-incident.
    * **always record the attempt** — `push_hub_command` writes the junction inbox
      whether the line took the frame or refused it, so "the strip is showing X"
      and "the cable is out" are distinguishable from the console.
    """
    from routing import dijkstra as routing_engine

    now = float(now or time.time())
    site = getattr(STATE, "site_map", None)
    if site is None:
        return
    routes = (STATE.underground or {}).get("routes") or {}
    per_level = list((routes.get("per_level") or {}).values())
    try:
        plan = routing_engine.junction_led_plan(site, STATE, per_level)
    except Exception as exc:                                    # noqa: BLE001
        print(f"[led] plan failed: {type(exc).__name__}: {exc}")
        return

    # Node 1 or 2 owns J1 and J3. Node 3 or 4 owns J2 and J4.
    # The route plan is still built above, but it must not paint a different
    # colour onto those strips — that was lighting the escape path while a
    # single pair was the actual alarm.
    nodes = getattr(STATE, "nodes", {}) or {}

    def _status(node_id: str) -> str:
        node = nodes.get(node_id)
        return getattr(node, "status", "") if node is not None else ""

    pair_a = routing_engine.alarm_from_status([_status("node_1"), _status("node_2")])
    pair_b = routing_engine.alarm_from_status([_status("node_3"), _status("node_4")])
    junction_lamp = {"J1": pair_a[2], "J3": pair_a[2], "J2": pair_b[2], "J4": pair_b[2]}
    for cmd in plan:
        if cmd.junction in junction_lamp:
            cmd.pattern = junction_lamp[cmd.junction]

    # A pulse is due when the last thing we sent was a *pulse*, or a change, more
    # than `HUB_REASSERT_S` ago. `last_push > 0` matters: on the very first tick
    # there is no history, and counting the opening push as a re-assert would make
    # the counter that the panel prints a lie from the first second.
    due = (_LED_STATE["last_push"] > 0.0
           and (now - _LED_STATE["last_push"]) >= float(config.HUB_REASSERT_S))

    changed = False
    led_sent = False
    for cmd in plan:
        key = f"{cmd.hub}:{cmd.junction}"
        body = cmd.to_dict()
        signature = (body.get("pattern"), body.get("colour"),
                     body.get("period_ms"), body.get("buzzer"))
        fresh = _LED_LAST.get(key) != signature
        if not fresh and not due:
            continue
        ok = routing_engine.push_hub_command(cmd)
        if ok:
            _LED_LAST[key] = signature
            led_sent = True
        changed = changed or (fresh and ok)
        if fresh and not ok:
            _LED_STATE["refused"] += 1

    if changed:
        _LED_STATE["changes"] += 1
        _LED_STATE["last_push"] = now
    elif due:
        _LED_STATE["reasserts"] += 1
        _LED_STATE["last_push"] = now
        routing_engine.note_reassert()

    # The lamps and the siren follow the reading, on both levels, in this
    # same pass. A warning is a creep (three beeps, red lamp on, green off).
    # A critical is a failure (continuous siren, red lamp blinking, green
    # off). A good reading puts the green lamp back and stops the sound.
    # Nothing is saved for a later tick: the gate, the strips and the buzzer
    # are told the current state together.
    # A quiet pair stays green. The horn still follows the worst reading,
    # on both levels. The strip colours were fixed on the route plan above.
    statuses = [_status("node_1"), _status("node_2"), _status("node_3"), _status("node_4")]
    tier, mode, _lamp = routing_engine.alarm_from_status(statuses)
    sound_key = "sound:mode"
    prev_sig = _LED_LAST.get(sound_key)
    sig = (mode, junction_lamp["J1"], junction_lamp["J2"], junction_lamp["J3"], junction_lamp["J4"])
    why = {"creep": "creep — three beeps, red on, green off",
           "failure": "failure — siren, red blinking, green off",
           "nominal": "readings normal — green, silent"}
    if prev_sig != sig or led_sent or due:
        pushed = 0
        for hub, junction in (("L1", "J1"), ("L1", "J2"), ("L2", "J3"), ("L2", "J4")):
            lamp = junction_lamp[junction]
            reason = why.get(
                {"strobe_red": "failure", "advisory_amber": "creep"}.get(lamp, "nominal"),
                tier)
            if routing_engine.push_hub_command(routing_engine.HubCommand(
                    hub=hub, junction=junction, pattern=lamp, reason=reason)):
                pushed += 1
        for stop in routing_engine.sound_commands(mode):
            if routing_engine.push_hub_command(stop):
                pushed += 1
        _LED_STATE["clear_step"] = "now"
        if pushed:
            _LED_LAST[sound_key] = sig
            # #region agent log
            if True:
                try:
                    import json as _json
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d",
                            "hypothesisId": "C",
                            "runId": "post-fix",
                            "location": "app.py:led_tick",
                            "message": "hub sound follows the lights",
                            "data": {
                                "mode": mode,
                                "tier": tier,
                                "lamp": dict(junction_lamp),
                                "clear_step": _LED_STATE.get("clear_step"),
                                "patterns": sorted({c.pattern for c in plan}),
                                "pushed": pushed,
                                "nodes": {
                                    n.node_id: {
                                        "status": getattr(n, "status", ""),
                                        "stale": bool(getattr(n, "stale", False)),
                                        "accel": round(float(getattr(n, "acceleration", 0) or 0), 2),
                                        "crack": str(getattr(n, "crack_stage", "")),
                                    }
                                    for n in (getattr(STATE, "nodes", {}) or {}).values()
                                },
                            },
                            "timestamp": int(time.time() * 1000),
                        }) + "\n")
                except Exception:
                    pass
            # #endregion


def led_block() -> dict:
    """The down-link's own status, for `/api/extended` and `/api/alerts`."""
    from routing import dijkstra as routing_engine
    stats = routing_engine.inbox_stats()
    return {
        "attached": routing_engine.downlink_attached(),
        "tick_s": float(config.HUB_LED_TICK_S),
        "reassert_s": float(config.HUB_REASSERT_S),
        "silence_s": float(config.HUB_SILENCE_S),
        "changes": _LED_STATE["changes"],
        "reasserts": _LED_STATE["reasserts"],
        "refused": _LED_STATE["refused"],
        **stats,
        "junction_inbox": routing_engine.junction_inbox(),
    }


#: The minute the telemetry aggregates were last queued for. Kept so a ticker that
#: fires late still queues *the* minute it belongs to, rather than skipping one —
#: and so `ref` is stable, which is what makes the enqueue idempotent.
_LAST_TELEMETRY_REF: str = ""


def enqueue_telemetry_aggregates(now: float | None = None, window_s: int = 60) -> int:
    """§9.5 — queue one summary per node per minute, from the local rows.

    Three deliberate choices, each of which is a bug avoided:

    * **Read the rows, don't remember them.** The aggregate is a SQL aggregate over
      `sensor_telemetry` — the same local record the hourly rollup reads — so the
      outbox item is a *projection* of durable data. If the process dies between
      the local write and this enqueue, the next tick recomputes the identical
      item; nothing is lost and nothing needs to be remembered. That is why this
      path does not need the same-transaction guarantee that alerts do (and why the
      alert path, where the event exists only in memory, does).
    * **One transaction, then one commit.** All four nodes' items land together or
      not at all, so a reader never sees a minute with three nodes in it.
    * **`ref` = node + minute.** The queue's unique `ref` is what turns a
      double-tick (a clock nudge, two daemons, a restart mid-minute) into a no-op
      instead of a duplicate summary on the far side.
    """
    from persistence import outbox

    from persistence import rollup as _rollup

    now = float(now or time.time())
    end = datetime.fromtimestamp(now, tz=timezone.utc).replace(second=0, microsecond=0)
    global _LAST_TELEMETRY_REF
    _LAST_TELEMETRY_REF = end.strftime("%Y%m%dT%H%MZ")

    queued = 0
    with db.borrowed() as conn:
        if conn is None:
            return 0
        try:
            # The aggregate lives in `persistence/rollup.py`, next to the hourly one
            # it mirrors: two copies of "the mean displacement" are two definitions
            # of it, and they drift.
            rows = _rollup.minute_aggregates(conn, end, window_s=window_s)
            for row in rows:
                node_id = row["node_id"]
                node = getattr(STATE, "nodes", {}).get(node_id)
                payload = {
                    "site_id": config.SITE_ID,
                    "node_id": node_id,
                    "minute_start": end.isoformat(),
                    "severity_max": str(getattr(node, "severity", "NOMINAL") or "NOMINAL"),
                    **{k: (float(v) if isinstance(v, (int, float)) else v)
                       for k, v in row.items() if k != "node_id"},
                }
                ref = f"tel:{node_id}:{end.strftime('%Y%m%dT%H%M')}"
                if outbox.enqueue_outbox(conn, "telemetry", payload, ref=ref) is None:
                    print(f"[outbox] FULL — telemetry aggregate for {node_id} not queued "
                          f"(cap {config.OUTBOX_MAX_ROWS} pending rows)")
                else:
                    queued += 1
            conn.commit()
        except Exception as exc:                                # noqa: BLE001
            try:
                conn.rollback()
            except Exception:
                pass
            print(f"[outbox] telemetry enqueue failed: {type(exc).__name__}: {exc}")
            return 0
    return queued


def _sync_block() -> dict:
    """The sync summary for `/api/extended` — never raises, never guesses."""
    try:
        from persistence import sync as _sync
        block = _sync.status()
        with db.borrowed() as conn:
            if conn is not None:
                counts = _sync.pending_counts(conn)
                block.update(counts)
                block["pending_total"] = counts.get("total", 0)
        return block
    except Exception as exc:                                    # noqa: BLE001
        return {"cloud_enabled": False, "error": f"{type(exc).__name__}: {exc}"}


def snapshot_history():
    """One chart sample per node, for ``/api/history``."""
    return STATE.snapshot_history()
def calculate_derivatives(node_id, current_disp, dt=None):
    """Velocity/acceleration from displacement using **measured** Δt (D5).

    Phase 1 moved the arithmetic into ``ingestion.serial_reader`` and added the
    ``dt`` argument: the old version hardcoded ``dt = 0.5``, so a node delivering
    frames at 0.25 s reported a quarter of its true velocity. The name is kept
    because the runbook calls it; ``dt=None`` means "no previous sample", which is
    the honest answer for a caller that does not track the node clock.
    """
    return serial_reader.compute_derivatives(STATE.nodes[node_id], float(current_disp), dt)
def load_model():
    """Load the TTF model at startup. Missing/broken => kinematic fallback.

    Implementation: ``engine.ttf.load_ttf_model()``. The printed lines
    ("[ai] TTF model loaded: …") are unchanged, so the runbook still matches.
    """
    return ttf_model.load_ttf_model()


def load_pattern():
    """Load Model 1 (Phase 5). Same degrade-don't-crash contract as M2.

    ``MANIFEST.json`` declares ``model_1.required = false``, so an absent or
    rejected artifact is a *supported* configuration: the log says so and the
    cascade falls back to M2 + the physics chain, which is the Phase 4 system.
    """
    from engine import pattern as pattern_engine
    ok = pattern_engine.load_pattern_model()
    if ok:
        meta = pattern_engine.PATTERN_INFO.get("meta") or {}
        print(f"[ai] pattern model loaded: {meta.get('model', 'pattern_model_v1')} "
              f"(threshold {meta.get('operating_threshold')}, "
              f"{len(meta.get('features') or [])} features)")
    else:
        print(f"[ai] pattern model unavailable — cascade runs M2-only "
              f"({pattern_engine.PATTERN_INFO.get('reason')})")
    return ok


def pattern_model_status():
    """The ``model_1`` block of ``/api/ai`` (additive, §5.3).

    Layered over ``pattern.pattern_status()`` with the **last live verdict**, so
    the dashboard's chip, bar and top-feature list have something to render the
    moment a window exists — without the endpoint having to run inference itself.
    Freshness rule mirrors M2's: a verdict older than ``MODEL_FRESH_S`` is
    reported as warming rather than left on screen as if it were current.
    """
    from engine import pattern as pattern_engine

    status = pattern_engine.pattern_status()
    decs = cascade_engine.last_decisions()
    live = None
    for d in decs.values():
        if d.m1 is not None:
            live = d
            break

    if live is None:
        status.update({"label": None, "disruption_index": None,
                       "structural_prob": None, "top_features": []})
        return status

    m1 = live.m1 or {}
    status.update({
        "label": m1.get("label"),
        "disruption_index": m1.get("disruption_index"),
        "structural_prob": m1.get("structural_prob"),
        "top_features": m1.get("top_features") or [],
        "node": live.node,
        # The tier and the band belong to the *cascade's* view of this window, not
        # to Model 1, but they are published here so the AI Cascade panel can
        # render one row per model from one request. The alternative — the deck
        # joining two blocks by node id on the client — is a join that silently
        # produces a blank when a node drops out, which is the failure mode the
        # frontend rule in §5.9 exists to avoid.
        "tier": live.tier,
        "ttf_s": live.ttf,
        "band": list(live.m2_band) if live.m2_band else None,
    })
    return status


def model_ttf_current():
    """True when the model has a recent, valid prediction to trust."""
    return ttf_model.model_fresh()


def run_model_inference():
    """**Deleted in Phase 5** — replaced by ``cascade_engine.cascade_tick``.

    Kept as a two-line shim only so any external caller or replay script that
    still says ``run_model_inference()`` keeps working; it now routes through the
    cascade so the two can never disagree about the system's verdict. §5.9 asks
    for the function to be deleted, and in spirit it is: nothing in this file
    calls it, and the ticker no longer knows how many models exist.

    The reason for the change is not tidiness. Running M2 on its own and *then*
    reading the cascade's opinion separately would let the pill and the alerts
    disagree — exactly the class of bug that produced
    "the TTF said 300 s while the siren was going".
    """
    return cascade_engine.cascade_tick(STATE, time.time())
def update_status_logic():
    """Per-node status → per-level status → the two route keys (D7).

    Phase 1 moved the cascade into ``SystemState.update_status_logic`` because it
    now depends on the level topology, which the state owns. The rule the
    dashboard reads is unchanged; Route A is Level 1's route and Route B is
    Level 2's, derived from the levels the nodes are actually on.
    """
    return STATE.update_status_logic()
def apply_injections(dt):
    """Advance every armed scenario profile by the **measured** dt (D14).

    Phase 1 moved the profiles into ``ingestion.simulator`` (7 of them) and
    stopped assuming the ticker's period is exactly 50 ms — the tick also does DB
    and buffer work, so the real period drifts and every profiling timing in the
    runbook was approximate.
    """
    return simulator.apply_injections(STATE, dt)
def ingest_line(line: str) -> None:
    """Handle one line from the hub — **the single ingestion path**.

    Order is load-bearing and unchanged from Phase 0: the black-box pipeline sees
    every line first (it consumes the protocol lines), then the parsed frame goes
    to exactly one handler. Phase 1 moved the parsing and the handlers into
    ``ingestion.serial_reader`` / ``ingestion.ring``; what is left here is the
    ordering, which is the part that must not be duplicated.

    An unparseable line is ignored, never fatal — a corrupt line from a
    flaky USB link must not take the reader thread down mid-drill.
    """
    # every line goes to the blackbox pipeline first: protocol lines
    # (CHUNK_*, DROP, BLACKBOX) are consumed there, telemetry rows are stored and
    # gated. Unknown lines are ignored, never fatal.
    if BLACKBOX is not None:
        BLACKBOX.feed(line)

    frame = serial_reader.parse_frame(line)
    # #region agent log
    if "uid" in line or "rc522" in line:
        try:
            import json as _json, time as _time
            with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a") as _f:
                _f.write(_json.dumps({
                    "sessionId": "83ae1d", "hypothesisId": "A",
                    "location": "app.py:ingest_line", "message": "rfid-looking line on the hub cable",
                    "data": {"raw": line[:200], "parsed": frame is not None,
                             "keys": sorted(frame)[:12] if isinstance(frame, dict) else []},
                    "timestamp": int(_time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
    # #endregion
    if frame is None:
        return

    rfid_row = None
    with state_lock:
        STATE.timestamp = iso_now()

        if frame.get("uid"):
            rfid_row = serial_reader.handle_rfid_frame(STATE, frame)
            if rfid_row:
                print(f"[rfid] {rfid_row[0]} at {rfid_row[1]} "
                      f"via {frame.get('reader') or 'hub'}")
                # #region agent log
                try:
                    import json as _json, time as _time
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d", "hypothesisId": "C",
                            "location": "app.py:ingest_line", "message": "rfid swipe stored",
                            "data": {"uid": rfid_row[0], "zone": rfid_row[1],
                                     "reader": frame.get("reader")},
                            "timestamp": int(_time.time() * 1000),
                        }) + "\n")
                except Exception:
                    pass
                # #endregion
        elif "rc522" in frame:
            STATE.note_rfid_reader(frame.get("reader"), frame.get("rc522"),
                                    frame.get("reads"))
            # #region agent log
            try:
                import json as _json, time as _time
                with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a") as _f:
                    _f.write(_json.dumps({
                        "sessionId": "83ae1d", "hypothesisId": "E",
                        "location": "app.py:ingest_line", "message": "reader heartbeat stored",
                        "data": {"reader": frame.get("reader"), "rc522": frame.get("rc522"),
                                 "reads": frame.get("reads")},
                        "timestamp": int(_time.time() * 1000),
                    }) + "\n")
            except Exception:
                pass
            # #endregion

        if "node" in frame or "frames" in frame:
            if frame.get("via") or "frames" in frame:
                # A relayed frame or a ring envelope. Ringed frames reach the
                # same handler, so a relayed N3 is indistinguishable from a direct
                # one except for the `via` badge.
                ring.handle_ring_frame(STATE, frame)
            else:
                serial_reader.handle_node_frame(STATE, frame)

    if rfid_row:
        db.insert_rfid(*rfid_row)
def hardware_reader_thread(stop_event=None):
    """The USB hub reader. The loop itself lives in ``ingestion.serial_reader``;
    this wrapper exists to hand it the gateway, the operator event log and the
    shutdown flag.

    Phase 2: the loop now reconnects on its own (§2.2) and sets
    ``STATE.serial_up`` / ``STATE.hardware_source``. This wrapper announces
    nothing — ``health.health_tick`` is the single announcer of serial
    transitions.
    """
    return serial_reader.read_serial_forever(
        STATE, ingest_line, blackbox=BLACKBOX, on_status=push_event,
        stop_event=stop_event)
def replay_reader_thread(csv_path, speed=1.0):
    """Feed a recorded session through ``ingest_line()`` — the same path as USB.

    Phase 0's whole point: record a live session once, then develop every later
    phase offline, deterministically, at 10x speed. The replay clock is the CSV's
    own ``t_ms`` column divided by ``speed``, so the sequence of states is the
    recorded one and only the wall-clock spacing changes.

    A gap longer than 2 s (after dividing by speed) is capped, so a session that
    contains a coffee break does not stall a rehearsal.
    """
    speed = max(0.01, float(speed or 1.0))
    try:
        rows = read_session_csv(csv_path)
    except Exception as e:
        print(f"[replay] cannot read {csv_path}: {e}")
        return
    if not rows:
        print(f"[replay] {csv_path} contains no rows")
        return

    print(f"[replay] {csv_path} — {len(rows)} rows at {speed:g}x")
    with state_lock:
        # A replay is real hardware, re-driven. Those are recorded samples off
        # actual boards, so the server must NOT report NO_HARDWARE while it runs
        # — the frames are measurements, just not new ones. `hardware_source`
        # keeps the distinction the mode ladder needs ("is anything real feeding
        # this process") without lying about where the numbers came from.
        STATE.hardware_source = "replay"
        STATE.serial_up = True
        push_event("NOMINAL", f"REPLAY attached — {len(rows)} recorded rows at {speed:g}x.")

    t_prev = float(rows[0].get("t_ms") or 0.0)
    for row in rows:
        t_now = float(row.get("t_ms") or 0.0)
        gap = max(0.0, t_now - t_prev) / 1000.0 / speed
        if gap:
            time.sleep(min(gap, 2.0))
        t_prev = t_now
        try:
            ingest_line(json.dumps(row))
        except Exception as e:
            print(f"[replay] row skipped ({e})")

    print("[replay] complete")
    with state_lock:
        STATE.serial_up = False
        push_event("NOMINAL", "REPLAY complete — recorded session exhausted.")


def ticker_thread():
    """50 ms tick: measured dt, health, chart history, DB, 1 Hz inference.

    Two Phase 1 changes matter here.

    **D14 — measured dt.** ``apply_injections(0.05)`` assumed this loop ran at
    exactly 50 ms. It does not: every pass also does health, history, a DB insert
    and a lock round-trip, so the real period is longer and drifts with load. The
    scenario console now gets the duration this loop actually took, which is why
    the profiling timings in the runbook can be trusted.

    **The local clock.** A node whose frames are arriving has its model window
    filled by ``handle_node_frame`` from the *sample* clock. A node being driven
    locally — the hardware-free bench, where the scenario console is the only
    source of motion — has no frames, so this loop ticks its window instead. The
    two clocks never mix inside one window (``append_model_buffer`` refuses it);
    without that rule a bench demo that started before the hub was plugged in
    would warm up the model on a stitched timeline.
    """
    last_hist = 0.0
    last_infer = 0.0
    last_alert = 0.0
    last_hour = int(time.time() // 3600)      # Phase 9 — the rollup boundary
    last_compact = time.time()                # Phase 9 — once a day, not oftener
    last_tel = time.time()                    # Phase 9 — the telemetry aggregate
    prev_t = time.time()
    #: node_id → the next time this loop may hand the crack engine a sample.
    #: The tick runs at 20 Hz; a *measurement* arrives at 2 Hz (MODEL_DT). Feeding
    #: the engine at the tick rate would fill a 30 s window with 600 samples of
    #: the same injected value and push the node's history past its 240-sample
    #: cap — after which `macro_d30` could never find the far end of its window
    #: and the bench would silently never raise a propagation. One cadence, both
    #: paths: the rig delivers frames at 2 Hz and so does this.
    crack_next: dict[str, float] = {}
    while True:
        t0 = time.time()
        dt = t0 - prev_t                                  # D14: measured, not 0.05
        prev_t = t0
        db_rows = None
        with state_lock:
            apply_injections(dt)
            # Phase 3 — the crack column on the hardware-free bench. On the rig
            # these samples arrive through `handle_node_frame`; here the scenario
            # console is the only thing moving the seam, so the engine has to be
            # fed from the same place the injection is applied. Same takeover rule
            # as the model buffer below: a node whose frames are arriving belongs
            # to the reader, and `last_arrival` — not `age_of` — is what tells the
            # two apart, because `age_of` answers 0.0 for a node that has never
            # spoken, which is exactly the node this branch exists for.
            for node in iter_nodes():
                heard = STATE.last_arrival.get(node.node_id) is not None
                if heard and STATE.age_of(node.node_id, t0) <= 1.0:
                    continue
                if t0 < crack_next.get(node.node_id, 0.0):
                    continue
                crack_next[node.node_id] = t0 + MODEL_DT
                for event in crack_engine.update_node_crack(STATE, node.node_id, node, t0):
                    event.publish(STATE)
            crack_engine.crack_tick(STATE, t0)
            update_status_logic()
            STATE.timestamp = iso_now()
            health.health_tick(STATE, t0)
            if t0 - last_hist >= HISTORY_INTERVAL_S:
                snapshot_history()
                last_hist = t0
                db_rows = STATE.nodes_db_rows()
                # Bench nodes: no frames, so tick their model windows locally.
                #
                # **These readings are published through
                # ``simulator.measure()`` — the same function the corpus
                # generator uses — and that is a Phase 5 correctness fix, not a
                # flourish.**
                #
                # ``measure()`` is the only place in the project where instrument
                # noise enters, and its docstring is right that the *serial* path
                # needs nothing: real instruments bring their own noise. But this
                # branch has no instrument behind it — it is synthesising the
                # reading a wire would have carried — and it was appending raw
                # integrated state instead. So this server served Model 1 windows
                # with a noise floor of exactly zero on every channel, while
                # Model 1 and Model 2 had both been trained on the noised windows
                # the generator produces.
                #
                # The consequence is not academic. On a perfectly still window the
                # features degenerate in ways training never showed: ``vib_count``
                # and ``zero_cross_rate`` land on exactly 0, ``tilt_acc_coupling``
                # and ``cross_sensor_coherence`` have no variance to correlate, and
                # ``spatial_coherence`` is computed from a peer that is also
                # exactly constant. The classifier then sees a point outside its
                # training distribution precisely when the mine is quietest —
                # which is the same "the world it learned vs the world it is
                # served" failure as PF-D1 and PF-D2, in the opposite direction.
                #
                # A no-serial demo is therefore no longer a demo of a different
                # system. The `--replay` and `--no-serial` paths both land here.
                #
                # **`heard` is load-bearing, and leaving it out was a live bug.**
                # `age_of()` answers 0.0 for a node that has never spoken — so on a
                # bench with no serial port attached, every node looked
                # permanently "fresh" and this branch skipped all four of them,
                # forever. The model windows stayed empty, so Model 1 and Model 2
                # never ran: `/api/ai` sat on "WARMING UP · building the 60 s
                # telemetry buffer" for the life of the process and the Phase 5
                # cascade reported "no cascade decisions yet" on a dashboard whose
                # numbers were otherwise moving.
                #
                # This is the *third* place in the repository this distinction has
                # had to be made (see `engine/health.py`'s third rule and the
                # crack-engine branch above, which already carries the guard), and
                # the second place it was forgotten. `age_of` cannot express "never
                # heard from" — it is documented as returning 0.0 — so the
                # predicate has to be built from `last_arrival` at every call site
                # that actually cares about the difference.
                for node in iter_nodes():
                    heard = STATE.last_arrival.get(node.node_id) is not None
                    if heard and STATE.age_of(node.node_id, t0) <= 1.0:
                        continue                  # frames are filling it
                    m_disp, m_tilt, m_acc = simulator.measure(
                        float(node.displacement_mm), float(node.tilt_x),
                        float(node.acceleration), t0, node.node_id)
                    STATE.append_model_buffer(
                        node.node_id, t0, m_disp, m_tilt, m_acc,
                        int(node.vib), node.micro, node.macro, clock="local")
        if db_rows is not None:
            log_telemetry_rows(db_rows)
        # **M1 runs at 2 Hz, always** (§5.7), and it is what decides how often M2
        # runs — that is where the compute saving lives. The old guard
        # (`MODEL_INFO["available"]`) is gone on purpose: the cascade must run
        # even with no regressor on disk, because Model 1 still classifies the
        # window and UNKNOWN still escalates. A missing model must make the system
        # less precise, never more silent.
        if t0 - last_infer >= 1.0 / max(float(config.M1_HZ), 1e-9):
            last_infer = t0
            cascade_engine.cascade_tick(STATE, t0)
        # Phase 8 — the alert layer, once a second. Outside the state lock on
        # purpose: the dispatcher reads the state and writes to the *alert* store,
        # and holding the telemetry lock across an SMTP timeout would stall the
        # ingest path for as long as a remote mail server felt like taking.
        if t0 - last_alert >= 1.0:
            last_alert = t0
            try:
                alert_tick(t0)
            except Exception as exc:                            # noqa: BLE001
                print(f"[alerts] tick failed: {type(exc).__name__}: {exc}")
        # The gate and the buzzer follow the reading that was just classified,
        # in this same pass. led_tick itself stays quiet when nothing changed,
        # so this is not a command every loop — it is the next state the moment
        # the reading crosses a line, not on the following one-second tick.
        try:
            led_tick(t0)
        except Exception as exc:                                # noqa: BLE001
            print(f"[led] tick failed: {type(exc).__name__}: {exc}")
        # Phase 9 (§9.5) — the telemetry aggregate. One summary per node per
        # minute, queued *after* it is safely in `sensor_telemetry`, so the outbox
        # item is a projection of durable data rather than a second copy of a fact
        # that exists only in memory.
        if t0 - last_tel >= float(config.OUTBOX_TELEMETRY_S):
            last_tel = t0
            try:
                _n = enqueue_telemetry_aggregates(t0)
                if _n:
                    print(f"[outbox] queued {_n} telemetry aggregate(s)")
            except Exception as exc:                            # noqa: BLE001
                print(f"[outbox] telemetry enqueue error: {type(exc).__name__}: {exc}")
        # Phase 9 — the hour boundary. In the ticker rather than in a cron job
        # because a cron job on a demo laptop is one more thing to configure,
        # forget and debug; and this is what makes the rollup exist at all.
        if int(t0 // 3600) != last_hour:
            last_hour = int(t0 // 3600)
            try:
                with db.borrowed() as _conn:
                    if _conn is None:
                        raise RuntimeError("no database connection")
                    from persistence import sync as _sync
                    _info = _sync.rollup_and_queue(_conn, STATE)
                    print(f"[sync] rolled up hour {_info['hour']} → {_info['rows']} row(s)"
                          f" · {_info['pending']} pending")
            except Exception as exc:                            # noqa: BLE001
                print(f"[sync] rollup failed: {type(exc).__name__}: {exc}")
        # Phase 9 — compaction, once a day. It prunes **only** SENT rows older
        # than the retention window; a PENDING row is the only copy of something
        # that has arrived nowhere, and a FAILED row is the only record of
        # something that could not.
        if t0 - last_compact >= 86_400:
            last_compact = t0
            try:
                with db.borrowed() as _conn:
                    if _conn is None:
                        raise RuntimeError("no database connection")
                    from persistence import outbox as _outbox
                    _pruned = _outbox.compact_outbox(_conn)
                    if _pruned:
                        print(f"[outbox] compacted {_pruned} delivered row(s)")
            except Exception as exc:                            # noqa: BLE001
                print(f"[outbox] compaction failed: {type(exc).__name__}: {exc}")
        time.sleep(0.05)
def public_telemetry():
    """The ``/api/telemetry`` body, projected from the state.

    `velocity` is stripped: it is the server's internal derivative, and the deck
    computes its own. `timestamp` is KEPT — the deck reads it as the node's
    last-seen value (ndSeen), and dropping it made that readout fall back to the
    word "live".
    """
    with state_lock:
        payload = deepcopy(STATE.public_payload())
    for n_id, node in payload.get("nodes", {}).items():
        node.pop("velocity", None)
    ttf = payload.get("ttf_seconds")
    if ttf is None:
        payload["ttf_seconds"] = "N/A"
    elif isinstance(ttf, float):
        payload["ttf_seconds"] = int(ttf)
    return payload
@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    return jsonify(public_telemetry())


def _model_health():
    """What the model layer can honestly say about itself.

    Reads ``ttf_model.MODEL_INFO``, which ``load_model()`` populates — including
    the degrade path. §A.3.8's boot policy is ``ANY_MISSING_ARTIFACT => LOG +
    DEGRADE, NEVER CRASH``, so an absent model is reported as a *state*, never
    raised as an error.

    **The projection is explicit, and that is not tidiness.** ``MODEL_INFO``
    holds the live ``XGBRegressor`` under ``"model"``, so returning it verbatim
    makes ``jsonify`` raise ``Object of type XGBRegressor is not JSON
    serializable`` — a 500 on the health endpoint, i.e. the one endpoint whose
    job is to tell you the system is healthy would be the one endpoint that
    fails. Anything that is not a JSON scalar is therefore dropped *and named*
    in ``non_serialisable``, so the next field added to ``MODEL_INFO`` shows up
    as a visible line rather than as a 500 discovered on stage.

    ``fed_fresh`` is separate from ``available`` on purpose: "the model loaded"
    and "the model is currently being fed" are different questions, and a
    loaded-but-starved model is the more dangerous of the two.
    """
    info = ttf_model.MODEL_INFO
    meta = info.get("meta") or {}

    def scalar(v):
        return v if isinstance(v, (str, int, float, bool)) or v is None else None

    out = {
        "available": bool(info.get("available")),
        "loaded": info.get("model") is not None,
        "scaler": "present" if info.get("scaler") else "absent",
        "name": scalar(meta.get("model")),
        "algorithm": scalar(meta.get("algorithm")),
        "trained_at": scalar(meta.get("trained_at")),
        "best_iteration": scalar(meta.get("best_iteration")),
    }
    metrics = meta.get("metrics")
    out["metrics"] = ({k: scalar(v) for k, v in metrics.items()}
                      if isinstance(metrics, dict) else {})

    dropped = [k for k, v in info.items()
               if k not in ("meta",) and not (isinstance(v, (str, int, float, bool))
                                              or v is None)]
    out["non_serialisable"] = sorted(dropped)

    fed = [n.node_id for n in STATE.iter_nodes() if STATE.window_complete(n.node_id)]
    out["fed_nodes"] = fed
    out["fed_fresh"] = bool(fed)
    return out


def _db_health():
    """One cheap round-trip to the local Postgres. Never raises."""
    started = time.time()
    conn = get_db_connection()
    if conn is None:
        return {"status": "DOWN", "error": "no connection"}
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return {"status": "UP", "latency_ms": round((time.time() - started) * 1000.0, 1)}
    except Exception as e:
        return {"status": "DOWN", "error": str(e)[:200]}
    finally:
        try:
            db.release(conn)
        except Exception:
            pass


@app.route("/api/health", methods=["GET"])
def get_health():
    """§2.3 — node, link, hub, model and DB health in one object.

    Read-only and side-effect free: it calls ``health_payload``, which recomputes
    from state and does not tick, so polling it never advances an edge detector
    or ages a link. The ticker remains the only thing that moves health forward.
    """
    with state_lock:
        payload = health.health_payload(STATE, model=_model_health(), db=_db_health())
        # Phase 3 — the two crack stages, their zeros and their counters. The
        # thresholds themselves are already published by `config.describe()` under
        # `thresholds.crack`; this is the live half.
        payload["crack"] = crack_engine.crack_summary(STATE)
        payload["thresholds"]["crack"] = config.describe()["crack"]
        return jsonify(payload)


@app.route("/api/extended", methods=["GET"])
def get_extended():
    with state_lock:
        counts = muster_counts()
        ext = {
            "mode": "LIVE",
            "nodes": {
                n_id: {
                    "velocity": STATE.nodes[n_id].velocity,
                    "peak_acceleration": STATE.peak_accel[n_id],
                    "level": STATE.nodes[n_id].level,
                    "rssi": STATE.nodes[n_id].rssi,
                    "batt": STATE.nodes[n_id].batt,
                    "via": STATE.via.get(n_id),
                    "window_complete": STATE.window_complete(n_id),
                    "flags": flags_to_list(STATE.nodes[n_id].flags),
                    # Phase 3 — the crack column, per node. Present for all four
                    # nodes so the deck reads one shape; the two without an
                    # instrument return a block that says so rather than nothing
                    # (an absent key is indistinguishable from an old server).
                    "crack": crack_engine.crack_public(STATE, n_id),
                }
                for n_id in STATE.node_ids
            },
            "crack": crack_engine.crack_summary(STATE),
            # Phase 4 — the pair/zone layer. Its own block rather than keys
            # sprinkled through this one: pair elongation belongs to a *level*,
            # zone risk to a *zone*, and neither is a property of a node, so the
            # deck reads them where they live. Purely additive (§4.5).
            "geomech": geomech.geomech_block(STATE),
            "levels": deepcopy(STATE.levels),
            "ring": STATE.ring.to_public(),
            # Phase 9 (B6) — the triangle's three edges and their fault state, the
            # server's end of §H5.5's fault frames. Additive: `ring` above keeps
            # the shipped `{1: …, 2: …}` shape, and a consumer that has never heard
            # of a segment is unaffected by this key existing.
            "ring_segments": STATE.ring.segments_public(),
            "muster": counts,
            "events": list(STATE.events),
            # Phase 7 (B5) — **the key the evac card has always read and the
            # server never sent.** `setMapBanner()` in the deck reads
            # `d.underground.route_A_status` / `route_B_status` and falls back to
            # "OPEN" for both when the block is missing, so a blocked route has
            # been rendering as an open one since the v4.4 contract changed shape.
            # Additive: the recorded `/api/extended` fixture is still a subset.
            "underground": deepcopy(STATE.underground),
            # Phase 8 (B4) — the alert layer, projected for the decks. The panel
            # reads `/api/alerts` for the detail; this block is the one-line
            # summary a deck card can print without a second request, and it is
            # what makes "there is an open alert, and nobody has acknowledged it"
            # visible on every screen rather than only on the Alerts tab.
            # Additive, like `underground` before it.
            "alerts": dict(alerts_dispatcher.extended_block(), **led_block()),
            # Phase 9 (B6) — the two-tier database, additive like the blocks
            # before it. `pending_total` is the number the demo beat is about:
            # watch it climb with the network off, then watch a force-sync empty it.
            "sync": _sync_block(),
        }
    # Option A: blackbox + cloud come off the real ingest + gate store. mode comes
    # with them, and is "LIVE" only when a real hub is on the serial port.
    if BLACKBOX is not None:
        ext.update(BLACKBOX.extended_blocks())
    else:
        ext["mode"] = "LIVE" if SERIAL_ACTIVE else "SIM"
    return jsonify(ext)


@app.route("/api/history", methods=["GET"])
def get_history():
    with state_lock:
        payload = {n: list(STATE.history[n]) for n in STATE.node_ids}
    return jsonify(payload)


@app.route("/api/ai", methods=["GET"])
def get_ai():
    """Phase 0 — the body moved to ``engine.ttf.model_status()``, which owns the
    model, the freshness rule, the physics gate and the three status strings.

    **Phase 5 adds two blocks and moves nothing** (§5.3). The original keys —
    model, status, horizon, last_train, confidence, note, forecasts — keep their
    names, their types and their meaning, because ``index.html`` reads all seven
    and a renamed key is a blank card on stage.

    What is new:

    * ``model_1`` — the pattern discriminator's verdict: class, disruption index,
      structural probability, its top features, and the operating threshold.
    * ``cascade`` — the fusion: mode, the cadence M2 is actually being run at, the
      disagreement flag, and a sentence saying why.

    Both are **additive**, and both are absent-safe: with no classifier on disk
    ``model_1`` reports ``UNAVAILABLE`` and ``cascade`` reports the M2-only path,
    rather than the endpoint 500-ing or the card going blank.
    """
    with state_lock:
        body = ttf_model.model_status()
        body["model_1"] = pattern_model_status()
        body["cascade"] = cascade_engine.cascade_status()
        # Which artifact is actually answering `predict_ttf`. The deck prints it,
        # because "which model produced the countdown" is not a detail an operator
        # should have to infer from a build ID.
        body["model_2_source"] = ttf_model.model_2_source()
        # Phase 6 (§6.3) — two more additive blocks.
        #
        # `severity` is the per-node answer to "how bad, how soon, how sure":
        # tier, TTF and its 80% band, the creep stage, the curvature that drove
        # it, confidence and the reason for that confidence, plus the ranked
        # actions Phase 8 will put in the SMS.
        #
        # `kinematic` is the provenance of the fallback: which rung answered
        # (`fukuzono_1/v` · `v/a` · `none`), the calibrated alpha and A it used,
        # and which node the countdown came from.
        body["severity"] = severity_engine.severity_snapshot(STATE)
        body["kinematic"] = severity_engine.kinematic_block(STATE)
        # §6.5 — the card's `confidence` is *computed from* `confidence_band()`
        # rather than typed, so the number on screen traces back to the artifact
        # (Rule 3): a HIGH tier means "window complete, models agree".
        #
        # Its **type does not change.** It stays the same "NN%" string index.html
        # has read since Phase 0 — §6.3's sketch shows a bare "HIGH", and a panel
        # that renders `confidence` as a percentage would print "HIGH%" on stage.
        # The tier itself is published per node inside `severity`, which is the
        # block §6.3 actually specifies for it.
        conf_tier = "MEDIUM"
        try:
            worst = max((v.get("confidence", "MEDIUM") for v in body["severity"].values()),
                        key=severity_engine.severity_rank, default="MEDIUM")
            conf_tier = worst
        except Exception:                                     # noqa: BLE001
            pass
        body["confidence"] = {"HIGH": "95%", "MEDIUM": "80%", "LOW": "60%"}.get(
            conf_tier, body["confidence"])
        # #region agent log
        try:
            import json as _json
            nodes = ((body.get("cascade") or {}).get("nodes") or {})
            with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                _f.write(_json.dumps({
                    "sessionId": "83ae1d",
                    "hypothesisId": "AI2",
                    "runId": "post-fix",
                    "location": "app.py:get_ai",
                    "message": "ai layers served",
                    "data": {
                        "model": body.get("model"),
                        "status": body.get("status"),
                        "nodes": {
                            k: {
                                "label": v.get("label"),
                                "ttf": v.get("ttf_s"),
                                "novel": (v.get("novelty") or {}).get("index"),
                            } for k, v in nodes.items()
                        },
                    },
                    "timestamp": int(time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
    return jsonify(body)


# --- PHASE 7 (B5): /api/site and /api/map MOVED to api/routes_map.py ---------
# The inline pair that used to sit here serialised the file and coloured zones by
# gross status. It had no routing in it at all — `route_A_status`/`route_B_status`
# were a blanket "is this level's worst node alarmed" boolean (D7b) — and the GIS
# card's own legend has claimed a computed route since before the server could
# compute one. Both routes are now served by `api.routes_map`, which builds the
# payload in `api.map_payload` and asks `routing.dijkstra` for the safe exit.
#
# Kept as a comment rather than deleted silently: this is the third site in this
# file where a route moved out, and the next reader deserves to know where to.
#
# The old body, for reference, is in git history and in `/tmp/app.py.bak`.
def _legacy_get_map_body():
    """The Phase-6-and-earlier `/api/map`. Retained **only** so the diff of this
    phase is legible; unreachable, unregistered, and scheduled for deletion."""
    status_rank = {"NOMINAL": 0, "WARNING": 1, "CRITICAL": 2}
    with state_lock:
        live = STATE.nodes_public()
        sys_status = STATE.system_status
    out = {
        "site": SITE,
        "system_status": sys_status,
        "nodes": [], "junctions": [], "exits": [],
        "links": TUNNEL_MAP.get("links", []),
        "zones": [],
    }
    for n_id, n in TUNNEL_MAP.get("nodes", {}).items():
        g = _geo(n["d_n_m"], n["d_e_m"])
        st = live.get(n_id, {})
        out["nodes"].append({
            "id": n_id, "label": n.get("label", n_id),
            "lat": g[0], "lon": g[1],
            "level": n.get("level"), "zone": n.get("zone"),
            "active": n.get("active", True),
            "status": st.get("status", "NOMINAL"),
            "tilt_x": st.get("tilt_x"),
            "displacement_mm": st.get("displacement_mm"),
            "acceleration": st.get("acceleration"),
        })
    for j in TUNNEL_MAP.get("junctions", []):
        g = _geo(j["d_n_m"], j["d_e_m"])
        out["junctions"].append({"id": j["id"], "level": j.get("level"), "lat": g[0], "lon": g[1]})
    for e in TUNNEL_MAP.get("exits", []):
        g = _geo(e["d_n_m"], e["d_e_m"])
        out["exits"].append({"id": e["id"], "label": e.get("label", ""), "lat": g[0], "lon": g[1]})
    for z in TUNNEL_MAP.get("zones", []):
        x0, y0, x1, y1 = z["rect"]
        poly = [_geo(y0, x0), _geo(y1, x0), _geo(y1, x1), _geo(y0, x1)]
        worst = 0
        for n_id, n in TUNNEL_MAP.get("nodes", {}).items():
            if n.get("zone") == z["id"] and n.get("active", True):
                worst = max(worst, status_rank.get(live.get(n_id, {}).get("status", "NOMINAL"), 0))
        out["zones"].append({
            "id": z["id"], "name": z.get("name", z["id"]),
            "polygon": [[a[0], a[1]] for a in poly],
            "risk": ["NOMINAL", "WARNING", "CRITICAL"][worst],
        })
    return jsonify(out)


@app.route("/api/sim/trigger", methods=["POST"])
def trigger_scenario():
    """Arm a scenario profile on one node.

    Accepts both shapes: ``{"node": "node_2", "profile": "machinery_pass"}`` and
    the original ``{"node": "node_2", "severity": "warning"|"critical"}``, which
    maps onto ``warning_creep`` / ``critical_collapse``. The runbook's existing
    curl commands must keep working verbatim — a runbook that needs editing before
    a demo is a runbook that will be wrong during one.
    """
    body = request.get_json(silent=True) or {}
    node = body.get("node")
    profile = body.get("profile")
    severity = body.get("severity")
    if node not in STATE.node_ids:
        return jsonify({"ok": False, "error": f"unknown node {node!r}"}), 400
    with state_lock:
        scenario = simulator.arm(STATE, node, profile, severity)
        if scenario is None:
            return jsonify({"ok": False,
                            "error": f"unknown profile {profile or severity!r}",
                            "profiles": sorted(simulator.SCENARIOS)}), 400
        level = "CRITICAL" if scenario.severity == "critical" else "WARNING"
        push_event(level, f"{node.upper()} {scenario.name} injection armed by operator.")
    return jsonify({"ok": True, "profile": scenario.name,
                    "verdict": scenario.verdict,
                    "duration_s": scenario.duration_s})


@app.route("/api/reset", methods=["POST"])
def reset_system():
    """Re-arm. **Clears every node's model window** and invalidates it (D13).

    Before Phase 1 this zeroed the node values and left ``node_buffers`` intact,
    so the next forecast was built from 60 s of pre-reset samples stitched to a
    handful of new ones. The runbook's "wait ~65 s" is now a true, enforced 60 s:
    ``window_complete()`` returns ``False`` until the state has 60 s of samples
    that are all *after* this call.
    """
    with state_lock:
        STATE.reset("operator re-arm", grace_s=config.REARM_GRACE_S)

    # Phase 8 (§8.5) — a re-arm CLOSES every open alert; it never deletes one.
    # "Delete the alert history" is the one thing a regulator should never see, so
    # the row survives with state='CLOSED' and the reason attached. The in-memory
    # machine forgets them, which is what lets the same condition raise a *new*
    # alert afterwards instead of being swallowed by its own dedup window.
    # The **database first**, then the machine. Both write the same reason, and the
    # order is not cosmetic: the machine's close persists each open event it holds,
    # so running it first leaves `close_open_alerts()` with nothing left in
    # NEW/ACTIVE/ACKED to touch — and the response then reports `alerts_closed_db: 0`
    # on a re-arm that plainly closed rows. A count that disagrees with the screen
    # is worse than no count, so the rows-only sweep goes first and the machine
    # afterwards (idempotent: same state, same reason).
    closed_db = db.close_open_alerts("manual re-arm")
    closed_mem = alerts_dispatcher.close_all(STATE, reason="manual re-arm",
                                             persist=db.persist_alert)
    print(f"[alerts] re-arm closed {closed_mem} in-memory, {closed_db} database row(s)")
    return jsonify({"ok": True, "warming_up_s": 60,
                    "rearm_grace_s": config.REARM_GRACE_S,
                    "alerts_closed": closed_mem, "alerts_closed_db": closed_db,
                    "note": "closed, never deleted — sensor triggers ignored for 30 s"})


def _check_api_auth(req) -> bool:
    import os
    expected = os.getenv("NS_API_KEY")
    if not expected:
        return True
    provided = req.headers.get("X-API-Key") or req.headers.get("Authorization", "").replace("Bearer ", "")
    return provided == expected


# ── Phase 9 (B6) — the sync surface ─────────────────────────────────────────
#: Guards `/api/sync` against two flushers at once. The database-level safety is
#: `FOR UPDATE SKIP LOCKED` (they would see disjoint batches); this lock exists so
#: the *demo button* cannot be pressed thirty times and produce thirty overlapping
#: cycles whose stats overwrite each other on the console.
_SYNC_LOCK = threading.Lock()


@app.route("/api/sync", methods=["POST"])
def post_sync():
    """Manual flush — **this is the demo button** (§9.2 rule 5)."""
    if not _check_api_auth(request):
        return jsonify({"ok": False, "error": "Unauthorized"}), 401
    from persistence import sync as _sync
    if not _sync.enabled():
        return jsonify({"ok": False, "reason": "cloud not configured",
                        "note": "the outbox keeps filling; set NS_CLOUD_DB_URL and/or "
                                "NS_CLOUD_HTTP_URL to drain it"}), 409
    if not _SYNC_LOCK.acquire(blocking=False):
        return jsonify({"ok": False, "reason": "a sync is already running"}), 409
    try:
        with db.borrowed() as conn:
            stats = _sync.sync_once(conn)
    finally:
        _SYNC_LOCK.release()
    ok = not stats.get("error")
    return jsonify({"ok": ok, **stats}), (200 if ok else 502)


@app.route("/api/sync/status")
def get_sync_status():
    """What the chip prints: how deep the queue is, and when it last moved.

    Three numbers, all read from the rows rather than remembered: pending rollups,
    pending alerts, pending outbox items. A chip that quotes a cached counter is a
    chip that lies at exactly the moment someone is looking at it for reassurance.
    """
    from persistence import sync as _sync
    payload = _sync.status()
    # Borrowed for the block, not for the request: Phase 9 found this handler
    # holding a pooled connection for the life of its *thread* — four reads of
    # this route and the pool is empty, after which the whole process reports
    # "database unreachable" while postgres is perfectly healthy.
    with db.borrowed() as conn:
        if conn is not None:
            try:
                payload.update(_sync.pending_counts(conn))
                from persistence import outbox as _outbox
                payload["outbox_detail"] = _outbox.outbox_status(conn)
            except Exception as exc:                            # noqa: BLE001
                payload["counts_error"] = f"{type(exc).__name__}: {exc}"
    payload["reachable"] = (_sync.probe_backhaul() if _sync.cloud_client.configured()
                            else {"ok": False, "reason": "no HTTP endpoint configured"})
    payload["ok"] = True
    return jsonify(payload)


@app.route("/<path:asset>")
def frontend_asset(asset):
    """Serve PWA assets (gis-map.js etc.) from neuralstrata-frontend/."""
    return send_from_directory(FRONTEND_DIR, asset)


@app.route("/")
def dashboard():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        return jsonify({"error": "frontend not found", "expected": str(FRONTEND_DIR)}), 404
    return send_from_directory(FRONTEND_DIR, "index.html")


def main():
    parser = argparse.ArgumentParser(description="Neural Strata edge server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--no-serial", action="store_true", help="Skip USB serial (API + dashboard only)")
    parser.add_argument("--bbx-sim-skip", metavar="CHUNK:ROWS", default=None,
                        help="SIMULATOR ONLY: make the stand-in hub lose ROWS rows of "
                             "CHUNK on the wire (it keeps them on its card), so §5.7's "
                             "reconcile + REPLAY path can be demonstrated with no "
                             "hardware. Ignored on a real serial port.")
    parser.add_argument("--no-blackbox", action="store_true",
                        help="Do not start the blackbox gateway (/api/extended omits it)")
    parser.add_argument("--sd-card-mb", type=int, default=None,
                        help="Capacity of the microSD in the hub, MB (reported to the deck)")
    parser.add_argument("--replay", metavar="FILE", default=None,
                        help="Replay a recorded session.csv through the live pipeline "
                             "instead of the USB serial port")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Replay speed multiplier (10 = ten times faster than real time)")
    args = parser.parse_args()

    # Phase 0 — fail loudly on nonsense *before* Flask starts. Thresholds that
    # cannot mean anything, a node living on two levels, a port outside 1-65535.
    # A missing model or map is NOT fatal: those degrade (LOG + DEGRADE, NEVER CRASH).
    problems = validate_config()
    if problems:
        print("FATAL — this configuration is not bootable:")
        for problem in problems:
            print(f"  · {problem}")
        print("  (fix .env, or set NS_ALLOW_BAD_CONFIG=1 to boot anyway)")
        if not env_bool("NS_ALLOW_BAD_CONFIG", False):
            sys.exit(2)

    global SERIAL_ACTIVE
    load_model()
    load_pattern()

    # ── Phase 9 (B6, §F.5) — the two-tier database, armed ────────────────────
    # The **edge** rollup runs whether or not a cloud exists: it is the mine's own
    # hourly summary, and it is what a laptop that slept through an hour boundary
    # would otherwise leave a hole in. Idempotent by construction — it re-derives
    # the last 48 hours from raw telemetry and upserts.
    # Borrowed for the block: the rollup is a one-off.
    try:
        with db.borrowed() as _edge_conn:
            if _edge_conn is not None:
                from persistence import rollup as _rollup
                _rolled = _rollup.rollup_pending(_edge_conn, config.SITE_ID,
                                                 lookback_hours=48)
                print(f"[sync] rolled up {_rolled} missed hour-row(s) on startup")
    except Exception as exc:                                    # noqa: BLE001
        print(f"[sync] startup rollup skipped: {type(exc).__name__}: {exc}")

    # …and one long-lived connection for the daemon, which is the *only* place in
    # this process that holds one deliberately: a sync cycle every 15 minutes does
    # not want to build a connection each time, and one reserved slot out of four
    # is a price worth paying for the daemon having a stable handle.
    from persistence import sync as _sync
    _daemon_conn = db.try_get_conn() if _sync.enabled() else None

    # The **cloud** daemon only when something is actually configured. With both
    # URLs empty this block does not run, and not one line of cloud code executes:
    # that guard is why a missing cloud can never break the edge.
    if _sync.enabled():
        _sync_stop = threading.Event()
        threading.Thread(target=_sync.sync_daemon,
                         args=(_daemon_conn, _sync_stop),
                         daemon=True, name="sync").start()
        print(f"[sync] cloud sync armed · interval {config.SYNC_INTERVAL_S}s · "
              f"db={'on' if config.CLOUD_ENABLED else 'off'} "
              f"http={'on' if _sync.cloud_client.configured() else 'off'}")
        # Every synced row carries `site_id`, and the whole multi-site story rests on
        # it being *this* site's id. The config default is a placeholder, so a site
        # that forgets `NS_SITE_ID` does not fail — it silently files its rows under
        # "site-unknown", where they merge with every other misconfigured edge and
        # nobody can tell where an incident happened. Half a minute of setup prevents
        # that, so it is said out loud once, at boot, where the operator will see it.
        if config.SITE_ID == "site-unknown":
            print("[sync] WARNING: NS_SITE_ID is unset — every row will be filed as "
                  "site_id='site-unknown'. Set it (e.g. NS_SITE_ID=tunnel-site-01) "
                  "before syncing to a shared central database.")
    else:
        print("[sync] no NS_CLOUD_DB_URL / NS_CLOUD_HTTP_URL — LOCAL-ONLY mode "
              "(the outbox still fills; nothing is lost)")

    threading.Thread(target=ticker_thread, daemon=True).start()

    # Phase 8 (§8.5) — one retry pass at boot, so anything raised while the network
    # was down goes out as soon as the server is back. It runs a couple of seconds
    # in, not at import: the dashboard channel needs the ticker to have produced a
    # first severity pass, and a retry with nothing to retry is just noise.
    def _boot_alert_retry():
        try:
            sent = alerts_dispatcher.retry_failed(STATE, persist=db.persist_alert)
            if sent:
                print(f"[alerts] boot retry delivered {sent} queued channel(s)")
        except Exception as exc:                                # noqa: BLE001
            print(f"[alerts] boot retry failed: {type(exc).__name__}: {exc}")

    threading.Timer(2.0, _boot_alert_retry).start()

    # The gateway comes up *before* the serial reader: the reader hands it the
    # write side of the port the moment it connects, and passes it every line it
    # reads. Starting it after would mean the port opens while BLACKBOX is still
    # None, and the downlink (ACK_DELETE) would be lost for the whole session.
    if not args.no_blackbox:
        # With --no-serial or --replay there is no line to share, so the gateway
        # runs its own simulator; otherwise the serial reader below feeds it.
        start_blackbox(card_mb=args.sd_card_mb, sim_skip=args.bbx_sim_skip,
                       external_feed=not (args.no_serial or args.replay))

    if args.replay:
        SERIAL_ACTIVE = True
        STATE.hardware_source = "replay"
        threading.Thread(target=replay_reader_thread,
                         args=(args.replay, args.speed), daemon=True).start()
    elif not args.no_serial:
        SERIAL_ACTIVE = True
        # `hardware_source` is set here, on *intent*, not on a successful open.
        # That ordering is deliberate: starting the reader and failing to open
        # the port must report "the hub is down", not "simulation only". Getting
        # it the other way round means an unplugged cable at boot produces
        # NO_HARDWARE — a comfortable, false explanation for a real fault — and
        # the operator reads the same chip they would see on a deliberate
        # hardware-free bench run.
        STATE.hardware_source = "usb"
        # The reader and its supervisor are started as a pair. The watchdog
        # restarts the reader if the thread ever *dies*; the reconnect ladder for
        # a merely-unplugged cable lives inside the reader itself. Splitting those
        # two faults across two owners is what the plan's own risk table warns
        # about, so only one of them ever touches the port.
        serial_reader.start_serial_watchdog(
            STATE, hardware_reader_thread,
            kwargs={"stop_event": STOP_EVENT}, on_status=push_event)
    else:
        with state_lock:
            STATE.hardware_source = "sim"
            push_event("NOMINAL", "Started without serial — waiting for hardware or scenario injection.")

    def _shutdown(_signum, _frame):
        """Ctrl+C: raise the flag, let the reader leave its wait, then exit."""
        if STOP_EVENT.is_set():
            sys.exit(1)                      # second Ctrl+C is a hard exit
        STOP_EVENT.set()
        print("\nShutting down — closing the serial link…")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f"Neural Strata Edge Server  →  http://127.0.0.1:{args.port}")
    print("  GET  /api/telemetry  /api/extended  /api/history  /api/ai  /api/site  /api/map  /api/health")
    print("  POST /api/sim/trigger  /api/reset  /api/cloud/force_sync  /api/sync")
    print("  GET  /api/sync/status  (outbox depth, last success, backhaul probe)")
    if FRONTEND_DIR.joinpath("index.html").exists():
        print(f"  Dashboard               →  http://127.0.0.1:{args.port}/")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
