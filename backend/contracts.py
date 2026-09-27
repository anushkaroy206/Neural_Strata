"""
Neural Strata — the frozen contracts.
=====================================

Schema version, canonical node identifiers, and the validators that stand
between a malformed wireless frame and a ``KeyError`` three functions deep in
the pipeline. **Contracts are code here, not prose.** Change one of these and
you bump ``SCHEMA_VERSION`` in the same commit.

Phase 0 · implements §A.3.1–A.3.5 and §A.3.8.

Frame inventory
---------------
==============  ==========================================================
node → hub      ``{"v":2,"node":3,"ms":…,"disp":…,"tilt":…,"acc":…, …}``
ring envelope   ``{"v":2,"via":"lvl2","seq":…,"link":…,"frames":[…]}``
main hub → PC   ``{"v":2,"via":"hub","seq":…,"t":…,"frames":[…],"uid":"001"}``
RFID            ``{"v":2,"uid":"001","reader":"hub"}``
==============  ==========================================================

Backward compatibility
----------------------
The firmware shipped today emits **bare node frames** (``{"node":1,…}``) with no
envelope and no version field. Those must keep working forever — that is the
``migrate_payload`` path, and the reason ``validate_node_frame`` accepts a
missing ``v`` (it is read as v1) while rejecting a *known-but-unknown* version.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Version & identifiers
# ---------------------------------------------------------------------------

#: Wireless + payload contract revision. Hub and server must agree.
SCHEMA_VERSION = 2

#: Versions this server understands. v1 = the bare frame the firmware ships today.
SUPPORTED_VERSIONS = (1, 2)

NodeId = Literal["node_1", "node_2", "node_3", "node_4"]

#: Canonical node identifiers — 4 sensor nodes (§topology of record).
NODE_IDS: tuple[str, ...] = ("node_1", "node_2", "node_3", "node_4")

#: Nodes that carry the crack instrumentation — the **two-stage crack column**:
#: a YZC-131 load cell through an HX711 (fine channel, µm of crack-mouth opening)
#: plus a 10 kΩ rotary pot on a lever (coarse channel, mm of seam opening).
#:
#: The plan's wording for the fine channel is "BF350 foil gauge"; the build uses a
#: load cell (Phase 3, H2). The *interface* is identical — a Wheatstone bridge on
#: an HX711 — which is why nothing downstream had to change except the unit.
CRACK_NODE_IDS: tuple[str, ...] = ("node_2", "node_4")

#: Frame flag bits (§A.3.1).
FLAG_MICRO_SATURATED = 1 << 0
FLAG_MACRO_AT_RAIL = 1 << 1
FLAG_BASELINE_UNSETTLED = 1 << 2
FLAG_LOW_BATTERY = 1 << 3
FLAG_LAST_GASP = 1 << 4
#: **Added in Phase 1 — and it is bit 5, not bit 3.**
#:
#: ``Backend_Build_Phases.md`` §1.3 says ``migrate_payload`` should "flag the
#: frame ``flags |= 8`` (synthetic)". Bit 3 *is* 8 — and bit 3 is already
#: ``FLAG_LOW_BATTERY`` in the §A.3.1 bitfield table. Followed literally, every
#: upconverted v1 frame would have been reported to the operator as a low
#: battery. The bitfield table is the authority (it is the wire contract the
#: firmware implements), so synthetic takes the first free bit.
FLAG_SYNTHETIC = 1 << 5

#: **Phase 2 — bit 6.** The node is alive; this frame is not a measurement.
#:
#: §2.5 puts a heartbeat on the wire every 2 s so that a quiet node and a dead
#: node stop looking alike. That only works if the server treats the two kinds of
#: frame differently: a heartbeat must refresh ``last_arrival`` (that is its whole
#: job) and must **not** enter the model window. A window built from repeated
#: values is a flat line, and a flat line through the TTF regressor reads as
#: *perfectly stable ground* — the most reassuring possible answer, computed from
#: no data at all.
#:
#: Additive by construction: a new value in an existing ``uint8`` field, not a
#: new field. The packed struct stays 35 B and an older server simply does not
#: name the bit (``flags_to_list`` reports only bits it knows).
FLAG_HEARTBEAT = 1 << 6

#: **Phase 3 — bit 7.** The load cell is no longer carrying its pre-tension: the
#: spring has gone slack, the seam has closed, or the cell has come unhooked.
#:
#: This bit exists because of the *choice of rig*, and it is worth being precise
#: about why. A crack-mouth rig under **tension** (spring across the gap, signal
#: rises as the seam opens) measures opening only while the spring is stretched.
#: Once it is slack the force is zero **regardless of what the seam does next** —
#: so the channel reports a constant, calm, plausible number while measuring
#: nothing at all. That is precisely the failure mode §3.1 names for the railed
#: gauge: *"an open gauge must read as fault, not as zero strain — these look
#: identical in the data otherwise."* Same trap, one level up, and it is only
#: reachable on a tension rig, which is why the plan (written for a bonded foil
#: gauge) does not name it.
#:
#: It is a separate bit from ``FLAG_MICRO_SATURATED`` because it is a separate
#: fault with a separate remedy: saturated = the ADC is at its rail (broken wire,
#: dead cell), unloaded = the ADC is reading fine and the *rig* is not measuring.
FLAG_MICRO_UNLOADED = 1 << 7

FLAG_NAMES = {
    FLAG_MICRO_SATURATED: "micro_saturated",
    FLAG_MACRO_AT_RAIL: "macro_at_rail",
    FLAG_BASELINE_UNSETTLED: "baseline_unsettled",
    FLAG_LOW_BATTERY: "low_battery",
    FLAG_LAST_GASP: "last_gasp",
    FLAG_SYNTHETIC: "synthetic",
    FLAG_HEARTBEAT: "heartbeat",
    FLAG_MICRO_UNLOADED: "micro_unloaded",
}


def flags_to_list(flags: int) -> list[str]:
    """Decode a frame flag bitfield into readable names (for logs and the API)."""
    return [name for bit, name in FLAG_NAMES.items() if int(flags or 0) & bit]


# ---------------------------------------------------------------------------
# Status vocabulary
# ---------------------------------------------------------------------------
#: The three words a node, a level or a zone may be in. This lives in the
#: dependency-free leaf — not in ``state.py`` — because **four** modules now need
#: to rank them: ``state`` (per node and per level), ``engine.geomech`` (per
#: zone), and ``engine.health`` (the mode ladder). ``geomech`` cannot import
#: ``state`` (state imports it), so a copy of the ranking there would be the D10
#: pattern again: two literals and a test hoping they stay equal. One literal,
#: imported by everything.
STATUSES: tuple[str, ...] = ("NOMINAL", "WARNING", "CRITICAL")
STATUS_RANK: dict[str, int] = {s: i for i, s in enumerate(STATUSES)}


def worst_status(statuses) -> str:
    """The worst of a collection of status words.

    Unknown words rank as ``NOMINAL`` — deliberately, and the same way the
    dashboard ranks them: a value the server does not recognise must not be able
    to raise an alarm, or a typo becomes an incident. Absence is handled by the
    caller (``worst_status([])`` is ``NOMINAL``), never by inventing a word here.
    """
    worst = "NOMINAL"
    for s in statuses:
        if STATUS_RANK.get(s, 0) > STATUS_RANK.get(worst, 0):
            worst = s
    return worst


# ---------------------------------------------------------------------------
# Numeric coercion
# ---------------------------------------------------------------------------

def coerce_float(v: Any, default: float = math.nan) -> float:
    """Safe numeric coercion from JSON.

    ``None``, ``""``, ``"n/a"`` and anything else unparseable become ``default``
    rather than raising — a single bad field must not take down a frame.
    Booleans are treated as numeric 0/1 (JSON has no separate int/bool repr for
    some senders), everything else goes through ``float()``.
    """
    if v is None:
        return default
    if isinstance(v, bool):
        return float(int(v))
    if isinstance(v, (int, float)):
        f = float(v)
        return f if math.isfinite(f) else default
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def coerce_int(v: Any, default: int = 0) -> int:
    """Safe integer coercion from JSON (truncates floats, like ``int()``)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v) if math.isfinite(v) else default
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Validation — every validator returns (ok, reason), never raises
# ---------------------------------------------------------------------------

