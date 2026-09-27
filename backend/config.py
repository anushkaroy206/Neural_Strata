"""
Neural Strata — configuration surface.
=====================================

Single source of truth for every tunable in the backend. Nothing else in the
package is allowed to hardcode one of these values.

Phase 0 · file moved *into existence* from the module-level literal block at the
top of `app.py` (§0.5). The values are unchanged; only their home is new.

Environment
-----------
Every constant is overridable at boot with an ``NS_``-prefixed variable, and a
``.env`` file beside the project root is read first (without clobbering real
environment variables). Tuning a threshold for a demo is therefore an edit to
``.env``, never an edit to production code at 1 a.m.

    NS_ACC_CRITICAL=3.5 python3 backend/app.py --no-serial

Boot safety
-----------
``validate_config()`` returns the list of *fatal* problems (nonsense thresholds,
a node that lives on two levels, a port outside 1–65535). ``app.py`` calls it
before Flask starts and exits non-zero if the list is non-empty. A missing model
or a missing map is *not* fatal — the server degrades instead of dying
(``boot_policy: ANY_MISSING_ARTIFACT => LOG + DEGRADE, NEVER CRASH``).
"""

from __future__ import annotations

import os
from pathlib import Path

#: The node registry — **imported, never re-typed.** `contracts` is the
#: dependency-free leaf, so it is the one module allowed to write the literal:
#: `validate_node_frame` needs the node enum to reject a frame from a node that
#: does not exist, and contracts cannot import config to find out.
#:
#: Phase 0 declared this tuple in *both* files and added a test asserting they
#: agreed. Two copies is the D10 pattern one level up — and a test that catches a
#: divergence after the fact is strictly worse than not having the divergence at
#: all. The identity is asserted in `tests/test_node_registry.py`.
from contracts import NODE_IDS, CRACK_NODE_IDS                        # noqa: E402


# ---------------------------------------------------------------------------
# 0. .env loading — moved verbatim from app.py::_load_dotenv
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent          # …/neural-strata/backend
PROJECT_ROOT = ROOT.parent                      # …/neural-strata


def load_dotenv(path: Path | None = None) -> None:
    """Read ``.env`` into ``os.environ`` without clobbering existing keys.

    Moved verbatim from ``app.py::_load_dotenv`` so the parsing rules (comments,
    blank lines, stripped quotes, never overwrite) stay identical.
    """
    env_path = Path(path) if path else (PROJECT_ROOT / ".env")
    if not env_path.exists():
        return
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = val


load_dotenv()


# ---------------------------------------------------------------------------
# 1. Typed environment access
# ---------------------------------------------------------------------------

_TRUTHY = {"1", "true", "yes", "on", "y", "t"}


def env_str(key: str, default: str) -> str:
    """String env access with default. Empty string is treated as unset."""
    val = os.environ.get(key)
    return default if val is None or val == "" else val


def env_int(key: str, default: int) -> int:
    try:
        return int(str(env_str(key, str(default))).strip())
    except (TypeError, ValueError):
        return int(default)


def env_float(key: str, default: float) -> float:
    try:
        return float(str(env_str(key, str(default))).strip())
    except (TypeError, ValueError):
        return float(default)


def env_bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None or val == "":
        return bool(default)
    return val.strip().lower() in _TRUTHY


# ---------------------------------------------------------------------------
# 2. Database — PostgreSQL on the edge laptop is the local source of truth
# ---------------------------------------------------------------------------

DB_HOST = env_str("NS_DB_HOST", "localhost")
DB_NAME = env_str("NS_DB_NAME", "neural_strata")
DB_USER = env_str("NS_DB_USER", "postgres")
DB_PASS = env_str("NS_DB_PASS", "")
DB_PORT = env_int("NS_DB_PORT", 5432)
DB_POOL_MIN = env_int("NS_DB_POOL_MIN", 1)
DB_POOL_MAX = env_int("NS_DB_POOL_MAX", 4)
#: How long a caller waits for a free pooled connection before giving up. The
#: pool is deliberately small (Phase 0: stop opening a socket per packet), which
#: makes exhaustion a *scheduling* problem rather than a fault — and the old
#: behaviour turned it into silent data loss, because `try_get_conn()` returned
#: None and every caller treats None as "no database configured" and drops the
#: write. Found in Phase 9: six sync tests skipped themselves as "database
#: unreachable" while a perfectly healthy server held the pool. A bounded wait
#: costs a stressed box one second and a quiet one nothing.
DB_POOL_WAIT_S = env_float("NS_DB_POOL_WAIT_S", 1.0)
DB_CONNECT_TIMEOUT_S = env_int("NS_DB_CONNECT_TIMEOUT_S", 3)


# ---------------------------------------------------------------------------
# 3. Serial link to the Main Hub
# ---------------------------------------------------------------------------

SERIAL_PORT = env_str("NS_SERIAL_PORT", "/dev/cu.usbserial-0001")
BAUD_RATE = env_int("NS_BAUD_RATE", 115200)
SERIAL_RECONNECT_S = env_float("NS_SERIAL_RECONNECT_S", 2.0)
SERIAL_TIMEOUT_S = env_float("NS_SERIAL_TIMEOUT_S", 0.5)


# ---------------------------------------------------------------------------
# 4. Topology — 4 sensor nodes · 2 level hubs · 1 main hub (7 boards)
# ---------------------------------------------------------------------------

#: Re-exported above from ``contracts`` — the name every other module uses.

#: Level 1 carries N1/N2 (J1, J2), level 2 carries N3/N4 (J3, J4).
LEVELS: dict[int, tuple[str, ...]] = {
    1: ("node_1", "node_2"),
    2: ("node_3", "node_4"),
}

#: Exits reachable from each level. Level 2 must reach EXIT2 (east), and only
#: EXIT2 — that is why "exit 2 far east" is a hard build rule and not a
#: preference. Cross-checked against ``assets/tunnel_map.json`` by
#: ``tools/check_contracts.py`` so config and the map cannot drift apart.
LEVEL_EXITS: dict[int, tuple[str, ...]] = {
    1: ("EXIT1", "EXIT2"),
    2: ("EXIT2",),
}

#: Passive junctions on each level. Junctions have **no MCU** — the level hub
#: drives their strips. This table is the only place that assignment is written.
LEVEL_JUNCTIONS: dict[int, tuple[str, ...]] = {
    1: ("J1", "J2"),
    2: ("J3", "J4"),
}

