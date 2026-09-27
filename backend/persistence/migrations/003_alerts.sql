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