#: field → (required, types, physical range, note). ``None`` range = unconstrained.
_NODE_FIELDS: dict[str, tuple[bool, tuple[type, ...], tuple[float, float] | None]] = {
    "node":  (True,  (int,),          (1, 4)),
    # `ms` is required for v2 (measured Δt) but absent from the bare v1 frames
    # the firmware ships today — see the explicit version check in the validator.
    "ms":    (False, (int, float),    (0, None)),
    "disp":  (True,  (int, float),    (-50_000.0, 50_000.0)),   # mm
    "tilt":  (True,  (int, float),    (-180.0, 180.0)),         # deg
    "acc":   (True,  (int, float),    (-100.0, 100.0)),         # m/s²
    "vib":   (False, (int, float),    (0, None)),               # counts / 2 s
    # `micro` = **crack-mouth opening, µm** (Phase 3). The plan called this
    # micro-strain in µε, which was right for the BF350 foil gauge it was written
    # for and wrong for the YZC-131 load cell that replaced it: a load cell
    # measures force across the seam, and calibration against a feeler gauge turns
    # that into an opening in µm. See `config.MICRO_RATE_THRESHOLD` for why the
    # unit change does not change the physics being detected.
    #
    # The plausibility window is wide on purpose: its job is to catch a unit error
    # (a µε number read as µm is off by ~1000×), never to argue with a real
    # opening — a *rejected frame* costs a sample, a *flagged one* costs nothing.
    "micro": (False, (int, float, type(None)), (-2_000.0, 100_000.0)),   # µm (CMOD)
    "macro": (False, (int, float, type(None)), (0.0, 100.0)),          # mm
    "rssi":  (False, (int, float),    (-120.0, 10.0)),          # dBm
    "batt":  (False, (int, float),    (0.0, 6.0)),              # V
    "flags": (False, (int,),          (0, 255)),
}


