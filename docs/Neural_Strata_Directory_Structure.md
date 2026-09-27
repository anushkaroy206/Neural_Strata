# Neural Strata — Project Directory Structure v6.0

**Status:** the target tree for the whole project as it stands after the blackbox addendum.
**Supersedes:** the Phase-0-era layout in `Neural_Strata_Backend_Build_Phases.md` §A.1 (which predates
the underground blackbox and had no firmware, AI, frontend or infra areas).
**Companion:** `Neural_Strata_Backend_Plan.md` v6.0 §7 (the phases) and §8 (this structure).
**Rev 6.0.1:** §4 extended — the firmware is now mapped to phases. §2 (the tree) was unchanged;
only the phase-to-file assignment gained the firmware column.
**Rev 6.0.2:** §4.4 added — the database, including **the changes to the existing database** (the
migration ledger). One file joined §2: `persistence/rollup.py`, which
`Neural_Strata_Dual_Database.md` §F.3 requires but the tree was missing.
**Rev 6.0.6:** end-of-Phase-5 audit (`/tmp/audit/tree_audit.py`, re-runnable). §2
gained **30 rows** for files that exist, compile or ship and that the tree had not
listed — the frontend's `src/` build tree (the bundle is generated, so the sources
are the artefact), the blackbox gateway package that has been running since Phase
0, the eight test files written across Phases 1–5, four documents, and
`model_meta_v2.json`. One **named wrongly**: the tree said
`06_train_pattern_model.py`; `Backend_Build_Phases.md` §5.8/§5.9, `02_training.py`'s
own imports and the file on disk all say `06_train_pattern.py`, so the tree was the
outlier and is corrected. The other 48 declared-but-absent files are Phases 6–13
work and are *correctly* absent.

**Rev 6.0.6 (continued):** one naming consequence recorded rather than a file
chased. The tree lists Phase 12's panel as `frontend/blackbox.js`, a root-level
file — that was the layout *before* the frontend gained a build step. In the
delivered layout the panel is `frontend/src/js/13-blackbox-cloud.js` and it is
inlined into the built `index.html` by `tools/build-single.js`. The Phase-12 row
in §4 is updated to the path that will actually be edited; the root
`frontend/blackbox.js` is **not** created, because a second copy of the panel
outside the bundle is an edit that would never ship.

**Rev 6.0.9:** end of Phase 7 (B5). §2 gains the files the phase created that the
tree had not listed — `frontend/src/js/14-gis.js` (the GIS panel) and the §2 rows
it needs — and records one naming reconciliation of the same kind Rev 6.0.6 made
for the black-box panel: the tree lists the GIS panel as `frontend/gis-map.js`, a
root-level file, which was the layout *before* the frontend gained a build step.
In the delivered layout the shipped page is the bundle built from `frontend/src/`
and every script in it is a numbered file under `src/js/`, so the panel is
`src/js/14-gis.js` and the root copy is **not** created — a second copy of the
panel outside the bundle is an edit that never ships. `/assets/<path>` and the
catch-all `/<path>` still serve the frontend directory, which is what §7.6's
`get_asset` route is for; nothing asks for `/assets/gis-map.js` any more.

**Rev 6.0.8:** end of Phase 6 (B3). §2 gains one row the phase created —
`tests/test_severity.py` (56 assertions over the tier bands, the creep-stage
machine and its hysteresis, D6's call graph, the confidence demotions and §8.6's
action table). The phase created no other file: `engine/severity.py` was already
declared, the frontend work landed in `frontend/src/` (already declared) and the
report went into `ai/reports/cascade_metrics.md` §8, which §2 lists as the
metrics report. Two §4 notes ride with it: **D2** and **D6** are both closed, and
`05_calibrate_kinematic.py` keeps its filename while its function is renamed to
`record_kinematic_constants()` — the tree is binding on the path, so the honesty
§6.5 asked for lives in the function name, the docstring and the artifact's new
`provenance` block.

**Rev 6.0.7:** end-of-Phase-5 close-out. The §2 audit is a committed tool now
(`backend/tools/audit_tree.py`) instead of a `/tmp` script that could not be re-run — which
defeats the point of an audit. Running it found eight files §2 declares that no phase
claimed, so §4 gained them where the other documents already put them: the **`app.py` split**
(`routes_telemetry.py` · `routes_ai.py` · `routes_admin.py` · `tests/test_api.py`) and
`tools/preflight.py` in **Phase 12** (§3 rule 1 fixes `app.py` as a bootstrap by then,
and `routes_admin.py` owns `/api/metrics`, a P12 endpoint), `tests/test_sync_logic.py` and
`tools/verify_dual_db_sql.py` in **Phase 9** (`Dual_Database.md` §F is where both are
specified), and `tools/led_test.py` in **Phase 8** (it fires patterns at the level-hub
strips). `§2` also gained the **`runtime/`** row that `.gitignore`, `blackbox/gateway.py`
and `sim/cloud_mock.py` all already required — it was a real directory the tree did not name.
The audit now reports 48 declared-but-absent files, every one owed by Phases 6–13 or H4,
and **nothing on disk outside §2**.

**Rev 6.0.5:** Phase 0 **closed against the row in §4**, including the third column
(**H0**) which Rev 6.0.4 left unbuilt: the three PlatformIO environments now carry
stub sources and all three compile. Two corrections recorded below.

**Rev 6.0.4:** Phase 0 built. §2 gains the six files the phase actually created
that the tree had not listed (`tests/conftest.py`, `tests/shape.py`,
`tests/fixtures/`, `tools/snapshot_api.py`, and the two one-shot migration
scripts); `recordings/` joins the runtime rows; §4.5 marks P0 complete with its
evidence.

**Rev 6.0.3:** pre-flight audit before Phase 0. Nine files that other documents require but the
tree lacked; `B9`/`B10` attached to Phase 5 (they were orphaned); every phase row now carries its
feature IDs; a feature-coverage check (§4.5) added. §2 gained 12 rows in total.

