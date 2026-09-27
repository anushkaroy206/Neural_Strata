"""
Neural Strata — the scenario console: 7 injection profiles.
===========================================================

The console is how the product is *demonstrated*, and it is also how the AI is
tested against the thing it will get wrong most often: a large disturbance that
is **not** a collapse. A passing 10-tonne loader looks like a collapse to a naive
threshold — the difference is that it has no memory.

Seven profiles, and the contrast between the first two is the pitch:

===============  ==============  ===============================================
Profile          Verdict class   What it represents
===============  ==============  ===============================================
warning_creep    STRUCTURAL      sustained creep; the TTF counts down
critical_collapse STRUCTURAL     terminal acceleration; TTF → seconds
machinery_pass   ENVIRONMENTAL   transient burst, no memory of it 30 s later
blast            ENVIRONMENTAL   one impulse, then silence
rain_seepage     ENVIRONMENTAL   slow low-amplitude wet-season drift
sensor_fault     FAULT           a sensor pinned at a rail — must block a forecast
nominal          —               everything returns to rest
===============  ==============  ===============================================

Only ``warning_creep`` and ``critical_collapse`` are *structural*. A structural
profile may raise an alarm; an environmental one may not — that rule is enforced
downstream (Phase 6), not here. This module's job is to produce the physical
signal; the verdict is somebody else's.

**D14.** ``apply_injections`` used to be called with a hardcoded ``0.05``. The
ticker does DB and buffer work inside the same loop, so the real period drifts
and every profiling timing in the runbook was approximate. The caller now passes
the **measured** dt.

Backward compatibility
----------------------
The runbook's curl commands use ``{"severity": "warning"|"critical"}``. Those
still work: ``SEVERITY_ALIASES`` maps them onto the profile names. Existing
commands must never need editing — a runbook that has to be updated is a runbook
that will be wrong during a demo.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import dataclass

from contracts import CRACK_NODE_IDS


# ---------------------------------------------------------------------------
# The seam model — the reason a truck pass is not a crack
# ---------------------------------------------------------------------------
#
# The crack column measures the **seam**: the gap between two blocks. A passing
# loader shakes the tunnel, and the whole portal moves a little — but the seam
# itself opens by essentially nothing, because the disturbance is impulsive and
# elastic and the seam comes back. A crack opens because the rock is failing, and
# that opening is cumulative and one-way.
#
# **That asymmetry is the premise of the entire two-stage design** ("a truck moves
# the accelerometer but does not strain the roof"), so the simulator has to be
# built around it rather than around whatever the displacement channel happens to
# be doing. Deriving the seam from raw displacement would make a truck pass look
# like a 30 mm crack — a simulator artefact that would then be *learned* by Model
# 1 in Phase 5 as "trucks crack the roof".
#
# So the seam has its own accumulator, opened only by profiles that represent a
# structural event (`_warning_creep`, `_critical_collapse`, and `rain_seepage`'s
# slow drift, which the plan's coherence table gives a small structural value).
# Environmental impulses pass ``seam=False`` and the seam does not move: the fine
# channel reports a real measurement of ~0 µm while the vibration channel reports
# a large count, which is exactly the coherence the plan's table predicts.
#
# The accumulator is deliberately **not** a field on ``NodeState``: it is not part
# of the wire contract, not a database column and not something the API publishes.
# Putting simulator scratch state inside a frozen contract dataclass is how a
# contract stops being frozen.

#: node_id → seam opening (mm). Module-level because there is one simulator per
#: process and it is scratch state, like the injection clock it sits beside.
_SEAM: dict[str, float] = {}

#: node_id → the idle clock, seconds. Only the crack nodes that are *not* being
#: driven by a profile use it, and only so their fine channel's noise term stays
#: deterministic (it is a function of ``t``). See ``_idle_crack_tick``.
_IDLE_T: dict[str, float] = {}

#: Resolution of the two channels' own instruments, in their own units.
#: The pot gives 100 mm over 4096 counts (H1.7); the load cell is calibrated to
#: ~1 µm per count (H2). Quantising the simulated channel to the sensor's real
#: resolution is not decoration — it is the reason the fine channel can cross its
#: threshold long before the coarse one, which is the entire demo.
SIM_POT_RES_MM = 100.0 / 4096.0
#: Noise floor on the fine channel, as one **standard deviation** in µm.
#:
#: Not ``random``: a replay must be reproducible, and a demo that differs run to
#: run is a demo that cannot be rehearsed. But not a *sine* either — see
#: ``_noise`` for why a periodic stand-in for noise is worse than none.
SIM_MICRO_NOISE_UM = 0.30
#: The value the fine channel transmits when it has railed (open gauge / dead
#: cell). Lives inside the contract's plausibility window — a rail is *reported*,
#: never a reason to drop the frame.
SIM_MICRO_RAIL_UM = 90_000.0
#: The opening a "collapse" profile drives, mm/s. Chosen so the drill crosses the
#: fine threshold in seconds and the coarse one (1.5 mm / 30 s) inside a minute.
SIM_SEAM_RATE_MM_S = 0.075

# ---------------------------------------------------------------------------
# Elastic vs plastic ground response (Phase 5 — the repair Phase 4 predicted)
# ---------------------------------------------------------------------------
#
# Until Phase 5, ``integrate()`` was a pure accumulator for **every** profile:
# ``disp += v*dt``, clamped to 0–100 mm. That is correct for rock failing — a
# crack is cumulative and one-way — but it is wrong for a loader rolling past,
# and the consequence was severe: an environmental profile drove displacement to
# the 100 mm clamp and *never came back*, so ``recovery_ratio`` read ≈ 0 for
# MACHINERY. A Model 1 trained on that corpus would have learned
# **"a loader pass is a structural collapse"** — the precise defect the Double AI
# exists to remove.
#
# Phase 4 recorded this and pinned it with a test written to fail when Phase 5
# arrived (`test_a_truck_pass_exceeds_the_level_threshold_on_the_bench`). It did.
# This is the repair.
#
# The physics is a forced, damped spring — the textbook response of elastic
# ground to a moving load:
#
#     disp  →  rest + COMPLIANCE × a      (lagged with time constant TAU)
#
# so displacement *follows the load* and returns to rest when the load passes.
# Structural profiles keep the accumulator, because rock does not spring back.
#
#: mm of deflection per m/s² of acceleration.
#:
#: **This number is a physical claim, and the first value was wrong.** At 2.0 a
#: 24 m/s² blast deflected the floor **48 mm** and a 4.6 m/s² loader 9 mm.
#: 48 mm is not "a distant detonation ringing the tunnel" — that is a floor on
#: its way to failing, and the rest of the system said so correctly: the sanity
#: gate (which rejects an imminent prediction only when displacement is under
#: 30 mm) let Model 2's short estimate through, the firewall escalated, and a
#: profile §5.5 describes as "full recovery, no alert" raised an imminent alarm.
#: The firewall was right about the number it was given; the number was wrong.
#:
#: At 0.5 the same two events deflect ~2.3 mm and ~12 mm — a floor *flexing*
#: under a dynamic load, which is what §5.5 actually describes — while the
#: plastic collapse still walks to 87–100 mm. That is a 40x separation, and it is
#: the separation the whole two-stage design rests on: an environmental event
#: moves the ground by millimetres and gives it back, a structural one moves it by
#: centimetres and keeps it.
SIM_ELASTIC_COMPLIANCE_MM_PER_MS2 = 0.5
#: Time constant of the spring's response, seconds. Shorter than the burst so the
#: deflection tracks the load rather than smearing past it.
SIM_ELASTIC_TAU_S = 0.45

#: node_id → displacement at the moment an elastic event began. Module-level and
#: scratch, like ``_SEAM``: it is not part of the wire contract.
_ELASTIC_REST: dict[str, float] = {}


def seam_open_mm(node_id: str) -> float:
    """The simulator's current seam opening for a node, mm."""
    return float(_SEAM.get(node_id, 0.0))


