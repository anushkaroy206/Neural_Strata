"""
Backend API module exports for Phase 12 split.

This module exposes the Flask blueprints that make up the HTTP surface after
Phase 12's split of `app.py`. Each blueprint lives in its own file:

* `routes_telemetry.py` — telemetry API (/api/telemetry, /api/extended, /api/history)
* `routes_ai.py` — AI cascade API (/api/ai)
* `routes_admin.py` — admin API (/api/sim/*, /api/metrics)
* `routes_cloud.py` — cloud observability (/api/cloud/*)
* `routes_blackbox.py` — blackbox API (/api/blackbox/*, Phase 10)
* `routes_map.py` — GIS API (/api/map, /api/site, /assets/<path>)
* `routes_alerts.py` — alert API (/api/alerts/*, Phase 8)

This structure lets `app.py` remain ~80 lines while the API surface grows
modularly. The original HTTP surface is preserved — nothing changes for the
frontend beyond Phase 12's new blackbox observability features.
"""

from . import routes_ai, routes_admin, routes_blackbox, routes_cloud, routes_map, routes_telemetry, routes_alerts

__all__ = [
    "routes_ai",
    "routes_admin",
    "routes_blackbox",
    "routes_cloud",
    "routes_map",
    "routes_telemetry",
    "routes_alerts",
]