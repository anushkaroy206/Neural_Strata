"""
B11 — the calibrated kinematic fallback, and the other half of defect D2.
================================================================================

**What was wrong.** `models/kinematic_calibration.json` has shipped since Phase 0
and nothing has ever read it. The live fallback was `ttf = v / a` — "time for
velocity to reach zero if deceleration is constant". §6.1 says what that number
is worth: *a plausible-sounding number that is not a time-to-failure.* The
calibration file and the formula that used it were two different quantities
sitting in the same repository, and the orphan was the file.

**What this module is.** The physics rung of the fallback chain, in the form the
plan specifies:

    cascade → M2 (fresh) → Fukuzono 1/v → v/a → "N/A"

- `fukuzono_ttf_live(buf)` — the **same 1/v least-squares estimator** that built
  the training feature `ttf_fuk`, run on the live window. This matters more than
  it looks: feature and fallback sharing one estimator is the difference between
  a physics check and a second opinion with its own arithmetic. The parity test
  in `tests/test_kinematic.py` pins them together at 1 %.
- `kinematic_ttf_accel(v, a)` — the legacy ratio, **demoted to last resort**.
  It survives for one case: a node with motion but not yet a full 60 s window,
  where there is nothing better and `None` would mean no number at all.

**What it is not.** Not a model: it reads no artifact, learns nothing, and holds
no state. Not an authority: it feeds `CascadeDecision`, and the cascade's
firewall decides what any TTF is allowed to do. A fallback that could raise an
alarm on its own would be a third author of the alarm state, and the whole point
of the firewall is that there are exactly two.

**The sign of the fit, because it is easy to get backwards.** Fukuzono's law has
`v → ∞` at failure, so `1/v` falls towards zero on the approach and the
least-squares intercept with the time axis *is* the horizon. Concretely: inside
the window `x = t_j − t_i ≤ 0`, so an accelerating node — velocity growing, `1/v`
falling toward now — gives a **negative** slope, which is why
`fukuzono_ttf_live` accepts only `a_fit < −1e-12` and reports `−b/a`.

The consequence is worth stating plainly, because it shapes the chain: **a node
that is steady or slowing has no horizon to project, and the estimator returns
`None`** — correctly. It is not "the node is fine"; it is "this estimator has
nothing to say". The chain then falls through to `v/a`, which will happily answer
for a steady node, and that is the rung most able to mislead: a large `a` against
a small `v` collapses to a few seconds on a node about to spring back. That is
exactly the reading `cascade.T_sanity` exists to refuse, and `kinematic_ttf`
below applies the same gate itself rather than trusting its caller to.

This module reproduces the training estimator **exactly**, on purpose. It is the
estimator the shipped model was trained against; changing the convention here
would silently decouple the fallback from `ttf_fuk` and from everything
downstream of it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

import config

#: Window the 1/v fit is taken over, and the lookback each velocity uses.
FUK_WINDOW_S = 30.0
FUK_LOOKBACK_S = 10.0
#: Minimum samples in the fit, and where the fit may start. Both mirror
#: ``engine/features.py``: the same eight points, the same "21 samples in" floor
#: that the training extractor applies at the fixed 2 Hz cadence.
FUK_MIN_POINTS = 8
FUK_FIRST_INDEX = 21
#: Tolerance on the 10 s lookback, identical to the training extractor's.
FUK_LOOKBACK_TOL_S = 0.5

#: Keys `kinematic_calibration.json` must carry. A calibration missing any of
#: them is not partially usable — it is a file that disagrees with the code.
REQUIRED_KEYS = (
    "alpha_exponent",
    "a_coefficient",
    "breach_limit_mm",
    "steady_creep_velocity_threshold_mm_s",
    "stable_ttf_clamp_s",
)

#: Used **only** when the file is absent. The plan ships the calibration as
#: ``required: false``, so the fallback has to keep working without it — but it
#: must not pretend the file was there: `calibration()` returns ``None`` and
#: `kinematic_source()` says so.
DEFAULTS: dict[str, float] = {
    "alpha_exponent": 1.95,
    "a_coefficient": 0.082,
    "breach_limit_mm": 100.0,
    "steady_creep_velocity_threshold_mm_s": 0.05,
    "stable_ttf_clamp_s": 3600.0,
}

_CALIB_CACHE: dict[str, Any] | None = None
_CALIB_LOADED = False


# ---------------------------------------------------------------------------
# The calibration file
# ---------------------------------------------------------------------------
def load_calibration(path: str | Path | None = None) -> dict[str, Any]:
    """Read and validate ``kinematic_calibration.json``.

    Raises ``ValueError`` — naming the key — rather than returning a partial
    dict. A calibration that is read halfway is worse than one that is refused:
    the caller can fall back to `DEFAULTS`, but it cannot un-mix two laws.
    """
    p = Path(path) if path is not None else config.MODEL_FILES["kinematic_calibration"]
    if not p.exists():
        raise FileNotFoundError(f"kinematic calibration not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{p.name} must contain a JSON object, got {type(data).__name__}")

    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ValueError(f"{p.name} is missing required key(s): {', '.join(missing)}")
    for key in REQUIRED_KEYS:
        value = data[key]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(
                f"{p.name}: {key} must be a number, got {value!r} "
                f"({type(value).__name__})")
        if not math.isfinite(float(value)):
            raise ValueError(f"{p.name}: {key} must be finite, got {value!r}")

    if float(data["alpha_exponent"]) <= 1.0:
        raise ValueError(
            f"{p.name}: alpha_exponent must be > 1 for the Voight law to have a "
            f"finite horizon (got {data['alpha_exponent']})")
    if float(data["a_coefficient"]) <= 0.0:
        raise ValueError(f"{p.name}: a_coefficient must be > 0 (got {data['a_coefficient']})")
    if float(data["stable_ttf_clamp_s"]) <= 0.0:
        raise ValueError(f"{p.name}: stable_ttf_clamp_s must be > 0")
    return dict(data)


def calibration() -> dict[str, Any] | None:
    """The loaded calibration, or ``None`` when the file is absent or invalid.

    Cached after the first call: this is read on the inference path, not per
    window. A `RuntimeError` is deliberately **not** raised on absence — see the
    module docstring on `required: false`.
    """
    global _CALIB_CACHE, _CALIB_LOADED
    if not _CALIB_LOADED:
        try:
            _CALIB_CACHE = load_calibration()
        except (FileNotFoundError, ValueError, OSError):
            _CALIB_CACHE = None
        _CALIB_LOADED = True
    return _CALIB_CACHE


def reset() -> None:
    """Drop the cache — for tests, and for a calibration edit at runtime."""
    global _CALIB_CACHE, _CALIB_LOADED
    _CALIB_CACHE = None
    _CALIB_LOADED = False


def _law(calib: dict[str, Any] | None = None) -> dict[str, Any]:
    """The constants to compute with: the file when it is there, else `DEFAULTS`."""
    return calib if calib is not None else (calibration() or DEFAULTS)


def calibration_info() -> dict[str, Any]:
    """What `/api/ai` and the runbook need to say about this rung, in one dict."""
    loaded = calibration()
    law = loaded or DEFAULTS
    return {
        "file": config.MODEL_FILES["kinematic_calibration"].name,
        "loaded": loaded is not None,
        "source": "file" if loaded is not None else "defaults",
        "method": (loaded or {}).get("method", "Fukuzono-Voight Inverse Velocity Kinematic Law"),
        "alpha": float(law["alpha_exponent"]),
        "a_coefficient": float(law["a_coefficient"]),
        "breach_limit_mm": float(law["breach_limit_mm"]),
        "clamp_s": float(law["stable_ttf_clamp_s"]),
    }


# ---------------------------------------------------------------------------
# The three estimators
# ---------------------------------------------------------------------------
def clamp_ttf(ttf: float, calib: dict[str, Any] | None = None) -> float:
    """Clamp a TTF to ``[0, stable_ttf_clamp_s]``.

    A NaN or infinite estimate clamps to the **stable** end, never to zero: not
    knowing when something fails is not the same as it failing now, and the
    firewall treats a short TTF as urgent.
    """
    clamp = float(_law(calib)["stable_ttf_clamp_s"])
    if ttf is None or not math.isfinite(float(ttf)):
        return clamp
    return float(min(clamp, max(0.0, float(ttf))))


def kinematic_ttf_accel(accel: float, vel: float) -> float | None:
    """The legacy ``v/a``, **last resort only**.

    Kept because it is the only estimator that works on a node with motion but
    no full window. Returns ``None`` when it cannot be computed — a zero or
    negative acceleration, a non-positive velocity — rather than a number the
    caller would have to know to distrust.
    """
    try:
        a, v = float(accel), float(vel)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(a) and math.isfinite(v)):
        return None
    if v <= 0.0 or a <= 0.0:
        return None
    return v / a


def fukuzono_ttf(disp: float, v: float, calib: dict[str, Any] | None = None) -> float:
    """Analytic TTF from the calibrated Voight law — no window needed.

    Voight's form of Fukuzono's law integrates to ``1/v ∝ (t_f − t)^{1/(α−1)}``,
    so a measured ``v`` gives a horizon directly:

        t_f − t = (1/v)^{α−1} / (A (α−1))

    A second, independent route to the same failure is the breach limit at the
    current velocity — ``(breach − disp) / v``. **The earlier of the two
    governs**: the ground fails when the first of its two clocks runs out, and
    taking the later one would report a safe horizon to a node that is about to
    reach the limit at a constant rate.

    Below the steady-creep velocity threshold there is no failure to project, and
    the stable clamp is returned — the same answer the live estimator gives by
    refusing to fit.
    """
    law = _law(calib)
    breach = float(law["breach_limit_mm"])
    v = float(v)
    disp = float(disp)
    if disp >= breach:
        return 0.0
    if not math.isfinite(v) or v <= float(law["steady_creep_velocity_threshold_mm_s"]):
        return clamp_ttf(float("inf"), law)

    alpha = float(law["alpha_exponent"])
    a_coef = float(law["a_coefficient"])
    horizon_law = (1.0 / v) ** (alpha - 1.0) / (a_coef * (alpha - 1.0))
    horizon_disp = (breach - disp) / v
    return clamp_ttf(min(horizon_law, horizon_disp), law)


def fukuzono_ttf_live(
    buf: Sequence[tuple[float, float, float, float]] | Sequence[Sequence[float]],
    calib: dict[str, Any] | None = None,
) -> float | None:
    """The windowed 1/v estimator — **the same one that builds `ttf_fuk`**.

    ``buf`` is a node's window in arrival order, ``(t, disp, tilt, acc)`` per
    sample — the shape ``state.buffers[node_id]`` already holds and the shape
    ``engine.features`` consumes, so the cascade can hand this function the exact
    window the model saw. Returns ``None`` when the fit is unusable; the caller
    reads that as "no estimate" and moves to the next rung.

    Deliberately a re-implementation of the training rule rather than a call into
    it: ``engine/features.py`` needs the whole 16-feature row to produce
    ``ttf_fuk``, and the fallback runs when that row may not exist. §6.2 unifies
    the two in Phase 6; this test pins them equal in the meantime.
    """
    if not buf or len(buf) < FUK_FIRST_INDEX + 1:
        return None
    law = _law(calib)
    ts = np.array([float(s[0]) for s in buf], dtype=float)
    disp = np.array([float(s[1]) for s in buf], dtype=float)
    i = len(ts) - 1

    lo = int(np.searchsorted(ts, ts[i] - FUK_WINDOW_S, side="left"))
    xs: list[float] = []
    ys: list[float] = []
    for j in range(lo, i + 1):
        if j < FUK_FIRST_INDEX:                 # need a lookback of history
            continue
        k = int(np.searchsorted(ts, ts[j] - FUK_LOOKBACK_S, side="right")) - 1
        if k < 0 or (ts[j] - ts[k]) > FUK_LOOKBACK_S + FUK_LOOKBACK_TOL_S:
            continue
        v = (disp[j] - disp[k]) / FUK_LOOKBACK_S
        if v > config.FUK_V_MIN:                # below the floor an extrapolation is noise
            xs.append(ts[j] - ts[i])
            ys.append(1.0 / v)
    if len(xs) < FUK_MIN_POINTS:
        return None

    A = np.column_stack([np.array(xs, dtype=float), np.ones(len(xs))])
    (a_fit, b_fit), *_ = np.linalg.lstsq(A, np.array(ys, dtype=float), rcond=None)
    if a_fit >= -1e-12 or not np.isfinite(a_fit) or not np.isfinite(b_fit):
        return None
    t0 = -b_fit / a_fit
    if not np.isfinite(t0) or t0 < 0:
        return None
    clamp = float(law["stable_ttf_clamp_s"])
    if 0.0 <= t0 <= clamp:
        return float(t0)
    return None


def disagreement(ml_ttf: float | None, kin_ttf: float | None) -> float:
    """Relative gap between the learned and the physics estimate, in ``[0, 1]``.

    ``|ml − kin| / max(ml, kin)`` — scale-free on purpose, because "60 s apart"
    means something very different at a 90 s and a 3000 s horizon. ``0.0`` when
    either side is missing: no second opinion is not a disagreement, it is the
    absence of one, and the cascade reports those two states differently.

    This is a **magnitude**, not the flag. `cascade.disagreement_flag` owns the
    boolean (M1's label against M2's band); this number drives the confidence
    display and the `MODEL DISAGREEMENT` tooltip.
    """
    if ml_ttf is None or kin_ttf is None:
        return 0.0
    try:
        ml, kin = float(ml_ttf), float(kin_ttf)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(ml) and math.isfinite(kin)):
        return 0.0
    hi = max(abs(ml), abs(kin))
    if hi <= 0.0:
        return 0.0
    return float(min(1.0, abs(ml - kin) / hi))


# ---------------------------------------------------------------------------
# The rung the cascade calls
# ---------------------------------------------------------------------------
def _v10(ts: np.ndarray, disp: np.ndarray) -> float:
    """The node's 10 s velocity, by the same lookback rule as the features."""
    i = len(ts) - 1
    k = int(np.searchsorted(ts, ts[i] - FUK_LOOKBACK_S, side="right")) - 1
    if k < 0 or (ts[i] - ts[k]) > FUK_LOOKBACK_S + FUK_LOOKBACK_TOL_S:
        return 0.0
    return float((disp[i] - disp[k]) / FUK_LOOKBACK_S)


