"""
Phase 4 (B8) — pair elongation, differential settlement and zone risk.
======================================================================

§4.6's acceptance table first, then everything that table implies. Eight groups:

**1 · The acceptance table.** ``test_pair_elongation`` · ``test_zone_risk`` — the
two rows §4.6 names, plus the sign convention this phase had to state.

**2 · The map is the baseline.** ``L₀`` comes from ``tunnel_map.json``, so a map
edit re-baselines and a missing map produces ``None`` rather than a number. §4.7
lists exactly two risks for this phase and this is one of them.

**3 · The zero.** One zero per node, captured on first sight, dropped by the
operator's Re-arm — and the trap that makes the baselining worth doing: two pots
that rest at different counts must not read as permanent settlement.

**4 · The units.** 3 mm across a 70 m drift is 42.9 µε, and the 400 µε level
threshold is 28 mm. Both are asserted, because the threshold's *reachability* on
the bench is a demo question, not a footnote.

**5 · Differential settlement.** The §4.2 case: both nodes moving together is a
panel settling; one moving relative to the other is not.

**6 · Zone risk.** Nodes *and* links inside the polygon, plus the level's own
relay leg — with Phase 2's rule that a leg which has never beaten is ``UNKNOWN``
and is not dark.

**7 · The level escalation.** §4.5: pair strain raises the **level**, not the
node, and only to WARNING.

**8 · Additive by construction.** Keys gained, none lost; the published block is
JSON scalars; ``NodeState`` is untouched by a phase that is about the ground
between nodes rather than anything a node knows.

No test sleeps: every timestamp is passed in.
"""

from __future__ import annotations

import json

import pytest

import config
import contracts
from engine import geomech as G
from state import SystemState

T0 = 1_000_000.0

N1, N2, N3, N4 = "node_1", "node_2", "node_3", "node_4"

#: The map's own geometry, asserted here so the tests fail loudly if the diorama
#: is re-measured rather than silently computing different numbers.
L0_MM = 70_000.0          # node_1 → node_2, east 20 m → 90 m


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def make_state(**kw) -> SystemState:
    state = SystemState(started_at=kw.pop("started_at", T0 - 600.0), **kw)
    state.armed = True
    return state


def at_rest(state, disp: float = 5.0) -> None:
    """Every node sitting still at its own rest reading."""
    for n_id in state.node_ids:
        state.nodes[n_id].displacement_mm = disp
        state.nodes[n_id].status = "NOMINAL"


def baseline(state, disp: float = 5.0) -> None:
    """Put the state at rest *and* capture its zero.

    The zero is captured on the pair layer's first **read** of a node, not when
    its displacement is set — deliberately, because on the rig the first read is
    the first frame and there is no earlier moment to hook. So a fixture that
    only assigned values would leave the baseline to be captured *after* the test
    had already moved something, and every later reading would be relative to the
    wrong instant. Reading once, here, is what makes that explicit.
    """
    at_rest(state, disp)
    G.reset_geomech_baseline("test fixture")
    for lv in (1, 2):
        pair = G.pair_for_level(lv)
        if pair:
            G.pair_elongation(state, *pair)


# ===========================================================================
# 1 · §4.6 — the acceptance table
# ===========================================================================

def test_pair_elongation():
    """§4.6: displace node 1 by 3 mm along the axis, node 2 by 0 ⇒ ΔL = 3 mm;
    displace both equally ⇒ ΔL ≈ 0.

    The sign convention this phase states (and the report records): a positive
    ``ΔL`` means the pair **lengthened**. Node 1 moving *away* from node 2 is
    therefore +3 mm, and the plan's magnitude row — ``|ΔL| = 3`` — is satisfied
    either way. ``axis_unit`` points 1 → 2, so motion along it is "toward".
    """
    state = make_state()
    baseline(state)

    state.nodes[N1].displacement_mm = 5.0 - 3.0            # N1 moves away from N2
    assert G.pair_elongation(state, N1, N2) == pytest.approx(3.0, abs=1e-6)

    state.nodes[N1].displacement_mm = 5.0 + 3.0            # N1 moves toward N2
    assert G.pair_elongation(state, N1, N2) == pytest.approx(-3.0, abs=1e-6)

    state.nodes[N1].displacement_mm = 5.0                  # N1 back to rest ...
    state.nodes[N2].displacement_mm = 5.0 + 3.0            # ... N2 moves away by 3
    assert G.pair_elongation(state, N1, N2) == pytest.approx(3.0, abs=1e-6)

    state.nodes[N1].displacement_mm = 5.0 - 3.0            # both move apart: 6 mm
    assert G.pair_elongation(state, N1, N2) == pytest.approx(6.0, abs=1e-6)

    # Both displaced *equally* — the whole panel translating 3 mm — is not
    # elongation. This is the row that separates a baseline from a movement log.
    state.nodes[N1].displacement_mm = 5.0 - 3.0
    state.nodes[N2].displacement_mm = 5.0 - 3.0
    assert G.pair_elongation(state, N1, N2) == pytest.approx(0.0, abs=1e-6)


