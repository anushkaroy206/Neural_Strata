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
