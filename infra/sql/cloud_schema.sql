-- ============================================================================
-- Neural Strata — the CLOUD schema (Tier 2). Phase 9 (B6), §E.2 / §H.1.
-- ============================================================================
-- Apply to the cloud database:  psql "$NS_CLOUD_DB_URL" -f infra/sql/cloud_schema.sql
--
-- Four design rules, and every one of them is why this file is *not* a copy of
-- the edge schema:
--
--  1. **A different shape, deliberately.** The edge schema is optimised for 2 Hz
--     writes and keeps raw telemetry forever. This one is optimised for reading a
--     year of history across many sites, so it keeps *summaries* — one row per node
--     per hour — and nothing that only the mine needs. Mirroring the edge is what
--     breaks a free tier.
--  2. **Every table carries `site_id`.** That is what makes the multi-site story
--     true rather than aspirational: a hundred edges, one account, and no row whose
--     origin has to be guessed from which connection wrote it.
--  3. **Every synced table has an idempotency key.** The daemon *will* re-send
--     (that is the recoverable failure mode it prefers), so a re-send must be a
--     no-op. Each table below names its key in a comment.
--  4. **`TIMESTAMPTZ` everywhere.** The mine, the laptop and the cloud may be in
--     three timezones; a naive timestamp silently shifts history.
--
-- Safety: additive and idempotent. Every statement is CREATE … IF NOT EXISTS, so
-- re-running this file against a live cloud changes nothing.
-- ============================================================================

-- ── 1 · the core reporting table: one row per node per hour -------------------
-- Idempotency key: (site_id, node_id, hour_start)
CREATE TABLE IF NOT EXISTS node_hourly (
    site_id       TEXT        NOT NULL,
    node_id       TEXT        NOT NULL,
    hour_start    TIMESTAMPTZ NOT NULL,
    n_samples     INTEGER     NOT NULL,
    -- displacement, mm
    disp_mean     REAL, disp_min REAL, disp_max REAL, disp_last REAL,
    -- tilt, degrees
    tilt_mean     REAL, tilt_max REAL,
    -- acceleration, g
    acc_mean      REAL, acc_max REAL,
    -- vibration: a count, not an average — averaging counts destroys the signal
    vib_sum       INTEGER,
    -- strain, µε. NULL on nodes with no strain channel (D10: the crack nodes)
    micro_max     REAL, macro_max REAL,
    -- the worst status the *system* believed in that hour. This is the column a
    -- post-mortem compares against the telemetry above: "it was CRITICAL and the
    -- panel was moving 43 mm — the model was not wrong, the night shift was."
    severity_max  VARCHAR(16),
    synced_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (site_id, node_id, hour_start)
);
CREATE INDEX IF NOT EXISTS ix_cloud_hourly_node
    ON node_hourly (site_id, node_id, hour_start DESC);
CREATE INDEX IF NOT EXISTS ix_cloud_hourly_sev
    ON node_hourly (site_id, hour_start DESC) WHERE severity_max <> 'NOMINAL';

