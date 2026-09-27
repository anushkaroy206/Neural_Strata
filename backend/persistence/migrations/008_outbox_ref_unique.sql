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
