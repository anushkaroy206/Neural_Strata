# Neural Strata — Backend Plan v5.0

**Underground Blackbox & Cloud Sync, from scratch**

Version 5.0 · 20 Sep 2026 · supersedes the v4.4 backend plan
Companion document: `Neural_Strata_Blackbox_Review.md` (the corrections applied here)

---

## 1. What this backend is

A **zero-loss telemetry backend** for an underground mine safety system, in which the
machine that records the data is *not* the machine that decides to delete it, and the
irreversible delete only happens when five independent conditions are all true.

Three sentences:

1. The **Central Hub** underground writes every sensor row twice — out over the USB link and
   into an SD-card blackbox — with one `chunk_id` stamped on both copies by a single CPU core.
2. The **laptop backend** stores rows in a truth store, queues them in an outbox, and syncs to
   the cloud over HTTPS/GZIP with an HMAC signature and an idempotency key.
3. The laptop sends `ACK_DELETE` **only** when the chunk is closed, count-matched, undropped,
   fully synced and past a safety margin. Otherwise the file stays on the card and the
   dashboard shows **why**.

**What is new versus the previous backend:** the blackbox ingest path, the outbox with a real
delivery contract, the delete gate, the reconciliation handshake, and the replay feature.
**What is deliberately unchanged:** the HTTP surface. v5 serves exactly the endpoints v4.4
served, so the already-issued frontend change request stays valid — v5 *adds*, it does not
replace.

---

## 2. Architecture

```
   ┌──────────────────────── UNDERGROUND ────────────────────────────┐
   │                                                                  │
   │   4 sensor nodes ── ESP-NOW 2.4 GHz ──┐                          │
   │                                        ▼                         │
   │                          ┌───────────────────────────┐           │
   │   L1 ── ring link ──▶    │   CENTRAL (MAIN) HUB      │           │
   │   L2 ── ring link ──▶    │   ESP32 · Core 1 · Core 0 │           │
   │                          └───────┬───────────┬───────┘           │
   │                                  │           │                   │
   │                        USB serial│           │ VSPI (SPI)        │
   │                     (data only)  │           ▼                   │
   │                                  │    ┌──────────────┐           │
   │                                  │    │ MicroSD      │           │
   │                                  │    │ BLACKBOX     │           │
   │                                  │    │ chunk_0042.. │           │
   │                                  │    └──────────────┘           │
   │   LOCAL 5 V ─── powers the hub ──┘          (never unplugged)     │
   └──────────────────────────────────┬───────────────────────────────┘
                                      │  ← this is the cable that gets cut
   ┌──────────────────────────────────▼───────────────────────────────┐
   │                          SURFACE LAPTOP                           │
   │                                                                   │
   │   ingest ──▶ TRUTH STORE ──▶ cloud_outbox ──▶ sync daemon         │
   │   (5)         (SQLite/PG)      (PENDING)        │  gzip + HMAC    │
   │      │                              ▲           │  idempotency    │
   │      │                              │           ▼                 │
   │      │                        ACK GATE ◀──── 200 OK               │
   │      │                      (5 conditions)      │                 │
   │      ▼                              │           ▼                 │
   │   AI cascade (M1+M2)                └────▶ NEON POSTGRES (cloud)  │
   │   alerts · dashboard · ledger                                     │
   └───────────────────────────────────────────────────────────────────┘
```

**The four planes, and why they are separated:**

| Plane | Job | Failure isolation |
|---|---|---|
| **Ingest** | one line at a time off the wire | a malformed line is counted, never fatal |
| **Truth store** | the record of what happened | independent of the cloud and of the hub |
| **Outbox / sync** | get rows to the cloud, exactly once | retries freely; cannot corrupt the truth store |
| **Gate** | decide whether the last copy may be destroyed | the only component allowed to authorise deletion |

---

## 3. Module map