---

## 1. What changed from the old tree

| Change | Why |
|---|---|
| **`backend/blackbox/` added as a peer package** | It is a whole plane of the system — its own store, its own wire contract, its own safety authority. Not a folder inside `persistence/`. |
| **`firmware/` added as a top-level area** | The Core-1-Master-Timekeeper / Core-0-Archivist split is a firmware fact. It belongs in the repository, not only in a document. |
| **`sim/` added with `hub_sim` and `cloud_mock`** | Every phase from 9 onward becomes testable with no hardware and no network. |
| **`api/routes_blackbox.py` and `api/routes_cloud.py`** | Six new endpoints; the frozen nine stay untouched. |
| **`alerts/blackbox_alerts.py`** | `NO SD`, `DROPPED > 0`, `SYNC STALLED` are safety alerts about the safety system. |
| **`models/kinematic_calibration.json` gets a reader** | Defect D2: the artifact existed and nothing read it. |
| **`tools/failure_suite.py`** | Ten disaster scenarios, scripted and timed. |
| **`docs/` added** | The plans, the review and the rendered PDFs live with the code. |
| **`engine/novelty.py` — RESERVED, EMPTY** | Conditional third AI layer (§5). Nothing depends on it; the slot exists so that adding it later is a change of one flag, not a refactor. |
| **`dashboard_data.py` — SUPERSEDED, do not create it** | A v5-era name for the chips, chunk ledger and round-trip trace. In v6.0 that work is split: the data comes from `api/routes_blackbox.py`, the panel is `frontend/blackbox.js`. `Neural_Strata_Backend_Plan.md` §5 still names it — that reference is stale. |

---

## 2. The tree