-- ── 2 · the incident record ---------------------------------------------------
-- Idempotency key: (site_id, ts, node_id, event_type)
-- The edge's alert columns, minus the delivery ledger (which is an edge concern:
-- what matters centrally is *that* the site alarmed, with what, and where).
CREATE TABLE IF NOT EXISTS alerts_history (
    id                 BIGSERIAL PRIMARY KEY,
    site_id            TEXT        NOT NULL,
    ts                 TIMESTAMPTZ NOT NULL,
    level_id           TEXT,
    node_id            TEXT        NOT NULL,
    severity           VARCHAR(16) NOT NULL,
    event_type         VARCHAR(64) NOT NULL,
    progression_stage  VARCHAR(16),
    ttf_seconds        INTEGER, ttf_q10 INTEGER, ttf_q90 INTEGER,
    disruption_label   VARCHAR(24), disruption_index REAL,
    -- coordinates: a problem-statement requirement, and the reason the email
    -- carries a map link rather than a description of one
    geo_lat            DOUBLE PRECISION,
    geo_lon            DOUBLE PRECISION,
    recommended_action TEXT,
    UNIQUE (site_id, ts, node_id, event_type)
);
CREATE INDEX IF NOT EXISTS ix_cloud_alerts_recent
    ON alerts_history (site_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_cloud_alerts_sev
    ON alerts_history (site_id, severity, ts DESC);

-- ── 3 · drill runs: what makes the demo auditable -----------------------------
-- One row per drill/test: which drill, when, what the model predicted, and what
-- actually happened. Without it, "the model works" is a claim about a memory.
CREATE TABLE IF NOT EXISTS drill_runs (
    id            BIGSERIAL PRIMARY KEY,
    site_id       TEXT        NOT NULL,
    drill_id      TEXT        NOT NULL,
    started_at    TIMESTAMPTZ NOT NULL,
    ended_at      TIMESTAMPTZ,
    scenario      TEXT,
    predicted_ttf INTEGER,
    observed_ttf  INTEGER,
    tier_at_start VARCHAR(16),
    outcome       TEXT,
    notes         TEXT,
    UNIQUE (site_id, drill_id)
);

-- ── 4 · model metrics: the single source of truth (defect D4) -----------------
-- One row per model version, publishing the MAE **with the run that produced it**.
-- Idempotency key: (model_name, version)
CREATE TABLE IF NOT EXISTS model_metrics (
    id           BIGSERIAL PRIMARY KEY,
    model_name   TEXT        NOT NULL,
    version      TEXT        NOT NULL,
    trained_at   TIMESTAMPTZ,
    published_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    mae_seconds  REAL,
    r2           REAL,
    n_train      INTEGER,
    n_test       INTEGER,
    features     INTEGER,
    artifact     TEXT,
    git_rev      TEXT,
    UNIQUE (model_name, version)
);

-- ── 5 · sync log: what arrived, and whether it worked -------------------------
-- The table that tells you sync is broken before a human notices. Every batch the
-- daemon commits writes one row here, from the *edge's* side — so an empty
-- sync_log beside a busy site is itself the alarm.
CREATE TABLE IF NOT EXISTS sync_log (
    id         BIGSERIAL PRIMARY KEY,
    site_id    TEXT        NOT NULL,
    ts         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    n_rollups  INTEGER NOT NULL DEFAULT 0,
    n_alerts   INTEGER NOT NULL DEFAULT 0,
    ok         BOOLEAN NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS ix_cloud_synclog
    ON sync_log (site_id, ts DESC);

-- ── the view: the one query the reporting story actually needs ----------------
-- "Per site, per day: how much did it move, how bad did it get, and did the data
-- arrive." A view rather than a materialised table, because on a free tier the
-- storage of a summary-of-a-summary is the thing that gets suspended first.
-- The alert count comes from a CTE and a LEFT JOIN, **not** from a correlated
-- subquery. The first version of this view was written as
--
--     (SELECT count(*) FROM alerts_history a
--       WHERE a.site_id = h.site_id AND (a.ts AT TIME ZONE 'UTC')::date = …)
--
-- which Postgres refuses outright: the reference to `h.hour_start` sits inside a
-- subquery, where the planner cannot match it to the grouped *expression*
-- `(h.hour_start AT TIME ZONE 'UTC')::date`, so the file died with "subquery uses
-- ungrouped column h.hour_start from outer query" — the whole schema, including
-- this view, failed to load on a fresh central database. The CTE also makes the
-- query a straight hash join instead of a correlated scan per group, which is the
-- difference between a daily report and a daily wait.
CREATE OR REPLACE VIEW v_site_daily AS
WITH alerts_by_day AS (
    SELECT site_id,
           (ts AT TIME ZONE 'UTC')::date AS day,
           count(*)                      AS alerts_raised
      FROM alerts_history
     GROUP BY site_id, (ts AT TIME ZONE 'UTC')::date
)
SELECT
    h.site_id,
    (h.hour_start AT TIME ZONE 'UTC')::date          AS day,
    count(DISTINCT h.node_id)                        AS nodes_reporting,
    count(*)                                         AS node_hours,
    sum(h.n_samples)                                 AS samples,
    max(h.disp_max)                                  AS worst_disp_mm,
    avg(h.disp_mean)                                 AS mean_disp_mm,
    max(h.tilt_max)                                  AS worst_tilt_deg,
    sum(h.vib_sum)                                   AS vibration_events,
    max(h.micro_max)                                 AS worst_micro_strain,
    max(h.severity_max)                              AS worst_severity,
    coalesce(a.alerts_raised, 0)                     AS alerts_raised
FROM node_hourly h
LEFT JOIN alerts_by_day a
       ON a.site_id = h.site_id
      AND a.day = (h.hour_start AT TIME ZONE 'UTC')::date
GROUP BY h.site_id, (h.hour_start AT TIME ZONE 'UTC')::date, a.alerts_raised
ORDER BY h.site_id, day DESC;
