-- ==========================================================================
-- Neural Strata — schema.sql
-- ==========================================================================
-- GENERATED FILE — do not edit by hand.
-- Produced by:  python3 -m persistence.db --dump-schema
-- Source of truth:  backend/persistence/migrations/*.sql
--
-- This is the full schema for a FRESH database (every migration, in order).
-- The demo laptop already has a database, so apply the migrations instead:
--     python3 -m persistence.db --init
-- ==========================================================================

-- ── migration 001 · init_core ───────────────────────────────
-- ============================================================================
-- Neural Strata — migration 001 · Phase 0 · core tables
-- ============================================================================
-- Owner phase : 0  (ground truth, contracts, replay harness)
-- Kind        : creates the three tables that exist, or are referenced, today
-- Safety      : ADDITIVE ONLY. `IF NOT EXISTS` throughout, no DROP, no UPDATE,
--               no row is read, moved or rewritten. Existing data is untouched.
--
-- Apply with  : python3 -m persistence.db --init
-- Before you do: pg_dump -Fc neural_strata > backup_$(date +%F_%H%M).dump
--
-- Why these three:
--   sensor_telemetry     the ticker has been INSERTing into this at 2 Hz since
--                        v4.4. On a machine where it was never created the log
--                        fills with `relation "sensor_telemetry" does not exist`
--                        and *nothing is persisted*. This migration makes the
--                        table the code already writes to.
--   rfid_evacuation_log  the muster audit trail (SRS business rule). Written by
--                        the RFID path; read by the muster count.
--   schema_migrations    the ledger itself, so `--init` is idempotent and every
--                        later phase can be applied exactly once.
-- ============================================================================

-- -- telemetry ---------------------------------------------------------------
-- Column order matters to `persistence/db.py::insert_telemetry`, which uses an
-- explicit column list — so this table can gain columns later (migration 002
-- adds vib/micro/macro/rssi/batt/level) without touching the current inserts.
CREATE TABLE IF NOT EXISTS sensor_telemetry (
    id              BIGSERIAL PRIMARY KEY,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    node_id         TEXT NOT NULL,
    tilt_x          DOUBLE PRECISION,
    displacement_mm DOUBLE PRECISION,
    acceleration    DOUBLE PRECISION
);

-- Belt and braces, and not theoretical: `CREATE TABLE IF NOT EXISTS` is a no-op
-- when the table is already there, so on any machine that has run the server
-- before — where an older version created `sensor_telemetry` itself — the CREATE
-- above changes nothing and the very next statement fails with
--     ERROR: column "ts" does not exist
-- leaving the ledger empty and the schema half-built. These ALTERs close exactly
-- that gap: the columns the indexes below depend on are guaranteed to exist,
-- whether this file created the table or found it. Still additive-only — no DROP,
-- no UPDATE, no existing value is read or rewritten.
ALTER TABLE sensor_telemetry ADD COLUMN IF NOT EXISTS ts              TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE sensor_telemetry ADD COLUMN IF NOT EXISTS node_id         TEXT;
ALTER TABLE sensor_telemetry ADD COLUMN IF NOT EXISTS tilt_x          DOUBLE PRECISION;
ALTER TABLE sensor_telemetry ADD COLUMN IF NOT EXISTS displacement_mm DOUBLE PRECISION;
ALTER TABLE sensor_telemetry ADD COLUMN IF NOT EXISTS acceleration    DOUBLE PRECISION;

-- Newest-first scans (the dashboard's history read) and per-node windows (the
-- feature extractor's back-fill).
CREATE INDEX IF NOT EXISTS ix_telemetry_ts   ON sensor_telemetry (ts DESC);
CREATE INDEX IF NOT EXISTS ix_telemetry_node ON sensor_telemetry (node_id, ts DESC);

-- -- RFID muster audit -------------------------------------------------------
CREATE TABLE IF NOT EXISTS rfid_evacuation_log (
    id           BIGSERIAL PRIMARY KEY,
    ts           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    miner_uid    TEXT NOT NULL,
    current_zone TEXT NOT NULL
);

-- Same reasoning as sensor_telemetry above: the index needs these columns to be
-- present whether this file created the table or found an older one.
ALTER TABLE rfid_evacuation_log ADD COLUMN IF NOT EXISTS ts           TIMESTAMPTZ NOT NULL DEFAULT NOW();
ALTER TABLE rfid_evacuation_log ADD COLUMN IF NOT EXISTS miner_uid    TEXT;
ALTER TABLE rfid_evacuation_log ADD COLUMN IF NOT EXISTS current_zone TEXT;

CREATE INDEX IF NOT EXISTS ix_rfid_uid_ts ON rfid_evacuation_log (miner_uid, ts DESC);

-- -- migration ledger --------------------------------------------------------
-- `version` is the leading integer of the filename: 001_init_core.sql → 1.
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Record 002 not being applied yet, so `--status` can show the planned shape of
-- the ledger rather than only what has run. Harmless, and additive.
COMMENT ON TABLE schema_migrations IS
    'Applied migration ledger. Written by persistence/db.py::migrate(), read by --status.';

-- ── migration 002 · telemetry_channels ──────────────────────
-- ============================================================================
-- Neural Strata — migration 002 · Phase 1 · widen telemetry
-- ============================================================================
-- Owner phase : 1  (B1 · 4-node state + ingestion)
-- Kind        : ALTER TABLE — six new columns on sensor_telemetry
-- Safety      : ADDITIVE ONLY. Every column is nullable (`ADD COLUMN IF NOT
--               EXISTS … `), no DROP, no UPDATE, no row is read, moved or
--               rewritten. The 2,000+ rows already in the table keep their
--               values and gain NULLs in the new columns, which is the honest
--               reading: before Phase 1 the hub never sent vib/micro/macro/rssi/
--               batt/level, so those values genuinely are unknown for old rows.
--               Writing 0 instead of NULL would invent data — a NULL says "not
--               measured", a 0 says "measured as zero", and the AI must be able
--               to tell those apart.
--
-- Apply with  : python3 -m persistence.db --init
-- Before you do: pg_dump -Fc neural_strata > backup_$(date +%F_%H%M).dump
--
-- Why the frame grew (Backend_Build_Phases.md §A.3.1, §1.5)
--   vib     SW-420 event count per 2 s window. The AI's vib_count_60 and
--           impulse_periodicity features are built from this; without a column
--           the ring buffer is the only copy and a restart loses the record.
--   micro   Load cell + HX711 crack-mouth opening (µm CMOD), nodes 2 & 4 only,
--           NULL elsewhere. Phase 3 re-specified the unit: the build measures
--           elongation across the seam with a load cell in tension, not strain
--           with a foil gauge, so µm is the unit the instrument can honestly
--           report. The column type did not change (DOUBLE PRECISION) and no
--           clause below was touched — this is a comment, and the ledger has no
--           checksum.
--   macro   Rotary-pot seam opening (mm), 0–25 mm travel, nodes 2 & 4 only.
--   rssi    ESP-NOW link quality — the first honest measure of radio health.
--   batt    Node supply voltage. Load-bearing for "did the node die or was it
--           unplugged", which is the difference between a fault and a fault
--           report that never arrives.
--   level   Which level the node sits on (1 or 2). Denormalised on purpose:
--           every routing and per-level query would otherwise join the node
--           registry, and the registry is in config, not in the database.
--
-- The matching Python side is `contracts.TELEMETRY_DB_COLUMNS` and
-- `contracts.NodeState.to_db_tuple()`; the ORDER is asserted equal in
-- tests/test_contracts.py, because a column list and a row tuple that drift
-- apart write the wrong column silently and the data still looks plausible.
-- ============================================================================

ALTER TABLE sensor_telemetry
    ADD COLUMN IF NOT EXISTS vib   INT,
    ADD COLUMN IF NOT EXISTS micro DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS macro DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS rssi  INT,
    ADD COLUMN IF NOT EXISTS batt  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS level SMALLINT;

-- Per-node, per-level ranges dominate the query patterns of Phase 2 onward
-- (health windows, hourly rollups, per-level routing history). Partial indexes
-- keep them cheap on a table that grows at 8 rows/s during a run.
CREATE INDEX IF NOT EXISTS ix_telemetry_node_level
    ON sensor_telemetry (node_id, level, ts DESC);

-- The crack channels are NULL on N1/N3 by design, so an index that only covers
-- rows where the instrument exists serves the crack queries without dragging the
-- NULL majority into the index.
CREATE INDEX IF NOT EXISTS ix_telemetry_crack
    ON sensor_telemetry (ts DESC, node_id)
    WHERE micro IS NOT NULL OR macro IS NOT NULL;

INSERT INTO schema_migrations (version) VALUES (2)
    ON CONFLICT (version) DO NOTHING;

-- ── migration 003 · alerts ──────────────────────────────────
-- ============================================================================
-- Neural Strata — migration 003 · Phase 8 · alerts
-- ============================================================================
-- Owner phase : 8  (B4 · persistent alert dispatcher)
-- Kind        : CREATE TABLE ×2 + indexes
-- Safety      : ADDITIVE ONLY. `IF NOT EXISTS` throughout, no DROP, no UPDATE,
--               no existing row is read, moved or rewritten. A database that has
--               never seen an alert is unchanged by this file except for gaining
--               two empty tables.
--
-- Apply with  : python3 -m persistence.db --init
-- Before you do: pg_dump -Fc neural_strata > backup_$(date +%F_%H%M).dump
--
-- Why now, and why these two tables
-- ---------------------------------
-- Defect D12: alerts were `push_event()` rows in an in-memory list capped at 64.
-- The cap is the tell — an operator log is allowed to forget, an *alert* record
-- is not. Between a shift's events and a regulator's question there has to be a
-- row that says: this happened, this is what the model saw, this is who was told,
-- through which channel, and whether it arrived.
--
--   alerts_history    one row per incident. Columns are §A.3.7's, verbatim.
--   alert_deliveries  one row per (alert, channel) attempt — the ledger. It is
--                     what makes "we send alerts" into "we can prove alerts were
--                     delivered", and the console renders it per alert.
--
-- The four `*_dispatched` booleans on `alerts_history` are kept **alongside** the
-- ledger rather than derived from it, and that is deliberate: they are the answer
-- to one question ("did this channel ever succeed for this alert?") that the deck
-- asks on every row of a 50-row list. The ledger is the detail behind them.
--
-- `alerts_history` has no `node_label`/`where`: the label is presentation and it
-- can change (someone renames a node); the coordinates are the fact that does not.
-- Migration 005 already adds its watermark columns *if the table exists*, and it
-- is written to run either way — so this migration arriving later is safe.
-- ============================================================================

-- ── incidents ----------------------------------------------------------------
-- `severity` is the tier the dispatcher actually used, after the firewall floor
-- and after the ADVISORY reclassification — not the raw model number. The row is
-- the record of what the system decided, and a row that stored a tier the system
-- never used would make the audit trail argue with the log.
CREATE TABLE IF NOT EXISTS alerts_history (
    id                  BIGSERIAL PRIMARY KEY,
    ts                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    level_id            VARCHAR(16) NOT NULL,
    node_id             VARCHAR(16) NOT NULL,
    severity            VARCHAR(16) NOT NULL,      -- WATCH|ELEVATED|HIGH|CRITICAL|ADVISORY
    event_type          VARCHAR(64) NOT NULL,
    progression_stage   VARCHAR(16),
    ttf_seconds         INTEGER,
    ttf_q10             INTEGER,
    ttf_q90             INTEGER,
    disruption_label    VARCHAR(24),
    disruption_index    REAL,
    geo_lat             DOUBLE PRECISION NOT NULL,
    geo_lon             DOUBLE PRECISION NOT NULL,
    recommended_action  TEXT NOT NULL,
    state               VARCHAR(16) NOT NULL DEFAULT 'NEW',   -- NEW|ACTIVE|ACKED|CLOSED
    acked_by            TEXT,
    acked_at            TIMESTAMPTZ,
    sms_dispatched      BOOLEAN DEFAULT FALSE,
    email_dispatched    BOOLEAN DEFAULT FALSE,
    local_dispatched    BOOLEAN DEFAULT FALSE,
    dash_dispatched     BOOLEAN DEFAULT FALSE,
    attempts            SMALLINT DEFAULT 0,
    last_error          TEXT
);

-- Phase 8 additions to the §A.3.7 sketch, all nullable, all additive:
--   dedupe_key      the machine's bucket key, so a re-open can be tied to the
--                   alert it repeated (node|event_type|tier)
--   firewalled      was the cascade firewall engaged? — the audit question when
--                   a regulator asks why an environmental reading escalated
--   escalated       did the timer fire without an acknowledgement?
--   suppressed      did policy choose to stay quiet, and why. **A suppression
--                   that is not recorded is indistinguishable from a failure.**
--   reference       the short id printed in the SMS/email body (`NS-A7F3`), so a
--                   printed message can be matched to its row by eye
--   closed_reason   how the incident ended: condition cleared, or manual re-arm
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS dedupe_key     TEXT;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS firewalled     BOOLEAN DEFAULT FALSE;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS escalated      BOOLEAN DEFAULT FALSE;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS suppressed     BOOLEAN DEFAULT FALSE;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS suppress_reason TEXT;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS reference      VARCHAR(16);
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS closed_reason  TEXT;
ALTER TABLE alerts_history ADD COLUMN IF NOT EXISTS updated_at     TIMESTAMPTZ;

-- The console's list query is "newest first, optionally filtered by tier and
-- time"; the drill report's is "how many alerts per tier per day".
CREATE INDEX IF NOT EXISTS alerts_history_ts_idx       ON alerts_history (ts DESC);
CREATE INDEX IF NOT EXISTS alerts_history_node_ts_idx  ON alerts_history (node_id, ts DESC);
CREATE INDEX IF NOT EXISTS alerts_history_state_idx    ON alerts_history (state);
CREATE INDEX IF NOT EXISTS alerts_history_tier_ts_idx  ON alerts_history (severity, ts DESC);
CREATE INDEX IF NOT EXISTS alerts_history_ref_idx      ON alerts_history (reference);

-- ── the delivery ledger ------------------------------------------------------
-- One row per attempt, kept forever. A failed attempt is data, not noise: the
-- re-notify path and the escalation record both read it, and "the SMS went out
-- on the second try, 40 s after the siren" is exactly the sentence a post-mortem
-- needs.
CREATE TABLE IF NOT EXISTS alert_deliveries (
    id          BIGSERIAL PRIMARY KEY,
    alert_id    BIGINT REFERENCES alerts_history(id),
    channel     TEXT NOT NULL,                 -- local|dashboard|sms|email|lifecycle
    ok          BOOLEAN NOT NULL,
    detail      TEXT,
    queued      BOOLEAN DEFAULT FALSE,         -- accepted for retry rather than failed
    verified    BOOLEAN DEFAULT FALSE,         -- the far end confirmed it
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS alert_deliveries_alert_idx ON alert_deliveries (alert_id);
CREATE INDEX IF NOT EXISTS alert_deliveries_ch_idx    ON alert_deliveries (channel, ts DESC);

-- ── migration 005 · sync_watermarks ─────────────────────────
-- ============================================================================
-- Neural Strata — migration 005 · Phase 0 · the bulk sync path
-- ============================================================================
-- Owner phase : 0  (listed as Phase-0 work by Neural_Strata_Dual_Database.md §H.6)
-- Kind        : new rollup table + the sync watermark on alerts
-- Safety      : ADDITIVE ONLY. New table, new index, guarded ADD COLUMN.
--
-- Apply with  : python3 -m persistence.db --init
--
-- Why a `synced_at` watermark instead of a separate outbox table
-- --------------------------------------------------------------
-- An outbox that *copies* rows duplicates storage and can drift from its source.
-- A nullable timestamp on the row itself is atomic, idempotent, costs one column
-- and cannot get out of sync. The generic `outbox` table (migration 003) stays
-- for discrete events; BULK rows use watermarks.
--
-- Ordering note (this is deliberate, not an oversight)
-- ----------------------------------------------------
-- `alerts_history` is created by migration 003, which belongs to Phase 8. This
-- migration runs in Phase 0, so it cannot assume the table exists. The ALTER
-- below is therefore wrapped in a guard: on an existing database that already
-- has `alerts_history` the columns are added now; on a database that does not,
-- the guard prints a notice and **migration 003 must create the three watermark
-- columns itself**. Both orders converge on the same schema — that property is
-- the thing being protected here, not the timing.
-- ============================================================================

-- -- hourly rollup: the unit of bulk sync ------------------------------------
CREATE TABLE IF NOT EXISTS node_hourly (
    site_id       TEXT        NOT NULL,
    node_id       TEXT        NOT NULL,
    hour_start    TIMESTAMPTZ NOT NULL,
    n_samples     INTEGER     NOT NULL,
    disp_mean     REAL, disp_min  REAL, disp_max  REAL, disp_last REAL,
    tilt_mean     REAL, tilt_max  REAL,
    acc_mean      REAL, acc_max   REAL,
    vib_sum       INTEGER,
    micro_max     REAL,                 -- NULL on nodes with no strain channel
    macro_max     REAL,
    severity_max  VARCHAR(16),          -- worst status seen in the hour
    -- sync bookkeeping (this IS the outbox for bulk rows)
    synced_at     TIMESTAMPTZ,
    sync_attempts SMALLINT DEFAULT 0,
    last_error    TEXT,
    PRIMARY KEY (site_id, node_id, hour_start)
);

-- The uploader's only query: "what has not gone up yet, oldest first".
CREATE INDEX IF NOT EXISTS ix_hourly_pending
    ON node_hourly (hour_start) WHERE synced_at IS NULL;

-- -- alerts gain the same watermark ------------------------------------------
DO $$
BEGIN
    IF to_regclass('public.alerts_history') IS NOT NULL THEN
        ALTER TABLE alerts_history
            ADD COLUMN IF NOT EXISTS synced_at     TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS sync_attempts SMALLINT DEFAULT 0,
            ADD COLUMN IF NOT EXISTS site_id       TEXT NOT NULL DEFAULT 'site-unknown';
        RAISE NOTICE '005: alerts_history is present — watermark columns ensured.';
    ELSE
        RAISE NOTICE '005: alerts_history absent (migration 003 / Phase 8 not applied yet). '
                     'Migration 003 must create synced_at, sync_attempts and site_id itself.';
    END IF;
END $$;

-- ── migration 006 · outbox ──────────────────────────────────
-- ============================================================================
-- Neural Strata — migration 006 · Phase 9 (B6) · the generic outbox
-- ============================================================================
-- Owner phase : 9  (Neural_Strata_Backend_Build_Phases.md §9.2, §A.3.7)
-- Kind        : new table + one index
-- Safety      : ADDITIVE ONLY. CREATE TABLE IF NOT EXISTS; no existing table is
--               touched, no column is altered, nothing is dropped.
-- Apply with  : python3 -m persistence.db --init
--
-- Why this table exists at all, when §F.2 of Dual_Database.md argues *against*
-- an outbox for bulk rows
-- ---------------------------------------------------------------------------
-- Both things are true and they are not in conflict:
--
--   * **Bulk rows** (hourly rollups, alert history) sync through a `synced_at`
--     watermark on the row itself. An outbox that copied them would double the
--     storage and could drift from the source — the copy is the bug.
--   * **Discrete events** (one alert, one upload manifest, one drill record) are
--     different: they are small, infrequent, and each one wants an *idempotency
--     key* the receiver can dedupe on. Those go in this table.
--
-- The rule that decides which is which — "can the receiver recognise a re-send of
-- this exact thing?" — is the same rule that decides whether a row needs a
-- watermark or a queue.
--
-- Priority (§9.5): a flooded queue must still ship the alerts first. It is an
-- integer column rather than an enum so that a new kind can be slotted between two
-- existing ones without a migration; `config.OUTBOX_PRIORITY` is the authority and
-- the default here (50) is deliberately *worse* than every kind it knows about,
-- so an unknown kind lands behind the ones the phase reasoned about.
-- ============================================================================

CREATE TABLE IF NOT EXISTS outbox (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    kind        TEXT        NOT NULL,
    payload     JSONB       NOT NULL,
    status      TEXT        NOT NULL DEFAULT 'PENDING',   -- PENDING|SENT|FAILED
    priority    INTEGER     NOT NULL DEFAULT 50,
    attempts    SMALLINT    NOT NULL DEFAULT 0,
    queued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    next_try_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_error  TEXT,
    sent_at     TIMESTAMPTZ
);

-- The claim query's index. Partial on PENDING because that is the only status the
-- daemon ever scans for, and a queue that has been draining for a week should cost
-- the same to scan as one that was born an hour ago.
CREATE INDEX IF NOT EXISTS ix_outbox_pending
    ON outbox (priority, id) WHERE status = 'PENDING';

-- FAILED rows are kept, never deleted (§9.2 rule 4: "to keep the last error for
-- inspection"). This index is what lets a console list them without a full scan.
CREATE INDEX IF NOT EXISTS ix_outbox_failed
    ON outbox (id) WHERE status = 'FAILED';

-- Compaction's index: SENT rows older than `keep_days` are the only rows the
-- prune is allowed to touch.
CREATE INDEX IF NOT EXISTS ix_outbox_sent
    ON outbox (sent_at) WHERE status = 'SENT';

-- Who produced it, for the multi-site story (§E.1 rule 2 — every synced row
-- carries a site_id; the outbox is no exception, or the central table cannot tell
-- two sites' alerts apart).
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS site_id TEXT NOT NULL DEFAULT 'site-unknown';

-- A captured alert's own identity, so the receiver can dedupe on it even if the
-- batch is re-sent. NULL for kinds that have no natural key.
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS ref TEXT;
CREATE INDEX IF NOT EXISTS ix_outbox_ref ON outbox (ref) WHERE ref IS NOT NULL;

-- ── migration 007 · alert_sync_watermark ────────────────────
-- ============================================================================
-- Neural Strata — migration 007 · Phase 9 (B6) · the alert watermark, for real
-- ============================================================================
-- Owner phase : 9  (found while building it; see "the defect" below)
-- Kind        : ADD COLUMN ×3 + one index on an existing table
-- Safety      : ADDITIVE ONLY. No column is altered or dropped, no row is touched.
-- Apply with  : python3 -m persistence.db --init
--
-- The defect this migration exists to close
-- -----------------------------------------
-- Migration 005 (`sync_watermarks`, Phase 0) adds `synced_at`, `sync_attempts`
-- and `site_id` to `alerts_history` — but it was applied at 04:33, and migration
-- 003 (`alerts`, Phase 8) created that table at 05:22. 005's guard therefore did
-- exactly what its own header says it would: it found no table, printed a notice,
-- and left a note that *"migration 003 must create the three watermark columns
-- itself"*. 003 did not — it was written before that note mattered, and nothing
-- queried those columns until the sync daemon tried to.
--
-- The generalisable lesson, which is why this header is long: **a migration that
-- is correct in either order is not the same as a schema that is correct in every
-- order.** Both orders "converged" only if somebody read the notice. The rule that
-- actually holds is the one used here: a later phase re-asserts the columns it
-- depends on, unconditionally, in its own migration. Idempotent, additive, and it
-- cannot be missed by a reader who is busy.
--
-- Verified by `tools/verify_dual_db_sql.py`, which fails loudly if any of the three
-- columns is missing — because the failure mode of this defect is not an error, it
-- is a sync path that quietly syncs nothing.
-- ============================================================================

DO $$
BEGIN
    IF to_regclass('public.alerts_history') IS NULL THEN
        RAISE NOTICE 'alerts_history absent — migration 003 has not run yet; skipping (007 re-runs at the next --init)';
    ELSE
        ALTER TABLE alerts_history
            ADD COLUMN IF NOT EXISTS synced_at     TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS sync_attempts SMALLINT NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS site_id       TEXT NOT NULL DEFAULT 'site-unknown';
        RAISE NOTICE 'alerts_history: sync watermark ensured (synced_at, sync_attempts, site_id)';
    END IF;
END $$;

-- The uploader's second query: "which alerts have not gone up yet, oldest first".
CREATE INDEX IF NOT EXISTS ix_alerts_pending
    ON alerts_history (ts) WHERE synced_at IS NULL;

-- ── migration 008 · outbox_ref_unique ───────────────────────
-- ============================================================================
-- Neural Strata — migration 008 · Phase 9 (B6) · the ref really is unique
-- ============================================================================
-- Owner phase : 9
-- Kind        : one dedupe DELETE + one UNIQUE partial index
-- Safety      : additive. The DELETE only ever removes a row that duplicates
--               another row's `ref` — a row that must not exist by definition,
--               since `ref` is the queue's identity for "this one thing".
-- Apply with  : python3 -m persistence.db --init
--
-- Why this migration exists
-- -------------------------
-- Migration 006 indexed `ref` but did not make it unique, and `enqueue_outbox`
-- inserted unconditionally. So §9.5's telemetry aggregate — whose whole enqueue
-- contract is "ref = node + minute, therefore a double tick is a no-op" — queued
-- *twice* when the ticker ran twice inside one minute, and the far side received
-- two identical summaries. Found by running the enqueue twice by hand and counting
-- the rows: 4 became 8.
--
-- The lesson worth keeping: an index is not a constraint. `ix_outbox_ref` made
-- lookups fast and guaranteed nothing, and the code that relied on it looked
-- exactly like code that was safe. **A uniqueness claim needs a UNIQUE index, and
-- the INSERT needs to say what to do when it is violated** — both halves, or the
-- guarantee is a comment.
-- ============================================================================

-- ── 1 · duplicates first: a unique index cannot be built over them ──────────
-- Keep the oldest row of each `ref`; the newer copy is the bug. Only PENDING rows
-- are touched: a SENT or FAILED row is a record of something that happened, and
-- deleting one to satisfy a constraint would destroy evidence to tidy up.
DO $$
DECLARE
    removed INTEGER;
BEGIN
    WITH dupes AS (
        SELECT id,
               row_number() OVER (PARTITION BY ref ORDER BY id) AS n
          FROM outbox
         WHERE ref IS NOT NULL AND status = 'PENDING'
    )
    DELETE FROM outbox WHERE id IN (SELECT id FROM dupes WHERE n > 1);
    GET DIAGNOSTICS removed = ROW_COUNT;
    IF removed > 0 THEN
        RAISE NOTICE 'outbox: removed % duplicate PENDING row(s) before adding the unique index', removed;
    END IF;
END $$;

-- ── 2 · the constraint the code already believed in ────────────────────────
-- Partial **twice over**, and each half is deliberate:
--
--   · `ref IS NOT NULL` — a `ref` is optional (a caller with nothing to identify
--     its row by passes NULL), and two NULLs are not two of the same thing;
--   · `status = 'PENDING'` — the uniqueness claim is about the *queue*, not about
--     history. One deliverable copy of `NS-976A#41` may be waiting; the copies
--     already sent are records of what happened, and a FAILED one is the evidence
--     of what could not. A full-table unique index would have made a re-raise
--     after a re-arm impossible — the same reference, legitimately queued again —
--     and would have needed a DELETE over rows that must never be deleted.
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_ref_pending
    ON outbox (ref) WHERE ref IS NOT NULL AND status = 'PENDING';