```
neural-strata/
│
├── README.md                       project overview · quick start · the demo script
├── Makefile                        make test · make run · make sim · make replay · make check
├── run.sh                          cold start: deps → schema → config → models → Flask → first curl
├── restore.sh                      pg_dump restore, the pair of the pre-rehearsal backup habit
├── requirements.txt                pinned dependencies (created in Phase 0)
├── recordings/                     session.csv captures for replay (runtime, gitignored)
├── runtime/                        blackbox sqlite store + cloud stand-in (runtime, gitignored)
├── .env.example                    every variable documented, no secrets
├── .gitignore
│
├── docs/                           plans and specifications, kept with the code
│   ├── Neural_Strata_Backend_Plan.md              ← v6.0, the authoritative plan
│   ├── Neural_Strata_Backend_Plan_v5.md           ← the v5 plan, kept for provenance (§1)
│   ├── Neural_Strata_Backend_Build_Phases.md      ← the phase-by-phase build manual
│   ├── Neural_Strata_Dual_Database.md             ← the edge/cloud split, cited by Phase 9
│   ├── Neural_Strata_Requirement_Compliance.md    ← requirement → artefact traceability
│   ├── Neural_Strata_Test_Pack.md                 ← the test matrix the phases draw from
│   ├── Neural_Strata_Blackbox_Review.md           ← the addendum review + corrections
│   ├── Neural_Strata_SRS_v1.1.md
│   ├── Neural_Strata_Hardware_Build_Phases.md
│   ├── Neural_Strata_Wiring_Walkthrough.md
│   ├── Neural_Strata_Frontend_Change_Request.md
│   ├── Neural_Strata_Frontend_Review.md
│   ├── Neural_Strata_Directory_Structure.md       ← this document
│   ├── neural-strata-crack-sensor.md              ← the H2 reference design (cited §A.4.2)
│   └── pdf/                        rendered, print-ready copies
│
├── firmware/                       PlatformIO · ESP32 · Arduino framework
│   ├── platformio.ini              env:main_hub · env:level_hub · env:node
│   ├── lib/
│   │   ├── ring/                   UART link framing, heartbeats, re-route detection
│   │   ├── espnow_packet/          the 2 Hz packet struct, shared by all three builds
│   │   └── ws2812b/                strip driver with the mA cap helper
│   └── src/
│       ├── common/
│       │   ├── config.h            node IDs, cadence, queue depth, chunk size
│       │   ├── pins.h              the A.2 pin freeze, in one file
│       │   ├── power.h             brownout detector: flag + stop ingest
│       │   └── leds.h              gate lamps, status indication
│       ├── main_hub/
│       │   ├── main.cpp            Core 1: ESP-NOW ingest + ring relay + serial out
│       │   ├── timekeeper.cpp      ★ CORE 1 MASTER TIMEKEEPER — owns row_counter & chunk_id
│       │   ├── archivist.cpp       ★ CORE 0 — queue drain, CSV append, 5 s flush, rotate
│       │   ├── blackbox.cpp        ★ SD mount · DROP counting · FIFO eviction · REPLAY serving
│       │   ├── protocol_out.cpp    ★ NDJSON up · ACK_DELETE / REPLAY / STATE? down
│       │   └── serial_surface.cpp  framed USB link; the cable that gets cut
│       ├── level_hub/
│       │   ├── main.cpp            ring relay + gate + strips + buzzer
│       │   ├── ring_port.cpp       two hardware UARTs, 17/16 and 4/13
│       │   ├── rc522.cpp           tag read → beep → gate LEDs
│       │   └── strips.cpp          J1/J2 or J3/J4 chase, 300 mA cap, FIFO apply
│       └── node/
│           ├── main.cpp            sensors → ESP-NOW at 2 Hz
│           ├── hx711.cpp           load-cell bridge: 15 s settle, baseline, fault-not-zero
│           ├── mpu6050.cpp         tilt + real acceleration (closes defect D9)
│           ├── sw420.cpp           vibration event-rate accumulator
│           └── pots.cpp            micro/macro potentiometer channels
│
├── backend/                        FastAPI/Flask · everything in Plan v6.0
│   ├── app.py                      bootstrap only (~80 lines by Phase 12)
│   ├── config.py                   every constant, env-parsed, imported by all
│   ├── contracts.py                schema version, node IDs, frame validators
│   ├── state.py                    SystemState + lock + history
│   ├── metrics.py                  counters, timers, /api/metrics
│   │
│   ├── ingestion/
│   │   ├── serial_reader.py        USB serial → validated frames → state
│   │   ├── ring.py                 relay frames, heartbeats, link state
│   │   └── simulator.py            scenario console + 7 injection profiles
│   │
│   ├── blackbox/                   ★ NEW — the recovery plane
│   │   ├── protocol.py             ★ frozen hub ↔ laptop wire format, both directions
│   │   ├── reader.py               pyserial thread, framing, reconnect with backoff
│   │   ├── ingest.py               ★ line loop → truth store + outbox + ledger
│   │   ├── gate.py                 ★ THE FIVE-CONDITION DELETE GATE (pure function)
│   │   ├── store.py                ★ truth store · chunk ledger · outbox writes
│   │   ├── gateway.py              the serial/SIM gateway the reader thread drives
│   │   ├── status.py               gateway mode + health, for /api/blackbox
│   │   ├── __main__.py             `python3 -m blackbox` — the standalone gateway run
│   │   ├── README.md               the recovery plane's own notes
│   │   ├── tests/                  the gateway's self-contained test runner
│   │   ├── reconciliation.py       ★ STATE? handshake, monotonic count merge
│   │   ├── recovery.py             ★ REPLAY orchestration + dashboard gap back-fill
│   │   └── schema.sql              ★ all blackbox DDL in one place
│   │
│   ├── engine/
│   │   ├── features.py             the 16-feature rolling-window extractor
│   │   ├── kinematic.py            calibrated Fukuzono–Voight fallback (B11, closes D2)
│   │   ├── pattern.py              MODEL 1 — Deformation Pattern Discriminator
│   │   ├── ttf.py                  MODEL 2 — TTF regressor + quantile band
│   │   ├── cascade.py              ★ THE DOUBLE AI orchestrator + scheduler + firewall
│   │   ├── novelty.py              ☆ RESERVED — conditional 3rd layer, stub only (§5)
│   │   ├── crack.py                B2 two-stage micro/macro crack engine
│   │   ├── geomech.py              B8 pair elongation, differential settlement, zone risk
│   │   ├── severity.py             B3 severity, progression stage, confidence
│   │   └── health.py               B7 heartbeat, staleness, degraded mode, hub health
│   │
│   ├── routing/
│   │   └── dijkstra.py             B5 hazard-weighted shortest safe exit
│   │
│   ├── alerts/
│   │   ├── dispatcher.py           B4 alert state machine + dedup + escalation
│   │   ├── channels.py             local siren/LED · dashboard · SMS (hub modem) · email
│   │   │                           §2 said "Telegram" here; §8.3/§8.4 mandate **SMS**
│   │   │                           through the main hub's 4G modem, and that is what
│   │   │                           shipped. Reconciled in Phase 8 — this row is the record.
│   │   ├── templates.py            message text, recommended actions, georeferenced links
│   │   └── blackbox_alerts.py      ★ NO SD · DROPPED · SYNC STALLED
│   │
│   ├── persistence/
│   │   ├── db.py                   pooled connections, schema init, migrations
│   │   ├── schema.sql              telemetry, alerts, ledger DDL
│   │   ├── outbox.py               B6 local queue with lease-based claims
│   │   ├── rollup.py               hourly rollup + synced_at watermark (the bulk sync path)
│   │   ├── sync.py                 B6 uploader daemon: gzip, HMAC+sequence, idempotency
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
│   │   └── map_payload.py          the GIS payload builder gis-map.js already expects
│   │
│   ├── assets/
│   │   ├── tunnel_map.json         frozen topology: nodes, J1–J4, exits, 9 links, zones
│   │   └── site_meta.json          baseline 23.643 / 85.318 · 1 cm = 10 m · exit list
│   │
│   ├── models/
│   │   ├── ttf_model_v1.json       MODEL 2, current
│   │   ├── ttf_model_v2.json       quantile trio q10/q50/q90
│   │   ├── pattern_model_v1.json   MODEL 1
│   │   ├── scaler.json             shared scaling contract
│   │   ├── pattern_meta.json       M1 metrics, thresholds, class list
│   │   ├── model_meta.json
│   │   ├── model_meta_v2.json      the trio's provenance: coverage 0.9986, MAE 2.49 s         canonical MAE / RMSE / n / dataset provenance
│   │   ├── kinematic_calibration.json   α and A from the Tier-2 calibration (now read)
│   │   ├── novelty_v1.joblib       ☆ RESERVED — absent by default, never committed
│   │   ├── novelty_meta.json       ☆ RESERVED — { "enabled": false } until the gate passes
│   │   └── MANIFEST.json           which artifacts go together; records novelty enabling
│   │
│   ├── sim/
│   │   ├── hub_sim.py              ★ fake Central Hub, real protocol bytes
│   │   ├── cloud_mock.py           ★ a cloud that can be told to fail, stall or duplicate
│   │   └── scenarios/              7 scripted incidents + 10 disaster cases
│   │
│   ├── tools/
│   │   ├── record.py               dump a live session → CSV
│   │   ├── replay.py               feed CSV back through the full pipeline
│   │   ├── make_counterfactuals.py generate the Model-1 training set (7 classes)
│   │   ├── eval_cascade.py         confusion matrix, suppression rate, latency
│   │   ├── check_numbers.py        ★ asserts every docs number against model_meta.json
│   │   ├── check_contracts.py      asserts code == frozen contract, exits non-zero on drift
│   │   ├── snapshot_api.py         capture the API surface and diff it against tests/fixtures
│   │   ├── audit_tree.py           ★ §2 against the filesystem — run it at every phase close-out
│   │   ├── port_app_phase0.py      ★ one-shot: the scripted v4.4 → v6.0 app.py extraction
│   │   ├── fix_numbers_phase0.py   ★ one-shot: the D3/D4 document reconciliation
│   │   ├── failure_suite.py        ★ run all ten disaster scenarios, timed, with assertions
│   │   ├── preflight.py            ★ READY / NOT READY in one line, five minutes before the demo
│   │   ├── bench_latency.py        asserts M1 < 1 ms and M2 < 3 ms per inference
│   │   ├── led_test.py             one-line host helper: fire any LED pattern at a junction
│   │   └── verify_dual_db_sql.py   re-runs the two sync checks after any sync.py edit
│   │
│   └── tests/
│       ├── conftest.py             puts backend/ on sys.path, pins the test environment
│       ├── shape.py                the key-path extractor every API-shape test compares with
│       ├── fixtures/              ★ the JSON captured from the RUNNING v4.4 server, pre-extraction
│       ├── test_contracts.py       the frozen schemas, version integers, node registry
│       ├── test_features.py        the 16-feature window, hand-checked values
│       ├── test_kinematic.py       Fukuzono fallback with every model file deleted
│       ├── test_crack.py           two-stage micro/macro, fault-not-zero
│       ├── test_geomech.py         pair elongation against hand-computed values
│       ├── test_health.py          heartbeat, staleness, ring cut, hub health
│       ├── test_dijkstra.py        blocked links, fail-safe HOLD
│       ├── test_alerts.py          four channels, dedup, blackbox alerts
│       ├── test_outbox.py          lease claims, retry, no double-send
│       ├── test_api.py             the frozen endpoint matrix + the new ones
│       ├── test_protocol.py        ★ the frozen wire format, both directions
│       ├── test_gate.py            ★ every delete-gate refusal path
│       ├── test_ingest.py          ★ replay idempotency, drop accounting, chunk ledger
│       ├── test_recovery.py        ★ REPLAY back-fill, reconciliation monotonicity
│       ├── test_cascade_safety.py  ★★ THE most important test in the repository
│       ├── test_pattern.py         ★ M1's verdicts, the bounded override, the §5.8 surface
│       ├── test_pattern_features.py ★ the 16 disruption features, hand-checked values
│       ├── test_scheduler_rates.py ★ §5.7 cadence — and that the firewall ignores it
│       ├── test_severity.py      ★ B3 tiers, the creep-stage machine, D6, confidence
│       ├── test_counterfactual_labels.py ★ corpus labels + the profile declarations
│       ├── test_api_contracts.py   the frozen fixture shapes, pre-extraction
│       ├── test_gateway_contract.py ★ the gateway's silent zero-row failure
│       ├── test_node_registry.py   node IDs come from config, never hardcoded
│       ├── test_ring_relay.py      the ring relay's link-state and heartbeat rules
│       ├── test_state_reset.py     ★ the re-arm path leaves no stale state behind
│       ├── test_derivatives_dt.py  ★ acceleration and rate maths against hand values
│       ├── test_sync_logic.py      ★ runs sync_once() verbatim against mock edge + mock cloud
│       └── test_novelty_isolation.py  ☆ proves the reserved layer changes nothing when off
│
├── ai/                             notebooks, training, validation, reports
│   ├── datasets/
│   │   ├── raw/                    harvest from the field runs
│   │   └── processed/              windowed features, never committed if large
│   ├── notebooks/
│   │   ├── 01_data_extraction.py   DB → windows
│   │   ├── 02_training.py          MODEL 2 baseline
│   │   ├── 03_synthetic_augmentation.py   in-domain, 50k+ windows (Tier 3)
│   │   ├── 04_external_validation.py      out-of-domain benchmark (Tier 1)
│   │   ├── 05_calibrate_kinematic.py      Fukuzono α, A (Tier 2)
│   │   ├── 06_train_pattern.py    MODEL 1, retrained for the v6.0 row schema
│   │   └── 07_train_novelty.py     ☆ RESERVED — only opened if the double-AI gate passes
│   ├── README.md                   which artifact came from which notebook, and when
│   ├── export/                     artifacts copied into backend/models/
│   └── reports/
│       ├── external_validation_report.txt
│       ├── cascade_metrics.md
│       └── DOUBLE_AI_GATE.md       ★ the decision record for the novelty layer (§5)
│
├── frontend/                       the operator dashboard (PWA)
│   ├── index.html                  ★ THE BUILT BUNDLE — generated by tools/build-single.js
│   ├── gis-map.js                  SVG tunnel map, zones, link states —
│   │                               ★ NOT CREATED: delivered as src/js/14-gis.js (Rev 6.0.9)
│   ├── blackbox.js                 ★ chips, chunk ledger, force-sync round trip —
│   │                               ★ NOT CREATED: delivered as src/js/13-blackbox-cloud.js (Rev 6.0.6)
│   ├── manifest.webmanifest
│   ├── service-worker.js
│   ├── README.md                   how to build and check the bundle
│   ├── package.json                the build scripts
│   ├── src/                        ★ the editable sources; index.html is built FROM these
│   │   ├── index.html
│   │   ├── css/                    core · decks · deck-tabs · access
│   │   └── js/                     00-config … 13-blackbox-cloud · 14-gis
│   ├── mock/                       standalone dev server for the deck shells
│   ├── docs/                       API.md · ARCHITECTURE.md
│   ├── pwa/                        icons
│   └── tools/
│       ├── check_claims.py         the CR-01…CR-14 acceptance checker (14 rules)
│       ├── build-single.js         src/ → root index.html, one inlined file
│       ├── check.js                the bundle's own assertions, run after every build
│       └── recover_shell.py        rebuilds a damaged index.html shell
│
└── infra/
    ├── docker-compose.yml          local Postgres, optional
    ├── sql/
    │   ├── cloud_schema.sql        the single cloud table
    │   └── ingest_function.sql     COMMIT-then-200, idempotent on batch_id
    ├── systemd/                    service units for the laptop
    └── runbook.md                  incident procedures, including gate refusal
```