def test_pair_elongation_is_the_distance_not_the_movement():
    """The phase's whole thesis, as arithmetic: two nodes that both move 9 mm
    apart *in the same direction* have not deformed anything."""
    state = make_state()
    baseline(state)
    for node, base in ((N1, 5.0), (N2, 5.0)):
        state.nodes[node].displacement_mm = base + 9.0
    assert G.pair_elongation(state, N1, N2) == pytest.approx(0.0, abs=1e-6)
    assert G.pair_strain(state, N1, N2) == pytest.approx(0.0, abs=1e-6)


def test_zone_risk():
    """§4.6: one node WARNING inside A2 ⇒ ``zone_risk("A2") == "WARNING"``;
    all nodes NOMINAL ⇒ ``NOMINAL``."""
    state = make_state()
    at_rest(state)

    assert G.zone_risk(state, "A1") == "NOMINAL"
    assert G.zone_risk(state, "A2") == "NOMINAL"
    assert G.geomech_block(state)["worst_zone_risk"] == "NOMINAL"

    state.nodes[N2].status = "WARNING"
    assert G.zone_risk(state, "A2") == "WARNING"
    assert G.zone_risk(state, "A1") == "NOMINAL", "a neighbour's alarm is not A1's"

    state.nodes[N2].status = "CRITICAL"
    assert G.zone_risk(state, "A2") == "CRITICAL"

    # The map places exactly one node per zone — if that ever changes the zone
    # rule still holds, but the *reason* string has to name the right node.
    assert G.zone_block(state, "A2")["reason"] == "node node_2"


# ===========================================================================
# 2 · The map is the baseline
# ===========================================================================

def test_the_baseline_comes_from_the_map():
    """``L₀`` is the map's two coordinates, in mm — not a literal in the code."""
    assert G.baseline_length_mm(N1, N2) == pytest.approx(L0_MM, abs=1.0)
    assert G.baseline_length_mm(N3, N4) == pytest.approx(L0_MM, abs=1.0)
    # 70 m, and the readout publishes metres.
    assert G.pair_block(make_state(), 1)["l0_m"] == pytest.approx(70.0, abs=0.01)


def test_a_moved_node_input_re_baselines(monkeypatch, tmp_path):
    """§4.7: *"a map edit automatically re-baselines"* — nothing caches it."""
    moved = json.loads(config.ASSET_FILES["tunnel_map"].read_text())
    moved["nodes"][N2]["d_e_m"] = 40.0                     # 20 m → 40 m = 20 m span
    path = tmp_path / "tunnel_map.json"
    path.write_text(json.dumps(moved))

    real = config.ASSET_FILES["tunnel_map"]
    monkeypatch.setitem(config.ASSET_FILES, "tunnel_map", path)
    G.load_map(reload=True)
    try:
        assert G.baseline_length_mm(N1, N2) == pytest.approx(20_000.0, abs=1.0)
    finally:
        # Put the path back *and* reload: monkeypatch only undoes the first, and
        # it does so after this test returns — a cache left holding the fixture
        # map would silently re-baseline every test after this one.
        monkeypatch.setitem(config.ASSET_FILES, "tunnel_map", real)
        G.load_map(reload=True)