def validate_node_frame(d: Any) -> tuple[bool, str]:
    """Validate one node → hub frame. Returns ``(True, "")`` or ``(False, reason)``.

    Accepts a v1 bare frame (no ``v``): the caller's version decides the rules.
    Rejects an *unsupported* version explicitly rather than guessing.
    """
    if not isinstance(d, dict):
        return False, f"frame is {type(d).__name__}, not an object"
    if "node" not in d:
        return False, "missing 'node' — not a node frame"

    version = d.get("v", 1)
    if not isinstance(version, int):
        return False, f"'v' is {type(version).__name__}, not an integer"
    if version not in SUPPORTED_VERSIONS:
        return False, f"unsupported schema version v{version} (this server speaks {SUPPORTED_VERSIONS})"

    node = d.get("node")
    if isinstance(node, bool) or not isinstance(node, int):
        return False, f"'node' is {type(node).__name__}, not an integer"
    if not 1 <= node <= 4:
        return False, f"'node' out of range: {node} (expected 1–4)"

    if version >= SCHEMA_VERSION and "ms" not in d:
        return False, "missing required field 'ms' (v2 frames carry node uptime for measured Δt)"

    for name, (required, types, rng) in _NODE_FIELDS.items():
        if name == "node":
            continue
        if name not in d or d[name] is None:
            if required:
                return False, f"missing required field '{name}'"
            continue
        val = d[name]
        numeric_types = tuple(t for t in types if t is not type(None))
        if isinstance(val, bool) or not isinstance(val, numeric_types):
            return False, f"'{name}' is {type(val).__name__}, expected {'/'.join(t.__name__ for t in numeric_types)}"
        if rng is not None:
            lo, hi = rng
            if lo is not None and val < lo:
                return False, f"'{name}' below range: {val} < {lo}"
            if hi is not None and val > hi:
                return False, f"'{name}' above range: {val} > {hi}"

    # Crack channels are defined only on the instrumented nodes.
    n_id = f"node_{node}"
    if n_id not in CRACK_NODE_IDS:
        for ch in ("micro", "macro"):
            if d.get(ch) is not None:
                return False, f"'{ch}' present on {n_id} (crack channels exist only on {', '.join(CRACK_NODE_IDS)})"

    return True, ""


