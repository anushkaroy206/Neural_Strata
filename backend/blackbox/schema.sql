-- Neural Strata v5.0 — schema
-- Portable SQLite now, Postgres-compatible types throughout so the cloud copy is a straight port.
-- Rule: the SD row schema on the hub matches sensor_telemetry field-for-field, so a
-- blackbox replay is a plain INSERT with no translation layer.

PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;      -- we are a safety system, not a web app

-- ---------------------------------------------------------------- truth store
CREATE TABLE IF NOT EXISTS sensor_telemetry (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id        INTEGER NOT NULL,
    seq             INTEGER NOT NULL,          -- 1..rows in this chunk, as stamped by Core 1
    ts_ms           INTEGER NOT NULL,          -- hub monotonic milliseconds
    received_at     TEXT    NOT NULL,          -- laptop wall clock, ISO-8601 UTC
    source          TEXT    NOT NULL DEFAULT 'live',   -- live | replay
    node_id         INTEGER NOT NULL,          -- 1..4
    level           INTEGER NOT NULL,          -- 1 or 2
    tilt_x          REAL,
    displacement_mm REAL,
    acceleration    REAL,
    vib             INTEGER,
    micro           REAL,
    macro           REAL,
    flags           INTEGER NOT NULL DEFAULT 0,
    UNIQUE (chunk_id, seq)                     -- makes replay idempotent
);
CREATE INDEX IF NOT EXISTS ix_tel_chunk   ON sensor_telemetry (chunk_id);
CREATE INDEX IF NOT EXISTS ix_tel_node_ts ON sensor_telemetry (node_id, ts_ms);

-- ------------------------------------------------------- blackbox bookkeeping
CREATE TABLE IF NOT EXISTS blackbox_chunks (
    chunk_id        INTEGER PRIMARY KEY,
    state           TEXT    NOT NULL DEFAULT 'OPEN',   -- OPEN | CLOSED
    expected_rows   INTEGER,                           -- from CHUNK_CLOSE; NULL until seen
    received_rows   INTEGER NOT NULL DEFAULT 0,
    dropped_rows    INTEGER NOT NULL DEFAULT 0,        -- hub-reported queue overflow
    opened_at       TEXT,
    closed_at       TEXT,
    ack_sent_at     TEXT,                              -- set the instant ACK_DELETE goes out
    ack_reason      TEXT,
    recover_at      TEXT                               -- last REPLAY request for this chunk
);

-- ------------------------------------------------------------- cloud outbox
CREATE TABLE IF NOT EXISTS cloud_outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id    INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    payload     TEXT    NOT NULL,              -- canonical JSON of the row
    status      TEXT    NOT NULL DEFAULT 'PENDING',   -- PENDING | SENT | FAILED
    attempts    INTEGER NOT NULL DEFAULT 0,
    claimed_at  TEXT,
    claim_id    TEXT,
    sent_at     TEXT,
    batch_id    TEXT,
    last_error  TEXT,
    UNIQUE (chunk_id, seq)                     -- one outbox row per telemetry row, ever
);
CREATE INDEX IF NOT EXISTS ix_outbox_status ON cloud_outbox (status, chunk_id);

CREATE TABLE IF NOT EXISTS cloud_batches (
    batch_id      TEXT PRIMARY KEY,
    chunk_ids     TEXT NOT NULL,
    row_count     INTEGER NOT NULL,
    bytes_raw     INTEGER NOT NULL,
    bytes_gzip    INTEGER NOT NULL,
    status        TEXT NOT NULL,               -- PENDING | SENT | FAILED
    attempts      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    completed_at  TEXT,
    http_status   INTEGER,
    last_error    TEXT
);

-- ------------------------------------------------------------------- status
CREATE TABLE IF NOT EXISTS hub_status (
    id               INTEGER PRIMARY KEY CHECK (id = 1),
    last_seen        TEXT,
    firmware         TEXT,
    blackbox_state   TEXT,                     -- OK | NOSD | FULL | FAULT
    free_mb          INTEGER,
    open_chunk       INTEGER,
    dropped_total    INTEGER NOT NULL DEFAULT 0,
    last_flush_ms    INTEGER
);

INSERT OR IGNORE INTO hub_status (id, dropped_total) VALUES (1, 0);
