# Neural Strata — Backend & AI Architecture Plan (v6.0)

**Document Status:** Final Technical Specification & Implementation Plan
**Target Platform:** Python 3.10+ / Flask / PostgreSQL / XGBoost / ESP32 Blackbox
**Scope:** 7-board prototype topology (Main Hub + 2 Level Hubs + 4 Sensor Nodes + SD Blackbox + GIS + Evacuation AI)
**Supersedes:** v5.0 of this document, and the standalone `Neural_Strata_Backend_Plan_v5.md` (now folded in)

---

## 1. What changed in v6.0

The **Underground Blackbox & Cloud Sync** architecture is now part of this plan. It is not a separate
document any more — it is four new features (B13–B16), three new phases (10, 11, 12), an upgraded
Phase 9, and one new rule that governs the whole system:

> **The machine that records the data is not the machine that decides to delete it.**
> The underground SD card is unlinked only when the chunk is **closed, count-matched, undropped,
> fully synced and past a safety margin** — five independent conditions, and the system prints the
> reason whenever it refuses.

| Change | Where |
|---|---|
| **B13** — Underground blackbox ingest | new, Phase 10 |
| **B14** — Five-condition delete gate | new, Phase 10 |
| **B15** — Reconciliation + REPLAY recovery | new, Phase 11 |
| **B16** — Blackbox observability (chips, ledger, drop counter) | new, Phase 12 |
| **B6** — Cloud sync outbox, upgraded with the commit contract | Phase 9, was 2 d → now 3 d |
| **B4** — Alert dispatcher gains blackbox-state alerts (NO SD, drops, sync stalled) | Phase 8 |
| **B7** — Node health gains Main-Hub health | Phase 2 |
| Main Hub is **locally powered**, surface cable is **data-only** | hardware note, §5.5 |
| Seven corrections from the review | applied throughout |

**What did not change:** the double-AI cascade, the 3-tier AI data strategy, the frozen v4.4 HTTP
surface, the topology, the routing engine, and every decision carried over from earlier documents.

---

## 2. Executive summary

The **Neural Strata** backend is the deterministic edge intelligence and supervisory control layer
for an underground mine early-warning system. It ingests 2 Hz telemetry from four ESP-NOW sensor
nodes through a triangular UART ring, runs a dual-model AI cascade with a kinematic fallback, drives
Dijkstra evacuation routing over a georeferenced tunnel map, and dispatches multi-channel alerts.

**v6.0 adds the layer that makes it credible in a real mine:** an underground flight recorder. When
the surface cable is crushed — the single most likely first casualty of a collapse — the Central Hub
keeps recording every row to a MicroSD card. When the cable comes back, the backlog **replays itself
into the dashboard** and the gap fills in. Nothing is deleted from the card until every copy is safe.

**The three delivery planes:**

1. **Live plane** — USB serial at 2 Hz, in-memory state, alert dispatcher, dashboard. Never blocked
   by storage or by the network.
2. **Durable plane** — truth store + cloud outbox on the laptop. Survives a restart, retries freely.
3. **Recovery plane** — the SD blackbox, the delete gate, and REPLAY. The only plane that can
   outlive a collapse, and the only one that can authorise deleting data.

---

## 3. Complete backend feature matrix (B1 – B16)

| ID | Feature Name | Component | Status | Phase |
|---|---|---|---|---|
| **B1** | 4-Node Multi-Level State Machine | `telemetry_state` & ring ingestion | Planned | 1 |
| **B2** | Two-Stage Crack Anomaly Engine | Micro-strain (load cell + HX711) & macro pot | Planned | 3 |
| **B3** | Severity & Progression Estimator | TTF bucketing & velocity curvature | Planned | 6 |
| **B4** | Persistent Alert Dispatcher | Siren/LED, dashboard, Telegram, email audit | Planned | 8 |
| **B5** | Evacuation Routing Engine | Dijkstra on `tunnel_map.json` | Operational / enhancing | 7 |
| **B6** | Cloud Sync Outbox — **upgraded** | Offline store → GZIP + HMAC + idempotency | Planned | **9** |
| **B7** | Node & Hub Health Watchdog | 2 s heartbeats, staleness, ring cuts, **hub health** | Planned | 2 |
| **B8** | Node-Pair Relative Elongation (Δ) | Strain tensor between collinear nodes | Planned | 4 |
| **B9** | In-Domain Synthetic Augmentation | 50k+ window physics generator | **Implemented** | AI track |
| **B10** | Real-World Event Validation Harness | Open-pit radar TTF benchmark | **Implemented** | AI track |
| **B11** | Empirical Kinematic Fallback Calibration | Fukuzono α, A constants | **Implemented** | AI track |
| **B12** | Gated Transfer Learning Engine | Domain adaptation with hard MAE gate | Specified | 12 |
| **B13** | **Underground Blackbox Ingest** | Chunk protocol, SD ledger mirror, drop accounting | **P1 done** | **10** |
| **B14** | **Five-Condition Delete Gate** | The safe-unlink authority | **P1 done** | **10** |
| **B15** | **Reconciliation & REPLAY Recovery** | `STATE?` handshake, gap back-fill | Planned | **11** |
| **B16** | **Blackbox Observability** | Chips, chunk ledger, round-trip trace, drops | Planned | **12** |

---

## 4. The 3-tier AI data strategy (carried forward, unchanged)

### 4.1 The technical challenge: avoid scale contamination

