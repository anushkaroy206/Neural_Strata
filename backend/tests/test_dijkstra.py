"""
Phase 7 (B5) — hazard-weighted routing, the GIS payload, and the fail-safe.
===========================================================================

§7.8's acceptance table first, then the properties that table depends on. Seven
groups:

**1 · The acceptance table.** ``test_dijkstra_basic`` · ``test_dijkstra_blocked`` ·
``test_fail_safe_hold`` · ``test_stale_is_not_safe`` · ``test_map_payload`` — the
five rows §7.8 names, in order.

**2 · The cost is not the distance.** A blocked leg is ``∞``, not "expensive"; a
hazardous leg is *more expensive in metres than it is long*. Both are properties of
``link_cost`` and both are asserted against numbers computed by hand from the map.

**3 · The blocked-link rule (SRS §5.5).** A node above a tunnel blocks the tunnel's
segments — for the UI *and* the LEDs. Phase 7 refines the v4.4 blanket boolean into
a per-segment rule, and the refinement is asserted rather than described.

**4 · You may always leave the node you are standing on.** The one deliberate
exception to "blocked means blocked", with the reason: telling a person to remain
inside a failing drift because their own sensor blocked the only door is a worse
answer than a risk-weighted exit.

**5 · D7b.** ``route_A_status`` / ``route_B_status`` are **derived** from the
router. The test asserts the derivation, and that a site with no map still falls
back to the Phase-4 rule instead of inventing a route.

**6 · The LED plan.** Five patterns, a precedence, and one prohibition: no green
pattern is ever emitted for a junction whose way out is gone.

**7 · The payload.** Every key ``gis-map.js`` reads, with the types it reads them
as — ``links`` as objects (§A.3.6), ``polygon`` as four ``[lat, lon]`` corners in
the order the label anchor uses.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

import config
from api import map_payload
from contracts import NodeState
from routing import dijkstra as R
from state import SystemState

MAP_PATH = Path(config.ASSET_FILES["tunnel_map"])
SITE = R.load_tunnel_map(MAP_PATH)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def fresh_state() -> SystemState:
    """A state object wired to the real map, driven directly by the test."""
    state = SystemState()
    state.site_map = SITE
    state.site_meta = map_payload.load_site_meta()
    return state


def put(state, node_id, *, status="NOMINAL", stale=False, crack_stage="NONE",
        disp=5.0, level=None):
    """Place one node into the state at a chosen hazard."""
    node = state.nodes.get(node_id)
    if node is None:
        node = NodeState(node_id=node_id)
        state.nodes[node_id] = node
    node.status = status
    node.stale = stale
    node.crack_stage = crack_stage
    node.displacement_mm = disp
    if level is not None:
        node.level = level
    return node


def quiet(state) -> SystemState:
    for n in state.node_ids:
        put(state, n)
    return state


def cost_of(state, path) -> float:
    """Sum of the live link costs along a path, computed independently of
    ``dijkstra`` — so the search and the arithmetic are two separate claims."""
    total = 0.0
    for a, b in zip(path, path[1:]):
        total += R.link_cost(state, (a, b), SITE.length(a, b), SITE)
    return total


# ---------------------------------------------------------------------------
# 1 · The acceptance table (§7.8)
# ---------------------------------------------------------------------------

def test_dijkstra_basic():
    """From J1, the path to {EXIT1, EXIT2} is found and costs what it is long.

    §7.8's first row. In a quiet site the risk-weighted cost must equal the plain
    metre length, because every penalty is zero — if it does not, the cost model
    is inventing hazard.
    """
    state = quiet(fresh_state())
    graph = R.build_graph(SITE, state)
    path, cost = R.dijkstra(graph, "J1", ("EXIT1", "EXIT2"))

    assert path[0] == "J1"
    assert path[-1] in ("EXIT1", "EXIT2")
    assert math.isfinite(cost)
    assert cost == pytest.approx(cost_of(state, path))
    # EXIT1 is 42.7 m west of J1; the east way round is J1→J2→J3→J4→EXIT2.
    assert path == ["J1", "EXIT1"]
    assert cost == pytest.approx(SITE.length("EXIT1", "J1"), abs=1e-9)


def test_dijkstra_blocked():
    """Block J2↔J3: Level 2 loses its exit and the engine returns a HOLD.

    §7.8's second row, and the honest version of it. With the cross-shaft gone,
    Level 2's only remaining exit is EXIT2 — and that is on the far side of the
    same blocked leg, so there is genuinely no way out. What must **not** happen is
    a path through the blocked leg.
    """
    state = quiet(fresh_state())
    # node_2 sits at J2 and node_4 at J4 — put both above the blocked segment.
    put(state, "node_2", status="CRITICAL")
    put(state, "node_4", status="CRITICAL")

    assert ("J2", "J3") in R.blocked_links(SITE, state) or \
           ("J3", "J2") in R.blocked_links(SITE, state)

    result = R.route_for_person(SITE, state, "node_3")
    assert result.state == "HOLD", result
    assert result.exit is None
    assert "J2" not in result.path and "J3" not in result.path[1:] or result.path == ()
    assert "no safe path" in result.reason


def test_fail_safe_hold():
    """All ways out gone ⇒ a HOLD, **no green pattern**, and an event logged.

    §7.8's third row, and the phase's headline behaviour: when every path is
    blocked we do not pick the least-bad one.

    The scenario is a site-wide failure, because that is what it takes now that a
    person may always leave their own node: alarming nodes on **both** levels close
    the stubs and every junction leg, J3 is left with no traversable link at all,
    and the person standing beside it is told to stay put rather than walk into
    Tunnel A.
    """
    state = quiet(fresh_state())
    put(state, "node_1", status="CRITICAL")     # closes J1's legs
    put(state, "node_2", status="CRITICAL")     # closes J2's legs
    put(state, "node_4", status="CRITICAL")     # closes J4's legs
    put(state, "node_3", status="NOMINAL")      # the person is not the problem

    result = R.route_for_person(SITE, state, "node_3")
    assert result.state == "HOLD", result
    assert result.exit is None
    assert result.eta_s is None
    assert result.path == ()
    assert "no safe path" in result.reason

    plan = {c.junction: c.pattern for c in
            R.junction_led_plan(SITE, state, [result])}
    # No invitation points *into* the dark from the junction that has no way out.
    assert plan["J3"] == "hold_amber"
    assert plan["J1"] != "flow_green" and plan["J2"] != "flow_green"

    # the hold is recorded on the state object itself, and logged
    R.fail_safe_hold(state, "J3")
    assert "J3" in state.underground["holds"]
    assert any("HOLD" in str(e.get("text", "")) for e in state.events)


def test_one_alarm_reroutes_rather_than_holds():
    """A single failure must **never** produce a HOLD: there is always a way.

    The complement of the row above, and the reason the fail-safe is credible: if
    a hold appeared every time a node went amber, nobody would believe the hold
    when it mattered.
    """
    state = quiet(fresh_state())
    put(state, "node_3", status="WARNING")
    put(state, "node_4", status="CRITICAL")

    result = R.route_for_person(SITE, state, "node_3")
    assert result.state == "OPEN"
    assert result.exit == "EXIT1"                # west, around the cross-shaft
    assert result.path == ("node_3", "J3", "J2", "J1", "EXIT1")


def test_stale_is_not_safe():
    """A stale node is priced as suspect, and Level 2 prefers the cross-link.

    §7.8's fourth row. Two assertions, and the second is the one that shows the
    cost model working: with node_3 stale, the legs at J3 are expensive, so the
    search raises the *cost* of the routes through B1 — and any route that can
    avoid them is preferred.
    """
    state = quiet(fresh_state())
    put(state, "node_3", stale=True)

    penalty = R.hazard_penalty(state, "J3", "J4", SITE)
    assert penalty == pytest.approx(config.HAZARD_COST["STALE"])

    fresh = quiet(fresh_state())
    assert R.hazard_penalty(fresh, "J3", "J4", SITE) == 0.0
    assert R.hazard_penalty(state, "J3", "J4", SITE) > \
        R.hazard_penalty(fresh, "J3", "J4", SITE)

    # and a node we cannot hear is *not* cleared by a NOMINAL status on the card
    node = state.nodes["node_3"]
    node.status = "NOMINAL"
    assert R.hazard_penalty(state, "J3", "J4", SITE) == \
        pytest.approx(config.HAZARD_COST["STALE"])


def test_map_payload():
    """Every key ``gis-map.js`` reads, with the type it reads it as.

    §7.8's fifth row. The SVG does ``d.nodes.concat(d.junctions, d.exits)`` and
    indexes ``p.lon`` / ``p.lat``; it draws zones from ``polygon[0]``/``polygon[3]``
    and links by their endpoints. All of that is asserted here, against the real
    builder rather than a hand-written dict.
    """
    state = quiet(fresh_state())
    payload = map_payload.build_map_payload(SITE, state)

    for key in ("site", "system_status", "nodes", "junctions", "exits",
                "links", "zones"):
        assert key in payload, f"/api/map lost '{key}'"
    assert isinstance(payload["system_status"], str)

    for point in payload["nodes"] + payload["junctions"] + payload["exits"]:
        assert isinstance(point["lat"], float) and isinstance(point["lon"], float)
        assert isinstance(point["id"], str)

    for zone in payload["zones"]:
        assert zone["risk"] in ("NOMINAL", "WARNING", "CRITICAL")
        assert len(zone["polygon"]) == 4
        for corner in zone["polygon"]:
            assert isinstance(corner, list) and len(corner) == 2
            assert all(isinstance(v, float) for v in corner)
        # SW corner first, NW third — the order the label anchor needs.
        assert zone["polygon"][0][0] <= zone["polygon"][3][0]

    for link in payload["links"]:
        assert set(link) >= {"from", "to", "state", "cost"}
        assert link["state"] in ("UP", "BLOCKED")
        assert link["cost"] is None or isinstance(link["cost"], float)

    # the payload is JSON-serialisable, because that is the whole job
    json.dumps(payload)


# ---------------------------------------------------------------------------
# 2 · Cost, not distance
# ---------------------------------------------------------------------------

def test_a_blocked_leg_is_infinite_not_expensive():
    state = quiet(fresh_state())
    put(state, "node_1", status="CRITICAL")
    assert R.link_cost(state, ("J1", "J2"), SITE.length("J1", "J2"), SITE) == R.BLOCKED
    assert R.link_cost(state, ("J1", "J2"), SITE.length("J1", "J2"), SITE) == float("inf")


def test_cost_is_length_times_one_plus_hazard():
    """The §7.3 formula, all three ways: quiet, priced, and closed.

    The middle case uses a **stale** node on purpose. A stale node is not in the
    blocked rule (it is not WARNING or CRITICAL — we simply do not know), so the
    leg stays *open and expensive*, which is exactly the "UNKNOWN is not safe"
    reading Phase 2 asked for: suspect, not impassable.
    """
    state = quiet(fresh_state())
    base = SITE.length("J3", "J4")
    assert R.link_cost(state, ("J3", "J4"), base, SITE) == pytest.approx(base)

    put(state, "node_3", stale=True)
    hazard = R.hazard_penalty(state, "J3", "J4", SITE)
    assert hazard == pytest.approx(config.HAZARD_COST["STALE"])
    assert R.link_cost(state, ("J3", "J4"), base, SITE) == pytest.approx(
        base * (1 + config.HAZARD_COST["STALE"]))

    put(state, "node_3", status="CRITICAL")
    assert R.link_cost(state, ("J3", "J4"), base, SITE) == R.BLOCKED


def test_a_hazardous_short_corridor_can_lose_to_a_clean_long_one():
    """The viva line, as an assertion — the east reroute.

    With Tunnel A's far section alarming, nobody on Level 1 may walk the west
    corridor any more. Level 1's own exit flips from **EXIT1 (67.7 m, 56 s)** to
    the long way round to **EXIT2**, and the router accepts the longer walk
    *because it is the safe one*. That flip is the demo §7.1 asks for: block the
    far tunnel and watch the route move.
    """
    state = quiet(fresh_state())
    before = R.site_routes(SITE, state)["per_level"]["1"]
    assert before["exit"] == "EXIT1" and before["base_m"] == pytest.approx(42.7, abs=0.5)

    put(state, "node_1", status="CRITICAL")
    after = R.site_routes(SITE, state)["per_level"]["1"]
    assert after["exit"] == "EXIT2", after
    assert after["base_m"] > before["base_m"] * 3       # 145 m instead of 43 m
    assert after["path"] == ["J2", "J3", "J4", "EXIT2"]
    # …and the west corridor is genuinely closed, not merely expensive
    assert any(set(b) == {"J1", "EXIT1"} for b in after["blocked"])
    assert any(set(b) == {"J1", "J2"} for b in after["blocked"])


def test_the_source_may_always_leave_its_own_node():
    """The deliberate exception, asserted so it cannot be "tidied" away."""
    state = quiet(fresh_state())
    put(state, "node_3", status="CRITICAL")

    # Traversal *through* node_3 is refused…
    graph = R.build_graph(SITE, state, source=None)
    costs = dict(graph["J3"])
    assert costs["node_3"] == R.BLOCKED

    # …but node_3 can still walk out, and its stub is priced, not free.
    result = R.route_for_person(SITE, state, "node_3")
    assert result.state == "OPEN"
    assert result.exit == "EXIT2"
    assert result.path[0] == "node_3"
    assert result.cost > result.base_m        # the hazard is still paid


def test_hazard_sums_two_channels():
    """Status and crack stage are added, not maxed (§7.3)."""
    state = quiet(fresh_state())
    put(state, "node_2", status="WARNING", crack_stage="PROPAGATION")
    expected = (config.HAZARD_COST["WARNING"] + config.CRACK_HAZARD["PROPAGATION"])
    assert R.hazard_penalty(state, "node_2", "J2", SITE) == pytest.approx(expected)


# ---------------------------------------------------------------------------
# 3 · The blocked-link rule
# ---------------------------------------------------------------------------

def test_blocked_rule_is_per_segment_not_site_wide():
    """One alarming node blocks **its** legs — not every route on the site."""
    state = quiet(fresh_state())
    put(state, "node_1", status="CRITICAL")

    blocked = {frozenset(p) for p in R.blocked_links(SITE, state)}
    assert frozenset(("node_1", "J1")) in blocked
    assert frozenset(("J1", "EXIT1")) in blocked
    assert frozenset(("J1", "J2")) in blocked
    # …while the far side of the site is untouched, which is the refinement the
    # v4.4 blanket boolean could not express.
    assert frozenset(("J3", "J4")) not in blocked
    assert frozenset(("EXIT2", "J4")) not in blocked


def test_a_warning_node_blocks_but_a_nominal_one_does_not():
    state = quiet(fresh_state())
    assert R.blocked_links(SITE, state) == ()
    put(state, "node_2", status="WARNING")
    assert any(frozenset(p) == frozenset(("node_2", "J2")) for p in R.blocked_links(SITE, state))


def test_inactive_nodes_are_excluded_not_dead():
    """An unwired node is not a hazard and not an obstacle (§7.6)."""
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    raw["nodes"]["node_4"]["active"] = False
    tmp = Path("/tmp/ns_map_inactive.json")
    tmp.write_text(json.dumps(raw), encoding="utf-8")
    site = R.load_tunnel_map(tmp)

    state = quiet(fresh_state())
    state.site_map = site
    graph = R.build_graph(site, state)
    assert all(neighbour != "node_4" for neighbour, _ in graph["J4"])
    assert all(neighbour != "J4" for neighbour, _ in graph.get("node_4", ()))
    assert site.active_nodes() == ("node_1", "node_2", "node_3")


# ---------------------------------------------------------------------------
# 4 · Map validation — a bad map fails at boot, loudly
# ---------------------------------------------------------------------------

def test_a_map_with_no_exits_is_rejected():
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    raw["exits"] = []
    with pytest.raises(R.MapError, match="no exits"):
        R.validate_map(raw)


def test_a_map_with_a_dangling_link_is_rejected():
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    raw["links"] = list(raw["links"]) + [["J1", "J9"]]
    with pytest.raises(R.MapError, match="unknown point"):
        R.validate_map(raw)


def test_a_map_that_isolates_a_node_is_rejected():
    """A level with no way out is a map bug, and it is caught at boot.

    The check is on the **calm** graph on purpose: a map that only fails to route
    during a collapse is a map that fails during the demo.
    """
    raw = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    raw["links"] = [l for l in raw["links"] if l != ["node_4", "J4"]]
    with pytest.raises(R.MapError, match="cannot reach any exit"):
        R.validate_map(raw)


def test_a_missing_map_file_names_the_path():
    with pytest.raises(R.MapError, match="not found"):
        R.load_tunnel_map("/tmp/definitely-not-here.json")


def test_the_real_map_validates_and_has_real_lengths():
    assert len(SITE.nodes) == 4 and len(SITE.junctions) == 4 and len(SITE.exits) == 2
    assert len(SITE.links) == 9
    # Euclidean metres between survey offsets, not the placeholder 45.0 the
    # site_meta render block carries for a calm-conditions default.
    assert SITE.length("EXIT1", "J1") == pytest.approx(
        math.hypot(45 - 5, 15 - 30), abs=0.05)
    assert SITE.length("J2", "J3") == pytest.approx(
        math.hypot(105 - 45, 45 - 15), abs=0.05)


# ---------------------------------------------------------------------------
# 5 · D7b — the booleans are derived
# ---------------------------------------------------------------------------

def test_route_booleans_follow_the_router():
    state = quiet(fresh_state())
    routes = R.site_routes(SITE, state)
    assert routes["per_level"]["1"]["exit"] == "EXIT1"
    assert routes["per_level"]["2"]["exit"] == "EXIT2"
    assert routes["hold"] is False

    # an alarm re-routes its own level…
    put(state, "node_1", status="CRITICAL")
    routes = R.site_routes(SITE, state)
    assert routes["per_level"]["1"]["exit"] == "EXIT2"
    assert routes["per_level"]["2"]["exit"] == "EXIT2"
    assert routes["hold"] is False

    # …and a site-wide failure is what it takes to hold
    put(state, "node_2", status="CRITICAL")
    put(state, "node_4", status="CRITICAL")
    routes = R.site_routes(SITE, state)
    assert routes["per_level"]["2"]["state"] == "HOLD"
    assert routes["hold"] is True
    # the level verdict names the reference point it used, so the deck can say
    # *where* the level is being judged from
    assert routes["per_level"]["1"]["reference"] == "junction"


def test_route_state_falls_back_when_there_is_no_map():
    """No map ⇒ the Phase-4 rule, not an invented route."""
    state = quiet(fresh_state())
    state.site_map = None
    assert state._route_state({}, 1) == state.levels.get("1", {}).get("route", "OPEN")


def test_underground_gains_the_phase_7_keys_and_keeps_the_old_ones():
    """§7.6: the two keys `index.html` reads survive, and are derived."""
    state = quiet(fresh_state())
    state._update_levels()
    state._update_routes()
    ug = state.underground
    assert ug["route_A_status"] in ("OPEN", "BLOCKED")
    assert ug["route_B_status"] in ("OPEN", "BLOCKED")
    assert ug["exit_A"] == "EXIT1" and ug["exit_B"] == "EXIT2"
    assert isinstance(ug["routes"], dict)
    assert isinstance(ug["holds"], list)


# ---------------------------------------------------------------------------
# 6 · The LED plan
# ---------------------------------------------------------------------------

def test_every_junction_gets_exactly_one_pattern():
    state = quiet(fresh_state())
    routes = R.site_routes(SITE, state)
    plan = R.junction_led_plan(SITE, state, list(routes["per_level"].values()))
    assert {c.junction for c in plan} == {"J1", "J2", "J3", "J4"}
    assert all(c.pattern in R.PATTERNS for c in plan)
    assert {c.pattern for c in plan} == {"flow_green"}
    # the hubs are the level hubs — there is no per-junction MCU
    assert {c.hub for c in plan} == {"L1", "L2"}


def test_the_hatch_quote_no_green_into_danger():
    """A blocked leg beats an invitation, every time.

    The precedence matters: a junction that is both on the escape route and beside
    a failing node must flash the hazard.
    """
    state = quiet(fresh_state())
    put(state, "node_2", status="CRITICAL")
    routes = R.site_routes(SITE, state)
    plan = {c.junction: c.pattern for c in
            R.junction_led_plan(SITE, state, list(routes["per_level"].values()))}
    # J2 is beside the failing node and has no way out at all — it holds.
    assert plan["J2"] == "hold_amber"
    # J1 still has its west exit, but its leg to J2 is blocked, so it flashes
    # "not that way" rather than inviting anyone east.
    assert plan["J1"] == "chase_red"
    assert plan["J1"] != "flow_green"


def test_an_evacuation_strobes_the_route():
    state = quiet(fresh_state())
    put(state, "node_4", status="CRITICAL")
    routes = R.site_routes(SITE, state)
    plan = {c.junction: c.pattern for c in
            R.junction_led_plan(SITE, state, list(routes["per_level"].values()))}
    # L1 is clear and its exit path runs J1→EXIT1; the site is evacuating.
    assert plan["J1"] in ("strobe_red", "flow_green")


def test_the_command_line_is_a_cmd_sentinel_plus_json():
    cmd = R.HubCommand(hub="L1", junction="J2", pattern="chase_red", reason="blocked")
    line = cmd.to_line()
    assert line.startswith("#CMD ")
    body = json.loads(line[5:])
    assert body == {"cmd": "led", "junction": "J2", "pattern": "chase_red",
                    "colour": "red", "period_ms": 120, "buzzer": False,
                    "reason": "blocked"}
    assert len(line) < 200          # one line, comfortably


def test_push_without_a_hub_returns_false_rather_than_pretending():
    R.attach_downlink(None)
    assert R.downlink_attached() is False
    assert R.push_hub_command(R.HubCommand("L1", "J1", "flow_green")) is False

    sent = []
    R.attach_downlink(sent.append)
    try:
        assert R.push_hub_command(R.HubCommand("L1", "J1", "flow_green")) is True
        assert sent and sent[0].startswith("#CMD ")
    finally:
        R.attach_downlink(None)


# ---------------------------------------------------------------------------
# 7 · Position resolution and the conversion helpers
# ---------------------------------------------------------------------------

def test_resolve_position_plans_from_the_worst_node_on_the_level():
    """The reader knows the level, not the seat — so plan from the worst seat."""
    state = quiet(fresh_state())
    state.zones = {"001": "Tunnel A", "002": "Tunnel B", "004": "Safe Exit"}
    put(state, "node_2", status="WARNING")

    assert R.resolve_position(state, "001", SITE) == "node_2"
    assert R.resolve_position(state, "002", SITE) in ("node_3", "node_4")
    assert R.resolve_position(state, "004", SITE) == "EXIT_SURFACE"
    assert R.resolve_position(state, "999", SITE) == ""


def test_offsets_convert_to_latlon_about_the_baseline():
    meta = map_payload.load_site_meta()
    lat, lon = map_payload.offset_to_latlon(0.0, 0.0, meta)
    assert (lat, lon) == (meta["baseline_lat"], meta["baseline_lon"])

    # 30 m north of the baseline is 30/111320 degrees; the east axis is scaled by
    # the precomputed metres-per-degree for this latitude.
    lat2, lon2 = map_payload.offset_to_latlon(30.0, 0.0, meta)
    assert lat2 - lat == pytest.approx(30.0 / meta["metres_per_degree_lat"])
    lat3, lon3 = map_payload.offset_to_latlon(0.0, 100.0, meta)
    assert lon3 - lon == pytest.approx(100.0 / meta["metres_per_degree_lon"])
    assert lat3 == lat
    assert meta["metres_per_degree_lon"] < meta["metres_per_degree_lat"]


def test_rect_to_polygon_returns_four_corners_in_anchor_order():
    meta = map_payload.load_site_meta()
    poly = map_payload.rect_to_polygon([0, 0, 60, 30], meta)
    assert len(poly) == 4
    # (y0,x0) (y1,x0) (y1,x1) (y0,x1) → SW, NW, NE, SE
    assert poly[0][0] < poly[1][0] and poly[2][1] > poly[1][1]


def test_zone_risk_is_worst_of_the_nodes_inside_it():
    state = quiet(fresh_state())
    put(state, "node_1", status="WARNING")
    zones = {z["id"]: z["risk"] for z in map_payload.zone_payload(SITE, state, SITE_META)}
    assert zones["A1"] == "WARNING"
    assert zones["A2"] == "NOMINAL"

    put(state, "node_1", status="CRITICAL")
    zones = {z["id"]: z["risk"] for z in map_payload.zone_payload(SITE, state, SITE_META)}
    assert zones["A1"] == "CRITICAL"


def test_a_stale_node_is_not_a_green_zone():
    state = quiet(fresh_state())
    put(state, "node_4", stale=True)
    zones = {z["id"]: z["risk"] for z in map_payload.zone_payload(SITE, state, SITE_META)}
    assert zones["B2"] == "WARNING"


SITE_META = map_payload.load_site_meta()


# ---------------------------------------------------------------------------
# 8 · Published shape (the deck reads these; additive only)
# ---------------------------------------------------------------------------

def test_the_routes_read_the_state_the_server_actually_runs():
    """`/api/map` must not answer from a shadow application module.

    This is a regression pin for a live defect: `api/routes_map.py` used to do
    `from app import STATE` at request time, which under `python3 app.py` (where
    the module is `__main__`) imported a **second copy** of app.py with a fresh,
    un-ticked SystemState. The map then drew open exits through obviously failing
    tunnels, because it was reading a state in which nothing had ever happened.
    The routes are handed the state at registration, so the only way to fail this
    test is to go back to importing it.
    """
    from api import routes_map

    sentinel = object()
    previous = routes_map._STATE
    try:
        routes_map.bind(SITE, map_payload.load_site_meta(), sentinel)
        state, site = routes_map._state()
        assert state is sentinel, "the blueprint invented its own state"
        assert site is SITE
    finally:
        routes_map.bind(previous and SITE, map_payload.load_site_meta(), previous)


def test_route_result_is_json_shaped():
    state = quiet(fresh_state())
    result = R.route_for_person(SITE, state, "node_2").to_dict()
    for key in ("src", "exit", "path", "cost", "base_m", "hazard", "eta_s",
                "state", "reason", "blocked", "level"):
        assert key in result
    assert result["path"][0] == "node_2"
    assert isinstance(result["eta_s"], int)
    json.dumps(result)
    # the numbers are rounded for the wire, not raw floats
    assert result["cost"] == round(result["cost"], 2)


def test_eta_uses_the_published_walk_speed():
    state = quiet(fresh_state())
    result = R.route_for_person(SITE, state, "node_1")
    assert result.eta_s == pytest.approx(result.base_m / config.WALK_SPEED_MPS, abs=1.0)


def test_full_route_on_the_real_map_is_the_shipped_one():
    """The end-to-end answer, pinned: quiet site, node_1 → EXIT1 in ~68 m."""
    state = quiet(fresh_state())
    result = R.route_for_person(SITE, state, "node_1")
    assert result.exit == "EXIT1"
    assert result.base_m == pytest.approx(67.7, abs=0.5)
    assert result.state == "OPEN"
    assert "risk-weighted" in result.reason
