"""The GIS payload `gis-map.js` already expects — §A.3.6.

Two conversions live here, and §7.2 says why they are *server-side*: the map
stores **metre offsets** from the diorama's south-west corner while the frontend
draws **lat/lon** and **polygons**. A browser that has to know about the metre
grid, the baseline, and ``cos(latitude)`` is a browser that can be wrong about
them in three places; the conversion belongs where the survey lives.

Units, stated once
------------------
``d_e_m`` / ``d_n_m`` are metres, and a zone's ``rect`` is ``[x0, y0, x1, y1]`` in
**the same metres** — the four zones tile the same 120 m × 60 m working extent the
nodes, junctions and exits are placed in, which is what makes a node's zone a
meaningful statement about where it is. The map file's own note gives the survey
rule that produces those numbers (``d_e_m = x_cm × 10``), so the diorama's
centimetres are converted **once, during the survey**, and every consumer
downstream — this module, the router, the SVG — reads metres.

The alternate reading (a rect in centimetres, scaled again here) would draw zone
A1 as 600 m wide around a node 20 m from the same corner, which is a 30× error and
would swallow the whole drawing. It is recorded here because it is the mistake the
units invite.
"""

from __future__ import annotations

import json

import config

#: Fallback georeference when ``site_meta.json`` cannot be read. The numbers are
#: the ones §A.3.6's example payload carries; a payload drawn on the fallback is
#: still a *usable* payload, which matters because the GIS card is the demo.
DEFAULT_GEO = {
    "baseline_lat": 23.643,
    "baseline_lon": 85.318,
    "metres_per_degree_lat": 111320.0,
    "metres_per_degree_lon": 102470.0,
}


def load_site_meta(path=None) -> dict:
    """The fixed baseline, read once. Never fatal: falls back and says so."""
    path = path or config.ASSET_FILES.get("site_meta")
    try:
        raw = json.loads(str(path and path.read_text(encoding="utf-8")))
    except Exception as exc:                                    # noqa: BLE001
        return {**DEFAULT_GEO, "_source": f"fallback ({type(exc).__name__})"}
    geo = raw.get("georeference", {})
    return {
        **{k: float(geo.get(k, DEFAULT_GEO[k])) for k in DEFAULT_GEO},
        "name": raw.get("name"),
        "site_id": raw.get("site_id"),
        "diorama": raw.get("diorama", {}),
        "levels": raw.get("levels", {}),
        "topology": raw.get("topology", {}),
        "tunnels": raw.get("tunnels", {}),
        "render": raw.get("render", {}),
        "_source": "assets/site_meta.json",
    }


def offset_to_latlon(d_n_m: float, d_e_m: float, meta: dict) -> tuple[float, float]:
    """Metre offsets from the SW corner → ``(lat, lon)``.

    ``metres_per_degree_lon`` is precomputed for the site's latitude (site_meta
    carries the working: ``cos(23.643°) × 111320``), so this function does no
    trigonometry per request — the GIS card polls every 2 s and §A.3.6's whole
    point is that the poll stays small and cheap.
    """
    lat = float(meta["baseline_lat"]) + float(d_n_m) / float(meta["metres_per_degree_lat"])
    lon = float(meta["baseline_lon"]) + float(d_e_m) / float(meta["metres_per_degree_lon"])
    return lat, lon


def rect_to_polygon(rect, meta: dict) -> list[list[float]]:
    """``[x0, y0, x1, y1]`` metres → four ``[lat, lon]`` corners.

    Order matters and is not cosmetic: ``gis-map.js`` builds the polygon's label
    anchor from ``polygon[0]`` and ``polygon[3]``, so the corners go
    SW → NW → NE → SE (i.e. ``(y0,x0) (y1,x0) (y1,x1) (y0,x1)``), which puts the
    label at the zone's south-west corner where the drawing has room for it.
    """
    x0, y0, x1, y1 = (float(v) for v in rect)
    corners = ((y0, x0), (y1, x0), (y1, x1), (y0, x1))
    return [list(offset_to_latlon(n, e, meta)) for n, e in corners]