Real-world geotechnical datasets sample at daily-to-monthly cadence (Fushun InSAR 12 days, Slovenia
GNSS daily, Baishuihe monthly). Underground strata collapse unfolds at **2 Hz**. Naively
concatenating them is **cross-scale temporal contamination**: the model is forced to reconcile
inconsistent acceleration time constants under one loss function, degrading the proven 5.4 s MAE.

### 4.2 Tier 1 — Real-event validation harness (best ROI, best viva answer)

External datasets are used as an **out-of-domain validation benchmark**, never as training data.
A real displacement trajectory passes through the same 16-feature rolling-window extractor into the
trained XGBoost model. The harness proves two things: steady-state creep pegs TTF at nominal
(**0.0 % false alarm**), and tertiary acceleration triggers `TTF < 120 s` **before** breach.

### 4.3 Tier 2 — Empirical kinematic fallback

The Fukuzono–Voight inverse-velocity law, `d²x/dt² = A (dx/dt)^α`, with α ≈ 1.95 ± 0.15 for brittle
rock. At α = 2.0 inverse velocity is linear in time. Wired in as a zero-dependency fallback that
survives missing, corrupted or absent models — **and it is the only estimator that still works when
the blackbox is replaying old data with no live sensors.**

### 4.4 Tier 3 — In-domain synthetic augmentation

Physics-based generation from 7,870 to 50,000+ windows: initial creep velocity 0.005–0.08 mm/s,
tertiary onset 0.3–0.6 × t_breach, α ∈ [1.6, 2.2], heteroscedastic Gaussian noise, and correlated
micro-strain / SW-420 event counts / macro opening. No external contamination.

### 4.5 Tier 4 — Gated transfer learning

Normalized-time domain adaptation on a separate branch, admitted only if held-out drill MAE beats
**5.4 s**. Otherwise the native augmented model stays in production.

---

## 5. Subsystem specifications

### 5.1 B1 — 4-node telemetry & multi-level state engine

- **Topology:** L1 (Tunnel A) = Node 1 near portal, Node 2 at the collapse face (two-stage crack);
  L2 (Tunnel B) = Node 3 near shaft, Node 4 at the subsidence seam (two-stage crack).
- **Ring ingestion:** Main Hub UART2 (GPIO 16/17) hears L1; UART1 (GPIO 4/13) hears L2. Heartbeats
  every 2.0 s per link. A single cut re-routes in < 1.0 s and the network stays up.

### 5.2 B2 — Two-stage crack anomaly engine

- **Micro (load cell + HX711 on Nodes 2 & 4):** 2 Hz median-filtered, 15 s settle-and-zero on boot.
  Event rule: `Δμ/Δt > 15 µε/s` → *"Micro-crack initiated"*. A railed reading is a **fault, never a
  zero** — an unplugged sensor must never look like "no strain".
- **Macro (rotary pot + lever on Nodes 2 & 4):** `Δcrack > 1.5 mm` → *"Crack propagating rapidly"*,
  clamped at 0 so backlash cannot subtract.

### 5.3 B4 — Persistent alert dispatcher (updated)

Four channels, each with its own failure mode, none dependent on the others:

| Channel | Audience | Latency | Transport |
|---|---|---|---|
| Local siren + WS2812B junction chase/strobe | miners underground | < 1 s | ring command |
| Dashboard + ledger | control room | < 1 s | in-process |
| Telegram push | shift in-charge, safety officer | < 5 s | HTTPS 443 |
| Email | DGMS regulator, audit trail | minutes | SMTP |

**New in v6.0:** a fifth alert class — **blackbox health** — covering `NO SD`, `DROPPED > 0`, and
`SYNC STALLED > 15 min`. These are safety alerts about the safety system, and they route to the
control room and the safety officer, not to the miners.

### 5.4 B5 & B8 — Dijkstra evacuation & pair elongation

`ΔL₁₂ = √((Δx₂−Δx₁)² + (Δy₂−Δy₁)²)` monitors tensile strain between collinear nodes. Routing computes
the shortest safe exit over `tunnel_map.json`, blocked links get `w = ∞`, and with no path to either
exit the junction **fails safe to HOLD** (amber pulse) — never pointing into danger.

### 5.5 B13 — Underground blackbox ingest

**The hardware fact that makes it work:** the Main Hub is **powered locally** and the surface cable is
**data-only**. Cut the cable and the hub keeps running; that is the entire point of the feature.

The Main Hub writes every row twice, with **one core owning the counter**:

- **Core 1 (Master Timekeeper)** receives every radio packet, increments `row_counter`, decides the
  rollover from chunk 42 to 43, and stamps the *same* `chunk_id` on both copies:
  - outward — the NDJSON line on the USB link;
  - inward — a 24-byte struct into Core 0's FreeRTOS queue.
- **Core 0 (Archivist)** appends the row to the open CSV chunk and rotates files **only when it sees
  the `chunk_id` change on the card**. It never counts. That removes the race condition by
  construction — a single writer, and the second core discovers state from the data.

**Rows vs chunks:** a row is 8 rows/s (2 Hz × 4 nodes). A chunk is 1,000 rows ≈ **2 minutes**. NAND
flash cannot erase a line, so we never delete rows — we **unlink whole files**.

**Queue overflow is counted, not denied.** `xQueueSend` with a 0-tick wait drops records when the
queue is full. Queue depth is ≥ 256 records (6 KB ≈ 32 s of stall); every dropped record increments a
counter; the hub reports `DROP:<chunk>,<count>`; and **any chunk with drops is never deleted**.