```
neuralstrata-backend-v5/
  protocol.py          frozen hub ↔ laptop wire format (both directions)     [P0 ✓]
  schema.sql           truth store · chunks · outbox · batches · hub status  [P0 ✓]
  store.py             the only write path for telemetry                     [P0 ✓]
  gate.py              the delete gate — pure function, five conditions      [P1 ✓]
  ingest.py            line loop + periodic ACK sweep                        [P1 ✓]
  sim/hub_sim.py       fake hub emitting the real protocol                   [P1 ✓]
  tests/               29 assertions                                          [P1 ✓]
  ── to build ──────────────────────────────────────────────────────────────
  serial_link.py       pyserial reader thread, reconnect, STATE? handshake   [P2]
  sync.py              outbox claim/lease, gzip, HMAC, retry, force_sync     [P3]
  cloud_client.py      HTTP transport, timeouts tuned for Neon cold start    [P3]
  api.py               FastAPI/Flask app: v4.4 surface + v5 additions        [P4]
  replay.py            REPLAY command + back-fill orchestration              [P4]
  ai/                  M1 RandomForest + M2 XGBoost, advisory cascade        [P5]
  alerts/              Telegram · local siren/LED · email audit              [P6]
  dashboard_data.py    the chips, the chunk ledger, the round-trip trace     [P7]
  failure_suite.py     scripted disaster rehearsal                           [P8]
```

---

## 4. The frozen serial protocol

**One character demuxes everything:** a telemetry line starts with `{`, a control line never does.

| Line | Direction | Meaning |
|---|---|---|
| `{"chunk_id":42,"seq":450,"ts_ms":142850,"node":2,"level":1,"tilt":0.14,"disp_mm":5.42,"accel":0.035,"vib":1,"micro":12.5,"macro":33.0,"flags":0}` | hub → laptop | one row, stamped by Core 1 |
| `CHUNK_OPEN:0043` | hub → laptop | Core 1 advanced the counter |
| `CHUNK_CLOSE:0042,1000,0` | hub → laptop | `<chunk>,<rows>,<dropped>` — the hub's own counts |
| `CHUNK_STATE:0042,450,0` | hub → laptop | reply to `STATE?` |
| `CHUNK_DONE:0042,1000,0` | hub → laptop | a closed chunk not yet acknowledged |
| `DROP:0042,3` | hub → laptop | queue overflow — 3 rows lost |
| `BLACKBOX:OK,3712` · `NOSD` · `FULL` · `FAULT` | hub → laptop | card health, free MB |
| `ACK_DELETE:0042` | laptop → hub | **the only command that destroys data** |
| `REPLAY:0042:0057` | laptop → hub | re-stream those chunks |
| `STATE?` · `PING` | laptop → hub | reconcile · liveness |

**Rules:** the laptop never echoes a line back. Every telemetry row carries `chunk_id` **and**
`seq` — those two fields are what make ingest and replay idempotent. Changing this file requires
bumping `PROTOCOL_VERSION`.

---

## 5. Data model

| Table | Holds | Key constraint |
|---|---|---|
| `sensor_telemetry` | every row that arrived, live or replayed | **`UNIQUE(chunk_id, seq)`** |
| `blackbox_chunks` | per-chunk state: open/closed, expected, received, dropped, acked | `chunk_id` |
| `cloud_outbox` | one queue row per telemetry row | **`UNIQUE(chunk_id, seq)`** |
| `cloud_batches` | per-upload audit: rows, raw vs gzipped bytes, HTTP status | `batch_id` |
| `hub_status` | hub health, free MB, **`dropped_total`** | singleton |

**The two unique constraints are the whole zero-loss design.** A replay of chunk 42 a hundred
times inserts nothing and re-queues nothing; a retry of a batch cannot double-insert; a
half-delivered chunk cannot be mistaken for a complete one.

**The SD-row rule:** the hub writes those same twelve fields, in the same order, as CSV on the
card. Replay is therefore a straight insert — no translation layer, no mapping table, nothing
that can drift out of sync with the schema.

---

## 6. The delete gate — five conditions

`ACK_DELETE` is **irreversible and destroys the last copy of data that exists nowhere else.**
The gate is a pure function with an explicit reason for every refusal.

| # | Condition | If it fails |
|---|---|---|
| 1 | chunk is **CLOSED** | *"chunk is still open — the hub is writing to it"* |
| 2 | `received_rows == expected_rows` | *"12 rows would be lost"* |
| 3 | `dropped_rows == 0` | *"the SD copy is the only record of them — keep the file"* |
| 4 | `pending == 0` **and** the table's own `COUNT(*)` agrees | *"350 rows not yet confirmed by the cloud"* / *"internal inconsistency"* |
| 5 | closed ≥ **60 s** ago | *"closed 12s ago, safety margin is 60s"* |