def _risk_of(node_status: str, node_active: bool = True) -> str:
    if not node_active:
        return "NOMINAL"
    return str(node_status or "NOMINAL").upper()


#: How a node's status ranks when a zone asks for the worst of its members.
_RANK = {"NOMINAL": 0, "WATCH": 0, "WARNING": 1, "CRITICAL": 2}


def zone_payload(site, state, meta: dict) -> list[dict]:
    """Zones, coloured by **worst-of** the nodes and links inside them (§4.6).

    A zone is a piece of ground, and the ground's risk is the worst thing standing
    on it. Links count too: a zone whose own nodes are quiet but whose only
    corridor is blocked is not a zone to walk into.
    """
    live = getattr(state, "nodes", {}) or {}
    causes: dict = {}
    try:
        from routing import dijkstra as routing
        causes = routing.blocked_causes(state, site)
    except Exception:                                           # noqa: BLE001
        causes = {}

    out: list[dict] = []
    for z in site.zones:
        worst = 0
        for n_id, n_meta in site.nodes.items():
            if n_meta.get("zone") != z["id"]:
                continue
            if not n_meta.get("active", True):
                continue
            node = live.get(n_id)
            worst = max(worst, _RANK.get(_risk_of(
                getattr(node, "status", "NOMINAL") if node else "NOMINAL"), 0))
            if getattr(node, "stale", False):
                worst = max(worst, 1)          # UNKNOWN is not safe
        # A blocked leg counts against this zone only when the node that closed
        # it is standing in this zone — see `dijkstra.blocked_causes`.
        for (a, b), who in causes.items():
            if any(site.node_zone(n) == z["id"] for n in who):
                worst = max(worst, 1)
        out.append({
            "id": z["id"],
            "name": z.get("name", z["id"]),
            "risk": ("NOMINAL", "WARNING", "CRITICAL")[worst],
            "polygon": rect_to_polygon(z["rect"], meta),
            # Additive (Phase 7): which nodes the colour is about, so a red zone
            # on the wall can be traced to the sensor that made it red.
            "nodes": [n for n, d in site.nodes.items()
                      if d.get("zone") == z["id"] and d.get("active", True)],
        })
    return out


def _points_in_rect(site, rect) -> set[str]:
    x0, y0, x1, y1 = (float(v) for v in rect)
    out = set()
    for p_id, (n_m, e_m) in site.coords.items():
        if y0 <= n_m <= y1 and x0 <= e_m <= x1:
            out.add(p_id)
    return out


