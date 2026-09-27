"""
Phase 4 (B8) — pair elongation, differential settlement and zone risk.
======================================================================

Four independent node readings become **one statement about the ground**:

* ``pair_elongation``      — how much longer the baseline between two collinear
  nodes has become, in mm. Signed: positive is the pair *lengthening*.
* ``pair_strain``          — that same number as strain, ``ΔL / L₀``, in µε.
* ``differential_settlement`` — |Δdisp_a − Δdisp_b| across a level, in mm.
* ``zone_risk``            — the worst thing inside a zone polygon: its nodes,
  the links that lie inside it, and the state of the level's relay leg.

Why a pair and not a node (plan §4.3, and the honest answer to *"why four nodes
and not one?"*)
------------------------------------------------------------------------------------------
A single node's displacement is **ambiguous**. Every node on the level can settle
by the same amount — the whole panel dropping together, which is worrying but not
a collapse — or one end can move while the other holds, which is the strata
failing between the pillars. The absolute reading cannot tell those apart. The
*difference* can, and only the difference. Land surveyors have measured baselines
rather than single marks for exactly this reason: a monument that moves is not an
event, two monuments that separate are.

The three things this module refuses to guess
---------------------------------------------
1. **The baseline is the map's, not a literal.** ``L₀`` comes from
   ``assets/tunnel_map.json`` through ``config.ASSET_FILES`` — one file, one
   geometry authority. §4.7 asks for the baseline to be captured at boot so a map
   edit re-baselines automatically; that is what happens, because nothing caches
   it except the module-level cache that ``load_map(reload=True)`` drops.
2. **The zeros are per node and captured on first sight.** ``displacement_mm`` is
   a calibrated reading from that node's own pot (H1's ``NS_DISP_*`` span), so two
   nodes at rest do not necessarily read the same number. Differencing the raw
   readings would report a permanent settlement between two nodes that are both
   perfectly still; differencing each node's movement since its own baseline does
   not, and gives the same 0.0 mm on a healthy level. ``state.reset()`` — the
   operator's Re-arm — drops the baselines, exactly as it drops the crack
   baseline (Phase 3) and the seam accumulator.
3. **Zone risk never invents a word.** A link that has never been heard from is
   ``UNKNOWN`` in Phase 2's link ledger and is *not* dark there, so it is not
   risk here either: silence at boot is not a hazard, it is the first minute. The
   vocabulary is the three words the dashboard already ranks — NOMINAL, WARNING,
   CRITICAL — and nothing else.

Placement: this module must not import ``state`` (``state`` imports *it*, to ask
the pair layer about level escalation), so everything it needs arrives as an
argument and is read with ``getattr``. It imports ``config`` and ``contracts``
only, and reads the map itself.
"""

from __future__ import annotations

import json
import math
from typing import Any

#: Imported as a **module**, not as names, and every value is read through it at
#: call time. ``config`` is reloaded by tests (``importlib.reload``) and
#: monkeypatched by tooling, and a reload rebuilds its globals — a `from config
#: import X` in a long-lived module then holds a stale object for the rest of the
#: session. That is not hypothetical: it is why these two tests failed under the
#: full suite and passed alone. One indirection, no stale geometry.
import config
from contracts import STATUS_RANK, worst_status

#: microstrain per strain. ``ΔL/L₀`` is dimensionless; µε is that × 1e6.
_UE = 1e6

#: The level's own relay leg, spelled the way ``engine.health.link_state`` spells
#: it. Kept as a format string rather than a dict so the two cannot drift.
_LEG_ID = "lvl{}→hub"


# ---------------------------------------------------------------------------
# The map — the one geometry authority
# ---------------------------------------------------------------------------

_MAP: dict[str, Any] | None = None


def load_map(reload: bool = False) -> dict[str, Any]:
    """``tunnel_map.json``, cached. ``reload=True`` drops the cache.

    Failure is not fatal (§4.7's rollback: *"purely additive readouts"*): a build
    with no readable map still runs, and every function here answers ``None``
    rather than a fabricated number. A demo that dies because a JSON file moved
    is worse than a demo that says it does not know.
    """
    global _MAP
    if _MAP is not None and not reload:
        return _MAP
    try:
        _MAP = json.loads(config.ASSET_FILES["tunnel_map"].read_text())
    except Exception as exc:                                  # pragma: no cover
        print(f"geomech: tunnel_map.json not loaded ({exc}) — pairs and zones disabled.")
        _MAP = {}
    return _MAP


