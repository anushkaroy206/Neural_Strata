"""
Neural Strata — the serial reader: bytes → validated frames → state.
====================================================================

This module owns **the single ingestion path**. The USB reader, the replay
reader, the ring relay and the simulator all end up in ``handle_node_frame``. Two
copies of these rules is exactly how a replay stops predicting what the live
system does, and then stops being trusted.

What Phase 1 fixes here
-----------------------
**D5 — measured Δt.** ``calculate_derivatives`` hardcoded ``dt = 0.5``. Velocity
and acceleration are now computed from the **node's own ``ms`` counter**, not from
when the bytes happened to arrive. USB and the relay add variable latency; the
node's clock does not. This is the difference between ``acceleration`` being a
measurement and being a random number — and ``acceleration`` feeds every
threshold in the product.

**D9 — real acceleration.** The MPU-6050 measures acceleration; v1 firmware threw
it away and the server differentiated displacement to invent one. When the frame
carries ``acc``, that value is used directly. Numerical differentiation remains
the fallback for v1 frames and for hardware-free simulation, and is flagged.

**D14 — measured tick.** ``apply_injections`` assumed the ticker ran at exactly
50 ms. It does not: each tick also does DB and buffer work. The caller now passes
the measured duration.

The Δt clamp
------------
``0.05 ≤ Δt ≤ 2.0``. Outside that range the sample is **skipped** and counted in
``derivative_skips`` rather than clamped silently — a 4 s gap is a dropped frame,
and pretending it was 2 s is how a velocity spike becomes a false alarm.
"""

from __future__ import annotations

import json
import threading
import time

from config import NODE_IDS
from contracts import (
    FLAG_HEARTBEAT,
    SCHEMA_VERSION,
    SUPPORTED_VERSIONS,
    coerce_float,
    coerce_int,
    migrate_payload,
    validate_node_frame,
    validate_rfid_frame,
)
from engine import crack as crack_engine

#: The measured-Δt clamp (§1.6). Below this we are reading two samples that are
#: effectively the same instant; above it we are reading a gap, not an interval.
DT_MIN_S = 0.05
DT_MAX_S = 2.0

#: Δt above this marks a discontinuity — the AI window is invalidated rather than
#: stitched across the gap.
DT_GAP_S = 1.0

#: EMA factors. **Do not retune these.** ACC_WARNING = 0.25 m/s² is calibrated
#: against 0.3/0.7 smoothing; changing the smoothing silently moves every
#: threshold in the product.
EMA_PREV = 0.3
EMA_NEW = 0.7

#: Counters for ``/api/metrics`` (Phase 10 surfaces them; they are cheap here).
COUNTS = {
    "frames_ok": 0,
    "frames_rejected": 0,
    "frames_unknown_version": 0,
    "derivative_skips": 0,
    "synthetic_accel_frames": 0,
    "relayed_frames": 0,
    "rfid_frames": 0,
    "heartbeats_ok": 0,
    "heartbeats_ignored": 0,
}


def reset_counts() -> None:
    for k in COUNTS:
        COUNTS[k] = 0


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def node_id_for_index(index: int | str) -> str | None:
    """``1`` → ``"node_1"``. Returns ``None`` for an index outside the topology.

    The single place the node *index* on the wire maps to the node *id* in state.
    Hardcoding ``"node_1" if str(n) == "1" else "node_2"`` — which is what v4.4
    did — is how a frame from N3 silently became N2.
    """
    try:
        idx = int(index)
    except (TypeError, ValueError):
        return None
    if 1 <= idx <= len(NODE_IDS):
        return NODE_IDS[idx - 1]
    return None