Plus: an already-acknowledged chunk is never acked twice, and a chunk whose `expected_rows` is
unknown (no `CHUNK_CLOSE` ever seen) is **held, not released** — absence of a count is a
constraint, not a licence.

**Every reason is displayed on the dashboard.** A gate nobody can see is a gate somebody will
bypass.

---

## 7. The cloud contract

| Rule | Why |
|---|---|
| **`200 OK` means `COMMIT`, not "accepted"** | otherwise the gate authorises deleting data that can still be lost |
| **Idempotency key per batch** — the cloud returns 200 for a duplicate instead of inserting twice | the outbox *will* retry; retries must be free |
| **HMAC-SHA256 over the body + a monotonically increasing batch sequence + timestamp** | a bare HMAC is replayable |
| **Timeouts tuned for a cold free-tier database** (Neon auto-suspends) | a 5 s timeout fails every first upload and the dashboard sits at `PENDING` while nothing is wrong |
| **Lease, not lock** — claim rows with `claimed_at` + `claim_id`, release on failure | adding a second uploader must not double-send |
| **Never mark `SENT` on anything but an explicit 200** | and never report a count after a failed COMMIT |

**GZIP is measured, not asserted.** `cloud_batches` stores `bytes_raw`, `bytes_gzip` and the HTTP
status per batch, and the dashboard shows the real ratio.

---

## 8. HTTP surface

**Frozen (preserved 1:1 from v4.4 — the frontend change request depends on these):**

`GET /api/telemetry` · `GET /api/extended` · `GET /api/history` · `GET /api/ai` ·
`POST /api/sim/trigger` · `POST /api/sim/reset` · `POST /api/ack`

**New in v5:**

| Endpoint | Returns |
|---|---|
| `GET /api/blackbox/status` | `{state, open_chunk, rows_in_open, free_mb, dropped_total, last_flush_ms}` |
| `GET /api/blackbox/chunks` | the ledger: chunk, rows, state, synced, dropped, acked, **hold reason** |
| `POST /api/blackbox/recover` | sends `REPLAY:first:last`; returns how many rows were back-filled |
| `GET /api/cloud/status` | `{pending, sent_24h, bytes_raw, bytes_gzip, last_error}` |
| `POST /api/cloud/force_sync` | runs a sync pass now; returns the **round-trip trace** |
| `GET /api/cloud/trace/{batch_id}` | `gzip → upload → 200 → ACK_DELETE → unlinked`, with timings |

---

## 9. Failure matrix

| Scenario | System response | Guarantee |
|---|---|---|
| **Surface cable cut** | Hub keeps running on local power; Core 0 keeps writing; dashboard shows the blackbox filling | every row is on the card; the live view is blind, not the record |
| **Hub queue overflows** | `DROP` counter increments; the chunk is **never deleted**; the chip shows `DROPPED: n` | loss is **counted and visible**, never silent |
| **Laptop restarts mid-chunk** | On reconnect the laptop sends `STATE?`; the hub answers `CHUNK_STATE` / `CHUNK_DONE`; counts reconcile monotonically | no chunk is ever closed or completed by assumption |
| **Rows lost in transit** | `received < expected` → gate refuses, dashboard says *"n rows would be lost"* | the card keeps the only copy of the missing rows |
| **Cloud down / free-tier asleep** | Outbox holds; no ACK is sent; nothing is deleted | both laptop and card hold the data |
| **Cloud returns 500 mid-batch** | Lease expires, rows return to `PENDING`, retry with backoff; idempotency key prevents duplicates | no loss, no duplication |
| **Sudden power loss underground** | Append + flush every 5 s; brownout detector sets a flag and stops ingest | ≤ 5 s of the **open** chunk at risk; all closed chunks intact |
| **SD card removed** | `BLACKBOX:NOSD`; the hub **refuses to acknowledge anything**; chip flashes red | nothing is deleted without a card present |
| **Card nearly full** | Oldest chunk unlinked by FIFO — **only chunks the gate already released** | the newest pre-collapse data always has room |
| **Cloud 200 but never committed** | Detected by the next batch's audit; the chunk is recent and re-publishable | bounded, auditable, recoverable |