def map_nodes() -> dict[str, dict]:
    """``node_id → {level, zone, d_n_m, d_e_m}`` from the map ({} if absent)."""
    nodes = load_map().get("nodes") or {}
    return nodes if isinstance(nodes, dict) else {}


def node_position(n_id: str) -> tuple[float, float] | None:
    """A node's ``(north_m, east_m)``, in metres. ``None`` if the map lacks it."""
    n = map_nodes().get(n_id)
    if not isinstance(n, dict):
        return None
    try:
        return (float(n["d_n_m"]), float(n["d_e_m"]))
    except (KeyError, TypeError, ValueError):
        return None


def _point_position(p_id: str) -> tuple[float, float] | None:
    """``(north_m, east_m)`` for any map point: a node, a junction or an exit."""
    pos = node_position(p_id)
    if pos is not None:
        return pos
    for key in ("junctions", "exits"):
        for row in load_map().get(key) or []:
            if isinstance(row, dict) and row.get("id") == p_id:
                try:
                    return (float(row["d_n_m"]), float(row["d_e_m"]))
                except (KeyError, TypeError, ValueError):
                    return None
    return None


def baseline_length_mm(a: str, b: str) -> float | None:
    """``L₀`` between two map points, in mm — the pair's unloaded length.

    Millimetres because both consumers are in millimetres (elongation) and one is
    a ratio of them (strain); converting once, here, is what keeps 70 m from
    turning into 28 mm somewhere downstream.
    """
    pa, pb = node_position(a), node_position(b)
    if pa is None or pb is None:
        return None
    return 1000.0 * math.hypot(pb[0] - pa[0], pb[1] - pa[1])


def axis_unit(a: str, b: str) -> tuple[float, float] | None:
    """The unit vector from ``a`` to ``b`` in the map plane, ``(north, east)``.

    Both pairs in this build are collinear **east-west drifts**, so the axis is
    horizontal and the pot's displacement — which the node measures along its own
    drift — lies on it. The general form costs nothing and is what lets a pair be
    declared at an angle later without touching this module.
    """
    pa, pb = node_position(a), node_position(b)
    if pa is None or pb is None:
        return None
    dn, de = pb[0] - pa[0], pb[1] - pa[1]
    length = math.hypot(dn, de)
    if length <= 0:
        return None
    return (dn / length, de / length)


def pair_for_level(level: int | str) -> tuple[str, str] | None:
    """The collinear pair a level carries, or ``None`` if it carries no pair."""
    members = config.LEVELS.get(int(level))
    if not members or len(members) < 2:
        return None
    return (members[0], members[1])


def pair_label(a: str, b: str) -> str:
    """A human name for the pair, taken from the map's own zone name.

    ``"Tunnel A · near"`` → ``"Tunnel A"``. The map owns the name and this reads
    it rather than re-typing "Tunnel A" into the source, so relabelling the
    diorama relabels the readout. Falls back to the node ids, never to a guess.
    """
    zones = {z.get("id"): z for z in (load_map().get("zones") or []) if isinstance(z, dict)}
    for n_id in (a, b):
        z = zones.get((map_nodes().get(n_id) or {}).get("zone"))
        name = str((z or {}).get("name", "")).strip()
        if name:
            return name.split("·")[0].strip()
    return f"{a} ↔ {b}"


def zones_of_level(level: int | str) -> list[str]:
    """Zone ids that hold at least one node of this level."""
    members = config.LEVELS.get(int(level), ())
    nodes = map_nodes()
    out: list[str] = []
    for n_id in members:
        z = (nodes.get(n_id) or {}).get("zone")
        if z and z not in out:
            out.append(z)
    return out


# ---------------------------------------------------------------------------
# Baselines — one zero per node, captured on first sight
# ---------------------------------------------------------------------------

#: ``node_id → displacement_mm`` at the moment the node was first measured.
#: Module-level and deliberately *not* a field on ``NodeState``: it is not part of
#: the wire contract, not a database column and not something the API publishes —
#: the same reasoning as Phase 3's seam accumulator.
_BASE: dict[str, float] = {}


def reset_geomech_baseline(reason: str = "") -> None:
    """Drop every pair baseline. Called by ``SystemState.reset()`` (Re-arm).

    ``reason`` is accepted and ignored on purpose: the *caller* owns the
    operator-facing line, and this module owns no event vocabulary — it is a
    calculator, not an announcer (the same split Phase 3 used for faults).
    """
    _BASE.clear()


