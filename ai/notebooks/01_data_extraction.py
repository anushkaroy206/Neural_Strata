#!/usr/bin/env python3
"""
Neural Strata — Phase 4.1 · Data extraction + oracle labeling
==============================================================
Pulls sensor_telemetry from PostgreSQL, segments it into runs
(nominal periods + failure drills), builds 60 s feature windows,
and writes the oracle-labeled training set:

    neuralstrata-ai/datasets/drill_dataset.csv

Label definition (agreed):
    ttf_label = seconds until displacement crosses 100 mm along THIS
    run's actual trajectory. 0 at/after crossing. 3600 (capped) for
    rows in runs that never breach (stable / warning-only).

Harvest assumptions (app.py with --no-serial):
    * baseline displacement = 5.0 mm, flat while NOMINAL
    * ramp start: displacement crosses above 6.5 mm
    * Re-arm: displacement drops from above 20 mm back to baseline
    * rows are written by the ticker at 2 Hz for BOTH nodes

Run it (read-only on the DB — safe to re-run after more drills):
    conda activate ai_env
    python neuralstrata-ai/notebooks/01_data_extraction.py
    # optionally restrict to a time window:
    python 01_data_extraction.py --since "2026-08-26 14:00"

Phase 9 (§9.5) · ``--from-central``
-----------------------------------
Points this script at the **synced central database** instead of the local edge
one, which is what makes the production story coherent: the model retrains on
merged data from many sites, not just the one it was born on.

    python 01_data_extraction.py --from-central
    python 01_data_extraction.py --from-central --central-url "postgresql://…"

Two honest constraints, both printed by the script rather than discovered later:

1. **The central store holds no raw telemetry.** It holds the *rolled up* copy —
   one `node_hourly` row per site, node and hour (`infra/sql/cloud_schema.sql`),
   plus the incident records. The 60 s windows this file builds for the TTF model
   therefore *cannot* be rebuilt from it; what comes back is an hourly,
   multi-site feature table written to ``datasets/central_hourly.csv``. It is a
   different artefact for a different job, and it says so in its first column.
2. **It needs no edge database at all.** `--from-central` never opens the local
   one, so it runs on a laptop that has never seen the tunnel.
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
AI_ROOT = Path(__file__).resolve().parents[1]          # .../neuralstrata-ai
OUT_CSV = AI_ROOT / "datasets" / "drill_dataset.csv"

WINDOW_S = 60.0        # feature window length
GAP_LIMIT_S = 120.0    # data gap that also splits runs
DISP_CROSS = 6.5       # ramp start: disp crosses 1.5 mm above the 5.0 mm baseline
DISP_HIGH = 20.0       # "in action" level; a Re-arm drops from above this
DISP_BASE = 7.0        # "back at baseline"
BREACH_MM = 99.0       # a run that reaches this counts as a breach
CAP_S = 3600.0         # stable label cap
MIN_RUN_S = 10.0       # discard segments shorter than this
V_MIN_FUK = 1.0        # mm/s — Fukuzono fit only uses samples with v10 >= this
                       # (low-v samples poison the 1/v linear fit; validated:
                       #  corr 0.996 with true TTF, ~5-8x low bias which trees
                       #  recalibrate — the monotonic ranking is what matters)

ENV_CANDIDATES = [
    AI_ROOT.parent / "neuralstrata-backend" / ".env",
    Path.home() / "NEURAL_STRATA" / "neuralstrata-backend" / ".env",
    Path.cwd() / "neuralstrata-backend" / ".env",
]


def load_db_config() -> dict:
    """Postgres.app defaults first, then NS_DB_* from the backend .env."""
    cfg = {
        "host": "localhost",
        "port": 5432,
        "dbname": "neural_strata",
        "user": os.getenv("USER") or os.getenv("USERNAME") or "",
        "password": None,
    }
    for p in ENV_CANDIDATES:
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip().strip('"').strip("'")
                mapping = {
                    "NS_DB_HOST": "host",
                    "NS_DB_PORT": "port",
                    "NS_DB_NAME": "dbname",
                    "NS_DB_DBNAME": "dbname",
                    "NS_DB_USER": "user",
                    "NS_DB_PASSWORD": "password",
                }
                if k in mapping:
                    cfg[mapping[k]] = int(v) if k == "NS_DB_PORT" else v
            print(f"[db] config from {p}")
            break
    if cfg["password"] == "":
        cfg["password"] = None
    return cfg


# --------------------------------------------------------------------------
# Segmentation
# --------------------------------------------------------------------------
def segment_indices(n: int, ts: np.ndarray, disp: np.ndarray) -> list:
    """Split a node's row index range into runs.

    Boundary between row i-1 and i when:
      * gap > GAP_LIMIT_S, or
      * ramp start: disp crosses above DISP_CROSS (leaves the 5.0 mm baseline), or
      * Re-arm:     disp drops from above DISP_HIGH back to the baseline
    """
    bounds = [0]
    for i in range(1, n):
        gap = (ts[i] - ts[i - 1]) / np.timedelta64(1, "s")
        d0, d1 = disp[i - 1], disp[i]
        start = d0 <= DISP_CROSS and d1 > DISP_CROSS
        reset = d0 > DISP_HIGH and d1 <= DISP_BASE
        if gap > GAP_LIMIT_S or start or reset:
            bounds.append(i)
    bounds.append(n)
    return [(bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]


# --------------------------------------------------------------------------
# Features (per node, over its full time series)
# --------------------------------------------------------------------------
def offset_value(ts, vals, off_s):
    """vals at (t - off_s) for every row; NaN if no sample near that offset.

    Tolerance = 1 s minimum or 5% of the offset — at 2 Hz sampling the
    nearest sample to (t - off) is at most ~0.3 s away, so this keeps
    virtually every row while still rejecting genuinely stale data.
    """
    off_ms = int(off_s * 1000)
    tol_ms = max(1000, int(off_ms * 0.05))
    idx = np.searchsorted(ts, ts - np.timedelta64(off_ms, "ms"), side="right") - 1
    out = np.full(len(vals), np.nan)
    ok = idx >= 0
    idx_safe = np.where(ok, idx, 0)
    lag = (ts - ts[idx_safe]).astype("timedelta64[ms]").astype(float)
    recent = ok & (lag <= off_ms + tol_ms)
    out[recent] = vals[idx_safe[recent]]
    return out


def build_node_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: one node's rows, sorted by time, DatetimeIndex = timestamp."""
    ts = df.index.values
    disp = df["displacement_mm"].to_numpy(float)
    tilt = df["tilt_x"].to_numpy(float)
    acc = df["acceleration"].to_numpy(float)

    f = pd.DataFrame(index=df.index)
    f["disp_now"] = disp

    d10 = disp - offset_value(ts, disp, 10.0)
    d30 = disp - offset_value(ts, disp, 30.0)
    d60 = disp - offset_value(ts, disp, WINDOW_S)
    f["d10"] = d10
    f["d30"] = d30
    f["d60"] = d60
    f["v10"] = d10 / 10.0
    f["v30"] = d30 / 30.0
    f["v60"] = d60 / WINDOW_S

    win = pd.Series(disp, index=df.index).rolling(f"{WINDOW_S:.0f}s")
    f["disp_max60"] = win.max()

    f["accel_now"] = acc
    win_a = pd.Series(acc, index=df.index).rolling(f"{WINDOW_S:.0f}s")
    f["accel_mean60"] = win_a.mean()
    f["accel_std60"] = win_a.std()
    f["accel_max60"] = win_a.max()
    f["jerk"] = acc - offset_value(ts, acc, 1.0)

    f["tilt_now"] = tilt
    f["tilt_d30"] = tilt - offset_value(ts, tilt, 30.0)

    # Fukuzono-style TTF: linear fit of 1/v over the last 30 s,
    # extrapolate to where 1/v hits 0.  NaN where the fit is not valid.
    v10 = f["v10"].to_numpy()
    inv_v = np.where(v10 > V_MIN_FUK, 1.0 / np.where(v10 > 1e-9, v10, 1.0), np.nan)
    L = np.searchsorted(ts, ts - np.timedelta64(30, "s"), side="left")
    ttf_fuk = np.full(len(ts), np.nan)
    for i in range(len(ts)):
        j0 = L[i]
        if i - j0 + 1 < 8:
            continue
        x = (ts[j0 : i + 1] - ts[i]) / np.timedelta64(1, "s")
        y = inv_v[j0 : i + 1]
        m = ~np.isnan(y)
        if m.sum() < 8:
            continue
        A = np.column_stack([x[m], np.ones(int(m.sum()))])
        (a, b), *_ = np.linalg.lstsq(A, y[m], rcond=None)
        if a < -1e-12:  # 1/v decreasing toward 0 → failure in the future
            t0 = -b / a
            if 0.0 <= t0 <= CAP_S:
                ttf_fuk[i] = t0
    f["ttf_fuk"] = ttf_fuk
    return f


