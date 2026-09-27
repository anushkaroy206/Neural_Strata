#!/usr/bin/env python3
"""
Neural Strata — Phase 4.6a · In-Domain Synthetic Augmentation Pipeline
======================================================================
Generates physically consistent synthetic drill runs to expand the training
dataset (from 7,870 windows to 50,000+ windows) without cross-scale contamination.

Physics model:
  * Primary creep: disp(t) = v_creep * t + noise
  * Tertiary acceleration (Fukuzono / Voight law): d^2 x / dt^2 = A * (dx/dt)^alpha
    giving hyperbolic/power-law acceleration towards breach (disp = 100 mm).
  * Correlated sensors:
      - tilt: correlated with displacement gradient + random wander
      - acc: standard deviation scales with d^2 x / dt^2
      - SW-420 vib count: Poisson rate proportional to acceleration
      - micro (µm) / macro (mm): ONE seam trace, two instruments — see the
        coupling block below, which is Phase 3's H2.5 replacement for the old
        `disp * 18` microstrain coupling.
"""

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

AI_ROOT = Path(__file__).resolve().parents[1]
DATASET_IN = AI_ROOT / "datasets" / "drill_dataset.csv"
DATASET_AUG = AI_ROOT / "datasets" / "drill_dataset_augmented.csv"

SAMPLING_HZ = 2.0
DT = 1.0 / SAMPLING_HZ
BREACH_DISP_MM = 100.0
STABLE_LABEL_S = 3600.0

# ── the crack column's couplings (Phase 3 · H2.5) ───────────────────────────
#
# Replaces the previous prototype's `microstrain = disp * 18` and
# `crack_macro = (disp - 15) * 0.4`. Both were wrong in the same way: they tied
# the crack channels to **roof displacement**, which is what a passing loader
# also moves. A truck shakes the whole portal and the seam closes again
# elastically; a crack opens because the rock is failing and the gap does not
# come back. Deriving one from the other teaches the model that vehicles crack
# the roof — from a generator artefact, not from physics.
#
# So the seam gets its own trace, opened only on the runs where the rock fails,
# and both instruments read **that**:
#
#   micro (µm)   load cell in tension across the seam; ~1.5 µm of noise
#   macro (mm)   the 25 mm rotary pot across the same gap; 25/4095 = 6.1 µm per
#                count, so its quantisation is visible at these openings
#
# The rate coupling is the bench's (H2.7): while the rock is failing, seam
# opening tracks roughly a twelfth of the roof's velocity — a 0.9 mm/s accelerating
# drill face opens the seam at ≈0.075 mm/s, which is the rate the backend's
# simulator uses and the rate the two-stage story is calibrated around.
SEAM_VEL_FRACTION = 1.0 / 12.0        # dimensionless, from H2.7
POT_MM_PER_COUNT = 25.0 / 4095.0      # NS_MACRO_MM_MAX / 12-bit full scale
MICRO_NOISE_UM = 1.5                  # load-cell noise floor, µm RMS
MICRO_RAIL_UM = 90000.0               # rail sentinel — matches NS_MICRO_RAIL_UM
SEAM_MAX_MM = 25.0                    # the pot's travel; the channel rails past it


def _crack_channels_from_seam(seam_mm, rng, steps):
    """One seam trace → the two channels the nodes publish.

    The pot's 6.1 µm quantisation is applied rather than smoothed away: it is the
    reason the coarse channel needs 1.5 mm of opening to cross its threshold,
    which is the whole source of the lead time between the two events.
    """
    seam = np.clip(seam_mm, 0.0, SEAM_MAX_MM)
    macro = np.round(seam / POT_MM_PER_COUNT) * POT_MM_PER_COUNT
    micro = seam * 1000.0 + rng.normal(0.0, MICRO_NOISE_UM, size=steps)
    return micro, macro


