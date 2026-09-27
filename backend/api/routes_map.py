"""The HTTP surface B5 adds: ``/api/map``, ``/api/site``, ``/assets/<path>``.

Three routes, one blueprint, so Phase 12's split has somewhere to put them and
``app.py`` keeps shrinking instead of growing. The routes are thin on purpose:
every decision they make was made in ``routing.dijkstra`` or ``api.map_payload``,
and both of those are importable and testable without a Flask client.

Defect D1 is the reason this file exists at all: ``gis-map.js`` has polled
``/api/map`` every two seconds since before there was a server answering it, and
the frontend's own header comment claimed a ``/<path:asset>`` route that was not
in the code. Both are now true statements.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, send_from_directory

import config
from api import map_payload

bp = Blueprint("map", __name__)

#: Injected by ``app.py`` at boot. Kept as module state rather than an import of
#: ``app`` because a blueprint that imports its own application is a circular
#: import waiting for the first refactor.
_SITE = None
_META = None
_ERROR = None
_STATE = None


def bind(site, meta, state, error: str | None = None) -> None:
    """Hand the boot-time :class:`SiteMap` **and the live state** to the routes.

    The state is passed in rather than imported, and that is not a style choice —
    it was a live defect. The obvious version of this module did

        from app import STATE

    at request time. It works perfectly under `pytest`, where the application
    module is importable as ``app``, and it works perfectly for anyone who starts
    the server with ``python -m app``. But the dashboard is started as
    ``python3 app.py``, where the application module is ``__main__`` — so the
    import loaded a **second copy of app.py**, with its own fresh ``SystemState``,
    its own model handles, and no ticker. `/api/map` then answered from a state in
    which every node was NOMINAL and no route was ever blocked: the GIS card
    cheerfully drew an open exit through a tunnel whose only sensor was reporting
    CRITICAL. The state is now an injected dependency, which cannot have a shadow.
    """
    global _SITE, _META, _ERROR, _STATE
    _SITE, _META, _ERROR, _STATE = site, meta, error, state


def _state():
    return _STATE, _SITE


@bp.route("/api/map", methods=["GET"])
def get_map():
    """The GIS payload. Positions are fixed; every colour is live.

    ``map_ok`` is explicit rather than implied by an empty body: a map that fails
    to load at boot answers **503 with the reason**, because a GIS card that
    silently renders an empty field looks exactly like a card that works.
    """
    state, site = _state()
    if state is None:
        return jsonify({"error": "state not bound — the server did not start "
                                 "through app.main()"}), 503
    if site is None:
        return jsonify({
            "error": "tunnel map unavailable",
            "detail": _ERROR or "not loaded",
            "map_ok": False,
            "site": {"name": "unavailable"},
            "system_status": state.system_status,
            "nodes": [], "junctions": [], "exits": [], "links": [], "zones": [],
            "routes": {"per_level": {}, "crews": {}, "blocked": [], "hold": False},
            "hold": False,
        }), 503
    return jsonify(map_payload.build_map_payload(site, state, _META))


@bp.route("/api/site", methods=["GET"])
def get_site():
    """Fixed baseline metadata — returned once at page load.

    The deck holds it; the 2 s poll stays small. §7.4's note, and the reason
    ``/api/site`` is not folded into ``/api/map``.
    """
    state, site = _state()
    if site is None:
        return jsonify({"error": "tunnel map unavailable",
                        "detail": _ERROR or "not loaded"}), 503
    return jsonify(map_payload.build_site_payload(site, _META))


@bp.route("/assets/<path:asset>", methods=["GET"])
def get_asset(asset: str):
    """Serve ``gis-map.js`` (and any future static asset) from the frontend.

    The frontend is built into one self-contained ``index.html``, so this route
    matters less than it used to — but the integration block §7.6 documents loads
    ``/gis-map.js``, and an asset route that 404s is how D1 started.
    """
    return send_from_directory(config.FRONTEND_DIR, asset)


__all__ = ["bp", "bind", "get_map", "get_site", "get_asset"]