# --------------------------------------------------------------------------
# Central (Phase 9, §9.5) — the synced copy, for multi-site training
# --------------------------------------------------------------------------
CENTRAL_CSV = AI_ROOT / "datasets" / "central_hourly.csv"


def load_central_url(explicit: str | None = None) -> str:
    """``--central-url`` > ``NS_CLOUD_DB_URL`` > the compose default (port 5433).

    The same three-step order the edge server uses (`persistence/cloud_client.py`),
    deliberately: two components reading "where is the cloud" from two different
    precedence rules is how a demo ends up half-pointed at one database.
    """
    if explicit:
        return explicit
    url = os.getenv("NS_CLOUD_DB_URL")
    if url:
        return url
    pass_str = os.getenv("NS_CLOUD_DB_PASS", "")
    return f"postgresql://neural_strata:{pass_str}@localhost:5433/neural_strata_cloud"


def extract_from_central(url: str, since: str | None, csv_path: Path) -> int:
    """Write the central hourly feature table. Returns the row count.

    Read-only, and shaped like a training table rather than like telemetry: one row
    per (site_id, node_id, hour_start), the hourly aggregates as features, and the
    hour's worst believed status as the label. ``site_id`` is carried as a column —
    not collapsed — because the whole point of training centrally is that the model
    can be told which site a row came from, and because a per-site holdout is the
    only honest way to validate it.
    """
    print(f"[central] connecting {url.split('@')[-1]}")
    conn = psycopg2.connect(url)
    try:
        sql = (
            "SELECT site_id, node_id, hour_start, n_samples, "
            "       disp_mean, disp_min, disp_max, disp_last, "
            "       tilt_mean, tilt_max, acc_mean, acc_max, vib_sum, "
            "       micro_max, macro_max, severity_max "
            "  FROM node_hourly"
        )
        params: tuple = ()
        if since:
            sql += " WHERE hour_start >= %s"
            params = (since,)
        sql += " ORDER BY hour_start, site_id, node_id"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    finally:
        conn.close()

    if not rows:
        print("[central] no node_hourly rows — has anything synced yet? "
              "(the outbox drains on its own; POST /api/sync forces it)")
        return 0

    df = pd.DataFrame(rows, columns=cols)
    df["hour_start"] = pd.to_datetime(df["hour_start"], utc=True)
    df["breached"] = df["disp_max"] >= BREACH_MM
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(csv_path, index=False)

    sites = df["site_id"].nunique()
    print(f"[central] {len(df)} hourly row(s) · {sites} site(s) · "
          f"{df['node_id'].nunique()} node(s) → {csv_path}")
    print(f"[central] {int(df['breached'].sum())} row(s) with disp_max >= {BREACH_MM} mm")
    by_sev = df.groupby("severity_max").size().sort_values(ascending=False)
    for sev, n in by_sev.items():
        print(f"[central]   {sev:<10} {n}")
    print("[central] NOTE: hourly aggregates — the 60 s windows the TTF model wants "
          "exist only on the edge, so this table is a multi-site *context* set, "
          "not a drop-in replacement for drill_dataset.csv")
    return len(df)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", help="only include rows at/after this timestamp, e.g. '2026-08-26 14:00'")
    ap.add_argument("--csv", default=str(OUT_CSV), help="output CSV path")
    # Phase 9 (§9.5): read the synced central database instead of the local edge one.
    ap.add_argument("--from-central", action="store_true",
                    help="read the *central* (synced) database and write an hourly, "
                         "multi-site feature table instead of the 60 s drill set")
    ap.add_argument("--central-url", default=None,
                    help="central Postgres URL (default: $NS_CLOUD_DB_URL, then the "
                         "docker-compose port 5433) — only used with --from-central")
    args = ap.parse_args()

    if args.from_central:
        # Default the output path by mode: two tables, two files, never one file
        # silently overwritten by the other mode.
        csv_path = Path(args.csv) if args.csv != str(OUT_CSV) else CENTRAL_CSV
        n = extract_from_central(load_central_url(args.central_url), args.since, csv_path)
        sys.exit(0 if n else 1)

    cfg = load_db_config()
    print(f"[db] connecting {cfg['dbname']}@{cfg['host']}:{cfg['port']} as {cfg['user'] or '?'}")
    conn = psycopg2.connect(**cfg)
    cur = conn.cursor()
    sql = (
        'SELECT "timestamp", node_id, '
        'COALESCE(tilt_x,0) AS tilt_x, '
        'COALESCE(displacement_mm,0) AS displacement_mm, '
        'COALESCE(acceleration,0) AS acceleration '
        'FROM sensor_telemetry'
    )
    if args.since:
        sql += ' WHERE "timestamp" >= %s'
        sql += ' ORDER BY "timestamp", id'
        cur.execute(sql, (args.since,))
    else:
        sql += ' ORDER BY "timestamp", id'
        cur.execute(sql)
    df = pd.DataFrame(
        cur.fetchall(),
        columns=["timestamp", "node_id", "tilt_x", "displacement_mm", "acceleration"],
    )
    conn.close()

    if df.empty:
        print("ERROR: no rows in sensor_telemetry. Is the harvest session done?")
        sys.exit(1)

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    n_total = len(df)
    nodes = sorted(df["node_id"].unique())

    # ---- overview diagnostics ----
    tmin, tmax = df["timestamp"].min(), df["timestamp"].max()
    print(
        f"[db] {n_total} rows | {tmin:%Y-%m-%d %H:%M:%S} → {tmax:%H:%M:%S} "
        f"({(tmax - tmin).total_seconds() / 60:.1f} min span) | node_ids: {nodes}"
    )
    d = df["displacement_mm"]
    print(f"[db] displacement: min {d.min():.1f} / median {d.median():.1f} / max {d.max():.1f} mm")
    for node in nodes:
        s = df[df["node_id"] == node]["timestamp"]
        if len(s) < 2:
            print(f"[db] {node}: {len(s)} row(s)")
            continue
        gaps = s.diff().dt.total_seconds().dropna()
        print(
            f"[db] {node}: {len(s)} rows, {s.min():%H:%M:%S} → {s.max():%H:%M:%S}, "
            f"max gap {gaps.max():.0f}s, gaps>30s: {int((gaps > 30).sum())}"
        )

    records = []
    print("\n--- segments found (discarded ones shown too) ---")
    for node in nodes:
        sub = df[df["node_id"] == node].set_index("timestamp").sort_index()
        if sub.empty:
            continue
        idx = segment_indices(len(sub), sub.index.values, sub["displacement_mm"].to_numpy(float))
        feats = build_node_features(sub)
        for k, (i0, i1) in enumerate(idx):
            rows = sub.iloc[i0:i1]
            dur = (rows.index[-1] - rows.index[0]).total_seconds()
            max_d = float(rows["displacement_mm"].max())
            is_breach = max_d >= BREACH_MM
            tag = "BREACH" if is_breach else "stable"
            if len(rows) < 2 or dur < MIN_RUN_S:
                print(
                    f"  [{node}_{k:02d}] {rows.index[0]:%H:%M:%S} → {rows.index[-1]:%H:%M:%S}  "
                    f"({dur:6.1f}s)  max {max_d:5.1f} mm  {tag:6s}  — DISCARDED (too short)"
                )
                continue
            # oracle label
            if is_breach:
                crossed = rows.index[rows["displacement_mm"] >= BREACH_MM]
                t_cross = crossed[0]
                label = np.clip((t_cross - rows.index).total_seconds(), 0.0, CAP_S)
            else:
                label = np.full(len(rows), CAP_S)
            f = feats.iloc[i0:i1]
            keep = f["d60"].notna()
            f = f[keep]
            label = label[keep.to_numpy()]
            if f.empty:
                print(
                    f"  [{node}_{k:02d}] {rows.index[0]:%H:%M:%S} → {rows.index[-1]:%H:%M:%S}  "
                    f"({dur:6.1f}s)  max {max_d:5.1f} mm  {tag:6s}  — DISCARDED (no full 60s window)"
                )
                continue
            run_id = f"{node}_{k:02d}"
            rec = f.copy()
            rec.insert(0, "run_id", run_id)
            rec.insert(1, "node_id", node)
            rec.insert(2, "run_class", "BREACH" if is_breach else "STABLE")
            rec.insert(3, "ttf_label", label)
            records.append(rec)
            print(
                f"  [{run_id}] {rows.index[0]:%H:%M:%S} → {rows.index[-1]:%H:%M:%S}  "
                f"({dur:6.1f}s)  max {max_d:5.1f} mm  {tag:6s}  {len(f):4d} windows  "
                f"label {float(label.min()):5.0f}–{float(label.max()):.0f}s"
            )

    if not records:
        print("\nERROR: no usable runs found.")
        print("Likely causes: (1) harvest session too short / not all drills done,")
        print("(2) app.py not logging both nodes, (3) idle periods not logged at all.")
        print("Re-run after the full session — the DB accumulates, script is read-only.")
        sys.exit(1)

    out = pd.concat(records)
    out = out.reset_index()  # timestamp is the index here -> turn it into a column
    out = out.sort_values(["node_id", "timestamp"]).reset_index(drop=True)
    out["timestamp"] = out["timestamp"].dt.tz_convert(None)
    out.to_csv(args.csv, index=False)

    print("\n=== HARVEST SUMMARY ===")
    print(f"rows in DB:        {n_total}")
    print(f"node_ids:          {nodes}")
    n_breach = out["run_id"].where(out["run_class"] == "BREACH").nunique()
    n_stable = out["run_id"].where(out["run_class"] == "STABLE").nunique()
    print(f"runs kept:         {out['run_id'].nunique()}  (breach {n_breach}, stable {n_stable})")
    print(f"windows in CSV:    {len(out)}")
    print(f"label stats:       min {out['ttf_label'].min():.0f}s, "
          f"median {out['ttf_label'].median():.0f}s, max {out['ttf_label'].max():.0f}s")
    print(f"ttf_fuk coverage:  {out['ttf_fuk'].notna().mean()*100:.0f}% of windows")
    print(f"written:           {args.csv}")

    warnings = []
    if out["run_id"].nunique() < 8:
        warnings.append(f"only {out['run_id'].nunique()} runs kept — expected ~12; check the session was logged")
    if n_stable == 0:
        warnings.append("no STABLE run kept — did the 3 min nominal run happen?")
    if n_breach < 8:
        warnings.append("fewer than 8 BREACH runs kept — the model needs the countdown data")
    if set(nodes) < {"node_1", "node_2"}:
        warnings.append(f"only {nodes} in DB — both nodes should be logged now")
    if warnings:
        print("\nWARNINGS:")
        for w in warnings:
            print(f"  ! {w}")
    print("\nPaste this whole output back to me and I'll verify before we train.")


if __name__ == "__main__":
    main()