#: Nodes the live pipeline runs. **Phase 1 (B1) flipped this to all four** — the
#: one-line change that D10 was about. The v4.4 server, the dashboard contract and
#: the model buffers were all two-node; they are four-node from here.
#:
#: Still env-overridable, because the bench runs partial builds constantly (N3/N4
#: were unplugged for most of the bench work): ``NS_ACTIVE_NODES=node_1,node_2``
#: reproduces the Phase 0 pipeline exactly, which is how the Phase 0 acceptance
#: recording stays replayable.
ACTIVE_NODES: tuple[str, ...] = tuple(
    n.strip() for n in env_str("NS_ACTIVE_NODES", ",".join(NODE_IDS)).split(",") if n.strip()
)


# ---------------------------------------------------------------------------
# 5. Cadence & buffers
# ---------------------------------------------------------------------------

TICK_MS = env_int("NS_TICK_MS", 50)                       # sim/state ticker
HISTORY_INTERVAL_S = env_float("NS_HISTORY_INTERVAL_S", 0.5)
HISTORY_MAX = env_int("NS_HISTORY_MAX", 240)              # 120 s of history
MODEL_BUF = env_int("NS_MODEL_BUF", 121)                  # 60 s at 2 Hz, +1
#: The cadence the model was TRAINED at. The live buffers are decimated to this
#: from the frame stream, keyed on each sample's own clock — not on wall-clock
#: ticks. That is what lets a 10x replay fill a 60 s window in 6 s of real time
#: and reach ``/api/ai`` ACTIVE (Phase 0 left that gap open).
MODEL_HZ = env_float("NS_MODEL_HZ", 2.0)
MODEL_DT = 1.0 / MODEL_HZ
#: A node that has not delivered a sample for this long is STALE — the node card
#: says so instead of showing its last value as if it were current. The node
#: sends at 2 Hz and the hub heartbeats at 2 s, so 5 s is two missed heartbeats:
#: long enough not to flap, short enough to notice during a demo.
#:
#: **Phase 2 renames this to ``STALE_AFTER_S``** (Backend_Build_Phases §2.8,
#: which documents the demo-day knob as ``NS_STALE_AFTER_S``). One value, one
#: name — but the Phase-1 name is still exported below, because
#: ``engine/health.py`` and every Phase-1 test import it. The env reader accepts
#: both spellings, new first; ``NS_NODE_STALE_S`` keeps an old ``.env`` working
#: rather than silently reverting a tuning to 5.0.
STALE_AFTER_S = env_float("NS_STALE_AFTER_S", env_float("NS_NODE_STALE_S", 5.0))

#: Phase-1 spelling. Not a second source of truth — an alias, asserted equal in
#: ``tests/test_health.py::test_stale_alias``.
NODE_STALE_S = STALE_AFTER_S

#: The ring's heartbeat contract, moved here from ``ingestion/ring.py`` in
#: Phase 2. ``config``'s own rule is that this file is the single source of every
#: tunable and nothing else hardcodes one — ``ring.py`` was the last violator, and
#: the phase that adds ``/api/health`` is the wrong phase to have two opinions
#: about when a link is dead. ``LINK_DEAD_S`` is **derived, never typed**: 2.0 s ×
#: 3 misses = 6.0 s. One knob, so the product cannot drift from its factors.
RING_HEARTBEAT_S = env_float("NS_RING_HEARTBEAT_S", 2.0)
RING_HEARTBEAT_MISSES = env_int("NS_RING_HEARTBEAT_MISSES", 3)
LINK_DEAD_S = RING_HEARTBEAT_S * RING_HEARTBEAT_MISSES

#: Serial reconnect backoff — first delay, multiplier, ceiling. §2.2 fixes the
#: ladder at 0.5 → 1 → 2 → 5 s. A cap, not a schedule: after five failures the
#: reader keeps retrying at the cap and keeps saying so.
SERIAL_BACKOFF_START_S = env_float("NS_SERIAL_BACKOFF_START_S", 0.5)
SERIAL_BACKOFF_CAP_S = env_float("NS_SERIAL_BACKOFF_CAP_S", 5.0)
MODEL_FRESH_S = env_float("NS_MODEL_FRESH_S", 5.0)        # pill falls back after this
INFER_HZ = env_float("NS_INFER_HZ", 1.0)


# ---------------------------------------------------------------------------
# 6. Thresholds — the physics (owner of the value in brackets)
# ---------------------------------------------------------------------------

# The alert table on the dashboard is the only trigger:
#   NOMINAL   a ≤ 0.25
#   WARNING   a > 0.25 held for 0.4 s   (creep: red lamp steady, three beeps)
#   CRITICAL  a ≥ 3.0 immediately        (failure: red lamp blinking, siren)
ACC_CRITICAL = env_float("NS_ACC_CRITICAL", 3.0)          # m/s²
ACC_WARNING = env_float("NS_ACC_WARNING", 0.25)           # m/s²
ACC_WARN_HOLD_S = env_float("NS_ACC_WARN_HOLD_S", 0.4)    # seconds, warning only
# Model 1's excursion feature was fitted at 0.25 m/s². The siren lines above
# are a bench setting for the MPU-6050. The feature must keep the trained
# cut, or a quiet creep the forest was taught to see disappears.
EXCURSION_ACC = env_float("NS_EXCURSION_ACC", 0.25)       # m/s², pattern feature only
# Prototype only: after Re-arm, incoming samples are shown but do not raise
# status, alerts or route changes until this many seconds have passed.
REARM_GRACE_S = env_float("NS_REARM_GRACE_S", 30.0)
VEL_WARNING = env_float("NS_VEL_WARNING", 0.03)           # mm/s
FUK_V_MIN = env_float("NS_FUK_V_MIN", 1.0)                # mm/s — 1/v fit floor (B11)

# ---------------------------------------------------------------------------
# 6a. The crack column — B2 (Phase 3), and the unit decision behind it
# ---------------------------------------------------------------------------
#
# **`micro` is crack-mouth opening in µm, not micro-strain in µε.**
#
# The plan was written for a BF350 foil gauge bonded to the rock. The build uses
# a **YZC-131 load cell in tension across the seam**, and a load cell measures
# *force*, not strain. Calling that number µε would be a unit that no instrument
# on the rig produces — the worst kind of documentation, because it is plausible
# and wrong. So the channel is redefined honestly:
#
#     micro (µm) = crack-mouth opening displacement, from the load cell
#                  through the spring, calibrated against a feeler gauge.
#
# One physical quantity, two instruments, two resolutions — and that is the whole
# two-stage story:
#
#     micro  resolution ≈ 1 µm    →  crosses its threshold after ~1.5 µm
#     macro  resolution ≈ 24 µm   →  crosses its threshold after 1.5 mm = 1500 µm
#
# Same event, same opening, the fine channel fires **first** because it is finer
# by ~1000×. No invented lead time is needed; it falls out of the sensors.
MICRO_RATE_THRESHOLD = env_float("NS_MICRO_RATE_THRESHOLD", 1.5)   # µm/s  (B2)
#: The plan's own number was **15 µε/s**. Strain × gauge length = elongation, so
#: the same physics at a 100 mm crack-mouth gauge is ``15e-6 × 100 mm = 1.5 µm``,
#: i.e. 1.5 µm/s. The threshold is the plan's; it is now expressed in the unit the
#: sensor actually measures. If the bench disagrees, the *window* moves before the
#: threshold does (§3.8: "the threshold is physics, the window is noise").
MACRO_JUMP_THRESHOLD = env_float("NS_MACRO_JUMP_THRESHOLD", 1.5)    # mm     (B2)
MICRO_SETTLE_S = env_float("NS_MICRO_SETTLE_S", 15.0)               # s      (B2)