def parse_frame(line: str) -> dict | None:
    """One line → one frame dict, or ``None``.

    Never raises. An unparseable line is a line we ignore; it is not a reason to
    drop the reader thread. Version upconversion happens here so every caller
    downstream sees a current-schema frame.
    """
    if not line:
        return None
    line = line.strip()
    if not line or line[0] != "{":
        # Framed/blackbox protocol lines are consumed by their own pipeline
        # before they reach here; anything else non-JSON is noise.
        return None
    try:
        frame = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(frame, dict):
        return None

    v = coerce_int(frame.get("v"), 1)
    if v not in SUPPORTED_VERSIONS:
        COUNTS["frames_unknown_version"] += 1
        return None
    if v < SCHEMA_VERSION:
        frame = migrate_payload(frame, from_v=v)
    return frame


# ---------------------------------------------------------------------------
# The derivative engine (D5)
# ---------------------------------------------------------------------------

def sample_seconds(frame: dict, arrival_t: float, first_arrival_t: float) -> float:
    """The sample's own clock, in seconds.

    A v2 frame carries ``ms`` (node uptime). A v1 frame does not, so we
    synthesise one from arrival time — the best available, and flagged as
    synthetic by ``migrate_payload`` so nothing downstream mistakes it for a
    measurement.
    """
    ms = frame.get("ms")
    if ms is not None:
        try:
            return float(ms) / 1000.0
        except (TypeError, ValueError):
            pass
    return max(0.0, arrival_t - first_arrival_t)


def compute_derivatives(node, current_disp: float, dt: float | None) -> tuple[float, float]:
    """Velocity and acceleration from displacement, using **measured** Δt (D5).

    ``dt`` is the frame-to-frame interval in seconds. ``None`` means "no previous
    sample" (first frame after boot or after a discontinuity) — we update the
    displacement but do not invent a velocity from it.

    Returns ``(acceleration, velocity)`` after EMA smoothing, or the unchanged
    pair when the sample was skipped.
    """
    prev_disp = float(node.displacement_mm)
    prev_v = float(node.velocity)

    if dt is None:
        node.displacement_mm = float(current_disp)
        return float(node.acceleration), float(node.velocity)

    if not (DT_MIN_S <= dt <= DT_MAX_S):
        # A duplicate timestamp or a gap. Skipping is the honest answer; counting
        # it is how you find out the link is dropping frames.
        COUNTS["derivative_skips"] += 1
        return float(node.acceleration), float(node.velocity)

    v = (float(current_disp) - prev_disp) / dt
    a = (v - prev_v) / dt

    node.velocity = (prev_v * EMA_PREV) + (v * EMA_NEW)
    node.acceleration = (float(node.acceleration) * EMA_PREV) + (a * EMA_NEW)
    node.displacement_mm = float(current_disp)

    return float(node.acceleration), float(node.velocity)


def use_measured_accel(node, acc: float, dt: float | None) -> float:
    """Use the MPU-6050's own acceleration reading (D9).

    The smoothing is applied to the **measured** signal, not to a differentiated
    position, so the EMA has the same time constant and the existing thresholds
    keep their calibration.
    """
    node.acceleration = (float(node.acceleration) * EMA_PREV) + (float(acc) * EMA_NEW)
    if dt is not None:
        node.velocity = max(-40.0, min(40.0, float(node.velocity) + node.acceleration * dt))
    return float(node.acceleration)


# ---------------------------------------------------------------------------
# Handlers — the one path into state
# ---------------------------------------------------------------------------