**Durability:** the chunk file is append-only and **flushed every 5 seconds**. A brownout detector
stops ingest — it does **not** attempt an SD write from an ISR, because that takes milliseconds and an
ISR lasts microseconds. Worst case: ≤ 5 s of the *open* chunk. Everything in every closed chunk is
on the card.

**The SD row schema equals the database schema**, twelve fields in the same order, so a blackbox
replay is a straight insert with no translation layer.

| SD / DB field | Meaning |
|---|---|
| `chunk_id` | stamped by Core 1 |
| `seq` | position within the chunk, 1..1000 |
| `ts_ms` | hub monotonic milliseconds |
| `node`, `level` | 1..4, 1 or 2 |
| `tilt`, `disp_mm`, `accel`, `vib`, `micro`, `macro`, `flags` | the reading |

**VSPI wiring (verified free against the A.2 pin freeze):** `CS → GPIO5`, `SCK → GPIO18`,
`MISO → GPIO19`, `MOSI → GPIO23`, `VCC → 3V3`, `GND → star ground`. The RC522 that uses these pins
lives on L1/L2, **not** on the Main Hub, so there is no contention. Start the bus at **4 MHz** and
raise it after it proves itself; 100 nF + 10 µF across the breakout.

### 5.6 B14 — The five-condition delete gate

`ACK_DELETE` is irreversible and destroys the last copy of data that exists nowhere else. It is a
**pure function** with an explicit reason for every refusal, and the reason is displayed.

| # | Condition | Refusal message |
|---|---|---|
| 1 | chunk is **CLOSED** | *"chunk is still open — the hub is writing to it"* |
| 2 | `received_rows == expected_rows` | *"12 rows would be lost"* |
| 3 | `dropped_rows == 0` | *"the SD copy is the only record of them — keep the file"* |
| 4 | `pending == 0` **and** the table's own `COUNT(*)` agrees | *"350 rows not yet confirmed by the cloud"* / *"internal inconsistency"* |
| 5 | closed ≥ **60 s** ago | *"closed 12s ago, safety margin is 60s"* |

An already-acknowledged chunk is never acked twice. A chunk whose `expected_rows` is unknown is
**held, not released** — absence of a count is a constraint, not a licence.

> The addendum's single-condition gate (`PENDING = 0 → delete`) would lose data permanently in the
> case where the laptop received fewer rows than the hub wrote: those rows were never enqueued, so
> `PENDING` reaches zero, and the file holding the only copy gets unlinked.

### 5.7 B15 — Reconciliation & REPLAY recovery

**Reconnect handshake.** On every reconnect the laptop sends `STATE?`. The hub answers
`CHUNK_STATE:<open>,<rows>,<dropped>` and `CHUNK_DONE:<chunk>,<rows>,<dropped>` for anything recently
closed and not yet acknowledged. Counts are **monotonic** — a stale reconciliation may never lower a
number we already hold, un-close a chunk, or erase rows.

**REPLAY — the demo that wins the room.** `REPLAY:<first>:<last>` makes the hub re-stream chunks from
the SD card. They pass through the *same* ingest path, and `UNIQUE(chunk_id, seq)` makes it
idempotent: replaying a chunk a hundred times inserts nothing new and re-queues nothing. On the
dashboard, the flat gap in the timeline **back-fills**. The addendum said "rescue teams retrieve the
card" — this is the part you can actually demonstrate in 20 seconds.

### 5.8 B16 — Blackbox observability

Four chips and a ledger, all fed from real state:

| Element | Shows | Turns red when |
|---|---|---|
| `BLACKBOX` | `ACTIVE · chunk 43 · 3,712 MB free` | card missing, full, or fault |
| `CLOUD` | `100 % SYNCED` or `350 PENDING` | pending > 0 for > 15 min |
| `DROPPED` | `0` | any drop — **and the affected chunk is pinned** |
| **Chunk ledger** | `chunk · rows · state · synced · dropped · hold reason` | a chunk is held |
| **Force-sync trace** | `pending → gzip 61 KB (85 %) → 200 in 412 ms → ACK → unlinked` | any stage fails |

### 5.9 B6 — Cloud sync outbox (upgraded)

| Rule | Why |
|---|---|
| **`200 OK` means `COMMIT`, not "accepted"** | otherwise the gate authorises deleting data that can still be lost |
| **Idempotency key per batch** — a duplicate returns 200 without inserting | the outbox *will* retry |
| **HMAC-SHA256 over body + batch sequence + timestamp** | a bare HMAC is replayable |
| **Timeouts tuned for a cold free-tier database** (Neon auto-suspends) | a 5 s timeout fails every first upload and the dashboard sits at PENDING while nothing is wrong |
| **Lease, not lock** — `claimed_at` + `claim_id`, released on failure | a second uploader must not double-send |
| **Never mark `SENT` except on an explicit 200**, and never report counts after a failed COMMIT | this was the old backend's worst bug |
| **GZIP is measured** — `bytes_raw`, `bytes_gzip` and HTTP status per batch | a measured number beats an asserted one |

**Target:** Neon Postgres (free tier). **Local truth store:** SQLite for the demo, schema written
Postgres-portable so the same code runs on both.

---

## 6. API endpoints

**Frozen — preserved 1:1 from v4.4** (the issued frontend change request depends on these):

