"""Frozen serial line protocol — Central Hub (ESP32) <-> surface laptop.

One USB serial link carries BOTH directions:
  hub  -> laptop : telemetry rows as NDJSON, control lines as UPPERCASE:colon
  laptop -> hub  : ACK_DELETE / REPLAY / STATE? / PING

Rules that must never change:
  1. Telemetry lines start with '{'. Control lines never do. That is the whole demux.
  2. Every telemetry row carries the chunk_id stamped by Core 1, plus its
     position within that chunk. Those two fields are what make the delete gate
     and the replay idempotent.
  3. The laptop never echoes a line back up the wire.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional, Union

PROTOCOL_VERSION = "1.0"

# fields in the order the hub writes them on the SD card (CSV) and in JSON on the wire
ROW_FIELDS = ("seq", "ts_ms", "node", "tilt", "disp_mm", "accel",
              "vib", "micro", "macro", "flags")


# --------------------------------------------------------------------- messages
@dataclass(frozen=True)
class TelemetryRow:
    chunk_id: int
    seq: int
    ts_ms: int
    node: int
    level: int
    tilt: float
    disp_mm: float
    accel: float
    vib: int
    micro: float
    macro: float
    flags: int = 0
    #: `live` or `replay`. **Not a diagnostic nicety** — §5.7 requires a replayed
    #: row to be distinguishable from a live one, because the AI pipeline is
    #: training on rows that arrived twice by design (once live, once from the card
    #: after a gap) and a model that cannot tell them apart double-counts a
    #: back-filled hour. Defaulted to `live` so every existing caller is unchanged.
    source: str = "live"

    def to_payload(self) -> str:
        """Canonical JSON for the outbox — sorted keys, no spaces, stable bytes."""
        return json.dumps(
            {
                "chunk_id": self.chunk_id, "seq": self.seq, "ts_ms": self.ts_ms,
                "node": self.node, "level": self.level, "tilt": self.tilt,
                "disp_mm": self.disp_mm, "accel": self.accel, "vib": self.vib,
                "micro": self.micro, "macro": self.macro, "flags": self.flags,
                "source": self.source,
            },
            sort_keys=True, separators=(",", ":"),
        )


@dataclass(frozen=True)
class ChunkOpen:
    chunk_id: int


@dataclass(frozen=True)
class ChunkClose:
    chunk_id: int
    rows: int
    dropped: int = 0


@dataclass(frozen=True)
class ChunkState:
    """Sent on reconnect / on STATE? — lets a restarted laptop reconcile."""
    chunk_id: int
    rows: int
    dropped: int = 0
    closed: bool = False


@dataclass(frozen=True)
class ChunkDone:
    """`CHUNK_DONE:<chunk>,<rows>,<dropped>` — the hub's **reconciliation** reply.

    §5.7 pairs it with `CHUNK_STATE` on every reconnect: `CHUNK_STATE` describes the
    chunk being written *now*, and one `CHUNK_DONE` is sent for every chunk closed
    on the card and not yet acknowledged.

    It is deliberately a **separate type from `ChunkClose`**, and the difference is
    the whole of §5.7's monotonicity rule:

        CHUNK_CLOSE   a live event, emitted once as the file closes. The hub's word
                      *now* — it may raise or lower a count, because nothing has
                      superseded it.
        CHUNK_DONE    a reply to a question, possibly composed minutes ago and
                      possibly relaying a chunk this laptop already holds.
                      **It may only ever add** — never lower a count, never
                      un-close a chunk, never erase rows.

    Merging the two into one type would have cost either the monotonic guarantee or
    the hub's ability to correct a live count. Two types, two merge rules, one line
    of wire.
    """
    chunk_id: int
    rows: int
    dropped: int = 0


@dataclass(frozen=True)
class ChunkFile:
    """`CHUNK_FILE:<chunk>,<bytes>,<drops>` — informational, from the archivist.

    Emitted the moment a CSV is closed and counted, right behind `CHUNK_CLOSE`.
    It is the only place the *size* of a file is ever reported, and it exists for
    exactly two readers: a human doing a post-mortem (`ls -l` on a card that is
    underground), and the surface's storage arithmetic — a chunk ledger that can
    say this file is 43 KB is a ledger somebody can check against the card.

    It is a **recognised message and not an unknown line**, which sounds pedantic
    and is not: `unknown` is the counter that means "the hub sent something I could
    not read", and a hub reporting a file size every two minutes would have made
    that counter climb forever on a perfectly healthy system. A diagnostic that
    cries wolf once per rotation is worse than none, because it teaches the reader
    to ignore the one number that would have caught a real framing fault.
    """
    chunk_id: int
    bytes: int
    dropped: int = 0


@dataclass(frozen=True)
class BlackboxStatus:
    state: str                      # OK | NOSD | FULL | FAULT
    free_mb: Optional[int] = None


@dataclass(frozen=True)
class DropNotice:
    chunk_id: int
    count: int


@dataclass(frozen=True)
class Unknown:
    raw: str
    reason: str


Message = Union[TelemetryRow, ChunkOpen, ChunkClose, ChunkState, ChunkDone,
                ChunkFile, BlackboxStatus, DropNotice, Unknown]


class ProtocolError(ValueError):
    pass


# ---------------------------------------------------------------------- parsing
def _as_int(v, name):
    """Lenient on the wire, strict about meaning: ints, integral floats and
    numeric strings all become an int. Anything else is a protocol error."""
    if isinstance(v, bool):
        raise ProtocolError(f"{name} must be numeric, got {v!r}")
    if isinstance(v, str):
        s = v.strip()
        try:
            f = float(s)
        except ValueError:
            raise ProtocolError(f"{name} must be numeric, got {v!r}") from None
    elif isinstance(v, (int, float)):
        f = float(v)
    else:
        raise ProtocolError(f"{name} must be numeric, got {v!r}")
    if f != int(f):
        raise ProtocolError(f"{name} must be a whole number, got {v!r}")
    return int(f)


def _as_float(v, name):
    if isinstance(v, bool):
        raise ProtocolError(f"{name} must be numeric, got {v!r}")
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            raise ProtocolError(f"{name} must be numeric, got {v!r}") from None
    raise ProtocolError(f"{name} must be numeric, got {v!r}")


#: The row's three identity fields have **two accepted spellings each**, and the
#: reason is a real firmware constraint rather than leniency. The hub splices its
#: stamp onto the node's own JSON frame, and two of those names are already taken:
#: `seq` is the *node's* monotonic counter (the gap watchdog in `ingestion/ring.py`
#: counts it) and `ms` is the node's uptime, which is what lets the server measure
#: Δt across a node reboot (defect D9's fix). So the hub writes `chunk_seq` and
#: `ts_ms` beside them, and this parser accepts either — a `hub_sim.py` stream and
#: a fielded hub are the same protocol.
#:
#: ── Why `chunk_seq` is tried **first**, and why that order is load-bearing ────
#:
#: `TelemetryRow.seq` is not "the node's sequence number"; it is **the row's index
#: inside its chunk**, because that is what `UNIQUE(chunk_id, seq)` — the whole of
#: replay idempotency — keys on. On the wire the two numbers are different things:
#:
#:     node's own counter   node 1: 991, 992, 993 …      node 2: 991, 992, 993 …
#:     hub's chunk counter  node 1: 17,  21,  25  …      node 2: 18,  22,  26  …
#:
#: Four nodes count from their own power-on, so node 1 and node 2 both reach 991 —
#: and in an *older* firmware the node's counter also restarts after a soft reset
#: (H3: "never reset seq", which is why the watchdog exists at all). Keyed on the
#: node's counter, `(42, 991)` from node 2 is a duplicate of `(42, 991)` from node 1
#: and the row is **silently dropped**, which is the one thing a blackbox may never
#: do. Keyed on the hub's counter it is unique by construction, because one Core-1
#: counter stamps every row of the chunk no matter which node sent it.
#:
#: A simulator or an older frame that carries only `seq` still parses (the sim's
#: `seq` *is* a per-chunk row index — see `sim/hub_sim.py`), so nothing here breaks
#: the bench; the fielded hub's twin simply wins when both are present.
SEQ_KEYS = ("chunk_seq", "seq")
TS_KEYS = ("ts_ms", "hub_ms", "ms")


def parse_telemetry(obj: dict) -> TelemetryRow:
    required = ("chunk_id", "ts_ms", "node")
    for k in required:
        if k not in obj and not any(alt in obj for alt in (SEQ_KEYS if k == "seq"
                                                           else TS_KEYS)):
            raise ProtocolError(f"telemetry missing field {k!r}")
    node = _as_int(obj["node"], "node")
    if not 1 <= node <= 4:
        raise ProtocolError(f"node out of range: {node}")
    level = obj.get("level")
    if level is None:
        level = 1 if node in (1, 2) else 2
    def _pick(keys):
        for k in keys:
            if k in obj:
                return k
        raise ProtocolError(f"telemetry missing any of {keys!r}")

    seq_key = _pick(SEQ_KEYS)
    ts_key = _pick(TS_KEYS)
    source = obj.get("source")
    if not isinstance(source, str) or source not in ("live", "replay"):
        source = "live"          # never a third state: a typo is a live row

    return TelemetryRow(
        chunk_id=_as_int(obj["chunk_id"], "chunk_id"),
        seq=_as_int(obj[seq_key], seq_key),
        ts_ms=_as_int(obj[ts_key], ts_key),
        node=node,
        level=_as_int(level, "level"),
        tilt=_as_float(obj.get("tilt", 0.0), "tilt"),
        disp_mm=_as_float(obj.get("disp_mm", obj.get("disp", 0.0)), "disp_mm"),
        accel=_as_float(obj.get("accel", obj.get("acceleration", 0.0)), "accel"),
        vib=_as_int(obj.get("vib", 0), "vib"),
        # A node with no crack column sends JSON null. That is "not fitted",
        # the same as the 0 the card schema stores, not a corrupt row.
        micro=_as_float(0.0 if obj.get("micro") is None else obj.get("micro"), "micro"),
        macro=_as_float(0.0 if obj.get("macro") is None else obj.get("macro"), "macro"),
        flags=_as_int(obj.get("flags", 0), "flags"),
        source=source,
    )


def parse_line(raw: str) -> Message:
    """Never raises for wire data — a bad line comes back as Unknown so the
    ingest loop can count it instead of dying mid-chunk."""
    line = (raw or "").strip()
    if not line:
        return Unknown(raw, "empty line")

    if line[0] == "{":
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            return Unknown(raw, f"bad JSON: {e.msg}")
        if not isinstance(obj, dict):
            return Unknown(raw, "JSON is not an object")
        # A heartbeat is `{"hb":…}`; a telemetry row carries a chunk id. Everything
        # else on this path is another phase's frame (the ring's own JSON, a
        # `#CMD` echo) and is counted as unknown rather than guessed at.
        if "hb" in obj or ("chunk_id" not in obj and "telemetry" not in obj):
            return Unknown(raw, "not a telemetry row")
        try:
            return parse_telemetry(obj)
        except ProtocolError as e:
            return Unknown(raw, str(e))

    head, _, tail = line.partition(":")
    head = head.strip().upper()
    parts = [p.strip() for p in tail.split(",")] if tail else []

    def ints(n):
        if len(parts) < n:
            raise ProtocolError(f"{head} needs {n} numbers, got {parts!r}")
        return [_as_int(p, head) for p in parts[:n]]

    try:
        if head == "CHUNK_OPEN":
            return ChunkOpen(chunk_id=ints(1)[0])
        if head == "CHUNK_CLOSE":
            cid, rows, dropped = ints(3)
            return ChunkClose(chunk_id=cid, rows=rows, dropped=dropped)
        if head == "CHUNK_STATE":
            cid, rows, dropped = ints(3)
            closed = len(parts) > 3 and parts[3].upper() in ("CLOSED", "1", "DONE")
            return ChunkState(chunk_id=cid, rows=rows, dropped=dropped, closed=closed)
        if head == "CHUNK_DONE":
            cid, rows, dropped = ints(3)
            return ChunkDone(chunk_id=cid, rows=rows, dropped=dropped)
        if head == "CHUNK_FILE":
            cid, size, dropped = ints(3)
            return ChunkFile(chunk_id=cid, bytes=size, dropped=dropped)
        if head == "BLACKBOX":
            state = (parts[0] if parts else "FAULT").upper()
            if state not in ("OK", "NOSD", "FULL", "FAULT"):
                return Unknown(raw, f"unknown blackbox state {state!r}")
            free = None
            if len(parts) > 1 and parts[1]:
                try:
                    free = _as_int(parts[1], "free_mb")
                except ProtocolError:
                    free = None          # free space is telemetry, not a contract
            return BlackboxStatus(state=state, free_mb=free)
        if head == "DROP":
            cid, count = ints(2)
            return DropNotice(chunk_id=cid, count=count)
    except ProtocolError as e:
        return Unknown(raw, str(e))

    return Unknown(raw, f"unrecognised control verb {head!r}")


# ------------------------------------------------------------------- outbound
def format_ack_delete(chunk_id: int) -> str:
    return f"ACK_DELETE:{chunk_id:04d}\n"


def format_replay(first_chunk: int, last_chunk: int) -> str:
    return f"REPLAY:{first_chunk:04d}:{last_chunk:04d}\n"


def format_state_query() -> str:
    return "STATE?\n"


def format_chunk_state(chunk_id: int, rows: int, dropped: int = 0,
                       closed: bool = False) -> str:
    """The hub's half of the handshake — mirrors `main_hub/protocol_out.cpp`."""
    return (f"CHUNK_STATE:{chunk_id:04d},{rows},{dropped}"
            f"{',CLOSED' if closed else ',OPEN'}\n")


def format_chunk_done(chunk_id: int, rows: int, dropped: int = 0) -> str:
    return f"CHUNK_DONE:{chunk_id:04d},{rows},{dropped}\n"


def format_ping() -> str:
    return "PING\n"