#: Rate window and the median filter in front of it. §3.2.2: 5 s window, 3-point
#: median — because an HX711 stack at 10 SPS is noisy and a raw difference of two
#: samples is a noise measurement, not a rate.
MICRO_RATE_WINDOW_S = env_float("NS_MICRO_RATE_WINDOW_S", 5.0)      # s
MICRO_MEDIAN_N = env_int("NS_MICRO_MEDIAN_N", 3)

#: Hysteresis, §3.2.3. A crossing must persist for this many consecutive samples
#: before it raises, and must fall back to this fraction of the threshold before
#: it clears. 3 samples at the 2 Hz sample cadence = 1.5 s of agreement — long
#: enough to reject a single noisy read, short enough to catch initiation.
CRACK_CONFIRM_N = env_int("NS_CRACK_CONFIRM_N", 3)                  # samples
CRACK_CLEAR_FRACTION = env_float("NS_CRACK_CLEAR_FRACTION", 0.70)   # ← 70 % rule

#: Whole-column bounds.
#:
#: ``MICRO_ABSENT_UM`` is what the *simulator* and a hot-swap use to mean "no
#: instrument"; the frame carries NaN for that, never 0. The plausibility window
#: is deliberately wide (−2 mm…+100 mm of opening): its job is to catch a unit
#: error — a µε value read as µm is off by ~1000× — not to argue with a genuinely
#: large opening, and a rejected frame is a worse outcome than a flagged one.
MICRO_PLAUSIBLE_UM = (-2_000.0, 100_000.0)
MACRO_PLAUSIBLE_MM = (0.0, 100.0)

#: Stage thresholds (§3.2.5). INITIATION is the rate crossing above; PROPAGATION
#: is the plan's macro jump; CRITICAL is either a seam that has opened past
#: ``CRACK_CRITICAL_MM`` or a propagation that has not arrested within
#: ``CRACK_CRITICAL_HOLD_S``. Both are physical: a 5 mm opening in a 2 m diorama
#: seam is already a fall hazard, and a crack still moving after 20 s is not
#: arresting on its own.
CRACK_CRITICAL_MM = env_float("NS_CRACK_CRITICAL_MM", 5.0)
CRACK_CRITICAL_HOLD_S = env_float("NS_CRACK_CRITICAL_HOLD_S", 20.0)
#: ``macro_d30`` — the 30 s macro delta the plan's coherence table calls
#: ``macro_d30`` (§3.6). Recomputed on read, never stored (§3.5 "Database").
MACRO_DELTA_WINDOW_S = env_float("NS_MACRO_DELTA_WINDOW_S", 30.0)

#: The two reference scales ``cross_sensor_coherence`` normalises against (§3.6).
#: ``VIB_REFERENCE`` is the vibration count that counts as "the ground is being
#: shaken", ``TILT_REFERENCE`` the tilt delta that counts as "the roof is leaning".
#: Both are the §3.6 table's own numbers: the truck row has vib_z ≈ 0.90 at 46
#: impacts, and the rain row has tilt_z ≈ 0.45.
VIB_REFERENCE = env_float("NS_VIB_REFERENCE", 50.0)                 # counts / 2 s
TILT_REFERENCE = env_float("NS_TILT_REFERENCE", 2.0)                # deg / 30 s

#: ``macro_opening``'s two-point calibration — the pot's own ruler, §3.4. The node
#: transmits millimetres already (firmware converts with the same two points), so
#: this pair exists for the raw-ADC path: the hub simulator, a bench capture, and
#: any future frame that carries counts. ``check_contracts.py`` asserts these equal
#: the firmware's ``NS_MACRO_ADC_*`` / ``NS_MACRO_MM_PER_COUNT``, because two
#: copies of a calibration is exactly how a channel ends up reading 40 % low.
MACRO_ADC_LO = env_float("NS_MACRO_ADC_LO", 0.0)                    # counts
MACRO_MM_LO = env_float("NS_MACRO_MM_LO", 0.0)                      # mm
MACRO_ADC_HI = env_float("NS_MACRO_ADC_HI", 4095.0)                 # counts
MACRO_MM_HI = env_float("NS_MACRO_MM_HI", 25.0)                     # mm

# ---------------------------------------------------------------------------
# 8. Geomechanics — pair elongation, settlement, zone risk (B8, Phase 4)
# ---------------------------------------------------------------------------

#: The collinear node pairs a *baseline* can be drawn between: the two nodes on
#: each level, in the order `LEVELS` lists them. Derived, never re-typed — the
#: pair `(node_1, node_2)` is Level 1's pair because those are the two nodes
#: Level 1 carries, and `validate_config()` refuses to boot a build where a level
#: holds anything but exactly two nodes.
#:
#: Why a *pair* and not a node (plan §4.3): a single node's displacement is
#: ambiguous — the whole panel can settle uniformly, or one end can move. Only
#: the difference says the strata between them are deforming. That is why land
#: surveyors measure baselines rather than single marks.
PAIRS: tuple[tuple[str, str], ...] = tuple(
    (members[0], members[1])
    for _lv, members in sorted(LEVELS.items())
    if len(members) >= 2
)

# ---------------------------------------------------------------------------
# 8b. Model 1 — the pattern discriminator's own contract (B9, Phase 5)
# ---------------------------------------------------------------------------

#: **The missing-value sentinel, and it is load-bearing.**
#:
#: Eleven of the 32 extended features are legitimately ``NaN`` on a real window —
#: a node with no load cell has no ``cross_sensor_coherence``, a quiet site has no
#: Fukuzono fit, a node whose neighbours have never spoken has no
#: ``spatial_coherence``. ``NaN`` is the honest representation and the extractor
#: returns it.
#:
#: But ``sklearn.ensemble.RandomForestClassifier`` — §5.4's chosen Model 1 —
#: **rejects NaN outright** (unlike XGBoost, which handles it natively). So the
#: vector that reaches the tree has to be numeric, and the substitution has to be
#: the *same* substitution at training time and at serving time or the model is
#: silently served a different world than it was fitted on.
#:
#: One constant, one conversion function (``features.to_model_vector``), called by
#: both the training notebook and the live path. It is recorded in
#: ``patterns_meta.json`` so an artifact can always be traced to its convention.
#:
#: ``-999`` is chosen to be far outside every feature's real range, so a tree split
#: on it is unambiguous and cannot be confused with a real reading. That is not a
#: trick: "this node has no crack instrument" is *genuinely informative* — the
#: classifier is entitled to learn it, and a sentinel a tree can isolate is
#: exactly how it does so.
PATTERN_MISSING_SENTINEL = env_float("NS_PATTERN_MISSING_SENTINEL", -999.0)

