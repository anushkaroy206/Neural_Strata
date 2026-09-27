"""
Neural Strata — the 16-feature rolling-window extractor.
========================================================

Moved **verbatim** out of ``app.py`` (``AI_FEATURES`` · ``_live_val_at`` ·
``compute_live_features``) in Phase 0. The arithmetic is unchanged, on purpose:
this must stay bit-identical to ``neuralstrata-ai/notebooks/01_data_extraction.py``,
because the shipped model was trained on windows produced by that notebook.

Phase 0 · closes defect **D3** (the docs said 14 features; the code has 16).
Phase 5 · adds the **16 disruption features** (§5.6) as a *second, additive*
layer. ``compute_live_features`` is untouched byte-for-byte, because
``ttf_model_v1.json`` was trained on its output and a feature list that drifts
by one decimal is a model that silently degrades. ``compute_extended_features``
calls it and appends — so the core 16 cannot drift by construction.

Feature order — *the* canonical order, must equal ``scaler.json["features"]``
---------------------------------------------------------------------------
====  ============  ==================================================
idx   name          meaning
====  ============  ==================================================
0     disp_now      displacement now (mm)
1     d10           Δdisp over 10 s
2     d30           Δdisp over 30 s
3     d60           Δdisp over 60 s
4     v10           mean velocity over 10 s
5     v30           mean velocity over 30 s
6     v60           mean velocity over 60 s
7     disp_max60    max displacement in the 60 s window
8     accel_now     acceleration now (m/s²)
9     accel_mean60  60 s mean acceleration
10    accel_std60   60 s std-dev of acceleration
11    accel_max60   60 s max acceleration
12    jerk          acc now − acc 1 s ago
13    tilt_now      tilt now (deg)
14    tilt_d30      Δtilt over 30 s
15    ttf_fuk       Fukuzono 1/v intercept → TTF (s), NaN when the fit is unusable
====  ============  ==================================================
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from config import FUK_V_MIN, MODEL_BUF, MODEL_DT

#: Canonical feature order (16). ``scaler.json["features"]`` must match exactly.
FEATURES: tuple[str, ...] = (
    "disp_now", "d10", "d30", "d60",
    "v10", "v30", "v60", "disp_max60",
    "accel_now", "accel_mean60", "accel_std60", "accel_max60", "jerk",
    "tilt_now", "tilt_d30", "ttf_fuk",
)

#: Named access instead of magic ``feats[4]`` everywhere.
FEATURE_INDEX: dict[str, int] = {name: i for i, name in enumerate(FEATURES)}

N_FEATURES = len(FEATURES)


def live_val_at(ts: np.ndarray, arr: np.ndarray, i: int, off_s: float) -> float:
    """Value of ``arr`` at ``t_i − off_s`` using the nearest sample.

    Same rule as training: step back to the last sample at or before the target,
    then reject it if it is further away than ``max(1 s, 5 % of the offset)``.
    Returns NaN when there is no acceptable sample — callers treat NaN as
    "window not trustworthy", never as zero.
    """
    target = ts[i] - off_s
    j = int(np.searchsorted(ts, target, side="right")) - 1
    if j < 0:
        return float("nan")
    tol = max(1.0, off_s * 0.05)
    if (ts[i] - ts[j]) > off_s + tol:
        return float("nan")
    return float(arr[j])


def compute_live_features(buf: Sequence[tuple[float, float, float, float]]) -> list[float] | None:
    """Build the 16 training features from a node's 60 s buffer.

    ``buf`` is a sequence of ``(t, disp, tilt, acc)`` samples in arrival order,
    sampled at the training cadence (2 Hz, 121 samples = 60 s + 1).

    Mirrors ``01_data_extraction.py`` exactly — same offsets, same Fukuzono rule.
    Returns the list in ``FEATURES`` order, or ``None`` if the window is
    incomplete (fewer than ``MODEL_BUF`` samples, or the 60 s offset is missing).
    """
    if len(buf) < MODEL_BUF:
        return None
    ts = np.array([s[0] for s in buf], dtype=float)
    disp = np.array([s[1] for s in buf], dtype=float)
    tilt = np.array([s[2] for s in buf], dtype=float)
    acc = np.array([s[3] for s in buf], dtype=float)
    i = len(ts) - 1

    d10 = disp[i] - live_val_at(ts, disp, i, 10.0)
    d30 = disp[i] - live_val_at(ts, disp, i, 30.0)
    d60 = disp[i] - live_val_at(ts, disp, i, 60.0)
    if math.isnan(d60):
        return None

    lo = int(np.searchsorted(ts, ts[i] - 60.0, side="left"))
    win_disp, win_acc = disp[lo:i + 1], acc[lo:i + 1]

    # Fukuzono 1/v fit over the last 30 s (same as training: v10 > FUK_V_MIN only)
    lo30 = int(np.searchsorted(ts, ts[i] - 30.0, side="left"))
    xs, ys = [], []
    for j in range(lo30, i + 1):
        if j < 21:                      # need 10 s of history for a v10
            continue
        k = int(np.searchsorted(ts, ts[j] - 10.0, side="right")) - 1
        if k < 0 or (ts[j] - ts[k]) > 10.5:
            continue
        v = (disp[j] - disp[k]) / 10.0
        if v > FUK_V_MIN:
            xs.append(ts[j] - ts[i])
            ys.append(1.0 / v)
    ttf_fuk = float("nan")
    if len(xs) >= 8:
        A = np.column_stack([np.array(xs), np.ones(len(xs))])
        (a_fit, b_fit), *_ = np.linalg.lstsq(A, np.array(ys), rcond=None)
        if a_fit < -1e-12:
            t0 = -b_fit / a_fit
            if 0.0 <= t0 <= 3600.0:
                ttf_fuk = float(t0)

    return [
        float(disp[i]), float(d10), float(d30), float(d60),
        d10 / 10.0, d30 / 30.0, d60 / 60.0, float(win_disp.max()),
        float(acc[i]), float(win_acc.mean()), float(win_acc.std()), float(win_acc.max()),
        float(acc[i]) - live_val_at(ts, acc, i, 1.0),
        float(tilt[i]), float(tilt[i]) - live_val_at(ts, tilt, i, 30.0),
        ttf_fuk,
    ]


# ---------------------------------------------------------------------------
# Phase 5 — Model 1's extended set: 16 core + 16 disruption (§5.6)
# ---------------------------------------------------------------------------

#: The 16 features Model 1 adds (indices 16–31). The four that do most of the
#: separation work (24 `recovery_ratio`, 26 `impulse_periodicity`, 29
#: `spatial_coherence`, 32 `tilt_acc_coupling`) each have a one-line physical
#: justification, which is what makes this set defensible rather than hand-wavy.
DISRUPTION_FEATURES: tuple[str, ...] = (
    "vib_count_60",             # 17  impulses in the window
    "vib_rate_10",              # 18  events/s over the last 10 s
    "micro_rate",               # 19  µm/s, from Phase 3
    "micro_z",                  # 20  micro_rate / threshold, normalised evidence
    "macro_d30",                # 21  mm of seam opening in 30 s
    "macro_total",              # 22  mm since baseline
    "cross_sensor_coherence",   # 23  §3.6 — THE headline feature
    "recovery_ratio",           # 24  1.0 = recovered (truck); ~0 = accumulating
    "excursion_duration_s",     # 25  seconds continuously above ACC_WARNING
    "impulse_periodicity",      # 26  periodic = engine/axle; flat = rock
    "zero_cross_rate",          # 27  spectral roughness
    "settlement_ratio",         # 28  slowing (recovering) vs accelerating
    "spatial_coherence",        # 29  local ⇒ machinery; multi-node ⇒ ground
    "spatial_decay",            # 30  distance-decaying ⇒ a surface source
    "n_active_anomalous",       # 31  how widespread
    "tilt_acc_coupling",        # 32  machinery tilts *with* its vibration
)

#: All 32, in model order. Model 2 keeps using ``FEATURES`` (the first 16).
EXTENDED_FEATURES: tuple[str, ...] = FEATURES + DISRUPTION_FEATURES
EXTENDED_INDEX: dict[str, int] = {name: i for i, name in enumerate(EXTENDED_FEATURES)}
N_EXTENDED = len(EXTENDED_FEATURES)

#: Guard rails, so a caller cannot silently feed the disruptor a truncated window
#: and get 16 plausible-looking numbers out of 3 s of data.
_MIN_PERIODICITY_SAMPLES = 8          # 4 s at 2 Hz — below this a lag-6 ACF is noise
_EPS = 1e-9


def _col(buf: Sequence, k: int) -> np.ndarray:
    """Column ``k`` of a 7-channel buffer, as float, NaN where the cell is None."""
    return np.array([(np.nan if s[k] is None else float(s[k])) for s in buf], dtype=float)


def _acf_periodicity(acc: np.ndarray, dt: float | None = None) -> float:
    """Autocorrelation peak over the 0.3–3 s lag band, minus its floor.

    **The feature that separates an engine from a rock.** A loader's vibration is
    periodic — an engine at a firing frequency, an axle at a wheel-pass rate — so
    its autocorrelation has a peak inside this band. Rock failing is *not*
    periodic; it is a burst with a decay. A flat ACF at every lag is the signature
    of a rock, and a peak is the signature of a machine.

    Returns ``0.0`` for an aperiodic window and up to ``1.0`` for a strongly
    periodic one, so the value is comparable across nodes and windows. ``NaN``
    when the window is too short for the lag band to mean anything — never
    ``0.0``, which would read as "definitely a rock".
    """
    n = len(acc)
    if n < _MIN_PERIODICITY_SAMPLES or not np.isfinite(acc).all():
        return float("nan")
    x = acc - acc.mean()
    denom = float(np.dot(x, x))
    if denom <= _EPS:
        return 0.0                      # perfectly flat: no periodicity to find
    if dt is None:
        dt = MODEL_DT
    lo = max(1, int(math.ceil(0.3 / dt)))
    hi = min(n - 1, int(math.floor(3.0 / dt)))
    if hi < lo:
        return float("nan")
    lags = list(range(lo, hi + 1))
    r = np.array([float(np.dot(x[:-k], x[k:])) / denom for k in lags])

    # **The peak must be INTERIOR, and it is scored by prominence.**
    #
    # The naive ``r.max()`` over the band is not a periodicity measure at all: a
    # smooth signal — a single rock burst, an exponential decay, any low-passed
    # trace — has its largest autocorrelation at the *shortest* lag and decays
    # monotonically from there. That maximum reports **smoothness**, and on this
    # rig it would label a rock burst 0.94 and call it a machine. It did, before
    # this was written: a Gaussian burst scored 0.935 while a genuinely periodic
    # signal scored 0.
    #
    # A periodic signal is one whose autocorrelation *comes back up* — a local
    # maximum somewhere inside the band. So: require a strict interior peak and
    # score it by how far it rises above the band's floor. A monotone decay has
    # no interior peak and scores 0.0, which is the correct answer for a rock.
    inner = range(1, len(lags) - 1) if len(lags) >= 3 else range(0)
    peaks = [r[i] for i in inner if r[i] > r[i - 1] and r[i] > r[i + 1]]
    if not peaks:
        return 0.0                      # monotone ⇒ a burst, not an engine
    prominence = float(max(peaks)) - float(r.min())
    return float(np.clip(prominence, 0.0, 1.0))


def _rolling_corr(a: np.ndarray, b: np.ndarray, win: int) -> float:
    """Mean Pearson r between two channels over the window, sample-paired.

    Used for ``tilt_acc_coupling``: does the roof lean *when* it is being shaken?
    Machinery tilts with its own vibration (the ground under it deflects as it
    loads); a propagating crack does not — the roof leans on its own schedule.
    ``NaN`` when either channel has no variance, because a correlation with a
    constant is 0/0 and reporting ``0.0`` would claim "uncorrelated" from no data.
    """
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 8:
        return float("nan")
    a2, b2 = a[m], b[m]
    if a2.std() <= _EPS or b2.std() <= _EPS:
        return float("nan")
    return float(np.clip(np.corrcoef(a2, b2)[0, 1], -1.0, 1.0))


def compute_disruption_features(
    buf: Sequence,
    *,
    peers: dict | None = None,
    crack: dict | None = None,
    acc_warning: float | None = None,
    now: float | None = None,
) -> list[float]:
    """The 16 disruption features from one node's **7-channel** window (§5.6).

    ``buf`` rows are ``(t, disp, tilt, acc, vib, micro, macro)`` — the shape
    ``state.append_model_buffer`` has written since Phase 1.

    Three groups, and the split matters:

    * **Local** (17–22, 24–28, 32) — computable from this node's window alone.
    * **Cross-sensor** (23) — ``crack["coherence"]``, Phase 3's engine. Passed in
      rather than recomputed: a second implementation of coherence in a feature
      extractor is a second answer to the same question, and Model 1 would then be
      trained on one and served the other.
    * **Cross-node** (29–31) — need ``peers``: ``{n_id: [7-channel rows]}``. A
      node whose peers are absent gets ``NaN`` here, never a flattering ``0.0``:
      "I cannot see the other nodes" and "the other nodes are quiet" are the two
      statements this feature set exists to distinguish.

    Returns a list in ``DISRUPTION_FEATURES`` order. NaN means *not measurable*.
    """
    peers = peers or {}
    crack = crack or {}
    if acc_warning is None:
        # The siren threshold and this feature are different numbers. Model 1
        # was trained with the excursion cut at 0.25 m/s².
        from config import EXCURSION_ACC as _AW
        acc_warning = _AW

    ts = _col(buf, 0)
    disp = _col(buf, 1)
    tilt = _col(buf, 2)
    acc = _col(buf, 3)
    vib = _col(buf, 4)
    # The micro (5) and macro (6) columns are deliberately *not* read here: the
    # disruption features take those channels from the crack engine's dict
    # (`micro_rate`, `macro_d30`, `macro_total`), which is already baseline-
    # corrected and thresholded. Reading the raw columns too would produce two
    # versions of the same feature that could disagree — and the raw ones are
    # pre-baseline, so they would disagree by exactly the baseline offset.
    i = len(ts) - 1
    dt = float(ts[i] - ts[i - 1]) if i >= 1 and ts[i] > ts[i - 1] else MODEL_DT

    nan = float("nan")

    # -- 17/18 · vibration counts. The SW-420 is a counter, not a level --------
    win60 = ts >= (ts[i] - 60.0)
    win10 = ts >= (ts[i] - 10.0)
    vib_count_60 = float(np.nansum(vib[win60])) if win60.any() else nan
    vib_rate_10 = float(np.nansum(vib[win10])) / 10.0 if win10.any() else nan

    # -- 19/20 · the structural evidence, straight from Phase 3 --------------
    micro_rate = crack.get("micro_rate", None)
    micro_rate = nan if micro_rate is None else float(micro_rate)
    try:
        from config import MICRO_RATE_THRESHOLD as _MRT
        if math.isnan(micro_rate):
            micro_z = nan
        else:
            # Clamped: the feature says "at or past the threshold", not "by how
            # many multiples". An unclamped z would let one railed sample dominate
            # a tree split that is meant to be about the threshold being crossed.
            micro_z = float(np.clip(micro_rate / _MRT, -3.0, 3.0))
    except Exception:                                   # pragma: no cover
        micro_z = nan

    # -- 21/22 · macro channels ----------------------------------------------
    macro_d30 = crack.get("macro_d30", None)
    macro_d30 = nan if macro_d30 is None else float(macro_d30)
    macro_total = crack.get("macro_total", None)
    macro_total = nan if macro_total is None else float(macro_total)

    # -- 23 · coherence, from Phase 3 (§5.6 calls it the headline feature) ----
    coh = crack.get("coherence", None)
    coherence = nan if coh is None else float(coh)

    # -- 24 · recovery ratio — the truck/rock discriminator -------------------
    # Over displacement: (peak − current) / (peak − baseline) across the window.
    # 1.0 = fully recovered (a truck has gone), ~0.0 = still accumulating (rock).
    recovery_ratio = nan
    dm = disp[win60]
    if dm.size and np.isfinite(dm).any():
        base, peak, cur = float(np.nanmin(dm)), float(np.nanmax(dm)), float(disp[i])
        span = peak - base
        recovery_ratio = 1.0 if span <= _EPS else float(np.clip((peak - cur) / span, 0.0, 1.0))

    # -- 25 · excursion duration: seconds = truck, minutes = creep -----------
    excursion_duration_s = 0.0
    j = i
    while j >= 0 and np.isfinite(acc[j]) and acc[j] > acc_warning:
        j -= 1
    excursion_duration_s = float((i - j) * dt)

    # -- 26/27 · waveform shape ---------------------------------------------
    amp = acc[win60]
    impulse_periodicity = _acf_periodicity(amp, dt) if amp.size >= _MIN_PERIODICITY_SAMPLES else nan
    zero_cross_rate = nan
    if amp.size >= 4 and np.isfinite(amp).all():
        centred = amp - amp.mean()
        zero_cross_rate = float((np.diff(np.sign(centred)) != 0).sum()) / max(1e-6, float(amp.size) * dt)

    # -- 28 · slowing (recovering) vs accelerating ---------------------------
    #
    # §5.6 writes this as ``|d60| / (|d10| + ε)`` — a 60-second delta divided by a
    # 10-second delta. Literally evaluated, that ratio's pivot is **6.0**, not 1.0
    # (a steady 1 mm/s gives d60 = 60 and d10 = 10), so the number cannot be read
    # as "1.0 = steady" and a tree would have to learn the 6.0 offset from data.
    # Dividing each delta by its own window turns it into a **velocity ratio**
    # with the pivot where the plan's prose wants it: <1 decelerating (recovering),
    # ≈1 steady, >1 accelerating. Recorded in §5.0 alongside the other Phase-5
    # deviations rather than applied silently.
    d10 = float(disp[i] - np.interp(ts[i] - 10.0, ts, disp)) if i > 0 else nan
    d60 = float(disp[i] - np.interp(ts[i] - 60.0, ts, disp)) if i > 0 else nan
    settlement_ratio = nan
    if np.isfinite(d10) and np.isfinite(d60):
        # **Clamped, and the clamp is not cosmetic.**
        #
        # The ratio is `|v60| / |v10|` — decelerating means the last 60 s moved
        # less than the last 10 s did, so > 1 is a settling creep and < 1 is an
        # accelerating one. The problem is the denominator: on a near-quiet
        # window `d10` is a few noise counts from zero, and the ratio then runs to
        # 1/ε. The first corpus contained values up to **2.5 × 10⁹**, which made
        # this the 5th most important feature in the forest — a number with no
        # physical meaning, deciding splits, because it was numerically loud.
        #
        # Clamping at 10.0 costs nothing real: everything above it already means
        # "the last 10 s were motionless to within instrument noise", and that
        # statement is identical at 10 and at 10⁹. Below the clamp the ordering —
        # the only thing a tree can use — is untouched.
        _den = abs(d10) / 10.0
        _num = abs(d60) / 60.0
        if _den <= _EPS:
            # No motion in the last 10 s at all. Defer to the 60 s rate so a
            # moving creep is not reported as a perfectly settled one.
            settlement_ratio = 10.0 if _num > _EPS else 1.0
        else:
            settlement_ratio = float(min(_num / _den, 10.0))

    # -- 29/30 · cross-node. Local ⇒ machinery; multi-node ⇒ ground ----------
    spatial_coherence = nan
    spatial_decay = nan
    n_active_anomalous = 0
    peer_amps = {}
    for p_id, p_buf in peers.items():
        if not p_buf or len(p_buf) < _MIN_PERIODICITY_SAMPLES:
            continue
        p_acc = _col(p_buf, 3)
        p_vib = _col(p_buf, 4)
        peer_amps[p_id] = p_acc
        if np.nansum(p_vib) > 0:
            n_active_anomalous += 1
    if len(peer_amps) >= 1:
        mine_rms = float(np.sqrt(np.nanmean(amp ** 2))) if amp.size and np.isfinite(amp).any() else nan
        rs = []
        for p_acc in peer_amps.values():
            n = min(len(amp), len(p_acc))
            a_slice, p_slice = amp[-n:], p_acc[-n:]
            if not (np.isfinite(a_slice).all() and np.isfinite(p_slice).all()):
                continue                    # genuinely missing — not a zero
            if p_slice.std() <= _EPS:
                # **Present and perfectly still is a measurement.**
                #
                # A neighbour whose channel exists and does not move is saying
                # "I am not shaking" — which is precisely the evidence that the
                # disturbance is *local*, the sentence feature #29 exists to
                # support. ``corrcoef`` against a constant is 0/0, so the naive
                # path returns NaN here and discards that evidence, leaving
                # ``spatial_coherence`` NaN on the commonest quiet case of all.
                rs.append(0.0)
                continue
            r = _rolling_corr(a_slice, p_slice, n)
            if np.isfinite(r):
                rs.append(r)
        if rs:
            spatial_coherence = float(np.clip(np.mean(rs), -1.0, 1.0))
            if np.isfinite(mine_rms) and mine_rms > _EPS:
                strongest = max(
                    (float(np.sqrt(np.nanmean(pa ** 2))) for pa in peer_amps.values()
                     if np.isfinite(pa).any()), default=nan)
                if np.isfinite(strongest):
                    spatial_decay = float(strongest / (mine_rms + _EPS))

    # -- 32 · does the roof lean *when* it is shaken? ------------------------
    tilt_d30 = tilt[i] - np.interp(ts[i] - 30.0, ts, tilt) if i > 0 else nan
    tilt_series = tilt - tilt[0] if len(tilt) and np.isfinite(tilt[0]) else tilt.copy()
    acc_mean_series = np.array([
        float(np.nanmean(acc[k:])) for k in range(len(acc))
    ]) if len(acc) else acc
    tilt_acc_coupling = _rolling_corr(tilt_series, acc_mean_series, len(acc)) if len(acc) else nan
    # `tilt_d30` is published as its own core feature (#14); this is the pairing.
    _ = tilt_d30

    return [
        vib_count_60, vib_rate_10,
        micro_rate, micro_z,
        macro_d30, macro_total,
        coherence,
        recovery_ratio, excursion_duration_s,
        impulse_periodicity, zero_cross_rate,
        settlement_ratio,
        spatial_coherence, spatial_decay, float(n_active_anomalous),
        tilt_acc_coupling,
    ]


def compute_extended_features(buf: Sequence, **kw) -> list[float] | None:
    """All **32** features: the core 16 verbatim, then the 16 disruption features.

    The core 16 come from ``compute_live_features`` **on the same buffer** — not
    reimplemented here. That is what makes this function additive by construction:
    if the core extractor ever changes for Model 2, Model 1's first 16 columns
    change with it, together, instead of drifting apart in two files.

    Returns ``None`` when the core window is incomplete, matching the core
    extractor's contract. A complete core window with unusable disruption inputs
    returns 32 numbers with NaNs in the unauthorised fields, because a tree model
    handles a missing value and cannot handle a truncated row.
    """
    core = compute_live_features(buf)
    if core is None:
        return None
    return list(core) + list(compute_disruption_features(buf, **kw))


def to_model_vector(feats: Sequence[float] | None) -> list[float] | None:
    """``NaN`` → ``PATTERN_MISSING_SENTINEL``, for the vector the tree consumes.

    **The one conversion, called by both the training notebook and the live path.**
    Model 1 is a scikit-learn ``RandomForestClassifier``, which rejects ``NaN``
    outright — unlike XGBoost, which is why Model 2 never needed this. So the
    honest ``NaN`` the extractor produces has to become a number at exactly one
    place, or the classifier is fitted on one world and served another.

    It is not merely a workaround. ``-999`` is outside every feature's real range,
    so a tree can split on it cleanly, and "this node carries no crack instrument"
    is a real, useful fact about a window — the classifier is entitled to learn it.
    """
    if feats is None:
        return None
    from config import PATTERN_MISSING_SENTINEL

    out: list[float] = []
    for v in feats:
        try:
            fv = float(v)
        except (TypeError, ValueError):
            out.append(float(PATTERN_MISSING_SENTINEL))
            continue
        out.append(float(PATTERN_MISSING_SENTINEL) if math.isnan(fv) else fv)
    return out


def extended_feature_row(feats: Sequence[float] | None) -> dict[str, float | None]:
    """``{feature_name: value}`` for the 32 — the log/CSV form."""
    if feats is None:
        return {name: None for name in EXTENDED_FEATURES}
    return {name: (None if isinstance(v, float) and math.isnan(v) else float(v))
            for name, v in zip(EXTENDED_FEATURES, feats)}


def window_complete(buf: Sequence, now: float | None = None) -> bool:
    """True when ``buf`` holds a full 60 s window **after the last discontinuity**.

    Phase 0 exposes the primitive; Phase 1 (D13) uses it to stop the re-arm path
    from feeding the model post-breach samples. A caller-supplied ``now`` (the
    timestamp of the oldest sample still allowed in the window) makes the check
    work for the replay clock as well as wall-clock.
    """
    if len(buf) < MODEL_BUF:
        return False
    if now is None:
        return True
    first_ts = float(buf[0][0])
    return first_ts >= float(now)


def feature_row(feats: Sequence[float] | None) -> dict[str, float | None]:
    """``{feature_name: value}`` for logs, CSV export and the docs checker."""
    if feats is None:
        return {name: None for name in FEATURES}
    return {name: (None if isinstance(v, float) and math.isnan(v) else float(v))
            for name, v in zip(FEATURES, feats)}


def scaler_matches(scaler: dict) -> bool:
    """Does a loaded scaler agree with the canonical feature order?

    The single most common demo-day failure is a new feature list running against
    an old scaler. This is the assertion that catches it (§A.3.8).
    """
    return list(scaler.get("features", [])) == list(FEATURES)


__all__ = [
    "FEATURES", "FEATURE_INDEX", "N_FEATURES",
    "DISRUPTION_FEATURES", "EXTENDED_FEATURES", "EXTENDED_INDEX", "N_EXTENDED",
    "live_val_at", "compute_live_features", "window_complete",
    "feature_row", "scaler_matches",
    "compute_disruption_features", "compute_extended_features", "extended_feature_row",
    "to_model_vector",
]