| Endpoint | Method | Cadence | Description |
|---|---|---|---|
| `/api/telemetry` | GET | 500 ms | node displacements, velocities, accelerations, crack metrics |
| `/api/extended` | GET | 500 ms | full state: 4 nodes, 2 levels, ring links, sirens, alerts |
| `/api/history` | GET | on load | rolling history for the trend charts |
| `/api/ai` | GET | 1 s | model output, TTF, model status, feature importance |
| `/api/sim/trigger` | POST | on drill | deterministic scenario injection |
| `/api/sim/reset` | POST | on re-arm | clear alerts, restore nominal routing |
| `/api/ack` | POST | on operator action | acknowledge an alert |

**New in v6.0:**

| Endpoint | Method | Returns |
|---|---|---|
| `/api/blackbox/status` | GET | `state, open_chunk, rows_in_open, free_mb, dropped_total, last_flush_ms` |
| `/api/blackbox/chunks` | GET | the ledger, including the **hold reason** per chunk |
| `/api/blackbox/recover` | POST | sends `REPLAY:first:last`; returns rows back-filled |
| `/api/cloud/status` | GET | `pending, sent_24h, bytes_raw, bytes_gzip, last_error` |
| `/api/cloud/force_sync` | POST | runs a sync pass; returns the **round-trip trace** |
| `/api/cloud/trace/{batch_id}` | GET | per-stage timings for one batch |

---

## 7. THE PHASES

Thirteen phases, 0 → 12. Each ends in a **check that must pass** before the next begins.

### 7.1 The phase table

| Phase | Title | B-ID | Effort | Demo-critical | Depends on |
|---|---|---|---|---|---|
| **0** | Ground truth, contracts, replay harness | — | 1.5 d | **YES** | — |
| **1** | 4-node state engine + ingestion hardening | B1 | 3 d | **YES** | 0 |
| **2** | Node & hub health, watchdog, degraded mode | B7 | 1.5 d | **YES** | 1 |
| **3** | Two-stage crack engine | B2 | 2 d | **YES** | 1 |
| **4** | Elongation, settlement, zone risk | B8 | 1 d | no | 3 |
| **5** | ★ **The Double AI** — dual-model cascade | — | 5 d | **YES** | 1, 3 |
| **6** | Severity, progression, confidence | B3 | 3 d | **YES** | 5 |
| **7** | Dijkstra routing + `/api/map` | B5 | 3 d | **YES** | 1, 4 |
| **8** | Persistent alert dispatcher (+ blackbox alerts) | B4 | 4 d | **YES** | 6, 7 |
| **9** | Cloud sync outbox — **with the commit contract** | B6 | **3 d** | no | 8 |
| **10** | **Blackbox ingest + the five-condition delete gate** | **B13, B14** | **3 d** | **YES** | 9 |
| **11** | **Reconciliation handshake + REPLAY recovery** | **B15** | **3.5 d** | **YES** | 10 |
| **12** | **Blackbox observability, drop accounting, failure rehearsal** + transfer gate, metrics, ops | **B16,** B12 | **4 d** | **YES** | 11 |

**Total ≈ 37.5 days.** Previous total was 28.5 days for phases 0–10; v6.0 adds **9 days** for the
blackbox and upgrades Phase 9 rather than leaving cloud sync as a side task. **Minimum demoable path:
Phases 0–11 ≈ 33.5 days**, and with the already-built P0/P1 code (§7.4) that drops by about a day.

### 7.2 Dependency graph

```
        ┌──────────────────────────────────────────────┐
        │  P0  ground truth · contracts · replay        │  1.5 d
        └───────────────────────┬──────────────────────┘
                                ▼
        ┌──────────────────────────────────────────────┐
        │  P1  B1  4-node state + ingestion             │  3 d
        └───────┬───────────────────────┬──────────────┘
                ▼                       ▼
  ┌──────────────────────┐   ┌──────────────────────────┐
  │ P2  B7 health/hub    │   │ P3  B2 two-stage crack   │  2 d
  └──────────────────────┘   └────────────┬─────────────┘
                                          ▼
                             ┌──────────────────────────┐
                             │ P4  B8 elongation/zones  │  1 d
                             └────────────┬─────────────┘
                                          ▼
                             ┌──────────────────────────────────┐
                             │ P5  ★ THE DOUBLE AI              │  5 d
                             └────────────┬─────────────────────┘
                                          ▼
                             ┌──────────────────────────────────┐
                             │ P6  B3 severity/progression      │  3 d
                             └────────────┬─────────────────────┘
                                          ▼
                   ┌──────────────────────────────────────────┐
                   │ P7  B5 Dijkstra + /api/map               │  3 d
                   └────────────┬─────────────────────────────┘
                                ▼
                   ┌──────────────────────────────────────────┐
                   │ P8  B4 alert dispatcher (+ blackbox)     │  4 d
                   └────────────┬─────────────────────────────┘
                                ▼
                   ┌──────────────────────────────────────────┐
                   │ P9  B6 cloud sync + COMMIT CONTRACT      │  3 d
                   └────────────┬─────────────────────────────┘
                                ▼
                   ┌──────────────────────────────────────────┐
                   │ P10  B13+B14  blackbox ingest + GATE     │  3 d   ◀ the safety core
                   └────────────┬─────────────────────────────┘
                                ▼
                   ┌──────────────────────────────────────────┐
                   │ P11  B15  reconciliation + REPLAY        │  3.5 d ◀ the demo winner
                   └────────────┬─────────────────────────────┘
                                ▼
                   ┌──────────────────────────────────────────┐
                   │ P12  B16 + B12  observability + hardening│  4 d
                   └──────────────────────────────────────────┘
```