#: Model 1 cadence, always (it must catch a 0.5 s truck impulse — §5.7).
M1_HZ = env_float("NS_M1_HZ", 2.0)
#: Model 2 cadence **by M1's verdict** — where the compute saving actually comes
#: from (§5.7). Quiet sites throttle the 400-tree boosted ensemble 10×.
M2_HZ: dict[str, float] = {
    "QUIET": env_float("NS_M2_HZ_QUIET", 0.2),
    "ENVIRONMENTAL": env_float("NS_M2_HZ_ENV", 1.0),
    "STRUCTURAL": env_float("NS_M2_HZ_STRUCT", 2.0),
    "UNKNOWN": env_float("NS_M2_HZ_UNKNOWN", 2.0),
}

#: The cascade's one safety switch (§5.2). ``advisory`` = Model 1 may advise and
#: may throttle compute, and may **never** suppress an alert. ``gate`` would let
#: it suppress — and a gatekeeper classifier can only ever remove information from
#: a safety system, so ``gate`` exists for the off-line experiment that measures
#: what suppression would have cost, not for production.
CASCADE_MODE = env_str("NS_CASCADE_MODE", "advisory")

#: §5.10's firewall: a Model-2 TTF at or below this **always** alerts, whatever
#: Model 1 said. Rule 2, and the most important constant in the repository.
CRITICAL_BAND_S = env_float("NS_CRITICAL_BAND_S", 30.0)

#: Pair strain that raises the **level** to WARNING (B8 §4.5), in microstrain.
#: The plan's starting value, and it is a big one: 400 µε across a 70 m baseline
#: is 28 mm of *relative* movement between the two nodes. That is deliberate —
#: this is the threshold for "the ground between the pillars is failing", not for
#: "a sensor twitched" — but it is worth knowing before the bench drill, because
#: it is a number the simulator has to actually reach.
PAIR_STRAIN_WARN = env_float("NS_PAIR_STRAIN_WARN", 400.0)          # µε

#: Severity → alert tier mapping (B3, §A.4) — the lower edges of the four tiers,
#: read in order: <30 s CRITICAL · 30–120 HIGH · 120–600 ELEVATED · >=600 NOMINAL.
#: ``engine.severity`` owns the mapping and the WATCH tier for "no estimate";
#: the numbers live here so a tuning pass changes one line, and the tests read
#: the same tuple the running server does. Overridable like every other
#: threshold: ``NS_TTF_BANDS=0,45,150,900``.
TTF_BANDS: tuple[float, ...] = tuple(
    float(x) for x in env_str("NS_TTF_BANDS", "0,30,120,600").split(","))


# ---------------------------------------------------------------------------
# 7. The Double AI — one safety switch, plus the scheduler table
# ---------------------------------------------------------------------------

#: "off" = never run Model 1 · "advisory" (shipped) = Model 1 may only *lower*
#: Model 2's sample rate, never change a verdict · "active" = reserved.
CASCADE_MODE = env_str("NS_CASCADE_MODE", "advisory")
CASCADE_MODES = ("off", "advisory", "active")

#: Model-2 sample rate by Model-1 class. Where the compute saving comes from.
M2_SAMPLE_HZ: dict[str, float] = {
    "QUIET": 1.0,
    "MICRO_SEEP": 1.0,
    "MACRO_OPENING": 2.0,
    "PROGRESSIVE": 5.0,
    "ACCELERATING": 10.0,
    "TERTIARY": 10.0,
    "CRITICAL": 10.0,
}


# ---------------------------------------------------------------------------
# 7b. Evacuation routing (B5, Phase 7)
# ---------------------------------------------------------------------------

#: Risk added to a leg by an **endpoint node**. §7.3's table, verbatim. Read as
#: "how much longer is this leg, in metres, than it looks": a CRITICAL node makes
#: a 30 m corridor worth 150 m to a router that is trying to keep people alive.
#:
#: STALE is deliberately *between* WARNING and CRITICAL. A node we cannot hear is
#: worse than one we can, because we do not know whether it is worse — that is
#: Phase 2's rule ("UNKNOWN is not safe") priced rather than merely stated.
HAZARD_COST: dict[str, float] = {
    "CRITICAL": 4.0,
    "WARNING": 1.5,
    "STALE": 2.5,
}

#: The crack engine's stage, added on top of the status term. Two channels, summed
#: rather than maxed: the crack engine sees micro-cracking before macro
#: displacement moves, and a node can be calm-but-cracking or loud-but-quiet.
CRACK_HAZARD: dict[str, float] = {
    "PROPAGATION": 1.0,
    "CRITICAL": 4.0,
}

#: Walking pace used for the ETA on the evac card. 1.2 m/s is a purposeful pace
#: on level ground; underground, in a smoke-free drift, carrying a lamp, it is
#: optimistic-but-not-silly. The ETA is a planning number, and the card says so —
#: it is never used for a decision, only for the sentence next to the route.
WALK_SPEED_MPS = env_float("NS_WALK_SPEED_MPS", 1.2)

#: How often the router re-plans. Independent of the 2 s map poll: the poll reads
#: the last plan, the ticker makes a new one. 1 Hz matches the status pass, so a
#: route never describes a world more than a second older than the amber light
#: beside it.
ROUTE_HZ = env_float("NS_ROUTE_HZ", 1.0)


# ---------------------------------------------------------------------------
# 8. Paths — resolved once, used everywhere
# ---------------------------------------------------------------------------

MODELS_DIR = Path(env_str("NS_MODELS_DIR", str(ROOT / "models")))
ASSETS_DIR = Path(env_str("NS_ASSETS_DIR", str(ROOT / "assets")))
TOOLS_DIR = ROOT / "tools"
TESTS_DIR = ROOT / "tests"
PERSISTENCE_DIR = ROOT / "persistence"
MIGRATIONS_DIR = PERSISTENCE_DIR / "migrations"


def _resolve_frontend_dir() -> Path:
    """Where the dashboard lives.

    Order: explicit ``NS_FRONTEND_DIR`` → ``neural-strata/frontend`` → the
    sibling ``NeuralStrata/`` build folder. The last fallback exists so the
    server still serves the dashboard on a machine where the frontend was
    delivered as its own folder rather than copied into the repo.
    """
    explicit = os.environ.get("NS_FRONTEND_DIR")
    if explicit:
        return Path(explicit)
    in_repo = PROJECT_ROOT / "frontend"
    if (in_repo / "index.html").exists():
        return in_repo
    return PROJECT_ROOT.parent / "NeuralStrata"