def validate_ring_frame(d: Any) -> tuple[bool, str]:
    """Validate a ring relay envelope (level hub → main hub).

    Reminder: this is a *relayed link*, not a hop. The envelope exists so level-2
    data can reach the main hub through the J2↔J3 cross-link.
    """
    if not isinstance(d, dict):
        return False, f"envelope is {type(d).__name__}, not an object"
    version = d.get("v", SCHEMA_VERSION)
    if version not in SUPPORTED_VERSIONS:
        return False, f"unsupported envelope version v{version}"
    via = d.get("via")
    if via not in ("lvl1", "lvl2"):
        return False, f"'via' must be 'lvl1' or 'lvl2' (got {via!r})"
    seq = d.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, (int, float)):
        return False, f"'seq' must be numeric (got {type(seq).__name__})"
    frames = d.get("frames")
    if not isinstance(frames, list):
        return False, "'frames' must be a list (possibly empty)"
    if len(frames) > 64:
        return False, f"envelope carries {len(frames)} frames — a relay burst is capped at 64"
    link = d.get("link")
    if link is not None and not isinstance(link, str):
        return False, f"'link' must be a string (got {type(link).__name__})"
    for i, fr in enumerate(frames):
        ok, why = validate_node_frame(fr)
        if not ok:
            return False, f"frames[{i}]: {why}"
    return True, ""


def validate_rfid_frame(d: Any) -> tuple[bool, str]:
    """Validate an RFID read (`{"v":2,"uid":"001","reader":"hub"}`)."""
    if not isinstance(d, dict):
        return False, f"frame is {type(d).__name__}, not an object"
    version = d.get("v", SCHEMA_VERSION)
    if version not in SUPPORTED_VERSIONS:
        return False, f"unsupported RFID version v{version}"
    uid = d.get("uid")
    if uid is None:
        return False, "missing 'uid'"
    if not isinstance(uid, str):
        return False, f"'uid' is {type(uid).__name__}, expected a string"
    if not uid.strip():
        return False, "'uid' is empty"
    reader = d.get("reader")
    if reader is not None and reader not in ("hub", "lvl1", "lvl2"):
        return False, f"'reader' must be hub/lvl1/lvl2 (got {reader!r})"
    return True, ""


def validate_downlink(cmd: dict) -> tuple[bool, str]:
    """Validate a server → hub command frame (§A.3.4).

    Junctions have no MCU: every ``led`` command targets a *level hub*, which
    drives its own strips. The ``junction`` field is addressed by ID only so the
    firmware can pick the segment to light.
    """
    if not isinstance(cmd, dict):
        return False, "command is not an object"
    kind = cmd.get("cmd")
    if kind == "led":
        if cmd.get("junction") not in ("J1", "J2", "J3", "J4"):
            return False, f"led: 'junction' must be J1–J4 (got {cmd.get('junction')!r})"
        if not isinstance(cmd.get("pattern"), str) or not cmd["pattern"]:
            return False, "led: 'pattern' must be a non-empty string"
        if coerce_int(cmd.get("ms"), 0) < 0:
            return False, "led: 'ms' must be >= 0"
        return True, ""
    if kind == "siren":
        if cmd.get("level") not in (1, 2):
            return False, f"siren: 'level' must be 1 or 2 (got {cmd.get('level')!r})"
        if not isinstance(cmd.get("on"), bool):
            return False, "siren: 'on' must be a boolean"
        return True, ""
    if kind == "advisory":
        if cmd.get("level") not in (1, 2):
            return False, f"advisory: 'level' must be 1 or 2 (got {cmd.get('level')!r})"
        if not isinstance(cmd.get("on"), bool):
            return False, "advisory: 'on' must be a boolean"
        return True, ""
    if kind == "gps":
        for k in ("lat", "lon"):
            if coerce_float(cmd.get(k)) is math.nan:
                return False, f"gps: '{k}' must be numeric"
        return True, ""
    return False, f"unknown command {kind!r} (expected led/siren/advisory/gps)"