### 7.3 Per-phase detail

**PHASE 0 — Ground truth, contracts, replay harness · 1.5 d**
*Goal:* freeze every interface before anything depends on it.
*Build:* `config.py`, `contracts.py`, `assets/tunnel_map.json`, `assets/site_meta.json`,
`tools/record.py`, `tools/replay.py`, `tools/check_numbers.py`.
*Acceptance:* replay of a recorded session reproduces the live pipeline byte-for-byte; every number
quoted in the docs is asserted against `model_meta.json`.
*Closes:* defects D3, D4, D11a.

**PHASE 1 — B1: 4-node multi-level state engine & ingestion hardening · 3 d**
*Goal:* one place owns node state; the parser cannot be killed by a bad frame.
*Build:* `ingestion/serial_reader.py`, `ingestion/ring.py`, `ingestion/simulator.py`, `state.py`.
*Acceptance:* 4 nodes × 2 Hz for 10 minutes with zero dropped frames; a malformed line is counted and
survived; node count is changed in **one** place, not five.
*Closes:* D5, D7a, D9, D10, D13, D14.

**PHASE 2 — B7: health, watchdog, degraded mode · 1.5 d**
*Goal:* the system knows what it has lost.
*Build:* `engine/health.py` — heartbeat tracking, staleness flags, ring-cut detection, **and Main-Hub
health (SD present, free space, drops)**.
*Acceptance:* unplug a node → stale flag in ≤ 4 s; cut a ring link → re-route in < 1 s; unplug the SD
card → `BLACKBOX:NOSD` appears in the dashboard within 2 s.
*Closes:* D8.

**PHASE 3 — B2: two-stage crack engine · 2 d**
*Goal:* micro and macro crack channels, each with its own failure semantics.
*Build:* `engine/crack.py` — median filtering, 15 s settle-and-zero, rate rules, fault-not-zero on a
railed reading, backlash clamp.
*Acceptance:* a railed micro reading sets a fault flag and **never** reports 0; the macro channel is
monotonic and clamped at 0.

**PHASE 4 — B8: elongation, settlement, zone risk · 1 d**
*Goal:* turn two nodes into one geomechanical measurement.
*Build:* `engine/geomech.py`.
*Acceptance:* Δ matches hand-computed values on recorded data; zones A1/A2/B1/B2 score correctly.

**PHASE 5 — ★ THE DOUBLE AI: the dual-model cascade · 5 d**
*Goal:* two models, one advisory cascade, a firewall between them and the alarms.
*Build:* `engine/pattern.py` (M1 RandomForest, 200 × d8), `engine/ttf.py` (M2 XGBoost quantile trio on
32 features), `engine/cascade.py` (orchestrator + scheduler + firewall), `engine/kinematic.py`,
`models/MANIFEST.json`.
*Acceptance:* held-out critical MAE ≤ 0.10 s (n = 61) reproduced; hazard bands
`{QUIET 0.2, ENV 1.0, STRUCT 2.0, UNKNOWN 2.0}` behave; **`test_cascade_safety.py` proves the cascade
can never block, gate or suppress an alert**; with models absent the kinematic fallback still returns
a TTF.
*Re-run required:* M1 and M2 must be retrained on the v6.0 row schema (twelve fields, `micro`/`macro`
present) before this phase can pass — the feature vector changed.

**PHASE 6 — B3: severity, progression, confidence · 3 d**
*Goal:* one number the dashboard and the dispatcher both trust.
*Build:* `engine/severity.py` — TTF bucketing, progression stage, confidence from data quality
(staleness, faults, dropped rows).
*Acceptance:* severity never rises on stale data; confidence drops when rows are missing.
*Closes:* D2, D6.

**PHASE 7 — B5: Dijkstra routing + the missing `/api/map` · 3 d**
*Goal:* safe exits, and the map endpoint that never existed.
*Build:* `routing/dijkstra.py`, `api/routes_map.py`, `api/map_payload.py`.
*Acceptance:* blocked links take `w = ∞`; no-path fails safe to HOLD (amber); `/api/map` returns the
full site model.
*Closes:* D1, D7b.

**PHASE 8 — B4: persistent alert dispatcher · 4 d**
*Goal:* four independent channels, plus alerts about the recording system itself.
*Build:* `alerts/dispatcher.py`, `alerts/channels.py`, `alerts/templates.py`, `alerts_history` table.
*Acceptance:* local siren fires with the network down; Telegram delivers over 443 and **fails loudly**
into the ledger; email is never the first alarm; **`NO SD`, `DROPPED`, `SYNC STALLED` raise
blackbox-health alerts**.
*Closes:* D11b, D12.

**PHASE 9 — B6: cloud sync outbox, with the commit contract · 3 d**
*Goal:* get rows to the cloud exactly once, survivably, and prove it.
*Build:* `persistence/outbox.py`, `persistence/sync.py`, `cloud_client.py`, `cloud_batches` audit.
*Acceptance (against a mock cloud that can be told to misbehave):* 10,000 queued rows drain to zero
with two 500s then a 200; a batch sent twice inserts **once**; a 90-second cold start is survived;
a replayed sequence is rejected; the reported gzip ratio is **measured**.
*Risk:* this is where the old backend's worst bug lived — `attempts` bumped at claim time, then rows
lost. Claim with a lease, release on failure, mark `SENT` only on a 200.