def handle_node_frame(state, frame: dict, via: str | None = None) -> bool:
    """Apply one node frame to ``state``. Returns ``True`` if it landed.

    Order is load-bearing:

    1. validate — a rejected frame must not touch state
    2. resolve the node index → id (never assume, see ``node_id_for_index``)
    3. **if the scenario console owns this node, drop the frame** — the operator's
       injection must not fight the bench feed
    4. measured Δt → velocity/acceleration (measured ``acc`` preferred, D9)
    5. status cascade, model buffer, health
    """
    ok, _why = validate_node_frame(frame)
    if not ok:
        COUNTS["frames_rejected"] += 1
        return False

    n_id = node_id_for_index(frame.get("node"))
    if n_id is None:
        COUNTS["frames_rejected"] += 1
        return False
    if n_id not in state.nodes:
        # A real node the registry does not carry. Reject loudly rather than
        # silently writing to a neighbour's entry.
        COUNTS["frames_rejected"] += 1
        return False

    if state.injection_holds(n_id):
        return False

    node = state.node(n_id)
    now = time.time()
    # #region agent log
    if n_id == "node_3":
        try:
            import json as _json
            handle_node_frame._n3 = getattr(handle_node_frame, "_n3", 0) + 1
            if handle_node_frame._n3 <= 50:
                with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                    _f.write(_json.dumps({
                        "sessionId": "83ae1d", "hypothesisId": "N3",
                        "location": "serial_reader.py:handle_node_frame",
                        "message": "node 3 frame",
                        "data": {
                            "n": handle_node_frame._n3,
                            "hb": bool(coerce_int(frame.get("flags"), 0) & FLAG_HEARTBEAT),
                            "raw_acc": frame.get("acc"),
                            "stored": round(float(node.acceleration), 3),
                            "disp": frame.get("disp"),
                            "ms": frame.get("ms"),
                        },
                        "timestamp": int(now * 1000),
                    }) + "\n")
        except Exception:
            pass
    # #endregion

    if coerce_int(frame.get("flags"), 0) & FLAG_HEARTBEAT:
        # §2.5 / Phase 2 — a heartbeat proves the node is alive and says nothing
        # else. It is handled **before** the sample clock is touched, and that
        # ordering is the whole correctness argument:
        #
        # * it must not call ``measured_dt``, which advances ``_last_t`` — a
        #   heartbeat in the middle of a real sampling run would otherwise be
        #   treated as the previous *sample* and hand the next real frame a Δt of
        #   ~2 s, turning a normal interval into a phantom velocity spike;
        # * it must not enter the model window — the payload is a repeat of the
        #   last reading, and 121 repeated values through the TTF regressor is a
        #   flat line, which reads as *perfectly stable ground*;
        # * it must not clear a pending discontinuity. If the samples stop for
        #   6 s while heartbeats keep coming, the node is alive but its window is
        #   broken, and the next real frame's gap check must still fire.
        #
        # What it *does* do is everything liveness needs: refresh the arrival
        # clock the watchdog reads, and carry the fresh radio facts (RSSI,
        # battery) that only a live radio can know.
        COUNTS["heartbeats_ok"] += 1
        node.rssi = coerce_int(frame.get("rssi"), node.rssi)
        node.batt = coerce_float(frame.get("batt"), node.batt)
        node.flags = coerce_int(frame.get("flags"), 0)
        # The node reads the accelerometer again for this packet. While sample
        # frames are getting through, the node does not send a heartbeat, so
        # this only runs when the live number would otherwise stay frozen.
        # It does not enter the model window and does not move the sample clock.
        acc_hb = frame.get("acc")
        if acc_hb is not None:
            before = float(node.acceleration)
            use_measured_accel(node, coerce_float(acc_hb, before), None)
            # #region agent log
            if n_id == "node_3" and abs(before - float(node.acceleration)) > 0.2:
                try:
                    import json as _json
                    with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                        _f.write(_json.dumps({
                            "sessionId": "83ae1d", "hypothesisId": "N3",
                            "runId": "post-fix",
                            "location": "serial_reader.py:heartbeat",
                            "message": "heartbeat moved node 3 acceleration",
                            "data": {"raw_acc": acc_hb,
                                     "before": round(before, 3),
                                     "after": round(float(node.acceleration), 3)},
                            "timestamp": int(now * 1000),
                        }) + "\n")
                except Exception:
                    pass
            # #endregion
        node.last_seen_s = 0.0
        node.stale = False
        state.note_arrival(n_id, now)
        return True

    ms = frame.get("ms")
    t_sample = sample_seconds(frame, now, state.first_arrival(n_id))
    dt = state.measured_dt(n_id, ms, t_sample)

    addr_flag = 0
    if via:
        COUNTS["relayed_frames"] += 1
    if via is None and state.via_of(n_id):
        # A direct frame supersedes a relayed one; the link healed.
        state.note_via(n_id, None)
    elif via is not None:
        state.note_via(n_id, via)

    disp = coerce_float(frame.get("disp"), float(node.displacement_mm))
    tilt = coerce_float(frame.get("tilt"), float(node.tilt_x))

    acc_in = frame.get("acc")
    if acc_in is None:
        compute_derivatives(node, disp, dt)
        COUNTS["synthetic_accel_frames"] += 1
        addr_flag |= 0
    else:
        node.displacement_mm = disp
        use_measured_accel(node, coerce_float(acc_in, 0.0), dt)

    node.tilt_x = tilt
    node.vib = coerce_int(frame.get("vib"), 0)
    node.micro = frame.get("micro", None)
    node.macro = frame.get("macro", None)
    node.rssi = coerce_int(frame.get("rssi"), node.rssi)
    node.batt = coerce_float(frame.get("batt"), node.batt)
    node.flags = coerce_int(frame.get("flags"), 0) | addr_flag
    node.timestamp = t_sample
    node.last_seen_s = 0.0
    node.stale = False

    if dt is not None and dt > DT_GAP_S:
        # A dropped frame or a stalled link: the window is not continuous, so the
        # model must not treat it as one.
        state.mark_discontinuity(n_id, "gap")

    COUNTS["frames_ok"] += 1
    state.note_arrival(n_id, now)

    state.update_peak(n_id)

    # Phase 3 — the crack column folds in **before** the status cascade, so an
    # initiation raised by this very frame can influence this very tick's status
    # (§3.5). It also writes the baseline-subtracted `micro` back onto the node,
    # so the model buffer below, the database row and the dashboard all carry the
    # number the engine reasoned about rather than the raw one.
    for event in crack_engine.update_node_crack(state, n_id, node, t_sample):
        event.publish(state)

    # **Phase 6 (§6.2.6) — D6 closed, right here.**
    #
    # This line used to read ``state.update_status_logic()``, which meant the
    # full status pass — per-node status, per-level status, both route keys, the
    # TTF chain and (from Phase 6) the 1 Hz stage machine — ran on *every frame*,
    # from the serial reader's thread, while the ticker ran it again 0–50 ms
    # later on its own thread. Two threads deriving the same verdict from the
    # same state is how you get duplicate event-log edges and WARNING timestamps
    # that belong to neither thread.
    #
    # The reader's job is the frame: parse it, fold it into the node, derive the
    # derivatives, run the crack engine so an initiation raised by *this* frame
    # can matter, and put the sample in the model window. The status pass belongs
    # to the ticker alone — one writer, one cadence, one verdict. The cost is at
    # most one ticker period (50 ms) of latency on the rig, which is below every
    # threshold in this system by two orders of magnitude.
    state.append_model_buffer(n_id, t_sample, disp, tilt, float(node.acceleration),
                              node.vib, node.micro, node.macro)
    return True