---

## 3. The five rules this layout encodes

1. **`app.py` shrinks.** It is a bootstrap file by Phase 12; everything else moved out.
2. **Every file has one reason to change.** The tunnel map changes → `dijkstra.py`. The wire format
   changes → `blackbox/protocol.py`. Nothing else moves.
3. **`blackbox/` is a peer, not a helper.** It owns a store, a wire contract and the only code
   authorised to destroy data — so it gets its own package.
4. **The gate is one small file.** It should be readable in one sitting and testable exhaustively.
5. **Mocks live with the code they mock.** `sim/hub_sim.py` and `sim/cloud_mock.py` make phases 9–12
   buildable with no hardware and no network.

---

## 4. Where each phase writes

Two clocks run in parallel and the firmware column is where they meet: the **backend phase**
says when a server feature lands, the **hardware phase** says when the wiring it talks to
exists. Firmware is *written* under the hardware phase and becomes *useful* at the backend
phase named here. Paths are relative to the repository root (`backend/`, `firmware/`).

| Phase | Backend & AI code | Firmware code | Hardware phase |
|---|---|---|---|
| **0** · ✅ **DONE** · ground truth, contracts, replay | `backend/config.py` · `contracts.py` · `assets/tunnel_map.json` · `assets/site_meta.json` · `tools/record.py` · `replay.py` · `check_numbers.py` · `check_contracts.py` · `tests/test_contracts.py` · **`backend/persistence/db.py`** (`init_schema`, `migrate`, `get_conn`, `release`) · **`backend/persistence/schema.sql`** · **migrations 001 + 005** · **`run.sh`** (cold start) · **`requirements.txt`** | `firmware/platformio.ini` — all envs created as stubs | **H0** |
| **1** · B1 4-node state + ingestion | `backend/ingestion/serial_reader.py` · `ring.py` · `simulator.py` · `backend/state.py` · `contracts.py` node registry | `firmware/src/node/main.cpp` · `firmware/src/common/config.h` · `pins.h` · `leds.h` · `firmware/src/node/mpu6050.cpp` · `sw420.cpp` · `firmware/lib/espnow_packet/` | **H1 · H3** |
| **2** · B7 health, watchdog, degraded mode | `backend/engine/health.py` (+ main-hub health) · `tests/test_health.py` | — *(the hub seq-gap watchdog lands with H3)* | **H3** |
| **3** · B2 two-stage crack engine | `backend/engine/crack.py` · `tests/test_crack.py` | `firmware/src/node/hx711.cpp` · `firmware/src/node/pots.cpp` (macro channel) | **H2** |
| **4** · B8 elongation, settlement, zone risk | `backend/engine/geomech.py` · `tests/test_geomech.py` | — | — |
| **5** · ★ **THE DOUBLE AI** · **B9 · B10 · B11** | `backend/engine/features.py` · `kinematic.py` · `pattern.py` · `ttf.py` · `cascade.py` · `backend/models/` · `ai/notebooks/06_*` · `tests/test_features.py` · `test_kinematic.py` · `test_cascade_safety.py` · `backend/tools/make_counterfactuals.py` · `eval_cascade.py` | — *(advisory only; runs server-side)* | — |
| **6** · B3 severity, progression, confidence | `backend/engine/severity.py` | — | — |
| **7** · B5 Dijkstra + `/api/map` | `backend/routing/dijkstra.py` · `backend/api/routes_map.py` · `map_payload.py` · `tests/test_dijkstra.py` | — | — |
| **8** · B4 persistent alert dispatcher | `backend/alerts/dispatcher.py` · `channels.py` · `templates.py` · `persistence/schema.sql` alert tables · `backend/api/routes_alerts.py` · `tests/test_alerts.py` · `backend/tools/led_test.py` | `firmware/src/level_hub/main.cpp` · `ring_port.cpp` · `rc522.cpp` · `strips.cpp` · `firmware/lib/ws2812b/` · the **`#CMD` parser** — `led` · `siren` · `advisory` · `pattern` | **H6 · H7** |
| **9** · B6 cloud sync outbox + commit contract | `backend/persistence/rollup.py` · `outbox.py` · `sync.py` · `cloud_client.py` · `backend/sim/cloud_mock.py` · `infra/sql/cloud_schema.sql` · `infra/sql/ingest_function.sql` · `tests/test_outbox.py` · `backend/tests/test_sync_logic.py` · `backend/tools/verify_dual_db_sql.py` · **`tools/central_stub.py` DELETED** | `firmware/lib/ring/` — main-hub link framing, heartbeats | **H5** |
| **10** · blackbox ingest + the five-condition gate · **B13 · B14** | `backend/blackbox/protocol.py` · `reader.py` · `ingest.py` · `gate.py` · `store.py` · `schema.sql` · `backend/api/routes_blackbox.py` · `backend/sim/hub_sim.py` · `tests/test_protocol.py` · `test_gate.py` · `test_ingest.py` | **`firmware/src/main_hub/timekeeper.cpp`** ★ Core-1 master timekeeper (owns `row_counter` + `chunk_id`) · **`archivist.cpp`** ★ Core 0 queue drain · CSV append · 5 s flush · rotate · **`blackbox.cpp`** ★ SD mount · DROP counting · FIFO eviction · REPLAY serving | **H4** |
| **11** · reconciliation + REPLAY recovery · **B15** | `backend/blackbox/reconciliation.py` · `recovery.py` · `tests/test_recovery.py` | **`firmware/src/main_hub/protocol_out.cpp`** ★ NDJSON up · `ACK_DELETE` / `REPLAY` / `STATE?` down | — |
| **12** · observability, drop accounting, failure rehearsal · **B12 · B16** | `backend/tools/failure_suite.py` · `backend/metrics.py` · `backend/api/routes_cloud.py` · `backend/alerts/blackbox_alerts.py` · `frontend/src/js/13-blackbox-cloud.js` · `infra/runbook.md` · `backend/tools/preflight.py` · the **`app.py` split** — `backend/api/routes_telemetry.py` · `routes_ai.py` · `routes_admin.py` · `backend/tests/test_api.py` | — *(the rehearsal exercises H3's `common/power.h` brownout handler)* | **H9** |
| **13** *(optional, gated)* | `backend/engine/novelty.py` · `ai/notebooks/07_train_novelty.py` · `backend/models/novelty_v1.joblib` · `tests/test_novelty_isolation.py` | — | — |

**Totals:** backend + AI ≈ **37.5 days**; minimum demoable path = phases 0–11 ≈ **33.5 days**.
Phase 13 starts only after the double AI passes its gate — nothing depends on it (§5).

### 4.1 Firmware files with no backend phase

Three firmware files are pure hardware. They have no server-side counterpart, so they belong
to the hardware phases alone and are deliberately absent from the table above:

| File | Belongs to | Why no backend phase |
|---|---|---|
| `firmware/src/main_hub/main.cpp` | **H4** | Core-1 ESP-NOW ingest + ring relay + serial out — the link the backend merely reads |
| `firmware/src/main_hub/serial_surface.cpp` | **H4** | the framed USB link; the backend sees only the bytes it produces |
| `firmware/src/common/power.h` | **H3** | brownout detector — exercised by the phase-12 rehearsal, authored in H3 |

### 4.2 Firmware coverage check

Every file in the §2 `firmware/` subtree, and the phase that produces it. This table exists so
the tree and the phases cannot drift apart again — if you add a firmware file to §2, add a row here.

| `firmware/` file | Written in |
|---|---|
| `platformio.ini` | **P0** (stubs) → **H1** onward (real envs) |
| `lib/espnow_packet/` | **P1** |
| `lib/ring/` | **P9** (begins) · **P10** (hub side) |
| `lib/ws2812b/` | **P8** |
| `common/config.h` · `common/pins.h` · `common/leds.h` | **P1** |
| `common/power.h` | **H3** |
| `node/main.cpp` · `node/mpu6050.cpp` · `node/sw420.cpp` | **P1** |
| `node/hx711.cpp` · `node/pots.cpp` | **P3** |
| `level_hub/main.cpp` · `ring_port.cpp` · `rc522.cpp` · `strips.cpp` | **P8** |
| `main_hub/main.cpp` · `serial_surface.cpp` | **H4** (no backend phase — see §4.1) |
| `main_hub/timekeeper.cpp` · `archivist.cpp` · `blackbox.cpp` | **P10** |
| `main_hub/protocol_out.cpp` | **P11** |

### 4.3 PlatformIO environment names — resolved

`Neural_Strata_Hardware_Build_Phases.md` §H0.5 names four envs (`env:node`, `env:hub`,
`env:lvl1`, `env:lvl2`). **This document wins** (it is the tree of record), so the envs are:

```
env:main_hub    env:level_hub    env:node
```

with the level distinguished by a build flag — `-D LEVEL=1` / `-D LEVEL=2` — exactly as §H6.6
already specifies. One env, two boards.

### 4.4 The database — what exists, what changes, and in which phase

The database is **two instances of one dialect**, not two technologies: a **local PostgreSQL on the
laptop** (the edge, and the source of truth) and a **free hosted PostgreSQL** — Neon by default,
Supabase equally fine — as a *derived reporting replica*. Design and setup live in
`Neural_Strata_Dual_Database.md`; this section records only where the code and the changes sit.

| Tier | Instance | What it holds | Rule |
|---|---|---|---|
| **Edge** | local Postgres, already running | raw telemetry · alerts · ledger · rollups | **the source of truth** — the dashboard reads only this |
| **Cloud** | Neon / Supabase, `NS_CLOUD_DB_URL` | 5 tables + 1 view: `node_hourly` · `alerts_history` · `drill_runs` · `model_metrics` · `sync_log` | a replica; empty ⇒ local-only mode, **zero code paths change** |

#### The migration ledger — the changes to the **existing** database

The database is already running and holds rehearsal data, so **every migration is additive**:
`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`, **no drops, no rewrites**. Existing rows
are untouched. Take a `pg_dump` before applying any of them (`Neural_Strata_Dual_Database.md` §C).

| Migration | Phase | Changes | Kind |
|---|---|---|---|
| **001** | **0** | `sensor_telemetry` (+2 indexes) · `rfid_evacuation_log` · `schema_migrations` | creates what is referenced today |
| **002** | **1** | `sensor_telemetry` gains `vib` · `micro` · `macro` · `rssi` · `batt` · `level` | `ADD COLUMN IF NOT EXISTS` |
| **003** | **8** | `alerts_history` + the outbox and delivery-ledger tables | new tables |
| **004** | — | *optional, and the plan's preferred answer is **not** to use it* — reuse `alerts_history` with `severity = 'INFRASTRUCTURE'` | nothing to apply |
| **005** | **0** | `node_hourly` (+ `ix_hourly_pending`) · `alerts_history` gains `synced_at` · `sync_attempts` · `site_id` | new table + watermarks |

> **Why a `synced_at` watermark instead of a separate outbox table for bulk rows:** an outbox that
> *copies* rows duplicates storage and can drift from the source. A nullable timestamp on the row
> itself is atomic, idempotent, costs one column, and cannot go out of sync. The generic `outbox`
> table stays for discrete events; **bulk rows use watermarks.**

#### Database artefacts by phase

| Phase | Database work |
|---|---|
| **0** | `persistence/db.py` (`init_schema`, `migrate`, `get_conn`, `release`, `insert_telemetry`, `insert_rfid`) · `persistence/schema.sql` · migrations **001 + 005** · `run.sh` cold start · `schema_migrations` bookkeeping |
| **1** | migration **002** (`ADD COLUMN` on `sensor_telemetry`) |
| **8** | migration **003** — `alerts_history`, the outbox table, the delivery ledger, including `disruption_label` / `disruption_index` so every historical alert records *why* it was classified as it was |
| **9** | `persistence/rollup.py` (hourly rollup + watermark upsert) · `sync.py` → Neon/Supabase · `infra/sql/cloud_schema.sql` (5 tables + 1 view) · `infra/sql/ingest_function.sql` (COMMIT-then-200, idempotent on `batch_id`) · `infra/docker-compose.yml` (local Postgres) · `tools/central_stub.py` **deleted** |
| **10** | migration **005**'s tables are already live from Phase 0; `/api/sync/status` joins `/api/metrics`; `blackbox/schema.sql` is a **separate** store and never touches Postgres |

#### Two things that must stay true

1. **The cloud is never on the critical path.** `NS_CLOUD_DB_URL` empty must run local-only with no
   errors — that is an acceptance test, not a preference (SRS §NFR-2).
2. **One schema file, no dialect translation.** Both tiers are real Postgres and are driven from
   `DATABASE_URL`-style env vars, so switching provider is one line in `.env`.

#### Two documented discrepancies, resolved here

| Source | Says | This document |
|---|---|---|
| `Backend_Build_Phases.md` §0.3 / §0.5 | run "migrations **001 + 002**" in Phase 0 | **001** is Phase 0; **002** is Phase 1 per §A.3.7's own table. Phase 0 applies 001; Phase 1 applies 002 |
| `Backend_Build_Phases.md` §A.3.7 vs `Dual_Database.md` §F.2 | migration 005 appears only in the database document | 005 is **Phase 0** work, per `Dual_Database.md` §H.6, and is listed as such above |

---

### 4.5 Feature coverage — all sixteen features land in a phase

The pre-flight audit found `B9` and `B10` described in `Backend_Plan.md` §4 but assigned to **no**
phase, and `B12`–`B16` missing from these phase rows. Both are now fixed above. This table is the
proof, and the check to re-run whenever the matrix changes.

| Feature | Phase | Where it is built |
|---|---|---|
| **B1** 4-node multi-level state machine | 1 | `ingestion/` · `state.py` |
| **B2** two-stage crack anomaly engine | 3 | `engine/crack.py` |
| **B3** severity & progression estimator | 6 | `engine/severity.py` |
| **B4** persistent alert dispatcher | 8 | `alerts/` · `persistence/schema.sql` |
| **B5** evacuation routing engine | 7 | `routing/dijkstra.py` |
| **B6** cloud sync outbox | 9 | `persistence/outbox.py` · `sync.py` |
| **B7** node & hub health watchdog | 2 | `engine/health.py` |
| **B8** node-pair relative elongation Δ | 4 | `engine/geomech.py` |
| **B9** in-domain synthetic augmentation *(Tier 3)* | **5** | `ai/notebooks/03_synthetic_augmentation.py` → 50k+ windows |
| **B10** real-world event validation harness *(Tier 1)* | **5** | `ai/notebooks/04_external_validation.py` · `ai/reports/external_validation_report.txt` |
| **B11** empirical kinematic fallback *(Tier 2)* | 5 | `engine/kinematic.py` · `ai/notebooks/05_calibrate_kinematic.py` |
| **B12** gated transfer learning *(Tier 4)* | **12** | the transfer branch — admitted only if the gate passes; **no notebook exists in the tree, by design** |
| **B13** underground blackbox ingest | **10** | `blackbox/ingest.py` · firmware `archivist.cpp` · `blackbox.cpp` |
| **B14** five-condition delete gate | **10** | `blackbox/gate.py` (pure function) |
| **B15** reconciliation & REPLAY recovery | **11** | `blackbox/reconciliation.py` · `recovery.py` |
| **B16** blackbox observability | **12** | `tools/failure_suite.py` · `metrics.py` · `alerts/blackbox_alerts.py` |

**16 of 16 assigned.** `B12`'s absence of a notebook is deliberate: the transfer branch is built only
after `ai/reports/DOUBLE_AI_GATE.md` records a pass, which is the same gate that opens `engine/novelty.py` (§5).

---

### 4.6 Already built

Phase 10 is roughly **40 % done** before it starts: `protocol.py`, `schema.sql`, `store.py`,
`gate.py`, `ingest.py`, `sim/hub_sim.py` and **29 passing assertions** already exist.

Phase 12's `frontend/blackbox.js` is also largely present — the blackbox panel (chips, chunk
ledger, force-sync round-trip, the five disaster drills) already ships in the delivered
`NeuralStrata/` build. That task is wiring the existing panel to `/api/extended`, not writing
it from scratch.