FRONTEND_DIR = _resolve_frontend_dir()

#: Scratch/runtime state for the edge laptop: the black-box truth store and the
#: cloud stand-in. Never committed (regenerable), never inside the package.
RUNTIME_DIR = Path(env_str("NS_RUNTIME_DIR", str(PROJECT_ROOT / "runtime")))

#: The interim black-box gateway modules (they become ``backend/blackbox`` proper
#: in Phase 10; until then ``app.py`` imports the gateway from here).
BLACKBOX_DIR = Path(env_str("NS_BLACKBOX_DIR", str(ROOT / "blackbox")))

ASSET_FILES = {
    "tunnel_map": ASSETS_DIR / "tunnel_map.json",
    "site_meta": ASSETS_DIR / "site_meta.json",
}

MODEL_FILES = {
    "ttf_model_v1": MODELS_DIR / "ttf_model_v1.json",
    "ttf_model_v2": MODELS_DIR / "ttf_model_v2.json",
    "scaler": MODELS_DIR / "scaler.json",
    "model_meta": MODELS_DIR / "model_meta.json",
    "pattern_model_v1": MODELS_DIR / "pattern_model_v1.json",
    "pattern_meta": MODELS_DIR / "pattern_meta.json",
    "kinematic_calibration": MODELS_DIR / "kinematic_calibration.json",
    "manifest": MODELS_DIR / "MANIFEST.json",
    "novelty_meta": MODELS_DIR / "novelty_meta.json",
}


# ---------------------------------------------------------------------------
# 9. Server
# ---------------------------------------------------------------------------

HOST = env_str("NS_HOST", "0.0.0.0")
PORT = env_int("NS_PORT", 5000)
RECORD_ENABLED = env_bool("NS_RECORD_ENABLED", False)


# ---------------------------------------------------------------------------
# 10. Boot-time validation
# ---------------------------------------------------------------------------

def validate_config() -> list[str]:
    """Return the list of *fatal* configuration problems. Empty list = good.

    Deliberately does **not** check for model artifacts or map files: those are
    a documented degrade path (§A.3.8 boot policy), not a boot failure.
    """
    problems: list[str] = []

    if not (0 < ACC_WARNING < ACC_CRITICAL):
        problems.append(
            f"thresholds: need 0 < ACC_WARNING < ACC_CRITICAL "
            f"(got {ACC_WARNING} / {ACC_CRITICAL}) — a node would go straight to CRITICAL"
        )
    if VEL_WARNING <= 0:
        problems.append(f"thresholds: VEL_WARNING must be > 0 (got {VEL_WARNING})")
    if FUK_V_MIN <= 0:
        problems.append(f"thresholds: FUK_V_MIN must be > 0 (got {FUK_V_MIN})")
    if MICRO_RATE_THRESHOLD <= 0:
        problems.append(f"thresholds: MICRO_RATE_THRESHOLD must be > 0 (got {MICRO_RATE_THRESHOLD})")
    if MACRO_JUMP_THRESHOLD <= 0:
        problems.append(f"thresholds: MACRO_JUMP_THRESHOLD must be > 0 (got {MACRO_JUMP_THRESHOLD})")

    # Phase 3 — the crack column's own arithmetic. Each of these is checked
    # because a wrong one fails *quietly*: a clear-fraction at or above 1.0 makes
    # the hysteresis never release, and a median of 1 is not a filter.
    if MICRO_SETTLE_S <= 0:
        problems.append(f"crack: MICRO_SETTLE_S must be > 0 (got {MICRO_SETTLE_S})")
    if MICRO_RATE_WINDOW_S <= 0:
        problems.append(f"crack: MICRO_RATE_WINDOW_S must be > 0 (got {MICRO_RATE_WINDOW_S})")
    if MICRO_MEDIAN_N < 1 or MICRO_MEDIAN_N % 2 == 0:
        problems.append(f"crack: MICRO_MEDIAN_N must be odd and >= 1 (got {MICRO_MEDIAN_N})")
    if CRACK_CONFIRM_N < 1:
        problems.append(f"crack: CRACK_CONFIRM_N must be >= 1 (got {CRACK_CONFIRM_N})")
    if not (0.0 < CRACK_CLEAR_FRACTION < 1.0):
        problems.append(
            f"crack: CRACK_CLEAR_FRACTION must be between 0 and 1 exclusive "
            f"(got {CRACK_CLEAR_FRACTION}) — at 1.0 a raised event can never clear"
        )
    if MACRO_DELTA_WINDOW_S <= 0:
        problems.append(f"crack: MACRO_DELTA_WINDOW_S must be > 0 (got {MACRO_DELTA_WINDOW_S})")
    if VIB_REFERENCE <= 0 or TILT_REFERENCE <= 0:
        problems.append(
            f"crack: VIB_REFERENCE and TILT_REFERENCE must be > 0 "
            f"(got {VIB_REFERENCE} / {TILT_REFERENCE}) — a zero divisor in coherence"
        )
    if not (0 < CRACK_CRITICAL_MM):
        problems.append(f"crack: CRACK_CRITICAL_MM must be > 0 (got {CRACK_CRITICAL_MM})")
    if CRACK_CRITICAL_MM <= MACRO_JUMP_THRESHOLD:
        problems.append(
            f"crack: CRACK_CRITICAL_MM ({CRACK_CRITICAL_MM}) must exceed "
            f"MACRO_JUMP_THRESHOLD ({MACRO_JUMP_THRESHOLD}) — otherwise the stage "
            "machine skips PROPAGATION entirely"
        )
    if MACRO_ADC_HI <= MACRO_ADC_LO:
        problems.append(
            f"crack: the macro pot calibration needs adc_hi > adc_lo "
            f"(got {MACRO_ADC_LO} → {MACRO_ADC_HI})"
        )
    _lo, _hi = MICRO_PLAUSIBLE_UM
    if not _lo < 0 < _hi:
        problems.append(f"crack: MICRO_PLAUSIBLE_UM must straddle zero (got {MICRO_PLAUSIBLE_UM})")

    # Phase 4 — the pair arithmetic. A level with one node or three has no
    # baseline to draw, and a zero threshold would put every level in WARNING on
    # boot; both are silent failures, not crashes.
    if PAIR_STRAIN_WARN <= 0:
        problems.append(f"geomech: PAIR_STRAIN_WARN must be > 0 (got {PAIR_STRAIN_WARN})")
    for _lv, _members in sorted(LEVELS.items()):
        if len(_members) != 2:
            problems.append(
                f"geomech: level {_lv} carries {len(_members)} nodes "
                f"({', '.join(_members)}) — pair elongation needs exactly two"
            )
    _paired = {n for _p in PAIRS for n in _p}
    if _paired != set(NODE_IDS):
        _missing = sorted(set(NODE_IDS) - _paired)
        if _missing:
            problems.append(
                f"geomech: {', '.join(_missing)} on no pair — every node needs a "
                "baseline partner or its zone can never be measured"
            )

    if MODEL_BUF < 2:
        problems.append(f"buffers: MODEL_BUF must be >= 2 (got {MODEL_BUF})")
    if MODEL_FRESH_S <= 0:
        problems.append(f"buffers: MODEL_FRESH_S must be > 0 (got {MODEL_FRESH_S})")
    if HISTORY_MAX <= 0:
        problems.append(f"buffers: HISTORY_MAX must be > 0 (got {HISTORY_MAX})")
    if HISTORY_INTERVAL_S <= 0:
        problems.append(f"buffers: HISTORY_INTERVAL_S must be > 0 (got {HISTORY_INTERVAL_S})")
    if not (1 <= TICK_MS <= 1000):
        problems.append(f"buffers: TICK_MS must be 1–1000 ms (got {TICK_MS})")
    if INFER_HZ <= 0:
        problems.append(f"buffers: INFER_HZ must be > 0 (got {INFER_HZ})")

    if not NODE_IDS:
        problems.append("topology: NODE_IDS is empty")
    if len(set(NODE_IDS)) != len(NODE_IDS):
        problems.append(f"topology: NODE_IDS has duplicates — {NODE_IDS}")

    seen: dict[str, int] = {}
    for level, members in LEVELS.items():
        for n_id in members:
            if n_id not in NODE_IDS:
                problems.append(f"topology: level {level} lists unknown node {n_id!r}")
            if n_id in seen:
                problems.append(
                    f"topology: {n_id} is on level {seen[n_id]} and level {level} — "
                    "each node belongs to exactly one level"
                )
            seen[n_id] = level
    missing = [n for n in NODE_IDS if n not in seen]
    if missing:
        problems.append(f"topology: {', '.join(missing)} on no level — LEVELS must cover NODE_IDS")

    if not ACTIVE_NODES:
        problems.append("topology: ACTIVE_NODES is empty — nothing would be tracked")
    unknown = [n for n in ACTIVE_NODES if n not in NODE_IDS]
    if unknown:
        problems.append(
            f"topology: ACTIVE_NODES lists {', '.join(unknown)} which is not in NODE_IDS "
            f"{NODE_IDS}"
        )

    if CASCADE_MODE not in CASCADE_MODES:
        problems.append(f"ai: CASCADE_MODE must be one of {CASCADE_MODES} (got {CASCADE_MODE!r})")

    if not (0 < PORT <= 65535):
        problems.append(f"server: PORT must be 1–65535 (got {PORT})")
    if BAUD_RATE <= 0:
        problems.append(f"serial: BAUD_RATE must be > 0 (got {BAUD_RATE})")
    if SERIAL_RECONNECT_S <= 0:
        problems.append(f"serial: SERIAL_RECONNECT_S must be > 0 (got {SERIAL_RECONNECT_S})")

    if DB_POOL_MIN < 1:
        problems.append(f"database: DB_POOL_MIN must be >= 1 (got {DB_POOL_MIN})")
    if DB_POOL_MAX < DB_POOL_MIN:
        problems.append(
            f"database: DB_POOL_MAX ({DB_POOL_MAX}) must be >= DB_POOL_MIN ({DB_POOL_MIN})"
        )
    if not (0 < DB_CONNECT_TIMEOUT_S <= 60):
        problems.append(f"database: DB_CONNECT_TIMEOUT_S must be 1–60 s (got {DB_CONNECT_TIMEOUT_S})")

    return problems