def handle_rfid_frame(state, frame: dict) -> tuple[str, str] | None:
    """Apply one RFID scan. Returns ``(uid, zone)`` for the DB, or ``None``.

    Zone assignment reads the level machinery (§1.2 target 5): a level whose
    route is blocked sends its readers to the evacuation zone. The zone table
    lives in ``state.py`` with the rest of the topology.
    """
    ok, _why = validate_rfid_frame(frame)
    if not ok:
        COUNTS["frames_rejected"] += 1
        # #region agent log
        try:
            import json as _json, time as _time
            with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a") as _f:
                _f.write(_json.dumps({
                    "sessionId": "83ae1d", "hypothesisId": "C",
                    "location": "serial_reader.py:handle_rfid_frame",
                    "message": "rfid frame rejected",
                    "data": {"why": _why, "reader": frame.get("reader"), "uid": frame.get("uid")},
                    "timestamp": int(_time.time() * 1000),
                }) + "\n")
        except Exception:
            pass
        # #endregion
        return None
    uid = str(frame.get("uid") or "").strip()
    if not uid:
        return None
    COUNTS["rfid_frames"] += 1
    reader = frame.get("reader") or frame.get("reader_id")
    if reader is None and frame.get("level") in (1, 2):
        reader = "L%d" % frame["level"]
    return state.note_rfid(uid, reader)