def simulate_synthetic_run(run_id, is_breach=True, duration_s=180.0, rng=None):
    if rng is None:
        rng = np.random.default_rng()

    steps = int(duration_s * SAMPLING_HZ)
    t = np.linspace(0, duration_s, steps)

    if not is_breach:
        # Stable creep: low random velocity, stays well below breach
        v_creep = rng.uniform(0.005, 0.04)  # mm/s
        disp = v_creep * t + rng.normal(0, 0.15, size=steps)
        disp = np.clip(disp, 0.0, 35.0)
        tilt = rng.uniform(1.0, 5.0) + 0.02 * disp + rng.normal(0, 0.05, size=steps)
        accel = rng.normal(0.02, 0.01, size=steps)
        # A stable run has no failing rock, so the seam does not open: what the
        # crack column reports is its own noise floor, and nothing else. That is
        # what the bench shows on a quiet seam, and it is the class the model
        # must not learn to call a crack.
        micro_um, crack_macro = _crack_channels_from_seam(np.zeros(steps), rng, steps)
        # vib_count is not a column here — see the note in the breach branch below
        ttf_label = np.full(steps, STABLE_LABEL_S)
        run_class = "STABLE"
    else:
        # Accelerating failure: steady creep -> tertiary acceleration
        t_fail = rng.uniform(duration_s * 0.7, duration_s * 0.95)
        t_onset = rng.uniform(t_fail * 0.3, t_fail * 0.6)
        alpha = rng.uniform(1.6, 2.2)  # Fukuzono exponent
        
        disp = np.zeros(steps)
        # 1. Pre-onset slow creep
        v0 = rng.uniform(0.02, 0.08)
        for i in range(steps):
            ti = t[i]
            if ti < t_onset:
                disp[i] = v0 * ti + rng.normal(0, 0.1)
            elif ti < t_fail:
                # Tertiary acceleration following inverse velocity
                progress = (ti - t_onset) / (t_fail - t_onset)
                # Hyperbolic curve to 100 mm
                disp[i] = disp[int(t_onset * SAMPLING_HZ)] + (BREACH_DISP_MM - disp[int(t_onset * SAMPLING_HZ)]) * (progress ** alpha)
                disp[i] += rng.normal(0, 0.25)
            else:
                disp[i] = BREACH_DISP_MM + rng.uniform(0.5, 3.0)

        disp = np.clip(disp, 0.0, 110.0)
        
        # The seam: quiet until the rock starts failing, then opening at the
        # measured fraction of the roof's own velocity. Both channels are derived
        # from this single trace, so they agree — a cross-check the model can
        # rely on and a contaminant the generator can no longer invent.
        vel = np.gradient(disp, DT)
        seam = np.zeros(steps)
        seam_rate_jitter = rng.uniform(0.7, 1.3)      # run-to-run rock variability
        for i in range(1, steps):
            ti = t[i]
            if ti >= t_onset:
                seam[i] = seam[i - 1] + max(0.0, vel[i]) * SEAM_VEL_FRACTION * seam_rate_jitter * DT
            else:
                seam[i] = seam[i - 1]
        micro_um, crack_macro = _crack_channels_from_seam(seam, rng, steps)

        tilt = rng.uniform(1.0, 3.0) + disp * rng.uniform(0.08, 0.15) + rng.normal(0, 0.08, size=steps)

        # Acceleration (vel was computed with the seam above)
        accel = np.gradient(vel, DT) + rng.normal(0.02, 0.02, size=steps)
        # The SW-420 impulse count is deliberately not a column of this dataset:
        # the shipped corpus takes its vibration channel from the simulator itself
        # (tools/make_counterfactuals.py), which counts the pulses the firmware
        # would count rather than re-deriving a Poisson draw from acceleration here.

        # TTF label: seconds until disp >= 100 mm
        breach_indices = np.where(disp >= BREACH_DISP_MM)[0]
        if len(breach_indices) > 0:
            first_breach_t = t[breach_indices[0]]
            ttf_label = np.maximum(0.0, first_breach_t - t)
            # Clip windows after breach to 0
            ttf_label[t > first_breach_t] = 0.0
        else:
            ttf_label = np.full(steps, STABLE_LABEL_S)

        run_class = "BREACH"

    # 16 windowed features, the count the backend's `engine/features.py` builds
    # and the count every published number in this repository uses (Phase 0's D3
    # audit). This comment said 14 while the dict below already held 16.
    rows = []
    # 60s window = 120 steps
    W = int(60.0 * SAMPLING_HZ)
    for i in range(W, steps):
        d_window = disp[i - W: i + 1]
        # tilt is read pointwise below, so no tilt window is built
        a_window = accel[i - W: i + 1]

        disp_now = float(disp[i])
        d10 = float(disp_now - disp[i - int(10 * SAMPLING_HZ)])
        d30 = float(disp_now - disp[i - int(30 * SAMPLING_HZ)])
        d60 = float(disp_now - disp[i - W])

        v10 = d10 / 10.0
        v30 = d30 / 30.0
        v60 = d60 / 60.0
        disp_max60 = float(np.max(d_window))

        accel_now = float(accel[i])
        accel_mean60 = float(np.mean(a_window))
        accel_std60 = float(np.std(a_window))
        accel_max60 = float(np.max(a_window))
        jerk = float((accel_now - accel[i - int(2 * SAMPLING_HZ)]) / 2.0)

        tilt_now = float(tilt[i])
        tilt_d30 = float(tilt_now - tilt[i - int(30 * SAMPLING_HZ)])

        # Fukuzono kinematic term 1 / v
        if v10 > 0.05:
            # simple 1/v estimate towards 100 mm limit
            ttf_fuk = float(min(STABLE_LABEL_S, max(0.0, (BREACH_DISP_MM - disp_now) / (v10 + 1e-6))))
        else:
            ttf_fuk = STABLE_LABEL_S

        rows.append({
            "run_id": f"syn_{run_id:04d}",
            "run_class": run_class,
            "disp_now": disp_now,
            "d10": d10,
            "d30": d30,
            "d60": d60,
            "v10": v10,
            "v30": v30,
            "v60": v60,
            "disp_max60": disp_max60,
            "accel_now": accel_now,
            "accel_mean60": accel_mean60,
            "accel_std60": accel_std60,
            "accel_max60": accel_max60,
            "jerk": jerk,
            "tilt_now": tilt_now,
            "tilt_d30": tilt_d30,
            # Phase 3 · H2.5 — the crack column reaches the dataset. Additive
            # columns: the 16 model features and the target are unchanged, and
            # nothing consumes these yet. They are here because a coupling that
            # never reaches the training data is a comment, not a measurement —
            # Phase 5's second model is trained on exactly this pair.
            "micro_um": float(micro_um[i]),
            "macro_mm": float(crack_macro[i]),
            "ttf_fuk": ttf_fuk,
            "ttf_label": float(ttf_label[i]),
        })

    return rows


