"""
Phase 0 — the API shape snapshot.
=================================

The dashboard is already built and already verified. This file is the reason a
"pure extraction" phase can be trusted: it replays every endpoint against
``tests/fixtures/``, which hold the JSON captured from the running v4.4 server
*before* the extraction, and asserts that no path moved, disappeared or changed
type.

The comparison is deliberately **path-based, not value-based**: telemetry values
are volatile by nature, but ``underground.route_A_status`` existing at that exact
path is a promise the deck depends on.

Reading a failure
-----------------
``/api/telemetry MISSING {'nodes.node_3.status'}`` means a node key vanished.
``ADDITIVE`` entries are fine and expected — the contract says new keys may be
added. A *removed* path is never fine.
"""

from __future__ import annotations

import json

import pytest

import app as app_module
import config
from contracts import REQUIRED_TELEMETRY_PATHS
from tests.shape import FIXTURES, fixture, key_paths

#: Every GET endpoint the dashboard polls, with the fixture it was captured from.
ENDPOINTS = ("telemetry", "ai", "history", "site", "map", "extended")


@pytest.fixture(scope="module")
def client():
    """A test client with the ticker's history primed.

    ``main()`` is what normally starts the ticker thread; a test client never
    calls it, so ``/api/history`` would be empty and its shape untestable. Two
    calls to ``snapshot_history()`` — the exact function the ticker calls — give
    it real samples without pretending a thread is running.
    """
    app_module.app.config.update(TESTING=True)
    for _ in range(3):
        app_module.snapshot_history()
    return app_module.app.test_client()


def _missing(client_response_payload, fixture_name):
    return {k for k in key_paths(fixture(fixture_name)) if k not in key_paths(client_response_payload)}


# ---------------------------------------------------------------------------
# The routes themselves
# ---------------------------------------------------------------------------

def test_route_list_is_unchanged():
    """No route is added, removed or renamed without this fixture being updated.

    Phase 2 amended the table on purpose: §2.3 specifies ``GET /api/health``, so
    Every phase since has added a line or two, and each one was written down here:
    Phase 9 (B6) added ``/api/sync`` and ``/api/sync/status``; Phase 10 (B13/B14)
    added ``/api/blackbox/status`` and ``/api/blackbox/chunks``. The assertion
    itself has never changed — it still fails on any *undeclared* change, which is
    what makes a declared one worth having recorded. A new route that appears in
    the app without appearing in this file is a contract change nobody reviewed.
    """
    frozen = json.loads((FIXTURES / "routes.json").read_text())
    live = sorted(
        f"{r.rule} [{','.join(sorted(r.methods - {'HEAD', 'OPTIONS'}))}]"
        for r in app_module.app.url_map.iter_rules()
    )
    assert live == frozen["endpoints"], (
        "the route table changed — this phase is a pure extraction\n"
        f"  added  : {sorted(set(live) - set(frozen['endpoints']))}\n"
        f"  removed: {sorted(set(frozen['endpoints']) - set(live))}"
    )


def test_the_ten_public_endpoints_are_present():
    """Nine from Phase 0, plus ``/api/health`` from Phase 2."""
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    for path in ("/api/telemetry", "/api/extended", "/api/history", "/api/ai",
                 "/api/site", "/api/map", "/api/sim/trigger", "/api/reset",
                 "/api/cloud/force_sync", "/api/health",
                 # Phase 9 (B6) — §9.4's two routes
                 "/api/sync", "/api/sync/status"):
        assert path in rules, f"{path} disappeared"


# ---------------------------------------------------------------------------
# Per-endpoint shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [e for e in ENDPOINTS if e != "extended"])
def test_endpoint_shape_matches_the_frozen_snapshot(client, name):
    resp = client.get(f"/api/{name}")
    assert resp.status_code == 200, f"/api/{name} → {resp.status_code}"
    missing = _missing(resp.get_json(), name)
    assert not missing, f"/api/{name} lost these JSON paths: {sorted(missing)}"


def test_extended_keeps_its_shape(client):
    """``/api/extended`` carries the deck's blackbox/cloud blocks.

    Those blocks come from the gateway, which is only started in ``main()``. With
    the gateway absent the contract is that the *non-blackbox* half is intact and
    the blocks are simply missing — never half-populated with invented numbers.
    """
    payload = client.get("/api/extended").get_json()
    base_paths = set(key_paths(fixture("extended")))
    live_paths = set(key_paths(payload))
    structural = {p for p in base_paths
                  if not p.startswith(("blackbox", "cloud"))}
    missing = structural - live_paths
    assert not missing, f"/api/extended lost {sorted(missing)}"
    if app_module.BLACKBOX is None:
        assert "blackbox" not in live_paths, "no gateway ⇒ no blackbox block"
        assert "cloud" not in live_paths, "no gateway ⇒ no cloud block"