def serial_backoff(attempt: int) -> float:
    """Reconnect delay for the *attempt*-th consecutive failure.

    §2.2 fixes the ladder at **0.5 → 1 → 2 → 5 s**, and this is that ladder
    written out rather than computed. A doubling formula clamped at the cap would
    produce 0.5, 1, 2, **4**, 5 — a fourth rung the plan does not name. Being
    literally faithful here costs one tuple and removes the question.

    The last rung repeats forever: five seconds is the ceiling, not a schedule.
    A pure function, so a test can assert the whole ladder without waiting ten
    seconds for it to happen.
    """
    from config import SERIAL_BACKOFF_CAP_S, SERIAL_BACKOFF_START_S

    ladder = (SERIAL_BACKOFF_START_S,
              SERIAL_BACKOFF_START_S * 2,
              SERIAL_BACKOFF_START_S * 4,
              SERIAL_BACKOFF_CAP_S)
    return ladder[min(max(int(attempt), 0), len(ladder) - 1)]


def read_serial_forever(state, on_line, port=None, baud=None, blackbox=None,
                        on_status=None, stop_event=None, max_attempts=None) -> None:
    """Read framed lines off the hub's USB port **until the process stops**.

    This is the loop, not the parsing: it belongs in ``ingestion/serial_reader``
    because the module's job is "USB serial → validated frames → state", and the
    loop is the "USB serial" part. ``on_line`` is ``app.ingest_line`` — the single
    ingestion path — so this function has no opinion about what a line means.

    The 50 ms cadence and the ``in_waiting`` poll are the v4.4 behaviour, kept
    deliberately: it is proven on the bench, and a select()-based rewrite is not
    worth the risk on the one code path that has to work during a demo.

    **Phase 2 — the outer reconnect loop (D8).** Phase 1 connected once; on any
    exception it printed and *returned*, so a single dropped cable left the
    process permanently blind for the rest of its life while the dashboard
    carried on showing plausible numbers. The body is now wrapped in a retry loop
    with the §2.2 backoff, and the port is always closed before it is reopened —
    the plan lists "the reconnect loop fights the OS for the port" as this
    phase's own risk, and closing is the whole mitigation.

    This function is the **only** thing that opens the port. It sets
    ``state.serial_up`` and ``state.hardware_source``; it announces nothing,
    because ``engine.health.health_tick`` owns every serial transition and two
    announcers would produce two events for one cable. The boot-time failure
    message is the exception: it fires before the first tick and no edge exists
    to detect yet.

    ``pyserial`` is imported here rather than at module scope so that importing
    this module does not require a serial stack — the tests import it freely.
    """
    global _SERIAL_PORT, _BAUD_RATE
    import serial as _serial

    from config import BAUD_RATE, SERIAL_PORT
    port = port or SERIAL_PORT
    baud = baud or BAUD_RATE
    _SERIAL_PORT, _BAUD_RATE = port, baud

    attempt = 0
    connected_once = False

    while not (stop_event is not None and stop_event.is_set()):
        try:
            ser = _serial.Serial(port, baud, timeout=1)
        except Exception as e:
            state.serial_up = False
            if not connected_once and attempt == 0:
                print(f"Failed to connect to Serial ({port}): {e}")
                if on_status:
                    on_status("WARNING",
                              "Hardware offline. Serial connection failed — API still live.")
            attempt += 1
            if max_attempts is not None and attempt >= max_attempts:
                return
            delay = serial_backoff(attempt)
            if stop_event is not None:
                # Wake immediately on shutdown instead of sitting in a 5 s sleep.
                stop_event.wait(delay)
            else:
                time.sleep(delay)
            continue

        print(f"Connected to ESP32 Hub on {port}")
        state.hardware_source = "usb"
        state.serial_up = True
        attempt = 0
        def _hub_write(payload) -> None:
            # pyserial rejects str. The router hands a `#CMD …` line; the
            # blackbox already passes bytes that end in a newline.
            if isinstance(payload, str):
                payload = payload.encode("utf-8")
                if not payload.endswith(b"\n"):
                    payload += b"\n"
            ser.write(payload)

        if blackbox is not None and hasattr(blackbox, "attach_writer"):
            # we are the only reader of this port; the gateway gets the write side
            blackbox.attach_writer(_hub_write)
        # PHASE 8 — the down-link the router has been waiting for since Phase 7.
        # One reader, one writer, one port: the router's hub commands (`#CMD …`)
        # and the gateway's blackbox frames share exactly this writer, which
        # is why the attach lives beside the gateway's. Until this line ran,
        # `push_hub_command()` returned False on a rig and the local alert channel
        # had nowhere to go — the defect Phase 7 recorded as still open.
        try:
            from routing import dijkstra as routing
            routing.attach_downlink(_hub_write)
            print("[routing] down-link attached to the hub serial writer")
        except Exception as exc:                                # noqa: BLE001
            print(f"[routing] down-link attach failed: {type(exc).__name__}: {exc}")
        if not connected_once:
            connected_once = True
            if on_status:
                on_status("NOMINAL", "Hardware serial link established.")

        try:
            last_line = time.time()
            while not (stop_event is not None and stop_event.is_set()):
                got = False
                # Empty the buffer before sleeping. Reading one line and then
                # waiting 50 ms lets the hub's USB buffer fill, and the hub
                # then blocks inside its own write and stops sending.
                while ser.in_waiting > 0:
                    line = ser.readline().decode("utf-8", errors="replace").strip()
                    if line:
                        on_line(line)
                        last_line = time.time()
                        got = True
                silent = time.time() - last_line
                if not got and silent > 8.0:
                    # #region agent log
                    try:
                        import json as _json
                        with open("/Users/mohneeshpurohit/Documents/GitHub/Neural_Strata/.cursor/debug-83ae1d.log", "a", encoding="utf-8") as _f:
                            _f.write(_json.dumps({
                                "sessionId": "83ae1d", "hypothesisId": "TEL",
                                "runId": "post-fix",
                                "location": "serial_reader.py:read_serial_forever",
                                "message": "hub silent, reopening the serial port",
                                "data": {"silent_s": round(silent, 1)},
                                "timestamp": int(time.time() * 1000),
                            }) + "\n")
                    except Exception:
                        pass
                    # #endregion
                    raise TimeoutError(f"hub silent for {silent:.0f}s")
                if not got:
                    time.sleep(0.02)
        except Exception as e:
            # A read error is a *link* error, not a process error. Health
            # announces the loss on the next tick; here we only tear down and
            # loop round to reopen.
            print(f"Read error: {e}")
        finally:
            state.serial_up = False
            try:
                ser.close()
            except Exception:
                pass


