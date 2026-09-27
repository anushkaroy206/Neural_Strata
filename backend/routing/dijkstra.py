"""B5 — hazard-weighted shortest **safe** path to an exit.  Phase 7.

Why cost and not distance
-------------------------
A 30 m corridor past a node in tertiary creep is worse than a 45 m corridor that
is clean, and a node we have lost contact with has to be treated as suspect
rather than ignored. So the edge weight is

    w(link) = ∞                                      if the link is BLOCKED
            = base_m × (1 + hazard_penalty(a, b))     otherwise

and ``dijkstra`` minimises the sum. Distances are real: `base_m` is the Euclidean
length of the leg between the two endpoints' survey offsets, read from the map.

Dijkstra and not A*: the graph has **eight vertices**. A* needs an admissible
heuristic to be correct, and with eight vertices the heuristic saves microseconds
while adding a correctness argument that has to be defended. Dijkstra is the right
algorithm *because the graph is tiny*.

Fail-safe
---------
When no path exists the engine does not pick the least-bad one. It returns a HOLD
— "stay where you are" — and the junction lights go amber. Pointing a green
chase into a collapsing tunnel is worse than telling someone to stand still.

The one exception, stated because it is deliberate: **you may always leave the
node you are standing on**, even if that node is the thing that is alarming. The
alternative is telling a person to remain inside a failing drift because their
own sensor blocked the only door.
"""

from __future__ import annotations

import heapq
import json
import math
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from pathlib import Path

import config

BLOCKED = float("inf")

#: Scores, kept as module constants so the report and the tests read the same
#: numbers the running server does. ``config`` exports them for the deck.
HAZARD_COST = dict(getattr(config, "HAZARD_COST", {
    "CRITICAL": 4.0,
    "WARNING": 1.5,
    "STALE": 2.5,
}))
CRACK_HAZARD = dict(getattr(config, "CRACK_HAZARD", {
    "PROPAGATION": 1.0,
    "CRITICAL": 4.0,
}))
WALK_SPEED_MPS = float(getattr(config, "WALK_SPEED_MPS", 1.2))

#: The five patterns the level hubs render (§7.6). Kept tiny and unambiguous,
#: because it has to be legible from across a room during a demo.
PATTERNS = {
    "flow_green":    {"colour": "green", "period_ms": 400, "buzzer": False,
                      "meaning": "route open — walk this way"},
    "advisory_amber": {"colour": "amber", "period_ms": 1500, "buzzer": False,
                       "meaning": "environmental disturbance — no evacuation"},
    "hold_amber":    {"colour": "amber", "period_ms": 300, "buzzer": False,
                      "meaning": "no safe path — do not move"},
    "chase_red":     {"colour": "red", "period_ms": 120, "buzzer": False,
                      "meaning": "route blocked"},
    "strobe_red":    {"colour": "red", "period_ms": 100, "buzzer": True,
                      "meaning": "evacuate now"},
}

#: The hub pin is not the pattern flag. Amber strips get the three-beep advisory
#: on both levels. Red strips get the continuous siren. Green is silent. A red
#: junction wins over an amber one, because the two sounds cannot play together.
AMBER_PATTERNS = frozenset({"advisory_amber", "hold_amber"})
RED_PATTERNS = frozenset({"chase_red", "strobe_red"})


def sound_mode(patterns) -> str:
    """``siren`` if any light is red, ``beep`` if any is amber, else ``quiet``."""
    names = set(patterns)
    if names & RED_PATTERNS:
        return "siren"
    if names & AMBER_PATTERNS:
        return "beep"
    return "quiet"


def alarm_from_status(statuses) -> tuple[str, str, str]:
    """What the level hubs are told, from the live readings.

    Creep is a warning: amber strips, the siren beeps three times, the red
    gate lamp stays on, and the green lamp is off. Failure is critical: red
    strips, the siren is continuous, and the red lamp blinks. Anything else
    is a normal board — green on, red off, silent. A blocked-route paint is
    not allowed to promote a creep into a failure.
    """
    names = {str(s or "").upper() for s in statuses}
    if "CRITICAL" in names:
        return "failure", "siren", "strobe_red"
    if "WARNING" in names:
        return "creep", "beep", "advisory_amber"
    return "nominal", "quiet", "flow_green"


def junction_for_nodes(node_ids) -> tuple[str, ...]:
    """Which strips a node's reading owns.

    Node 1 or node 2 lights junction 1 and junction 3. Node 3 or node 4
    lights junction 2 and junction 4. The creep and failure patterns
    themselves are unchanged.
    """
    names = {str(n) for n in node_ids}
    out: list[str] = []
    if names & {"node_1", "node_2"}:
        out.extend(("J1", "J3"))
    if names & {"node_3", "node_4"}:
        out.extend(("J2", "J4"))
    return tuple(out)


