# Neural Strata — Backend Build Phases
## Phase-by-Phase Construction Plan · Master Function Registry · Dual-Model (Double AI) Specification

| | |
|---|---|
| **Document** | `Neural_Strata_Backend_Build_Phases.md` |
| **Version** | v1.0 (build plan) |
| **Companion to** | `BACKEND_PLAN.md` (v5.0, what the system *is*) — this document is *how to build it, in what order* |
| **Hardware twin** | `Neural_Strata_Hardware_Build_Phases.md` (H0–H9) — the physical build order, master GPIO registry and soldering spec |
| **Written against** | The live code in `neuralstrata-backend/app.py` (696 lines, v4.4), `models/` (4 artifacts), `neuralstrata-ai/notebooks/` (01–05), `neuralstrata-frontend/gis-map.js`, `app-v44/tunnel_map.json` |
| **Audited on** | 18 Sep 2026 |
| **Phases** | 11 (Phase 0 → Phase 10) |
| **Total effort** | ~24–30 dev-days, parallelisable to ~3 calendar weeks for a 6-person team |
| **Minimum demo path** | Phases **0, 1, 3, 5, 6, 7, 8** (≈14 days) |

---

## How to read this document

This plan is written to be executed in order. Each phase is a self-contained work package that:
1. states exactly what already exists (audited against the real code, not against the plan's own wishes),
2. states the target behaviour and the exact data contract,
3. lists every function it adds or changes,
4. lists **every change required in the existing project** — `app.py`, firmware, frontend, database, and the AI notebooks — down to the function and line,
5. gives the acceptance test and the rollback.

**Three rules the whole plan obeys:**

> **Rule 1 — Never break the running system.** `python3 app.py` must boot and serve `/api/telemetry` after *every* phase. The refactor is done by extraction (strangler-fig), not by rewrite. Each phase ends with the same boot command it started with.
>
> **Rule 2 — Never let the AI silence a warning.** The Dual-Model Cascade (Phase 5) may change *how loudly* the system shouts and *how often* it computes. It may never change a hazard into a non-hazard. This is enforced by an automated test, not by good intentions.
>
> **Rule 3 — One canonical number.** Every metric quoted in the SRS, the deck, the README and `model_meta.json` must come from one training run whose report file is committed. See Phase 0.

---

## Table of contents

**Part A — The whole backend at a glance**
- A.1 Target package layout
- A.2 **Master function registry** (every function, every module, existing + planned)
- A.3 Data contracts (frozen schemas)
- A.4 Configuration & constants registry
- A.5 Model & artifact registry
- A.6 **Defect audit** — 14 defects found in the current code (root cause list)
- A.7 Phase map, dependency graph, effort table

**Part B — The phases**
- Phase 0 — Ground Truth, Contracts & Replay Harness
- Phase 1 — B1: 4-Node Multi-Level State Engine & Ingestion Hardening
- Phase 2 — B7: Node Health, Watchdog & Degraded Mode
- Phase 3 — B2: Two-Stage Crack Anomaly Engine
- Phase 4 — B8: Pair Elongation, Differential Settlement & Zone Risk
- Phase 5 — **THE DOUBLE AI: Dual-Model Cascade** (Model 1 Pattern Discriminator + Model 2 TTF Regressor)
- Phase 6 — B3: Severity, Progression, Confidence & Kinematic Unification
- Phase 7 — B5: Dijkstra Evacuation Routing + GIS `/api/map`
- Phase 8 — B4: Persistent Alert Dispatcher
- Phase 9 — B6: Cloud Sync Outbox
- Phase 10 — B12 + Hardening: Gated Transfer Learning, Observability, Ops

**Part C — Appendices**
- C.1 Critical path & compressed 5-day track
- C.2 Risk register
- C.3 Viva question bank (per phase)
- C.4 Acceptance checklist
- C.5 Glossary (plain-language, for the team)

---
---

# PART A — THE WHOLE BACKEND AT A GLANCE

## A.1 Target package layout

Today the entire backend is one 696-line file. By Phase 10 it is a package. The migration is **incremental**: `app.py` keeps working throughout, and each phase moves one concern out of it.

```
neuralstrata-backend/
├── app.py                      ← SHRINKS to bootstrap only (~80 lines by Phase 10)
├── config.py                   ← [NEW] every constant, env-parsed, imported by all
├── contracts.py                ← [NEW] schema version, node IDs, frame validators
│
├── ingestion/
│   ├── serial_reader.py        ← [NEW] USB serial → validated frames → state
│   ├── simulator.py            ← [NEW] scenario console + 7 injection profiles
│   └── ring.py                 ← [NEW] relay frames, heartbeats, link state
│
├── engine/
│   ├── features.py             ← [NEW] 16-feature window (moved out of app.py)
│   ├── kinematic.py            ← [NEW] calibrated Fukuzono–Voight fallback (B11 wired in)
│   ├── pattern.py              ← [NEW] MODEL 1 — Deformation Pattern Discriminator
│   ├── ttf.py                  ← [NEW] MODEL 2 — TTF Regressor (+ quantile band)
│   ├── cascade.py              ← [NEW] THE DOUBLE AI orchestrator + scheduler + firewall
│   ├── crack.py                ← [NEW] B2 two-stage micro/macro crack engine
│   ├── geomech.py              ← [NEW] B8 pair elongation, zone risk
│   ├── severity.py             ← [NEW] B3 severity, progression stage, confidence
│   └── health.py               ← [NEW] B7 heartbeat, staleness, degraded mode
│
├── routing/
│   └── dijkstra.py             ← [NEW] B5 hazard-weighted shortest safe exit
│
├── alerts/
│   ├── dispatcher.py           ← [NEW] B4 alert state machine + dedup + escalation
│   ├── channels.py             ← [NEW] hub siren/LED, dashboard, SMS, email
│   └── templates.py            ← [NEW] message text, recommended actions, geo links
│
├── persistence/
│   ├── db.py                   ← [NEW] pooled connections, schema init, migrations
│   ├── schema.sql              ← [NEW] all DDL in one place
│   ├── outbox.py               ← [NEW] B6 local queue
│   └── sync.py                 ← [NEW] B6 uploader daemon
│
├── api/
│   ├── routes_telemetry.py     ├── routes_map.py     ├── routes_alerts.py
│   ├── routes_ai.py            ├── routes_admin.py   └── map_payload.py
│
├── state.py                    ← [NEW] SystemState class + lock + history
├── metrics.py                  ← [NEW] counters, timers, /api/metrics
│
├── assets/
│   ├── tunnel_map.json         ← MOVED from app-v44/
│   └── site_meta.json          ← [NEW] baseline coords, scale, exits
│
├── models/
│   ├── ttf_model_v1.json       ← exists (MODEL 2, current)
│   ├── ttf_model_v2.json       ← Phase 5/6 (quantile trio)
│   ├── pattern_model_v1.json   ← [NEW] MODEL 1 (Phase 5)
│   ├── scaler.json             ← exists (shared scaling contract)
│   ├── pattern_meta.json       ← [NEW] M1 metrics, thresholds, class list
│   └── MANIFEST.json           ← [NEW] which artifacts go together (version pin)
│
├── tools/
│   ├── record.py               ← [NEW] dump live session → CSV
│   ├── replay.py               ← [NEW] feed CSV back → full pipeline (dev w/o hardware)
│   ├── make_counterfactuals.py ← [NEW] generate Model-1 training set (7 classes)
│   ├── eval_cascade.py         ← [NEW] confusion matrix, suppression rate, latency
│   └── check_numbers.py        ← [NEW] asserts docs == model_meta.json (Phase 0)
│
└── tests/
    ├── test_contracts.py  test_features.py  test_dijkstra.py  test_alerts.py
    ├── test_outbox.py     test_kinematic.py test_api.py
    └── test_cascade_safety.py  ← THE most important test in the repository
```

**Why this layout:** every file has one reason to change. `features.py` changes when the feature contract changes. `dijkstra.py` changes when the tunnel map changes. Today, changing the number of nodes forces edits in **five places inside one file** (defect D10), which is how regressions happen at 2 a.m. before a demo.

---

## A.2 Master function registry

Every backend function. `Status` is one of **EXISTS** (in the live code today), **MOVE** (exists but is relocated/renamed in a phase), **NEW**, or **CHANGE** (exists, behaviour modified in a phase).

### A.2.1 `config.py` — configuration surface

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `load_dotenv` | `load_dotenv(path: Path) -> None` | Read `.env` into `os.environ` without clobbering existing keys | 0 | MOVE (from `app.py::_load_dotenv`) |
| `env_str` | `env_str(key, default) -> str` | Typed env access with default | 0 | NEW |
| `env_int` | `env_int(key, default) -> int` | Typed env access | 0 | NEW |
| `env_float` | `env_float(key, default) -> float` | Typed env access | 0 | NEW |
| `env_bool` | `env_bool(key, default) -> bool` | Typed env access (`1/true/yes/on`) | 0 | NEW |
| `validate_config` | `validate_config() -> list[str]` | Boot-time sanity: sane thresholds, existing paths, port ranges | 0 | NEW |
| `describe` | `describe() -> dict` | Redacted config dump for `/api/health` | 2 | NEW |

**Constants owned by `config.py`** (single source of truth — nothing else may hardcode these):

```
DB_HOST · DB_NAME · DB_USER · DB_PASS · DB_POOL_MIN · DB_POOL_MAX
SERIAL_PORT · BAUD_RATE · SERIAL_RECONNECT_S · SERIAL_TIMEOUT_S
NODE_IDS = ("node_1","node_2","node_3","node_4")
LEVELS   = {1: ("node_1","node_2"), 2: ("node_3","node_4")}
TICK_MS = 50 · HISTORY_INTERVAL_S = 0.5 · HISTORY_MAX = 240
MODEL_BUF = 121 · MODEL_FRESH_S = 5.0 · INFER_HZ = 1.0 · FUK_V_MIN = 1.0
ACC_CRITICAL = 3.0 · ACC_WARNING = 0.25 · VEL_WARNING = 0.03
MICRO_RATE_THRESHOLD = 15.0   # µε/s          (B2)
MACRO_JUMP_THRESHOLD = 1.5    # mm            (B2)
MICRO_SETTLE_S = 15.0         # HX711 warm-up (B2)
HEARTBEAT_S = 2.0 · STALE_AFTER_S = 5.0       (B7)
TTF_BANDS = (0, 30, 120, 600) # CRITICAL/HIGH/ELEVATED/WATCH (B3)
CASCADE_MODE = "advisory"     # "advisory" | "gate"  (Phase 5)
M1_SAMPLE_HZ = 2.0            # M1 always runs at full telemetry rate
M2_SAMPLE_HZ = {"QUIET":0.2, "ENVIRONMENTAL":1.0, "STRUCTURAL":2.0, "UNKNOWN":2.0}
ALERT_DEDUP_S = 60 · ALERT_ESCALATE_S = 120 · SMS_MAX_PER_HOUR = 20
SMS_GATEWAY_PORT · SMTP_HOST · SMTP_PORT · SMTP_USER · SMTP_PASS · ALERT_TO_EMAILS
OUTBOX_MAX_ROWS · SYNC_INTERVAL_S · SYNC_ENDPOINT · SYNC_HMAC_KEY
LOG_LEVEL · METRICS_ENABLED
```

### A.2.2 `contracts.py` — schema & validation

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `SCHEMA_VERSION` | `int = 2` | Wireless contract revision; hub and server must agree | 0 | NEW |
| `NODE_IDS` / `NodeId` | tuple / `Literal` | Canonical node identifiers | 0 | NEW |
| `coerce_float` | `coerce_float(v, default=nan) -> float` | Safe numeric coercion from JSON | 0 | NEW |
| `validate_node_frame` | `validate_node_frame(d) -> tuple[bool, str]` | Field presence, type, range, schema version check | 1 | NEW |
| `validate_ring_frame` | `validate_ring_frame(d) -> tuple[bool, str]` | Relay envelope validation | 1 | NEW |
| `validate_rfid_frame` | `validate_rfid_frame(d) -> tuple[bool,str]` | UID present, non-empty | 1 | NEW |
| `NodeState` | dataclass | One node's live fields + `to_public()` / `to_db_tuple()` | 1 | NEW |
| `TelemetryPayload` | dataclass | The `/api/telemetry` body; frozen field names | 1 | NEW |
| `migrate_payload` | `migrate_payload(p, from_v) -> dict` | Accept v1 (2-node) frames after the v2 upgrade | 1 | NEW |

### A.2.3 `state.py` — the state engine

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `class SystemState` | — | Owns nodes, links, events, RFID log, armed flag, one `RLock` | 1 | NEW |
| `SystemState.get_node` | `(self, nid) -> NodeState` | Guarded accessor | 1 | NEW |
| `SystemState.iter_nodes` | `(self, active_only=True)` | Replaces all hardcoded `("node_1","node_2")` tuples | 1 | NEW |
| `SystemState.nodes_of_level` | `(self, level) -> tuple` | Level grouping | 1 | NEW |
| `SystemState.worst_status` | `(self) -> str` | Worst-of across active nodes | 1 | NEW |
| `SystemState.snapshot` | `(self) -> dict` | Deep copy for API serialisation | 1 | NEW |
| `SystemState.reset` | `(self, keep_history=False)` | Re-arm; **also clears AI buffers** (defect D13) | 1 | CHANGE |
| `push_event` | `push_event(state, level, text, source="engine") -> None` | Ring-buffered event log, now with source + persistence hook | 1 | CHANGE |
| `snapshot_history` | `snapshot_history(state) -> None` | Append chart points for all active nodes | 1 | CHANGE |
| `append_model_buffer` | `append_model_buffer(state, nid, t, **channels) -> None` | Feed the 60 s AI window (disp, tilt, acc, vib, micro, macro) | 1 | CHANGE (from inline in `ticker_thread`) |
| `mark_discontinuity` | `mark_discontinuity(state, nid, reason) -> None` | Invalidate window on Re-arm / sensor re-baseline | 1 | NEW |

### A.2.4 `ingestion/serial_reader.py` — hardware in

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `open_serial` | `open_serial(port, baud) -> Serial \| None` | Open with timeout; never raises to caller | 0 | NEW |
| `read_frames` | `read_frames(ser) -> Iterator[str]` | Line generator, tolerant of partial/garble | 0 | NEW |
| `parse_frame` | `parse_frame(line) -> dict \| None` | JSON decode, dispatch to validators | 1 | NEW |
| `handle_rfid_frame` | `(state, d) -> tuple[str,str] \| None` | Miner check-in; returns DB row | 1 | CHANGE (from `hardware_reader_thread`) |
| `handle_node_frame` | `(state, d) -> None` | Apply telemetry, update derivatives, crack, health | 1 | CHANGE |
| `compute_derivatives` | `(state, nid, disp, dt) -> tuple[float,float]` | v and a with **measured Δt**, EMA 0.3/0.7 | 1 | **CHANGE** (fixes D5) |
| `hardware_reader_thread` | `(state, stop_event) -> None` | Read loop → frames → state; survives disconnects | 2 | CHANGE |
| `serial_watchdog` | `(state, stop_event) -> None` | Reconnect with exponential backoff, raises NO_HARDWARE | 2 | NEW |
| `handle_ring_frame` | `(state, d) -> None` | Unwrap level-hub relay envelope | 1 | NEW |

### A.2.5 `ingestion/simulator.py` — scenario console (the demo's engine)

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `SCENARIOS` | `dict[str, ScenarioProfile]` | **7 profiles**: `nominal`, `warning_creep`, `critical_collapse`, `machinery_pass`, `blast`, `rain_seepage`, `sensor_fault` | 5 | NEW (extends 2) |
| `class ScenarioProfile` | dataclass | `accel_fn`, `disp_gain`, `duration_s`, `recovers`, `strain_coupled` | 5 | NEW |
| `apply_injections` | `(state, dt) -> None` | Advance every armed scenario with **measured dt** | 1 | CHANGE |
| `start_scenario` | `(state, nid, name) -> bool` | Arm a profile (replaces severity-only trigger) | 5 | CHANGE |
| `stop_scenario` | `(state, nid) -> None` | Disarm and settle | 5 | NEW |
| `scenario_progress` | `(state, nid) -> float` | 0–1 for the UI progress ring | 5 | NEW |

### A.2.6 `engine/features.py` — the 16-feature window

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `FEATURES` | `tuple[str, ...]` (16) | **Canonical order.** Must equal `scaler.json["features"]` | 0 | MOVE (from `02_training.py` + `app.py`) |
| `FEATURE_INDEX` | `dict[str, int]` | Named access instead of magic `feats[4]` | 0 | NEW |
| `live_val_at` | `(ts, arr, i, off_s) -> float` | Value at `t_i − off_s`, nearest sample, tolerance-gated | 0 | MOVE (`_live_val_at`) |
| `compute_live_features` | `(buf, now=None) -> list[float] \| None` | Build 16 features from a 60 s buffer, identical to training | 0 | MOVE |
| `compute_extended_features` | `(buf) -> list[float]` | **32-feature set** for Model 1 (16 + 16 disruption features) | 5 | NEW |
| `feature_health` | `(feats) -> dict` | NaN count, staleness, per-channel presence | 2 | NEW |
| `window_complete` | `(buf, now) -> bool` | Full 60 s **after** the last discontinuity | 1 | NEW |

### A.2.7 `engine/kinematic.py` — B11 finally wired in (fixes D2)

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `load_calibration` | `(path) -> dict` | Read `kinematic_calibration.json`; validate keys | 6 | NEW |
| `fukuzono_ttf` | `(disp, v, calib) -> float` | Analytic TTF from the calibrated law | 6 | NEW |
| `fukuzono_ttf_live` | `(buf, calib) -> float` | Windowed 1/v least-squares fit → intercept with the time axis (**same estimator as the training feature `ttf_fuk`**) | 6 | NEW |
| `kinematic_ttf_accel` | `(accel, vel) -> float` | Legacy `v/a` extrapolation, kept as the **last-resort** path | 6 | MOVE (from `update_status_logic`) |
| `clamp_ttf` | `(ttf, calib) -> float` | Clamp to `[0, stable_ttf_clamp_s]` | 6 | NEW |
| `disagreement` | `(ml_ttf, kin_ttf) -> float` | Relative gap, drives confidence display | 6 | NEW |

### A.2.8 `engine/pattern.py` — **MODEL 1** (Double AI, first half)

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `load_pattern_model` | `() -> PatternModel \| None` | Load RF + `pattern_meta.json`; `None` ⇒ cascade degrades to M2-only | 5 | NEW |
| `compute_disruption_features` | `(buf) -> list[float]` | The 16 disruption features (A.2.6 + §5.6) | 5 | NEW |
| `predict_pattern` | `(feats) -> PatternResult` | Class + `disruption_index` | 5 | NEW |
| `class PatternResult` | dataclass | `label`, `disruption_index`, `proba: dict`, `top_features` | 5 | NEW |
| `explain_pattern` | `(res, feats) -> list[tuple[str,float]]` | Top-k contributing features for the UI/viva | 5 | NEW |
| `operating_threshold` | `(meta) -> float` | Recall-tuned threshold loaded from `pattern_meta.json` | 5 | NEW |
| `is_structural` | `(res, thr) -> bool` | Safety-critical predicate (recall ≥ 0.98 target) | 5 | NEW |

### A.2.9 `engine/ttf.py` — **MODEL 2** (Double AI, second half)

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `load_ttf_model` | `() -> TtfModel \| None` | Load XGB regressor(s) + scaler + meta | 0 | MOVE (`load_model`) |
| `predict_ttf` | `(feats) -> float` | Point forecast, clamped `[0, 3600]` | 0 | MOVE (`run_model_inference` inner) |
| `predict_ttf_band` | `(feats) -> tuple[float,float,float]` | `(q10, q50, q90)` from the quantile trio | 6 | NEW |
| `sanity_gate` | `(feats, ttf) -> bool` | "Imminent failure with no motion is not credible" | 0 | MOVE |
| `model_status` | `() -> dict` | ACTIVE / WARMING UP / FALLBACK + MAE + trained_at | 0 | MOVE (`get_ai` inner) |

### A.2.10 `engine/cascade.py` — the Double AI orchestrator

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `class CascadeDecision` | dataclass | `nid`, `m1`, `m2_ttf`, `band`, `kin_ttf`, `mode`, `route`, `reason` | 5 | NEW |
| `run_cascade` | `(state, nid, now) -> CascadeDecision` | M1 (always) → M2 (scheduled) → route decision | 5 | NEW |
| `inference_scheduler` | `(state, now) -> list[str]` | Which nodes run M2 **this tick** (0.2 / 1 / 2 Hz) | 5 | NEW |
| `cascade_tick` | `(state, now) -> list[CascadeDecision]` | Called from the ticker; owns the schedule | 5 | NEW |
| `route_decision` | `(dec, mode) -> str` | `"QUIET" / "ADVISORY" / "ELEVATED" / "IMMINENT"` | 5 | NEW |
| `firewall_check` | `(dec) -> bool` | **Safety assertion**: M1 must never suppress a low M2 TTF | 5 | NEW |
| `disagreement_flag` | `(dec) -> bool` | M1 says environment, M2 says failure ⇒ escalate + flag | 5 | NEW |
| `explain_decision` | `(dec) -> str` | One-line human sentence for the alert log | 5 | NEW |

### A.2.11 `engine/crack.py` — B2 two-stage crack engine

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `update_micro_baseline` | `(state, nid, raw) -> float` | 15 s HX711 settling average, then zero-referenced µε | 3 | NEW |
| `micro_rate` | `(state, nid) -> float` | Median-filtered d(µε)/dt over 5 s | 3 | NEW |
| `macro_opening` | `(state, nid) -> float` | Potentiometer-derived seam opening, mm | 3 | NEW |
| `classify_crack` | `(state, nid) -> CrackEvent \| None` | Applies both thresholds with hysteresis | 3 | NEW |
| `cross_sensor_coherence` | `(state, nid) -> float` | **The Model-1 headline feature** — see §5.6 | 3 | NEW |
| `crack_stage` | `(state, nid) -> str` | `NONE / INITIATION / PROPAGATION / CRITICAL` | 3 | NEW |
| `reset_crack_baseline` | `(state, nid) -> None` | Called on Re-arm and on sensor hot-swap | 3 | NEW |

### A.2.12 `engine/geomech.py` — B8

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `pair_elongation` | `(state, a, b) -> float` | ΔL between two collinear nodes, mm | 4 | NEW |
| `pair_strain` | `(state, a, b) -> float` | ΔL / L₀ in µε | 4 | NEW |
| `differential_settlement` | `(state, level) -> float` | |Δdisp| across a level | 4 | NEW |
| `zone_risk` | `(state, zone_id) -> str` | Worst-of nodes + links in a zone | 4 | NEW |

### A.2.13 `engine/severity.py` — B3

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `severity_from_ttf` | `(ttf, bands) -> str` | `CRITICAL / HIGH / ELEVATED / WATCH` | 6 | NEW |
| `progression_stage` | `(state, nid) -> str` | Primary / Secondary / **Tertiary** creep, and FAILURE | 6 | NEW |
| `curvature_score` | `(feats) -> float` | Second-derivative energy ⇒ tertiary-ness | 6 | NEW |
| `confidence_band` | `(meta, n_windows, stale, disagreement) -> str` | Honest confidence for the UI | 6 | NEW |
| `recommended_actions` | `(sev, stage, route, people_underground) -> list[str]` | Human instructions in the alert | 6 | NEW |

### A.2.14 `engine/health.py` — B7

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `health_tick` | `(state, now) -> list[str]` | Emit `NODE_STALE`, `LINK_DOWN`, `RING_SPLIT`, `BATT_LOW` | 2 | NEW |
| `node_last_seen` | `(state, nid) -> float` | Seconds since last valid frame | 2 | NEW |
| `is_stale` | `(state, nid, now) -> bool` | Beyond `STALE_AFTER_S` | 2 | NEW |
| `link_state` | `(state, link_id) -> str` | `UP / STALE / DOWN` | 2 | NEW |
| `ring_cut_detect` | `(state, now) -> list[str]` | Which relay leg is dark | 2 | NEW |
| `degraded_mode` | `(state, now) -> str` | `FULL / SENSOR_PARTIAL / RING_SPLIT / NO_HARDWARE` | 2 | NEW |
| `has_hardware` | `(state) -> bool` | Serial up and ≥1 fresh node | 2 | NEW |
| `watchdog_alarm` | `(state) -> bool` | Anything dark while armed ⇒ banner | 2 | NEW |

### A.2.15 `routing/dijkstra.py` — B5

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `load_tunnel_map` | `(path) -> SiteMap` | Parse + validate `tunnel_map.json` | 7 | NEW |
| `class SiteMap` | dataclass | Nodes, junctions, exits, links, zones, adjacency | 7 | NEW |
| `build_graph` | `(site, state) -> dict[str, list[tuple[str,float]]]` | Adjacency with live costs | 7 | NEW |
| `link_cost` | `(state, link, base_m) -> float` | `∞` if blocked; else `m × (1 + hazard)` | 7 | NEW |
| `hazard_penalty` | `(state, a, b) -> float` | Accumulates risk along the leg from node + crack state | 7 | NEW |
| `dijkstra` | `(graph, src, exits) -> tuple[list[str], float]` | Weighted shortest safe path | 7 | NEW |
| `route_for_person` | `(site, state, src) -> RouteResult` | Route + exit + ETA + reason | 7 | NEW |
| `nearest_exit` | `(site, state, src) -> str` | Fallback when the graph is fully dark | 7 | NEW |
| `fail_safe_hold` | `(state, junction) -> None` | Amber pulse, never point into danger | 7 | NEW |
| `junction_led_plan` | `(site, state, routes) -> list[HubCommand]` | Chase/strobe plan for the **level hub** to drive WS2812B | 7 | NEW |
| `push_hub_command` | `(cmd: HubCommand) -> bool` | Serial down-link to the hub (LEDs, siren) | 7 | NEW |
| `resolve_position` | `(state, miner_uid) -> str` | RFID zone → graph node | 7 | NEW |

### A.2.16 `alerts/` — B4

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `class AlertEvent` | dataclass | Full incident record (matches `alerts_history`) | 8 | NEW |
| `dedupe_key` | `(e) -> str` | `node|event_type|level` bucket key | 8 | NEW |
| `class AlertStateMachine` | — | `NEW → ACTIVE → ACKED → CLOSED`, with escalation | 8 | NEW |
| `AlertStateMachine.observe` | `(e, now) -> str` | Compute transition | 8 | NEW |
| `AlertStateMachine.escalate` | `(now) -> bool` | Unacknowledged escalation timer | 8 | NEW |
| `dispatch` | `(state, e) -> AlertEvent` | Fan out to channels in priority order | 8 | NEW |
| `notify_local` | `(state, e) -> bool` | Hub siren + junction strobe (the latency-critical one) | 8 | CHANGE (was UI-only) |
| `notify_dashboard` | `(state, e) -> bool` | Banner + GIS flash + event log | 8 | CHANGE |
| `notify_sms` | `(e) -> bool` | 4G modem AT commands via the hub | 8 | NEW |
| `notify_email` | `(e) -> bool` | SMTP with georeferenced deep link | 8 | NEW |
| `retry_failed` | `(conn) -> int` | Re-send undelivered channels | 8 | NEW |
| `suppression_guard` | `(e, rule) -> bool` | **SRS 5.5 rule**: a Tier-3 evacuation can never be auto-suppressed | 8 | NEW |
| `ack_alert` | `(conn, alert_id, operator) -> bool` | Human acknowledgement only | 8 | NEW |

### A.2.17 `persistence/` — DB, outbox, sync (B6)

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `init_schema` | `(conn) -> None` | Create all tables + `schema_migrations` | 0 | NEW |
| `migrate` | `(conn) -> int` | Apply pending migrations, idempotent | 0 | NEW |
| `get_conn` / `release` | pooled | Replaces per-call `psycopg2.connect` (fixes D11) | 0 | NEW |
| `insert_telemetry` | `(conn, rows) -> None` | Batched insert (already batched; now pooled) | 0 | CHANGE |
| `insert_rfid` | `(conn, uid, zone) -> None` | Check-in record | 0 | MOVE |
| `insert_alert` | `(conn, e) -> int` | Returns alert id | 8 | NEW |
| `fetch_alerts` | `(conn, limit, severity, since) -> list` | Paginated alert log | 8 | NEW |
| `query_metric_series` | `(conn, node, field, since) -> list` | Historical trend chart | 9 | NEW |
| `enqueue_outbox` | `(conn, kind, payload) -> int` | Append to the FIFO outbox | 9 | NEW |
| `claim_outbox_batch` | `(conn, n) -> list` | Atomic claim (`FOR UPDATE SKIP LOCKED`) | 9 | NEW |
| `mark_outbox_sent` | `(conn, ids) -> None` | Cleared on 2xx | 9 | NEW |
| `mark_outbox_failed` | `(conn, ids, err) -> None` | Retry counting with cap | 9 | NEW |
| `compact_outbox` | `(conn, keep_days) -> int` | Housekeeping | 9 | NEW |
| `sync_daemon` | `(state, stop_event) -> None` | Background flusher | 9 | NEW |
| `probe_backhaul` | `(timeout) -> bool` | Is there internet? | 9 | NEW |
| `flush_batch` | `(batch) -> bool` | HTTPS POST with HMAC + gzip | 9 | NEW |
| `sign_payload` | `(body, key) -> str` | HMAC-SHA256 header | 9 | NEW |

### A.2.18 `api/` — the HTTP surface

| Endpoint | Handler | Cadence | Contract | Phase | Status |
|---|---|---|---|---|---|
| `GET /api/telemetry` | `get_telemetry` | 500 ms | 4 nodes, levels, routes, TTF, status | 1 | CHANGE |
| `GET /api/extended` | `get_extended` | 500 ms | + velocity, peak accel, muster, events | 1 | CHANGE |
| `GET /api/history` | `get_history` | 1 s | Chart series | 1 | CHANGE |
| `GET /api/ai` | `get_ai` | 1 s | **+ M1 block, cascade mode, band, disagreement** | 5 | CHANGE |
| **`GET /api/map`** | `get_map` | 2 s | GIS payload — **route currently missing entirely (D1)** | 7 | **NEW** |
| `GET /api/site` | `get_site` | on load | Baseline metadata | 7 | NEW |
| `GET /api/alerts` | `get_alerts` | 1 s | Paginated, filterable, with actions | 8 | NEW |
| `POST /api/alerts/<id>/ack` | `ack_alert` | on click | Operator acknowledgement | 8 | NEW |
| `GET /api/health` | `get_health` | 5 s | Node/link/model/db health | 2 | NEW |
| `GET /api/metrics` | `get_metrics` | 5 s | Counters, latencies, queue depth | 10 | NEW |
| `POST /api/sim/trigger` | `post_sim_trigger` | on drill | **`profile` accepted, not just severity** | 5 | CHANGE |
| `POST /api/reset` | `post_reset` | on re-arm | Clears buffers + baselines | 1 | CHANGE |
| `POST /api/sync` | `post_sync` | manual | Force outbox flush | 9 | NEW |
| `GET /assets/<path>` | `get_asset` | — | Serves `gis-map.js` (**also missing — part of D1**) | 7 | NEW |
| `GET /` | `dashboard` | — | Serve `index.html` | 0 | EXISTS |

### A.2.19 `metrics.py`

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `incr` | `(name, n=1) -> None` | Counter | 10 | NEW |
| `observe` | `(name, value) -> None` | Histogram (latency, MAE live) | 10 | NEW |
| `timer` | `(name) -> ContextManager` | `with timer("inference"):` | 10 | NEW |
| `snapshot` | `() -> dict` | Serialisable metrics blob | 10 | NEW |

### A.2.20 `tools/` & `tests/`

| Function | Signature | Purpose | Phase | Status |
|---|---|---|---|---|
| `record.main` | `(args) -> None` | Poll `/api/telemetry` → timestamped CSV | 0 | NEW |
| `replay.main` | `(csv, speed) -> None` | Push CSV through the real pipeline at 1×/10× | 0 | NEW |
| `make_counterfactuals.main` | `(runs, out) -> None` | Emit `pattern_dataset.csv` (7 classes) | 5 | NEW |
| `eval_cascade.main` | `(dataset) -> None` | Per-class P/R, structural recall, suppression rate, latency | 5 | NEW |
| `check_numbers.main` | `() -> int` | Assert docs == `model_meta.json`; exit 1 on drift | 0 | NEW |
| `bench_latency.main` | `() -> None` | M1/M2 ms per inference on the hub CPU | 5 | NEW |

**Test suite (13 new tests):** `test_contracts`, `test_features` (incl. **parity vs `01_data_extraction.py`**), `test_state_reset` (D13), `test_derivatives_dt` (D5), `test_health`, `test_crack_thresholds`, `test_kinematic` (vs `ttf_fuk`), `test_dijkstra` (incl. no-path ⇒ HOLD), **`test_cascade_safety`** (M1 can never suppress M2), `test_alerts_dedup`, `test_outbox_retry`, `test_api_contracts` (schema snapshot), `test_map_payload` (matches what `gis-map.js` reads).

**Registry total: 152 functions across 21 modules** — 24 exist today, 12 are moved/renamed, 116 are new.

---

## A.3 Data contracts (frozen schemas)

These are the interfaces everything else depends on. **Freeze them in Phase 0; change them only by bumping the version integer.**

### A.3.1 Node → Hub frame (schema v2) — the wireless contract

Current firmware sends a 3-field frame. Target:

```json
{
  "v": 2,
  "node": 3,
  "ms": 184220,
  "disp": 12.40,
  "tilt": 1.85,
  "acc": 0.31,
  "vib": 4,
  "micro": 412.5,
  "macro": 3.20,
  "rssi": -62,
  "batt": 3.78,
  "flags": 0
}
```

| Field | Type | Units | Notes |
|---|---|---|---|
| `v` | int | — | Schema version. Server rejects unknown majors, logs once. |
| `node` | int 1–4 | — | Node index. |
| `ms` | int | ms | Node uptime; used for **measured Δt**, not the arrival clock (fixes D5). |
| `disp` | float | mm | Potentiometer → macro displacement. |
| `tilt` | float | deg | MPU-6050 tilt_x. |
| `acc` | float | m/s² | **Real** MPU-6050 acceleration, now transmitted instead of numerically differentiated (fixes D9). |
| `vib` | int | counts/2 s | SW-420 event accumulator. |
| `micro` | float \| null | µε | BF350 + HX711, only on nodes 2 & 4. `null` elsewhere. |
| `macro` | float \| null | mm | Rotary-pot seam opening, only on nodes 2 & 4. |
| `rssi` | int | dBm | ESP-NOW link quality. |
| `batt` | float | V | Node supply (present in production). |
| `flags` | int | bitfield | bit0 micro saturated · bit1 macro at rail · bit2 baseline not settled · bit3 low battery · bit4 last-gasp packet |

### A.3.2 Ring relay envelope (Level Hub → Main Hub)

**Terminology note:** this is a relayed link, not a "hop". The envelope exists so Level 2 data can reach the Main Hub through the J2↔J3 cross-link when the direct leg is dark.

```json
{
  "v": 2,
  "via": "lvl2",
  "seq": 9912,
  "link": "lvl2→hub",
  "frames": [ {"...": "node frames"} ]
}
```

### A.3.3 Main Hub → Server frame (USB serial, one JSON object per line)

```json
{"v":2,"via":"hub","seq":18422,"t":1758211200.250,"frames":[ ... ],"uid":"001"}
```

RFID is a separate frame type: `{"v":2,"uid":"001","reader":"hub"}`.
The server must accept **bare node frames** (`{"node":1,...}`) forever, because `app.py` today and the firmware today emit that — this is the v1 compatibility path (`migrate_payload`).

### A.3.4 Server → Hub command frame (down-link, `push_hub_command`)

```json
{"cmd":"led",   "junction":"J2",  "pattern":"chase_red", "ms":120}
{"cmd":"led",   "junction":"J3",  "pattern":"hold_amber","ms":400}
{"cmd":"siren", "level":2,        "on":true}
{"cmd":"advisory","level":1,      "on":true}
{"cmd":"gps",   "lat":23.643,     "lon":85.318, "alt":0}
```

**Constraint honoured:** junctions have **no MCU of their own**. Every `led` command targets a *level hub*, which drives its own WS2812B junctions and buzzers. The command is addressed by junction ID only so the firmware can pick which strip segment to light.

### A.3.5 `telemetry_state` (server in-memory + `/api/telemetry`)

```json
{
  "schema": 2,
  "system_status": "NOMINAL",
  "degraded_mode": "FULL",
  "cascade_mode": "advisory",
  "ttf_seconds": 3599,
  "ttf_band": [3540, 3599, 3600],
  "nodes": {
    "node_1": {"status":"NOMINAL","tilt_x":0.0,"displacement_mm":5.0,
               "acceleration":0.0,"velocity":0.0,"vib":0,
               "micro":null,"macro":null,"crack_stage":"NONE",
               "last_seen_s":0.4,"stale":false},
    "node_2": {"...":"8 more nodes follow the identical shape"},
    "node_3": {}, "node_4": {}
  },
  "levels": {
    "1": {"status":"NOMINAL","route":"OPEN","exits":["EXIT1","EXIT2"]},
    "2": {"status":"NOMINAL","route":"OPEN","exits":["EXIT2"]}
  },
  "underground": {"route_A_status":"OPEN","route_B_status":"OPEN",
                  "junctions":{"J1":"OK","J2":"OK","J3":"OK","J4":"OK"}},
  "ai": {"model_2":{"name":"ttf_model_v2","status":"ACTIVE","mae_s":0.10},
         "model_1":{"name":"pattern_model_v1","status":"ACTIVE",
                    "disruption_index":0.06,"label":"QUIET"},
         "kinematic":{"source":"fukuzono","alpha":1.95}},
  "rfid_log": []
}
```

**Backward-compatibility contract:** `/api/telemetry` must keep `system_status`, `ttf_seconds`, `nodes`, `underground.route_A_status`, `underground.route_B_status`, `rfid_log` at the same JSON paths forever — the dashboard reads those. New keys are additive only.

### A.3.6 `/api/map` payload — **what `gis-map.js` already expects**

`gis-map.js` computes its own projection from lat/lon. It reads:

```json
{
  "site": {"name":"Jharia panel — 1:1000 diorama","center":[23.643,85.318],"scale":"..."},
  "system_status": "NOMINAL",
  "nodes":     [{"id":"node_1","lat":23.6432,"lon":85.3180,"label":"N1…","level":1,"status":"NOMINAL"}],
  "junctions": [{"id":"J1","lat":23.6432,"lon":85.3184,"status":"OK"}],
  "exits":     [{"id":"EXIT1","lat":23.6430,"lon":85.3175,"label":"Exit 1 (west)"}],
  "links":     [{"from":"J1","to":"J2","state":"UP","cost":45.0}],
  "zones":     [{"id":"A1","name":"Tunnel A · near","risk":"NOMINAL",
                 "polygon":[[lat,lon],[lat,lon],[lat,lon],[lat,lon]]}]
}
```

**Critical:** the current `tunnel_map.json` stores **metre offsets** (`d_n_m`, `d_e_m`) and **rectangles in cm-space**; `gis-map.js` needs **lat/lon and polygons**. The conversion helper `offsets_to_latlon()` + `rect_to_polygon()` therefore belongs in Phase 7 and is one of the two reasons `/api/map` cannot simply serialise the file.

### A.3.7 Database schema (target)

Migration 001 (Phase 0) = the three tables that exist or are referenced today:

```sql
CREATE TABLE IF NOT EXISTS sensor_telemetry (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_id TEXT NOT NULL,
    tilt_x DOUBLE PRECISION, displacement_mm DOUBLE PRECISION,
    acceleration DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_telemetry_ts   ON sensor_telemetry (ts DESC);
CREATE INDEX IF NOT EXISTS ix_telemetry_node ON sensor_telemetry (node_id, ts DESC);

CREATE TABLE IF NOT EXISTS rfid_evacuation_log (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    miner_uid TEXT NOT NULL, current_zone TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Migration 002 (Phase 1) — widen telemetry:

```sql
ALTER TABLE sensor_telemetry
  ADD COLUMN IF NOT EXISTS vib INT, ADD COLUMN IF NOT EXISTS micro DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS macro DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS rssi INT,
  ADD COLUMN IF NOT EXISTS batt DOUBLE PRECISION, ADD COLUMN IF NOT EXISTS level SMALLINT;
```

Migration 003 (Phase 8) — alerts, with an outbox and a delivery ledger:

```sql
CREATE TABLE IF NOT EXISTS alerts_history (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level_id VARCHAR(16) NOT NULL, node_id VARCHAR(16) NOT NULL,
    severity VARCHAR(16) NOT NULL,          -- WATCH|ELEVATED|HIGH|CRITICAL
    event_type VARCHAR(64) NOT NULL,
    progression_stage VARCHAR(16),
    ttf_seconds INTEGER, ttf_q10 INTEGER, ttf_q90 INTEGER,
    disruption_label VARCHAR(24), disruption_index REAL,
    geo_lat DOUBLE PRECISION NOT NULL, geo_lon DOUBLE PRECISION NOT NULL,
    recommended_action TEXT NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'NEW',   -- NEW|ACTIVE|ACKED|CLOSED
    acked_by TEXT, acked_at TIMESTAMPTZ,
    sms_dispatched BOOLEAN DEFAULT FALSE, email_dispatched BOOLEAN DEFAULT FALSE,
    local_dispatched BOOLEAN DEFAULT FALSE, dash_dispatched BOOLEAN DEFAULT FALSE,
    attempts SMALLINT DEFAULT 0, last_error TEXT
);
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id BIGSERIAL PRIMARY KEY, alert_id BIGINT REFERENCES alerts_history(id),
    channel TEXT NOT NULL, ok BOOLEAN NOT NULL, detail TEXT,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS outbox (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind TEXT NOT NULL, payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING|SENT|FAILED
    attempts SMALLINT DEFAULT 0, last_error TEXT, sent_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_outbox_pending ON outbox (status, id) WHERE status='PENDING';
```

### A.3.8 Model artifacts & the manifest

`models/MANIFEST.json` pins which artifacts belong together — this prevents the demo-day failure mode of a new M1 running against an old scaler.

```json
{
  "manifest_version": 1,
  "schema": 2,
  "features": ["disp_now","…","ttf_fuk"],
  "model_1": {"file":"pattern_model_v1.json","kind":"RandomForestClassifier",
              "meta":"pattern_meta.json","threshold":0.42,"required":false},
  "model_2": {"file":"ttf_model_v2.json","kind":"XGBRegressor",
              "meta":"model_meta.json","quantiles":[0.1,0.5,0.9],"required":false},
  "scaler":  {"file":"scaler.json","required":false},
  "kinematic":{"file":"kinematic_calibration.json","required":false},
  "boot_policy": "ANY_MISSING_ARTIFACT => LOG + DEGRADE, NEVER CRASH"
}
```

---

## A.4 Configuration & constants registry

Everything below moves into `config.py` and is env-overridable (`NS_` prefix). Today these values are scattered as bare module-level literals in `app.py`, which makes the demo fragile: tuning a threshold means editing production code at 1 a.m.

| Constant | Today | Target | Owner phase | Why it matters |
|---|---|---|---|---|
| `ACC_CRITICAL` | `3.0` in `app.py` | same, env `NS_ACC_CRITICAL` | 0 | The runbook already tells the operator to bump this live. Should be env. |
| `ACC_WARNING` | `0.25` | same, env | 0 | Idem — runbook step 2. |
| `VEL_WARNING` | `0.03` | same, env | 0 | Currently inlined in `update_status_logic`. |
| `MODEL_BUF` | `121` | unchanged | 0 | 60 s at 2 Hz + 1. |
| `MODEL_FRESH_S` | `5.0` | unchanged | 0 | Pill falls back after 5 s of no prediction. |
| `FUK_V_MIN` | `1.0` | unchanged | 0 | Low-velocity samples poison the 1/v fit — validated in `01_data_extraction.py`. |
| `NODE_IDS` | hardcoded `("node_1","node_2")` × 5 sites | tuple in `config` | 1 | Defect D10. |
| `MICRO_RATE_THRESHOLD` | — | `15.0 µε/s` | 3 | B2. |
| `MACRO_JUMP_THRESHOLD` | — | `1.5 mm` | 3 | B2. |
| `CASCADE_MODE` | — | `"advisory"` | 5 | The Double AI's one safety switch. |
| `M2_SAMPLE_HZ` | — | dict by M1 class | 5 | Where the compute saving actually comes from. |
| `TTF_BANDS` | — | `(0,30,120,600)` | 6 | Severity → alert tier mapping. |

---

## A.5 Defect audit — 14 things in the current code that shape the phase order

Every one of these was read out of the live files, not inferred. They are listed here because **each phase's "changes to the previous project" section is really "how this phase closes defect D-n".**

| # | Defect | Where | Consequence | Closed in |
|---|---|---|---|---|
| **D1** | **`/api/map` route does not exist.** `gis-map.js` polls `/api/map` (2 s) and expects a projection-ready payload; `BACKEND_PLAN.md` §5 lists it as specified. There is also **no static-asset route** to serve `gis-map.js` itself. | `app.py` route list; `gis-map.js` header comment | The GIS card cannot load. Listed as "implemented" in the plan but absent in code. | **Phase 7** |
| **D2** | **`kinematic_calibration.json` is orphaned.** `app.py` never reads it. The live fallback is `ttf = v / a` (line 376) — a *different formula* from the trained `ttf_fuk` feature, which is a 1/v regression intercept. Two "kinematic TTF" definitions coexist. | `app.py:376` vs `05_calibrate_kinematic.py` | B11's deliverable is inert; the deck claims a calibrated fallback that isn't wired. | **Phase 6** |
| **D3** | **Feature count mismatch: 16 vs 14.** Code has 16 (`app.py::AI_FEATURES`, `02_training.py::FEATURES`). Docs say 14 in `BACKEND_PLAN.md` §3.2, Slide 11, and the `04_external_validation.py` docstring. | 4 files | A judge counting the list finds 16. | **Phase 0** |
| **D4** | **Three different MAE/dataset numbers in circulation.** `model_meta.json` says **MAE 0.10 s / RMSE 0.42 s, n=61, dataset `/tmp/synth_drill_dataset.csv`, 5,806 windows**. The SRS, Slide 11 and README say **MAE 5.4 s / RMSE 9.5 s, n=62**. README also says **7,870 windows**. | `models/model_meta.json` vs SRS §D / `gen_sih_ppt.py` vs `README.md` | The deployed artifact does not match the published claim. This is the single most dangerous thing in the repo for a viva. | **Phase 0** |
| **D5** | **Hardcoded `dt = 0.5`** in `calculate_derivatives`, regardless of true packet arrival interval. | `app.py` | v/a are scaled wrong whenever cadence drifts (jitter, relay delay, 4-node congestion). `acceleration` drives every threshold in the system. | **Phase 1** |
| **D6** | **`update_status_logic()` runs from two threads** (serial reader on every packet, ticker every 50 ms) | `app.py` | Duplicate/racy event-log edges, wasted work, unpredictable WARNING timestamps. | **Phase 6** |
| **D7** | **Route status hardcoded to node_1/node_2, and any node WARNING blocks the *whole* route.** | `app.py::update_status_logic` | Cannot express 2 levels / 4 nodes; a single noisy node blacks out an entire level's route. | **Phase 1 + 7** |
| **D8** | **No serial reconnect.** On `SerialException` the reader thread ends silently; the system drops to simulation forever with no operator alarm. | `app.py::hardware_reader_thread` | During a live demo a knocked USB cable = the system pretends everything is nominal. | **Phase 2** |
| **D9** | **Real acceleration is discarded.** MPU-6050 `acc` is available on the node but the frame carries only `disp` + `tilt`; acceleration is numerically differentiated. | firmware + `app.py` | Noisier physics than necessary; jerk feature is second-differentiated noise. | **Phase 1** |
| **D10** | **Node iteration hardcoded in 5 places** (`("node_1","node_2")` tuples) | `app.py` | Scaling to 4 nodes = 5 coordinated edits in one file. | **Phase 1** |
| **D11** | **DB connection per call at 2 Hz**, and **no schema/migration path** — `alerts_history` exists only in the plan. | `app.py::log_telemetry_rows` | Connection churn; nothing persists alerts. | **Phase 0 + 8** |
| **D12** | **`events_log` is in-memory, capped at 64.** | `app.py::push_event` | No audit trail, violates the SRS "muster audit trail" and business rules. | **Phase 8** |
| **D13** | **Re-arm does not clear the AI buffers.** `reset_system()` sets `displacement_mm = 5.0` but leaves the 121-sample deque untouched, so the 60 s window still contains post-breach displacement for up to a minute. | `app.py::reset_system` | The runbook's "wait ~65 s → ACTIVE again" is **masking this bug**, not a warm-up. After a drill the model would otherwise keep predicting ≈0 s. | **Phase 1** |
| **D14** | **Off-by-drift in simulation:** `apply_injections(0.05)` assumes the ticker's period is exactly 50 ms, but each tick also does DB work and buffer work. | `app.py::ticker_thread` | Injected scenarios run slightly fast/slow; timings in the runbook are approximate. | **Phase 1** |

---

## A.6 Phase map, dependencies & effort

```
               ┌───────────────────────────────────────────────┐
               │  P0  Ground truth, contracts, replay harness   │  1.5 d
               └───────────────┬───────────────────────────────┘
                               ▼
               ┌───────────────────────────────────────────────┐
               │  P1  B1  4-node state + ingestion hardening    │  3 d
               └───────┬───────────────────────┬───────────────┘
                       ▼                       ▼
       ┌───────────────────────────┐  ┌─────────────────────────────┐
       │  P2  B7  health/watchdog  │  │  P3  B2  two-stage crack    │  2 d
       └───────────────┬───────────┘  └───────────┬─────────────────┘
                       │                          ▼
                       │              ┌─────────────────────────────┐
                       │              │  P4  B8  elongation/zones   │  1 d
                       │              └───────────┬─────────────────┘
                       │                          ▼
                       │        ┌──────────────────────────────────────────┐
                       └───────►│  P5  ★ THE DOUBLE AI (M1 + M2 + cascade) │  5 d
                                └───────────────┬──────────────────────────┘
                                                ▼
                                ┌──────────────────────────────────────────┐
                                │  P6  B3  severity/progression/confidence  │  3 d
                                └───────────────┬──────────────────────────┘
                                                ▼
                    ┌───────────────────┬───────────────────────┐
                    ▼                   │                       │
  ┌──────────────────────────┐          │                       │
  │  P7  B5  Dijkstra + /map │  3 d     │                       │
  └───────────────┬──────────┘          │                       │
                  ▼                     ▼                       │
        ┌──────────────────────────────────────────────┐        │
        │  P8  B4  persistent alert dispatcher         │  4 d   │
        └───────────────┬──────────────────────────────┘        │
                        ▼                                       │
        ┌──────────────────────────────────────────────┐        │
        │  P9  B6  cloud sync outbox                   │  2 d   │
        └───────────────┬──────────────────────────────┘        │
                        ▼                                       ▼
        ┌──────────────────────────────────────────────────────────┐
        │  P10  B12 gated transfer learning + observability + ops  │  3 d
        └──────────────────────────────────────────────────────────┘
```

| Phase | Title | B-ID | Effort | Demo-critical | Depends on | Closes |
|---|---|---|---|---|---|---|
| 0 | Ground truth, contracts, replay | — | 1.5 d | **YES** | — | D3, D4, D11a |
| 1 | 4-node state engine + ingestion | B1 | 3 d | **YES** | 0 | D5, D7a, D9, D10, D13, D14 |
| 2 | Health, watchdog, degraded mode | B7 | 1.5 d | **YES** | 1 | D8 |
| 3 | Two-stage crack engine | B2 | 2 d | **YES** | 1 | — |
| 4 | Elongation, settlement, zone risk | B8 | 1 d | no | 3 | — |
| 5 | **Double AI cascade** | — | 5 d | **YES** | 1, 3 | (enables M1) |
| 6 | Severity, progression, confidence | B3 | 3 d | **YES** | 5 | D2, D6 |
| 7 | Dijkstra routing + `/api/map` | B5 | 3 d | **YES** | 1, 4 | D1, D7b |
| 8 | Persistent alert dispatcher | B4 | 4 d | **YES** | 6, 7 | D11b, D12 |
| 9 | Cloud sync outbox | B6 | 2 d | no | 8 | — |
| 10 | Transfer gate, metrics, ops | B12 | 3 d | no | 5 | — |

**Parallel tracks for a 6-person team:** after Phase 1 splits, one pair can run P2→P4 (sensors/geomechanics) while another pair runs P5 (AI) and a third runs P7 (routing) — but **P5 must not start before P3 lands**, because the crack channels are Model 1's single most valuable input.

---
# PART B — THE PHASES

Every phase uses the same nine-part template. Read **§5 "Changes to the existing project"** before writing any code — that is the section that prevents the currently-working demo from breaking.

---
---

## PHASE 0 — Ground Truth, Contracts & Replay Harness

> **Goal** — Freeze the interfaces, settle the numbers, and build the ability to develop *without hardware*.
> **Effort** — 1.5 dev-days · **Demo-critical — YES** · **Depends on** — nothing
> **Demoable result** — `pytest` passes; `python3 tools/replay.py session.csv` drives the real pipeline at 10× speed; `tools/check_numbers.py` proves the deck, SRS and `model_meta.json` agree.

### 0.1 Audit — what exists today

- `app.py` (696 lines) mixes configuration, state, ingestion, feature extraction, inference, routing flags and HTTP routes in one module. 24 functions, all module-level globals.
- `models/` holds `ttf_model_v1.json`, `scaler.json`, `model_meta.json`, `kinematic_calibration.json`.
- The three numbers quoted publicly **disagree** (defects D3 and D4).
- There is no test suite, no `requirements.txt` in the repo, no schema/migration file, and no way to exercise the pipeline without either USB hardware or the dashboard.
- `01_data_extraction.py` reads PostgreSQL; `02_training.py` reads the CSV it writes. Both are run manually.

### 0.2 Target behaviour

1. **One canonical metrics set.** Exactly one training run is "the" run. It produces `neuralstrata-ai/export/training_report.txt` plus `models/model_meta.json`, and every document quotes that file — or is changed to.
2. **Contracts are code, not prose.** `contracts.py` owns schema version, node IDs, and frame validators. A malformed frame is rejected with a reason, not a `KeyError`.
3. **The pipeline is replayable.** Record a live session once, then develop every later phase offline, deterministically, at 10× speed. This is what makes Phases 1–10 cheap.
4. **The database has a schema file and migrations.** No more "the table is in the plan but not in the DB".
5. **Boot is guarded.** `validate_config()` fails loudly on nonsense (negative thresholds, missing model dir, port out of range) *before* Flask starts.

### 0.3 Data contracts introduced

- `SCHEMA_VERSION = 2` (definitions in §A.3.1–A.3.4).
- `schema.sql` migration 001 and 002 (§A.3.7).
- `models/MANIFEST.json` (§A.3.8).
- `session.csv` format for record/replay:

```
t_ms,node,disp,tilt,acc,vib,micro,macro,uid,source
0,1,5.00,0.00,0.01,0,,,,hardware
500,1,5.01,0.00,0.02,0,,,,hardware
```

### 0.4 Functions delivered in Phase 0

| Module | Functions |
|---|---|
| `config.py` | `load_dotenv`, `env_str/int/float/bool`, `validate_config`, `describe` + all constants |
| `contracts.py` | `SCHEMA_VERSION`, `coerce_float`, `validate_node_frame`, `validate_ring_frame`, `validate_rfid_frame`, `NodeState`, `TelemetryPayload`, `migrate_payload` |
| `engine/features.py` | `FEATURES`, `FEATURE_INDEX`, `live_val_at`, `compute_live_features` (moved verbatim out of `app.py`), `window_complete` |
| `engine/ttf.py` | `load_ttf_model`, `predict_ttf`, `sanity_gate`, `model_status` |
| `persistence/db.py` | `init_schema`, `migrate`, `get_conn`, `release`, `insert_telemetry`, `insert_rfid` |
| `tools/` | `record.main`, `replay.main`, `check_numbers.main` |
| `tests/` | `test_contracts`, `test_features`, `test_api_contracts` |

### 0.5 Changes to the existing project

#### `app.py`
- **Delete** `_load_dotenv()` → import from `config`.
- **Delete** the module-level constant block (`ACC_CRITICAL` … `MODEL_BUF`) → import from `config`.
- **Delete** `AI_FEATURES`, `_live_val_at()`, `compute_live_features()` → import from `engine.features`.
- **Delete** `load_model()` → call `engine.ttf.load_ttf_model()` at boot; keep the same printed lines (`[ai] TTF model loaded: …`) so the runbook still matches.
- **Replace** `get_db_connection()` body with the pooled `persistence.db.get_conn()`; keep the name as a thin shim so nothing else changes.
- **Add** at the bottom of `main()`: `validate_config()` call, and a `--replay FILE` argument that swaps the serial thread for the replay source.
- **No route changes. No behaviour changes.** This phase must be a pure extraction — the git diff should show *moves*, not logic edits.

#### Firmware
- **No change.** (Phase 0 is server-only. The hub keeps sending v1 frames, and `migrate_payload` accepts them.)

#### Frontend
- **`neuralstrata-frontend/index.html`** — add a "REC ●" recorder toggle that calls `POST /api/admin/record?on=true`. Optional; the CLI recorder is enough. **No other change** — `/api/telemetry` is byte-compatible.

#### Database
- Run `schema.sql` migrations 001 + 002. Existing rows are untouched (`ADD COLUMN IF NOT EXISTS`).
- **Do this on the Mac before the next rehearsal**, and re-run `01_data_extraction.py` afterwards: the widened `sensor_telemetry` means the extraction query can now pull `acc` directly instead of only `displacement_mm`.

#### AI notebooks / models
- **Re-run `02_training.py` once** on the *current* dataset, and treat that run as canonical. Then fix every downstream copy. Here is the exact audit, done on 18 Sep 2026 — work down it, and tick each line:

**D4 — every place the MAE appears (the dangerous one):**

| File | Line(s) | Currently says | Action |
|---|---|---|---|
| `models/model_meta.json` | — | `held_out_critical_mae_s: 0.1`, `rmse: 0.42`, `n_windows_total: 5806`, `dataset: /tmp/synth_drill_dataset.csv` | Point `dataset` at the real repo path; publish this run's own numbers |
| `gen_sih_ppt.py` | 199, 392, 491, 512, 584 | "MAE 5.4 s" ×5 | One value, from the canonical run |
| `gen_srs_v11.py` | 303, 343 | "MAE 5.4 s", "n = 62 windows" | One value |
| `Neural_Strata_SRS_v1.1.md` | 580, 634 | "MAE 5.4 s / RMSE 9.5 s (n = 62 windows)" | One value |
| `Neural_Strata_Backend_Plan.md` | 45, 81, 82 | "proven 5.4 s MAE"; transfer gate `< 5.4 s` | Gate must reference the canonical value, not a literal |
| `neuralstrata-backend/BACKEND_PLAN.md` | 45 | "proven 5.4 s MAE" | One value |
| `neuralstrata-ai/README.md` | 12 | "held-out critical MAE 5.4 s" | One value |

**D3 — every place the feature count appears:**

| File | Line(s) | Currently says | Action |
|---|---|---|---|
| `BACKEND_PLAN.md` + `Neural_Strata_Backend_Plan.md` | 52 | "the exact same **14-feature** rolling window extractor" | → **16** |
| `gen_sih_ppt.py` | 390, 391 | "identical **14-feature** pipeline", "Features (**14**):" | → **16** |
| `notebooks/04_external_validation.py` | 5, 175 | "our **14-feature** windowing" | → **16** |
| `export/external_validation_report.txt` | 16 | "the exact same **14-feature** pipeline" | → **16**, then regenerate the report |
| `Neural_Strata_SRS_v1.1.md` | 264, 517, 626 | "16 features" | ✅ **already correct — do not touch** |
| `notebooks/02_training.py` | 12 | "the 16 numeric columns" | ✅ already correct |

**D4b — the dataset-size claim, which currently shows three different numbers:**

| Source | Says |
|---|---|
| `models/model_meta.json` | **5,806** windows |
| `Neural_Strata_SRS_v1.1.md` (100, 267, 615, 626) and `README.md` (10) | **7,870** windows, 18 runs (9 breach / 9 stable) |
| `BACKEND_PLAN.md` §3.4 and Slide 11 | **50,000+** — the augmentation *target*, which no shipped artifact has been trained on yet |

These three are not necessarily wrong — they describe three different datasets (the raw harvest, the run that produced the shipped artifact, and the augmented set). **But nothing in the repository says that**, which is exactly how a contradiction looks from the outside. Phase 0's job is to make the sentence exist: add a **Dataset provenance** table to `neuralstrata-ai/README.md` naming each dataset, its window count, its row count, and which artifact was trained on it. Then a reader can see 5,806 and 7,870 and 50,000 on the same page and understand rather than doubt.

- **Add** `tools/check_numbers.py`: greps `gen_sih_ppt.py`, `Neural_Strata_SRS_v1.1.md` and `README.md` for a `MAE x.x s` pattern and a `(\d+)-feature` pattern, compares them against `model_meta.json`, and exits non-zero on drift. Run it in the pre-demo script so this class of bug cannot come back.

### 0.6 Why "one canonical number" is worth a whole phase

Because the alternative already happened. The repository currently says 5.4 s in four public places and 0.10 s in the artifact that actually ships. A judge who opens `models/model_meta.json` — and one will — either finds a smaller number (good) and wonders why the deck is pessimistic, or finds a *larger* one next time and concludes the numbers were decorative. Neither is survivable. Pick the run, publish the run, delete the rest. If the two runs differ legitimately (different dataset sizes), publish **both** with their n and dataset — a table is fine, a contradiction is not.

### 0.7 Acceptance tests

```bash
cd ~/NEURAL_STRATA/neuralstrata-backend
conda activate ai_env

python -m pytest tests/ -q                 # all green
python tools/check_numbers.py              # exit 0, prints the canonical row
python app.py --no-serial                  # boots, /api/telemetry unchanged
curl -s localhost:5000/api/telemetry | python -m json.tool   # same keys as before

python tools/record.py --seconds 120 --out /tmp/session.csv
python tools/replay.py /tmp/session.csv --speed 10 --no-serial
curl -s localhost:5000/api/ai             # identical shape to before
```

**Pass criteria:** the replayed session produces the *same* `/api/ai` transitions as the live one, and `git diff` on `app.py` shows deletions of moved code, not rewrites of logic.

### 0.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| A "pure extraction" phase accidentally changes behaviour | Snapshot `/api/telemetry` and `/api/ai` JSON before and after; `diff` them. Keep the snapshots in `tests/fixtures/`. |
| Touching the DB on the Mac breaks the rehearsal DB | Migrations are additive-only (`IF NOT EXISTS`, no drops). Take `pg_dump` first. |
| Changing the deck/SRS numbers late | Do it now, in one commit, with `check_numbers.py` enforcing it forever. |

**Rollback:** `git revert` the phase. Nothing external depends on it. The DB columns added are harmless if unused.

### 0.9 Viva lines

> *"Before we added features we added a contract. The wireless frame, the API payload and the 16-feature order are all versioned, and a script proves the numbers in our slides match the model we actually ship. That's what makes the next nine phases safe to build."*

---
---

## PHASE 1 — B1: 4-Node Multi-Level State Engine & Ingestion Hardening

> **Goal** — Make the backend genuinely 4-node / 2-level, and make the physics it computes correct.
> **Effort** — 3 dev-days · **Demo-critical — YES** · **Depends on** — Phase 0
> **Demoable result** — all four nodes live on the dashboard with real velocity/acceleration, and a Re-arm produces an *immediately* valid forecast instead of a 65-second lie.

### 1.1 Audit — what exists today

- `telemetry_state["nodes"]` has exactly two keys. `node_buffers`, `model_ttf`, `history`, `peak_accel`, `injections`, `injection_t` are all two-key dicts.
- The literal tuple `("node_1", "node_2")` appears in **five** functions: `snapshot_history`, `run_model_inference`, `update_status_logic`, `apply_injections`, `ticker_thread`, plus `reset_system` and `get_extended`.
- `calculate_derivatives(node_id, current_disp)` hardcodes `dt = 0.5`.
- `tunnel_map.json` already contains `node_3` and `node_4` — **marked `"active": false`**. The map was written ahead of the code; this phase turns them on.
- `reset_system()` does not touch `node_buffers` (defect D13).
- The frame carries `disp` and `tilt` only; acceleration is numerically differentiated (defect D9).

### 1.2 Target behaviour

1. **One registry.** `config.NODE_IDS` and `config.LEVELS` are the only place the node set is written down. Every loop iterates `state.iter_nodes()`.
2. **Measured time.** Velocity and acceleration use the **frame-to-frame interval from the node's own `ms` field**, with EMA smoothing. A dropped frame stretches `dt` instead of corrupting `v`.
3. **Discontinuity-safe windows.** A Re-arm, a sensor re-baseline, or a >5 s data gap marks the window invalid (`window_complete()` returns `False`), so the model warms up in exactly 60 s of *valid* data — never on stitched-together pre/post-reset samples.
4. **Real acceleration ingested.** `acc` from the MPU-6050 is used directly when present; numerical differentiation is the fallback for v1 frames and hardware-free simulation.
5. **Per-level status.** `levels["1"]`, `levels["2"]` each carry status and route. `underground.route_A_status` is *derived* from the level, so the existing dashboard keys keep working.

### 1.3 Data contract

Node frame v2 as in §A.3.1; `telemetry_state` as in §A.3.5. `migrate_payload` upconverts a v1 `{"node":1,"disp":…,"tilt":…}` frame by synthesising `ms` from arrival time and `acc` from the differentiator, and flagging the frame `flags |= 8` (synthetic).

### 1.4 Functions delivered

| Function | Module | Note |
|---|---|---|
| `class SystemState` + 8 methods | `state.py` | Owns the lock, nodes, levels, events, buffers |
| `append_model_buffer` | `state.py` | Now writes 7 channels: `t, disp, tilt, acc, vib, micro, macro` |
| `mark_discontinuity` | `state.py` | Called by Re-arm / gap detection / baseline reset |
| `window_complete` | `engine/features.py` | Full 60 s **since the last discontinuity** |
| `compute_derivatives` | `ingestion/serial_reader.py` | Measured Δt, EMA 0.3/0.7 preserved (same smoothing as before, so thresholds do not need retuning) |
| `handle_node_frame`, `handle_ring_frame`, `parse_frame` | `ingestion/serial_reader.py` | Validation + dispatch |
| `validate_node_frame`, `migrate_payload` | `contracts.py` | |
| `health_tick` | `engine/health.py` | Called every tick even before Phase 2 fills it out |

### 1.5 Changes to the existing project

#### `app.py`
- **`telemetry_state`** — rebuild from `SystemState()`. Keep the exact JSON shape the frontend reads; add `nodes.node_3`, `nodes.node_4`, `levels`, `schema`, `degraded_mode`.
- **`calculate_derivatives`** — **modified**: signature gains `dt`; replace the constant. Keep the EMA factors at 0.3/0.7 so `ACC_WARNING = 0.25` stays calibrated. Add a `dt` sanity clamp: `0.05 ≤ dt ≤ 2.0`, else skip the sample and count a `derivative_skips` metric.
- **`reset_system()`** — **add**: `state.reset()` which now (a) clears every node buffer, (b) calls `reset_crack_baseline` for nodes 2 & 4, (c) writes a `WINDOW_INVALIDATED` event. This is defect **D13** — after this phase the runbook's "~65 s" wait becomes a *true* 60 s warm-up, and a Re-arm mid-drill stops leaving a poisoned window.
- **Every `("node_1","node_2")` literal** → `state.iter_nodes()`. There are 5 sites; check them off one by one and add a regression test that greps `app.py` for the literal.
- **`update_status_logic()`** — iterate all active nodes; compute `levels[L]` from `LEVELS`; derive `route_A_status`/`route_B_status` from them for compatibility. Mark **D7a** closed here; the proper hazard-cost routing is Phase 7.
- **`hardware_reader_thread()`** — replace the inline parsing with `parse_frame` → `handle_node_frame` / `handle_rfid_frame`. Performance note: this loop currently sleeps 50 ms and polls `in_waiting`; keep that cadence (it is proven) but move the work into the handlers.
- **`ticker_thread()`** — append all 7 channels to the buffer; use the measured tick duration for `apply_injections` (**D14**).
- **`get_telemetry()`** — unchanged output keys plus additive ones.

#### Firmware (`hub.cpp`, `node.cpp` / per-board PlatformIO envs)
- **`node.cpp`** — add to the ESP-NOW payload struct: `uint32_t ms; float acc; uint16_t vib; float micro; float macro; int8_t rssi; uint16_t batt; uint8_t flags;` and a `uint8_t v = 2;`. **Read the MPU-6050 acceleration registers directly** rather than deriving from tilt (**D9**).
- **`hub.cpp`** — bump the USB-serial JSON to the §A.3.3 envelope, but **keep emitting a bare `{"node":n,...}` frame if `#define FRAME_V1_COMPAT` is defined**, so you can flash the hub and the server independently, in either order. Never have a window where neither matches.
- **Level-hub firmware** — relay envelope with `via` and `seq`; forward frames received from the other level hub. **Do not** add per-junction logic; junctions remain passive WS2812B driven by the level hub.
- **Bench-calibrate the BF350 + HX711 on nodes 2 & 4 before flashing** (15 s settling, then baseline). This is the Phase 3 input; doing it now saves a second flash cycle.

#### Frontend
- **`index.html`** — node cards generated from `Object.keys(data.nodes)` instead of hardcoded `node_1`/`node_2` divs. Add a Level 1 / Level 2 grouping header. Chart series keyed by node id.
- **`gis-map.js`** — **no change yet** (it still polls a 404 until Phase 7). Optionally hide the card behind a feature flag so the console stays clean during Phase 1 testing.

#### Database
- Apply migration 002 (§A.3.7).
- `log_telemetry_rows` — extend the row tuple to include `vib, micro, macro, rssi, batt, level`; use `executemany` as today.

#### AI notebooks / models
- **`01_data_extraction.py`** — no logic change, but the extraction query should now prefer the DB's `acceleration` column note: this column is still the *derived* acceleration (the server writes it). Post-Phase-1 the preferred source is the frame's `acc`. Add a `--source {derived,raw}` flag; default `derived` so the existing dataset and model stay valid.
- **Do not retrain in this phase.** The 16-feature contract is unchanged, so `ttf_model_v1.json` remains valid — that is the point of freezing the contract in Phase 0.
- **`tools/replay.py`** must be able to emit 4-node sessions; extend `record.py` to capture all channels.

### 1.6 Algorithm detail — measured Δt

```
Δt_k = clamp( (ms_k − ms_{k−1}) / 1000 , 0.05 , 2.0 )      # node clock, not arrival clock
v_k  = (disp_k − disp_{k−1}) / Δt_k
a_k  = (v_k − v_{k−1}) / Δt_k
v̂_k  = 0.3·v̂_{k−1} + 0.7·v_k
â_k  = 0.3·â_{k−1} + 0.7·a_k
if Δt_k > 1.0 or dropped_frames > 0:  mark_discontinuity(nid, "gap")
```

Why the node clock: USB and the relay add variable latency; the node's `ms` counter does not. This is the difference between `acceleration` being a measurement and being a random number — and `acceleration` feeds **every** threshold in the product.

### 1.7 Acceptance tests

```bash
python -m pytest tests/test_state_reset.py tests/test_derivatives_dt.py -q
python tools/replay.py /tmp/session4node.csv --speed 5
curl -s localhost:5000/api/telemetry | python -m json.tool | head -60
grep -c 'node_1", "node_2' app.py        # must be 0
```

| Test | Assertion |
|---|---|
| `test_state_reset` | After `POST /api/reset`, `window_complete()` is `False`, and a forecast is not produced until 60 s of *new* samples. |
| `test_derivatives_dt` | Feeding frames at 0.25 s and at 1.0 s intervals with identical displacement slope yields **the same velocity** (±2%). With the old code they differ by 4×. |
| `test_ring_relay` | A Level-2 frame wrapped in a `via:"lvl2"` envelope updates `node_3`/`node_4` identically to a direct frame. |
| Manual | Pull the USB cable from node 1: only node 1 goes stale; nodes 2–4 keep updating. |

### 1.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| Firmware and server get out of sync mid-flash | The `FRAME_V1_COMPAT` define and `migrate_payload` make both orders of flashing safe. Flash the hub first with compat on, verify, then flash nodes. |
| EMA retuning breaks the thresholds | Keep 0.3/0.7 exactly. Re-tuning is a separate, evidence-backed change. |
| 4 nodes at 2 Hz = 8 DB rows/s | Batched `executemany` already handles this; if the DB is slow, raise `HISTORY_INTERVAL` for persistence only (chart history can stay at 2 Hz). |

**Rollback:** revert `app.py`; the DB columns are additive and harmless.

### 1.9 Viva lines

> *"A four-node system is not four copies of a two-node system. We moved the node set into one registry, so adding a node is a config line. And we fixed a real bug: acceleration was computed with an assumed 0.5-second interval, so any jitter corrupted the velocity — every threshold in our system depends on that number."*

---
---

## PHASE 2 — B7: Node Health, Watchdog & Degraded Mode

> **Goal** — The system must *know* when it has gone blind, and say so.
> **Effort** — 1.5 dev-days · **Demo-critical — YES** · **Depends on** — Phase 1
> **Demoable result** — unplug any node or the USB cable on stage: the dashboard shows a red `SENSOR LOST` banner with the node and the elapsed time, and the siren state is explicitly marked *unverified* rather than silently nominal.

### 2.1 Audit — what exists today

- `hardware_reader_thread()` connects once. On any exception it prints and the loop exits — the thread is gone for the rest of the process lifetime (defect **D8**).
- Nothing tracks when a node last spoke. If node 2's radio dies, `node_2` simply freezes at its last value and the dashboard shows a perfectly plausible NOMINAL.
- `system_armed` is the only health-ish flag.
- No link-level visibility: the J2↔J3 cross-link and the two level-hub uplinks are invisible to the server.

### 2.2 Target behaviour

1. **Heartbeat accounting.** Every valid frame updates `last_seen[t]`. `is_stale()` beyond `STALE_AFTER_S = 5.0` flips the node to `STALE`.
2. **Automatic serial recovery.** A `serial_watchdog` thread reopens the port with exponential backoff (0.5 → 1 → 2 → 5 s cap), emits `SERIAL_RECONNECTED`, and never leaves the process blind without an alarm.
3. **Degraded mode is a first-class state**, computed every tick:

| Mode | Condition | Behaviour |
|---|---|---|
| `FULL` | serial up, all 4 nodes fresh | Normal |
| `SENSOR_PARTIAL` | serial up, 1–3 nodes stale | Routing treats stale nodes as **unknown, not safe**; hazard penalty applied |
| `RING_SPLIT` | one relay leg dark, others live | Routing uses the surviving leg; the cut leg's zones are marked degraded |
| `NO_HARDWARE` | serial down | Simulation-only; banner; **all hardware-derived claims in the alert text are suppressed** |

4. **Watchdog alarm.** While armed, any dark node/link raises a persistent banner and an `alerts_history` row of type `INFRASTRUCTURE` — visible, but never mixed with a geological severity tier.

### 2.3 Data contract

Adds to `/api/telemetry`:

```json
"degraded_mode": "SENSOR_PARTIAL",
"nodes": { "node_3": {"...": "...", "last_seen_s": 14.2, "stale": true} },
"links": [ {"id":"lvl1→hub","state":"UP","last_seen_s":0.4},
           {"id":"lvl2→hub","state":"STALE","last_seen_s":8.1},
           {"id":"J2↔J3","state":"UP","last_seen_s":0.9} ]
```

New endpoint `GET /api/health` → node/link/model/DB health in one object, for the operator strip and for Phase 10's metrics.

### 2.4 Functions delivered

`health_tick`, `node_last_seen`, `is_stale`, `link_state`, `ring_cut_detect`, `degraded_mode`, `has_hardware`, `watchdog_alarm` (all `engine/health.py`); `serial_watchdog`, revised `hardware_reader_thread` (`ingestion/serial_reader.py`); `get_health` route.

### 2.5 Changes to the existing project

#### `app.py`
- **`hardware_reader_thread()`** — wrap the body in an outer `while not stop_event:` reconnect loop; catch `serial.SerialException` and `OSError` explicitly and route them to `serial_watchdog`. Move the `print`/`push_event` pair into a `SERIAL_LOST` / `SERIAL_RECONNECTED` event pair so the event log tells the story.
- **`ticker_thread()`** — call `health_tick(state, now)` each 0.5 s tick; store `degraded_mode` on the state.
- **`push_event`** — throttle infrastructure events to one per transition (not per tick) using an edge detector. Without this the log floods at 20 Hz.
- **New route** `GET /api/health`.
- **`main()`** — start `serial_watchdog` as a thread; pass `stop_event` everywhere so `Ctrl+C` is clean.

#### Firmware
- **`node.cpp`** — every ESP-NOW packet already carries RSSI at the radio level; add an explicit **heartbeat frame every 2 s even when nothing changed**, so a quiet node is distinguishable from a dead node. This is the single most important firmware change in this phase: *silence must be decidable*.
- **`hub.cpp`** — track per-node `last_seen` and emit `{"event":"node_lost","node":n,"after_s":6}` upstream so the server sees link death even if it misses the last data frame.
- **Level hubs** — emit the relay envelope's `seq` **monotonically**; a non-increasing `seq` means a reset/reflash, which the server must treat as a discontinuity (Phase 1's `mark_discontinuity`).

#### Frontend
- **`index.html`** — top strip gains: `DEGRADED MODE: SENSOR_PARTIAL` chip (amber), a per-node "last seen" dot, and a link indicator for `J2↔J3`. When `stale == true` the node card greys out and its last value is struck through — **never show a frozen number as if live.** This is a safety-presentation rule, not a cosmetic one.

#### Database
- Optional `infrastructure_events` table in migration 004, or reuse `alerts_history` with `severity = 'INFRASTRUCTURE'` (preferred — one audit trail, one query).

#### AI notebooks / models
- **No retraining.** But `features.py::feature_health()` should now be asserted in `test_features`: a stale node must **not** produce a feature vector. Feeding the model a frozen window is how a dead sensor becomes a confident "stable" prediction.

### 2.6 Algorithm detail — edge-triggered health events

```
for each node n:
    age = now − last_seen[n]
    stale_now = age > STALE_AFTER_S
    if stale_now and not stale_prev[n]:  emit NODE_STALE(n, round(age,1))
    if not stale_now and stale_prev[n]:  emit NODE_RECOVERED(n)
    stale_prev[n] = stale_now
```

Edge detection is what keeps a 20 Hz loop from producing a 20 Hz event log. It is also what makes the alert history readable to a human afterwards.

### 2.7 Acceptance tests

| Test | How |
|---|---|
| `test_health` | Feed frames for node 2, then stop; `is_stale` flips at 5 s ±0.2 s, exactly one `NODE_STALE` event is emitted. |
| Serial reconnect | `python app.py` with hardware, **unplug USB**, wait 10 s, replug. Expect `SERIAL_LOST` then `SERIAL_RECONNECTED`; telemetry resumes without a restart. |
| RING_SPLIT | Power down Level Hub 2; expect `degraded_mode == "RING_SPLIT"` if the cross-link carries node_3/4, else `SENSOR_PARTIAL`. |
| No-frozen-numbers | With a stale node, `GET /api/telemetry` marks `stale: true` and the UI struck the value. |

### 2.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| Aggressive staleness threshold causes flicker on a busy radio | 5 s is 10 missed frames — generous. If the diorama's ESP-NOW is lossy, raise to 8 s via `NS_STALE_AFTER_S` (env, no code change). |
| Reconnect loop fights the OS for the port | Backoff caps at 5 s and the port is always closed before reopening. |

**Rollback:** the health layer is additive; removing the `health_tick` call restores Phase 1 behaviour exactly.

### 2.9 Viva lines

> *"The most dangerous state for a safety system is not 'alert' — it's 'I don't know, and I look fine'. We track last-seen per node and per link, and we have an explicit degraded mode. If a node goes quiet, the dashboard greys it out and the routing engine treats it as unknown rather than safe."*

---
## PHASE 3 — B2: Two-Stage Crack Anomaly Engine

> **Goal** — Detect the *start* of a crack (micro-strain) separately from its *growth* (macro opening), because they mean different things at different times.
> **Effort** — 2 dev-days · **Demo-critical — YES** · **Depends on** — Phase 1
> **Demoable result** — bend the diorama's crack seam: the MICRO channel spikes first, then the MACRO channel follows seconds later, and the event log prints `Micro-crack initiated` → `Crack propagating rapidly` as two distinct events. **This two-stage timing is also the input that makes Model 1 work (Phase 5).**

### 3.0 Built — two decisions this phase had to make (recorded, not silently applied)

> **Annotation added when Phase 3 was built (22 Sep 2026).** The plan is the spec and
> every number below still stands; these two sentences exist so that the code and
> this document do not disagree on a reader's first pass.
>
> 1 · **`micro` is in µm of crack-mouth opening (CMOD), not µε.** The build pairs the
> HX711 with a **1 kg load-cell beam in tension across the seam** (§H2's mounting
> rule), so the fine channel measures elongation, and µm is the unit that rig can
> report honestly. §3.1's threshold keeps its physics: `15 µε/s × 100 mm gauge =
> 1.5 µm/s`, which is the value `backend/config.py` ships. §3.6's coherence and §3.7's
> event table are unchanged.
> 2 · **The seam runs in tension**, so a rising count is a rising opening. A slack
> spring — a channel that has stopped measuring — reports `FLAG_MICRO_UNLOADED`
> (bit 7), never a closing crack.
>
> Where the code and this document differ on wording, `backend/contracts.py` and
> `backend/config.py` are the machine-readable truth; `tools/check_contracts.py`
> fails the build if a frozen fixture disagrees.

### 3.1 Audit — what exists today

- No crack channels anywhere. `sensor_telemetry` has `tilt_x, displacement_mm, acceleration` only.
- The hardware design is **fixed by the user, final**: a two-stage sensor on nodes 2 & 4 — **BF350 foil strain gauge (350 Ω, GF ≈ 2.0) through an HX711** for micro-strain, and a **10 kΩ rotary potentiometer with a lever** for macro seam opening. Do not substitute either.
- The BF350 + HX711 combination has one known trap: **HX711 baseline drift immediately after power-up**. The plan already requires a 15 s settling window; this phase implements it as code, not as a note.
- `BACKEND_PLAN.md` §4.2 specifies thresholds `>15 µε/s` (micro rate) and `>1.5 mm` (macro jump). Those are the starting values.

### 3.2 Target behaviour

1. **Baseline, not absolute.** Raw µε is meaningless without a zero reference. On boot (and on Re-arm, and on sensor hot-swap) the engine averages `MICRO_SETTLE_S = 15 s` of samples and subtracts it. Until settled, the channel reports `null` and sets `flags |= 4`.
2. **Median-filtered rate.** `micro_rate` is `d(µε)/dt` over a 5 s window with a 3-point median filter, because HX711 stacks are noisy.
3. **Hysteresis, not a bare threshold.** A crossing must persist for 3 consecutive samples to raise an event, and must fall to 70% of the threshold to clear it. This eliminates the flicker that a bare `>` comparison produces.
4. **Two named events**, matching the plan's wording: `Micro-crack initiated` (rate) and `Crack propagating rapidly` (macro delta), each with its own severity contribution.
5. **A stage machine**: `NONE → INITIATION → PROPAGATION → CRITICAL`, driven by both channels and by whether the crack channels disagree with the displacement channel.
6. **`cross_sensor_coherence`** — a single scalar in `[0,1]` measuring whether the vibration channel *and* the strain channel agree that something structural happened. This is deliberately built here because **it is Model 1's most valuable feature** (Phase 5, §5.6).

### 3.3 Data contract

Adds to `sensor_telemetry`: `micro`, `macro` (already added by migration 002). Adds to the state:

```json
"node_2": {
  "micro": 412.5, "micro_rate": 18.4, "micro_baseline_ue": 388.1, "micro_settled": true,
  "macro": 3.20, "macro_d30": 0.42,
  "crack_stage": "PROPAGATION",
  "cross_sensor_coherence": 0.81
}
```

New events (level `WARNING`/`CRITICAL`, `event_type` fixed):
`CRACK_MICRO_INITIATION`, `CRACK_MACRO_PROPAGATION`, `CRACK_CRITICAL`.

### 3.4 Functions delivered

| Function | Module | Detail |
|---|---|---|
| `update_micro_baseline` | `engine/crack.py` | 15 s settling average; idempotent; returns 0.0 until settled |
| `micro_rate` | `engine/crack.py` | 5 s window, 3-point median, µε/s |
| `macro_opening` | `engine/crack.py` | mm from the pot ADC with a two-point calibration `(adc_lo, mm_lo, adc_hi, mm_hi)` |
| `classify_crack` | `engine/crack.py` | Hysteresis state machine → `CrackEvent` or `None` |
| `cross_sensor_coherence` | `engine/crack.py` | §3.6 |
| `crack_stage` | `engine/crack.py` | Derived stage |
| `reset_crack_baseline` | `engine/crack.py` | Called by `SystemState.reset()` |

### 3.5 Changes to the existing project

#### `app.py`
- **`handle_node_frame()`** (from Phase 1) — route `micro`/`macro` into `crack.update_*` before computing status, so a crack event can influence the same tick's status.
- **`update_status_logic()`** — add: `crack_stage == "CRITICAL"` forces node status to at least `WARNING` even if acceleration is quiet. Rationale: a seam that has opened 1.5 mm is a hazard with or without vibration.
- **`reset_system()`** — `reset_crack_baseline` for nodes 2 & 4 (already wired in Phase 1's `state.reset()`; verify).
- **`push_event`** — the two crack events go into the same event log with distinct text, so the dashboard's log shows the two-stage story.

#### Firmware
- **`node.cpp` (nodes 2 & 4 only)** — HX711 driver at 10 SPS (its native rate is 10 or 80; use 10 for noise), median of 2 readings, publish at 2 Hz. Map the pot through `analogRead` with `analogSetWidth(12)`.
- **Boot sequence**: on power-up the node sets `flags |= 4` (baseline not settled) for its first 15 s and reports `micro: null`. **Do not** let the server do the settling — the node owns its own zero, the server just trusts the flag.
- **Set `flags |= 1`** when the HX711 reading rails (open gauge / broken wire). An open gauge must read as *fault*, not as *zero strain* — these look identical in the data otherwise.
- **Level hub / main hub** — pass `micro`/`macro` through untouched. No processing in the hubs.

#### Frontend
- **`index.html`** — two new mini-gauges on nodes 2 & 4: `MICRO (µε)` with a rising-trend arrow, and `CRACK (mm)` with the stage label. Colour the stage chip: `NONE` grey, `INITIATION` amber, `PROPAGATION` orange, `CRITICAL` red.
- Show `"SETTLING 12s"` while `flags & 4`, so the operator isn't confused by a null field during warm-up.
- **Copy rule:** say "micro-crack sensor" and "crack-opening sensor", never "strain gauge module" — matches the terminology the user already fixed.

#### Database
- Migration 002 already covers the columns. Add `micro_rate` and `macro_d30` as *derived, not stored* — do not persist derived values; recompute on read.

#### AI notebooks / models
- **`03_synthetic_augmentation.py`** — it already synthesises `microstrain` and `crack_macro`, but with hard-coded couplings (`microstrain = 450` after onset; `crack_macro = (disp−10) × U[0.6,0.9]`). **Replace those with the calibrated couplings measured in this phase** — i.e. fit the real micro↔disp and macro↔disp relationships from the diorama and put the fitted coefficients into the generator. This is a genuine improvement: it makes the augmentation *physics-measured* rather than *physics-guessed*.
- Do not retrain yet. The 16-feature contract is unchanged (micro/macro are new channels, not new features) — Phase 5 adds them to the **extended** feature set used by Model 1.

### 3.6 Algorithm detail — `cross_sensor_coherence`

The core insight, stated plainly: **a passing truck moves the accelerometer but does not strain the roof.**

```
micro_z   = clamp( micro_rate / MICRO_RATE_THRESHOLD , 0, 1 )      # structural evidence
macro_z   = clamp( macro_d30  / MACRO_JUMP_THRESHOLD , 0, 1 )      # structural evidence
vib_z     = clamp( vib_count  / VIB_REFERENCE        , 0, 1 )      # non-specific evidence
tilt_z    = clamp( |tilt_d30| / TILT_REFERENCE       , 0, 1 )      # non-specific evidence

structural   = max(micro_z, macro_z)          # slow, accumulating, physical
nonspecific  = max(vib_z,  tilt_z)            # fast, impulsive, ambiguous

cross_sensor_coherence = 1 − |structural − nonspecific|
```

| Situation | structural | nonspecific | coherence | Meaning |
|---|---|---|---|---|
| Quiet | 0.0 | 0.05 | 0.95 | nothing happening |
| **Truck / machinery pass** | **0.02** | **0.90** | **0.12** | **vibration with no strain ⇒ environmental** |
| Blast nearby | 0.15 | 1.00 | 0.15 | impulsive, decays, no residual ⇒ environmental |
| **Micro-crack initiating** | **0.80** | **0.30** | **0.50** | **strain rising without matching vibration ⇒ structural** |
| Tertiary collapse | 1.00 | 0.85 | 0.85 | both high ⇒ unambiguous |
| Rain / seepage | 0.10 | 0.45 | 0.65 | slow tilt drift, no strain, no vibration ⇒ environmental, slow |

**Read the table again — this is the whole Dual-Model argument in one number.** The low-coherence cases (machinery, blast) are exactly the false alarms the ministry's problem statement complains about, and the high-coherence-with-micro cases are exactly the real hazards. A single scalar computed from physics separates them *before* any model is trained, and then Model 1 uses it as its strongest input.

Design note: coherence alone is **not** used to suppress anything — it is a feature, and the suppression decision is made in Phase 5 by a model that also sees duration, recovery and spatial extent. That separation is deliberate (see Rule 2).

### 3.7 Acceptance tests

| Test | Assertion |
|---|---|
| `test_crack_settle` | With `flags & 4` set, `micro` reports `null` and no crack event can fire; after 15 s + 1 sample, values appear. |
| `test_crack_hysteresis` | A single sample above threshold fires **nothing**; 3 consecutive samples fire **once**; a decay to 69% does not clear, to 71% does. |
| `test_crack_rail` | A railed HX711 (`flags & 1`) raises `SENSOR_FAULT`, never `CRACK_MICRO_INITIATION`. |
| `test_coherence_truck_vs_crack` | Replaying a synthetic machinery pass gives `coherence < 0.3`; replaying a synthetic structural onset gives `coherence > 0.45`. |
| Manual | Twist the crack lever slowly: expect `INITIATION` then `PROPAGATION`, with the micro gauge leading the macro gauge in time. |

### 3.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| HX711 noise swamps the µε/s rate | 3-point median + 5 s window + hysteresis. If still noisy, drop to a 10 s window before touching the threshold — the threshold is physics, the window is noise. |
| Pot mechanical backlash gives negative macro values | Clamp at 0 and log `BACKLASH` counts; do not let negatives subtract from the delta. |
| Two extra sensors = two more failure points on demo day | Both channels are `null`-safe: absent, railed or unsettled reads as *unknown*, never as *safe*. Verify by never connecting them and confirming the system still boots and runs. |

**Rollback:** the crack engine is additive. Setting both thresholds to `inf` disables events without touching structure.

### 3.9 Viva lines

> *"We detect the crack in two stages because they're physically different phenomena. Micro-strain on a foil gauge tells you damage has *started* — that's seconds to minutes before anything visible moves. The macro potentiometer tells you the seam is *opening* — that's already a hazard. And the relationship between the two is what separates a rock breaking from a truck driving past: a truck shakes the accelerometer but doesn't strain the roof."*

---
---

## PHASE 4 — B8: Pair Elongation, Differential Settlement & Zone Risk

> **Goal** — Turn four independent node readings into *one* statement about the ground: is the roof stretching, and which zone is worst?
> **Effort** — 1 dev-day · **Demo-critical — no** (but cheap, and it feeds routing in Phase 7) · **Depends on** — Phase 3
> **Demoable result** — a "ΔL N1↔N2" readout that grows during a collapse drill and stays flat when a single sensor is nudged.

### 4.0 Built — four decisions this phase had to make (recorded, not silently applied)

> **Annotation added when Phase 4 was built (22 Sep 2026).** The plan is the spec and
> every number below still stands; these sentences exist so that the code and this
> document do not disagree on a reader's first pass.
>
> 1 · **`pair_elongation` is signed, and positive means the pair *lengthened*.** §4.2
> and §4.6 write the row as a magnitude — `√((Δx₂−Δx₁)² + (Δy₂−Δy₁)²)` — which cannot
> say whether the ground stretched or closed. An operator needs that sentence, so the
> function returns the signed change in distance and the acceptance row's `|ΔL|` still
> holds. The pair moves along the axis `a → b`, because a node's pot measures motion
> along its own drift and the drift *is* that axis.
>
> 2 · **Each node has its own zero, captured on first sight, dropped by Re-arm.**
> ``displacement_mm`` is a calibrated reading from *that node's* pot span, so two pots
> at rest need not read the same number. §4.2's literal `|disp_a − disp_b|` would
> therefore report permanent settlement on a healthy level, on every build whose two
> pots were zeroed at different counts. ``differential_settlement`` differences each
> node's *movement since its baseline* instead: the same 0.0 mm when both are at rest,
> and the correct number when one has actually moved. §4.7's "store the baseline at
> boot" is what makes this possible.
>
> 3 · **`PAIR_STRAIN_WARN` is 400 µε, which is 28 mm across this diorama's 70 m
> baseline.** The plan says "start at 400 µε"; this is the arithmetic that says what
> the bench has to achieve for the rule to fire at all, asserted in
> `tests/test_geomech.py::test_the_level_threshold_is_a_reachable_number`. The
> threshold is env-overridable (`NS_PAIR_STRAIN_WARN`) and is the *only* tunable here:
> the baseline lengths and the zone polygons live in `assets/tunnel_map.json`, so a
> map edit re-baselines and nothing else has to be touched.
>
> 4 · **A link belongs to a zone only when both endpoints are inside it, and a leg
> that has never beaten is not dark.** `J2↔J3` crosses from A2 into B1, so neither
> zone owns it — blaming one would be arbitrary. And a relay leg that has never
> beaten is `UNKNOWN`, not `DOWN` (§2.2's own distinction): a boot that painted every
> zone amber because the hubs had not spoken yet would be a boot nobody reads twice.
>
> **One cross-phase defect is recorded, pinned, and deliberately NOT repaired here.**
> The plan's simulator profiles are *accumulators*: `displacement_mm` integrates
> acceleration and never relaxes, so on the bench an **environmental** profile (the
> loader rolling past) moves a node as far as a collapse does — ΔL ≈ 95 mm, ≈1357 µε,
> level WARNING, route BLOCKED. That contradicts the claim the whole build rests on
> ("a truck moves the accelerometer but does not strain the roof" — the sentence
> Phase 3's seam model exists to make true). It is not fixed in Phase 4 because the
> repair belongs to the phase that owns disturbance discrimination: Phase 5's Model 1
> already carries the `ENVIRONMENTAL` verdict and the `disruption_index`, and a pair
> layer that quietly discounted one profile would be a second, hidden author of the
> hazard verdict. `tests/test_geomech.py::test_a_truck_pass_exceeds_the_level_threshold_on_the_bench`
> is the pin — if Phase 5 fuses the environmental verdict into this escalation, that
> test is the one that must be rewritten, and it will fail loudly until it is.
>
> Where the code and this document differ on wording, `backend/contracts.py` and
> `backend/config.py` are the machine-readable truth; `tools/check_contracts.py`
> fails the build if a frozen fixture disagrees.

### 4.1 Audit — what exists today

- Nothing. `tunnel_map.json` gives each node `d_n_m`/`d_e_m` (metre offsets from the SW corner), which is all that is needed for a baseline length.
- The dashboard has no zone-level aggregation; risk is per node only.

### 4.2 Target behaviour

1. **Pair elongation** along each collinear tunnel pair: `node_1↔node_2` (Level 1, Tunnel A) and `node_3↔node_4` (Level 2, Tunnel B). `ΔL₁₂ = √((Δx₂−Δx₁)² + (Δy₂−Δy₁)²)` — i.e. the change in distance between the two nodes, not the change in either one.
2. **Pair strain** in µε: `ΔL / L₀`.
3. **Differential settlement** per level: `|disp_a − disp_b|` — catches the case where both nodes move together (fine) versus one moving relative to the other (not fine).
4. **Zone risk** = worst-of the nodes *and* links inside the zone polygon. This is the value Phase 7's `/api/map` colours the GIS card with.

### 4.3 Why "pair" matters more than "single node"

A single node's displacement is ambiguous: the whole panel can settle uniformly, or one end can move. Only the **difference** tells you the strata are deforming. That is the same reason land surveyors measure baselines rather than single marks, and it is a genuinely strong answer to *"why four nodes and not one?"*.

### 4.4 Functions delivered

`pair_elongation`, `pair_strain`, `differential_settlement`, `zone_risk` — all in `engine/geomech.py`.

### 4.5 Changes to the existing project

#### `app.py`
- **`get_extended()`** — add a `geomech` block: pair elongation, pair strain and per-level differential settlement.
- **`update_status_logic()`** — a pair strain above `PAIR_STRAIN_WARN` (start at 400 µε) raises the **level** status to `WARNING`, not the individual node. Rationale: the hazard is the ground between the nodes.

#### Firmware
- **No change.**

#### Frontend
- **`index.html`** — a "Tunnel A ΔL" / "Tunnel B ΔL" readout beside each level header, with a sparkline. Zone labels in the schematic take the `zone_risk` colour.

#### Database
- **No new columns** — derived values are recomputed, not stored. (If you want historical trend lines for pairs later, that belongs in Phase 9's `query_metric_series`.)

#### AI notebooks / models
- **`01_data_extraction.py`** — *optional but recommended*: add `pair_strain` as a candidate feature and run one experiment to see whether it helps the held-out MAE. If it does not improve the 5.4 s / 0.10 s baseline, **do not add it** — adding an unhelpful feature for narrative reasons is exactly what enlarges the gap between the reported and the real number. Decision recorded either way in the training report.

### 4.6 Acceptance tests

| Test | Assertion |
|---|---|
| `test_pair_elongation` | Displace node 1 by 3 mm along the tunnel axis and node 2 by 0 ⇒ ΔL = 3 mm; displace both equally ⇒ ΔL ≈ 0. |
| `test_zone_risk` | One node WARNING inside A2 ⇒ `zone_risk("A2") == "WARNING"`; all nodes NOMINAL ⇒ `NOMINAL`. |
| Manual | Nudge one pot only: ΔL moves, differential settlement moves, level status may not change. |

### 4.7 Risks & rollback

| Risk | Mitigation |
|---|---|
| Placeholder coordinates in `tunnel_map.json` make ΔL meaningless | The file already warns these are placeholders. Phase 7 forces the real measurement (cm from the SW corner) — do it once, correctly, before Phase 7. |
| Baseline length changes when a node is repositioned | Store `L₀` derived from the map at boot; a map edit automatically re-baselines. |

**Rollback:** purely additive readouts.

---

## PHASE 5 — ★ THE DOUBLE AI: Dual-Model Cascade

> **Goal** — Split the brain in two: **Model 1** decides *what kind of event this is* (structural or environmental), **Model 2** decides *when it will fail*. Neither one is allowed to make the system less safe.
> **Effort** — 5 dev-days · **Demo-critical — YES** · **Depends on** — Phase 1 (4-node state), Phase 3 (crack channels)
> **Demoable result** — trigger `machinery_pass` on node 2: the dashboard shows `ENVIRONMENTAL · ADVISORY`, **no SMS is sent**, and the alarm does not fire. Then trigger `warning_creep`: the same panel shows `STRUCTURAL`, the TTF counts down, and the alert escalates. That contrast *is* the pitch.

### 5.0 Built — two deviations the feature extractor had to make

> **Annotation added while Phase 5 was built (22 Sep 2026).** Both are in
> `engine/features.py`, both are covered by `tests/test_pattern_features.py`, and
> neither is applied silently.
>
> **1 · `impulse_periodicity` requires an *interior* autocorrelation peak.**
> §5.6 describes it as "autocorrelation peak of the acc window at 0.3–3 s lag".
> Implemented as a raw band maximum it measures **smoothness, not periodicity**:
> any smooth trace — a rock burst, an exponential decay — has its largest
> autocorrelation at the shortest lag and decays from there. On the first test a
> Gaussian burst scored **0.94** while a genuinely periodic signal scored **0.00** —
> the feature reported the opposite of its name. It now requires a strict interior
> local maximum scored by prominence (peak minus band floor), which is what
> "periodic" means: the correlation has to *come back up*. A monotone decay has no
> interior peak and scores 0.0, which is the correct answer for a rock.
> *Sampling caveat, stated so nobody over-claims:* the acc channel arrives at
> **2 Hz**, so Nyquist is 1 Hz and the 0.3–3 s band is lags 1–6. Periodicity above
> 1 Hz — an engine firing, an axle — is **not observable at this rate**; the
> feature separates the periodicity that survives sampling, not every machine on
> site.
>
> **2 · `settlement_ratio` is normalised into a velocity ratio.** §5.6 writes it as
> `|d60| / (|d10| + ε)`. Evaluated literally that is a 60-second delta divided by a
> 10-second delta, so a steady 1 mm/s gives **6.0** — the pivot is six, not one,
> and the number cannot be read as "1.0 = steady". Each delta is divided by its own
> window instead, giving a ratio whose pivot is 1.0 and whose orientation is
> **HIGH = decelerating, LOW = accelerating** — the same way round as
> `recovery_ratio` (#24), so the two features do not point in opposite directions.
>
> Where the code and this document differ on wording, `backend/config.py` and
> `backend/contracts.py` are the machine-readable truth.

### 5.1 Audit — what exists today

- **One model.** `ttf_model_v1.json` (XGBoost regressor) predicting TTF from 16 features, plus a kinematic `v/a` fallback.
- No classifier, no disturbance discrimination, no concept of "this vibration is not geological".
- `update_status_logic()` thresholds on acceleration alone — so **any** large vibration, from any cause, is `CRITICAL`. In a real pit, that includes every loaded haul truck.
- The scenario console (`apply_injections`) has exactly two profiles, `warning` and `critical`. There is no way to inject a *false alarm*.
- The 16 features contain **no vibration count and no strain channel** — because those channels were not ingested until Phase 1 and Phase 3. So Model 1 could not have been trained before this phase even if someone had wanted to.

### 5.2 The architecture — and one deliberate deviation from the reference design

The reference cascade in the original pitch is a **hard gate**: Model 1 says Pass, or Model 2 never runs. Neural Strata implements the cascade **inverted**:

```
                    ┌──────────────────────────────────────────────┐
   telemetry ──────►│              MODEL 1  (always on)            │
   2 Hz, 4 nodes    │  Deformation Pattern Discriminator           │
                    │  RandomForestClassifier · 200 trees · depth 8│
                    │  IN: 32 extended features                    │
                    │  OUT: class ∈ {QUIET, ENVIRONMENTAL,         │
                    │         STRUCTURAL, UNKNOWN}                 │
                    │       disruption_index ∈ [0,1]               │
                    └───────────────────┬──────────────────────────┘
                                        │  routing decision
        ┌───────────────────────────────┼────────────────────────────────┐
        │                               ▼                                │
        │   QUIET ──────► M2 @ 0.2 Hz        (compute saving lives here) │
        │   ENVIRONMENTAL ► M2 @ 1.0 Hz  + operator ADVISORY, no SMS     │
        │   STRUCTURAL ──► M2 @ 2.0 Hz  + alert tier escalation          │
        │   UNKNOWN ─────► M2 @ 2.0 Hz  + alert tier escalation          │
        │                                                                │
        │                  ┌──────────────────────────────────────┐      │
        └─────────────────►│        MODEL 2  (never gated off)    │      │
                           │  TTF Regressor                       │      │
                           │  XGBoost · quantile trio q10/q50/q90 │      │
                           │  IN: 16 core features                │      │
                           │  OUT: ttf_s, band, severity          │      │
                           └──────────────────┬───────────────────┘      │
                                              │                          │
                          ┌───────────────────▼───────────────────────┐  │
                          │        SAFETY FIREWALL  (Rule 2)          │  │
                          │  if M2_ttf ≤ CRITICAL_BAND → ALERT,       │  │
                          │     regardless of what M1 said            │  │
                          │  if M1=ENVIRONMENTAL and M2 low →         │  │
                          │     raise MODEL_DISAGREEMENT, escalate    │  │
                          └───────────────────┬───────────────────────┘  │
                                              ▼                          │
                                    severity → alerts (Phase 8)  ◄───────┘
```

**Why inverted, in one sentence you can say on stage:** *a gatekeeper classifier can only ever remove information from the safety system, and every removal it gets wrong is a fatality — so Model 1 is given authority over how often we compute and how loudly we shout, and zero authority over whether we warn.*

`CASCADE_MODE` is a config flag:
- `"advisory"` (**default, shipped**): as above.
- `"gate"`: honors the reference cascade — M2 runs only when M1 is not `QUIET`. **Still subject to the firewall**, so a low M2 TTF from a previous window keeps the alert live until it clears. Useful if a judge specifically asks to see the gated form; the demo can flip it live with one env var.

### 5.3 Data contract

New artifact `models/pattern_meta.json`:

```json
{
  "model": "pattern_model_v1",
  "algorithm": "sklearn.ensemble.RandomForestClassifier",
  "trained_at": "2026-09-27T11:04:00+00:00",
  "n_windows": 61248, "n_runs": 418,
  "features": ["…32 names…"],
  "classes": ["QUIET","ENVIRONMENTAL","STRUCTURAL","UNKNOWN"],
  "operating_threshold": 0.42,
  "calibration": {"target":"STRUCTURAL recall ≥ 0.98",
                  "achieved_recall": 0.984, "achieved_precision": 0.91},
  "metrics": {"macro_f1": 0.93, "quiet_fp_rate": 0.011,
              "env_suppression_precision": 0.95},
  "latency_ms_per_window": 0.31
}
```

`/api/ai` gains a block (additive — the existing keys do not move):

```json
"model_1": {"name":"pattern_model_v1","status":"ACTIVE",
            "label":"ENVIRONMENTAL","disruption_index":0.71,
            "structural_prob":0.09,"top_features":[["coherence",0.18],["recovery",0.14]],
            "threshold":0.42},
"cascade": {"mode":"advisory","m2_hz":1.0,"disagreement":false,
            "reason":"vibration with no strain, full recovery in 3.2 s"}
```

### 5.4 Which model for which job — and why

| | **MODEL 1 — Deformation Pattern Discriminator** | **MODEL 2 — Time-to-Failure Regressor** |
|---|---|---|
| **Question** | *What kind of event is this?* | *When will it fail?* |
| **Task** | 4-class classification (+ 2-class collapse) | Regression, seconds |
| **Recommended** | **`sklearn.ensemble.RandomForestClassifier`** (200 trees, `max_depth=8`, `min_samples_leaf=4`, `class_weight="balanced_subsample"`, `n_jobs=-1`) | **`xgboost.XGBRegressor`**, three models at `quantile_alpha` 0.1 / 0.5 / 0.9, `reg:quantileerror` |
| **Why this one** | ① **Decorrelated errors.** M2 is boosted trees; if M1 were also boosted trees the two would fail on the same windows. A bagged forest has a different inductive bias, so a common-mode failure is far less likely — this is a real engineering argument, not a preference. ② **`predict_proba` is native**, which is what makes `disruption_index` a *soft* number instead of a brittle boolean. ③ **No feature scaling** ⇒ one fewer contract (`scaler.json`) to keep in sync. ④ Bagging is parallel and cheap: 200 trees × depth 8 ≈ **0.3 ms** per window, so M1 can run at 2 Hz × 4 nodes for < 1% of the hub's CPU. ⑤ `feature_importances_` is directly quotable in the viva. | ① Tabular, small-n, non-linear, mixed scales — exactly XGBoost's home turf. ② **Native NaN handling**, which matters because `ttf_fuk` is legitimately missing on low-velocity windows (`FUK_V_MIN = 1.0`); a linear model would need imputation. ③ **Quantile regression gives a prediction interval** — reporting *"TTF 6–11 s (80% band)"* instead of a bare "8 s" is what turns a hackathon demo into a safety system. ④ **`monotone_constraints` encodes physics**: TTF must not increase when displacement or velocity increase. That is a free, defensible prior that stops unphysical wiggles. ⑤ Already built and validated — this is an upgrade, not a rewrite. |
| **Rejected** | **XGBoost for M1** (correlated failure modes with M2, needs a scaler, redundant). **Logistic regression** (cannot capture the cross-sensor interaction that *is* the signal, without hand-built products). **LSTM/CNN** (both are latency-heavy, unexplainable, and there is no data volume to justify them). **Z-score anomaly detection** (it is what we are replacing — it cannot tell a truck from a collapse, which is the entire complaint). | **LSTM** (5.8k–60k windows of *tabular* features; a sequence model adds latency and removes explainability for no measurable gain). **Gaussian Process** (O(n³) — unusable at this n). **Linear/polynomial regression** (the TTF surface is strongly non-linear in `v10`; the existing tree model already beats any polynomial fit). |
| **Optional third** | **`IsolationForest`** as an unsupervised novelty layer: catches *unknown* disturbance types that were never labelled. Report-only, never suppressing. | — |
| **Anti-overfit controls** | Split **by `run_id`**, never by random row — a random row split leaks the same event into train and test and produces a beautiful, meaningless 99% accuracy. Report **per-class** recall, never accuracy, on a 9:1 imbalanced problem. | Keep the existing v2 split design (held-out critical run + validation run). Breach sample weight 5×. |

### 5.5 The training set — where does the "truck" data come from?

There is no haul truck in the diorama, and no public dataset of mine machinery vibration aligned to a same-scale crack event. So the Model-1 training set is **generated counterfactually, in-domain** — the same methodological choice already made and defended for Model 2's augmentation (Tier 3). Seven classes:

| # | Class | Family | Physics signature |
|---|---|---|---|
| 1 | `QUIET` | — | steady creep, no disturbance |
| 2 | `MACHINERY` | ENVIRONMENTAL | impulse train 0.5–4 s, high `vib`/`acc`, `micro` flat, `macro` unchanged, **full recovery**, single node |
| 3 | `BLAST` | ENVIRONMENTAL | very high `acc` with ringing decay 2–8 s, `vib` saturates, `micro` transient only, full recovery |
| 4 | `RAIN_SEEPAGE` | ENVIRONMENTAL | slow monotonic `tilt` drift over minutes, **no** `vib`, no `micro` |
| 5 | `SENSOR_FAULT` | INSTRUMENT | one channel ramps alone with no cross-sensor support (e.g. `disp` drifts while `tilt`, `acc`, `vib` stay flat) |
| 6 | `STRUCTURAL_INITIATION` | **STRUCTURAL** | `micro` rate crosses first, `vib` low, `macro` starts, **no recovery** |
| 7 | `STRUCTURAL_TERTIARY` | **STRUCTURAL** | everything rises together, accelerating, no recovery, often multi-node |

Generator: `tools/make_counterfactuals.py` → `neuralstrata-ai/datasets/pattern_dataset.csv`, using the **same** simulation scaffolding as `03_synthetic_augmentation.py` so the two datasets are physically compatible.

Primary metric collapses classes 2–5 to `ENVIRONMENTAL` and 6–7 to `STRUCTURAL`.

### 5.6 The 16 disruption features (Model 1's extended set = 16 core + 16 new)

The core 16 (unchanged) plus:

| # | Feature | Definition | What it separates |
|---|---|---|---|
| 17 | `vib_count_60` | SW-420 events in 60 s | impulsive vs continuous |
| 18 | `vib_rate_10` | events in the last 10 s, per s | impulse train detection |
| 19 | `micro_rate` | µε/s, median-filtered (Phase 3) | **structural evidence** |
| 20 | `micro_z` | `micro_rate / MICRO_RATE_THRESHOLD`, clamped | normalised structural evidence |
| 21 | `macro_d30` | mm opening in 30 s | macro propagation |
| 22 | `macro_total` | mm since baseline | accumulated damage |
| 23 | `cross_sensor_coherence` | §3.6 | **THE headline feature** |
| 24 | `recovery_ratio` | `(peak − current) / (peak − baseline)` over the last 60 s | **1.0 = fully recovered (machinery); ~0 = accumulating (structural)** |
| 25 | `excursion_duration_s` | seconds above `ACC_WARNING` continuously | seconds = truck, minutes = creep |
| 26 | `impulse_periodicity` | autocorrelation peak of the acc window at 0.3–3 s lag | **periodic = engine/axle; non-periodic = rock** |
| 27 | `zero_cross_rate` | acc sign changes per second | spectral roughness proxy |
| 28 | `settlement_ratio` | `|d60| / (|d10| + ε)` | slowing (recovering) vs accelerating |
| 29 | `spatial_coherence` | mean pairwise correlation of `acc` across active nodes | **local ⇒ machinery; multi-node ⇒ ground** |
| 30 | `spatial_decay` | ratio of the second node's response to the first | distance-decaying ⇒ a surface source |
| 31 | `n_active_anomalous` | count of nodes currently in anomaly | how widespread |
| 32 | `tilt_acc_coupling` | correlation of `tilt_d30` with `accel_mean60` | machinery tilts *with* its vibration; a crack does not |

Features 24, 26, 29 and 32 are the four that do most of the separation work, and each one has a one-line physical justification — which is what makes this defensible rather than hand-wavy.

### 5.7 The scheduler — where the compute saving actually comes from

```
M1_HZ = 2.0     (always — it must catch a 0.5 s truck impulse)
M2_HZ = { QUIET: 0.2,  ENVIRONMENTAL: 1.0,  STRUCTURAL: 2.0,  UNKNOWN: 2.0 }

per tick (50 ms):
    if now - last_m1 >= 1/M1_HZ:  run M1 for every fresh node          # ~1.2 ms total for 4 nodes
    for nid in inference_scheduler(state, now):                        # respects M2_HZ
        run M2 for nid
```

**Honest framing (important):** the saving is **hub CPU and database write volume**, not sensor battery. In this prototype the inference runs on the operator's laptop (SRS §D.4), and the nodes just stream numbers. Do **not** claim battery savings for the node — a judge who has read your architecture diagram will ask, and the correct answer is:

> *"The saving is on the hub: M2 is a 400-tree boosted ensemble running four nodes, and Model 1 throttles it to 0.2 Hz when the site is quiet — a 10× reduction in steady-state inference load, which directly reduces thermal load and DB writes on the on-site server. In production, Model 1 gets a second job: it gates the **uplink cadence**, so a node in a quiet period reports every 2 s instead of every 0.5 s — and that does save node battery."*

That last sentence is honest, is a real design, and lands better than a claim that is wrong.

### 5.8 Functions delivered

| Module | Functions |
|---|---|
| `engine/pattern.py` | `load_pattern_model`, `compute_disruption_features`, `predict_pattern`, `PatternResult`, `explain_pattern`, `operating_threshold`, `is_structural` |
| `engine/ttf.py` (extend) | `predict_ttf_band` (quantile trio) |
| `engine/cascade.py` | `CascadeDecision`, `run_cascade`, `inference_scheduler`, `cascade_tick`, `route_decision`, **`firewall_check`**, `disagreement_flag`, `explain_decision` |
| `ingestion/simulator.py` | `SCENARIOS` (7 profiles), `ScenarioProfile`, `start_scenario`, `stop_scenario`, `scenario_progress` |
| `engine/features.py` (extend) | `compute_extended_features` (32) |
| `tools/` | `make_counterfactuals.main`, `eval_cascade.main`, `bench_latency.main` |
| `tests/` | **`test_cascade_safety`**, `test_pattern_features`, `test_scheduler_rates`, `test_counterfactual_labels` |
| Notebook | `neuralstrata-ai/notebooks/06_train_pattern.py` |

### 5.9 Changes to the existing project

#### `app.py`
- **`run_model_inference()`** → **delete**; replaced by `cascade.cascade_tick(state, now)` in the ticker. Keep the same 1 Hz *upper* cadence; the scheduler may now call it less often.
- **`ticker_thread()`** — one call site changes: `if t0 - last_infer >= 1.0: run_model_inference()` becomes `cascade.cascade_tick(state, now)`. The ticker no longer needs to know how many models exist.
- **`update_status_logic()`** — the TTF pill now prefers `CascadeDecision.route`. Preserve the existing fallback chain exactly: cascade → M2 fresh → kinematic → `"N/A"`. The dashboard must never see a missing value.
- **`get_ai()`** — add the `model_1` and `cascade` blocks (§5.3). **Do not move or rename any existing key** — `index.html` reads `model`, `status`, `horizon`, `last_train`, `confidence`, `note`, `forecasts`.
- **`trigger_scenario()`** — accept `{"node":"node_2","profile":"machinery_pass"}` **and** keep accepting `{"severity":"warning"|"critical"}` by mapping severity → profile. The runbook's existing curl commands must keep working verbatim.

#### Firmware
- **`node.cpp`** — already sending `vib` and `acc` after Phase 1. Add the **SW-420 event counter** as a proper interrupt-driven 2 s accumulator (not an ADC read) if it is not already; the `impulse_periodicity` feature needs real timing.
- **Level hub** — add the down-link command handler for `{"cmd":"advisory","level":n,"on":bool}`: a *distinct*, softer indication (slow amber breathe) from the CRITICAL strobe. **The industrial design rule: advisory and evacuation must never look the same.**
- **No M1/M2 code on any MCU.** Inference stays on the hub server. Do not attempt a TFLite port before the demo.

#### Frontend
- **`index.html`** — a new **"AI Cascade"** panel: two stacked rows.
  - Row 1 — `MODEL 1 · PATTERN`: class chip, `disruption_index` bar, top-3 features, and the scheduler rate (`M2 @ 0.2 Hz`).
  - Row 2 — `MODEL 2 · TTF`: countdown, the q10–q90 band as a shaded interval behind the number, severity chip.
  - A `MODEL DISAGREEMENT` flag that lights when M1 and M2 contradict.
- **Scenario console** — a profile dropdown listing all 7 profiles (this makes the truck-vs-crack demo a two-click operation on stage).

#### Database
- Migration 004 adds `pattern_dataset` provenance columns? **No** — keep the DB for runtime, not training. Instead add to `alerts_history` (Phase 8) the columns `disruption_label`, `disruption_index` already specified in §A.3.7, so every historical alert records *why* it was classified as it was. That audit trail is what makes the classifier accountable.

#### AI notebooks / models
- **New `06_train_pattern.py`** — mirrors the structure of `02_training.py` (same reporting style, same export convention):
  - reads `pattern_dataset.csv`,
  - `GroupShuffleSplit` **by `run_id`** (never random rows),
  - trains the RandomForest, then sweeps the decision threshold over `[0.05, 0.95]` to find the **lowest threshold that achieves STRUCTURAL recall ≥ 0.98**,
  - writes `pattern_model_v1.json` (via `joblib`), `pattern_meta.json`, and `export/pattern_training_report.txt`,
  - prints the confusion matrix and the four most important features.
- **`03_synthetic_augmentation.py`** — reuse its `run_class` split so `STRUCTURAL` runs are labelled consistently with the existing `BREACH` runs; extend `--target` usage so one invocation can emit both datasets.
- **`02_training.py`** — upgrade in place to emit the **quantile trio** (`ttf_model_v2.json`) with `monotone_constraints` on `disp_now`, `v10`, `v30`. Keep `ttf_model_v1.json` on disk untouched so a rollback is a one-line manifest edit.
- **`models/MANIFEST.json`** — add `model_1` with `"required": false`, so a missing classifier degrades to M2-only instead of crashing.

### 5.10 The safety firewall — the most important 30 lines in the repository

```python
# engine/cascade.py
CRITICAL_BAND_S = 30.0

def firewall_check(dec: CascadeDecision) -> bool:
    """Rule 2. Returns True if this decision REQUIRES an alert
    no matter what Model 1 concluded."""
    if dec.m2_ttf is not None and dec.m2_ttf <= CRITICAL_BAND_S:
        return True                      # a contradiction is never allowed to silence us
    if dec.kin_ttf is not None and dec.kin_ttf <= CRITICAL_BAND_S:
        return True                      # the physics fallback has equal authority
    if dec.m1 is not None and dec.m1.label in ("STRUCTURAL", "UNKNOWN"):
        return True
    return False
```

And the test that makes it real:

```python
# tests/test_cascade_safety.py
def test_m1_can_never_suppress_m2():
    for label in ("QUIET", "ENVIRONMENTAL"):
        for ttf in (0, 5, 15, 29, 30, 60):
            dec = mk(m1_label=label, m2_ttf=ttf)
            tier = route_decision(dec, mode="advisory")
            if ttf <= 30:
                assert tier in ("ELEVATED", "IMMINENT"), \
                    f"M1={label} suppressed M2_ttf={ttf}"

def test_gate_mode_still_respects_firewall():
    """Even with the reference Pass/Block cascade enabled, a low M2
    TTF from the previous window keeps the alert live."""
    ...
```

> Add this test file **before** writing `cascade.py`. Write the failing test, then make it pass. A safety property that is only asserted in a slide is not a safety property.

### 5.11 Metrics that matter (and the ones that don't)

| Metric | Target | Why |
|---|---|---|
| **STRUCTURAL recall** | **≥ 0.98** | The only number that can kill someone if it is wrong. Tune the threshold for this, not accuracy. |
| ENVIRONMENTAL precision | ≥ 0.90 | If we call real machinery "structural" too often, operators stop trusting the system. Trust is the actual product. |
| QUIET false-positive rate | ≤ 0.02 | Nuisance advisories erode confidence. |
| Macro-F1 (4-class) | ≥ 0.90 | Headline for the slide. |
| **Suppression rate on STRUCTURAL** | **0 by construction** | Enforced by `firewall_check` + test. Report it as 0 and explain *why* it is structurally impossible, not merely empirically absent. |
| M1 latency | ≤ 1 ms/window | It runs at 2 Hz × 4 nodes. |
| M2 latency | ≤ 3 ms/window | Runs ≤ 2 Hz × 4 nodes; the 1 Hz demo cadence is comfortable. |

**Do not report classification accuracy.** The class distribution is roughly 9:1 QUIET; a model that always says QUIET scores ~0.9 accuracy and is worthless. Reporting accuracy on an imbalanced safety problem is the fastest way to lose a technical judge's respect.

### 5.12 Acceptance tests

```bash
python tools/make_counterfactuals.py --runs 400 --out neuralstrata-ai/datasets/pattern_dataset.csv
python neuralstrata-ai/notebooks/06_train_pattern.py
python -m pytest tests/test_cascade_safety.py -q       # MUST pass before any tuning
python tools/bench_latency.py                          # M1 <1 ms, M2 <3 ms
python tools/eval_cascade.py --dataset pattern_dataset.csv
```

| Scenario | Expected |
|---|---|
| `machinery_pass` on node 2 | `label=ENVIRONMENTAL`, `disruption_index > 0.6`, `recovery_ratio > 0.9`, **no alert tier above ADVISORY**, no SMS |
| `blast` on node 2 | `label=ENVIRONMENTAL`, advisory, no SMS, node status returns to NOMINAL within 10 s |
| `sensor_fault` on node 3 | `label` = INSTRUMENT/UNKNOWN, `SENSOR_FAULT` event, **not** a geological alert |
| `warning_creep` on node 2 | `label=STRUCTURAL`, TTF counting down, tier ELEVATED→IMMINENT |
| `critical_collapse` on node 1 | `label=STRUCTURAL`, tier IMMINENT, siren, route blocked, SMS (Phase 8) |
| **M1 forced to `ENVIRONMENTAL` during a real collapse** | **Alert still fires.** (This is the test that proves Rule 2.) |

### 5.13 Risks & rollback

| Risk | Mitigation |
|---|---|
| Model 1 is trained on synthetic counterfactuals, so it may not transfer to a real pit | Say so explicitly. "Counterfactual, physics-parameterised, in-domain — the same Tier-3 methodology we use for Model 2." Then note the mitigation: `IsolationForest` for unseen disturbances, and `UNKNOWN` routing to full alert tier. |
| Model 1 becomes a scapegoat if a demo drill misfires | `CASCADE_MODE="gate"` → `"advisory"` is one env var, and the firewall keeps the system safe either way. Rehearse both. |
| The 32-feature extractor drifts from training | `pattern_meta.json` stores the feature list; `load_pattern_model()` asserts order equality and refuses to load on mismatch (same pattern already used for `scaler.json`). |
| Class imbalance (STRUCTURAL is rarer) | `class_weight="balanced_subsample"` + threshold sweep to hit recall ≥ 0.98 + oversampling the `STRUCTURAL_INITIATION` class specifically, since it is the earliest-warning class. |

**Rollback:** delete `models/pattern_model_v1.json`, or set `manifest.model_1.required=false`. The cascade degrades to exactly Phase 4 behaviour — M2 + kinematic — with an `ADVISORY`-less UI. Test this path before demo day; it is the same graceful-degradation trick that already works for M2.

### 5.14 Viva lines (rehearse these three)

> **Q: "Why two models?"**
> *"Because they answer different questions. Model 1 asks 'is this geological or environmental?' — that's the ministry's 'identify abnormal deformation patterns'. Model 2 asks 'how long until it fails?' — that's 'predict subsidence and estimate severity'. One model doing both would have to trade off false alarms against warning time inside a single loss function, and you can't tune that trade-off in a safety system. Two models let us hold recall at 98% on the structural class while still suppressing machinery noise."*

> **Q: "Won't the gatekeeper miss a real collapse?"**
> *"It can't. Model 1 never gates Model 2 off — it only changes the sampling rate and the alert tier. And there's a firewall: if Model 2 predicts under 30 seconds, we alert regardless of what Model 1 said, and if they disagree we raise a MODEL_DISAGREEMENT flag and escalate. There's an automated test that asserts Model 1 cannot suppress a critical prediction. That's a design property, not a hope."*

> **Q: "Why Random Forest for Model 1 and XGBoost for Model 2?"**
> *"Deliberately different families. If both were boosted trees they'd fail on the same windows — correlated errors are the worst possible property in a cascaded safety system. Bagging gives Model 1 a different inductive bias, native probability output for a soft index instead of a hard yes/no, no feature scaling, and 0.3-millisecond inference so it can run at full 2 Hz on all four nodes."*

---
## PHASE 6 — B3: Severity, Progression, Confidence & Kinematic Unification

> **Goal** — Turn "TTF = 7 s" into "**TERTIARY creep, HIGH severity, TTF 6–11 s (80% band), confidence HIGH**", and finally wire the calibrated physics fallback that Phase 4.6c produced but nobody connected.
> **Effort** — 3 dev-days · **Demo-critical — YES** · **Depends on** — Phase 5
> **Demoable result** — the TTF pill becomes an honest interval, and pulling the model files mid-drill shows a *calibrated Fukuzono* countdown instead of the old `v/a` guess.

### 6.1 Audit — what exists today

- **Defect D2, still open:** `models/kinematic_calibration.json` exists (`alpha_exponent 1.95`, `a_coefficient 0.082`, `breach_limit_mm 100`, `steady_creep_velocity_threshold_mm_s 0.05`, `stable_ttf_clamp_s 3600`) and **nothing reads it**. The live fallback at `app.py:376` is `ttf = v / a`.
- Those are two different quantities: `v/a` is "time for velocity to reach zero if deceleration is constant" — a plausible-sounding number that is not a time-to-failure. The training feature `ttf_fuk` is a **1/v regression intercept**, which *is* the Fukuzono estimator. The system's own fallback is inconsistent with its own feature.
- `telemetry_state["ttf_seconds"]` is a single integer. No band, no severity tier, no progression stage.
- `get_ai()` derives `confidence` as `100 − MAE×3`, floored at 50 — a decoration, not a measurement.
- `update_status_logic()` is still called from two threads (**D6**).

### 6.2 Target behaviour

1. **Kinematic unification.** `engine/kinematic.py` loads the calibration JSON and provides:
   - `fukuzono_ttf_live(buf)` — the **same 1/v least-squares estimator** already used to build the `ttf_fuk` feature, now also used at runtime. Feature and fallback become the same physics.
   - `kinematic_ttf_accel(v, a)` — the legacy `v/a` path, demoted to **last resort only** (no 60 s window available yet).
   - Fallback chain, explicit and logged: **cascade → M2 (fresh) → Fukuzono 1/v → v/a → `"N/A"`**.
2. **Severity tiers** from `TTF_BANDS = (0, 30, 120, 600)` → `CRITICAL / HIGH / ELEVATED / WATCH`, plus `NOMINAL` above 600 s.
3. **Progression stage**, computed from displacement and velocity shape — this maps to the geotechnical vocabulary the problem statement uses:

| Stage | Physical meaning | Detection rule |
|---|---|---|
| `PRIMARY` | early, decelerating creep | `v60 > 0` and `v10 < v60` (slowing) |
| `SECONDARY` | steady-state creep | `v10 ≈ v60` within 25% |
| **`TERTIARY`** | **accelerating towards failure** | `v10 > 1.25 · v60` sustained 10 s, or `curvature_score > 0.5` |
| `FAILURE` | breached | `disp ≥ 100 mm` |

4. **Confidence as a number, not a decoration**: derived from (a) out-of-run MAE, (b) window completeness after the last discontinuity, (c) M1/M2 disagreement, (d) kinematic-vs-ML gap. Reported as `HIGH / MEDIUM / LOW` **with the reason string**.
5. **Recommended actions** attached to every alert (Phase 8 consumes them), depending on severity, stage, route state and whether people are underground.
6. **D6 closed:** `update_status_logic()` is called from the ticker **only**; the serial reader updates raw values and calls `crack`/`health`, never the full status pass.

### 6.3 Data contract

`/api/ai` becomes:

```json
{
  "model":"ttf_model_v2","status":"ACTIVE","horizon":"1 Hour","last_train":"…",
  "confidence":"HIGH",
  "note":"XGBoost TTF forecast · held-out MAE 0.10 s · Fukuzono fallback armed.",
  "forecasts":{"node_1":false,"node_2":true},
  "severity":{"node_2":{"tier":"HIGH","ttf":47,"band":[31,52,88],
                        "stage":"TERTIARY","curvature":0.71,
                        "confidence":"HIGH","confidence_reason":"window complete, models agree"}},
  "kinematic":{"source":"fukuzono_1/v","alpha":1.95,"A":0.082,"ttf":51.2}
}
```

`ttf_seconds` on `/api/telemetry` stays an integer (the dashboard reads it as one); the band lives in `/api/ai`.

### 6.4 Functions delivered

`load_calibration`, `fukuzono_ttf`, `fukuzono_ttf_live`, `kinematic_ttf_accel`, `clamp_ttf`, `disagreement` (`engine/kinematic.py`); `severity_from_ttf`, `progression_stage`, `curvature_score`, `confidence_band`, `recommended_actions` (`engine/severity.py`).

### 6.5 Changes to the existing project

#### `app.py`
- **`update_status_logic()`** — extract the kinematic block (lines ~374–380) into `engine.kinematic`; replace the `ttf = v / a` line with the fallback chain. **This is the line the deck has been claiming for weeks.** After this change the claim "calibrated Fukuzono–Voight α = 1.95 on real failure curves" is literally true of the running code.
- **Freeze `ttf_seconds`'s type.** It is currently sometimes an `int` and sometimes `"N/A"`. Keep it that way (the frontend already handles both) and add the band under a new key rather than changing the existing one's type.
- **`run_model_inference` / `cascade_tick`** — populate `severity` per node; call `progression_stage` at 1 Hz (not 2 Hz — the stage is a slow variable and computing it faster only adds noise).
- **`get_ai()`** — add the `severity` and `kinematic` blocks; compute `confidence` from `confidence_band()` **including the MAE read from `model_meta.json`** — so the number on screen traces back to the artifact (Rule 3).
- **`push_event`** — a stage transition (`SECONDARY → TERTIARY`) is itself an event: `"Node 2 entered TERTIARY creep — acceleration sustained."` That is the single most quotable line in the demo log.

#### Firmware
- **No change.** (Severity lives server-side; the hub only needs to obey the LED/siren commands it already handles.)

#### Frontend
- **`index.html`** — the TTF pill gains a band whisker; a `STAGE` chip (`PRIMARY/SECONDARY/TERTIARY/FAILURE`); the confidence chip becomes a hover-tooltip showing `confidence_reason`. **Never show a bare number without its band** — the band is the honest part.

#### Database
- `alerts_history` already carries `progression_stage`, `ttf_seconds`, `ttf_q10`, `ttf_q90` (§A.3.7) — populate them in Phase 8.

#### AI notebooks / models
- **`02_training.py` → quantile trio.** Emit `ttf_model_q10/q50/q90.json` (or one file with three boosters), `model_meta.json` v2 listing all three, and keep the point model for `forecasts`. Calibrate the band on the held-out run and **report empirical coverage** — e.g. *"the 80% band contained the true TTF in 78% of held-out windows"*. If coverage is far off 80%, widen the band; an uncalibrated interval is worse than no interval because it looks rigorous.
- **`05_calibrate_kinematic.py`** — currently it writes **hard-coded constants** (`alpha_mean = 1.95`, `a_param = 0.082`) rather than fitting anything. Either (a) rename it honestly to `05_set_kinematic_constants.py` with a docstring stating the values come from published empirical ranges, or (b) make it actually fit `(α, A)` to the calibration series it has. **Option (a) is honest and takes ten minutes; option (b) is better science and takes two hours.** Do not leave it named "calibrate" while it assigns literals — that is the kind of detail a technical judge will find.
- **`01_data_extraction.py`** — record the `ttf_fuk` coverage fraction in the training report (it already prints it); confirm runtime `fukuzono_ttf_live` and training `ttf_fuk` produce the same value on the same window. Add `tests/test_kinematic.py` asserting parity to within 1%.

### 6.6 Algorithm detail — why the fallback must be the *same estimator* as the feature

During training, `ttf_fuk` was computed as the time-axis intercept of `1/v` fitted over 30 s of samples with `v10 > 1 mm/s`, clamped to `[0, 3600]`. The model learned to trust that quantity. If at runtime the fallback computes a *different* quantity (`v/a`), then on the day the model files go missing the system changes its *definition* of TTF — and the operator sees a number that means something else. Unifying them means:

- the fallback is a first-class predictor, not a substitute;
- the model and the fallback can be compared on the same axis (`disagreement`);
- "the kinematic fallback is armed" becomes a meaningful sentence in the alert text.

### 6.7 Acceptance tests

| Test | Assertion |
|---|---|
| `test_kinematic_parity` | For a synthetic window, `fukuzono_ttf_live(buf)` equals the training `ttf_fuk` within 1%. |
| `test_fallback_chain` | With model files removed, `/api/ai` reports `FALLBACK`, `kinematic.source == "fukuzono_1/v"`, and a countdown still appears during a critical drill. |
| `test_bands` | `ttf=25` ⇒ `CRITICAL`; `ttf=100` ⇒ `ELEVATED`; `ttf=900` ⇒ `NOMINAL`; boundaries exact at 30/120/600. |
| `test_stage_tertiary` | On a synthetic accelerating run, `progression_stage` reaches `TERTIARY` **before** `disp` crosses 60 mm (it must lead the breach, otherwise it is a lagging indicator). |
| `test_status_single_thread` | Grep: `update_status_logic` appears exactly once in the call graph; the serial reader does not call it. |
| Band coverage | Held-out empirical coverage of the q10–q90 band ∈ [0.72, 0.88]. |

### 6.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| Quantile models are slower or less accurate on the point estimate | Keep the point model authoritative for `ttf_seconds`; the band is *additional* information. If the quantile trio underperforms, ship the point model + a heuristic band derived from the held-out residual distribution. |
| Stage machine flips back and forth on noise | Require the tertiary condition to hold for 10 s and require a threshold *margin* (1.25×) to enter, 1.05× to exit — hysteresis, exactly as in the crack engine. |
| The renamed/changed fallback changes the demo timing | Re-run the 4.5 runbook end to end after this phase and update the timed table with the new numbers. |

**Rollback:** the fallback chain is one function; reverting restores `v/a`.

### 6.9 Viva lines

> *"Time-to-failure alone is not enough for a control room. We report the stage — primary, secondary or tertiary creep — because that's the vocabulary geotechnical engineers already use, and tertiary creep is the one that means 'evacuate now'. We report a confidence interval, not just a number, and we report why we're confident. And our physics fallback is the same Fukuzono estimator our model was trained on — so if the AI disappears mid-shift, the number on the screen means the same thing."*

---
---

## PHASE 7 — B5: Dijkstra Evacuation Routing + the Missing `/api/map`

> **Goal** — Give every person a computed way out, and give the dashboard the map it has been asking for since before it could run.
> **Effort** — 3 dev-days · **Demo-critical — YES** · **Depends on** — Phase 1 (4 nodes), Phase 4 (zone risk)
> **Demoable result** — the GIS card finally renders, colours zones by live risk, and shows the computed safe exit path flipping when you block the far tunnel.

### 7.1 Audit — what exists today

This phase closes the largest single gap in the project.

- **Defect D1:** `gis-map.js` polls `GET /api/map` every 2 s. **That route does not exist** (the route list is `/api/telemetry`, `/api/extended`, `/api/history`, `/api/ai`, `/api/sim/trigger`, `/api/reset`, `/`). There is also **no static-asset route**, so `/gis-map.js` cannot even be fetched — although the file's own header comment says *"the `/<path:asset>` route was added in app.py v4.5"*, which is not true of the code on disk.
- Routing is two booleans: `route_A_status`, `route_B_status`, hardcoded to node_1/node_2 (**D7b**).
- `tunnel_map.json` exists and is well-formed — nodes with `d_n_m`/`d_e_m`, junctions J1–J4, exits EXIT1/EXIT2, 9 links, 4 zones as **rectangles in centimetre space**, and node_3/node_4 marked `"active": false`. It also states plainly: **"OFFSETS ARE PLACEHOLDERS."**

### 7.2 Target behaviour

1. **`/api/map` exists** and returns exactly what `gis-map.js` reads (§A.3.6): `site`, `system_status`, `nodes[]`, `junctions[]`, `exits[]`, `links[]`, `zones[]`.
2. **`GET /assets/<path>` exists** and serves `gis-map.js` (and any future static asset) with the correct MIME type.
3. **Coordinate conversion is server-side.** The map stores metres and cm-space rectangles; the frontend needs lat/lon and polygons. Helpers: `offsets_to_latlon(d_n_m, d_e_m)` (metres → degrees using the baseline centre and the surveyed scale) and `rect_to_polygon(rect_cm)` → 4 corner points → lat/lon.
4. **Real Dijkstra**, hazard-weighted, over the map's adjacency, from a person's position to the nearest *safe* exit.
5. **Fail-safe HOLD** when no path exists: amber pulse at the junction, never a green light pointing into danger.
6. **Junction LEDs driven through the level hub** — junctions are passive WS2812B; there is exactly **one MCU per level hub** and no per-junction MCU. The server sends junction *intent*; the hub renders it.
7. **Personnel-aware routing**: an RFID check-in resolves a miner to a graph node; the route is computed for where people actually are.

### 7.3 The routing model

```
w(link) = ∞                                     if the link is BLOCKED
        = base_m × (1 + hazard_penalty(a,b))    otherwise
hazard_penalty(a,b) = Σ over endpoint nodes of:
        node CRITICAL      → 4.0
        node WARNING       → 1.5
        node STALE         → 2.5   (UNKNOWN is not safe — Phase 2's rule)
        crack_stage PROPAGATION → +1.0 ; CRITICAL → +4.0
```

Then plain Dijkstra from source to the set of exits, taking the minimum total cost. Why *cost* rather than "shortest": a 30 m corridor past a node in tertiary creep is worse than a 45 m corridor that is clean, and a stale node must be treated as suspect rather than ignored.

**Blocked-link rule (SRS §5.5, already a business rule):** if a node above a tunnel is WARNING or CRITICAL, that tunnel's route segments must be blocked for *both* the UI and the physical hub LEDs. Today this is implemented as a blanket boolean; Phase 7 refines it to per-segment, which is strictly more informative and still obeys the rule.

### 7.4 Data contract

`/api/map` per §A.3.6. New: `GET /api/site` (fixed baseline metadata — returned once at page load, so the 2 s poll stays small).

### 7.5 Functions delivered

`load_tunnel_map`, `SiteMap`, `build_graph`, `link_cost`, `hazard_penalty`, `dijkstra`, `route_for_person`, `nearest_exit`, `fail_safe_hold`, `junction_led_plan`, `push_hub_command`, `resolve_position` (`routing/dijkstra.py`); `offset_to_latlon`, `rect_to_polygon`, `build_map_payload` (`api/map_payload.py`); `get_map`, `get_site`, `get_asset` routes.

### 7.6 Changes to the existing project

#### `app.py`
- **Add three routes**: `/api/map`, `/api/site`, and `/assets/<path:asset>`. The asset route is what finally makes the frontend's `<script src="/gis-map.js">` work.
- **Move `tunnel_map.json`** from `neuralstrata-backend-app-v44/` to `neuralstrata-backend/assets/` and load it **at boot**, failing loudly if it is missing or malformed (`validate_config` checks it). A map that loads lazily is a map that fails during a demo.
- **`update_status_logic()`** — replace the two hardcoded route booleans with `route_for_person` results, while **keeping `route_A_status` / `route_B_status` in the payload** (derived from the level routes) because `index.html` reads them. **D7b closed.**
- **`reset_system()`** — recompute routes; clear the HOLD flags.
- **Serial down-link** — `push_hub_command` writes a compact JSON line to the same serial port, tagged so the hub can distinguish commands from nothing-else (commands are server→hub; telemetry is hub→server, so no collision, but add a `#CMD` sentinel for safety during bring-up).

#### Firmware
- **Level hub (`lvl1` / `lvl2` envs)** — add a command parser in the main loop that reads the serial/ESP-NOW down-link and drives: (a) the WS2812B strip segments for its junctions, (b) the 2-pin buzzer, (c) the advisory breathe pattern. **Junctions remain passive LEDs on the strip** — the hub addresses them by index. No new MCUs anywhere.
- **Pattern vocabulary** (keep it tiny and unambiguous on stage):

| Pattern | Meaning | Colour behaviour |
|---|---|---|
| `flow_green` | route open, walk this way | green chase, 400 ms |
| `advisory_amber` | environmental disturbance, no evacuation | amber breathing, 1.5 s |
| `hold_amber` | no safe path — **do not move** | amber solid-fast 300 ms |
| `chase_red` | route blocked | red chase, 120 ms |
| `strobe_red` | evacuate now | red strobe, 100 ms + buzzer |

- **Main hub** — forward down-link commands to the addressed level hub. It is a router, not a decision-maker. **Keep all decision logic on the server** so the algorithm is in one language and one place.

#### Frontend
- **`index.html`** — add the three integration lines the file's header comment already documents:
  ```html
  <div id="gis-map" style="height:430px"></div>
  <script src="/gis-map.js"></script>
  <script>initGisMap("gis-map");</script>
  ```
  Then verify: the card renders, zone colours track risk, and clicking a node shows its detail.
- **`gis-map.js`** — **only two changes needed**: (a) it currently expects `p.lon`/`p.lat` on every point, which the new payload provides; (b) add the route polyline layer by drawing the `links` whose `cost` is finite and whose endpoints are on the computed path. **Do not restructure the file** — it is 6 KB, dependency-free and offline-capable, which is exactly right for a diorama with no internet.

#### Database
- **No change.** Routing is computed in memory; the map is a file.

#### AI notebooks / models
- **No ML change.** But record the **surveyed** coordinates: the map's own note says the offsets are placeholders. Measure each node, junction and exit in **centimetres from the SW corner of the diorama**, then set `d_e_m = x_cm × 10`, `d_n_m = y_cm × 10` (the stated 1 cm = 10 m scale). Flip `"active": true` on `node_3` and `node_4` only when those nodes actually exist on the board — an inactive node must be excluded from `build_graph`, not routed around as if it were dead.
- Keep the placeholder-vs-surveyed distinction visible: replace the file's `"note"` field with the survey date and the measured table rather than deleting it.

### 7.7 Why Dijkstra and not A\*

Because the graph has 8 vertices. A\* needs a heuristic that must be admissible to be correct; with 8 nodes the heuristic saves microseconds and adds a correctness argument to defend. Dijkstra is the right algorithm *because the graph is tiny* — say that, and it sounds like judgement rather than inexperience.

### 7.8 Acceptance tests

| Test | Assertion |
|---|---|
| `test_dijkstra_basic` | From `J1`, the shortest path to `{EXIT1, EXIT2}` in an unblocked graph is found; cost equals the sum of link lengths. |
| `test_dijkstra_blocked` | Block the J2↔J3 link ⇒ the path from Level 2 to EXIT1 disappears; the engine returns EXIT2 rather than a path through the blocked leg. |
| `test_fail_safe_hold` | Block **all** links from a junction's component ⇒ `fail_safe_hold` is returned, no green pattern is ever emitted, and an event is logged. |
| `test_stale_is_not_safe` | Mark node_3 stale ⇒ routes through B1/B2 carry the hazard penalty, and Level 2 traffic prefers the cross-link. |
| `test_map_payload` | The `/api/map` body contains every key `gis-map.js` reads, with `polygon` arrays of `[lat, lon]` in the order the file expects (it uses `polygon[0]` and `polygon[3]` for the label anchor). |
| Manual | Open the dashboard: the GIS card renders; unplug Level Hub 2: the L2 links grey out and the route re-computes live. |

### 7.9 Risks & rollback

| Risk | Mitigation |
|---|---|
| The GIS card renders but is geometrically wrong (placeholder coordinates) | Do the survey **before** this phase. The map is only as good as the numbers in it, and the file itself warns about this. |
| Two sources of truth for routing (the old booleans + Dijkstra) | The booleans become **derived** values in this phase, never independent ones. |
| Serial down-link interferes with uplink parsing | The hub ignores any line beginning with `#CMD` on the uplink path; the server ignores any line beginning with `#CMD` on its own read path. Cheap, explicit, testable. |
| Demo risk: LED patterns fight the old firmware | Flash the level hubs first, test the five patterns manually with a one-line helper (`tools/led_test.py J2 chase_red`), then integrate. |

**Rollback:** the routes are additive; reverting `update_status_logic` restores the boolean behaviour and the GIS card simply 404s again (as it does today).

### 7.10 Viva lines

> *"We don't route by shortest distance, we route by risk-weighted cost — because a 30-metre corridor past a node in tertiary creep is worse than a 45-metre corridor that's clean, and a node we've lost contact with has to be treated as suspect, not safe. If every path is blocked we don't pick the least-bad one; we hold people in place with an amber pulse, because pointing a green light into a collapsing tunnel is worse than telling someone to stand still."*

---
## PHASE 8 — B4: Persistent Alert Dispatcher

> **Goal** — A warning that nobody received is not a warning. Get the right message to the right person through the right channel, prove it arrived, and keep the audit trail.
> **Effort** — 4 dev-days · **Demo-critical — YES** · **Depends on** — Phase 6 (severity), Phase 7 (routes)
> **Demoable result** — during a critical drill, the phone in the room buzzes with an SMS containing the node, the severity, the TTF band and the coordinates, while the junction LEDs strobe — and the SMS line stays silent during the machinery-pass drill **by design**, which is the more impressive half.

### 8.1 Audit — what exists today

- Alerts are `push_event()` rows in an in-memory list capped at 64 (**D12**). No persistence, no acknowledgement, no delivery record.
- `alerts_history` is specified in `BACKEND_PLAN.md` §4.3 but **no table and no code exists**.
- SRS §D.4 lists notifications as a **production delta**: *"Dashboard banner + event log → SMS / email / push."* This phase moves that delta into the prototype, which is a direct, quotable answer to the problem statement.
- SRS §5.5 business rules already state: *"Only the Safety Officer or Control Room may acknowledge and downgrade alerts; Tier 3 evacuation triggers may never be suppressed automatically."* That rule needs code.
- The 4G modem and SMTP settings do not exist in `app.py` at all.

### 8.2 Target behaviour — a real alert state machine, not a print statement

```
                     ┌──────────┐
   condition met ───►│   NEW    │  dedup by (node, event_type, tier) within ALERT_DEDUP_S
                     └────┬─────┘
                          │ dispatch
                     ┌────▼─────┐   no ack within ALERT_ESCALATE_S
                     │  ACTIVE  │ ─────────────────────────► escalate
                     └────┬─────┘                            (add channel, raise tier,
                          │ operator ack                      re-notify)
                     ┌────▼─────┐
                     │  ACKED   │  visible, no re-notify
                     └────┬─────┘
                          │ condition clears (+ settle time)
                     ┌────▼─────┐
                     │  CLOSED  │  written to alerts_history, kept forever
                     └──────────┘
```

**Channel priority (latency-first, always):**

| Order | Channel | Latency target | Works offline? | Audience |
|---|---|---|---|---|
| 1 | **Local: junction LEDs + buzzer via the level hub** | < 1 s | yes | miners underground — **the ones in danger** |
| 2 | **Dashboard banner + GIS flash + audio** | < 1 s | yes | control room |
| 3 | **SMS via 4G modem** | < 5 s | no (buffered into the outbox) | Safety Officer / Shift In-charge |
| 4 | **SMTP email with a georeferenced deep link** | < 30 s | no (buffered) | DGMS regulator, mine manager |

**The local channel is attempted first and its failure is a distinct, louder alarm than an SMS failure.** An SMS that fails means a regulator learns late; a siren that fails means people underground do not know.

**Suppression rules (all *filtered* by `suppression_guard`, never silently):**
- `ADVISORY` tier (M1 says ENVIRONMENTAL, TTF healthy): **no SMS, no email**, LEDs breathe amber, dashboard advisory chip. **This is the payoff of Phase 5.**
- `WATCH`/`ELEVATED`: dashboard + log only.
- `HIGH`: + SMS.
- `CRITICAL`: + SMS + email + strobe/siren.
- **Any tier, if `firewall_check` is true ⇒ never below `HIGH`.** Even if M1 says environmental. This is Rule 2 expressed in the alert layer.

### 8.3 Data contract

Tables `alerts_history`, `alert_deliveries` per §A.3.7. New endpoints:
- `GET /api/alerts?limit=50&severity=HIGH&since=ISO8601` → paginated
- `POST /api/alerts/<id>/ack` `{"operator":"Safety Officer"}` → `{ok, state, acked_at}`
- `GET /api/alerts/<id>/deliveries` → per-channel delivery ledger

SMS body (must fit in 160 characters, must contain coordinates — that is a problem-statement requirement):

```
NEURAL STRATA CRITICAL | N2 Tunnel A | TTF 47s (31-88) | TERTIARY
Route A BLOCKED. Use Exit 2. 23.64321,85.31804 | NS-A7F3
```

Email adds: the full recommended-action list, the model/M1 classification and its reason, the recommended route as a list of junctions, and a link to `/api/map` state at the time of the alert.

### 8.4 Functions delivered

`AlertEvent`, `dedupe_key`, `AlertStateMachine` (+`observe`, `escalate`), `dispatch`, `notify_local`, `notify_dashboard`, `notify_sms`, `notify_email`, `retry_failed`, **`suppression_guard`**, `ack_alert` (`alerts/dispatcher.py`, `alerts/channels.py`); `sms_body`, `email_subject`, `email_body`, `action_list`, `geo_link` (`alerts/templates.py`); `insert_alert`, `fetch_alerts`, `insert_delivery`, `get_alerts`, `ack_alert_route`, `get_deliveries`.

### 8.5 Changes to the existing project

#### `app.py`
- **`push_event()`** — keep it (the dashboard log still uses it), but **hook it**: any event at `WARNING`/`CRITICAL` also constructs an `AlertEvent` and goes through `dispatch()`. This means **every existing call site keeps working** — there are many (`ROUTE A BLOCKED`, `Micro-crack initiated`, `SERIAL_LOST`, …) and none of them should need editing.
- **`reset_system()`** — close open alerts as `CLOSED` with `reason="manual re-arm"` rather than deleting them. Deleting an alert history is exactly the behaviour a regulator should never see.
- **New routes** `/api/alerts`, `/api/alerts/<id>/ack`, `/api/alerts/<id>/deliveries`.
- **`log_telemetry_rows`** — unchanged; alerts get their own writer.
- **Startup** — `retry_failed()` once at boot, so anything queued while offline goes out when the server comes back.

#### Firmware
- **Main hub** — accept `{"cmd":"sms","to":"…","body":"…"}` and drive the 4G modem with AT commands (`AT+CMGF=1`, `AT+CMGS="<number>"`, body, `Ctrl+Z`), returning `{"event":"sms_result","ok":true|false,"ref":n}`. Keep the AT logic in the hub, not the server: the modem lives on the hub's UART.
- **Hub buffering** — if the modem is absent, the hub returns `ok:false` immediately; the server's outbox (Phase 9) holds the message. **Never let a missing modem block the siren.**
- **Escalation feedback** — the hub reports siren/LED state back upstream so the server can record `local_dispatched` as *proven*, not *assumed*. If the siren command is not acknowledged within 2 s, raise `LOCAL_ALERT_UNVERIFIED` — which is a `CRITICAL`-class infrastructure event, because it is the worst failure mode in the system.

#### Frontend
- **`index.html`** — an **Alerts** panel: live list with severity chips, an `ACK` button per row (calls `/api/alerts/<id>/ack`), and a per-alert delivery ledger (✓ local, ✓ dash, ✓ SMS, ✗ email). Showing the delivery ledger on screen is a strong demo beat — it is the difference between "we send alerts" and "we can prove alerts were delivered".
- **Operator identity** — a simple dropdown (`Safety Officer`, `Control Room`, `Test`) stored in `localStorage`; the ack carries it. No auth system needed for a prototype, but the field must exist, because SRS §5.5 says only those roles may downgrade alerts.
- **Suppression visibility** — when an alert is suppressed at SMS level, show it as a grey `ADVISORY — no SMS by policy` row. **Never make a suppression invisible**; the operator must always be able to see that the system chose to stay quiet.

#### Database
- Apply migration 003. Then **repoint `01_data_extraction.py`** — it currently reads `sensor_telemetry` only; it should also read `alerts_history` so the training report can correlate drills with alert outcomes. Optional but it closes the loop between the ML and the alerting halves.

#### AI notebooks / models
- **No retraining.** But add the alert outcome to the training report as context: how many alerts fired per drill, and how many were suppressed by M1 — that table is the empirical evidence that Model 1 is doing its job in the running system, not just on the held-out set.

### 8.6 Why "recommended actions" belong in the message

The problem statement asks the system to *advise*, not merely to alarm. A 160-character SMS that says "CRITICAL" makes the receiver do the thinking under stress. The same message with `Route A BLOCKED. Use Exit 2.` is a decision. `recommended_actions()` (Phase 6) supplies them, ranked:

| Severity | Stage | Recommended actions (SMS gets the first two; email gets all) |
|---|---|---|
| WATCH | any | "Monitor. No action required." |
| ELEVATED | SECONDARY | "Restrict entry to zone. Increase monitoring to continuous." |
| HIGH | TERTIARY | "Withdraw non-essential personnel. Prepare evacuation. Verify route." |
| CRITICAL | TERTIARY / FAILURE | "**EVACUATE NOW.** Route A BLOCKED — use Exit 2. Do not enter zone A2." |
| any | FAILURE | "**EVACUATE. Do not approach.** Account for all personnel at muster." |

### 8.7 Acceptance tests

| Test | Assertion |
|---|---|
| `test_dedup` | Twenty consecutive ticks with the same condition produce **one** alert within `ALERT_DEDUP_S`, not twenty. |
| `test_escalation` | An unacknowledged `HIGH` alert escalates after `ALERT_ESCALATE_S` (shorten via env for the test). |
| `test_no_auto_suppress` | `suppression_guard` returns `False` for any `CRITICAL`-tier event, for all inputs. (SRS §5.5.) |
| `test_firewall_in_alerts` | With `firewall_check == True`, the dispatched tier is ≥ `HIGH` **even if** `m1.label == "ENVIRONMENTAL"`. |
| `test_offline_queue` | With the modem absent, `sms_dispatched` stays `False`, the alert is still `ACTIVE`, local + dashboard channels still succeed, and the row is queued for Phase 9. |
| `test_ack_persist` | Acking writes `state='ACKED'`, `acked_by`, `acked_at`; the row survives a server restart. |
| Demo | Critical drill ⇒ SMS within 5 s with coordinates. Machinery-pass drill ⇒ **no SMS**, amber advisory visible on screen and on the junction strip. |

### 8.8 Risks & rollback

| Risk | Mitigation |
|---|---|
| SMS fails on stage (no SIM credit, no signal) | Rehearse with a **pre-paid SIM and a known-good tower**, and have a fallback: the hub can also emit the SMS text to serial so it appears on the projector — *show* the message even if the network is down. Better: run the demo with the modem unplugged on purpose and show the outbox retaining the message. Honesty about the offline case is more impressive than a lucky signal. |
| Email lands in spam / is slow | Send to a mailbox you control, check it, and show the delivery ledger. Set the alert as the email subject line's first word so it is unmissable. |
| Alert storm during a drill with 4 nodes | Dedup key includes node + event type + tier; escalation is time-based, not event-based. Add a global cap (`SMS_MAX_PER_HOUR = 20`) with a `RATE_LIMITED` log line — never silently drop. |
| The demo's "no SMS" moment looks like a bug | Rehearse the narration: *"Model 1 classified that as environmental, so we deliberately did not wake the Safety Officer at 3 a.m. — but look at the advisory, and notice the siren never depends on the network."* |

**Rollback:** alerts are additive. Setting `dispatch()` to a no-op restores Phase 7 behaviour (dashboard log only).

### 8.9 Viva lines

> *"The problem statement asked for notification, so we built the full chain: junction LEDs and the buzzer first — those work with zero network, and they reach the people who are actually in danger. Then the dashboard, then SMS, then email with the coordinates. Every delivery is recorded, so we can prove what arrived. And when Model 1 classifies a disturbance as environmental, we deliberately don't SMS the Safety Officer at 3 a.m. — but the siren still works, the advisory is on screen, and an automatic escalation timer re-notifies if nobody acknowledges."*

---
---

## PHASE 9 — B6: Cloud Sync Outbox

> **Goal** — Work perfectly with no internet, and reconcile when the link returns.
> **Effort** — 2 dev-days · **Demo-critical — no** · **Depends on** — Phase 8
> **Demoable result** — run a drill with the network off; the outbox depth climbs; plug the network back in; the queue drains and the central database shows every drill row.

### 9.1 Audit — what exists today

- Nothing. The SRS §NFR-2 states the system must be "Offline-First" and the prototype already is (local HTTP only) — but there is no sync mechanism, so the phrase is currently a constraint rather than a feature.

### 9.2 Target behaviour

1. **Outbox pattern, in the database, not in memory.** Every payload that must leave the site is written to the `outbox` table **in the same transaction** as the local record. If the process dies mid-write, the queue is still correct — that is the difference between an outbox and a `try/except`.
2. **Claim–send–mark.** `claim_outbox_batch` uses `FOR UPDATE SKIP LOCKED` so two sync threads can never send the same row twice.
3. **Batching + compression + signing.** gzip, HMAC-SHA256 over the body with a shared key, `X-NS-Signature` header.
4. **Exponential backoff** on failure (1, 2, 4, 8, 16, 30 s capped) and a per-row attempt cap of 10 → `FAILED` with the last error retained for inspection.
5. **Manual flush** via `POST /api/sync` for the demo.
6. **Compaction**: rows older than `keep_days` are pruned only after being confirmed `SENT`.

### 9.3 Why an outbox and not "just POST from the ticker"

Because the ticker runs at 2 Hz and the network is the least reliable component in the building. HTTP inside the ingest path means the ingest path inherits the network's latency and failure modes — the exact mistake that makes "offline-first" systems stall when the link flaps. The outbox decouples them: local writes are always fast and always durable; the network catches up later. This is the standard transactional-outbox pattern, and naming it correctly is worth marks.

### 9.4 Functions delivered

`enqueue_outbox`, `claim_outbox_batch`, `mark_outbox_sent`, `mark_outbox_failed`, `compact_outbox` (`persistence/outbox.py`); `sync_daemon`, `probe_backhaul`, `flush_batch`, `sign_payload` (`persistence/sync.py`); routes `POST /api/sync`, `GET /api/sync/status`.

### 9.5 Changes to the existing project

#### `app.py`
- **`log_telemetry_rows()`** — in the same transaction, `enqueue_outbox("telemetry", payload)` for the 0.5 s aggregate (not per row — batch it; 8 rows/s would flood the queue).
- **`push_event()` / `dispatch()`** — alert events enqueue with `kind="alert"`, which is the higher-priority class. **Priority column** or a fixed ordering (`alert` before `telemetry`) so a flooded queue still ships the alerts first.
- **`main()`** — start `sync_daemon` with a `stop_event`.

#### Firmware
- **No change.** Sync is server-side. (In production the hub would own the 4G link; in the prototype the laptop has Wi-Fi.)

#### Frontend
- **`index.html`** — an `OUTBOX: 412 pending · last sync 3 min ago` chip, and a `FORCE SYNC` button calling `POST /api/sync`. Showing a deep queue and then draining it live is a genuinely good demo beat, and it directly demonstrates the SRS's offline-first NFR.

#### Database
- Migration 003 already creates `outbox`.

#### AI notebooks / models
- **`01_data_extraction.py`** — add a `--from-central` flag that reads the *synced* central database instead of the local one, so the AI pipeline can be pointed at either. This is what makes the production story coherent: the model retrains on merged data from many sites.

### 9.6 Acceptance tests

| Test | Assertion |
|---|---|
| `test_outbox_atomic` | Kill the server between the local write and the sync attempt ⇒ the row is present exactly once and still `PENDING`. |
| `test_no_double_send` | Two concurrent flush threads process disjoint batches; no row is sent twice (verify via a unique `ref` at the receiver). |
| `test_backoff` | After 3 failures, attempts increments, `last_error` is recorded, and the delay sequence follows the backoff table. |
| `test_priority` | With 500 pending telemetry rows and 1 alert row, the alert ships in the first batch. |
| Manual | Network off ⇒ `OUTBOX` climbs; network on ⇒ drains to 0. |

### 9.7 Risks & rollback

| Risk | Mitigation |
|---|---|
| The central endpoint does not exist, so "sync" has nothing to talk to | Ship a 20-line Flask stub in `tools/central_stub.py` that accepts the batch and writes it to SQLite, purely for the demo. Then the demo is end-to-end real, not a mocked arrow on a slide. |
| Queue growth on a long offline period | Compaction + priority ordering + a `MAX_OUTBOX_ROWS` cap with an explicit `OUTBOX_FULL` infrastructure alert (never silent). |

**Rollback:** stop the daemon; local operation is unaffected by construction.

---

## PHASE 10 — B12 + Hardening: Gated Transfer Learning, Observability, Ops

> **Goal** — Add the research branch *behind a gate*, add the numbers that prove the system is alive, and write down how to run it.
> **Effort** — 3 dev-days · **Demo-critical — no** · **Depends on** — Phase 5
> **Demoable result** — `/api/metrics` shows inference latency, alert counts and queue depth; the transfer-learning experiment is documented with its gate result; the ops runbook covers a cold start.

### 10.1 B12 — the gated transfer experiment

Domain adaptation across **normalised time horizons** (`t / t_event`) so the model can be exposed to real open-pit and GNSS/InSAR series without contaminating the 2 Hz training set. **The gate, stated exactly:**

```
deploy ttf_model_transfer.json  ⟺  MAE_transfer(held-out drill test) < MAE_canonical
if the gate fails → the native model stays in production, and the negative result is published
```

Two things make this defensible rather than decorative:
1. **The gate value is the canonical MAE from Phase 0, whatever it turns out to be** — not a stale 5.4 s typed into a slide. Use `model_meta.json`.
2. **A negative result is a result.** "We tried domain adaptation; it did not beat the baseline on held-out drill data, so we did not ship it" is a *stronger* answer than a vague claim that transfer learning is coming. Report it either way in `export/transfer_experiment.txt`.

### 10.2 Observability (`metrics.py`, `/api/metrics`)

| Metric | Why it exists |
|---|---|
| `inference_latency_ms{m1,m2}` (p50/p95) | Proves the cascade is cheap enough to run on the on-site server |
| `m2_runs_total` per class | Proves the scheduler is actually saving compute — the Phase 5 claim, measured |
| `cascade_class_total{QUIET,ENVIRONMENTAL,STRUCTURAL,UNKNOWN}` | The production distribution of M1 decisions |
| `suppressions_total` | Every suppression is counted, never invisible |
| `firewall_escalations_total` | **Should be near zero** — if it spikes, M1 and M2 disagree systematically and that is a finding |
| `alerts_total{tier,channel}`, `deliveries_failed_total{channel}` | Alert health |
| `node_last_seen_seconds{node}`, `degraded_mode` | Health |
| `outbox_depth`, `db_write_ms` (p95) | Backpressure |
| `kinematic_fallbacks_total` | How often the physics path carried the system |
| `windows_invalidated_total` | How often Re-arm/gaps forced a warm-up (D13 in production) |

### 10.3 Ops & hardening checklist

- **Cold start**: a single `run.sh` that (a) checks Python + packages, (b) `init_schema`, (c) `validate_config`, (d) `load_model` + `load_pattern_model` (warn, never fail), (e) loads `tunnel_map.json`, (f) starts Flask, (g) prints the runbook's first curl. **One command from a cold laptop to a live dashboard.**
- **`docs/RUNBOOK.md`** — rewrite the 4.5 runbook for the phase-10 system: the 7 scenarios, the new endpoints, the degraded-mode checklist, the "what to say when X fails" table.
- **Backup/restore**: `pg_dump` before every rehearsal; a `restore.sh`.
- **Data retention**: telemetry rows per day at 2 Hz × 4 nodes ≈ 691k rows/day. Add a rollup policy (keep raw 7 days, 1-hour means forever) — otherwise the demo laptop's Postgres dies in a fortnight.
- **Config surface freeze**: every tunable in `config.py`, every one env-overridable. The runbook's "if the pot noise flickers the pill, raise `ACC_WARNING`" instruction becomes `NS_ACC_WARNING=0.4 python app.py` — no code edit during a rehearsal.
- **Pre-demo script** (`tools/preflight.py`): checks DB reachable, model artifacts loadable, map valid, 4 nodes fresh, siren acked, outbox depth, and prints a single `READY / NOT READY` line. Run it five minutes before you present.

### 10.4 Changes to the existing project

| Area | Change |
|---|---|
| `app.py` | Add `/api/metrics`; wrap the ticker and inference in `timer()`; add `preflight` to `main()` startup logging |
| Firmware | None (freeze) — **do not flash anything in the last 48 hours before the demo except to fix a demonstrated bug** |
| Frontend | A small status drawer showing outbox depth, degraded mode and the top three metrics; hidden behind a `?debug=1` flag so the main view stays clean |
| Database | Retention/rollup job (§10.3) |
| Notebooks | `07_transfer_experiment.py` → `export/transfer_experiment.txt` with the gate verdict, plus `check_numbers.py` extended to cover the new artifacts |

### 10.5 Acceptance tests

| Test | Assertion |
|---|---|
| `test_metrics_present` | `/api/metrics` returns every key in §10.2 with non-null values after 60 s of running |
| `test_preflight` | With the DB down, preflight prints `NOT READY` with the failing item and exits non-zero |
| Gate | `07_transfer_experiment.py` writes a verdict line: `GATE PASS — deployed` or `GATE FAIL — canonical model retained` |
| Cold start | From a fresh shell: `./run.sh` → dashboard live in < 30 s |

### 10.6 Viva lines

> *"We built the transfer-learning branch and then we gated it: it ships only if it beats our held-out drill MAE. It didn't, so we didn't ship it — and we say that out loud, because a negative result we can defend is worth more than a feature we can't. Our out-of-domain work went where it actually pays: validating the pipeline on real open-pit failure data instead of contaminating the training set with it."*

---
---

# PART C — APPENDICES

## C.1 Critical path & the compressed track

**True critical path (nothing can start earlier):**

```
P0 ─► P1 ─► P3 ─► P5 ─► P6 ─► P8
      │                 ▲
      └─► P7 ───────────┘        (P7 can run in parallel with P3–P6; P8 needs both)
```

`P2` and `P4` are off the critical path and can be done by a second pair at any time after `P1`. `P9` and `P10` are strictly post-demo.

**If you have five days before the demo:**

| Day | Work | Team split |
|---|---|---|
| 1 | **P0** in the morning (contracts + numbers + record/replay). **P1** starts after lunch. | 2 on P0/P1, 1 on the firmware frame change, 1 on the frontend node loops, 2 on the Model-1 dataset generator (can start immediately — it depends on physics, not code) |
| 2 | Finish **P1**. Start **P3** (crack) and **P7** (map + Dijkstra) in parallel. | 2 crack + firmware, 2 map + GIS card, 2 AI training |
| 3 | **P5** (train M1, build cascade, plant the safety test). **P2** squeezed in by whoever is free. | 3 on P5, 1 on P2, 2 on P7 finish |
| 4 | **P6** (severity + kinematic wiring) then **P8** (alerts) — P8 is the longest single phase, start it the moment P6's severity object exists. | 2 P6, 2 P8 channels, 2 rehearsal prep |
| 5 | **Full rehearsal only.** No new code. Preflight, timed run, fix only what breaks. | Everyone rehearses, one person drives the curl commands |

**Cut list, in order, if you run out of time:** P9 → P10 → P4 → P2 → (never cut P0, P3, P5, P6, P7, P8).

## C.2 Risk register

| # | Risk | Likelihood | Impact | Response |
|---|---|---|---|---|
| R1 | Model 1 underperforms on real diorama data | Medium | High | `CASCADE_MODE=advisory` means a weak M1 costs you *narration*, not safety. If STRUCTURAL recall < 0.95 on real data, **disable M1 for the demo** and present the pipeline as validated on counterfactuals with the honest caveat. |
| R2 | Firmware/serial instability on stage | Medium | High | Phase 2's watchdog + Phase 0's replay means the demo can run entirely from a recorded session if the hardware dies. **Rehearse the replay fallback.** |
| R3 | QRM (Question/Answer) on "synthetic data" | High | Medium | Have the Tier-3 answer ready (§5.13) and be the first to raise the limitation rather than the last. |
| R4 | The 5.4 s / 0.10 s contradiction is discovered | High if unfixed | High | Phase 0 fixes it. This is the highest-value half-day in the plan. |
| R5 | Two people edit `app.py` simultaneously | High | Medium | Phases are file-disjoint after P1 (that is why the extraction is done early). Enforce by assigning `app.py` to one person per phase. |
| R6 | Scope creep from the "digital twin" framing | Medium | Medium | Do not use the phrase. Say "physics-informed hybrid predictor" (§5.2). |
| R7 | Postgres on the laptop dies mid-demo | Low | High | Retention job (§10.3), `pg_dump` before every rehearsal, and the server must boot with the DB down — the DB is a recorder, not a dependency. **Test this.** |

## C.3 Viva question bank (the eleven most likely, with answers in the document)

| Question | Where the answer lives |
|---|---|
| "Why two models instead of one?" | §5.14 |
| "Won't the classifier miss a real collapse?" | §5.10, §5.14 |
| "Where did your training data come from?" | §5.5, §5.13 (counterfactuals, in-domain) |
| "Why not train on real GNSS/InSAR data?" | §3.1 of `BACKEND_PLAN.md` + §10.1 (scale contamination; validated instead) |
| "Why Random Forest and not XGBoost for Model 1?" | §5.4 |
| "How do you know it works on a real mine?" | Landau open-pit validation harness (`04_external_validation.py`) + the honest caveat that it validates the *pipeline*, not the site |
| "What happens if the AI fails?" | §6.2 fallback chain: cascade → M2 → Fukuzono 1/v → v/a → N/A, plus the model-removal degradation test |
| "What if the internet is down?" | Phase 9 outbox + Phase 8 local-first channels |
| "What if you lose a sensor?" | Phase 2: stale ≠ safe; the routing engine applies a hazard penalty to unknown nodes |
| "How do you avoid false alarms?" | Phase 5 Model 1 + §3.6 cross-sensor coherence, with the measured suppression rate |
| "Is the alert actionable?" | Phase 8: coordinates + recommended actions + delivery ledger |

## C.4 Acceptance checklist for the whole backend

- [ ] `python3 app.py` boots with **no** hardware, **no** database, and **no** model files — and still serves `/api/telemetry`.
- [ ] `python3 app.py` boots with everything present and logs `[ai] TTF model loaded` + `[ai] pattern model loaded`.
- [ ] All four nodes appear; `grep -c 'node_1", "node_2' app.py` returns 0.
- [ ] Unplugging any single node produces exactly one `NODE_STALE` event and greys that node in the UI.
- [ ] `POST /api/reset` mid-drill produces an immediately-invalid window and a true 60 s warm-up (D13 verified).
- [ ] Removing `models/` drops the system to the calibrated Fukuzono fallback with a countdown that still works.
- [ ] `machinery_pass` produces **no SMS**; `critical_collapse` produces an SMS with coordinates in under 5 s.
- [ ] `tests/test_cascade_safety.py` passes — and would fail if someone made M1 a hard gate.
- [ ] `/api/map` returns every key `gis-map.js` reads, and the GIS card renders with live zone colours.
- [ ] Blocking all routes yields `HOLD` (amber), never a green path into a blocked zone.
- [ ] The DB goes down mid-run: the API keeps serving, the outbox records failures, nothing crashes.
- [ ] `tools/check_numbers.py` exits 0 — deck, SRS, README and `model_meta.json` agree.
- [ ] `tools/preflight.py` prints `READY`.

## C.5 Glossary (plain language — for whoever joins the team late)

| Term | Plain meaning |
|---|---|
| **TTF** | Time-to-failure: how many seconds until the displacement crosses the 100 mm danger limit. |
| **Primary / secondary / tertiary creep** | Slow and slowing down → steady → accelerating towards collapse. Tertiary is the "get out now" stage. |
| **Feature / window** | One row of numbers describing the last 60 seconds of a sensor: current value, and how it changed over 10/30/60 s, its speed and its acceleration. |
| **Model 1 / Model 2 (the Double AI)** | Model 1 answers "is this a real rock problem or a truck?"; Model 2 answers "how long do we have?". |
| **Cascade** | The two models used one after the other, rather than a single model doing both jobs. |
| **Disruption index** | A 0-to-1 number from Model 1: how much disturbance is happening, and how confident it is that something is wrong. |
| **Cross-sensor coherence** | Do vibration *and* strain agree? A truck shakes without straining; a crack strains without shaking. |
| **Counterfactual data** | Synthetic examples of things we can't easily record — generated physically, so a "truck pass" is a real set of numbers, not a label. |
| **Fukuzono / inverse velocity** | A published rock-failure law: plotting 1/velocity against time gives a straight line that hits zero at the moment of collapse. |
| **Kinematic fallback** | The physics-only TTF estimate used when the AI is missing — same estimator as the trained model, so the number means the same thing. |
| **Outbox** | A local to-do list of messages that must leave the site; drained when there is a network. |
| **Dijkstra** | The standard "shortest path" algorithm; here the "length" of a corridor is inflated by how dangerous it is. |
| **Fail-safe HOLD** | If no safe exit exists, tell people to stand still (amber) rather than point them into danger (green). |
| **Degraded mode** | The system's declaration of what it cannot currently see — e.g. one sensor lost, or the ring split. |
| **Strangler-fig refactor** | Extracting code out of one big file bit by bit while the system keeps running, instead of a risky rewrite. |

---

*End of document. Part A is the interface contract, Part B is the build order, Part C is the safety net. If a phase's §5 conflicts with anything in `BACKEND_PLAN.md`, this document wins for **how** to build and `BACKEND_PLAN.md` wins for **what** the system should do — put the conflict in a comment and resolve it before writing code.*