---

### 4.7 Naming drift — old names that must not be followed

`Neural_Strata_Backend_Build_Phases.md` predates v6.0 and cites file names that the tree has since
replaced. It is still the best *working* document for the backend phases, so read it — but **create
the file under the v6.0 name, not the old one.** This table is the translation.

| Old name (Build Phases doc) | v6.0 name (this tree) | Where |
|---|---|---|
| `05_set_kinematic_constants.py` | `ai/notebooks/05_calibrate_kinematic.py` | Tier-2 calibration |
| `06_train_pattern.py` | `ai/notebooks/06_train_pattern_model.py` | Model 1 retrain, v6.0 row schema |
| `07_transfer_experiment.py` | *(none — B12, built only after the gate)* | see §4.5 |
| `training_report.txt` | `ai/reports/cascade_metrics.md` | cascade metrics |
| `pattern_training_report.txt` | `ai/reports/cascade_metrics.md` | same report, merged |
| `transfer_experiment.txt` | `ai/reports/DOUBLE_AI_GATE.md` | the gate decision record |
| `ttf_model_transfer.js` | `backend/models/*.json` (JSON, not JS) | model artifacts |
| `hub.cpp` · `node.cpp` · `brownout.cpp` | `main_hub/main.cpp` · `node/main.cpp` · `common/power.h` | firmware split |
| `dashboard_data.py` | `api/routes_blackbox.py` + `frontend/blackbox.js` | superseded, see §1 |
| `map.h` | `firmware/src/common/pins.h` + the topology in `backend/assets/tunnel_map.json` | the map is data, not firmware |

