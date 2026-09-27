"""
Phase 1 (B1) — the node registry, and the D10 regression guard.
==============================================================

D10 was that the node set was written down in six places: ``telemetry_state``,
``node_buffers``, ``peak_accel``, ``history``, ``injections``, ``injection_t`` —
each a two-key dict — plus the literal tuple ``("node_1", "node_2")`` inside five
functions. Going from two nodes to four meant six coordinated edits in one file,
and missing one of them did not raise: it left a node silently un-updated.

The last test in this file is a **source-level grep**, which is an unusual thing
for a test to do and is deliberate. The failure mode is not a wrong value, it is
a forgotten literal, and you cannot assert on code that has already been deleted.
The grep is the only way to keep it deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from contracts import CRACK_NODE_IDS, NODE_IDS
from state import SystemState

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture()
def state():
    return SystemState()


# ---------------------------------------------------------------------------
# The registry itself
# ---------------------------------------------------------------------------

def test_four_nodes_two_per_level(state):
    assert state.node_ids == NODE_IDS == ("node_1", "node_2", "node_3", "node_4")
    assert config.ACTIVE_NODES == NODE_IDS, "Phase 1 runs all four"
    assert state.levels_map == {1: ("node_1", "node_2"), 2: ("node_3", "node_4")}


def test_every_node_is_on_exactly_one_level(state):
    seen = []
    for members in state.levels_map.values():
        seen.extend(members)
    assert sorted(seen) == sorted(NODE_IDS)
    assert len(seen) == len(set(seen)), "a node on two levels would double-count"
    for n_id in NODE_IDS:
        assert len(state.levels_of(n_id)) == 1
        assert state.nodes[n_id].level == state.level_of(n_id)


def test_iter_nodes_is_the_whole_registry(state):
    """The single iteration primitive. Everything else walks this."""
    assert [n.node_id for n in state.iter_nodes()] == list(NODE_IDS)


def test_every_per_node_structure_is_four_wide(state):
    """The six two-key dicts of v4.4, now all derived from one registry."""
    for attr in ("nodes", "buffers", "peak_accel", "history",
                 "injections", "injection_t", "via"):
        d = getattr(state, attr)
        assert set(d) == set(NODE_IDS), f"{attr} is not the full registry"


def test_all_four_nodes_reach_the_wire():
    """N3 and N4 are alive, not placeholders. This is what B1 was for."""
    import app as app_module
    app_module.app.config.update(TESTING=True)
    payload = app_module.app.test_client().get("/api/telemetry").get_json()

    assert set(payload["nodes"]) == set(NODE_IDS)
    assert set(payload["levels"]) == {"1", "2"}
    for n_id, node in payload["nodes"].items():
        # The wire shape is the contract shape minus `velocity`, which is
        # server-internal and stripped in `public_telemetry()`.
        expected = set(app_module.STATE.nodes[n_id].to_public()) - {"velocity"}
        assert set(node) == expected
        assert "status" in node and "displacement_mm" in node
        assert node["status"] in ("NOMINAL", "WARNING", "CRITICAL")


def test_extended_reports_link_state_for_every_node():
    import app as app_module
    app_module.app.config.update(TESTING=True)
    ext = app_module.app.test_client().get("/api/extended").get_json()

    assert set(ext["nodes"]) == set(NODE_IDS)
    assert set(ext["ring"]) == {"1", "2"}
    sample = ext["nodes"]["node_4"]
    for key in ("velocity", "peak_acceleration", "level", "rssi", "batt",
                "via", "window_complete", "flags"):
        assert key in sample, f"/api/extended node block must carry {key}"
    assert sample["level"] == 2, "node_4 is on level 2"


def test_instrumented_nodes_are_the_crack_pair(state):
    """Only N2 and N4 carry the load cell + macro pot. Everything else reports 0."""
    assert CRACK_NODE_IDS == ("node_2", "node_4")
    for n_id in CRACK_NODE_IDS:
        assert state.nodes[n_id].level in (1, 2)
    for n_id in ("node_1", "node_3"):
        assert state.nodes[n_id].micro is None and state.nodes[n_id].macro is None


# ---------------------------------------------------------------------------
# The D10 guard — a source-level check, done semantically
# ---------------------------------------------------------------------------
#
# These three tests read source code, which is unusual for a test to do and is
# deliberate: the failure mode is not a wrong value, it is a forgotten literal,
# and you cannot assert on code that has already been deleted. The check parses
# each module with `ast` rather than grepping, so that **prose is not code** —
# the docstrings in app.py and state.py quote the literal ``("node_1", "node_2")``
# precisely because they are explaining why it is gone.

import ast


def _two_node_literal_lines(path: Path) -> list[int]:
    """Line numbers where a 2-element tuple/list of exactly node_1 + node_2 is built."""
    tree = ast.parse(path.read_text())
    hits = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Tuple, ast.List)) and len(node.elts) == 2:
            vals = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            if len(vals) == 2 and set(vals) == {"node_1", "node_2"}:
                hits.append(node.lineno)
    return hits


def test_app_py_builds_no_two_node_literal():
    """``("node_1", "node_2")`` must not come back anywhere in app.py.

    It is not that the pair is wrong — it is right for Level 1. The defect is that
    when it appeared in ``update_status_logic``, ``apply_injections``,
    ``snapshot_history``, ``ticker_thread`` and ``get_extended``, it silently
    *was* the node registry. Four nodes would have been two nodes with two
    decorative extras.
    """
    hits = _two_node_literal_lines(BACKEND / "app.py")
    assert hits == [], (
        f"app.py builds a hardcoded node pair at line(s) {hits}. "
        "Iterate STATE.iter_nodes() instead — see D10."
    )


def test_state_py_builds_no_two_node_literal():
    hits = _two_node_literal_lines(BACKEND / "state.py")
    assert hits == [], f"state.py builds a hardcoded node pair at line(s) {hits}"


@pytest.mark.parametrize("module", ["app.py", "state.py", "ingestion/serial_reader.py",
                                    "ingestion/ring.py", "ingestion/simulator.py",
                                    "engine/health.py"])
def test_no_module_builds_a_two_node_literal(module):
    assert _two_node_literal_lines(BACKEND / module) == [], f"{module} hardcodes the pair"


def test_the_registry_is_declared_exactly_once():
    """One literal, in ``contracts``; every other module re-exports the object.

    Phase 0 wrote the tuple out in both ``config.py`` and ``contracts.py`` and
    added a test asserting they agreed. That is the D10 pattern one level up: two
    copies is two edits per change, and a test that catches the divergence
    afterwards is worse than not having the divergence. Identity, not equality.
    """
    import contracts
    assert config.NODE_IDS is contracts.NODE_IDS
    assert config.CRACK_NODE_IDS is contracts.CRACK_NODE_IDS

    declarations = []
    for rel in ("config.py", "contracts.py", "app.py", "state.py"):
        tree = ast.parse((BACKEND / rel).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                    and node.target.id == "NODE_IDS" \
                    and isinstance(node.value, (ast.Tuple, ast.List)):
                declarations.append(rel)
    assert declarations == ["contracts.py"], (
        f"NODE_IDS is written out literally in {declarations} — expected only contracts.py"
    )