def _movement_mm(state, n_id: str) -> float | None:
    """How far this node has moved since its own baseline, mm.

    Returns ``None`` — never ``0.0`` — while the node has not reported a
    displacement at all, because "no measurement" and "did not move" are the two
    statements this whole module exists to keep apart.
    """
    node = getattr(state, "nodes", {}).get(n_id)
    if node is None:
        return None
    disp = getattr(node, "displacement_mm", None)
    if disp is None:
        return None
    disp = float(disp)
    if n_id not in _BASE:
        _BASE[n_id] = disp
    return disp - _BASE[n_id]


# ---------------------------------------------------------------------------
# The four plan functions (§A.2.12)
# ---------------------------------------------------------------------------

def pair_elongation(state, a: str, b: str) -> float | None:
    """``ΔL`` between two collinear nodes, **mm**. Positive = the pair lengthened.

    Sign is stated rather than implied: the two nodes move along the axis between
    them, so the change in distance is ``movement_b − movement_a`` measured along
    ``a → b``. §4.2 writes the row as a magnitude ``√((Δx₂−Δx₁)² + (Δy₂−Δy₁)²)``
    and the magnitude is what §4.6 asserts; the *sign* is this module's addition,
    because "the seam stretched 3 mm" and "the seam closed 3 mm" are not the same
    sentence to an operator, and a formula that can only return ≥ 0 cannot say
    which one happened. ``|ΔL|`` is what the acceptance table checks.
    """
    unit = axis_unit(a, b)
    if unit is None:
        return None
    ma, mb = _movement_mm(state, a), _movement_mm(state, b)
    if ma is None or mb is None:
        return None
    # Collinear nodes make the projection the identity: a node's pot measures its
    # motion along its own drift, and its drift *is* the axis between the pair.
    # So b moving +1 mm away lengthens the pair, and a moving +1 mm toward b
    # shortens it. (``axis_unit`` is still consulted above rather than assumed,
    # because it is also what decides whether the pair has a geometry at all.)
    return float(mb - ma)


def pair_strain(state, a: str, b: str) -> float | None:
    """``ΔL / L₀`` in **µε** — the pair's elongation as strain.

    The scale is why this matters: 3 mm of separation across a 70 m drift is
    42.9 µε, which is a real strain in rock and an invisible displacement on a
    dashboard. The pair layer is what converts one into the other.
    """
    dl = pair_elongation(state, a, b)
    l0 = baseline_length_mm(a, b)
    if dl is None or not l0:
        return None
    return float(_UE * dl / l0)


def differential_settlement(state, level: int | str) -> float | None:
    """``|Δdisp_a − Δdisp_b|`` across a level, **mm**.

    The case §4.2 singles out: two nodes moving *together* is a panel settling,
    which is survivable and often expected; two nodes moving *apart* is the roof
    span changing shape. Both nodes' readings are relative to their own baselines,
    so a pair of pots that rest at different counts does not read as settlement.
    """
    pair = pair_for_level(level)
    if pair is None:
        return None
    ma, mb = _movement_mm(state, pair[0]), _movement_mm(state, pair[1])
    if ma is None or mb is None:
        return None
    return float(abs(ma - mb))


# ---------------------------------------------------------------------------
# Zones — nodes, links, and the leg that carries them
# ---------------------------------------------------------------------------

def zone_rect(zone_id: str) -> tuple[float, float, float, float] | None:
    """``(east_lo, north_lo, east_hi, north_hi)`` in metres, as the map writes it.

    The map's ``rect`` is ``[east_lo, north_lo, east_hi, north_hi]`` — the same
    reading ``/api/map`` already uses to build its polygon — and the bounds are
    **inclusive**, so EXIT1 sitting exactly on A1's northern edge belongs to A1.
    """
    for z in load_map().get("zones") or []:
        if isinstance(z, dict) and z.get("id") == zone_id:
            try:
                x0, y0, x1, y1 = (float(v) for v in z["rect"])
            except (KeyError, TypeError, ValueError):
                return None
            return (x0, y0, x1, y1)
    return None