**PHASE 10 — B13 + B14: blackbox ingest and the five-condition delete gate · 3 d**
*Goal:* mirror the underground card on the surface and build the authority that is allowed to destroy
it — and that refuses by default.
*Build:* `blackbox/protocol.py`, `blackbox/store.py`, `blackbox/gate.py`, `blackbox/ingest.py`,
`schema.sql` (truth store, chunk ledger, outbox, batches, hub status), **and the hub firmware that
emits the protocol**.
*Acceptance:*
- 480 rows over a 3-chunk simulated stream → 480 telemetry rows, 480 outbox rows, 0 unknown
- replaying the stream twice changes **nothing**
- every refusal path has a test, including *"12 rows would be lost"* — the case the addendum's
  single-condition gate would have deleted
- malformed wire data is counted, never fatal
- `CHUNK_STATE` reconciliation cannot un-close a chunk
- unplug the SD card → `BLACKBOX:NOSD` → **no ACK is ever emitted**
*Firmware acceptance:* Core 1 stamps both copies; Core 0 rotates only on a `chunk_id` change;
DROP is counted and reported; flush every 5 s.

**PHASE 11 — B15: reconciliation and REPLAY recovery · 3.5 d**
*Goal:* make the recovery story demonstrable.
*Build:* the `STATE?` handshake on reconnect, `REPLAY:first:last` handling in firmware and backend,
`recovery.py` orchestration, `POST /api/blackbox/recover`.
*Acceptance:*
- kill and restore the USB link 20 times mid-chunk; counts reconcile every time and no chunk is ever
  wrongly released
- cut the feed, let the hub write 3 chunks, reconnect, recover → **the dashboard gap back-fills** and
  `COUNT(*)` returns to exact
- recovering twice back-fills nothing the second time
- a replayed chunk's rows re-enter the AI pipeline with `source = 'replay'` and are distinguishable

**PHASE 12 — B16 + hardening: observability, drop accounting, failure rehearsal · 4 d**
*Goal:* rehearse the disasters until they are boring, and make the pipeline visible.
*Build:* `dashboard_data.py`, the four chips, the chunk ledger, the force-sync round-trip trace;
`failure_suite.py`; metrics, ops, runbook; the B12 transfer gate if time allows.
*Acceptance:* all ten failure-matrix scenarios pass **twice in a row**; the ledger shows
`CLOSED → SYNCED → ACK → DELETED` live; `DROPPED` shows a real number when a drop is injected;
every number in the presentation was produced by the suite.

### 7.4 Already built

Phases 10 and 11 are **partly complete** — a working skeleton exists and passes its tests:

| Built | Covers |
|---|---|
| `protocol.py` — frozen bidirectional wire format | Phase 10 firmware contract |
| `schema.sql` — truth store, chunk ledger, outbox, batches, hub status | Phase 10 |
| `store.py` — single write path, `UNIQUE(chunk_id, seq)` | Phase 10 |
| `gate.py` — the five-condition gate, pure function | **Phase 10 safety core** |
| `ingest.py` — line loop + periodic ACK sweep | Phase 10 |
| `sim/hub_sim.py` — fake hub speaking the real protocol | Phase 10 test harness |
| `tests/` — **29 assertions, all passing** | Phase 10 acceptance |

So Phase 10 is roughly **40 % done**; the remaining work is the firmware side and the real serial link
from Phase 1. Phase 11 (reconnect, REPLAY) is not started.

---

## 8. Target directory structure — the new project

One repository, five top-level areas: firmware, backend, AI, frontend, infra. The backend is the part
that changed most in v6.0, so it is expanded in full.