# ---------------------------------------------------------------------------
# migrate_payload — the v1 compatibility path
# ---------------------------------------------------------------------------

def migrate_payload(p: Any, from_v: int | None = None) -> dict:
    """Bring an older payload up to ``SCHEMA_VERSION``. Never raises.

    v1 → v2 for a bare node frame means: tag the version, fill the channels that
    only exist on the instrumented nodes with ``None``, and default the link
    fields. Absent values are filled, present values are **never** modified.
    """
    if not isinstance(p, dict):
        raise TypeError(f"migrate_payload expects a dict, got {type(p).__name__}")

    out = dict(p)
    version = from_v if from_v is not None else out.get("v", 1)
    if version >= SCHEMA_VERSION:
        return out

    if "node" in out:
        # node → hub frame
        out.setdefault("v", 2)
        synthetic = "ms" not in out or "acc" not in out
        for k, default in (("vib", 0), ("micro", None), ("macro", None),
                           ("rssi", 0), ("batt", None), ("flags", 0)):
            out.setdefault(k, default)
        if synthetic:
            # A v1 frame carries no node clock and no measured acceleration. The
            # reader synthesises both; the flag records that neither is a
            # measurement (§1.3).
            out["flags"] = coerce_int(out.get("flags"), 0) | FLAG_SYNTHETIC
    elif "frames" in out:
        # ring / hub envelope
        out.setdefault("v", 2)
        out.setdefault("via", "hub")
        out.setdefault("seq", 0)
        out["frames"] = [
            migrate_payload(f, from_v=1) if isinstance(f, dict) else f
            for f in out.get("frames") or []
        ]
    elif "uid" in out:
        out.setdefault("v", 2)
        out.setdefault("reader", "hub")
    return out


# ---------------------------------------------------------------------------
# The API-shape contracts
# ---------------------------------------------------------------------------

#: JSON paths that ``/api/telemetry`` must keep **forever** — the dashboard
#: reads these. New keys are additive only (§A.3.5 backward-compatibility
#: contract). Exercised by ``tests/test_api_contracts.py``.
#:
#: This list is the shape the **shipped v4.4 server actually emits**, captured in
#: ``tests/fixtures/telemetry.json`` and verified against it — not the richer
#: target shape drawn in §A.3.5. The difference matters: §A.3.5 also shows
#: ``schema``, ``degraded_mode``, ``levels``, ``ai`` and the per-node crack
#: channels, none of which the running server has ever sent. Those arrive as
#: *additive* keys in later phases (``vib``/``micro``/``macro`` in Phase 1, the
#: cascade block in Phase 5), and adding a key is allowed. Losing one of these is
#: not.
REQUIRED_TELEMETRY_PATHS: tuple[str, ...] = (
    "system_status",
    "ttf_seconds",
    "timestamp",
    "nodes",
    "nodes.node_1.status",
    "nodes.node_1.tilt_x",
    "nodes.node_1.displacement_mm",
    "nodes.node_1.acceleration",
    "nodes.node_2.status",
    "underground.route_A_status",
    "underground.route_B_status",
    "rfid_log",
)