def reset_seam(node_id: str | None = None) -> None:
    """Zero the seam accumulator — called by ``SystemState.reset()``.

    A real seam does not heal, and nothing here pretends otherwise *within* a
    drill: this is the operator's Re-arm, which re-bases the whole picture
    (Phase 3's ``reset_crack_baseline`` does the same for the engine).
    """
    if node_id is None:
        _SEAM.clear()
        _IDLE_T.clear()
        _ELASTIC_REST.clear()
    else:
        _SEAM.pop(node_id, None)
        _IDLE_T.pop(node_id, None)
        _ELASTIC_REST.pop(node_id, None)


def _crack_channels(node, t: float) -> None:
    """Write the two crack channels for whichever node carries the column.

    Runs on *every* profile, structural or not, so the channel always reports a
    real measurement: ``micro ≈ 0`` during a truck pass is a measurement of "the
    seam did not open", which is a very different statement from a missing channel
    (and the coherence formula depends on the difference).
    """
    n_id = getattr(node, "node_id", None)
    if n_id not in CRACK_NODE_IDS:
        return
    opening = seam_open_mm(n_id)
    node.macro = round(round(opening / SIM_POT_RES_MM) * SIM_POT_RES_MM, 4)
    # The fine channel's noise floor, from `_noise` — **not** a sine. This line
    # used to read ``SIM_MICRO_NOISE_UM * math.sin(t * 7.3)``, and that one
    # expression was the root of two separate defects: at 2 Hz a 1.162 Hz ripple
    # aliases to 0.162 Hz and drove `micro_rate` to 5.81 µm/s on an idle node
    # (4x its own threshold), with a phase that depended on run history — so the
    # corpus and the live server disagreed about a node that had not moved.
    node.micro = round(opening * 1000.0 + _noise(t, _salt(n_id, 4), SIM_MICRO_NOISE_UM), 3)


def _idle_crack_tick(state, dt: float) -> None:
    """Keep the crack column live on a crack node that no profile is driving.

    On the rig this is not a special case at all: an undisturbed node's HX711
    keeps sampling, so it keeps reporting ``micro ≈ 0`` and a closed seam. On the
    bench the only thing that writes those channels is a profile, so an idle
    crack node would otherwise publish *nothing* — and the engine is right to
    read a missing channel as UNKNOWN. The effect was that a drill on N2 showed
    ``worst_stage = UNKNOWN`` for its first twenty seconds, because the untouched
    N4 outranked the real INITIATION; and a quiet dashboard claimed two blind
    crack columns when it had two quiet ones.

    ``micro ≈ 0`` is a measurement — "the seam did not open" — and it is the
    claim the coherence formula is built on, so the bench has to make it too.
    """
    for node in state.iter_nodes():
        n_id = getattr(node, "node_id", None)
        if n_id not in CRACK_NODE_IDS:
            continue
        if state.injections.get(n_id):
            continue                      # a profile is already writing it
        _IDLE_T[n_id] = _IDLE_T.get(n_id, 0.0) + dt
        _crack_channels(node, _IDLE_T[n_id])


