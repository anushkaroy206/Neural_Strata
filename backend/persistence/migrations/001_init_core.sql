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
