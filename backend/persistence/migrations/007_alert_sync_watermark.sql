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