#: Structural profiles may escalate an alert. Environmental ones may not.
VERDICT_STRUCTURAL = "STRUCTURAL"
VERDICT_ENVIRONMENTAL = "ENVIRONMENTAL"
VERDICT_FAULT = "FAULT"


def clamp(v: float, lo: float, hi: float) -> float:
    return lo if v < lo else (hi if v > hi else v)


def integrate(node, a: float, dt: float, seam: bool = False, t: float = 0.0,
              elastic: bool = False) -> None:
    """The shared motion integrator: acceleration → velocity → displacement → tilt.

    Every profile ends here, so an environmental and a structural injection
    produce the *same shape* of state change. If they did not, the AI would be
    distinguishing them by an artefact of the simulator instead of by physics.

    ``t`` is the profile's own clock, used for the fine channel's deterministic
    noise floor — the same replay has to produce the same numbers.

    ``seam=True`` additionally opens the crack column's seam — and only the
    profiles that represent rock failing pass it. A seismic impulse moves the
    block without opening the seam, which is the physical claim the whole
    two-stage design rests on; making the seam follow raw displacement would
    teach Model 1 the opposite lesson (see the seam-model note above).

    ``elastic=True`` selects the **recovering** response instead of the
    accumulator: displacement follows the load through a damped spring and
    returns to rest. Environmental profiles pass it (a loader deflects the ground
    and drives on); structural profiles do not (a crack does not close). This is
    the distinction Phase 5's Model 1 is trained to recognise, so the simulator
    has to *contain* it rather than average over it — see the elastic-model note
    above for what happened when it did not.
    """
    node.acceleration = a
    n_id_ = getattr(node, "node_id", None)

    if elastic:
        # **Elastic ground: the deflection tracks the load and returns.**
        # A loader or a blast shakes the portal; the rock deflects under it and
        # springs back when it has passed. Modelling that as an accumulator (as
        # every profile did before Phase 5) makes a truck indistinguishable from
        # a collapse by *construction*, which is a simulator artefact the
        # classifier would then faithfully learn.
        rest = _ELASTIC_REST.setdefault(n_id_, float(node.displacement_mm))
        prev = float(node.displacement_mm)
        target = rest + SIM_ELASTIC_COMPLIANCE_MM_PER_MS2 * a
        alpha = min(1.0, dt / SIM_ELASTIC_TAU_S) if SIM_ELASTIC_TAU_S > 0 else 1.0
        node.displacement_mm = clamp(prev + (target - prev) * alpha, 0.0, 100.0)
        # Velocity is the *observed* rate of deflection, not an integrator state:
        # `v/a` is the kinematic TTF, and it has to be the real slope.
        node.velocity = ((node.displacement_mm - prev) / dt) if dt > 0 else 0.0
    else:
        # **Plastic ground: rock failing is cumulative and one-way.** A crack does
        # not close because the load that opened it went away.
        node.velocity = clamp(node.velocity + a * dt, -0.4, 12.0)
        node.displacement_mm = clamp(node.displacement_mm + node.velocity * dt, 0.0, 100.0)

    node.tilt_x = node.tilt_x * 0.92 + a * 0.9

    n_id = getattr(node, "node_id", None)
    if n_id in CRACK_NODE_IDS:
        if seam:
            _SEAM[n_id] = _SEAM.get(n_id, 0.0) + SIM_SEAM_RATE_MM_S * dt
        _crack_channels(node, float(t))


#: Noise floor of each instrument, as one standard deviation, in its own units.
#:
#: **Why the simulator has to have a noise floor at all.** Without one a quiet
#: tunnel is *bit-exact* zero on acceleration, tilt and velocity: every node sits
#: at ``0.0``, the displacement delta over 60 s is ``0.0`` to the last bit, and a
#: classifier fitted on that learns splits like ``accel_max60 > 0`` — scoring
#: 0.999 on numbers no real instrument can produce, and failing the moment it met
#: an MPU-6050. The first corpus did exactly this.
#:
#: The magnitudes are the instruments' real noise floors: an MPU-6050 with its
#: DLPF open is a few 0.01 m/s² RMS, a 10-turn pot on a 12-bit ADC dithers by a
#: few hundredths of a degree, an HX711 with a 1 kg cell wanders by a fraction of
#: a count, and displacement is measured through a pot whose one count is
#: 100/4096 = 0.024 mm.
SIM_NOISE_ACC_STD = 0.02        # m/s²
SIM_NOISE_TILT_STD = 0.03       # degrees
SIM_NOISE_DISP_STD = 0.02       # mm

#: Correlation between consecutive noise samples (a one-pole low-pass). Real
#: instruments are bandwidth-limited; white noise would also make
#: ``zero_cross_rate`` saturate at Nyquist in a way no DLPF'd sensor does.
_NOISE_RHO = 0.7
_NOISE_TAPS = 6