def test_a_missing_map_answers_none_not_a_number(monkeypatch, tmp_path):
    """§4.7's rollback is *"purely additive readouts"* — so no map, no numbers.

    ``None`` in every field, and no exception: a build whose JSON moved still
    boots, and the deck renders "—" instead of a fabricated 0.0 mm.
    """
    real = config.ASSET_FILES["tunnel_map"]
    monkeypatch.setitem(config.ASSET_FILES, "tunnel_map", tmp_path / "nope.json")
    G.load_map(reload=True)
    try:
        state = make_state()
        at_rest(state)
        assert G.baseline_length_mm(N1, N2) is None
        assert G.pair_elongation(state, N1, N2) is None
        assert G.pair_strain(state, N1, N2) is None
        assert G.zone_nodes("A1") == []
        assert G.zone_risk(state, "A1") == "NOMINAL"
        block = G.pair_block(state, 1)
        assert block["l0_m"] is None and block["strain_ue"] is None
        assert block["warn"] is False
        assert json.dumps(G.geomech_block(state))           # still serialisable
    finally:
        monkeypatch.setitem(config.ASSET_FILES, "tunnel_map", real)
        G.load_map(reload=True)


# ===========================================================================
# 3 · The zero
# ===========================================================================

def test_a_node_with_no_reading_yields_none_not_zero():
    """No measurement ≠ did not move — the distinction the module exists for."""
    state = make_state()
    state.nodes[N1].displacement_mm = None
    assert G.pair_elongation(state, N1, N2) is None
    assert G.pair_strain(state, N1, N2) is None
    assert G.differential_settlement(state, 1) is None


def test_two_pots_that_rest_differently_do_not_read_as_settlement():
    """The trap the per-node baseline closes.

    N1's pot rests at 5.0 mm and N2's at 12.0 mm. Both are still. The plan's
    literal ``|disp_a − disp_b|`` would report 7 mm of settlement on a healthy
    level, for ever, on every build whose two pots were zeroed at different
    counts — which, on two different sensors, is all of them.
    """
    state = make_state()
    state.nodes[N1].displacement_mm = 5.0
    state.nodes[N2].displacement_mm = 12.0
    G.reset_geomech_baseline("rest")

    assert G.differential_settlement(state, 1) == pytest.approx(0.0, abs=1e-6)
    assert G.pair_elongation(state, N1, N2) == pytest.approx(0.0, abs=1e-6)

    # ... and a real 4 mm of relative movement still shows up, as 4 mm.
    state.nodes[N2].displacement_mm = 16.0
    assert G.differential_settlement(state, 1) == pytest.approx(4.0, abs=1e-6)


def test_re_arm_re_baselines_the_pairs():
    """``state.reset()`` is the operator re-basing the picture, so the pair layer
    re-bases with it — otherwise the previous drill's movement is re-reported as
    if it had just happened."""
    state = make_state()
    baseline(state)
    state.nodes[N2].displacement_mm = 5.0 + 30.0
    assert G.pair_elongation(state, N1, N2) == pytest.approx(30.0, abs=1e-6)

    state.reset("test re-arm")
    at_rest(state)
    assert G.pair_elongation(state, N1, N2) == pytest.approx(0.0, abs=1e-6)
    assert G.pair_strain(state, N1, N2) == pytest.approx(0.0, abs=1e-6)


# ===========================================================================
# 4 · The units
# ===========================================================================

def test_strain_is_microstrain_of_the_map_baseline():
    """3 mm on a 70 m baseline is 42.9 µε — the conversion that makes a
    millimetre readout mean something in rock mechanics."""
    state = make_state()
    baseline(state)
    state.nodes[N2].displacement_mm = 5.0 + 3.0

    strain = G.pair_strain(state, N1, N2)
    assert strain == pytest.approx(1e6 * 3.0 / L0_MM, rel=1e-9)
    assert strain == pytest.approx(42.857, abs=0.01)


def test_the_level_threshold_is_a_reachable_number():
    """400 µε across 70 m is **28 mm** of relative movement.

    Asserted rather than assumed: the threshold is the plan's, and this test is
    the arithmetic that says what the bench has to achieve for the level rule to
    fire at all. If the diorama's baseline is re-measured, this number moves and
    the test says so.
    """
    mm_at_threshold = config.PAIR_STRAIN_WARN * L0_MM / 1e6
    assert mm_at_threshold == pytest.approx(28.0, abs=0.1)


# ===========================================================================
# 5 · Differential settlement
# ===========================================================================