def generate_augmented_dataset(n_runs=250, target_windows=50000, seed=42):
    rng = np.random.default_rng(seed)
    all_rows = []

    # Read base dataset if exists
    if DATASET_IN.exists():
        base_df = pd.read_csv(DATASET_IN)
        print(f"[base] loaded {len(base_df)} windows from {DATASET_IN.name}")
        all_rows.append(base_df)
        existing_windows = len(base_df)
    else:
        existing_windows = 0

    needed_windows = max(0, target_windows - existing_windows)
    print(f"[augment] generating ~{needed_windows} additional synthetic windows across {n_runs} runs...")

    syn_runs = []
    run_idx = 1
    total_generated = 0
    while total_generated < needed_windows:
        is_breach = (run_idx % 2 == 1)  # 50% breach / 50% stable
        dur = rng.uniform(140.0, 260.0)
        run_rows = simulate_synthetic_run(run_idx, is_breach=is_breach, duration_s=dur, rng=rng)
        syn_runs.extend(run_rows)
        total_generated += len(run_rows)
        run_idx += 1

    syn_df = pd.DataFrame(syn_runs)
    print(f"[augment] generated {len(syn_df)} synthetic windows across {run_idx - 1} runs.")

    if all_rows:
        combined = pd.concat([all_rows[0], syn_df], ignore_index=True)
    else:
        combined = syn_df

    DATASET_AUG.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(DATASET_AUG, index=False)
    print(f"[export] wrote {len(combined)} total windows to {DATASET_AUG}")
    return combined


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", type=int, default=200, help="Number of synthetic runs")
    ap.add_argument("--target", type=int, default=50000, help="Target total windows")
    args = ap.parse_args()
    generate_augmented_dataset(n_runs=args.runs, target_windows=args.target)