#: **The quantum the noise advances on**, in Hz — and it must match the rate the
#: channel is *sampled* at, or the taps are not a filter at all.
#:
#: The first attempt indexed the hash by centiseconds and summed six consecutive
#: integers. Because a model sample is 0.5 s apart, those six integers were 50
#: apart, so the six taps drew six *independent* values and the "low-pass" was
#: pure white noise: measured lag-1 autocorrelation -0.055 where 0.70 was
#: intended, and single-sample steps four times larger than the standard
#: deviation. The filter only exists if the taps span one sample interval.
_NOISE_HZ = 2.0


def _u(i: int, salt: int) -> float:
    """Deterministic white draw in ``[-1, 1]`` from an integer tick.

    An integer hash — multiply/xor-shift mixing, the same shape as xxHash's
    finaliser — rather than ``random``. Two properties are needed and this is
    the smallest thing that has both:

    * **Deterministic across processes.** ``hash()`` on a string is salted per
      process (``PYTHONHASHSEED``), so using it here would silently destroy the
      reproducibility this function exists to provide. Arithmetic on ints is not
      salted and gives identical results in every interpreter.
    * **Aperiodic.** See ``_noise``.
    """
    x = (i * 0x9E3779B97F4A7C15 + salt * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 30
    x = (x * 0xBF58476D1CE4E5B9) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 27
    x = (x * 0x94D049BB133111EB) & 0xFFFFFFFFFFFFFFFF
    x ^= x >> 31
    return (x / 0xFFFFFFFFFFFFFFFF) * 2.0 - 1.0


def _noise(t: float, salt: int, std: float) -> float:
    """Instrument noise: **aperiodic**, deterministic, band-limited. Pure in ``t``.

    **A periodic function is not a substitute for noise, and this is the defect
    that taught us so.** The first version used three incommensurate sines and the
    micro channel used a single ``sin(t * 7.3)``. Both were "deterministic noise"
    in name only, and both broke the features they were meant to make realistic:

    * ``t*7.3`` is **1.162 Hz, sampled at 2 Hz. Nyquist is 1 Hz.** So the fine
      channel's ripple *aliased* to 0.162 Hz — a slow, entirely fictitious drift.
      ``micro_rate`` is a median-of-3 difference taken 5 s apart, which samples
      that aliased wave at two different phases, and it read **5.81 µm/s against a
      1.5 µm/s threshold**: an idle, undisturbed node appearing to open a seam.
      Worse, the alias's phase depends on how long the *process* has been running,
      because the micro channel's clock is an accumulator — so the training corpus
      and the live server saw *different phases of the same fictitious wave*, and
      model verdicts diverged between them on identical physics. That was the
      defect that took a whole evening to find (PF-D2).
    * Three sines are periodic too, so ``impulse_periodicity`` — the feature whose
      entire job is to tell a periodic engine from an aperiodic rock — saturated at
      **1.0000 on silent windows**. The noise was shouting "perfectly periodic" at
      every quiet tunnel (PF-D1).

    So: an integer hash has no period at all, and a six-tap exponential sum of
    consecutive draws is the same one-pole low-pass a real DLPF applies, computed
    as a pure function of ``t`` so replays stay exact. ``std`` is the standard
    deviation of the result, in the channel's own units.
    """
    # floor(x + 0.5), not round(x): Python's round() is banker's rounding, so
    # round(0.5) is 0 and round(1.5) is 2 — the ticks would come out unevenly
    # spaced and the filter would change shape with the phase of t.
    i = int(math.floor(t * _NOISE_HZ + 0.5))
    acc = 0.0
    wsq = 0.0
    w = 1.0
    for k in range(_NOISE_TAPS):
        acc += w * _u(i - k, salt)
        wsq += w * w
        w *= _NOISE_RHO
    # Normalise so the output's variance is `std²` regardless of the tap count:
    # _u has variance 1/3, so sqrt(3 / Σw²) restores unit variance before scaling.
    if wsq <= 0.0:                                   # pragma: no cover
        return 0.0
    return std * acc * math.sqrt(3.0 / wsq)


def _salt(n_id: str | None, channel: int) -> int:
    """A stable per-node, per-channel salt.

    A CRC of the node id rather than ``hash()`` — see ``_u``. Different channels
    of the same node must not share a salt, or the accelerometer and the tilt pot
    would report *correlated* noise, which would make ``tilt_acc_coupling``
    measure the simulator's bookkeeping instead of the ground.
    """
    base = zlib.crc32((n_id or "").encode()) & 0xFFFFFFFF
    return (base * 0x9E3779B1 + channel * 0x85EBCA77) & 0xFFFFFFFFFFFFFFFF


def measure(disp_mm: float, tilt_deg: float, acc_ms2: float, t: float,
            n_id: str | None) -> tuple[float, float, float]:
    """Add each instrument's noise floor to a *reading*. Plant in, measurement out.

    **Why this is a separate function and not a line inside** ``integrate``.
    Three reasons, and the first two were live bugs:

    1. **Coverage.** ``integrate`` only runs for a node that has an active
       injection. Noise added there left every *quiet* node at exactly ``0.0`` on
       acceleration, tilt and displacement — the very degeneracy the noise exists
       to remove — because the nodes that most need a noise floor are the ones
       nothing is being injected into.
    2. **It must not reach the plant.** ``displacement_mm`` and ``tilt_x`` are
       *integrated state*. Adding a fresh noise sample to them every tick does not
       add noise, it **integrates** it: over a 150 s run that is a 300-step random
       walk drifting the node's real position by a third of a millimetre, and the
       drift would be indistinguishable from slow creep — the exact signal the
       crack detector is looking for. Noise belongs on the reading, never on the
       state that the next tick integrates.
    3. **It is what the hardware does.** The sensors are noisy; the rock is not.
       A simulator that conflates them cannot be used to argue about either.

    So callers apply it at the moment a value is *published* — the corpus
    generator when it fills a model window. The live path needs nothing: real
    instruments bring their own noise.

    Deterministic on purpose (see ``SIM_NOISE_ACC_MS2``). ``velocity`` is
    deliberately never noised: it is the slope ``v/a`` is taken from, and
    differentiation amplifies noise without limit, so a noisy slope would make the
    kinematics fallback jitter by whole seconds.
    """
    return (
        clamp(float(disp_mm or 0.0) + _noise(t, _salt(n_id, 1), SIM_NOISE_DISP_STD),
              0.0, 100.0),
        float(tilt_deg or 0.0) + _noise(t, _salt(n_id, 2), SIM_NOISE_TILT_STD),
        float(acc_ms2 or 0.0) + _noise(t, _salt(n_id, 3), SIM_NOISE_ACC_STD),
    )


def settle(node) -> None:
    """Return a node to rest without zeroing its displacement.

    A node that has actually moved stays moved — re-zeroing the position would
    erase the evidence of the event the operator just watched.
    """
    node.velocity *= 0.3
    node.acceleration = 0.0


# ---------------------------------------------------------------------------
# The seven profiles
# ---------------------------------------------------------------------------

@dataclass
class ScenarioProfile:
    """The declarative half of a profile — §5.5's table, in code (§5.8).

    These five fields say *what the physics is* without saying how to execute it,
    and every one of them is falsifiable against the profile body below:
    ``test_scenario_profiles.py`` checks the declarations against the behaviour
    rather than trusting them, because a descriptor that drifts from the function
    it describes is worse than no descriptor — it is a second, staler opinion
    about the same thing.

    ``accel_fn``
        The forcing function ``a(t)`` in m/s², or ``None`` when the profile adds
        no acceleration and only manipulates existing state (``nominal`` decays,
        ``sensor_fault`` pins).
    ``disp_gain``
        Displacement coupling, in exactly three honest values:

        * ``0.0`` — the profile never touches the displacement channel;
        * ``1.0`` — it drives displacement through ``integrate``;
        * ``-1.0`` — it **overwrites** displacement (the railed gauge). Kept in
          the same field because the question the UI asks is "is this node's
          displacement telling me about the ground or about the sensor?", and a
          separate boolean would let those two answers disagree.
    ``duration_s``
        The console's advertised length — what the progress ring counts against.
    ``recovers``
        ``True`` if the profile ends on its own and settles the node;
        ``False`` if it runs until an operator resets (``critical_collapse``).
    ``strain_coupled``
        ``True`` if the profile opens the seam, i.e. whether the fine crack
        channel is expected to move *because the ground moved*. This is the
        single field that separates "structural" from "noisy" in the simulator,
        and it is why ``machinery_pass`` — the loudest profile here — is not.
    """

    accel_fn: object = None           # Callable[[float], float] | None
    disp_gain: float = 0.0
    duration_s: float = 0.0
    recovers: bool = False
    strain_coupled: bool = False


@dataclass
class Scenario(ScenarioProfile):
    """One injection profile: the declaration above **plus** how to run it.

    ``advance`` mutates the node for one tick and returns ``True`` while the
    profile is still running. Returning ``False`` ends it (the console then
    applies ``on_end`` and clears the injection).

    ``Scenario`` *is a* ``ScenarioProfile`` — it inherits the five declarative
    fields rather than repeating them — so §5.8's ``SCENARIOS: dict[str,
    ScenarioProfile]`` is satisfied by construction and ``isinstance`` says so,
    while the runner keeps the callables it needs on the same object.
    """

    name: str = ""
    verdict: str = VERDICT_ENVIRONMENTAL
    severity: str = "warning"         # what the console arms: warning | critical | fault
    advance: object = None            # Callable[[node, float, float], bool]
    on_end: object | None = None      # Callable[[node], None]
    note: str = ""

    def describe(self) -> dict:
        return {"name": self.name, "verdict": self.verdict,
                "duration_s": self.duration_s, "severity": self.severity,
                "note": self.note, "recovers": self.recovers,
                "strain_coupled": self.strain_coupled}


# -- 1. warning_creep — the shipped "warning" curve, unchanged ---------------

def _warning_creep(node, t: float, dt: float) -> bool:
    if t > 46:
        return False
    taper = 1.0 if t < 34 else max(0.0, 1 - (t - 34) / 8)
    a = 0.95 * (1 - math.exp(-t / 1.6)) * math.exp(-t / 10) * taper
    # **Low, not zero.** §5.5's signature for initiation is "``micro`` rate crosses
    # first, ``vib`` low" — and *low* has to mean a small positive count, because
    # a propagating crack in rock does emit micro-seismic events. Leaving this at
    # 0 made the soil channel read exactly like an undisturbed tunnel, so
    # ``n_active_anomalous`` could never count a structural node and the corpus
    # taught the classifier that "no impacts" is evidence for a crack.
    node.vib = int(round(1.5 * (1 - math.exp(-t / 6.0))))
    # **Structural**: the seam opens as the ground creeps. This is the profile the
    # crack column exists for — the fine channel crosses its threshold within a
    # few seconds of onset, the coarse one needs 1.5 mm of opening (≈20 s at this
    # profile's rate), so the two events arrive in the order the story predicts.
    integrate(node, a, dt, seam=True, t=t)
    return True


def _warning_creep_end(node) -> None:
    settle(node)
    node.status = "NOMINAL"


# -- 2. critical_collapse — the shipped "critical" curve, unchanged ----------

def _critical_collapse(node, t: float, dt: float) -> bool:
    a = 3.6 * (1 - math.exp(-t / 1.4)) * (1 + 0.12 * math.sin(t * 2.1))
    # Structural: this one ends with the seam past the critical opening, which is
    # what takes the stage from PROPAGATION to CRITICAL and the node to WARNING.
    #
    # **Vibration rises with it** — §5.5's tertiary signature is "everything rises
    # together". Rock failing under load is loud in the soil channel; the contrast
    # the classifier needs is not "impact vs no impact" (a truck wins that) but
    # "impacts *with* an opening seam vs impacts *without* one", which is the
    # cross-sensor coherence claim and needs both channels to be live here.
    node.vib = int(round(4.0 + 8.0 * (1 - math.exp(-t / 6.0))))
    integrate(node, a, dt, seam=True, t=t)
    return True


# -- 3. machinery_pass — a large disturbance with no memory ------------------

def _machinery_pass(node, t: float, dt: float) -> bool:
    """A loaded hauler crossing: an **impulse train**, then nothing.

    §5.5 specifies "impulse train 0.5–4 s, high ``vib``/``acc``, ``micro`` flat,
    ``macro`` unchanged, full recovery, single node" — and the first version of
    this profile did not honour its own spec. It was a *single* Gaussian burst at
    t = 2.2 s, which is a smooth unimodal pulse: its autocorrelation decays
    monotonically, so ``impulse_periodicity`` correctly returned ~0 on it and the
    feature looked broken when the profile was. An axle does not strike once; it
    strikes every revolution.

    **The spacing is 2.0 s and that number is not free.** §5.0 records the caveat
    that ``impulse_periodicity`` is computed on a 2 Hz series, so its Nyquist is
    1 Hz: an impact repetition faster than once per second aliases and is simply
    not observable at this sample rate. A train spaced 0.5 s apart — the fast end
    of §5.5's own range — would have been invisible for a reason that has nothing
    to do with machinery. 2.0 s sits inside the specified range *and* below
    Nyquist, so the feature can actually see what the physics put there.

    The peak deliberately exceeds ``ACC_CRITICAL``. That is the point — a
    threshold-only system alarms on this, and the low displacement accumulated
    over the burst is what a pattern model uses to say *environmental*.
    """
    if t > 9.0:
        return False
    spacing = 2.0
    first = 1.5
    a = 0.0
    impacts = 0.0
    k = 0
    while True:
        centre = first + k * spacing
        if centre > 9.0:
            break
        pulse = math.exp(-((t - centre) ** 2) / (2 * 0.15 ** 2))
        a += 4.6 * pulse
        impacts += 11.0 * pulse
        k += 1
    # **Environmental, and the seam is the tell.** ``seam=False``: the loader
    # shakes the tunnel and the seam does not open. The vibration channel counts
    # impacts while the fine channel measures ~0 µm of opening, and
    # ``cross_sensor_coherence`` on this profile is supposed to come out low
    # (< 0.3, §3.7) — the machine-readable form of "a truck is not a collapse".
    integrate(node, a, dt, seam=False, t=t, elastic=True)
    node.vib = int(round(impacts))               # counted impacts, not a level
    return True


def _machinery_pass_end(node) -> None:
    settle(node)
    node.vib = 0
    node.status = "NOMINAL"


# -- 4. blast — one impulse, then silence ------------------------------------

def _blast(node, t: float, dt: float) -> bool:
    if t > 6.0:
        return False
    a = 24.0 * math.exp(-t / 0.30)
    # Environmental: one impulse, no residual. A blast nearby cracks nothing on
    # this rig — it just rings everything for a second. Elastic, so the ground
    # returns: a blast that left the ground permanently displaced would model a
    # rockburst, not a distant detonation, and §5.5 requires "full recovery".
    integrate(node, a, dt, seam=False, t=t, elastic=True)
    node.vib = int(round(60 * math.exp(-t / 0.5)))
    return True


def _blast_end(node) -> None:
    settle(node)
    node.vib = 0


# -- 5. rain_seepage — slow, low-amplitude, wet-season -----------------------

def _rain_seepage(node, t: float, dt: float) -> bool:
    """Stays *under* ACC_WARNING by design — it is a drift, not an event.

    A monitoring system that only looks at acceleration is blind to this one,
    which is why the crack-mouth-opening channel exists.
    """
    if t > 150.0:
        return False
    a = 0.09 * (1 - math.exp(-t / 20.0)) * math.sin(t / 9.0)
    # Slow seepage *does* strain the seam a little — the plan's coherence table
    # gives this row structural 0.10 against non-specific 0.45 (a slow tilt drift
    # with no real strain), and the small seam opening here is what produces it.
    # The explicit micro/macro arithmetic this profile used to do is gone: the
    # channels now come from the seam model like every other profile's, so there
    # is one coupling in the file instead of seven versions of it.
    integrate(node, a, dt, seam=True, t=t, elastic=True)
    return True


def _rain_seepage_end(node) -> None:
    settle(node)


# -- 6. sensor_fault — pinned at a rail --------------------------------------

def _sensor_fault(node, t: float, dt: float) -> bool:
    """A dead sensor, not a moving ground.

    The displacement is pinned and the measured acceleration goes to zero. A
    system that trusts the number would read *stable*; the flags are what tell
    the truth, and the forecast must be blocked rather than wrong.
    """
    if t > 30.0:
        return False
    node.displacement_mm = 0.0        # rail: the pot has lost its wiper
    node.acceleration = 0.0
    node.velocity = 0.0
    # **A railed channel reports its rail, never a zero.** §3.1: *"an open gauge
    # must read as fault, not as zero strain — these look identical in the data
    # otherwise."* So the fine channel transmits the rail value (90 000 µm, the
    # ADC's full scale through the calibration) and the flag says what it is;
    # zero would be a measurement that says "the seam is shut", which is the
    # single most reassuring lie this instrument can tell.
    node.micro = SIM_MICRO_RAIL_UM
    node.macro = 0.0
    node.flags |= 1 << 0              # micro saturated (H2.4, flags bit 0)
    node.flags |= 1 << 1              # macro at rail
    return True


def _sensor_fault_end(node) -> None:
    # Bit 0 (micro saturated) comes off with the others: a recovered channel is
    # recovered, and the engine announces the recovery on that edge.
    node.flags &= ~((1 << 0) | (1 << 1) | (1 << 2))
    settle(node)


# -- 7. nominal --------------------------------------------------------------

def _nominal(node, t: float, dt: float) -> bool:
    if t > 3.0:
        return False
    node.acceleration *= 0.8
    node.velocity *= 0.8
    return True


def _creep_a(t: float) -> float:
    """``warning_creep``'s forcing function, extracted so the declaration below
    quotes the same expression the profile runs instead of a copy of it."""
    if t > 34:
        return 0.95 * (1 - math.exp(-t / 1.6)) * math.exp(-t / 10) * max(0.0, 1 - (t - 34) / 8)
    return 0.95 * (1 - math.exp(-t / 1.6)) * math.exp(-t / 10)


def _collapse_a(t: float) -> float:
    """``critical_collapse``'s forcing function."""
    return 3.6 * (1 - math.exp(-t / 1.4)) * (1 + 0.12 * math.sin(t * 2.1))


def _rain_a(t: float) -> float:
    """``rain_seepage``'s forcing function — stays under ``ACC_WARNING``."""
    return 0.09 * (1 - math.exp(-t / 20.0)) * math.sin(t / 9.0)


def _blast_a(t: float) -> float:
    """``blast``'s forcing function — one impulse, exponential tail."""
    return 24.0 * math.exp(-t / 0.30)


#: §5.8 — the seven profiles. ``duration_s`` is the console's advertised length;
#: where a profile body ends earlier than that (``machinery_pass`` stops at 9 s
#: against a 12 s advertisement) the **body** is authoritative and the ring is
#: documented as approximate rather than corrected here, because changing the
#: number would change the armed window and every corpus row built from it.
SCENARIOS: dict[str, Scenario] = {
    "nominal": Scenario(
        name="nominal", verdict=VERDICT_ENVIRONMENTAL, severity="warning",
        duration_s=3.0, advance=_nominal, recovers=True,
        accel_fn=None, disp_gain=0.0, strain_coupled=False,
        note="decays any residual motion; nothing to see"),
    "warning_creep": Scenario(
        name="warning_creep", verdict=VERDICT_STRUCTURAL, severity="warning",
        duration_s=46.0, advance=_warning_creep, on_end=_warning_creep_end,
        recovers=True, accel_fn=_creep_a, disp_gain=1.0, strain_coupled=True,
        note="sustained creep — the shipped warning curve, unchanged"),
    "critical_collapse": Scenario(
        name="critical_collapse", verdict=VERDICT_STRUCTURAL, severity="critical",
        duration_s=0.0, advance=_critical_collapse,
        recovers=False, accel_fn=_collapse_a, disp_gain=1.0, strain_coupled=True,
        note="terminal acceleration; runs until reset"),
    "machinery_pass": Scenario(
        name="machinery_pass", verdict=VERDICT_ENVIRONMENTAL, severity="warning",
        duration_s=12.0, advance=_machinery_pass, on_end=_machinery_pass_end,
        recovers=True, accel_fn=None, disp_gain=1.0, strain_coupled=False,
        note="transient burst — exceeds ACC_CRITICAL and is still not a collapse"),
    "blast": Scenario(
        name="blast", verdict=VERDICT_ENVIRONMENTAL, severity="critical",
        duration_s=6.0, advance=_blast, on_end=_blast_end,
        recovers=True, accel_fn=_blast_a, disp_gain=1.0, strain_coupled=False,
        note="single impulse with a decay tail"),
    "rain_seepage": Scenario(
        name="rain_seepage", verdict=VERDICT_ENVIRONMENTAL, severity="warning",
        duration_s=150.0, advance=_rain_seepage, on_end=_rain_seepage_end,
        recovers=True, accel_fn=_rain_a, disp_gain=1.0, strain_coupled=True,
        note="stays under ACC_WARNING — only the micro channel sees it"),
    "sensor_fault": Scenario(
        name="sensor_fault", verdict=VERDICT_FAULT, severity="warning",
        duration_s=30.0, advance=_sensor_fault, on_end=_sensor_fault_end,
        recovers=True, accel_fn=None, disp_gain=-1.0, strain_coupled=False,
        note="pinned at a rail; must block a forecast, never fake a quiet one"),
}

#: The runbook's existing curl bodies. Never remove a name from this table.
SEVERITY_ALIASES = {
    "warning": "warning_creep",
    "critical": "critical_collapse",
    "fault": "sensor_fault",
}


def resolve(profile: str | None, severity: str | None = None) -> Scenario | None:
    """Profile name or legacy severity → a ``Scenario``. ``None`` if unknown."""
    name = (profile or "").strip()
    if name not in SCENARIOS and severity:
        name = SEVERITY_ALIASES.get(str(severity).strip().lower(), "")
    return SCENARIOS.get(name)


def arm(state, node_id: str, profile: str | None = None,
        severity: str | None = None) -> Scenario | None:
    """Arm a profile on one node. Returns the scenario, or ``None`` if unknown."""
    scenario = resolve(profile, severity)
    if scenario is None or node_id not in state.nodes:
        return None
    state.injections[node_id] = scenario.name
    state.injection_t[node_id] = 0.0
    return scenario


# ---------------------------------------------------------------------------
# §5.8 — the console's verbs
# ---------------------------------------------------------------------------

def start_scenario(state, nid: str, name: str) -> bool:
    """Arm a profile on one node. ``True`` if it armed.

    Returns a boolean rather than the scenario because §5.8 specifies
    ``-> bool`` and because the caller that matters — the trigger endpoint — has
    to answer a client, and "did this take effect?" is the only question a client
    can act on. A return of the scenario object would let a caller check truthiness
    by accident and think a *refusal* succeeded.
    """
    return arm(state, nid, name) is not None


def stop_scenario(state, nid: str) -> None:
    """Disarm one node and settle it.

    Runs the profile's own ``on_end`` when it has one, so an operator's stop and
    a natural expiry leave the node in the *same* state. Two different cleanups
    for the same event is how a stopped ``machinery_pass`` would keep counting
    impacts until the next tick overwrote them.
    """
    scenario = SCENARIOS.get(state.injections.get(nid) or "")
    state.injections[nid] = None
    state.injection_t[nid] = 0.0
    node = state.nodes.get(nid)
    if node is None:
        return
    if scenario is not None and scenario.on_end is not None:
        scenario.on_end(node)
    else:
        settle(node)


def scenario_progress(state, nid: str) -> float:
    """``0.0–1.0`` through the armed profile, for the console's progress ring.

    Zero when nothing is armed, and **also** zero for an open-ended profile
    (``critical_collapse`` advertises ``duration_s = 0.0``). That is deliberate:
    a collapse has no fraction to show, and inventing one would put a
    reassuring-looking progress bar next to a number — Model 2's interval — that
    is already trying to say "this may not finish". The ring stays empty and the
    TTF band carries the message.
    """
    name = state.injections.get(nid)
    scenario = SCENARIOS.get(name or "")
    if scenario is None or scenario.duration_s <= 0.0:
        return 0.0
    t = float(state.injection_t.get(nid) or 0.0)
    return max(0.0, min(1.0, t / float(scenario.duration_s)))


def apply_injections(state, dt: float) -> None:
    """Advance every armed scenario by the **measured** dt (D14).

    ``dt`` comes from the caller's own clock, not from an assumed 50 ms tick.
    """
    dt = max(0.0, float(dt))
    for n_id in list(state.injections.keys()):
        name = state.injections.get(n_id)
        if not name:
            continue
        scenario = SCENARIOS.get(name)
        if scenario is None:
            state.injections[n_id] = None
            state.injection_t[n_id] = 0.0
            continue

        state.injection_t[n_id] += dt
        t = state.injection_t[n_id]
        node = state.nodes.get(n_id)
        if node is None:
            continue

        if not scenario.advance(node, t, dt):
            if scenario.on_end is not None:
                scenario.on_end(node)
            state.injections[n_id] = None
            state.injection_t[n_id] = 0.0
            if scenario.name == "warning_creep":
                # The shipped event text, verbatim — the deck and the runbook
                # both quote it.
                state.note_event("NOMINAL",
                                 f"{n_id.upper()} creep settled — node back to NOMINAL.")
            else:
                state.note_event("NOMINAL",
                                 f"{n_id.upper()} {scenario.name} injection complete.")
            continue

        state.update_peak(n_id)

    _idle_crack_tick(state, dt)
    state.update_status_logic()


__all__ = [
    "SCENARIOS", "SEVERITY_ALIASES", "Scenario", "ScenarioProfile",
    "arm", "resolve", "start_scenario", "stop_scenario", "scenario_progress",
    "apply_injections",
    "integrate", "settle", "clamp",
    "VERDICT_STRUCTURAL", "VERDICT_ENVIRONMENTAL", "VERDICT_FAULT",
    "seam_open_mm", "reset_seam",
    "measure",
    "SIM_NOISE_ACC_STD", "SIM_NOISE_TILT_STD", "SIM_NOISE_DISP_STD",
    "SIM_POT_RES_MM", "SIM_MICRO_NOISE_UM", "SIM_MICRO_RAIL_UM", "SIM_SEAM_RATE_MM_S",
]