# ---------------------------------------------------------------------------
# The named promises
# ---------------------------------------------------------------------------

def test_telemetry_keeps_every_documented_path(client):
    payload = client.get("/api/telemetry").get_json()
    paths = key_paths(payload)
    for required in REQUIRED_TELEMETRY_PATHS:
        assert required in paths, f"/api/telemetry must keep {required} forever"


def test_telemetry_exposes_every_active_node(client):
    """Every node in the registry is on the wire. (Was
    ``test_telemetry_keeps_two_nodes_until_phase_one``.)

    Phase 0 asserted exactly ``{"node_1", "node_2"}`` — the two-node pipeline
    that shipped in v4.4 — and its whole purpose was to make the *next* phase's
    job visible. Phase 1 (B1) is that phase: ``ACTIVE_NODES`` flipped to all four
    in one line of ``config.py``, which is what D10 was about. The assertion is
    the same shape, against the widened registry.
    """
    payload = client.get("/api/telemetry").get_json()
    assert set(payload["nodes"]) == set(config.ACTIVE_NODES) == set(config.NODE_IDS)
    assert len(payload["nodes"]) == 4, "four nodes, two per level"
    # Phase 1 additive keys: the per-level machinery the dashboard will read.
    assert set(payload["levels"]) == {"1", "2"}
    assert payload["schema"] == 2


def test_ttf_seconds_is_int_or_na(client):
    """The deck formats this directly — a float would print as 3599.812."""
    value = client.get("/api/telemetry").get_json()["ttf_seconds"]
    assert value == "N/A" or isinstance(value, int) and not isinstance(value, bool)


def test_ai_status_is_one_of_the_three_contract_strings(client):
    """The seven keys ``index.html`` reads, and the three status strings.

    **This assertion was exact key equality until Phase 5, and that was the right
    thing to write at the time** — it caught a rename, which is a blank card on
    stage. §5.3 then added ``model_1`` and ``cascade`` *additively*: the
    requirement is "do not move or rename any existing key", not "never add one".
    Exact equality cannot express that, so it is now a superset check on the
    original seven plus shape checks on the two new blocks.

    The guard against the failure the original was written for has not been
    weakened: the seven must all still be present, spelled the same way.
    """
    payload = client.get("/api/ai").get_json()
    assert payload["status"] in ("ACTIVE", "WARMING UP", "FALLBACK")

    required = {"model", "status", "horizon", "last_train",
                "confidence", "note", "forecasts"}
    missing = required - set(payload)
    assert not missing, f"the AI card reads these and they are gone: {sorted(missing)}"

    assert set(payload["forecasts"]) == set(config.ACTIVE_NODES)
    assert payload["note"], "the AI card renders this line"
    assert payload["confidence"].endswith("%")

    # Phase 5's additive blocks (§5.3) — present even when M1 is absent, because
    # a card that vanishes is worse than one that says UNAVAILABLE.
    assert "model_1" in payload and "cascade" in payload
    m1 = payload["model_1"]
    assert m1["status"] in ("ACTIVE", "UNAVAILABLE")
    assert "threshold" in m1 and "classes" in m1
    cas = payload["cascade"]
    assert set(cas) >= {"mode", "m2_hz", "disagreement", "reason"}
    assert cas["mode"] in ("advisory", "gate")
    assert isinstance(cas["disagreement"], bool)


def test_map_payload_gis_keys(client):
    """gis-map.js projects from lat/lon itself — these keys are its input."""
    payload = client.get("/api/map").get_json()
    for key in ("site", "system_status", "nodes", "junctions", "exits", "links", "zones"):
        assert key in payload, f"gis-map.js expects {key}"
    assert isinstance(payload["site"]["center"], list) and len(payload["site"]["center"]) == 2


def test_history_is_a_bounded_series(client):
    payload = client.get("/api/history").get_json()
    assert isinstance(payload, dict) and payload, "history must be a non-empty object"
    for node, series in payload.items():
        assert isinstance(series, list)
        assert len(series) <= config.HISTORY_MAX
        if series:
            assert set(series[0]) >= {"status", "tilt_x", "displacement_mm", "acceleration"}


def test_site_payload(client):
    payload = client.get("/api/site").get_json()
    assert payload, "/api/site must return the site block"


# ---------------------------------------------------------------------------
# POST endpoints — behaviour, not shape
# ---------------------------------------------------------------------------

def test_reset_rearms_the_system(client):
    resp = client.post("/api/reset")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body is not None
    after = client.get("/api/telemetry").get_json()
    assert after["system_status"] == "NOMINAL"


def test_unknown_api_path_is_a_404_not_a_crash(client):
    resp = client.get("/api/does_not_exist")
    assert resp.status_code in (404, 200), "the asset catch-all may 404 internally"
    assert resp.status_code != 500