**Rule:** if a document names a file that is not in §2, do not create it under that name. Find it in
§4.5 (features), §4.2 (firmware) or this table first — or raise it, because it means a document and
the tree have drifted again.

---

**Phase 0 — complete (built 2026-09-22).** The v4.4 server was ported by script
(`backend/tools/port_app_phase0.py`, which fails loudly rather than half-applying),
and the extraction is proven to be behaviour-preserving:

| Evidence | Result |
|---|---|
| `pytest tests/` | **87 passed** |
| `tools/check_contracts.py` | **21 checks pass** |
| `tools/check_numbers.py` | **17 claims agree** with `model_meta.json` |
| `tools/snapshot_api.py --diff` | every endpoint keeps its frozen shape vs the pre-extraction capture |
| migrations 001 + 005 | applied to the live database, idempotent on re-run, 4 tables |
| `record.py` → `replay.py` @10× | the replayed pipeline reproduces the recorded run exactly (node_2 peak 100.000 mm) |
| `./run.sh --check` | all 7 cold-start steps green |
| **H0 acceptance** — `pio run -e node -e level_hub -e main_hub` | **3 succeeded**, 3 distinct binaries. H0.5's test is "building a stub for each board proves the toolchain sees all 7"; an env with no source file proves nothing, so each env now has a stub source. |
| record → replay @10×, sampled at 20 Hz | the replay reproduces **both** recorded states in order — the WARNING on node_1 and the CRITICAL on node_2 — and writes 195 rows per node to PostgreSQL |