def test_differential_settlement_is_per_level():
    """One level moving relative to itself does not move the other level."""
    state = make_state()
    baseline(state)

    state.nodes[N2].displacement_mm = 5.0 + 6.0             # level 1 only
    assert G.differential_settlement(state, 1) == pytest.approx(6.0, abs=1e-6)
    assert G.differential_settlement(state, 2) == pytest.approx(0.0, abs=1e-6)

    state.nodes[N3].displacement_mm = 5.0 + 2.0             # level 2 joins in
    assert G.differential_settlement(state, 2) == pytest.approx(2.0, abs=1e-6)
    assert G.differential_settlement(state, 1) == pytest.approx(6.0, abs=1e-6)


def test_a_level_with_no_pair_answers_none():
    """A one-node level has no baseline to draw — ``None``, not 0.0 mm."""
    state = make_state(levels={1: (N1, N2), 3: (N3,)})
    at_rest(state)
    assert G.differential_settlement(state, 3) is None
    assert G.pair_for_level(3) is None
    assert G.zone_risk(state, "A1") == "NOMINAL"


# ===========================================================================
# 6 · Zone risk
# ===========================================================================

def test_zone_links_are_the_ones_fully_inside_the_polygon():
    """Both endpoints inside, or the link belongs to no zone.

    ``J2↔J3`` crosses from A2 into B1, so neither zone owns it — it is the link
    *between* them, and blaming one zone for it would be arbitrary.
    """
    assert G.zone_links("A1") == [("EXIT1", "J1"), (N1, "J1")]
    assert G.zone_links("A2") == [(N2, "J2")]
    # Order follows the map's own `links` array, so the readout is reproducible.
    assert G.zone_links("B2") == [("J4", "EXIT2"), (N4, "J4")]
    assert all(("J2", "J3") not in links for links in
               (G.zone_links(z) for z in ("A1", "A2", "B1", "B2")))
    # A zone id the map does not know is not an error — it is an empty zone.
    assert G.zone_rect("ZZ") is None and G.zone_links("ZZ") == []
    assert G.zone_risk(make_state(), "ZZ") == "NOMINAL"


def test_a_link_is_only_as_safe_as_the_ground_it_lands_on():
    """A WARNING at N4 makes the N4–J4 and J4–EXIT2 links risky, so B2 is WARNING.

    The plan's *"worst-of the nodes and links"* is not vacuous here: the link's
    risk is derived *through* the map's topology rather than read from the node
    list, so a link to a junction no node sits on still gets a verdict — and it
    gets it from the node it connects to.
    """
    state = make_state()
    at_rest(state)
    assert G.zone_risk(state, "B2") == "NOMINAL"

    state.nodes[N4].status = "WARNING"
    assert G.zone_risk(state, "B2") == "WARNING"
    assert G.zone_block(state, "B2")["reason"] == "node node_4"
    assert G.zone_risk(state, "B1") == "NOMINAL"


def test_a_dark_relay_leg_raises_the_levels_zones():
    """A zone whose hub has gone dark is at least WARNING: the ground may be
    fine, but the mine has stopped being able to see it."""
    now = T0
    state = make_state()
    at_rest(state)

    # A leg that has beaten and then stopped is DOWN (Phase 2's rule).
    state.ring.note_heartbeat(1, seq=1, t=now - 100.0)
    state.ring.note_heartbeat(2, seq=1, t=now)
    for lv in (1, 2):
        state.ring.links[lv].up = (lv == 2)

    assert G.zone_risk(state, "A1") == "WARNING"
    assert G.zone_risk(state, "A2") == "WARNING"
    assert G.zone_risk(state, "B1") == "NOMINAL", "level 2's leg is up"
    assert G.zone_block(state, "A1")["reason"] == "level 1 relay leg down"


def test_a_leg_that_has_never_beaten_raises_nothing():
    """Phase 2's distinction, re-used rather than re-invented: ``UNKNOWN`` is the
    first minute of a shift, not a hazard. A boot that painted every zone amber
    would be a boot nobody reads twice."""
    state = make_state()
    at_rest(state)
    for lv in (1, 2):
        assert state.ring.links[lv].last_heartbeat_t is None
    assert G.zone_risk(state, "A1") == "NOMINAL"
    assert G.zone_risk(state, "B2") == "NOMINAL"
    assert G.geomech_block(state)["worst_zone_risk"] == "NOMINAL"


def test_zone_nodes_survive_a_node_that_is_not_running():
    """A zone's risk is a statement about the ground, so an unplugged board does
    not delete the zone — it just stops contributing a status."""
    state = make_state(node_ids=(N1, N2 + ""))              # N3/N4 not in the pipeline
    at_rest(state)
    assert G.zone_nodes("B2") == [N4], "the map still places a node in B2"
    assert G.zone_risk(state, "B2") == "NOMINAL", "no live status, no invented alarm"