```
neural-strata/
│
├── README.md                       project overview, quick start, demo script
├── Makefile                        make test · make run · make sim · make replay
├── .env.example                    every variable, documented, no secrets
│
├── docs/                           the plans, review notes and rendered PDFs
│   ├── Neural_Strata_Backend_Plan.md              ← this document (v6.0)
│   ├── Neural_Strata_Blackbox_Review.md           the addendum review + corrections
│   ├── Neural_Strata_SRS_v1.1.md
│   ├── Neural_Strata_Hardware_Build_Phases.md
│   ├── Neural_Strata_Wiring_Walkthrough.md
│   └── Neural_Strata_Frontend_Change_Request.md
│
├── firmware/                       PlatformIO · ESP32 · Arduino framework
│   ├── platformio.ini              env:main_hub · env:level_hub · env:node
│   ├── lib/
│   │   ├── ring/                   UART link framing, heartbeats, re-route
│   │   └── espnow_packet/          the 2 Hz packet struct, shared by all three builds
│   └── src/
│       ├── common/                 config.h · pins.h · power.h · leds.h
│       ├── main_hub/
│       │   ├── main.cpp            Core 1: ESP-NOW + ring + serial
│       │   ├── timekeeper.cpp      ★ Core 1 MASTER TIMEKEEPER — owns chunk_id
│       │   ├── archivist.cpp       ★ Core 0 — queue drain, append, 5 s flush, rotate
│       │   ├── blackbox.cpp        ★ SD mount, FIFO eviction, DROP counting, REPLAY
│       │   ├── protocol_out.cpp    ★ NDJSON up, ACK_DELETE / REPLAY down
│       │   └── brownout.cpp        ★ flag + stop ingest (never writes from an ISR)
│       ├── level_hub/
│       │   ├── main.cpp            ring relay + gate + strips + buzzer
│       │   ├── rc522.cpp           tag read → beep → gate LEDs
│       │   └── strips.cpp          WS2812B junction chase, 300 mA cap
│       └── node/
│           ├── main.cpp            sensors → ESP-NOW at 2 Hz
│           ├── hx711.cpp           load-cell bridge, 15 s settle, fault-not-zero
│           ├── mpu6050.cpp         tilt, acceleration
│           └── sw420.cpp           vibration event-rate accumulator
│
├── backend/                        FastAPI/Flask · the whole v6.0 plan
│   ├── app.py                      bootstrap only (~80 lines)
│   ├── config.py                   every constant, env-parsed
│   ├── contracts.py                schema version, node IDs, frame validators
│   ├── state.py                    SystemState + lock + history
│   ├── metrics.py                  counters, timers, /api/metrics
│   │
│   ├── ingestion/
│   │   ├── serial_reader.py        USB serial → validated frames → state
│   │   ├── ring.py                 relay frames, heartbeats, link state
│   │   └── simulator.py            scenario console, 7 injection profiles
│   │
│   ├── blackbox/                   ★ NEW in v6.0 — the recovery plane
│   │   ├── protocol.py             ★ frozen hub ↔ laptop wire format (both ways)
│   │   ├── ingest.py               ★ line loop + periodic ACK sweep
│   │   ├── gate.py                 ★ THE FIVE-CONDITION DELETE GATE
│   │   ├── store.py                ★ truth store + chunk ledger + outbox writes
│   │   ├── reconciliation.py       ★ STATE? handshake, monotonic count merge
│   │   ├── recovery.py             ★ REPLAY orchestration + gap back-fill
│   │   ├── reader.py               pyserial thread, framing, reconnect backoff
│   │   └── schema.sql              ★ all blackbox DDL in one place
│   │
│   ├── engine/
│   │   ├── features.py             the 16-feature window extractor
│   │   ├── kinematic.py            calibrated Fukuzono–Voight fallback (B11)
│   │   ├── pattern.py              MODEL 1 — deformation pattern discriminator
│   │   ├── ttf.py                  MODEL 2 — TTF regressor + quantile band
│   │   ├── cascade.py              THE DOUBLE AI orchestrator + firewall
│   │   ├── crack.py                B2 two-stage micro/macro crack engine
│   │   ├── geomech.py              B8 pair elongation, differential settlement
│   │   ├── severity.py             B3 severity, progression stage, confidence
│   │   └── health.py               B7 heartbeat, staleness, degraded mode, hub health
│   │
│   ├── routing/
│   │   └── dijkstra.py             B5 hazard-weighted shortest safe exit
│   │
│   ├── alerts/
│   │   ├── dispatcher.py           B4 alert state machine + dedup + escalation
│   │   ├── channels.py             siren/LED, dashboard, Telegram, email
│   │   ├── templates.py            message text, recommended actions, geo links
│   │   └── blackbox_alerts.py      ★ NO SD · DROPPED · SYNC STALLED
│   │
│   ├── persistence/
│   │   ├── db.py                   pooled connections, schema init, migrations
│   │   ├── outbox.py               B6 local queue with lease-based claims
│   │   ├── sync.py                 B6 uploader daemon: gzip, HMAC+seq, idempotency
│   │   └── cloud_client.py         HTTP transport, cold-start timeouts, retry policy
│   │
│   ├── api/
│   │   ├── routes_telemetry.py     /api/telemetry · /api/extended · /api/history
│   │   ├── routes_ai.py            /api/ai
│   │   ├── routes_alerts.py        /api/alerts · /api/ack
│   │   ├── routes_map.py           /api/map · /api/site
│   │   ├── routes_blackbox.py      ★ /api/blackbox/{status,chunks,recover}
│   │   ├── routes_cloud.py         ★ /api/cloud/{status,force_sync,trace}
│   │   ├── routes_admin.py         /api/sim/{trigger,reset} · /api/metrics
│   │   └── map_payload.py          the GIS payload builder
│   │
│   ├── assets/
│   │   ├── tunnel_map.json         frozen topology: nodes, J1–J4, exits, 9 links, zones
│   │   └── site_meta.json          baseline 23.643 / 85.318, 1 cm = 10 m, exit list
│   │
│   ├── models/
│   │   ├── ttf_model_v1.json       MODEL 2, current
│   │   ├── ttf_model_v2.json       quantile trio q10/q50/q90
│   │   ├── pattern_model_v1.json   MODEL 1
│   │   ├── scaler.json             shared scaling contract
│   │   ├── pattern_meta.json       M1 metrics, thresholds, class list
│   │   ├── kinematic_calibration.json   α, A from Tier 2
│   │   └── MANIFEST.json           which artifacts belong together (version pin)
│   │
│   ├── sim/
│   │   ├── hub_sim.py              ★ fake Central Hub, real protocol bytes
│   │   ├── cloud_mock.py           ★ a cloud that can be told to fail
│   │   └── scenarios/              7 scripted incidents
│   │
│   ├── tools/
│   │   ├── record.py               dump a live session → CSV
│   │   ├── replay.py               feed CSV back through the full pipeline
│   │   ├── make_counterfactuals.py generate the Model-1 training set
│   │   ├── eval_cascade.py         confusion matrix, suppression rate, latency
│   │   ├── check_numbers.py        assert every docs number against model_meta.json
│   │   └── failure_suite.py        ★ run all ten disaster scenarios, timed
│   │
│   └── tests/
│       ├── test_contracts.py       test_features.py      test_dijkstra.py
│       ├── test_alerts.py          test_outbox.py        test_kinematic.py
│       ├── test_api.py             test_health.py        test_crack.py
│       ├── test_gate.py            ★ every delete-gate refusal path
│       ├── test_ingest.py          ★ replay idempotency, drop accounting
│       ├── test_protocol.py        ★ the frozen wire format
│       ├── test_recovery.py        ★ REPLAY back-fill, reconciliation
│       └── test_cascade_safety.py  ★ THE most important test in the repository
│
├── ai/                             notebooks, training, validation
│   ├── datasets/                   raw + processed, never committed if large
│   ├── notebooks/
│   │   ├── 03_synthetic_augmentation.py
│   │   ├── 04_external_validation.py
│   │   ├── 05_calibrate_kinematic.py
│   │   └── 06_train_pattern_model.py    M1, retrained for the v6.0 row schema
│   ├── export/                     model artifacts copied into backend/models/
│   └── reports/                    validation output, MAE tables, plots
│
├── frontend/                       the operator dashboard (PWA)
│   ├── index.html
│   ├── gis-map.js                  SVG tunnel map, zones, link states
│   ├── blackbox.js                 ★ chips, chunk ledger, force-sync trace
│   ├── manifest.webmanifest
│   ├── service-worker.js
│   ├── pwa/                        icons
│   └── tools/
│       └── check_claims.py         the CR-01…CR-14 acceptance checker
│
└── infra/
    ├── docker-compose.yml          local Postgres, optional
    ├── sql/                        cloud-side schema + the ingest function
    ├── systemd/                    service units for the laptop
    └── runbook.md                  incident procedures, including gate refusal
```