#: Node fields the dashboard reads, beyond the ones already listed above.
#:
#: Phase 3 appends the five crack readouts. They are *derived* — recomputed on
#: read, never persisted (§3.5 "Database") — and they are ``null``, not ``0``, on
#: a node with no instrument, because a zero opens the door to "measured, and it
#: is not moving", which is the one thing an absent channel must never say.
NODE_PUBLIC_FIELDS: tuple[str, ...] = (
    "status", "tilt_x", "displacement_mm", "acceleration", "velocity",
    "vib", "micro", "macro", "crack_stage", "last_seen_s", "stale",
    "micro_rate", "micro_baseline_um", "micro_settled", "macro_d30",
    "cross_sensor_coherence",
)


#: Column order of the ``sensor_telemetry`` INSERT (migration 002). Must stay in
#: step with ``NodeState.to_db_tuple()``.
TELEMETRY_DB_COLUMNS: tuple[str, ...] = (
    "node_id", "tilt_x", "displacement_mm", "acceleration",
    "vib", "micro", "macro", "rssi", "batt", "level",
)


# ---------------------------------------------------------------------------
# The session.csv contract (§0.3) — record once, replay forever
# ---------------------------------------------------------------------------

#: Column order of a recorded session. Frozen: ``tools/record.py`` writes it,
#: ``tools/replay.py`` and ``app.py --replay`` read it, and the AI notebooks can
#: ingest it directly.
SESSION_CSV_FIELDS: tuple[str, ...] = (
    "t_ms", "node", "disp", "tilt", "acc", "vib", "micro", "macro", "uid", "source",
)

#: ``source`` values. ``hardware`` = a real frame; ``replay`` = from a CSV;
#: ``sim`` = an injected scenario.
SESSION_SOURCES = ("hardware", "replay", "sim")


def format_session_row(t_ms: float, node: int | None, disp: float = 0.0, tilt: float = 0.0,
                       acc: float = 0.0, vib: int = 0, micro: float | None = None,
                       macro: float | None = None, uid: str = "",
                       source: str = "hardware") -> str:
    """One CSV line.

    Empty string for an absent value — never a literal ``nan`` (some readers turn
    that into the string ``"nan"``, others into 0.0) and never a literal ``0`` for
    an absent node (0 is not a node, and a replay must not invent one). An RFID
    event is written with an empty ``node`` column.
    """
    def num(v):
        return "" if v is None or (isinstance(v, float) and math.isnan(v)) else repr(float(v))
    return ",".join([
        str(int(t_ms)), "" if not node else str(int(node)), num(disp), num(tilt), num(acc),
        str(int(vib or 0)), num(micro), num(macro), str(uid or ""), source,
    ])


def parse_session_row(row: dict) -> dict | None:
    """Parse one ``csv.DictReader`` row into a node frame or an RFID event.

    Returns ``None`` for a row that carries neither — a malformed line must never
    stop a replay half way through.
    """
    node_idx = coerce_int(row.get("node"), 0)
    has_node = 1 <= node_idx <= 4          # 0 / blank / junk is not a node frame
    has_uid = row.get("uid") not in (None, "")
    if not has_uid and not has_node:
        return None

    t_ms = coerce_float(row.get("t_ms"), 0.0)
    if has_uid and not has_node:
        return {"t_ms": t_ms, "uid": str(row["uid"]).strip(), "reader": row.get("source") or None}

    def num(key: str) -> float:
        v = coerce_float(row.get(key))
        return 0.0 if math.isnan(v) else v

    def optional(key: str) -> float | None:
        v = coerce_float(row.get(key))
        return None if math.isnan(v) else v

    frame: dict = {
        "v": SCHEMA_VERSION,
        "node": node_idx,
        "ms": int(t_ms),
        "disp": num("disp"),
        "tilt": num("tilt"),
        "acc": num("acc"),
        "vib": coerce_int(row.get("vib"), 0),
        "micro": optional("micro"),
        "macro": optional("macro"),
        "t_ms": t_ms,
    }
    return frame


def read_session_csv(path) -> list[dict]:
    """Read a session file into a list of parsed rows (frames and RFID events).

    Tolerant by design: blank lines and comment lines are skipped, a bad row is
    skipped rather than aborting, and the file's own order is preserved.
    """
    import csv as _csv
    from pathlib import Path as _Path

    out: list[dict] = []
    with _Path(path).open(newline="") as fh:
        reader = _csv.DictReader(
            line for line in fh if line.strip() and not line.lstrip().startswith("#")
        )
        for row in reader:
            parsed = parse_session_row(row)
            if parsed is not None:
                out.append(parsed)
    return out