# ═══════════════════════════════════════════════════════════════════════════
# §8b · ALERTS  (Phase 8 · B4) — the timing, the channels, the limits
# ═══════════════════════════════════════════════════════════════════════════
# Every number here is a promise the system makes out loud, so each one is a
# named constant with the reasoning beside it rather than a literal at a call site.

#: Same condition, same node, same tier within this window ⇒ ONE alert. The ticker
#: runs at 2 Hz; without a dedup window a single condition would generate 7,200
#: identical alerts an hour and the Safety Officer would switch the phone off.
ALERT_DEDUP_S = env_float("NS_ALERT_DEDUP_S", 90.0)

#: No acknowledgement within this long ⇒ re-notify, adding a channel and raising
#: the tier. **Time-based, not event-based**: a condition that is still true is
#: still true, and re-sending on every tick is not escalation, it is noise.
ALERT_ESCALATE_S = env_float("NS_ALERT_ESCALATE_S", 60.0)

#: A condition that has cleared for this long closes its own alert. It settles
#: rather than snapping shut because a signal that crosses a threshold twice in a
#: second is one condition, not two alerts.
ALERT_SETTLE_S = env_float("NS_ALERT_SETTLE_S", 30.0)

#: The tier floor when the cascade's firewall is engaged (Rule 2, §8.2). Not
#: configurable on purpose: M1 and M2 disagreeing is the exact case where the
#: alert layer must not be the thing that decides to stay quiet.
FIREWALL_ALERT_FLOOR = "HIGH"

# -- the down-link (Phase 8, §7.6's server end) ------------------------------
#: How often the server looks at the junction plan. 1 s: the plan is a handful of
#: Dijkstra calls on nine links, and this is the loop that decides what the strips
#: say — a slower tick means a light that is visibly late to a hazard.
HUB_LED_TICK_S = env_float("NS_HUB_LED_TICK_S", 1.0)

#: How often an **unchanged** plan is re-sent down the ring.
#:
#: This number is what makes §H6.6's hold rule mean anything. The hub is told to
#: "hold the last pattern for 30 s, then default to hold_amber" when commands stop
#: arriving — but with commands sent only on change, silence is the *normal* state
#: of a quiet mine, so every steady green would decay to amber. 20 s is comfortably
#: inside the hub's 30 s window and comfortably above a chatty re-route, and the
#: re-assert also self-heals the case the plan alone cannot see: a hub that
#: rebooted mid-incident gets the current picture within one interval instead of
#: showing its boot breathe until the situation next changes.
HUB_REASSERT_S = env_float("NS_HUB_REASSERT_S", 20.0)

#: How long a junction may go without any command before the hub stops trusting the
#: picture it is holding and falls back to `hold_amber` (§H6.6). Kept here so the
#: server's re-assert interval and the hub's patience are reviewable side by side:
#: the firmware constant `NS_SILENCE_MS` must stay **greater** than this.
HUB_SILENCE_S = env_float("NS_HUB_SILENCE_S", 30.0)