class SerialWatchdog:
    """Supervises the reader thread. **Never opens the port itself.**

    §2.2 asks for "a ``serial_watchdog`` thread [that] reopens the port". It is
    implemented here as a *supervisor*, and the distinction is deliberate: two
    threads that both believe they own one serial port is exactly the failure the
    plan's own risk table warns about ("Reconnect loop fights the OS for the
    port"). So the reconnect-and-backoff ladder lives inside
    ``read_serial_forever`` — one owner, guaranteed — and this thread has
    exactly one job: notice that the reader thread **died** and start it again.

    Those are different faults and they need different handlers. A dropped cable
    is handled in the loop without a thread ever restarting. A crash in the loop
    (a bug in a parser, an ``OSError`` from a yanked USB adapter that escapes the
    inner catch) leaves nothing running at all, and no amount of backoff inside a
    dead thread will fix that.

    ``check()`` is one pass and returns a decision string, so the policy is
    unit-testable without a thread, a port or a ten-second wait.
    """

    def __init__(self, state, restart, is_alive, stop_event=None, poll_s: float = 1.0,
                 on_status=None, max_restarts: int | None = None):
        self.state = state
        self.restart = restart
        self.is_alive = is_alive
        self.stop_event = stop_event
        self.poll_s = poll_s
        self.on_status = on_status
        self.max_restarts = max_restarts
        self.restarts = 0
        self.deaths = 0

    def check(self) -> str:
        """One supervision pass → ``"stopped"`` / ``"ok"`` / ``"restart"`` / ``"giveup"``."""
        if self.stop_event is not None and self.stop_event.is_set():
            return "stopped"
        if self.is_alive():
            return "ok"
        self.deaths += 1
        if self.max_restarts is not None and self.restarts >= self.max_restarts:
            return "giveup"
        self.restarts += 1
        return "restart"

    def run(self) -> None:
        """Thread body. Polls ``check()`` forever."""
        while True:
            decision = self.check()
            if decision == "stopped":
                return
            if decision == "restart":
                self.state.serial_up = False
                if self.on_status:
                    self.on_status(
                        "WARNING",
                        "SERIAL WATCHDOG — reader thread exited; restarting it.",
                        kind="INFRASTRUCTURE")
                try:
                    self.restart()
                except Exception as e:              # pragma: no cover - defensive
                    print(f"Serial watchdog: restart failed ({e})")
            elif decision == "giveup":
                if self.on_status:
                    self.on_status(
                        "CRITICAL",
                        f"SERIAL WATCHDOG — reader died {self.deaths}× and hit the "
                        f"restart ceiling; telemetry is dark until an operator acts.",
                        kind="INFRASTRUCTURE")
                return
            time.sleep(self.poll_s)