@dataclass
class NodeState:
    """One node's live fields (server-side), with the two projections anything
    downstream is allowed to use."""

    node_id: str
    status: str = "NOMINAL"
    tilt_x: float = 0.0
    displacement_mm: float = 5.0
    acceleration: float = 0.0
    velocity: float = 0.0
    vib: int = 0
    #: Crack-mouth opening, µm, baseline-subtracted (Phase 3 — see
    #: ``config.MICRO_RATE_THRESHOLD``). ``None`` = no channel, a channel that has
    #: not settled, or a railed one. Never ``0`` for "unknown".
    micro: float | None = None
    #: Seam opening, mm (macro pot). Clamped at 0 by the firmware: mechanical
    #: backlash must not subtract from the delta (§3.8).
    macro: float | None = None
    #: Phase 3 derived readouts, written by ``engine.crack`` and projected to the
    #: dashboard through ``to_public()``.
    micro_rate: float | None = None          # µm/s, 5 s window, 3-point median
    micro_baseline_um: float | None = None   # the zero it was subtracted from
    micro_settled: bool = False              # has the 15 s baseline completed
    macro_d30: float | None = None           # mm over 30 s
    cross_sensor_coherence: float | None = None   # §3.6, [0, 1]
    crack_stage: str = "NONE"
    last_seen_s: float = 0.0
    stale: bool = False
    timestamp: float | None = None
    flags: int = 0
    #: Phase 1 — link quality and supply. Deliberately NOT in ``to_public()``:
    #: §A.3.5's node shape is frozen at the eleven fields in
    #: ``NODE_PUBLIC_FIELDS``. These ride on ``/api/extended`` instead.
    rssi: int = 0
    batt: float = 0.0
    level: int | None = None
    via: str | None = None

    #: Phase 6 (B3) — severity, creep stage and confidence, written by
    #: ``engine.severity`` and published on ``/api/ai``. Deliberately **not** in
    #: ``to_public()``: §A.3.5's node shape is frozen at eleven fields and the
    #: deck reads that shape. These ride on the AI card, which is where §6.3
    #: puts them.
    stage: str = "STABLE"
    stage_since: float | None = None
    #: Seconds the TERTIARY condition has held, and when it was last evaluated —
    #: the hysteresis accumulator, reset with the node.
    stage_hold_s: float = 0.0
    #: The sustained-rise counter and the previous evaluation's ``v10`` — the
    #: second-derivative evidence for TERTIARY (see ``engine.severity``).
    stage_rise_s: float = 0.0
    stage_last_v10: float | None = None
    stage_eval_t: float | None = None
    severity: str = "NOMINAL"
    curvature: float = 0.0
    confidence: str = "MEDIUM"
    confidence_reason: str = ""

    def to_public(self) -> dict:
        """The ``/api/telemetry`` shape. ``velocity`` and ``stale`` are internal
        readouts the dashboard may use; ``velocity`` is stripped by
        ``public_telemetry`` before the payload leaves the server."""
        return {
            "status": self.status,
            "tilt_x": float(self.tilt_x),
            "displacement_mm": float(self.displacement_mm),
            "acceleration": float(self.acceleration),
            "velocity": float(self.velocity),
            "vib": int(self.vib),
            "micro": self.micro,
            "macro": self.macro,
            "crack_stage": self.crack_stage,
            "micro_rate": self.micro_rate,
            "micro_baseline_um": self.micro_baseline_um,
            "micro_settled": bool(self.micro_settled),
            "macro_d30": self.macro_d30,
            "cross_sensor_coherence": self.cross_sensor_coherence,
            "last_seen_s": float(self.last_seen_s),
            "stale": bool(self.stale),
        }

    def to_db_tuple(self) -> tuple:
        """Row for ``sensor_telemetry`` — column order matches migration **002**.

        Was four columns in Phase 0. Migration 002 adds ``vib``, ``micro``,
        ``macro``, ``rssi``, ``batt`` and ``level``, so the row grew with the
        frame. ``TELEMETRY_DB_COLUMNS`` is the matching INSERT list; the pair is
        asserted equal in ``tests/test_contracts.py``, because a tuple and an
        INSERT that drift apart write the wrong column silently.
        """
        return (self.node_id, float(self.tilt_x), float(self.displacement_mm),
                float(self.acceleration), int(self.vib),
                None if self.micro is None else float(self.micro),
                None if self.macro is None else float(self.macro),
                int(self.rssi), float(self.batt),
                None if self.level is None else int(self.level))

    def to_frame(self) -> dict:
        """Wire frame for this node (for the simulator and the replay tool)."""
        idx = NODE_IDS.index(self.node_id) + 1 if self.node_id in NODE_IDS else 0
        return {
            "v": SCHEMA_VERSION, "node": idx,
            "disp": float(self.displacement_mm), "tilt": float(self.tilt_x),
            "acc": float(self.acceleration), "vib": int(self.vib),
            "micro": self.micro, "macro": self.macro, "flags": int(self.flags),
        }


