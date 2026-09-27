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