---

## 10. Phases

Each phase ends in a **check that must pass** before the next one starts. Times are one developer.

### P0 — Skeleton and contracts · **1.5 h · COMPLETE**

**Goal:** freeze the interfaces before writing anything that depends on them.
**Deliverable:** `protocol.py`, `schema.sql`, `store.py`, package layout.
**Acceptance:** schema creates clean; protocol round-trips; serialisers emit zero-padded commands.
**Done:** ✓

### P1 — Ingest, outbox, and the gate · **3.5 h · COMPLETE**

**Goal:** rows land, get queued, and the gate refuses everything it should.
**Deliverable:** `ingest.py`, `gate.py`, `sim/hub_sim.py`, `tests/` (29 assertions).
**Acceptance:**
- 480 rows over a 3-chunk simulated stream → 480 telemetry rows, 480 outbox rows, 0 unknown ✓
- replaying the same stream twice changes **nothing** ✓
- every gate refusal path has a test, including the *"12 rows would be lost"* case the addendum's
  single-condition gate would have missed ✓
- malformed wire data is counted, never fatal ✓
- `CHUNK_STATE` reconciliation cannot un-close a chunk ✓
**Done:** ✓ — `python3 tests/run_all.py` → **29 passed, 0 failed**

### P2 — The real serial link · **2.5 h**

**Goal:** replace the simulator with the physical hub.
**Deliverable:** `serial_link.py` — reader thread, line framing, reconnect with backoff,
`STATE?` on every reconnect, writer queue for `ACK_DELETE`/`REPLAY`.
**Acceptance:** kill and restore the USB link 20 times mid-chunk; row counts reconcile every time
and no chunk is ever wrongly released. Unplug the SD card → `BLACKBOX:NOSD` → **no ACK is emitted**.
**Risk:** 2 Hz × 4 nodes is only 200 B/s — framing is easy. The real risk is reconnect behaviour.

### P3 — Outbox and the cloud sync daemon · **5 h**

**Goal:** get rows to the cloud exactly once, survivably.
**Deliverable:** `sync.py` + `cloud_client.py`: lease-based claim, gzip, HMAC + sequence,
idempotency key, exponential backoff, `force_sync`, per-batch audit rows.
**Acceptance (against a mock cloud that can be told to misbehave):**
- 10,000 queued rows drain to zero with the mock returning 500 twice then 200
- a batch sent twice inserts **once** (idempotency)
- a 90-second cold-start timeout is survived, not treated as failure
- a replayed batch (same sequence) is rejected by the mock as a duplicate
- reported `bytes_gzip / bytes_raw` is the **measured** ratio
**Risk:** this is where the old backend had its worst bug — bumping `attempts` at claim time and
losing rows. Claim with a **lease**, release on failure, mark `SENT` only on a 200.

### P4 — API surface and replay · **5 h**

**Goal:** serve the frozen contract plus the blackbox endpoints, and make recovery demoable.
**Deliverable:** `api.py`, `replay.py`.
**Acceptance:**
- the v4.4 endpoint matrix returns the same status codes as before (`200` for
  `telemetry|extended|history|ai`, `404` for `map|actuators|alerts|whoami|sync`)
- `GET /api/blackbox/chunks` shows a hold reason for every held chunk
- **`POST /api/blackbox/recover` back-fills a gap**: cut the feed, let the hub write 3 chunks,
  reconnect, recover → the dashboard's gap fills and `COUNT(*)` returns to exact
- recovering twice back-fills nothing the second time (idempotent)

### P5 — AI cascade · **3 h**

**Goal:** re-attach the double-AI advisory layer to the new store, unchanged in behaviour.
**Deliverable:** `ai/` — M1 RandomForest (200×d8, advisory) and M2 XGBoost quantile
(q10/q50/q90 on 32 features), the hazard-state thresholds `{QUIET 0.2, ENV 1.0, STRUCT 2.0,
UNKNOWN 2.0}`, and the kinematic TTF fallback.
**Acceptance:** `GET /api/ai` returns the same schema as v4.4; the cascade is **advisory only** —
it can never block, gate or suppress an alert; `micro`/`macro` from replayed rows feed the models
identically to live rows.
**Deliberate:** no novelty layer. Double AI, as decided.