**The five rules this layout encodes:**

1. **`app.py` shrinks.** By Phase 12 it is a bootstrap file. Everything else moved out.
2. **Every file has one reason to change.** The tunnel map changes → `dijkstra.py`. The wire format
   changes → `blackbox/protocol.py`. Nothing else moves.
3. **`blackbox/` is a peer, not a helper.** It is a whole plane of the system with its own store, its
   own contract and its own safety authority — so it gets its own package, not a folder inside
   `persistence/`.
4. **The gate is one small file.** It is the only code in the repository whose job is to authorise the
   destruction of data, so it should be small enough to read in one sitting and test exhaustively.
5. **Mocks live with the code they mock.** `sim/hub_sim.py` and `sim/cloud_mock.py` mean every phase
   from 9 onward is testable with no hardware and no network.

---

## 9. Timeline

| Week | Focus | Phases |
|---|---|---|
| **W1** | Mesh foundation, contracts, state engine | 0, 1 |
| **W2** | Sensors, health, ring integration | 2, 3, 4 |
| **W3** | AI cascade, severity, routing | 5, 6, 7 |
| **W4** | Alerts, cloud sync with the commit contract | 8, 9 |
| **W5** | **Blackbox: ingest, gate, reconciliation, REPLAY** | **10, 11** |
| **W6** | **Observability, failure rehearsal, transfer gate** | **12** |

**Parallel tracks for 6 people:** after Phase 1 splits, one pair runs 2→4 (sensors/geomechanics), a
second runs 5 (AI), a third runs 7 (routing). **Phase 5 must not start before Phase 3 lands** — the
crack channels are Model 1's most valuable input. **Phases 10 and 11 are the least parallelisable**:
the gate is a safety authority and should be written by one person and reviewed by another.

---

## 10. Acceptance checklist for the whole backend

- [ ] A malformed serial line cannot stop ingestion — it is counted and survived
- [ ] Changing the node count requires editing exactly **one** place
- [ ] `test_cascade_safety.py` proves the AI cascade can never block, gate or suppress an alert
- [ ] With every model file deleted, the kinematic fallback still returns a TTF
- [ ] Dijkstra fails safe to HOLD, never pointing into danger
- [ ] The local siren fires with the laptop's network down
- [ ] Telegram delivers over 443 and fails **loudly** into the ledger
- [ ] 10,000 outbox rows drain with two injected 500s, and a duplicated batch inserts once
- [ ] **The delete gate refuses on all five conditions, each with a test**
- [ ] **A chunk with dropped rows is never deleted, even when fully synced**
- [ ] **Unplugging the SD card stops every ACK_DELETE**
- [ ] **Replaying a chunk twice changes nothing in the database**
- [ ] **After a cable cut, RECOVER back-fills the dashboard gap and the count returns to exact**
- [ ] All ten failure-matrix scenarios pass twice in a row
- [ ] Every number quoted in the presentation was produced by `failure_suite.py`

---

## 11. Deliberately not building

| Not building | Why |
|---|---|
| A novelty / anomaly-detection layer | Decided: double AI only |
| Per-junction microcontrollers | Junctions stay passive WS2812B |
| GPS / DGPS | Prototype decision; georeferencing is a fixed baseline plus offsets |
| SMS | Conditional production delta; not a demo channel |
| A second uploader or worker fleet | One laptop, one outbox, one lease holder |
| Cloud-side migration tooling | The cloud copy is one table and a straight insert |