# ===========================================================================
# 7 · The level escalation (§4.5)
# ===========================================================================

def test_pair_strain_blocks_the_level_route_not_the_node():
    """§4.5: the level goes WARNING, the node does not — and only the level whose
    pair actually stretched."""
    state = make_state()
    baseline(state)
    state.update_status_logic()
    assert state.levels["1"]["status"] == "NOMINAL"

    # 30 mm of relative movement on a 70 m baseline = 428.6 µε > 400 µε.
    state.nodes[N2].displacement_mm = 5.0 + 30.0
    state.update_status_logic()

    assert state.levels["1"]["status"] == "WARNING"
    assert state.levels["1"]["route"] == "BLOCKED"
    assert state.levels["1"]["pair_warn"] is True
    assert state.levels["2"]["status"] == "NOMINAL"
    assert state.levels["2"]["pair_warn"] is False
    assert state.nodes[N2].status == "NOMINAL", "the node itself saw nothing wrong"
    assert state.underground["route_A_status"] == "BLOCKED"
    assert state.underground["route_B_status"] == "OPEN"


def test_the_escalation_is_warning_only_never_critical():
    """CRITICAL stays the acceleration channel's verdict. A second author of
    CRITICAL — here or anywhere — is a second opinion about the same number, and
    Phase 3 refused the same temptation for the crack stage."""
    state = make_state()
    baseline(state)
    for _ in range(40):                                     # a metre of separation
        state.nodes[N2].displacement_mm += 25.0
        state.update_status_logic()
    assert state.levels["1"]["status"] == "WARNING"
    assert state.system_status != "CRITICAL"
    assert state.nodes[N2].status == "NOMINAL"


def test_a_small_pair_strain_raises_nothing():
    """Below the threshold the level stays NOMINAL and the route stays open —
    the rule has to be a threshold, not a detector of any movement at all."""
    state = make_state()
    baseline(state)
    state.nodes[N2].displacement_mm = 5.0 + 20.0            # 285.7 µε < 400
    state.update_status_logic()
    assert state.levels["1"]["status"] == "NOMINAL"
    assert state.levels["1"]["pair_warn"] is False


# ===========================================================================
# 8 · Additive by construction
# ===========================================================================

def test_the_geomech_block_is_json_scalars():
    """``/api/extended`` renders this: an object that is not serialisable is a
    500 on the endpoint the operator is watching during a drill."""
    state = make_state()
    at_rest(state)
    block = G.geomech_block(state)
    json.dumps(block)

    assert set(block) == {"pairs", "zones", "worst_zone", "worst_zone_risk", "counts"}
    assert set(block["pairs"]) == {"1", "2"}, "keyed the way state.levels is"
    assert set(block["pairs"]["1"]) >= {"level", "a", "b", "label", "l0_m",
                                        "elongation_mm", "strain_ue",
                                        "settlement_mm", "warn", "threshold_ue"}
    assert set(block["zones"]) == {"A1", "A2", "B1", "B2"}
    assert set(block["zones"]["A1"]) >= {"risk", "nodes", "links", "reason", "rect", "level"}
    # The schematic colours a level from its zones, so the level has to travel.
    assert {z["level"] for z in block["zones"].values()} == {1, 2}
    assert G.zone_level("A2") == 1 and G.zone_level("B1") == 2


def test_the_levels_block_gained_a_key_and_lost_none():
    """``levels`` is read by the deck's route logic. Phase 4 adds ``pair_warn``
    and touches nothing that was already there."""
    state = make_state()
    at_rest(state)
    state.update_status_logic()
    for key in ("1", "2"):
        assert set(state.levels[key]) == {"status", "route", "exits", "pair_warn"}


def test_the_phase_writes_nothing_to_a_node():
    """This phase is about the ground *between* nodes. Nothing it computes is a
    field on ``NodeState``, and the wire contract is untouched."""
    state = make_state()
    at_rest(state)
    before = set(state.nodes[N2].to_public())
    state.nodes[N2].displacement_mm = 5.0 + 40.0
    G.geomech_block(state)
    G.zone_risk(state, "A2")
    assert set(state.nodes[N2].to_public()) == before

    import dataclasses
    fields = {f.name for f in dataclasses.fields(contracts.NodeState)}
    assert not (fields & {"pair", "elongation", "strain", "settlement", "zone_risk"})


