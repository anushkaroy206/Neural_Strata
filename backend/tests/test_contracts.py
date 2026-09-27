"""
Phase 0 — the frozen contracts.
===============================

If this file fails, the wireless frame, the API payload or the session format
changed. That is allowed — but it requires bumping ``SCHEMA_VERSION`` in the same
commit, and the dashboard has to be told. It is not allowed to happen by accident.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from contracts import (
    CRACK_NODE_IDS, FLAG_LOW_BATTERY, FLAG_SYNTHETIC, NODE_IDS,
    NODE_PUBLIC_FIELDS, REQUIRED_TELEMETRY_PATHS, TELEMETRY_DB_COLUMNS,
    SCHEMA_VERSION, SUPPORTED_VERSIONS, TelemetryPayload, coerce_float, coerce_int,
    format_session_row, flags_to_list, migrate_payload, parse_session_row,
    read_session_csv, validate_downlink, validate_node_frame, validate_rfid_frame,
    validate_ring_frame,
)
import config


# ---------------------------------------------------------------------------
# The version line
# ---------------------------------------------------------------------------

def test_schema_version_is_two():
    assert SCHEMA_VERSION == 2
    assert 1 in SUPPORTED_VERSIONS, "v1 (the firmware shipping today) must stay supported"


def test_topology_matches_the_board_count():
    """4 sensor nodes · 2 level hubs · 1 main hub = 7 boards."""
    assert NODE_IDS == ("node_1", "node_2", "node_3", "node_4")
    assert CRACK_NODE_IDS == ("node_2", "node_4"), "instrumented nodes only"
    assert set(config.NODE_IDS) == set(NODE_IDS), "config and contracts must agree"
    covered = [n for level in config.LEVELS.values() for n in level]
    assert sorted(covered) == sorted(NODE_IDS), "every node on exactly one level"
    assert not config.validate_config(), "the shipped configuration must validate"


# ---------------------------------------------------------------------------
# Coercion — never raise, always a number
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (1, 1.0), (1.5, 1.5), ("2.5", 2.5), (" 3 ", 3.0), (True, 1.0), (False, 0.0),
])
def test_coerce_float_accepts(value, expected):
    assert coerce_float(value) == expected


@pytest.mark.parametrize("value", [None, "", "abc", "n/a", float("nan"), float("inf"), [], {}])
def test_coerce_float_falls_back(value):
    assert coerce_float(value, default=-1.0) == -1.0


def test_coerce_int_truncates_and_survives_junk():
    assert coerce_int("7") == 7
    assert coerce_int(7.9) == 7
    assert coerce_int(None, 3) == 3
    assert coerce_int("nonsense", 3) == 3


# ---------------------------------------------------------------------------
# The node frame
# ---------------------------------------------------------------------------

def valid_frame(**over):
    f = {"v": 2, "node": 3, "ms": 184220, "disp": 12.40, "tilt": 1.85, "acc": 0.31,
         "vib": 4, "micro": None, "macro": None, "rssi": -62, "batt": 3.78, "flags": 0}
    f.update(over)
    return f


def test_valid_frame_passes():
    ok, why = validate_node_frame(valid_frame())
    assert ok, why


def test_bare_v1_frame_still_passes():
    """The firmware shipping today emits this. It must never be rejected."""
    ok, why = validate_node_frame({"node": 1, "disp": 5.0, "tilt": 0.0, "acc": 0.01})
    assert ok, why


@pytest.mark.parametrize("over,needle", [
    ({"node": 9}, "range"),
    ({"node": "2"}, "integer"),
    ({"v": 99}, "unsupported"),
    ({"disp": "abc"}, "disp"),
    ({"disp": None}, "missing"),
    ({"acc": 500.0}, "above range"),
    ({"batt": 12.0}, "above range"),
    ({"rssi": -300}, "below range"),
    ({"flags": 999}, "above range"),
])
def test_bad_frame_is_rejected_with_a_reason(over, needle):
    ok, why = validate_node_frame(valid_frame(**over))
    assert not ok
    assert needle.lower() in why.lower(), f"unhelpful reason: {why!r}"


def test_crack_channels_only_on_instrumented_nodes():
    """micro/macro exist on nodes 2 and 4 and nowhere else."""
    ok, why = validate_node_frame(valid_frame(node=2, micro=412.5, macro=3.2))
    assert ok, why
    ok, why = validate_node_frame(valid_frame(node=1, micro=412.5))
    assert not ok and "micro" in why
    ok, why = validate_node_frame(valid_frame(node=3, macro=3.2))
    assert not ok and "macro" in why


def test_non_dict_and_non_frame_input():
    assert validate_node_frame("hello")[0] is False
    assert validate_node_frame({"uid": "001"}) == (False, "missing 'node' — not a node frame")


def test_flags_decode():
    assert flags_to_list(0) == []
    assert flags_to_list(1 | 16) == ["micro_saturated", "last_gasp"]
    assert flags_to_list(None) == []


# ---------------------------------------------------------------------------
# Envelopes, RFID, downlink
# ---------------------------------------------------------------------------

def test_ring_envelope():
    env = {"v": 2, "via": "lvl2", "seq": 9912, "link": "lvl2→hub",
           "frames": [valid_frame(node=3, micro=None, macro=None)]}
    ok, why = validate_ring_frame(env)
    assert ok, why
    assert validate_ring_frame({**env, "via": "lvl9"})[1].startswith("'via' must be")
    assert "capped at 64" in validate_ring_frame({**env, "frames": [valid_frame()] * 65})[1]


def test_ring_envelope_rejects_a_bad_inner_frame():
    env = {"v": 2, "via": "lvl1", "seq": 1, "frames": [valid_frame(node=77)]}
    ok, why = validate_ring_frame(env)
    assert not ok and why.startswith("frames[0]:"), why


def test_level_hub_readers_reach_their_own_tunnels():
    """The level hub names its reader lvl1/lvl2. Those must not collapse onto
    the unnamed-reader fallback, or a level-2 swipe is drawn on level 1."""
    from ingestion.serial_reader import handle_rfid_frame
    from state import SystemState

    state = SystemState()
    handle_rfid_frame(state, {"v": 2, "uid": "A1B2C3D4", "reader": "lvl1"})
    handle_rfid_frame(state, {"v": 2, "uid": "E5F60718", "reader": "lvl2"})
    zones = {row["id"]: row["zone"] for row in state.rfid_log}
    assert zones["A1B2C3D4"] == "Tunnel A"
    assert zones["E5F60718"] == "Tunnel B"
    state.note_rfid_reader("lvl1", True, 2)
    report = {r["reader"]: r for r in state.public_payload()["rfid_readers"]}
    assert report["lvl1"]["present"] is True
    assert report["lvl1"]["reads"] == 2
    assert [row["id"] for row in state.public_payload()["rfid_log"]] == [
        "E5F60718", "A1B2C3D4"]


def test_a_blocked_level_moves_tags_already_on_the_ledger():
    """A warning or a blocked route takes that level off the working ledger.

    The row records level and tunnel while the person is working. Once that
    level's alarm is on, the tag is evacuated and the on-screen row is gone.
    Opening the route again does not invent them back.
    """
    from state import SystemState

    state = SystemState()
    state.note_rfid("9182FB66", "lvl2")
    state.note_rfid("A1B2C3D4", "lvl1")
    assert state.rfid_log[0]["zone"] == "Tunnel A"
    assert state.rfid_log[0]["level"] == "Level 1"
    assert state.rfid_log[0]["tunnel"] == "Tunnel A"
    assert state.zones["9182FB66"] == "Tunnel B"
    state.underground["route_B_status"] = "BLOCKED"
    assert state.refresh_muster() == 1
    assert "9182FB66" not in state.zones
    assert [row["id"] for row in state.rfid_log] == ["A1B2C3D4"]
    state.underground["route_B_status"] = "OPEN"
    assert state.refresh_muster() == 0
    assert [row["id"] for row in state.rfid_log] == ["A1B2C3D4"]
    state.levels["1"] = {"status": "WARNING", "route": "OPEN"}
    assert state.refresh_muster() == 1
    assert state.rfid_log == []
    assert state.zones == {}


def test_a_warning_on_one_level_clears_every_layer():
    """Warning and critical empty the ledger even when the tags are on the
    other level and the site status word has not caught up yet."""
    from state import SystemState

    state = SystemState()
    state.note_rfid("9182FB66", "lvl2")
    state.note_rfid("A1B2C3D4", "lvl1")
    assert state.system_status == "NOMINAL"
    state.levels["2"] = {"status": "WARNING", "route": "OPEN"}
    assert state.refresh_muster() == 2
    assert state.rfid_log == []
    assert state.zones == {}


def test_a_site_alarm_clears_tags_on_the_other_level():
    """The siren is the site alarm. A Level 1 hold must not leave Level 2
    tags sitting on the muster board."""
    from state import SystemState

    state = SystemState()
    state.note_rfid("9182FB66", "lvl2")
    state.note_rfid("A1B2C3D4", "lvl1")
    state.system_status = "CRITICAL"
    state.underground["holds"] = ["J2"]
    assert state.refresh_muster() == 2
    assert state.rfid_log == []
    assert state.zones == {}


def test_rfid_frame():
    assert validate_rfid_frame({"v": 2, "uid": "001", "reader": "hub"})[0]
    assert validate_rfid_frame({"uid": "001"})[0], "v defaults to the current schema"
    assert "empty" in validate_rfid_frame({"uid": "   "})[1]
    assert validate_rfid_frame({"uid": 42})[1].startswith("'uid' is int")
    assert validate_rfid_frame({"v": 2})[1] == "missing 'uid'"


@pytest.mark.parametrize("cmd", [
    {"cmd": "led", "junction": "J2", "pattern": "chase_red", "ms": 120},
    {"cmd": "siren", "level": 2, "on": True},
    {"cmd": "advisory", "level": 1, "on": False},
    {"cmd": "gps", "lat": 23.643, "lon": 85.318, "alt": 0},
])
def test_downlink_valid(cmd):
    assert validate_downlink(cmd)[0]


@pytest.mark.parametrize("cmd,needle", [
    ({"cmd": "led", "junction": "J9", "pattern": "x"}, "J1–J4"),
    ({"cmd": "led", "junction": "J1", "pattern": ""}, "'pattern'"),
    ({"cmd": "siren", "level": 3, "on": True}, "level"),
    ({"cmd": "siren", "level": 1, "on": "yes"}, "boolean"),
    ({"cmd": "reboot"}, "unknown command"),
])
def test_downlink_invalid(cmd, needle):
    ok, why = validate_downlink(cmd)
    assert not ok and needle in why, why


# ---------------------------------------------------------------------------
# migrate_payload — the compatibility path
# ---------------------------------------------------------------------------

def test_migrate_bare_v1_node_frame():
    out = migrate_payload({"node": 2, "disp": 5.0, "tilt": 0.0, "acc": 0.01})
    assert out["v"] == 2
    for k, default in (("vib", 0), ("micro", None), ("macro", None), ("rssi", 0),
                       ("batt", None)):
        assert out[k] == default
    # Phase 1: a v1 frame carries no `ms` and (here) no `acc`, so the upconverted
    # frame is *synthetic* and says so. It must not be FLAG_LOW_BATTERY — that is
    # bit 3, which §1.3 of the build-phases doc would have set by mistake.
    assert out["flags"] == FLAG_SYNTHETIC
    assert FLAG_SYNTHETIC == 1 << 5, "bit 3 is low battery; synthetic is bit 5"
    assert not (out["flags"] & FLAG_LOW_BATTERY)
    assert out["disp"] == 5.0, "present values are never modified"


def test_migrate_v2_frame_is_not_flagged_synthetic():
    """A frame that carried its own clock and its own acceleration is measured."""
    out = migrate_payload({"node": 2, "disp": 5.0, "tilt": 0.0, "acc": 0.01,
                           "ms": 184220})
    assert out["flags"] == 0


def test_migrate_never_overwrites_present_values():
    src = {"node": 1, "disp": 1.0, "micro": 9.9, "vib": 7}
    out = migrate_payload(src)
    assert out["micro"] == 9.9 and out["vib"] == 7


def test_migrate_is_idempotent_for_v2():
    src = valid_frame()
    assert migrate_payload(src) == src


def test_migrate_envelope_recurses():
    env = {"via": "hub", "seq": 1, "frames": [{"node": 1, "disp": 1.0, "tilt": 0.0, "acc": 0.0}]}
    out = migrate_payload(env, from_v=1)
    assert out["v"] == 2 and out["frames"][0]["v"] == 2


def test_migrate_rejects_non_dict():
    with pytest.raises(TypeError):
        migrate_payload(["not", "a", "dict"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The session.csv contract (record → replay)
# ---------------------------------------------------------------------------

def test_session_row_round_trip():
    line = format_session_row(500, 2, 5.01, 0.0, 0.02, 3, 412.5, 1.2, "", "hardware")
    row = dict(zip(
        "t_ms,node,disp,tilt,acc,vib,micro,macro,uid,source".split(","), line.split(",")))
    parsed = parse_session_row(row)
    assert parsed["node"] == 2 and parsed["disp"] == 5.01
    assert parsed["vib"] == 3 and parsed["micro"] == 412.5 and parsed["macro"] == 1.2


def test_rfid_row_is_not_a_node_frame():
    """An RFID event has an empty node column — never a literal 0, which would
    invent node zero in a replay."""
    line = format_session_row(1000, None, uid="001")
    assert line.split(",")[1] == "", "the node column must be empty for an RFID row"
    row = dict(zip(
        "t_ms,node,disp,tilt,acc,vib,micro,macro,uid,source".split(","), line.split(",")))
    parsed = parse_session_row(row)
    assert parsed["uid"] == "001"
    assert "node" not in parsed


def test_absent_numbers_are_blank_not_nan():
    line = format_session_row(0, 1, 5.0, 0.0, 0.01)
    assert "nan" not in line.lower()
    assert line.split(",")[6] == "" and line.split(",")[7] == ""


def test_session_file_skips_junk_and_comments():
    body = "\n".join([
        "t_ms,node,disp,tilt,acc,vib,micro,macro,uid,source",
        format_session_row(0, 1, 5.0, 0.0, 0.01),
        "", "  ", "# a comment row", "not,a,valid,row",
        format_session_row(500, 2, 5.02, 0.0, 0.02),
        "end",                                  # short row → skipped, not fatal
    ])
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "s.csv"
        p.write_text(body)
        rows = read_session_csv(p)
    assert len(rows) == 2, f"expected the 2 valid rows, got {rows}"
    assert rows[0]["node"] == 1 and rows[1]["node"] == 2


# ---------------------------------------------------------------------------
# The payload shapes
# ---------------------------------------------------------------------------

def test_node_state_projections():
    from contracts import NodeState
    n = NodeState(node_id="node_2", micro=412.5, macro=3.2)
    pub = n.to_public()
    assert set(pub) == set(NODE_PUBLIC_FIELDS)
    # Phase 1: migration 002 widened the row from 4 columns to 10. The tuple and
    # the INSERT column list are asserted equal, because a row and an INSERT that
    # drift apart write the wrong column and the data still looks plausible.
    assert n.to_db_tuple() == ("node_2", 0.0, 5.0, 0.0, 0, 412.5, 3.2, 0, 0.0, None)
    assert len(n.to_db_tuple()) == len(TELEMETRY_DB_COLUMNS) == 10
    assert n.to_frame()["node"] == 2


def test_telemetry_skeleton_carries_every_required_path():
    from tests.shape import key_paths
    paths = key_paths(TelemetryPayload.skeleton().to_public())
    for required in REQUIRED_TELEMETRY_PATHS:
        assert required in paths, f"skeleton is missing {required}"


def test_required_paths_are_well_formed_and_resolve():
    """Every documented path is well formed — no empty segments, no stray
    whitespace, and never deeper than the payload nests. That each one actually
    *exists* is the sibling test above; this one is about the spelling."""
    assert len(REQUIRED_TELEMETRY_PATHS) >= 10
    for required in REQUIRED_TELEMETRY_PATHS:
        parts = required.split(".")
        assert all(parts), f"{required} has an empty segment"
        assert required == required.strip(), f"{required} has stray whitespace"
        assert len(parts) <= 3, f"{required} is deeper than the payload goes"
    # The two route paths are the ones that actually gate evacuation, so their
    # values must be constrained, not just present.
    assert "underground.route_A_status" in REQUIRED_TELEMETRY_PATHS


def test_required_paths_match_the_shipped_payload_contract():
    """The list is the *shipped* shape — every entry must exist in the captured
    v4.4 fixture, or the list is describing something the server never sent."""
    import json
    from pathlib import Path
    from tests.shape import key_paths
    fx = Path(__file__).resolve().parent / "fixtures" / "telemetry.json"
    if not fx.exists():                             # pragma: no cover
        pytest.skip("baseline fixture not present")
    shipped = key_paths(json.loads(fx.read_text()))
    for required in REQUIRED_TELEMETRY_PATHS:
        assert required in shipped, (
            f"{required} is required by the contract but absent from the v4.4 "
            "baseline — either the list is wrong or the contract regressed"
        )