#### The §4 row, item by item

All sixteen code items in the Phase 0 row exist and are exercised:
`config.py` · `contracts.py` · `assets/{tunnel_map,site_meta}.json` ·
`tools/{record,replay,check_numbers,check_contracts}.py` (each with a `main()`) ·
`tests/test_contracts.py` · `persistence/db.py` (all four named functions:
`init_schema`, `migrate`, `get_conn`, `release`) · `persistence/schema.sql` ·
migrations **001 + 005** · `run.sh` · `requirements.txt` ·
`firmware/platformio.ini` (all envs as stubs) · **H0**.

**Three environment names, not five.** H0.5 still says `env:node` · `env:hub` ·
`env:lvl1` · `env:lvl2`; §4.3 resolves this to **`env:main_hub` · `env:level_hub` ·
`env:node`**, with the board instance selected by `-DLEVEL=1/2` and `-DNODE_ID=1..4`.
One environment per board class, so a board class has one firmware to keep in sync
instead of two.

#### Two corrections to the Rev 6.0.4 record

1. **"Three stand-alone test runs PASS" was wrong.** The four relocated black-box
   test files are pytest-style — they define functions and have **no `__main__`
   runner**, so `python3 tests/test_x.py` exits 0 without running a single
   assertion. Every such claim in Rev 6.0.4 is withdrawn; only `pytest tests/`
   runs them (121 tests).