def build_map_payload(site, state, meta: dict | None = None,
                      routes: dict | None = None) -> dict:
    """The ``/api/map`` body: exactly §A.3.6's keys, plus ``routes``.

    Every key the SVG reads is present and typed the way it reads it — ``links``
    are objects (``from`` / ``to`` / ``state`` / ``cost``) as the contract says,
    and ``polygon`` is a list of ``[lat, lon]`` in label-anchor order.
    """
    from routing import dijkstra as routing

    meta = meta or load_site_meta()
    live = getattr(state, "nodes", {}) or {}
    routes = routes if routes is not None else routing.site_routes(site, state)
    blocked = {frozenset(p) for p in routes.get("blocked", [])}

    nodes = []
    for n_id, n_meta in site.nodes.items():
        lat, lon = offset_to_latlon(n_meta["d_n_m"], n_meta["d_e_m"], meta)
        node = live.get(n_id)
        active = n_meta.get("active", True)
        nodes.append({
            "id": n_id,
            "label": n_meta.get("label", n_id),
            "lat": lat, "lon": lon,
            "level": n_meta.get("level"),
            "zone": n_meta.get("zone"),
            "active": active,
            "status": "NOMINAL" if not active else str(
                getattr(node, "status", "NOMINAL") or "NOMINAL"),
            "stale": bool(getattr(node, "stale", False)) if active else False,
            "crack_stage": getattr(node, "crack_stage", None) if active else None,
            "displacement_mm": getattr(node, "displacement_mm", None) if active else None,
            "tilt_x": getattr(node, "tilt_x", None) if active else None,
            "acceleration": getattr(node, "acceleration", None) if active else None,
        })

    junctions = []
    for j in site.junctions:
        lat, lon = offset_to_latlon(j["d_n_m"], j["d_e_m"], meta)
        junctions.append({
            "id": j["id"], "level": j.get("level"),
            "lat": lat, "lon": lon,
            "status": "BLOCKED" if any(
                frozenset((j["id"], other)) in blocked
                for other in site.adjacency.get(j["id"], ())) else "OK",
        })

    exits = []
    for e in site.exits:
        lat, lon = offset_to_latlon(e["d_n_m"], e["d_e_m"], meta)
        exits.append({"id": e["id"], "label": e.get("label", e["id"]),
                      "level": e.get("level"), "lat": lat, "lon": lon})

    links = []
    for a, b in site.links:
        if a in site.nodes and a not in site.active_nodes():
            continue
        if b in site.nodes and b not in site.active_nodes():
            continue
        is_blocked = frozenset((a, b)) in blocked
        cost = routing.link_cost(state, (a, b), site.length(a, b), site)
        links.append({
            "from": a, "to": b,
            "state": "BLOCKED" if is_blocked else "UP",
            "cost": None if is_blocked else round(cost, 2),
            "base_m": round(site.length(a, b), 1),
            "hazard": round(max(0.0, cost - site.length(a, b)), 2) if not is_blocked else None,
        })

    hold = bool(routes.get("hold"))
    return {
        "site": {
            "name": (site.site or {}).get("name") or meta.get("name") or "Neural Strata site",
            "center": [meta["baseline_lat"], meta["baseline_lon"]],
            "scale": (meta.get("diorama") or {}).get(
                "scale", "1 cm diorama = 10 m real ground"),
            "site_id": meta.get("site_id"),
            "positioning": "fixed baseline + offsets (GPS deferred — spec ready)",
            "units": "offsets are metres from the SW corner",
        },
        "system_status": getattr(state, "system_status", "NOMINAL"),
        "nodes": nodes,
        "junctions": junctions,
        "exits": exits,
        "links": links,
        "zones": zone_payload(site, state, meta),
        # Additive (Phase 7) — §A.3.6 fixes what the SVG reads; the router's own
        # answer rides alongside it rather than inside it.
        "routes": routes,
        "hold": hold,
        "map_ok": True,
        "generated": getattr(state, "timestamp", ""),
    }


def build_site_payload(site, meta: dict | None = None) -> dict:
    """``/api/site`` — the page-load handshake. Small, fixed, cached by the deck."""
    meta = meta or load_site_meta()
    return {
        "name": meta.get("name") or (site.site or {}).get("name"),
        "site_id": meta.get("site_id"),
        "center": [meta["baseline_lat"], meta["baseline_lon"]],
        "scale": (meta.get("diorama") or {}).get("scale"),
        "positioning": "fixed baseline + offsets (GPS deferred — spec ready)",
        "georeference": {k: meta[k] for k in
                         ("baseline_lat", "baseline_lon",
                          "metres_per_degree_lat", "metres_per_degree_lon")},
        "levels": meta.get("levels", {}),
        "topology": meta.get("topology", {}),
        "exits": [{"id": e["id"], "label": e.get("label", e["id"]),
                   "lat": offset_to_latlon(e["d_n_m"], e["d_e_m"], meta)[0],
                   "lon": offset_to_latlon(e["d_n_m"], e["d_e_m"], meta)[1]}
                  for e in site.exits],
        "walk_speed_mps": getattr(config, "WALK_SPEED_MPS", 1.2),
    }


__all__ = ["DEFAULT_GEO", "load_site_meta", "offset_to_latlon", "rect_to_polygon",
           "zone_payload", "build_map_payload", "build_site_payload"]