def kinematic_ttf(state, node_id: str) -> float | None:
    """The fallback estimate for one node, or ``None``.

    This is the name `engine/cascade.py` looks for when M2 has nothing fresh —
    the hook has been in place, guarded and inert, since Phase 5's cascade was
    written. The order is the plan's:

        Fukuzono 1/v  (a window exists)  →  v/a  (motion, no window)  →  None

    **The same sanity gate as the legacy path is applied here, in-module.** The
    hook returns whatever this function returns without a second look, so a
    physics estimate that skipped the gate would reach the firewall unexamined —
    and a formula is not more trustworthy than a model for having been written
    by hand.
    """
    try:
        buf = (getattr(state, "buffers", {}) or {}).get(node_id)
        if not buf or len(buf) < 2:
            return None
        ts = np.array([float(s[0]) for s in buf], dtype=float)
        disp = np.array([float(s[1]) for s in buf], dtype=float)

        from engine.cascade import T_sanity        # local: cascade imports us lazily
        v10 = _v10(ts, disp)
        disp_now = float(disp[-1])

        estimate = fukuzono_ttf_live(list(buf))
        if estimate is None:
            node = getattr(state, "nodes", {}).get(node_id)
            if node is None:
                return None
            estimate = kinematic_ttf_accel(
                float(getattr(node, "acceleration", 0.0) or 0.0),
                float(getattr(node, "velocity", 0.0) or 0.0))
            if estimate is None:
                return None
        if not T_sanity(v10, disp_now, estimate):
            return None
        return clamp_ttf(estimate)
    except Exception:  # noqa: BLE001 - a fallback that raises is worse than one that admits
        return None


def kinematic_source(state, node_id: str) -> str:
    """Which rung answered: ``fukuzono_1/v`` · ``v/a`` · ``none``.

    The vocabulary §6.2's acceptance test uses, so the dashboard can show the
    provenance of a countdown rather than a bare number.
    """
    try:
        buf = (getattr(state, "buffers", {}) or {}).get(node_id)
        if buf and fukuzono_ttf_live(list(buf)) is not None:
            return "fukuzono_1/v"
        node = getattr(state, "nodes", {}).get(node_id)
        if node is None:
            return "none"
        if kinematic_ttf_accel(float(getattr(node, "acceleration", 0.0) or 0.0),
                               float(getattr(node, "velocity", 0.0) or 0.0)) is not None:
            return "v/a"
        return "none"
    except Exception:  # noqa: BLE001
        return "none"