2. That hollow check hid a real defect: `blackbox/reader.py` still imported the
   moved simulator from its old `blackbox/sim/` path *inside a generator*, so the
   gateway came up, reported `mode=SIM`, and produced **zero rows** — silently,
   because the exception died in the reader thread. Relocating a file means
   grepping for every reference to it, not just fixing the imports the linter
   sees. Caught by `tests/test_gateway_contract.py`, which is exactly the file
   that exists to notice a silent feed failure.


Files Phase 0 created that this tree did not previously list: `tests/conftest.py`,
`tests/shape.py`, `tests/fixtures/`, `tools/snapshot_api.py`,
`tools/port_app_phase0.py`, `tools/fix_numbers_phase0.py`, and root `recordings/`.

## 5. The novelty layer — reserved, conditional, and isolated

**The instruction has changed.** It was previously *"double AI only, no third layer"* — you have now
said the novelty layer goes in **if the double AI layer is successful**. Recorded as a conditional
reversal, not a cancellation of the earlier decision.

**What "reserved" means here — three things, all cheap:**

1. **A stub exists, and nothing imports it.** `engine/novelty.py` exposes one function that returns
   `None` while disabled. `cascade.py` treats that as "no third opinion" and continues exactly as
   today. **Removing the file cannot break anything**, which is the property that makes a conditional
   feature safe to promise.
2. **The switch is a flag, not a refactor.** `NS_NOVELTY_ENABLED=false` in `.env.example`;
   `models/novelty_meta.json` ships as `{"enabled": false}`; `models/MANIFEST.json` records the state
   so an artifact set is self-describing.
3. **An isolation test proves it.** `tests/test_novelty_isolation.py` runs the full pipeline with the
   flag off and with the artifact file deliberately absent, and asserts byte-identical output to the
   run without the module present.

**The gate — the layer is admitted only if all four are true:**

| # | Condition | Where it is recorded |
|---|---|---|
| 1 | M2 held-out critical MAE ≤ **0.10 s** on the canonical run | `models/model_meta.json` |
| 2 | `test_cascade_safety.py` green — the cascade still cannot block or suppress an alert | CI |
| 3 | The novelty signal adds **measurable** early warning on the replay corpus (lead time, not just a score) | `ai/reports/cascade_metrics.md` |
| 4 | It stays **advisory** — never a Pass/Block gate, never on the critical path | `engine/cascade.py` review |

If any of the four fails, the slot stays empty and the shipped system is exactly the double-AI system
it is today. **The decision is written down in `ai/reports/DOUBLE_AI_GATE.md` with the numbers that
drove it** — so it is a measured decision, not a preference.

**Cost, honestly:** reserving the slot now is **≈0.5 h** (stub, flag, isolation test, MANIFEST key).
Building it later is **≈2 days** as an optional Phase 13, after Phase 12, and it never delays the
demo path.

**One thing it must not become:** a third model whose output the alarm logic waits on. The cascade
firewall already states the rule for two models — *advisory only, can never block, gate or suppress
an alert* — and the novelty layer inherits it unchanged.