# -- SMS -------------------------------------------------------------------
#: The cap that stops an alert storm becoming twenty texts. When it bites, the
#: refusal is written to the log as RATE_LIMITED and onto the row as suppressed —
#: §8.8: *never silently drop*.
SMS_MAX_PER_HOUR = env_int("NS_SMS_MAX_PER_HOUR", 20)
#: One red-alert text per minute. A repeat inside this window is held, not sent.
SMS_MIN_GAP_S = env_float("NS_SMS_MIN_GAP_S", 60.0)
ALERT_SMS_TO = env_str("NS_ALERT_SMS_TO", "")          # comma-separated handsets, E.164
#: hub = the main hub's modem frame. messages = this laptop's Messages app,
#: which is what a bench with no 4G modem can actually deliver.
SMS_TRANSPORT = env_str("NS_SMS_TRANSPORT", "hub")
SMS_MODEM_UART = env_int("NS_SMS_MODEM_UART", 2)       # the hub's modem port

# -- SMTP ------------------------------------------------------------------
SMTP_HOST = env_str("NS_SMTP_HOST", "")
SMTP_PORT = env_int("NS_SMTP_PORT", 587)
SMTP_USER = env_str("NS_SMTP_USER", "")
SMTP_PASSWORD = env_str("NS_SMTP_PASSWORD", "")
SMTP_FROM = env_str("NS_SMTP_FROM", "")
SMTP_TLS = env_bool("NS_SMTP_TLS", True)
#: Short on purpose. A mail server that takes 30 s to time out must not hold the
#: dispatcher — the siren is already sounding by then and the operator is watching
#: a frozen console.
SMTP_TIMEOUT_S = env_float("NS_SMTP_TIMEOUT_S", 8.0)
ALERT_EMAIL_TO = env_str("NS_ALERT_EMAIL_TO", "")
#: Where the deep link in the email points. Empty = a relative link, which is the
#: honest default for a prototype whose console is on a laptop.
ALERT_LINK_BASE = env_str("NS_ALERT_LINK_BASE", "")

# -- Tier 1 · the edge site identity (Phase 9) -----------------------------
#: Every synced row carries it. §E.1 rule 2: this is what makes the multi-site
#: story true rather than aspirational — one cloud account, many edges, and no
#: row whose origin has to be inferred from a connection.
SITE_ID = env_str("NS_SITE_ID", "site-unknown")
SITE_NAME = env_str("NS_SITE_NAME", "Neural Strata site")

# -- Tier 2 · the cloud (Phase 9) -----------------------------------------
#: Empty ⇒ **local-only mode, and not one line of cloud code runs** (§F.5). That
#: guard is the whole reason a missing or misconfigured cloud can never break the
#: edge: the mine keeps working, the queue keeps filling, and the console says so.
CLOUD_DB_URL = env_str("NS_CLOUD_DB_URL", "")
CLOUD_ENABLED = bool(CLOUD_DB_URL)

#: The **HTTP** half of the same story: the outbox ships discrete events (alerts,
#: batch manifests) as gzip+HMAC POSTs, because those are small, infrequent and
#: want an idempotency key. Bulk rows (hourly rollups, alert history) go over the
#: Postgres connection above with a `synced_at` watermark. §E.1 rule 1: the two
#: tiers are deliberately different shapes.
CLOUD_HTTP_URL = env_str("NS_CLOUD_HTTP_URL", "")
CLOUD_HMAC_KEY = env_str("NS_CLOUD_HMAC_KEY", "")

#: 900 s: §G.1's number. A free tier is suspended for idleness, not for volume, so
#: a slower cadence is *safer* than a faster one — and a manual flush exists for
#: the demo. No work ⇒ no connection at all, so a quiet site costs nothing.
SYNC_INTERVAL_S = env_int("NS_SYNC_INTERVAL_S", 900)
SYNC_BATCH = env_int("NS_SYNC_BATCH", 500)
CLOUD_TIMEOUT_S = env_int("NS_CLOUD_CONNECT_TIMEOUT", 5)
#: After this many failures a row is FAILED and keeps its last error, rather than
#: being retried forever. §9.2 rule 4. Bumped **on failure only** — see sync.py.
SYNC_MAX_ATTEMPTS = env_int("NS_SYNC_MAX_ATTEMPTS", 10)

#: §9.2 rule 4: 1, 2, 4, 8, 16, 30 … capped. The cap matters more than the curve:
#: an uncapped doubling reaches an hour by the tenth attempt, which reads as "sync
#: died" on a console that is merely having a bad afternoon.
SYNC_BACKOFF_S = (1, 2, 4, 8, 16, 30)

#: §9.7's cap. When the queue is full the refusal is an explicit `OUTBOX_FULL`
#: **infrastructure alert** — never a silent drop, the same rule as the SMS rate cap.
OUTBOX_MAX_ROWS = env_int("NS_OUTBOX_MAX_ROWS", 50_000)

#: How often the telemetry aggregate becomes an outbox item (§9.5).
#:
#: **Not 0.5 s, and the arithmetic is the argument.** §9.5 says "telemetry enqueue
#: per 0.5 s aggregate (not per row)", which fixes the *unit* — one summary per
#: aggregate, not one row per serial frame — and the unit is what the correctness
#: of the queue depends on. The *cadence* is a separate question, and at the
#: aggregate's own rate it is fatal: four nodes at 2 Hz is 480 items/min, while the
#: daemon drains SYNC_BATCH=500 every SYNC_INTERVAL_S=900 s ≈ 33/min. The queue
#: would grow by ~450 rows a minute, reach OUTBOX_MAX_ROWS in under two hours, and
#: sit full — so §9.7's OUTBOX_FULL alert would be permanently lit on a site where
#: nothing is wrong, which is the one thing an infrastructure alert must never be.
#:
#: One minute keeps the demo's promise intact ("watch the queue climb with the link
#: down"): 4 nodes × 1/min = 5 760 rows/day against a 50 000-row cap, and 4 rows per
#: cycle against a batch of 500. `NS_OUTBOX_TELEMETRY_S=0.5` reproduces the literal
#: reading; the counter that says it was a mistake is the OUTBOX_FULL alert itself.
OUTBOX_TELEMETRY_S = env_float("NS_OUTBOX_TELEMETRY_S", 60.0)

#: Compaction keeps a delivered row for this long before pruning it, and prunes
#: **only** rows confirmed SENT (§9.2 rule 6). A pending row is never pruned: it is
#: the only copy of something that has not arrived anywhere yet.
OUTBOX_KEEP_DAYS = env_int("NS_OUTBOX_KEEP_DAYS", 7)