def start_serial_watchdog(state, target, args=(), kwargs=None, on_status=None,
                          restart_ceiling: int | None = None) -> tuple:
    """Start the reader thread and its supervisor. Returns ``(thread, watchdog)``.

    The reader is started here rather than by the caller so the pair is created
    together — a watchdog with nothing to watch is a thread that restarts a
    function that was never running.
    """
    stop_event = kwargs.pop("stop_event", None) if kwargs else None
    if kwargs is None:
        kwargs = {}
    if stop_event is not None:
        kwargs["stop_event"] = stop_event

    holder: dict = {}

    def _run():
        target(*args, **kwargs)

    def _alive() -> bool:
        return holder["t"].is_alive()

    t = threading.Thread(target=_run, daemon=True)
    holder["t"] = t
    wd = SerialWatchdog(state, restart=lambda: _restart(holder, _run),
                        is_alive=_alive, stop_event=stop_event, on_status=on_status,
                        max_restarts=restart_ceiling)
    t.start()
    threading.Thread(target=wd.run, daemon=True).start()
    return t, wd


def _restart(holder, run) -> None:
    """Start a fresh reader thread and point the watchdog's liveness probe at it."""
    t = threading.Thread(target=run, daemon=True)
    holder["t"] = t
    t.start()


_SERIAL_PORT = None
_BAUD_RATE = None

__all__ = [
    "read_serial_forever", "serial_backoff",
    "SerialWatchdog", "start_serial_watchdog",
    "parse_frame", "node_id_for_index", "sample_seconds",
    "compute_derivatives", "use_measured_accel",
    "handle_node_frame", "handle_rfid_frame",
    "DT_MIN_S", "DT_MAX_S", "DT_GAP_S", "EMA_PREV", "EMA_NEW",
    "COUNTS", "reset_counts",
]