def test_the_status_vocabulary_has_exactly_one_literal():
    """Phase 4 moved ``STATUS_RANK``/``worst_status`` into ``contracts`` because
    ``geomech`` needs them and cannot import ``state``. If this ever fails, the
    D10 pattern is back: two copies and a test hoping they agree."""
    import state as state_module

    assert state_module.STATUS_RANK is contracts.STATUS_RANK
    assert state_module.worst_status is contracts.worst_status
    assert contracts.worst_status(["nominal", "WARNING"]) == "WARNING", "unknown words rank NOMINAL"
    assert set(contracts.STATUSES) == {"NOMINAL", "WARNING", "CRITICAL"}


# ===========================================================================
# 9 · A cross-phase coherence defect — recorded, pinned, not silently "fixed"
# ===========================================================================

def test_a_truck_pass_no_longer_escalates_the_level():
    """**Phase 4's loudest warning, closed by Phase 5 — and the pin flipped.**

    Phase 4 shipped this behaviour as a *deliberate, visible defect*: the plan's
    profiles were pure accumulators (``displacement_mm`` integrated acceleration
    and never relaxed), so on the bench the loader rolling past moved N2 by the
    same ~95 mm a collapse does —

        ΔL ≈ 95 mm  →  ≈1357 µε  →  level WARNING, route BLOCKED,

    — which contradicts the claim the whole build rests on: *"a truck moves the
    accelerometer but does not strain the roof"*. Its original docstring ended
    "if Phase 5 fuses the environmental verdict into this escalation, this test is
    the one that should be rewritten, and it will fail loudly until it is." It did
    fail loudly, and this is the rewrite.

    **What changed is the simulator, not the threshold.** §5.9's separation of
    plant from measurement and Phase 5's elastic ground: environmental profiles
    now track load through a damped spring and *return*, so a truck pass peaks at a
    fraction of a millimetre instead of walking to the 100 mm clamp. 400 µε on a
    70 m baseline needs 28 mm, so the pair channel now has to be shown real, and
    the level stays open.

    **The escalation path itself is untouched** — ``test_pair_strain_blocks_the_
    level_route_not_the_node`` above still drives a real strain past the threshold
    and still BLOCKs the level. This test only pins that a truck no longer does it
    by accident. It is the same assertion §5.12 makes for the classifier
    ("machinery_pass ⇒ ENVIRONMENTAL, no tier above ADVISORY"), stated at the
    layer that used to get it wrong.

    The contrast with a real event is the point: a collapse moves the pair by tens
    of millimetres because the seam is *opening*; a truck moves it by a fraction
    because the rock is *flexing*.
    """
    from ingestion import simulator

    simulator.reset_seam()
    state = make_state()
    state.reset("bench")
    simulator.arm(state, N2, "machinery_pass")
    peak_vib = 0
    for _ in range(400):                                   # 12 s at 50 ms, then it ends
        simulator.apply_injections(state, 0.05)
        peak_vib = max(peak_vib, state.nodes[N2].vib)
    # Read the *peak*, not the final value: the profile's `on_end` clears the
    # impact counter when the loader has gone, which is the correct resting state
    # for the channel and would make a post-hoc read say "no truck ever passed".

    dl = G.pair_elongation(state, N1, N2)
    strain = G.pair_strain(state, N1, N2)

    # The truck was real: it shook the node hard enough to be seen.
    assert peak_vib > 0, "the impact channel saw the truck"

    # ... and it did not strain the roof.
    assert dl is not None and abs(dl) < 5.0, \
        f"a truck pass should barely move the pair, moved {dl:.3f} mm"
    assert strain is not None and strain < config.PAIR_STRAIN_WARN, \
        f"a truck pass reached {strain:.0f} µe, past the {config.PAIR_STRAIN_WARN:.0f} µe level threshold"
    assert state.levels["1"]["pair_warn"] is False
    assert state.levels["1"]["route"] != "BLOCKED", \
        "an environmental profile blocked a route"

    # ... and the seam never opened, so the crack column — the channel that is
    # supposed to see rock failing — correctly reports nothing.
    assert simulator.seam_open_mm(N2) == 0.0
    assert state.nodes[N2].micro is not None and abs(state.nodes[N2].micro) < 5.0