### P6 — Alerts and channels · **3 h**

**Goal:** four channels, each with its own failure mode.
**Deliverable:** `alerts/` — local siren + LED rail, dashboard + ledger, Telegram push over 443,
email for the audit/regulator path.
**Acceptance:** Telegram delivers with the network up and fails **loudly** in the ledger when it
does not; email is never the first alarm; the local siren works with the laptop's network down.
**Constraint carried over:** venue Wi-Fi may block SMTP 25/587/465 — the live demo push rides 443.

### P7 — Dashboard data and the ledger · **2 h**

**Goal:** make the invisible pipeline visible.
**Deliverable:** `dashboard_data.py` — the four chips, the chunk ledger, the force-sync round-trip
trace, the recover progress.
**Acceptance:** during a live run the ledger shows `CLOSED → SYNCED → ACK → DELETED` for chunk 42
as it happens, and `DROPPED` shows a real number when a drop is injected.

### P8 — Failure rehearsal and viva pack · **4 h**

**Goal:** rehearse the disasters until they are boring.
**Deliverable:** `failure_suite.py` — a scripted, timed run of all ten rows of §9, each with a
pass/fail assertion, plus the viva answer sheet.
**Acceptance:** all ten scenarios pass twice in a row; the timed demo script lands inside its
budget; every number quoted in the presentation is one the suite produced.

**Total: ≈ 30 h ≈ 4 working days.** Minimum demoable path: **P0–P4** (≈ 17 h).

---

## 11. Demo script — the 90 seconds that matter

| t | What happens | What the room sees |
|---|---|---|
| 0:00 | Normal operation | `BLACKBOX: ACTIVE · chunk 42 · 3,712 MB free`, ledger ticking |
| 0:15 | **Flip switch 1** — cut the surface cable | Live view freezes. Hub keeps running on local power. `CLOUD: 0 pending`. The blackbox chip keeps counting: `chunk 43 · 1,847 rows` |
| 0:35 | Nothing to do but wait — that *is* the point | The ledger shows chunks 43, 44, 45 growing on the card while the surface is blind |
| 0:50 | Reconnect the cable | Laptop reconnects, sends `STATE?`, reconciles |
| 0:55 | **Press RECOVER** | `REPLAY:0043:0045` → the dashboard's flat gap **back-fills** with three chunks of history |
| 1:05 | Outbox drains; cloud ACKs | `CLOUD: 100% SYNCED` |
| 1:20 | **Press FORCE SYNC** | `350 pending → gzip 61 KB (85%) → HTTP 200 in 412 ms → ACK_DELETE:0042 → unlinked` |
| 1:30 | Point at the ledger | Chunk 42's row changes from `SYNCED` to `DELETED`. **It is gone, and it is gone only now — after every copy was safe.** |

**If asked "what if the card is full?"** — FIFO unlinks the oldest **released** chunk. **If asked
"what if the hub's queue overflows?"** — the `DROPPED` chip shows the number and the affected chunk
is never deleted. **If asked "if you cut the cable, how does the hub still have power?"** — *"it
doesn't draw from that cable; the surface link is data-only and the hub is on local power."*

---

## 12. Deliberately not building

| Not building | Why |
|---|---|
| A novelty/anomaly layer | Decided: double AI only |
| Per-junction microcontrollers | Junctions stay passive WS2812B |
| GPS / DGPS | Prototype decision; georeferencing is a fixed baseline plus offsets |
| SMS | Conditional production delta; not a demo channel |
| A second uploader / worker fleet | One laptop, one outbox, one lease holder |
| Cloud-side schema migration tooling | The cloud copy is one table and a straight insert |

## 13. Open decisions

1. **Card size** — 512 MB (fills in ~29 days, matches the isolation scenario) or 4 GB (holds ~7
   months, makes the scenario impossible)? *Recommendation: 4 GB, and change the scenario to a 90 %
   capacity warning, because 33 MB/day of headroom costs nothing.*
2. **SQLite or Postgres for the laptop's truth store** — *Recommendation: SQLite for the demo, with
   the schema written Postgres-portable so the same code runs on both.*
3. **Order of P2 and P3** — *Recommendation: P3 before P2.* The cloud path is where the old backend
   had its worst bug and it can be fully tested against a mock; the serial link needs the hardware.