def sound_commands(mode: str) -> list["HubCommand"]:
    """Both level hubs. Beep and siren are mutually exclusive.

    The main hub replays only the last line it was given, every couple of
    seconds. The command that has to keep winning is therefore last: the
    siren-off on the way back to green, otherwise a cleared beep leaves the
    horn and the red lamp latched on.
    """
    siren = mode == "siren"
    beep = mode == "beep"
    why = {"siren": "failure — continuous siren, red blinking, green off",
           "beep": "creep — three beeps, red on, green off",
           "quiet": "readings normal — green, silent"}.get(mode, "green — silent")
    out: list[HubCommand] = []
    for level in (1, 2):
        if mode == "siren":
            pair = (("advisory", False), ("siren", True))
        elif mode == "beep":
            pair = (("siren", False), ("advisory", True))
        else:
            pair = (("advisory", False), ("siren", False))
        for cmd, on in pair:
            out.append(HubCommand(hub=f"L{level}", cmd=cmd, level=level,
                                  on=on, reason=why))
    return out


class MapError(ValueError):
    """The tunnel map is missing, malformed, or internally inconsistent."""


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SiteMap:
    """The diorama as a graph. Frozen: a route is computed against one snapshot."""

    site: dict
    nodes: dict[str, dict]
    junctions: tuple[dict, ...]
    exits: tuple[dict, ...]
    links: tuple[tuple[str, str], ...]
    zones: tuple[dict, ...]
    coords: dict[str, tuple[float, float]]          # id -> (d_n_m, d_e_m)
    lengths: dict[tuple[str, str], float]           # frozenset key -> metres
    adjacency: dict[str, tuple[str, ...]]

    # -- lookups ----------------------------------------------------------
    def points(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for n_id, n in self.nodes.items():
            out[n_id] = {**n, "id": n_id, "kind": "node"}
        for j in self.junctions:
            out[j["id"]] = {**j, "kind": "junction"}
        for e in self.exits:
            out[e["id"]] = {**e, "kind": "exit"}
        return out

    def active_nodes(self) -> tuple[str, ...]:
        return tuple(n for n, d in self.nodes.items() if d.get("active", True))

    def level_of(self, point_id: str) -> int | None:
        p = self.points().get(point_id) or {}
        return p.get("level")

    def length(self, a: str, b: str) -> float:
        key = (a, b) if (a, b) in self.lengths else (b, a)
        return self.lengths.get(key, 0.0)

    def node_zone(self, node_id: str) -> str | None:
        return (self.nodes.get(node_id) or {}).get("zone")


def _link_key(a: str, b: str) -> tuple[str, str]:
    """Canonical undirected key. The map's links are undirected corridors."""
    return (a, b) if a <= b else (b, a)


def _metres_between(p: tuple[float, float], q: tuple[float, float]) -> float:
    return math.hypot(q[0] - p[0], q[1] - p[1])


def validate_map(raw: dict) -> dict:
    """Validate a parsed ``tunnel_map.json``. Raise loudly; never half-load.

    §7.6 is blunt about this and it is right: *"a map that loads lazily is a map
    that fails during a demo."* So every problem is found here, at boot, with the
    field named in the message.
    """
    if not isinstance(raw, dict):
        raise MapError("tunnel map is not an object")
    for key in ("nodes", "junctions", "exits", "links", "zones"):
        if key not in raw:
            raise MapError(f"tunnel map has no '{key}'")
    if not isinstance(raw["nodes"], dict) or not raw["nodes"]:
        raise MapError("tunnel map 'nodes' must be a non-empty object")
    if not raw["exits"]:
        raise MapError("tunnel map has no exits — nobody can be routed out")

    known: set[str] = set(raw["nodes"])
    for j in raw["junctions"]:
        known.add(j["id"])
    for e in raw["exits"]:
        known.add(e["id"])

    for n_id, n in raw["nodes"].items():
        for k in ("d_n_m", "d_e_m"):
            if not isinstance(n.get(k), (int, float)):
                raise MapError(f"node '{n_id}' has no numeric '{k}'")
    for group, label in ((raw["junctions"], "junction"), (raw["exits"], "exit")):
        for item in group:
            if not item.get("id"):
                raise MapError(f"a {label} has no id")
            for k in ("d_n_m", "d_e_m"):
                if not isinstance(item.get(k), (int, float)):
                    raise MapError(f"{label} '{item['id']}' has no numeric '{k}'")

    seen: set[tuple[str, str]] = set()
    for link in raw["links"]:
        if not isinstance(link, (list, tuple)) or len(link) < 2:
            raise MapError(f"link {link!r} is not a [from, to] pair")
        a, b = str(link[0]), str(link[1])
        if a not in known or b not in known:
            raise MapError(f"link {a}→{b} references an unknown point")
        if a == b:
            raise MapError(f"link {a}→{b} is a self-loop")
        key = _link_key(a, b)
        if key in seen:
            raise MapError(f"link {a}→{b} is declared twice")
        seen.add(key)

    for z in raw["zones"]:
        if not z.get("id"):
            raise MapError("a zone has no id")
        rect = z.get("rect")
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
            raise MapError(f"zone '{z['id']}' needs a 4-number rect")

    # Every level that has nodes must be able to reach an exit, or the map is
    # telling us to build a level with no way out. This is a *map* check, not a
    # live check: it uses the calm-conditions graph, so a broken map is caught
    # at boot rather than discovered during the collapse it was meant to escape.
    calm = _build_adjacency(raw, blocked=frozenset())
    exit_ids = {e["id"] for e in raw["exits"]}
    reachable = _reachable_from(calm, exit_ids)
    for n_id, n in raw["nodes"].items():
        if n.get("active", True) and n_id not in reachable:
            raise MapError(
                f"node '{n_id}' cannot reach any exit in the calm graph — "
                "the map has an isolated node")
    return raw


def _build_adjacency(raw: dict, blocked: frozenset) -> dict[str, tuple[str, ...]]:
    adj: dict[str, list[str]] = {}
    for link in raw["links"]:
        a, b = str(link[0]), str(link[1])
        if _link_key(a, b) in blocked:
            continue
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    return {k: tuple(v) for k, v in adj.items()}


def _reachable_from(adj: dict[str, tuple[str, ...]], targets: set[str]) -> set[str]:
    """Every point from which some target is reachable, walking backwards."""
    rev: dict[str, list[str]] = {}
    for a, neighbours in adj.items():
        for b in neighbours:
            rev.setdefault(b, []).append(a)
    seen = set(targets)
    stack = list(targets)
    while stack:
        cur = stack.pop()
        for prev in rev.get(cur, ()):
            if prev not in seen:
                seen.add(prev)
                stack.append(prev)
    return seen


def load_tunnel_map(path: str | Path | None = None) -> SiteMap:
    """Parse + validate ``tunnel_map.json`` into a :class:`SiteMap`."""
    path = Path(path or config.ASSET_FILES["tunnel_map"])
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise MapError(f"tunnel map not found at {path}") from exc
    except json.JSONDecodeError as exc:
        raise MapError(f"tunnel map at {path} is not valid JSON: {exc}") from exc
    validate_map(raw)

    coords: dict[str, tuple[float, float]] = {}
    for n_id, n in raw["nodes"].items():
        coords[n_id] = (float(n["d_n_m"]), float(n["d_e_m"]))
    for j in raw["junctions"]:
        coords[j["id"]] = (float(j["d_n_m"]), float(j["d_e_m"]))
    for e in raw["exits"]:
        coords[e["id"]] = (float(e["d_n_m"]), float(e["d_e_m"]))

    lengths: dict[tuple[str, str], float] = {}
    for link in raw["links"]:
        a, b = str(link[0]), str(link[1])
        lengths[_link_key(a, b)] = _metres_between(coords[a], coords[b])

    return SiteMap(
        site=raw.get("site", {}),
        nodes=dict(raw["nodes"]),
        junctions=tuple(raw["junctions"]),
        exits=tuple(raw["exits"]),
        links=tuple((str(a), str(b)) for a, b in raw["links"]),
        zones=tuple(raw["zones"]),
        coords=coords,
        lengths=lengths,
        adjacency=_build_adjacency(raw, frozenset()),
    )


# ---------------------------------------------------------------------------
# Live cost
# ---------------------------------------------------------------------------

def _node_risk(state, node_id: str) -> float:
    """Hazard score of one sensor node, from what the node is actually saying.

    Two independent channels, summed rather than maxed: a node can be reporting a
    benign status while its crack engine is in PROPAGATION (the crack engine sees
    the *micro*-crack before the macro displacement moves), and the reverse. Sum
    is the conservative reading and — being monotone in both — never makes a route
    look safer than either channel alone.
    """
    node = getattr(state, "nodes", {}).get(node_id)
    if node is None:
        return 0.0
    score = 0.0
    if getattr(node, "stale", False):
        # UNKNOWN is not safe (Phase 2's rule). A node we cannot hear is priced
        # like a WARNING-and-a-half: worse than a known warning, because we do not
        # know whether it is worse.
        score += HAZARD_COST["STALE"]
    else:
        score += HAZARD_COST.get(str(getattr(node, "status", "")).upper(), 0.0)
    score += CRACK_HAZARD.get(str(getattr(node, "crack_stage", "")).upper(), 0.0)
    return score


def _attached_nodes(site: SiteMap, point_id: str) -> tuple[str, ...]:
    """Sensor nodes bolted directly to ``point_id`` (or itself if it is a node)."""
    if point_id in site.nodes:
        return (point_id,)
    return tuple(n for n, neighbours in site.adjacency.items()
                 if n in site.nodes and point_id in neighbours)


def hazard_penalty(state, a: str, b: str, site: SiteMap | None = None) -> float:
    """Risk accumulated along the leg ``a``–``b``.

    §7.3 says "Σ over endpoint nodes". A junction is not a node, though, and the
    corridor it sits on is exactly where its node's drift meets traffic — so an
    endpoint junction inherits the hazard of the nodes bolted to it. Without that
    rule the J2–J3 cross-link would score zero no matter what was happening at
    J2, which is the opposite of what a hazard-weighted cost is for.
    """
    site = site or getattr(state, "site_map", None)
    ids: list[str] = []
    for point in (a, b):
        if site is None:
            if point in getattr(state, "nodes", {}):
                ids.append(point)
        else:
            ids.extend(_attached_nodes(site, point))
    seen: set[str] = set()
    total = 0.0
    for n_id in ids:
        if n_id in seen:
            continue
        seen.add(n_id)
        total += _node_risk(state, n_id)
    return total


def linked_above(state, point_id: str, site: SiteMap | None = None,
                 ignore=()) -> bool:
    """Is any node at ``point_id`` alarming? (SRS §5.5's blocked-link rule.)

    ``ignore`` is the *source* exception — see :func:`build_graph`. A node whose
    alarm is ignored still pays its hazard in the cost; it just does not close the
    door behind the person standing next to it.
    """
    site = site or getattr(state, "site_map", None)
    ignore = set(ignore or ())
    ids = [n for n in (_attached_nodes(site, point_id) if site else (point_id,))
           if n not in ignore]
    for n_id in ids:
        node = getattr(state, "nodes", {}).get(n_id)
        if node is None:
            continue
        if str(getattr(node, "status", "")).upper() in ("WARNING", "CRITICAL"):
            return True
    return False


def _link_blocked(state, link: tuple[str, str], site: SiteMap | None = None,
                  ignore=()) -> bool:
    """SRS §5.5, refined to the segment.

    *"if a node above a tunnel is WARNING or CRITICAL, that tunnel's route
    segments must be blocked for both the UI and the physical hub LEDs."* The
    v4.4 server implemented that as one boolean for the whole site. Per-segment is
    strictly more informative and still obeys the rule: a leg is blocked when an
    endpoint is an alarming node, or when an endpoint junction has one bolted to
    it.
    """
    a, b = link
    return (linked_above(state, a, site, ignore)
            or linked_above(state, b, site, ignore))


def link_cost(state, link: tuple[str, str], base_m: float,
              site: SiteMap | None = None) -> float:
    """``∞`` when blocked, else ``base_m × (1 + hazard)``."""
    if _link_blocked(state, link, site):
        return BLOCKED
    return float(base_m) * (1.0 + hazard_penalty(state, link[0], link[1], site))


def build_graph(site: SiteMap, state, source: str | None = None,
                allow_leave: bool = True) -> dict[str, list[tuple[str, float]]]:
    """Adjacency with live costs.

    Inactive nodes are **excluded**, not routed around as if they were dead
    (§7.6): an unwired node is not a hazard, it is not there yet. Its stub simply
    does not exist.

    ``allow_leave`` generalises the module docstring's one exception: **for this
    source, the source node's own alarm never blocks a leg** — not its stub, and
    not the legs it is attached to. It still *prices* them (the hazard term is
    untouched), so the person's own failing drift becomes the most expensive thing
    on the site rather than a wall. Telling someone to stand still inside the
    drift that is failing because their own sensor closed the door is the one
    answer this module refuses to give.
    """
    graph: dict[str, list[tuple[str, float]]] = {p: [] for p in site.points()}
    ignore = (source,) if (allow_leave and source in site.nodes) else ()
    blocked = frozenset(_link_key(a, b) for a, b in site.links
                        if _link_blocked(state, (a, b), site, ignore))

    for a, b in site.links:
        if a in site.nodes and a not in site.active_nodes():
            continue
        if b in site.nodes and b not in site.active_nodes():
            continue
        key = _link_key(a, b)
        base = site.lengths.get(key, 0.0)
        cost = BLOCKED if key in blocked else base * (1.0 + hazard_penalty(state, a, b, site))
        graph[a].append((b, cost))
        graph[b].append((a, cost))
    return graph


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------

def dijkstra(graph: dict[str, list[tuple[str, float]]], src: str,
             exits) -> tuple[list[str], float]:
    """Weighted shortest path from ``src`` to the nearest reachable exit.

    Returns ``([], ∞)`` when nothing is reachable — the caller turns that into a
    HOLD. Ties break on the path itself (``heapq`` orders the tuple), so the same
    inputs always produce the same route: a route that flickers between two
    equally good paths would be unreadable on the wall.
    """
    targets = set(exits if not isinstance(exits, str) else (exits,))
    if src in targets:
        return [src], 0.0

    dist: dict[str, float] = {src: 0.0}
    prev: dict[str, str] = {}
    queue: list[tuple[float, tuple[str, ...], str]] = [(0.0, (src,), src)]
    settled: set[str] = set()

    while queue:
        cost, path, node = heapq.heappop(queue)
        if node in settled:
            continue
        settled.add(node)
        if node in targets:
            return list(path), cost
        for neighbour, weight in graph.get(node, ()):
            if weight == BLOCKED or not math.isfinite(weight):
                continue
            if neighbour in settled:
                continue
            new_cost = cost + weight
            if new_cost < dist.get(neighbour, BLOCKED) - 1e-12:
                dist[neighbour] = new_cost
                prev[neighbour] = node
                heapq.heappush(queue, (new_cost, path + (neighbour,), neighbour))
    return [], BLOCKED


def base_length(site: SiteMap, path) -> float:
    """Plain metres along a path — what the ETA is computed from."""
    return sum(site.lengths.get(_link_key(a, b), 0.0) for a, b in zip(path, path[1:]))


@dataclass
class RouteResult:
    """One person's way out, with the reason it came out that way."""

    src: str
    exit: str | None
    path: tuple[str, ...]
    cost: float
    base_m: float
    hazard: float
    eta_s: float | None
    state: str                      # OPEN | HOLD | ARRIVED
    reason: str
    blocked: tuple[tuple[str, str], ...] = ()
    level: int | None = None

    @property
    def held(self) -> bool:
        return self.state == "HOLD"

    def to_dict(self) -> dict:
        return {
            "src": self.src,
            "exit": self.exit,
            "path": list(self.path),
            "cost": None if not math.isfinite(self.cost) else round(self.cost, 2),
            "base_m": round(self.base_m, 1),
            "hazard": round(self.hazard, 2),
            "eta_s": None if self.eta_s is None else round(self.eta_s),
            "state": self.state,
            "reason": self.reason,
            "blocked": [list(b) for b in self.blocked],
            "level": self.level,
        }


def blocked_links(site: SiteMap, state, ignore=()) -> tuple[tuple[str, str], ...]:
    return tuple((a, b) for a, b in site.links
                 if _link_blocked(state, (a, b), site, ignore))


def blocked_causes(state, site: SiteMap) -> dict[tuple[str, str], list[str]]:
    """Which alarming node closed which leg.

    The GIS card colours a zone by the worst thing inside it, and "a leg through
    this zone is blocked" is only *about* this zone when the node that closed it
    is standing here. Without the attribution, a node in Tunnel A's near section
    painted Tunnel A's far section amber — which is a true statement about the
    corridor and a false one about the ground.
    """
    out: dict[tuple[str, str], list[str]] = {}
    for a, b in site.links:
        causes: list[str] = []
        for point in (a, b):
            for n_id in _attached_nodes(site, point):
                node = getattr(state, "nodes", {}).get(n_id)
                if node is not None and str(
                        getattr(node, "status", "")).upper() in ("WARNING", "CRITICAL"):
                    causes.append(n_id)
        if causes:
            out[(a, b)] = causes
    return out


def nearest_exit(site: SiteMap, state, src: str) -> str | None:
    """The exit closest to ``src`` **as the crow flies**.

    Used only when the graph is dark, and only to answer "which way is out?" in
    the HOLD text. It is deliberately *not* a route: straight-line distance
    ignores the corridor, and a HOLD that names a direction is a HOLD that has
    already given up on the detail.
    """
    if src not in site.coords:
        return None
    here = site.coords[src]
    best, best_d = None, BLOCKED
    for e in site.exits:
        d = _metres_between(here, site.coords[e["id"]])
        if d < best_d:
            best, best_d = e["id"], d
    return best


def route_for_person(site: SiteMap, state, src: str) -> RouteResult:
    """Route + exit + ETA + reason for a person at graph point ``src``."""
    if src not in site.points():
        return RouteResult(src=src, exit=None, path=(), cost=BLOCKED, base_m=0.0,
                           hazard=0.0, eta_s=None, state="HOLD",
                           reason=f"position '{src}' is not on the map",
                           level=None)

    exit_ids = tuple(e["id"] for e in site.exits)
    if src in exit_ids:
        return RouteResult(src=src, exit=src, path=(src,), cost=0.0, base_m=0.0,
                           hazard=0.0, eta_s=0.0, state="ARRIVED",
                           reason="already at an exit",
                           level=site.level_of(src))

    graph = build_graph(site, state, source=src)
    path, cost = dijkstra(graph, src, exit_ids)
    blocked = blocked_links(site, state)
    hazard = max(0.0, cost - base_length(site, path)) if path else 0.0

    if not path:
        alt = nearest_exit(site, state, src)
        return RouteResult(
            src=src, exit=None, path=(), cost=BLOCKED, base_m=0.0, hazard=0.0,
            eta_s=None, state="HOLD", blocked=blocked, level=site.level_of(src),
            reason=(f"no safe path — every route out of {src} is blocked"
                    + (f" (nearest exit is {alt}, but the corridor to it is not safe)"
                       if alt else "")),
        )

    base = base_length(site, path)
    eta = base / WALK_SPEED_MPS if WALK_SPEED_MPS > 0 else None
    worst = _worst_hazard_on(site, state, path)
    reason = (f"{len(path) - 1} legs · {base:.0f} m at {WALK_SPEED_MPS:.1f} m/s"
              + (f" · risk-weighted ×{cost / base:.2f}" if base > 0 else "")
              + (f" · {worst}" if worst else ""))
    return RouteResult(
        src=src, exit=path[-1], path=tuple(path), cost=cost, base_m=base,
        hazard=hazard, eta_s=eta, state="OPEN", reason=reason,
        blocked=blocked, level=site.level_of(src),
    )


def _worst_hazard_on(site: SiteMap, state, path) -> str:
    worst, worst_score = None, 0.0
    seen: set[str] = set()
    for a, b in zip(path, path[1:]):
        for point in (a, b):
            for n_id in _attached_nodes(site, point):
                if n_id in seen:
                    continue
                seen.add(n_id)
                score = _node_risk(state, n_id)
                if score > worst_score:
                    worst, worst_score = n_id, score
    if worst is None:
        return ""
    node = state.nodes.get(worst)
    label = "stale" if getattr(node, "stale", False) else \
        str(getattr(node, "status", "")).lower()
    stage = str(getattr(node, "crack_stage", "")).upper()
    if stage in CRACK_HAZARD:
        label += f"/{stage.lower()}"
    return f"passes {worst} ({label})"


# ---------------------------------------------------------------------------
# Position, fail-safe, and the hub lights
# ---------------------------------------------------------------------------

#: RFID reader → the level whose crew it reports. The reader knows the *level*,
#: not the person's position in it, and pretending otherwise would be inventing
#: precision the hardware does not have.
READER_LEVEL = {"L1": 1, "L2": 2}


def resolve_position(state, miner_uid: str, site: SiteMap | None = None) -> str:
    """RFID zone → graph node.

    The reader resolves a **level**, and the crew is somewhere on it. So the route
    is planned from the worst node on that level — the conservative reading: if
    the plan is safe from there, it is safe from anywhere on the level, and it
    never flatters the situation. A miner already at ``Safe Exit`` resolves to the
    exit itself, and gets no route at all.
    """
    site = site or getattr(state, "site_map", None)
    zone = (getattr(state, "zones", {}) or {}).get(str(miner_uid), "")
    if zone == "Safe Exit":
        return "EXIT_SURFACE"
    level = {"Tunnel A": 1, "Evacuating A": 1,
             "Tunnel B": 2, "Evacuating B": 2}.get(zone)
    if level is None or site is None:
        return ""

    members = [n for n in site.active_nodes() if site.level_of(n) == level]
    if not members:
        return ""
    return max(members, key=lambda n: _node_risk(state, n))


def fail_safe_hold(state, junction: str) -> None:
    """Log the HOLD and record it. **No green pattern is ever emitted for it.**

    The junction keeps an amber pulse (``hold_amber``), which reads as *stop*
    rather than *go*, and the event goes into the same log every other verdict
    does, so the black box has the moment the site ran out of exits.
    """
    holds = getattr(state, "underground", None)
    if isinstance(holds, dict):
        hold_list = holds.setdefault("holds", [])
        if junction not in hold_list:
            hold_list.append(junction)
    try:
        state.note_event(state.system_status,
                         f"EVACUATION HOLD — no safe path from {junction}. "
                         "Amber pulse; crews to stay in place.")
    except Exception:                                   # noqa: BLE001
        pass


def clear_holds(state) -> None:
    """Called from a Re-arm: a hold survives until the site is re-armed."""
    underground = getattr(state, "underground", None)
    if isinstance(underground, dict):
        underground["holds"] = []


@dataclass
class HubCommand:
    """One down-link instruction for the hub that owns a junction's strip.

    Phase 8 widened this from "an LED command" to "one command of the vocabulary
    §H6.6 puts on the wire". The names are the specs' (`led`, `pattern`, `siren`,
    `advisory`) and the bodies are the specs' too — `{"cmd":"siren","level":2,
    "on":true}` is quoted verbatim in both build-phase documents, so the hub's
    parser and this encoder are the same sentence read from either end.

    `cmd="led"` remains the default, which is why every Phase-7 call site still
    builds exactly the frame it built before.
    """

    hub: str                       # "L1" | "L2"
    junction: str = ""             # "J1" .. "J4"   (led/pattern)
    pattern: str = "hold_amber"    # a key of PATTERNS
    reason: str = ""
    cmd: str = "led"               # led | pattern | siren | advisory
    level: int = 0                 # siren/advisory address a *level*, not a junction
    on: bool = True                # siren/advisory: sound / breathe, or stop
    _fields: dict = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        """The JSON body. One shape per command, none of them invented here."""
        if self.cmd in ("siren", "advisory"):
            return {"cmd": self.cmd, "level": self.level_of(), "on": bool(self.on)}
        spec = PATTERNS.get(self.pattern, PATTERNS["hold_amber"])
        body = {
            "cmd": "pattern" if self.cmd == "pattern" else "led",
            "junction": self.junction,
            "pattern": self.pattern,
            "colour": spec["colour"],
            "period_ms": spec["period_ms"],
            "buzzer": spec["buzzer"],
            "reason": self.reason or spec["meaning"],
        }
        if body["cmd"] == "pattern" and not self.junction:
            # A level-wide `pattern` has **no** junction, and an empty string is not
            # the same as an absent key to a JSON parser: the hub would find the
            # key, fail to match "J1..J4" against "", and refuse the whole frame.
            # Omit it, so "the whole level" says exactly that.
            body.pop("junction")
        return body

    def level_of(self) -> int:
        """Which level this command addresses.

        `level` wins when set; otherwise the junction letter decides, because J1/J2
        are level 1 and J3/J4 are level 2 (§A.2.3) and a caller that names a
        junction has already answered the question. `hub` is the last resort.
        """
        if self.level:
            return int(self.level)
        if len(self.junction) >= 2 and self.junction[0] == "J" and self.junction[1].isdigit():
            return 1 if int(self.junction[1]) <= 2 else 2
        digits = "".join(ch for ch in str(self.hub) if ch.isdigit())
        return int(digits) if digits else 1

    def to_line(self) -> str:
        """The wire format: ``#CMD`` sentinel + one JSON object.

        The sentinel is what makes the down-link unambiguous during bring-up —
        the hub ignores any *uplink* line beginning with ``#CMD``, and the server
        ignores any *read* line beginning with it. Cheap, explicit, testable.
        """
        return "#CMD " + json.dumps(self.to_dict(), separators=(",", ":"))


#: Set once the hub's line exists. ``None`` means SIM — every push returns False
#: rather than pretending, so the deck can tell "hub said yes" from "no hub".
_DOWNLINK = None


def attach_downlink(send) -> None:
    """Register the write side of the hub's line (the black box owns the port)."""
    global _DOWNLINK
    _DOWNLINK = send


def downlink_attached() -> bool:
    return _DOWNLINK is not None


#: The junction inbox (Phase 8). What each junction was **last told**, and whether
#: the line took it. It exists because "the strip is showing X" is otherwise
#: unknowable from the server: the plan is computed, a frame is written, and if the
#: cable is out the only record is a `False` nobody keeps. One dict, written at the
#: door every frame goes through, read by the Alerts panel and by the bench tool.
_INBOX: dict = {}
_INBOX_STATS = {"pushed": 0, "refused": 0, "reasserted": 0}

#: Junction-free commands (siren, advisory) are keyed by level instead — they are
#: addressed to a board, not a lamp, and filing them under a junction would invent
#: a lamp that does not exist.
def _inbox_key(cmd: HubCommand) -> str:
    if cmd.cmd in ("siren", "advisory"):
        return f"L{cmd.level_of()}:{cmd.cmd}"
    if cmd.junction:
        return cmd.junction
    return f"L{cmd.level_of()}:led"


def note_delivery(cmd: HubCommand, ok: bool, detail: str = "") -> None:
    """Record one down-link attempt against its junction. Never raises."""
    try:
        body = cmd.to_dict()
        _INBOX[_inbox_key(cmd)] = {
            "key": _inbox_key(cmd),
            "cmd": body.get("cmd"),
            "hub": cmd.hub,
            "junction": body.get("junction", ""),
            "level": cmd.level_of(),
            "pattern": body.get("pattern", ""),
            "colour": body.get("colour", ""),
            "period_ms": body.get("period_ms", 0),
            "buzzer": bool(body.get("buzzer", body.get("on", False))),
            "reason": body.get("reason", ""),
            "delivered": bool(ok),
            "detail": detail,
            "ts": time.time(),
        }
        _INBOX_STATS["pushed" if ok else "refused"] += 1
    except Exception:                                   # noqa: BLE001
        pass


def junction_inbox() -> list:
    """The inbox, one entry per junction/board, junctions first. A copy."""
    return [dict(_INBOX[k]) for k in
            sorted(_INBOX, key=lambda k: (k.startswith("L"), k))]


def inbox_stats() -> dict:
    return dict(_INBOX_STATS)


def note_reassert() -> None:
    _INBOX_STATS["reasserted"] += 1


def reset_inbox() -> None:
    """Bench/test helper: forget everything. The server never calls this."""
    _INBOX.clear()
    for k in _INBOX_STATS:
        _INBOX_STATS[k] = 0


def push_hub_command(cmd: HubCommand) -> bool:
    """Serial down-link to the hub (LEDs, siren, advisory). ``False`` when there is none."""
    if _DOWNLINK is None:
        note_delivery(cmd, False, "no hub attached")
        return False
    try:
        _DOWNLINK(cmd.to_line())
        note_delivery(cmd, True)
        return True
    except Exception as exc:                            # noqa: BLE001
        note_delivery(cmd, False, f"{type(exc).__name__}: {exc}")
        return False


def send_downlink(payload) -> bool:
    """Write **one** down-link line to the hub, returning whether it went.

    `push_hub_command` is the LED path and speaks in :class:`HubCommand`. This is
    the same line for frames that are not LED commands — Phase 8's modem frame is
    the first of them — and it exists so that the alert layer never reaches for
    `_DOWNLINK` directly. One door, one place to add the `#CMD` sentinel.
    """
    if _DOWNLINK is None:
        return False
    line = payload if isinstance(payload, str) else (
        "#CMD " + json.dumps(payload, separators=(",", ":")))
    try:
        _DOWNLINK(line)
        return True
    except Exception:                                   # noqa: BLE001
        return False


def junction_led_plan(site: SiteMap, state, routes) -> list[HubCommand]:
    """What every junction's strip should be doing right now.

    Precedence, most urgent first — a junction that is both on the escape route
    and next to a failing node has to flash the hazard, not the invitation:

    1. **every** leg blocked            → ``hold_amber``   (fail-safe — do not move)
    2. **some** leg blocked             → ``chase_red``    (not this way)
    3. an evacuation is running         → ``strobe_red``   (leave now, this way)
    4. on an open escape path           → ``flow_green``
    5. a nearby node is WARNING         → ``advisory_amber``
    6. quiet                            → ``flow_green``

    The fail-safe is checked **first**, ahead of the blocked-leg flash, because a
    block says *where not to go* and a hold says *do not go anywhere*: the hold is
    the strictly stronger instruction, so a junction with nowhere to send anyone
    must say "stay" rather than paint every exit red and let the operator pick.
    """
    # Callers hand this either the router's own objects or the dicts that
    # `site_routes` publishes (state.py reads the plan every status pass). One
    # normalisation here beats two shapes of caller.
    def _view(r):
        if isinstance(r, dict):
            return SimpleNamespace(state=r.get("state"), path=tuple(r.get("path") or ()))
        return r

    routes = [_view(r) for r in routes]
    open_routes = [r for r in routes if r.state == "OPEN"]
    any_hold = any(r.state == "HOLD" for r in routes)
    evacuating = any(getattr(n, "status", "") == "CRITICAL"
                     for n in (getattr(state, "nodes", {}) or {}).values())
    on_path: set[tuple[str, str]] = set()
    for r in open_routes:
        for a, b in zip(r.path, r.path[1:]):
            on_path.add(_link_key(a, b))
    blocked = set(blocked_links(site, state))

    plan: list[HubCommand] = []
    for j in site.junctions:
        j_id = j["id"]
        hub = f"L{j.get('level', 1)}"
        legs = [_link_key(j_id, other) for other in site.adjacency.get(j_id, ())]
        blocked_legs = [leg for leg in legs if leg in blocked]
        # "Cut off" means **no path to any exit from here** — asked of the router
        # rather than counted off the blocked set, because a junction whose only
        # open leg leads to a dead-end node is cut off too, and counting legs
        # would call that one open and paint it red instead of amber.
        cut_off = route_for_person(site, state, j_id).state == "HOLD"
        if cut_off:
            pattern, reason = "hold_amber", "no safe path from this junction"
            fail_safe_hold(state, j_id)
        elif blocked_legs:
            pattern, reason = "chase_red", "a leg from this junction is blocked"
        elif evacuating and any(leg in on_path for leg in legs):
            pattern, reason = "strobe_red", "evacuation ordered — exit this way"
        elif any_hold and not any(leg in on_path for leg in legs):
            pattern, reason = "hold_amber", "this junction is not on any safe path"
        elif any(leg in on_path for leg in legs):
            pattern, reason = "flow_green", "on the computed escape route"
        elif any(str(getattr(state.nodes.get(n), "status", "")).upper() == "WARNING"
                 for n in _attached_nodes(site, j_id)):
            pattern, reason = "advisory_amber", "a node at this junction is warning"
        else:
            # Prototype: a calm junction is the same green as the escape path.
            # advisory_amber stays for a real warning. The level hub buzzes on
            # that pattern, so a quiet mine must not be painted with it.
            pattern, reason = "flow_green", "quiet"
        plan.append(HubCommand(hub=hub, junction=j_id, pattern=pattern, reason=reason))
    return plan


# ---------------------------------------------------------------------------
# The whole site, in one call
# ---------------------------------------------------------------------------

def site_routes(site: SiteMap, state) -> dict:
    """One route per **level**, plus a per-crew map. Phase 1's compatibility layer.

    The dashboard's ``route_A_status`` / ``route_B_status`` come from here, and
    they are **derived** — never a second source of truth (§7.9).

    A level's verdict is computed **from its junctions**, not from its worst node,
    and the difference matters. A level's question is "can people get out of this
    level?", and the people on it are standing in the tunnels around its
    junctions. Routing from the worst *node* instead would ask a different
    question — "can the node that is failing get out?" — and would then have to
    excuse that node's own alarm to answer yes, which quietly re-opens the very
    corridor the blocked-link rule closed. From a junction there is nothing to
    excuse: if a junction has a way out, the level has a way out; if **every**
    junction on the level is cut off, the level holds.

    The per-crew routes below are where the person-specific exception belongs, and
    they carry it explicitly.
    """
    per_level: dict[str, dict] = {}
    levels = sorted({site.level_of(n) for n in site.active_nodes()
                     if site.level_of(n) is not None})
    for level in levels:
        members = [n for n in site.active_nodes() if site.level_of(n) == level]
        worst = max(members, key=lambda n: _node_risk(state, n)) if members else None
        junctions = [j["id"] for j in site.junctions if j.get("level") == level]
        if not junctions:                       # a level with no junction on the map
            junctions = members or []

        reachable = []
        for j_id in junctions:
            res = route_for_person(site, state, j_id)
            if res.state == "OPEN":
                reachable.append((res.cost, res))
        if reachable:
            best = min(reachable, key=lambda pair: (pair[0], pair[1].path))[1]
            entry = {**best.to_dict(), "reference": "junction",
                     "junction": best.src, "from_worst_node": worst}
        else:
            # Nothing on this level reaches an exit: the HOLD is the level's.
            ref = junctions[0] if junctions else (members[0] if members else "")
            res = route_for_person(site, state, ref)
            entry = {**res.to_dict(), "reference": "junction",
                     "junction": ref, "from_worst_node": worst,
                     "state": "HOLD", "exit": None}
        per_level[str(level)] = entry

    crews: dict[str, dict] = {}
    for uid in sorted(getattr(state, "zones", {}) or {}):
        pos = resolve_position(state, uid, site)
        if pos in ("", "EXIT_SURFACE"):
            crews[uid] = {"state": "ARRIVED" if pos else "UNKNOWN",
                          "src": pos or None, "exit": None}
            continue
        crews[uid] = route_for_person(site, state, pos).to_dict()

    return {"per_level": per_level, "crews": crews,
            "blocked": [list(b) for b in blocked_links(site, state)],
            "hold": any(r["state"] == "HOLD" for r in per_level.values()),
            "walk_speed_mps": WALK_SPEED_MPS}


__all__ = [
    "BLOCKED", "MapError", "SiteMap", "RouteResult", "HubCommand", "PATTERNS",
    "load_tunnel_map", "validate_map", "build_graph", "link_cost",
    "hazard_penalty", "linked_above", "blocked_links", "blocked_causes",
    "dijkstra", "base_length",
    "route_for_person", "nearest_exit", "fail_safe_hold", "clear_holds",
    "junction_led_plan", "push_hub_command", "send_downlink", "attach_downlink",
    "downlink_attached", "junction_inbox", "inbox_stats", "note_reassert",
    "reset_inbox",
    "resolve_position", "site_routes",
]