#: Which kinds ship first when the queue is deep. §9.5: *"a flooded queue still
#: ships the alerts first"* — a regulator reading a month-old telemetry summary
#: cares less than a safety officer reading yesterday's alert.
OUTBOX_PRIORITY = {"alert": 0, "manifest": 1, "drill": 2, "telemetry": 3, "rollup": 4}


# -- who may acknowledge ---------------------------------------------------
#: SRS §5.5: acknowledgement is a human act with a name attached. The console
#: offers this list; the server refuses an empty operator.
ALERT_OPERATORS = ("Safety Officer", "Control Room", "Test")


def _alerts_block() -> dict:
    """The alert section of :func:`describe()` — with the password absent.

    `describe()` is printed by `config.py --check` and pasted into reports; a
    credentials dump in a report is a defect, so the SMTP password is reported as
    a boolean and never as a value.
    """
    return {
        "dedup_s": ALERT_DEDUP_S,
        "escalate_s": ALERT_ESCALATE_S,
        "settle_s": ALERT_SETTLE_S,
        "firewall_floor": FIREWALL_ALERT_FLOOR,
        "sms": {"max_per_hour": SMS_MAX_PER_HOUR, "to": ALERT_SMS_TO,
                "transport": SMS_TRANSPORT, "modem_uart": SMS_MODEM_UART},
        "smtp": {"host": SMTP_HOST, "port": SMTP_PORT, "from": SMTP_FROM,
                 "to": ALERT_EMAIL_TO, "tls": SMTP_TLS, "timeout_s": SMTP_TIMEOUT_S,
                 "configured": bool(SMTP_HOST and ALERT_EMAIL_TO),
                 "password_set": bool(SMTP_PASSWORD)},
        "operators": list(ALERT_OPERATORS),
        "link_base": ALERT_LINK_BASE,
        "downlink": {"tick_s": HUB_LED_TICK_S, "reassert_s": HUB_REASSERT_S,
                     "silence_s": HUB_SILENCE_S},
        "sync": {"site_id": SITE_ID, "site_name": SITE_NAME,
                 "enabled": CLOUD_ENABLED, "http": bool(CLOUD_HTTP_URL),
                 "signed": bool(CLOUD_HMAC_KEY),
                 "interval_s": SYNC_INTERVAL_S, "batch": SYNC_BATCH,
                 "max_attempts": SYNC_MAX_ATTEMPTS,
                 "backoff_s": list(SYNC_BACKOFF_S),
                 "outbox_max_rows": OUTBOX_MAX_ROWS,
                 "outbox_telemetry_s": OUTBOX_TELEMETRY_S,
                 "keep_days": OUTBOX_KEEP_DAYS},
    }


def describe() -> dict:
    """Redacted configuration dump for ``/api/health`` (Phase 2 wires the route).

    The password is never returned — only whether one has been set.
    """
    return {
        "root": str(ROOT),
        "project_root": str(PROJECT_ROOT),
        "database": {
            "host": DB_HOST,
            "port": DB_PORT,
            "name": DB_NAME,
            "user": DB_USER,
            "password_set": bool(DB_PASS) and DB_PASS != "your_password_here",
            "pool": [DB_POOL_MIN, DB_POOL_MAX],
        },
        "serial": {"port": SERIAL_PORT, "baud": BAUD_RATE},
        "topology": {"nodes": list(NODE_IDS), "active_nodes": list(ACTIVE_NODES),
                     "levels": {str(k): list(v) for k, v in LEVELS.items()}},
        "cadence": {
            "tick_ms": TICK_MS,
            "history_interval_s": HISTORY_INTERVAL_S,
            "history_max": HISTORY_MAX,
            "model_buf": MODEL_BUF,
            "model_fresh_s": MODEL_FRESH_S,
            "infer_hz": INFER_HZ,
            "stale_after_s": STALE_AFTER_S,
        },
        "thresholds": {
            "acc_critical": ACC_CRITICAL,
            "acc_warning": ACC_WARNING,
            "vel_warning": VEL_WARNING,
            "fuk_v_min": FUK_V_MIN,
            "micro_rate_threshold": MICRO_RATE_THRESHOLD,
            "macro_jump_threshold": MACRO_JUMP_THRESHOLD,
        },
        # Phase 3 — the crack column, in the units it is actually measured in.
        # ``/api/health`` publishes this so the dashboard, the runbook and the
        # bench script all read one set of numbers instead of three.
        "crack": {
            "micro_unit": "um",
            "micro_definition": "crack-mouth opening (load cell + spring)",
            "micro_rate_threshold_um_s": MICRO_RATE_THRESHOLD,
            "micro_settle_s": MICRO_SETTLE_S,
            "micro_rate_window_s": MICRO_RATE_WINDOW_S,
            "micro_median_n": MICRO_MEDIAN_N,
            "macro_jump_threshold_mm": MACRO_JUMP_THRESHOLD,
            "macro_delta_window_s": MACRO_DELTA_WINDOW_S,
            "confirm_samples": CRACK_CONFIRM_N,
            "clear_fraction": CRACK_CLEAR_FRACTION,
            "critical_mm": CRACK_CRITICAL_MM,
            "critical_hold_s": CRACK_CRITICAL_HOLD_S,
            "instrumented_nodes": list(CRACK_NODE_IDS),
            "vib_reference": VIB_REFERENCE,
            "tilt_reference": TILT_REFERENCE,
            "macro_calibration": {"adc_lo": MACRO_ADC_LO, "mm_lo": MACRO_MM_LO,
                                  "adc_hi": MACRO_ADC_HI, "mm_hi": MACRO_MM_HI},
        },
        # Phase 4 — the pair/zone layer's one tunable. The baseline lengths and
        # the zone polygons live in ``tunnel_map.json`` (geomech reads them), so
        # they are deliberately absent here: two copies of a 70 m baseline is how
        # a strain readout ends up 30 % wrong.
        "geomech": {
            "pair_strain_warn_ue": PAIR_STRAIN_WARN,
            "pairs": [list(pair) for pair in PAIRS],
            "units": {"elongation": "mm", "strain": "ue", "settlement": "mm"},
        },
        "ai": {"cascade_mode": CASCADE_MODE, "ttf_bands": list(TTF_BANDS)},
        "paths": {
            "runtime": str(RUNTIME_DIR),
            "models": str(MODELS_DIR),
            "assets": str(ASSETS_DIR),
            "frontend": str(FRONTEND_DIR),
            "blackbox": str(BLACKBOX_DIR),
        },
        "server": {"host": HOST, "port": PORT},
        "alerts": _alerts_block(),
    }


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import json
    import sys

    print(json.dumps(describe(), indent=2))
    bad = validate_config()
    if bad:
        print("\nFATAL:", file=sys.stderr)
        for b in bad:
            print(f"  · {b}", file=sys.stderr)
        sys.exit(2)
    print("\nconfig OK")