@dataclass
class TelemetryPayload:
    """The ``/api/telemetry`` body. Frozen field names — see
    ``REQUIRED_TELEMETRY_PATHS``."""

    schema: int = SCHEMA_VERSION
    system_status: str = "NOMINAL"
    timestamp: str = ""
    degraded_mode: str = "FULL"
    cascade_mode: str = "advisory"
    ttf_seconds: float | int | str | None = None
    ttf_band: list[int] | None = None
    nodes: dict[str, dict] = field(default_factory=dict)
    levels: dict[str, dict] = field(default_factory=dict)
    underground: dict = field(default_factory=dict)
    ai: dict = field(default_factory=dict)
    rfid_log: list = field(default_factory=list)

    def to_public(self) -> dict:
        return asdict(self)

    @classmethod
    def skeleton(cls) -> "TelemetryPayload":
        """An empty-but-complete payload — used by tests and by the replay tool."""
        return cls(
            nodes={n: NodeState(node_id=n).to_public() for n in NODE_IDS},
            levels={str(lv): {"status": "NOMINAL", "route": "OPEN", "exits": [],
                              "pair_warn": False}
                    for lv in (1, 2)},
            underground={"route_A_status": "OPEN", "route_B_status": "OPEN",
                         "junctions": {j: "OK" for j in ("J1", "J2", "J3", "J4")}},
            ai={"model_2": {"name": "ttf_model_v1", "status": "FALLBACK", "mae_s": None},
                "model_1": {"name": "pattern_model_v1", "status": "ABSENT",
                            "disruption_index": None, "label": None},
                "kinematic": {"source": "fukuzono", "alpha": None}},
        )


__all__ = [
    "SCHEMA_VERSION", "SUPPORTED_VERSIONS", "NodeId", "NODE_IDS", "CRACK_NODE_IDS",
    "FLAG_MICRO_SATURATED", "FLAG_MACRO_AT_RAIL", "FLAG_BASELINE_UNSETTLED",
    "FLAG_LOW_BATTERY", "FLAG_LAST_GASP", "FLAG_SYNTHETIC", "FLAG_HEARTBEAT",
    "FLAG_NAMES",
    "flags_to_list", "TELEMETRY_DB_COLUMNS",
    "coerce_float", "coerce_int",
    "validate_node_frame", "validate_ring_frame", "validate_rfid_frame",
    "validate_downlink", "migrate_payload",
    "REQUIRED_TELEMETRY_PATHS", "NODE_PUBLIC_FIELDS", "NodeState", "TelemetryPayload",
    "SESSION_CSV_FIELDS", "SESSION_SOURCES", "format_session_row", "parse_session_row",
    "read_session_csv",
]
