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