def _in_rect(pos: tuple[float, float], rect: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = rect
    return (x0 <= pos[1] <= x1) and (y0 <= pos[0] <= y1)


def zone_links(zone_id: str) -> list[tuple[str, str]]:
    """Map links with **both** endpoints inside the zone.

    A link that straddles a boundary — ``J2↔J3`` crosses from A2 into B1 — belongs
    to neither zone, and that is the honest answer: it is the link *between* them,
    so no single zone can be blamed for it. It still gets a health state and it is
    still routed over (Phase 7); it is simply not a zone's property.
    """
    rect = zone_rect(zone_id)
    if rect is None:
        return []
    out: list[tuple[str, str]] = []
    for link in load_map().get("links") or []:
        if not (isinstance(link, (list, tuple)) and len(link) == 2):
            continue
        pa, pb = _point_position(link[0]), _point_position(link[1])
        if pa is None or pb is None:
            continue
        if _in_rect(pa, rect) and _in_rect(pb, rect):
            out.append((str(link[0]), str(link[1])))
    return out


def zone_nodes(zone_id: str) -> list[str]:
    """Node ids the map places inside this zone.

    Every map node counts, including the ones the server does not currently run
    (``active: false`` in the map, or absent from ``ACTIVE_NODES``): a zone's
    risk is a statement about the ground, and ground does not stop existing
    because a board is unplugged. A node with no live status contributes nothing
    — it does not contribute "safe".
    """
    return [n_id for n_id, n in map_nodes().items()
            if isinstance(n, dict) and n.get("zone") == zone_id]


def _node_status(state, n_id: str) -> str | None:
    node = getattr(state, "nodes", {}).get(n_id)
    if node is None:
        return None
    status = getattr(node, "status", None)
    return str(status).upper() if status else None


def _level_leg_state(state, level: int | str) -> str:
    """Phase 2's verdict for this level's relay leg: UP / DOWN / UNKNOWN.

    Imported lazily — ``engine.health`` is a sibling and importing it at module
    scope would be fine, but the *call* is the only thing that needs it and a
    module-level import here would make ``geomech`` unimportable while
    ``engine.health`` is mid-import. One line, no cycle.
    """
    from engine.health import link_state

    lid = _LEG_ID.format(level)
    try:
        rows = link_state(state)
    except Exception:                                        # pragma: no cover
        return "UNKNOWN"
    for row in rows:
        if row.get("id") == lid:
            return str(row.get("state", "UNKNOWN")).upper()
    return "UNKNOWN"


def _link_risk(state, p: str, q: str) -> str:
    """A link is only as safe as the ground it lands on.

    Its own endpoints' node statuses, and — where the link is the one the build
    actually instruments, the ``J2↔J3`` cross-link — Phase 2's health verdict for
    that leg. ``DOWN`` is the only word that raises risk: ``UNKNOWN`` means "has
    never beaten", which is the first minute of a shift, not a hazard (§2.2's own
    distinction).
    """
    risks = [s for s in (_node_status(state, p), _node_status(state, q)) if s]
    if {str(p), str(q)} == {"J2", "J3"}:
        try:
            from engine.health import link_state

            for row in link_state(state):
                if str(row.get("id")) == "J2↔J3" and row.get("state") == "DOWN":
                    risks.append("WARNING")
        except Exception:                                    # pragma: no cover
            pass
    return worst_status(risks) if risks else "NOMINAL"


def zone_risk(state, zone_id: str) -> str:
    """Worst-of the nodes **and** links inside a zone (§4.2), plus the way in.

    A zone whose level hub has gone dark is at least WARNING: the ground may be
    fine, but the mine has stopped being able to see it, and a zone that reports
    NOMINAL while blind is the one failure mode this project exists to avoid.
    """
    risks: list[str] = []

    for n_id in zone_nodes(zone_id):
        s = _node_status(state, n_id)
        if s:
            risks.append(s)
    for p, q in zone_links(zone_id):
        risks.append(_link_risk(state, p, q))

    for lv in (1, 2):
        if zone_id in zones_of_level(lv) and _level_leg_state(state, lv) == "DOWN":
            risks.append("WARNING")

    return worst_status(risks) if risks else "NOMINAL"


def _zone_reason(state, zone_id: str, risk: str) -> str:
    """One short phrase for the UI's tooltip — why this zone is the colour it is."""
    if risk == "NOMINAL":
        return "all quiet"
    blamed = [n_id for n_id in zone_nodes(zone_id)
              if (_node_status(state, n_id) or "NOMINAL") == risk]
    if blamed:
        return "node " + ", ".join(blamed)
    for lv in (1, 2):
        if zone_id in zones_of_level(lv) and _level_leg_state(state, lv) == "DOWN":
            return f"level {lv} relay leg down"
    return "link degrading"


# ---------------------------------------------------------------------------
# The published blocks
# ---------------------------------------------------------------------------

def pair_block(state, level: int | str) -> dict:
    """One level's baseline readout. JSON scalars only (``/api/extended``)."""
    pair = pair_for_level(level)
    if pair is None:
        return {"level": int(level), "a": None, "b": None, "label": None,
                "l0_m": None, "elongation_mm": None, "strain_ue": None,
                "settlement_mm": None, "warn": False,
                "threshold_ue": float(config.PAIR_STRAIN_WARN)}
    a, b = pair
    l0 = baseline_length_mm(a, b)
    dl = pair_elongation(state, a, b)
    strain = None if (dl is None or not l0) else float(_UE * dl / l0)
    settlement = differential_settlement(state, level)
    return {
        "level": int(level),
        "a": a,
        "b": b,
        "label": pair_label(a, b),
        "l0_m": None if l0 is None else round(l0 / 1000.0, 2),
        "elongation_mm": None if dl is None else round(dl, 3),
        "strain_ue": None if strain is None else round(strain, 1),
        "settlement_mm": None if settlement is None else round(settlement, 3),
        "warn": bool(strain is not None and abs(strain) > config.PAIR_STRAIN_WARN),
        "threshold_ue": float(config.PAIR_STRAIN_WARN),
    }


def zone_level(zone_id: str) -> int | None:
    """The level a zone belongs to, read off the nodes the map places in it.

    Published so the deck can colour a *level* from its zones' risks without
    hardcoding which zone is on which level — the map already knows, and a
    second copy of that mapping in JavaScript is how a schematic ends up
    colouring Level 1 amber for a Level 2 problem.
    """
    for n_id in zone_nodes(zone_id):
        lv = (map_nodes().get(n_id) or {}).get("level")
        if lv is not None:
            return int(lv)
    return None


def zone_block(state, zone_id: str) -> dict:
    """One zone's risk with the evidence behind it."""
    risk = zone_risk(state, zone_id)
    rect = zone_rect(zone_id)
    return {
        "risk": risk,
        "level": zone_level(zone_id),
        "nodes": zone_nodes(zone_id),
        "links": [list(lk) for lk in zone_links(zone_id)],
        "reason": _zone_reason(state, zone_id, risk),
        "rect": None if rect is None else [float(v) for v in rect],
    }


def geomech_block(state) -> dict:
    """The whole pair/zone layer, as ``/api/extended`` publishes it.

    Keyed by the level the way ``state.levels`` is keyed (``"1"``/``"2"``), so the
    deck can read a level's pair and its status off the same key. Purely additive:
    nothing already in the payload moves, and a build whose map failed to load
    publishes the same shape with ``null`` values rather than dropping the block.
    """
    pairs = {str(lv): pair_block(state, lv) for lv in sorted(config.LEVELS)}
    zones = {z["id"]: zone_block(state, z["id"])
             for z in (load_map().get("zones") or [])
             if isinstance(z, dict) and z.get("id")}

    worst_zone, worst_risk = None, "NOMINAL"
    for z_id, block in zones.items():
        if STATUS_RANK.get(block["risk"], 0) > STATUS_RANK.get(worst_risk, 0):
            worst_zone, worst_risk = z_id, block["risk"]

    return {
        "pairs": pairs,
        "zones": zones,
        "worst_zone": worst_zone,
        "worst_zone_risk": worst_risk,
        "counts": {"pairs": len(pairs), "zones": len(zones),
                   "warned_pairs": sum(1 for p in pairs.values() if p["warn"])},
    }


def level_is_warned(state, level: int | str) -> bool:
    """Does this level's pair strain exceed ``PAIR_STRAIN_WARN``?

    The one predicate ``SystemState._update_levels`` calls, so the escalation rule
    lives beside the number that defines it instead of being retyped in the state
    engine — §4.5's *"a pair strain above PAIR_STRAIN_WARN raises the level status
    to WARNING"*.
    """
    pair = pair_for_level(level)
    if pair is None:
        return False
    strain = pair_strain(state, *pair)
    return bool(strain is not None and abs(strain) > config.PAIR_STRAIN_WARN)


__all__ = [
    "load_map", "map_nodes", "node_position", "baseline_length_mm", "axis_unit",
    "pair_for_level", "pair_label", "zones_of_level",
    "reset_geomech_baseline",
    "pair_elongation", "pair_strain", "differential_settlement",
    "zone_rect", "zone_links", "zone_nodes", "zone_level", "zone_risk",
    "pair_block", "zone_block", "geomech_block", "level_is_warned",
]
